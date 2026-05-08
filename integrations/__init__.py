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
    For every enabled rule, find files matching the condition and apply the action.
    Called after every scan/eval cycle.
    """
    rules = [r for r in db.list_automation_rules() if r["enabled"]]
    if not rules: return
    actions_run = 0

    # Group rules by integration
    by_integration = {}
    for r in rules:
        by_integration.setdefault(r["integration_id"], []).append(r)

    for int_id, ruleset in by_integration.items():
        server = db.get_integration(int_id)
        if not server or not server.get("enabled"): continue
        kind = server["kind"]
        plugin = make_plugin(server)
        if not plugin or not hasattr(plugin, "set_monitored"): continue

        files = db.files_for_automation(kind)
        for f in files:
            sev_rank = f.get("sev_rank") or 0
            current_monitored = f.get("monitored")
            for r in ruleset:
                if not _rule_matches(r, sev_rank): continue
                target_monitored = (r["action"] == "monitor")
                if current_monitored == (1 if target_monitored else 0):
                    continue  # already at target state
                # Apply
                arg = f["arr_file_id"] if kind == "sonarr" else f["arr_id"]
                try:
                    ok, _ = plugin.set_monitored(arg, target_monitored)
                    if ok:
                        db.update_file_monitored(f["id"], target_monitored)
                        actions_run += 1
                except Exception: pass

        # Mark rules as run
        for r in ruleset:
            db.update_automation_rule(r["id"], last_run=datetime.now().isoformat())

    return actions_run


def _rule_matches(rule, sev_rank):
    threshold = SEVERITY_RANK.get(rule["when_severity"], 0)
    cmp = rule["comparison"]
    if cmp == "at_least": return sev_rank >= threshold
    if cmp == "at_most":  return sev_rank <= threshold
    if cmp == "equals":   return sev_rank == threshold
    return False
