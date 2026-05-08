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
    rule's action. Supported actions:

      monitor / unmonitor             — Sonarr/Radarr (existing)
      transcode_via_tdarr             — Tdarr (queues file)
      search_subs_via_bazarr          — Bazarr (re-search subs for a media)
      delete_sub_via_bazarr           — Bazarr (deletes the subtitle file)
    """
    rules = [r for r in db.list_automation_rules() if r["enabled"]]
    if not rules: return 0
    actions_run = 0

    by_integration = {}
    for r in rules:
        by_integration.setdefault(r["integration_id"], []).append(r)

    for int_id, ruleset in by_integration.items():
        server = db.get_integration(int_id)
        if not server or not server.get("enabled"): continue
        kind = server["kind"]
        plugin = make_plugin(server)
        if not plugin: continue

        for r in ruleset:
            count = _apply_one_rule(r, server, kind, plugin)
            actions_run += count
            db.update_automation_rule(
                r["id"],
                last_run=datetime.now().isoformat(),
                runs_count=(r.get("runs_count") or 0) + 1,
                last_action_count=count,
            )

    return actions_run


def _apply_one_rule(rule, server, kind, plugin):
    """Apply one automation rule. Returns number of actions taken."""
    action = rule["action"]
    cfg = rule.get("action_config_obj") or {}
    file_cat = rule.get("file_category")

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
        sev_rank = f.get("sev_rank") or 0
        if not _matches_threshold(sev_rank, threshold, cmp_op): continue
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
                else: continue  # nothing to do
                ok, _ = plugin.queue_transcode(f["path"], **kwargs)
                if ok: count += 1
            elif action == "search_subs_via_bazarr":
                # Need media_type + bazarr id from sonarr/radarr linkage
                media_type = "movie" if f.get("arr_kind") == "radarr" else "episode"
                aid = f.get("arr_id")
                if not aid: continue
                ok, _ = plugin.search_subtitles(media_type, aid)
                if ok: count += 1
            elif action == "delete_sub_via_bazarr":
                # f is the subtitle file
                media_type = "movie" if f.get("arr_kind") == "radarr" else "episode"
                ok, _ = plugin.delete_subtitle(f["path"], media_type, f.get("arr_id"))
                if ok: count += 1
        except Exception: pass

    return count


def _matches_threshold(sev_rank, threshold, cmp_op):
    if cmp_op == "at_least": return sev_rank >= threshold
    if cmp_op == "at_most":  return sev_rank <= threshold
    if cmp_op == "equals":   return sev_rank == threshold
    return False


def _rule_matches(rule, sev_rank):
    threshold = SEVERITY_RANK.get(rule["when_severity"], 0)
    return _matches_threshold(sev_rank, threshold, rule["comparison"])
