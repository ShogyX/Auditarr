#!/usr/bin/env python3
"""server.py — Flask API.

Routes (selected):
    GET  /                                  — frontend
    GET/POST /api/config
    POST /api/scan/start | /reeval | /targeted
    GET  /api/scan/<job>/status

    GET  /api/files                          — filtered file list
    GET  /api/files/<id>                     — full detail incl. probe + issues + device matrix
    POST /api/files/<id>/{rescan,delete,rename,move,hash,virustotal,monitor}

    GET  /api/stats                          — aggregate stats
    GET  /api/devices                        — device list
    GET  /api/severities                     — severity definitions

    GET  /api/integrations
    GET  /api/integrations/plugins           — available plugin types
    POST /api/integrations
    PUT  /api/integrations/<id>
    DELETE /api/integrations/<id>
    POST /api/integrations/<id>/test
    POST /api/integrations/<id>/sync
    POST /api/integrations/webhook/<id>      — webhook receiver
    GET  /api/integrations/events

    GET  /api/automation/rules
    POST /api/automation/rules
    DELETE /api/automation/rules/<id>
    POST /api/automation/run                  — manually run all rules
"""
import json
import os
import shutil
import threading
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

import db
import checks
import integrations
import scanner

app = Flask(__name__, static_folder="frontend", static_url_path="")
db.init()


# ─── Config ──────────────────────────────────────────────────────────────────

DEFAULT_CONFIG = {
    "library_paths": ["/media", "/mnt/media"],
    "workers": 4,
    "sample_offset_seconds": 60,
    "bitrate_threshold_mbps": 80,
    "schedule_enabled": False,
    "schedule_time": "02:00",
    "prune_missing": True,
    "compatibility_mode": "plex",   # plex | jellyfin | both
    "ignore_patterns": list(db.DEFAULT_IGNORE_PATTERNS),
    "media_extensions": list(checks.MEDIA_EXTS),
    "subtitle_extensions": list(checks.SUBTITLE_EXTS),
    "image_extensions":   list(checks.IMAGE_EXTS),
    "metadata_extensions":list(checks.METADATA_EXTS),
    "virustotal_api_key": "",
}
config_path = Path(__file__).parent / "config.json"

def load_config():
    if config_path.exists():
        try: return {**DEFAULT_CONFIG, **json.loads(config_path.read_text())}
        except Exception: pass
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    config_path.write_text(json.dumps(cfg, indent=2))


# ─── Scheduler ───────────────────────────────────────────────────────────────

_scheduler = BackgroundScheduler(daemon=True)
_scheduler.start()
_SCHEDULED_JOB_ID = "library_scan"


def _scheduled_scan_fn():
    cfg = load_config()
    scanner.start_full_scan(cfg)


def reload_schedule():
    """Read config and (re)install the cron job."""
    cfg = load_config()
    if _scheduler.get_job(_SCHEDULED_JOB_ID):
        _scheduler.remove_job(_SCHEDULED_JOB_ID)
    if cfg.get("schedule_enabled"):
        try:
            time_str = cfg.get("schedule_time", "02:00")
            h, m = map(int, time_str.split(":"))
            _scheduler.add_job(_scheduled_scan_fn, CronTrigger(hour=h, minute=m),
                               id=_SCHEDULED_JOB_ID, replace_existing=True)
        except Exception:
            pass

reload_schedule()


# ─── Frontend ────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("frontend", "index.html")


# ─── Config API ──────────────────────────────────────────────────────────────

@app.route("/api/config", methods=["GET"])
def get_config():
    return jsonify(load_config())


@app.route("/api/config", methods=["POST"])
def set_config():
    save_config(request.json)
    reload_schedule()
    return jsonify({"ok": True})


# ─── Scan API ────────────────────────────────────────────────────────────────

@app.route("/api/scan/start", methods=["POST"])
def start_full():
    return jsonify({"job_id": scanner.start_full_scan(load_config())})

@app.route("/api/scan/reeval", methods=["POST"])
def start_reeval():
    return jsonify({"job_id": scanner.start_reeval(load_config())})

@app.route("/api/scan/targeted", methods=["POST"])
def start_targeted():
    paths = (request.json or {}).get("paths", [])
    if not paths: return jsonify({"error": "No paths"}), 400
    return jsonify({"job_id": scanner.start_targeted_scan(load_config(), paths)})

@app.route("/api/scan/<job_id>/status")
def scan_status(job_id):
    s = db.get_scan(job_id)
    if not s: return jsonify({"error": "Not found"}), 404
    return jsonify(s)

