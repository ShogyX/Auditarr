"""
integrations/__init__.py — Plugin registry, polling worker, automation runner.
"""
import threading
import time
from datetime import datetime

import db

# Import plugins so they self-register
from integrations.base import Integration, REGISTRY, get_plugin, all_plugins  # noqa
from integrations import sonarr_radarr  # noqa
from integrations import scaffolds      # noqa
from integrations import bazarr         # noqa
from integrations import tdarr          # noqa


# ─── Public API ───────────────────────────────────────────────────────────────

def make_plugin(server: dict) -> Integration | None:
    cls = get_plugin(server.get("kind", ""))
    return cls(server) if cls else None


def test_connection(server: dict) -> tuple[bool, str]:
    p = make_plugin(server)
    if not p: return False, f"Unknown integration kind: {server.get('kind')}"
    return p.test_connection()


def sync_server(server: dict) -> tuple[int, str]:
    p = make_plugin(server)
    if not p: return 0, "Unknown integration"
    if not p.SUPPORTS_SYNC: return 0, "Sync not supported by this integration"
    return p.sync()


def parse_webhook(server: dict, payload: dict) -> dict:
    p = make_plugin(server)
    if not p: return {"kind": server.get("kind","unknown"), "event_type": payload.get("eventType","Unknown"), "file_paths": []}
    return p.parse_webhook(payload)


def record_webhook(server_id: int, payload: dict) -> dict:
    server = db.get_integration(server_id)
    if not server:
        return {"kind": "unknown", "event_type": "Unknown", "file_paths": []}
    parsed = parse_webhook(server, payload)
    db.add_integration_event(
        integration_id=server_id, event_type=parsed["event_type"],
        kind=parsed["kind"], payload=payload, file_paths=parsed["file_paths"],
    )
    return parsed


def set_monitored(file_record: dict, monitored: bool) -> tuple[bool, str]:
    """Toggle monitoring on the *arr that owns this file."""
    kind = file_record.get("arr_kind")
    arr_id = file_record.get("arr_id")
    arr_file_id = file_record.get("arr_file_id")
    if not (kind and arr_file_id is not None):
        return False, "File is not linked to an *arr server"
    # Find an enabled server of this kind
    servers = db.all_enabled_integrations(kind=kind)
    if not servers:
        return False, f"No enabled {kind} server"
    server = servers[0]
    plugin = make_plugin(server)
    if not plugin or not hasattr(plugin, "set_monitored"):
        return False, f"{kind} plugin does not support monitoring control"
    if kind == "sonarr":
        ok, msg = plugin.set_monitored(arr_file_id, monitored)
    else:
        ok, msg = plugin.set_monitored(arr_id, monitored)
    if ok:
        db.update_file_monitored(file_record["id"], monitored)
    return ok, msg


# ─── Polling worker ───────────────────────────────────────────────────────────

class PollingWorker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self._stop = threading.Event()
        self._last_run = {}

    def stop(self): self._stop.set()

    def run(self):
        while not self._stop.is_set():
            try:
                servers = db.all_enabled_integrations()
                now = time.time()
                for s in servers:
                    plugin = make_plugin(s)
                    if not plugin or not plugin.SUPPORTS_SYNC: continue
                    last = self._last_run.get(s["id"], 0)
                    if now - last >= (s.get("poll_interval") or 900):
                        try: sync_server(s)
                        except Exception: pass
                        self._last_run[s["id"]] = now
            except Exception: pass
            for _ in range(60):
                if self._stop.is_set(): return
                time.sleep(1)


# ─── Automation runner ────────────────────────────────────────────────────────

SEVERITY_RANK = {
    "ok": 0, "info": 1, "high_bitrate": 2, "possible_transcode": 3,
    "always_transcode": 4, "unplayable": 5,
}


def run_automation_rules():
    """
    For every enabled rule, find files matching the condition and apply the
    rule's action. Each rule is wrapped in try/except so one broken rule never
    crashes the whole post-scan pipeline.

    Supported actions:
      monitor / unmonitor             — Sonarr/Radarr
      transcode_via_tdarr             — Tdarr
      search_subs_via_bazarr          — Bazarr
      delete_sub_via_bazarr           — Bazarr
    """
    try:
        rules = [r for r in db.list_automation_rules() if r["enabled"]]
    except Exception as e:
        print(f"[automation] failed to list rules: {e}")
        return 0
    if not rules: return 0
    actions_run = 0

    by_integration = {}
    for r in rules:
        by_integration.setdefault(r["integration_id"], []).append(r)

    for int_id, ruleset in by_integration.items():
        try:
            server = db.get_integration(int_id)
            if not server or not server.get("enabled"): continue
            kind = server["kind"]
            plugin = make_plugin(server)
            if not plugin: continue
        except Exception as e:
            print(f"[automation] integration {int_id} setup failed: {e}")
            continue

        for r in ruleset:
            try:
                count = _apply_one_rule(r, server, kind, plugin)
                actions_run += count
                db.update_automation_rule(
                    r["id"],
                    last_run=datetime.now().isoformat(),
                    runs_count=(r.get("runs_count") or 0) + 1,
                    last_action_count=count,
                )
            except Exception as e:
                print(f"[automation] rule {r.get('id')} '{r.get('name')}' failed: {e}")

    return actions_run


