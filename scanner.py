"""
scanner.py — Filesystem walk → categorise → probe → store → evaluate.

Three job kinds:
  full     — walk all configured paths
  targeted — scan specific paths (webhook handler)
  reeval   — re-run evaluation on stored data only (fast, no ffprobe)

Subtitle pairing happens in a second pass after media files are indexed.
Automation rules run after every job completes.
"""
import hashlib
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import db
import checks
import integrations


# ─── File hashing ─────────────────────────────────────────────────────────────

def compute_hash(path, algo="sha256"):
    h = hashlib.new(algo)
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None


# ─── Single-file classification ───────────────────────────────────────────────

def classify_file(path: Path, cfg: dict) -> dict:
    """
    Probe + categorise a file. Returns a record ready for db.upsert_file().
    Returns None if the file is on the ignore list (caller skips).
    """
    ignore_patterns = cfg.get("ignore_patterns") or []
    if checks.should_ignore(path, ignore_patterns):
        return None

    record = {
        "path": str(path),
        "name": path.name,
        "extension": path.suffix.lower(),
        "size_bytes": 0,
        "mtime": None,
        "scan_status": "ok",
    }

    try:
        st = path.stat()
        record["size_bytes"] = st.st_size
        record["mtime"] = st.st_mtime
    except OSError:
        record["scan_status"] = "missing"
        return record

    # Categorise
    record["category"] = checks.categorise_file(path)

    # For non-media files, optionally hash the junk ones
    if record["category"] != "media":
        if record["category"] == "junk":
            # Hash junk files for VirusTotal lookup later
            record["hash"] = compute_hash(path)
        return record

    # Media: probe (sample-only)
    if record["size_bytes"] == 0:
        return record  # rules will flag empty

    sample_offset = cfg.get("sample_offset_seconds", 60)
    probe = checks.probe_sample(path, sample_offset)
    if not probe:
        record["scan_status"] = "probe_failed"
        return record

    record["probe"] = probe
    record.update(checks.derive_fields(probe))
    return record


# ─── Subtitle pairing (second pass after files are in DB) ─────────────────────

def pair_subtitle_to_media(sub_record: dict) -> dict | None:
    """Find a media file in the same folder whose name matches the subtitle's base name."""
    if sub_record.get("category") != "subtitle":
        return None
    p = Path(sub_record["path"])
    base, _, _ = checks.parse_sub_filename(p.name)
    folder = str(p.parent)
    candidates = db.find_media_in_folder(folder, base)
    if candidates:
        # Prefer exact base match
        for c in candidates:
            cstem = Path(c["name"]).stem
            if cstem == base:
                return c
        return candidates[0]
    return None


# ─── Job runner helpers ───────────────────────────────────────────────────────

class _Progress:
    def __init__(self):
        self.processed = 0
        self.total = 0
        self.lock = threading.Lock()
    def bump(self):
        with self.lock: self.processed += 1


def _set_progress(job_id, prog):
    db.update_scan(job_id, processed=prog.processed, total=prog.total)


def _scan_one_and_eval(path: Path, cfg: dict, prog: _Progress, job_id: str, bitrate_threshold: int, custom_rules: list = None):
    """Worker — scan a single file, store + evaluate."""
    try:
        record = classify_file(path, cfg)
        if record is None:
            # Ignored — don't even add to DB
            return
        file_id = db.upsert_file(record)

        # For subtitles, pair after the file is in DB so find_media_in_folder works
        paired = None
        if record.get("category") == "subtitle":
            paired = pair_subtitle_to_media(record)
            db.update_file_paired(file_id, paired["id"] if paired else None)

        # Evaluate
        eval_record = {
            "extension": record.get("extension"),
            "size_bytes": record.get("size_bytes"),
            "category": record.get("category"),
            "scan_status": record.get("scan_status"),
            "probe": record.get("probe"),
            "path": record["path"],
            "name": record.get("name"),
            "codec": record.get("codec"),
            "audio_codec": record.get("audio_codec"),
            "container": record.get("container"),
            "resolution": record.get("resolution"),
            "dovi_profile": record.get("dovi_profile"),
            "bitrate": record.get("bitrate"),
            "duration_sec": record.get("duration_sec"),
        }
        issues = checks.evaluate(eval_record,
                                 bitrate_threshold=bitrate_threshold,
                                 paired_media=paired)

        # Apply custom rules
        for rule in (custom_rules or []):
            iss = checks.evaluate_custom_rule(eval_record, rule)
            if iss: issues.append(iss)

        db.replace_evaluations(file_id, issues)
    finally:
        prog.bump()
        if prog.processed % 25 == 0:
            _set_progress(job_id, prog)


# ─── Job entry points ─────────────────────────────────────────────────────────