@app.route("/api/scan/recent")
def scan_recent():
    return jsonify(db.list_scans(20))


# ─── Reference data ──────────────────────────────────────────────────────────

@app.route("/api/devices")
def devices():
    return jsonify({
        "plex": checks.PLEX_DEVICES,
        "jellyfin": checks.JELLYFIN_DEVICES,
        "all": checks.PLEX_DEVICES + checks.JELLYFIN_DEVICES,
        "ecosystem": checks.DEVICE_ECOSYSTEM,
    })

@app.route("/api/severities")
def severities():
    return jsonify({
        "order": db.SEVERITY,        # rank: ok=0 ... unplayable=5
        "labels": checks.SEVERITY_LABELS,
    })


# ─── Files API ───────────────────────────────────────────────────────────────

def _expand_file_row(f):
    """Inflate JSON columns and add computed fields."""
    f["issues"] = json.loads(f.pop("issues_json", None) or "[]")
    for iss in f["issues"]:
        try: iss["affected"] = json.loads(iss.get("affected") or "[]")
        except Exception: iss["affected"] = []
    rank = f.pop("sev_rank", 0) or 0
    # Map back from rank to severity name
    f["severity"] = ["ok","info","high_bitrate","possible_transcode","always_transcode","unplayable"][rank]
    if f.get("arr_metadata"):
        try: f["arr_metadata"] = json.loads(f["arr_metadata"])
        except Exception: pass
    f.pop("probe_json", None)
    return f


@app.route("/api/files")
def files_list():
    args = request.args
    files = db.list_files_filtered(
        severity=args.get("severity"),
        category=args.get("category"),
        file_category=args.get("file_category"),
        codec=args.get("codec"),
        arr_kind=args.get("arr_kind"),
        arr_id=args.get("arr_id", type=int),
        q=args.get("q"),
        monitored=(None if args.get("monitored") is None else args.get("monitored") == "1"),
        limit=args.get("limit", 5000, type=int),
        offset=args.get("offset", 0, type=int),
    )
    return jsonify([_expand_file_row(f) for f in files])


@app.route("/api/files/<int:file_id>")
def file_detail(file_id):
    f = db.get_file_by_id(file_id)
    if not f: return jsonify({"error": "Not found"}), 404
    if f.get("probe_json"):
        try: f["probe"] = json.loads(f["probe_json"])
        except Exception: f["probe"] = None
    f.pop("probe_json", None)
    if f.get("arr_metadata"):
        try: f["arr_metadata"] = json.loads(f["arr_metadata"])
        except Exception: pass
    f["issues"] = db.get_evaluations(file_id)
    for iss in f["issues"]:
        try: iss["affected"] = json.loads(iss.get("affected") or "[]")
        except Exception: iss["affected"] = []

    # Filter by compatibility mode
    cfg_now = load_config()
    compat_mode = cfg_now.get("compatibility_mode", "plex")
    for iss in f["issues"]:
        iss["affected"] = checks.filter_devices_for_mode(iss["affected"], compat_mode)

    # Build a UNIFIED device matrix — one entry per device with worst status across all issues
    device_map = {}
    SEV_PRIO = {"unplayable": 5, "always_transcode": 4, "possible_transcode": 3, "high_bitrate": 2, "info": 1, "ok": 0}
    STATUS_PRIO = {"fail": 3, "transcode": 2, "partial": 1, "ok": 0}
    for iss in f["issues"]:
        for a in iss.get("affected", []):
            dev = a.get("device")
            stat = a.get("status", "ok")
            existing = device_map.get(dev)
            new_entry = {
                "device": dev,
                "status": stat,
                "severity": iss["severity"],
                "ecosystem": checks.DEVICE_ECOSYSTEM.get(dev, "plex"),
                "issues": [{"rule_key": iss.get("rule_key"), "message": iss.get("message"), "severity": iss["severity"]}],
            }
            if not existing:
                device_map[dev] = new_entry
            else:
                if STATUS_PRIO.get(stat, 0) > STATUS_PRIO.get(existing["status"], 0):
                    existing["status"] = stat
                if SEV_PRIO.get(iss["severity"], 0) > SEV_PRIO.get(existing["severity"], 0):
                    existing["severity"] = iss["severity"]
                existing["issues"].append({"rule_key": iss.get("rule_key"), "message": iss.get("message"), "severity": iss["severity"]})

    # Determine the device list based on mode
    if compat_mode == "plex":     dev_list = checks.PLEX_DEVICES
    elif compat_mode == "jellyfin": dev_list = checks.JELLYFIN_DEVICES
    else:                          dev_list = checks.PLEX_DEVICES + checks.JELLYFIN_DEVICES

    matrix = []
    for d in dev_list:
        e = device_map.get(d, {"device": d, "status": "ok", "severity": "ok",
                               "ecosystem": checks.DEVICE_ECOSYSTEM.get(d, "plex"), "issues": []})
        matrix.append(e)
    f["device_matrix"] = matrix
    f["compatibility_mode"] = compat_mode

    # Paired media (subtitles)
    if f.get("paired_media_id"):
        paired = db.get_file_by_id(f["paired_media_id"])
        if paired:
            paired.pop("probe_json", None)
            f["paired_media"] = {"id": paired["id"], "path": paired["path"], "name": paired["name"]}

    return jsonify(f)