def _apply_one_rule(rule, server, kind, plugin):
    """Apply one automation rule. Returns number of actions taken."""
    action = rule["action"]
    cfg = rule.get("action_config_obj") or {}
    file_cat = rule.get("file_category")
    sev_match = (rule.get("severity_match") or "highest").lower()

    # Find candidate files
    if action in ("monitor", "unmonitor"):
        if not hasattr(plugin, "set_monitored"): return 0
        files = db.files_for_automation(kind)
    elif action == "transcode_via_tdarr":
        if kind != "tdarr": return 0
        files = db.files_for_severity_filter(min_rank=0, file_category="media")
    elif action in ("search_subs_via_bazarr", "delete_sub_via_bazarr"):
        if kind != "bazarr": return 0
        cat = "subtitle" if action == "delete_sub_via_bazarr" else "media"
        files = db.files_for_severity_filter(min_rank=0, file_category=cat)
    else:
        return 0

    threshold = SEVERITY_RANK.get(rule["when_severity"], 0)
    cmp_op = rule["comparison"]

    count = 0
    for f in files:
        # Decide which severity rank to compare against based on mode
        sev_ranks_for_file = _file_severity_ranks(f["id"])
        highest = max(sev_ranks_for_file) if sev_ranks_for_file else 0
        lowest  = min(sev_ranks_for_file) if sev_ranks_for_file else 0

        if sev_match == "any":
            # Match if ANY of the file's severities triggers
            ranks_to_check = sev_ranks_for_file or [0]
        elif sev_match == "lowest":
            ranks_to_check = [lowest]
        else:  # highest (default)
            ranks_to_check = [highest]

        if not any(_matches_threshold(r, threshold, cmp_op) for r in ranks_to_check):
            continue
        if file_cat and f.get("category") and f.get("category") != file_cat:
            continue

        try:
            if action == "monitor" or action == "unmonitor":
                target = (action == "monitor")
                if f.get("monitored") == (1 if target else 0): continue
                arg = f["arr_file_id"] if kind == "sonarr" else f["arr_id"]
                if arg is None: continue
                ok, _ = plugin.set_monitored(arg, target)
                if ok:
                    db.update_file_monitored(f["id"], target)
                    count += 1
            elif action == "transcode_via_tdarr":
                kwargs = {}
                if cfg.get("library_id"):       kwargs["library_id"] = cfg["library_id"]
                elif cfg.get("plugin_id"):      kwargs["plugin_id"]  = cfg["plugin_id"]
                elif cfg.get("inline_profile"): kwargs["inline_profile"] = cfg["inline_profile"]
                else: continue
                ok, _ = plugin.queue_transcode(f["path"], **kwargs)
                if ok: count += 1
            elif action == "search_subs_via_bazarr":
                media_type = "movie" if f.get("arr_kind") == "radarr" else "episode"
                aid = f.get("arr_id")
                if not aid: continue
                ok, _ = plugin.search_subtitles(media_type, aid)
                if ok: count += 1
            elif action == "delete_sub_via_bazarr":
                media_type = "movie" if f.get("arr_kind") == "radarr" else "episode"
                ok, _ = plugin.delete_subtitle(f["path"], media_type, f.get("arr_id"))
                if ok: count += 1
        except Exception: pass

    return count


def _file_severity_ranks(file_id):
    """Return a list of severity ranks for all of this file's evaluations."""
    sevs = db.get_evaluations(file_id) or []
    return [SEVERITY_RANK.get(e.get("severity"), 0) for e in sevs]


def _matches_threshold(sev_rank, threshold, cmp_op):
    if cmp_op == "at_least": return sev_rank >= threshold
    if cmp_op == "at_most":  return sev_rank <= threshold
    if cmp_op == "equals":   return sev_rank == threshold
    return False


def _rule_matches(rule, sev_rank):
    threshold = SEVERITY_RANK.get(rule["when_severity"], 0)
    return _matches_threshold(sev_rank, threshold, rule["comparison"])