def run_full_scan(job_id: str, cfg: dict):
    db.update_scan(job_id, status="running", started_at=datetime.now().isoformat())

    paths = []
    for p in cfg.get("library_paths", []):
        root = Path(p)
        if root.exists():
            for dp, _, fnames in os.walk(root):
                for fn in fnames:
                    paths.append(Path(dp) / fn)

    prog = _Progress()
    prog.total = len(paths)
    _set_progress(job_id, prog)

    bitrate_threshold = (cfg.get("bitrate_threshold_mbps", 80)) * 1_000_000
    workers = max(1, cfg.get("workers", 4))
    custom_rules = db.list_custom_rules(only_enabled=True)

    # Pass 1: scan every file (subtitles will need a second pass for pairing)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_scan_one_and_eval, p, cfg, prog, job_id, bitrate_threshold, custom_rules) for p in paths]
        for f in as_completed(futures):
            try: f.result()
            except Exception: pass

    # Pass 2: re-pair all subtitles now that all media files are indexed
    _repair_subtitle_pairs(cfg, bitrate_threshold, custom_rules)

    # Prune missing files + files now matching ignore patterns
    if cfg.get("prune_missing", True):
        on_disk = {str(p) for p in paths}
        ignore_patterns = cfg.get("ignore_patterns") or []
        roots = [str(Path(p)) for p in cfg.get("library_paths", [])]
        for stored in db.all_file_paths():
            in_lib = any(stored.startswith(r) for r in roots)
            if not in_lib: continue
            stored_path = Path(stored)
            # Missing on disk
            if stored not in on_disk:
                db.delete_file_by_path(stored)
                continue
            # Newly matches ignore patterns
            if checks.should_ignore(stored_path, ignore_patterns):
                db.delete_file_by_path(stored)

    db.update_scan(job_id, status="done", finished_at=datetime.now().isoformat(),
                   processed=prog.processed, total=prog.total)

    # Run automation rules
    try: integrations.run_automation_rules()
    except Exception: pass


def run_targeted_scan(job_id: str, cfg: dict, paths: list[str]):
    db.update_scan(job_id, status="running", started_at=datetime.now().isoformat())
    prog = _Progress()
    prog.total = len(paths)
    _set_progress(job_id, prog)

    bitrate_threshold = (cfg.get("bitrate_threshold_mbps", 80)) * 1_000_000
    workers = max(1, min(cfg.get("workers", 4), len(paths) or 1))
    custom_rules = db.list_custom_rules(only_enabled=True)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_scan_one_and_eval, Path(p), cfg, prog, job_id, bitrate_threshold, custom_rules) for p in paths]
        for f in as_completed(futures):
            try: f.result()
            except Exception: pass

    db.update_scan(job_id, status="done", finished_at=datetime.now().isoformat(),
                   processed=prog.processed, total=prog.total)
    try: integrations.run_automation_rules()
    except Exception: pass


def run_reeval(job_id: str, cfg: dict):
    """Re-run evaluation on stored probe data only.

    Does NOT walk the filesystem.
    Does NOT call ffprobe.
    Does re-read each external subtitle file (to validate format) — fast.
    """
    db.update_scan(job_id, status="running", started_at=datetime.now().isoformat(), kind="reeval")

    bitrate_threshold = (cfg.get("bitrate_threshold_mbps", 80)) * 1_000_000
    custom_rules = db.list_custom_rules(only_enabled=True)

    files = list(db.all_files_for_eval())
    prog = _Progress()
    prog.total = len(files)
    _set_progress(job_id, prog)

    for f in files:
        record = {
            "extension":   f.get("extension"),
            "size_bytes":  f.get("size_bytes"),
            "category":    f.get("category"),
            "scan_status": f.get("scan_status"),
            "probe":       f.get("probe"),
            "path":        f["path"],
            "name":        Path(f["path"]).name,
        }
        # Pair subtitles using current paired_media_id (already set on scan)
        paired = None
        if record["category"] == "subtitle":
            if f.get("paired_media_id"):
                paired = db.get_file_by_id(f["paired_media_id"])
            else:
                paired = pair_subtitle_to_media(record)
                if paired:
                    db.update_file_paired(f["id"], paired["id"])

        issues = checks.evaluate(record, bitrate_threshold=bitrate_threshold, paired_media=paired)
        for rule in custom_rules:
            iss = checks.evaluate_custom_rule(record, rule)
            if iss: issues.append(iss)
        db.replace_evaluations(f["id"], issues)
        prog.bump()
        if prog.processed % 100 == 0:
            _set_progress(job_id, prog)

    db.update_scan(job_id, status="done", finished_at=datetime.now().isoformat(),
                   processed=prog.processed, total=prog.total)

    try: integrations.run_automation_rules()
    except Exception: pass


def _repair_subtitle_pairs(cfg, bitrate_threshold, custom_rules=None):
    """Re-pair all subtitles + re-evaluate them now that media is fully indexed."""
    custom_rules = custom_rules or []
    for f in db.all_files_for_eval():
        if f.get("category") != "subtitle":
            continue
        record = {
            "extension":   f.get("extension"),
            "size_bytes":  f.get("size_bytes"),
            "category":    "subtitle",
            "scan_status": f.get("scan_status"),
            "probe":       f.get("probe"),
            "path":        f["path"],
        }
        paired = pair_subtitle_to_media(record)
        db.update_file_paired(f["id"], paired["id"] if paired else None)
        issues = checks.evaluate(record, bitrate_threshold=bitrate_threshold, paired_media=paired)
        for rule in custom_rules:
            iss = checks.evaluate_custom_rule(record, rule)
            if iss: issues.append(iss)
        db.replace_evaluations(f["id"], issues)


# ─── Public entry points (used by Flask routes) ───────────────────────────────

def start_full_scan(cfg):
    job_id = str(uuid.uuid4())[:8]
    db.create_scan(job_id, "full", cfg)
    threading.Thread(target=run_full_scan, args=(job_id, cfg), daemon=True).start()
    return job_id

def start_targeted_scan(cfg, paths):
    job_id = str(uuid.uuid4())[:8]
    db.create_scan(job_id, "targeted", cfg)
    threading.Thread(target=run_targeted_scan, args=(job_id, cfg, paths), daemon=True).start()
    return job_id

def start_reeval(cfg):
    job_id = str(uuid.uuid4())[:8]
    db.create_scan(job_id, "reeval", cfg)
    threading.Thread(target=run_reeval, args=(job_id, cfg), daemon=True).start()
    return job_id