@app.route("/api/files/<int:file_id>/rescan", methods=["POST"])
def file_rescan(file_id):
    f = db.get_file_by_id(file_id)
    if not f: return jsonify({"error": "Not found"}), 404
    return jsonify({"job_id": scanner.start_targeted_scan(load_config(), [f["path"]])})

@app.route("/api/files/<int:file_id>/delete", methods=["POST"])
def file_delete(file_id):
    f = db.get_file_by_id(file_id)
    if not f: return jsonify({"error": "Not found"}), 404
    try:
        p = Path(f["path"])
        if p.exists(): p.unlink()
        db.delete_file_by_path(f["path"])
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/files/<int:file_id>/rename", methods=["POST"])
def file_rename(file_id):
    f = db.get_file_by_id(file_id)
    if not f: return jsonify({"error": "Not found"}), 404
    new_name = (request.json or {}).get("new_name", "").strip()
    if not new_name: return jsonify({"error": "No new_name"}), 400
    src = Path(f["path"]); dst = src.parent / new_name
    try:
        src.rename(dst); db.update_file_path(str(src), str(dst))
        return jsonify({"ok": True, "new_path": str(dst)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/files/<int:file_id>/move", methods=["POST"])
def file_move(file_id):
    f = db.get_file_by_id(file_id)
    if not f: return jsonify({"error": "Not found"}), 404
    dest_dir = (request.json or {}).get("destination", "").strip()
    if not dest_dir: return jsonify({"error": "No destination"}), 400
    src = Path(f["path"]); dst_dir = Path(dest_dir)
    try:
        dst_dir.mkdir(parents=True, exist_ok=True)
        dst = dst_dir / src.name
        shutil.move(str(src), str(dst))
        db.update_file_path(str(src), str(dst))
        return jsonify({"ok": True, "new_path": str(dst)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/files/<int:file_id>/hash", methods=["POST"])
def file_hash(file_id):
    f = db.get_file_by_id(file_id)
    if not f: return jsonify({"error": "Not found"}), 404
    return jsonify({"hash": scanner.compute_hash(f["path"])})

@app.route("/api/files/<int:file_id>/virustotal", methods=["POST"])
def file_virustotal(file_id):
    f = db.get_file_by_id(file_id)
    if not f: return jsonify({"error": "Not found"}), 404
    cfg = load_config()
    api_key = cfg.get("virustotal_api_key", "")
    if not api_key:
        return jsonify({"error": "No VirusTotal API key configured"}), 400
    file_hash = f.get("hash_sha256") or scanner.compute_hash(f["path"])
    if not file_hash: return jsonify({"error": "Could not compute hash"}), 500
    try:
        req = urllib.request.Request(f"https://www.virustotal.com/api/v3/files/{file_hash}",
                                     headers={"x-apikey": api_key})
        with urllib.request.urlopen(req, timeout=15) as resp:
            vt = json.loads(resp.read())
        attrs = vt.get("data",{}).get("attributes",{})
        s = attrs.get("last_analysis_stats",{})
        return jsonify({
            "malicious": s.get("malicious",0), "suspicious": s.get("suspicious",0),
            "undetected": s.get("undetected",0), "harmless": s.get("harmless",0),
            "permalink": f"https://www.virustotal.com/gui/file/{file_hash}",
            "name": attrs.get("meaningful_name",""), "hash": file_hash,
        })
    except urllib.error.HTTPError as e:
        if e.code == 404: return jsonify({"not_found": True, "hash": file_hash})
        return jsonify({"error": f"VT API error {e.code}"}), 500
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/files/<int:file_id>/monitor", methods=["POST"])
def file_monitor(file_id):
    f = db.get_file_by_id(file_id)
    if not f: return jsonify({"error": "Not found"}), 404
    monitored = bool((request.json or {}).get("monitored", True))
    ok, msg = integrations.set_monitored(f, monitored)
    return jsonify({"ok": ok, "message": msg})


# ─── Stats API ───────────────────────────────────────────────────────────────

@app.route("/api/stats")
def stats():
    return jsonify(db.stats_summary())


# ─── Integrations API ────────────────────────────────────────────────────────

@app.route("/api/integrations/plugins")
def integration_plugins():
    return jsonify(integrations.all_plugins())

@app.route("/api/integrations", methods=["GET"])
def integrations_list():
    return jsonify(db.list_integrations(request.args.get("kind")))

@app.route("/api/integrations", methods=["POST"])
def integrations_add():
    d = request.json or {}
    sid = db.add_integration(
        kind=d.get("kind"), name=d.get("name"), base_url=d.get("base_url",""),
        api_key=d.get("api_key",""), poll_interval=d.get("poll_interval",900),
        options=d.get("options",{}),
    )
    return jsonify({"id": sid})

@app.route("/api/integrations/<int:server_id>", methods=["PUT"])
def integrations_update(server_id):
    d = request.json or {}
    db.update_integration(server_id, **{k:v for k,v in d.items()
        if k in ("kind","name","base_url","api_key","enabled","poll_interval","options")})
    return jsonify({"ok": True})

@app.route("/api/integrations/<int:server_id>", methods=["DELETE"])
def integrations_delete(server_id):
    db.delete_integration(server_id); return jsonify({"ok": True})

@app.route("/api/integrations/<int:server_id>/test", methods=["POST"])
def integrations_test(server_id):
    s = db.get_integration(server_id)
    if not s: return jsonify({"error": "Not found"}), 404
    ok, msg = integrations.test_connection(s)
    return jsonify({"ok": ok, "message": msg})

@app.route("/api/integrations/<int:server_id>/sync", methods=["POST"])
def integrations_sync(server_id):
    s = db.get_integration(server_id)
    if not s: return jsonify({"error": "Not found"}), 404
    threading.Thread(target=integrations.sync_server, args=(s,), daemon=True).start()
    return jsonify({"ok": True, "message": "Sync started in background"})

@app.route("/api/integrations/webhook/<int:server_id>", methods=["POST"])
def integrations_webhook(server_id):
    payload = request.json or {}
    parsed = integrations.record_webhook(server_id, payload)
    if parsed["event_type"] in {"Download","Upgrade","Rename"}:
        existing = [p for p in (parsed.get("file_paths") or []) if Path(p).exists()]
        if existing:
            scanner.start_targeted_scan(load_config(), existing)
    elif "Delete" in parsed["event_type"]:
        for p in parsed.get("file_paths") or []:
            db.delete_file_by_path(p)
    return jsonify({"ok": True, "event": parsed["event_type"], "files": len(parsed["file_paths"])})

@app.route("/api/integrations/events")
def integrations_events():
    return jsonify(db.list_integration_events(50))


# ─── Automation rules ────────────────────────────────────────────────────────

@app.route("/api/automation/rules", methods=["GET"])
def automation_list():
    return jsonify(db.list_automation_rules(request.args.get("integration_id", type=int)))

@app.route("/api/automation/rules", methods=["POST"])
def automation_add():
    d = request.json or {}
    rid = db.add_automation_rule(
        integration_id=d["integration_id"], name=d["name"],
        when_severity=d["when_severity"], comparison=d["comparison"],
        action=d["action"], enabled=d.get("enabled", 1),
    )
    return jsonify({"id": rid})

@app.route("/api/automation/rules/<int:rule_id>", methods=["DELETE"])
def automation_delete(rule_id):
    db.delete_automation_rule(rule_id); return jsonify({"ok": True})

@app.route("/api/automation/rules/<int:rule_id>", methods=["PUT"])
def automation_update(rule_id):
    d = request.json or {}
    db.update_automation_rule(rule_id, **{k:v for k,v in d.items() if k in ("name","when_severity","comparison","action","enabled")})
    return jsonify({"ok": True})

@app.route("/api/automation/run", methods=["POST"])
def automation_run():
    n = integrations.run_automation_rules() or 0
    return jsonify({"actions_run": n})


# ─── Custom rules ────────────────────────────────────────────────────────────

@app.route("/api/rules/schema")
def rules_schema():
    """Field + operator catalog for the visual builder."""
    return jsonify(db.custom_rule_field_options())


@app.route("/api/rules", methods=["GET"])
def rules_list():
    return jsonify(db.list_custom_rules())


@app.route("/api/rules/<int:rule_id>")
def rules_get(rule_id):
    r = db.get_custom_rule(rule_id)
    if not r: return jsonify({"error": "Not found"}), 404
    return jsonify(r)


@app.route("/api/rules", methods=["POST"])
def rules_add():
    d = request.json or {}
    if not d.get("name") or not d.get("severity") or not d.get("spec"):
        return jsonify({"error": "name, severity, spec required"}), 400
    if d.get("severity") not in db.SEVERITY:
        return jsonify({"error": f"Invalid severity. Must be one of {db.SEVERITY}"}), 400
    rid = db.add_custom_rule(
        name=d["name"], description=d.get("description",""),
        severity=d["severity"], category=d.get("category","custom"),
        spec=d["spec"], affected_devices=d.get("affected_devices") or [],
        message=d.get("message", d["name"]),
        detail=d.get("detail",""), enabled=d.get("enabled", 1),
    )
    return jsonify({"id": rid})


@app.route("/api/rules/<int:rule_id>", methods=["PUT"])
def rules_update(rule_id):
    d = request.json or {}
    if "severity" in d and d["severity"] not in db.SEVERITY:
        return jsonify({"error": f"Invalid severity"}), 400
    keys = ("name","description","severity","category","spec","affected_devices_list",
            "message","detail","enabled")
    payload = {}
    for k in keys:
        if k in d: payload[k] = d[k]
    if "affected_devices" in d and "affected_devices_list" not in payload:
        payload["affected_devices_list"] = d["affected_devices"]
    db.update_custom_rule(rule_id, **payload)
    return jsonify({"ok": True})


@app.route("/api/rules/<int:rule_id>", methods=["DELETE"])
def rules_delete(rule_id):
    db.delete_custom_rule(rule_id)
    return jsonify({"ok": True})


@app.route("/api/rules/test", methods=["POST"])
def rules_test():
    """Test a rule spec without saving it. Returns matching file count + sample paths."""
    d = request.json or {}
    spec = d.get("spec") or {}
    ids = db.query_files_by_rule_spec(spec, limit=200)
    sample = []
    for fid in ids[:20]:
        f = db.get_file_by_id(fid)
        if f: sample.append({"id": f["id"], "path": f["path"], "name": f["name"]})
    return jsonify({"match_count": len(ids), "sample": sample})


@app.route("/api/rules/<int:rule_id>/preview")
def rules_preview(rule_id):
    """Show what files this rule currently matches."""
    rule = db.get_custom_rule(rule_id)
    if not rule: return jsonify({"error": "Not found"}), 404
    ids = db.query_files_by_rule_spec(rule.get("spec") or {}, limit=200)
    sample = []
    for fid in ids[:20]:
        f = db.get_file_by_id(fid)
        if f: sample.append({"id": f["id"], "path": f["path"], "name": f["name"]})
    return jsonify({"match_count": len(ids), "sample": sample})


@app.route("/api/rules/apply", methods=["POST"])
def rules_apply():
    """Re-run all custom rules against existing files (no re-eval of built-ins)."""
    rules = db.list_custom_rules(only_enabled=True)
    applied = 0
    for f in db.all_files_for_eval():
        record = {
            "extension":   f.get("extension"),
            "size_bytes":  f.get("size_bytes"),
            "category":    f.get("category"),
            "scan_status": f.get("scan_status"),
            "probe":       f.get("probe"),
            "path":        f["path"],
            "name":        Path(f["path"]).name,
        }
        # Get existing built-in evaluations (keep them, just refresh customs)
        existing = [dict(e) for e in db.get_evaluations(f["id"])]
        for e in existing:
            try: e["affected"] = json.loads(e.get("affected") or "[]")
            except Exception: e["affected"] = []
        # Drop existing custom_* issues
        kept = [e for e in existing if not (e.get("rule_key") or "").startswith("custom_")]
        for rule in rules:
            iss = checks.evaluate_custom_rule(record, rule)
            if iss:
                kept.append(iss)
                applied += 1
        db.replace_evaluations(f["id"], kept)
    return jsonify({"matches": applied, "rules": len(rules)})


# ─── Polling worker ──────────────────────────────────────────────────────────

_poller = integrations.PollingWorker()
_poller.start()


if __name__ == "__main__":
    print("Auditarr server running at http://localhost:7842")
    app.run(host="0.0.0.0", port=7842, debug=False, threaded=True)
