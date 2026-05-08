"""
db.py — SQLite persistence layer (v2).

CHANGES vs v1:
  - severity values now from the new 6-level scale
  - files.is_media replaced by files.category
  - arr_servers replaced by generic 'integrations' table
  - new 'automation_rules' table for severity-driven monitoring toggles
  - new 'monitored' column on files
"""
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).parent / "media_audit.db"
_local = threading.local()
_init_lock = threading.Lock()
_initialized = False

# ─── Severity scale ───────────────────────────────────────────────────────────
SEVERITY = ["ok", "info", "high_bitrate", "possible_transcode", "always_transcode", "unplayable"]
SEVERITY_RANK = {s: i for i, s in enumerate(SEVERITY)}

DEFAULT_IGNORE_PATTERNS = [
    ".plexmatch", ".DS_Store", "Thumbs.db", "desktop.ini",
    ".nomedia", "@eaDir", ".AppleDouble", ".gitkeep",
]
CATEGORIES = ["media", "subtitle", "image", "metadata", "junk", "ignored"]


def _connect():
    if not hasattr(_local, "conn"):
        conn = sqlite3.connect(DB_PATH, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        _local.conn = conn
    return _local.conn


@contextmanager
def cursor():
    conn = _connect()
    cur = conn.cursor()
    try: yield cur
    finally: cur.close()


def init():
    global _initialized
    with _init_lock:
        if _initialized: return
        with cursor() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS files (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    path            TEXT NOT NULL UNIQUE,
                    name            TEXT,
                    extension       TEXT,
                    size_bytes      INTEGER,
                    mtime           REAL,
                    category        TEXT,
                    hash_sha256     TEXT,
                    codec           TEXT,
                    audio_codec     TEXT,
                    resolution      TEXT,
                    container       TEXT,
                    dovi_profile    TEXT,
                    duration_sec    REAL,
                    bitrate         INTEGER,
                    probe_json      TEXT,
                    paired_media_id INTEGER,
                    arr_kind        TEXT,
                    arr_id          INTEGER,
                    arr_file_id     INTEGER,
                    arr_metadata    TEXT,
                    monitored       INTEGER,
                    first_scanned   TEXT,
                    last_scanned    TEXT,
                    last_evaluated  TEXT,
                    scan_status     TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_files_path     ON files(path);
                CREATE INDEX IF NOT EXISTS idx_files_category ON files(category);
                CREATE INDEX IF NOT EXISTS idx_files_codec    ON files(codec);
                CREATE INDEX IF NOT EXISTS idx_files_dovi     ON files(dovi_profile);
                CREATE INDEX IF NOT EXISTS idx_files_arr      ON files(arr_kind, arr_id);

                CREATE TABLE IF NOT EXISTS evaluations (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id       INTEGER NOT NULL,
                    severity      TEXT NOT NULL,
                    category      TEXT NOT NULL,
                    rule_key      TEXT,
                    message       TEXT,
                    detail        TEXT,
                    affected      TEXT,
                    evaluated_at  TEXT,
                    FOREIGN KEY(file_id) REFERENCES files(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_eval_file     ON evaluations(file_id);
                CREATE INDEX IF NOT EXISTS idx_eval_severity ON evaluations(severity);
                CREATE INDEX IF NOT EXISTS idx_eval_category ON evaluations(category);

                CREATE TABLE IF NOT EXISTS scans (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id       TEXT UNIQUE,
                    kind         TEXT,
                    status       TEXT,
                    started_at   TEXT,
                    finished_at  TEXT,
                    total        INTEGER DEFAULT 0,
                    processed    INTEGER DEFAULT 0,
                    config_json  TEXT
                );

                CREATE TABLE IF NOT EXISTS integrations (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind          TEXT NOT NULL,
                    name          TEXT NOT NULL,
                    base_url      TEXT NOT NULL,
                    api_key       TEXT,
                    enabled       INTEGER DEFAULT 1,
                    last_sync     TEXT,
                    last_error    TEXT,
                    poll_interval INTEGER DEFAULT 900,
                    options       TEXT
                );

                CREATE TABLE IF NOT EXISTS integration_events (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    integration_id  INTEGER,
                    event_type      TEXT,
                    kind            TEXT,
                    payload         TEXT,
                    file_paths      TEXT,
                    received_at     TEXT,
                    processed       INTEGER DEFAULT 0,
                    FOREIGN KEY(integration_id) REFERENCES integrations(id) ON DELETE SET NULL
                );

                CREATE TABLE IF NOT EXISTS automation_rules (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    integration_id  INTEGER NOT NULL,
                    name            TEXT NOT NULL,
                    when_severity   TEXT NOT NULL,
                    comparison      TEXT NOT NULL,
                    action          TEXT NOT NULL,
                    enabled         INTEGER DEFAULT 1,
                    last_run        TEXT,
                    FOREIGN KEY(integration_id) REFERENCES integrations(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS custom_rules (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    name            TEXT NOT NULL,
                    description     TEXT,
                    severity        TEXT NOT NULL,    -- one of SEVERITY
                    category        TEXT,             -- defaults to 'custom'
                    spec_json       TEXT NOT NULL,    -- {conditions:[{field,op,value}], match:'all|any'}
                    affected_devices TEXT,            -- JSON list of {device,status}; empty = all OK
                    message         TEXT,             -- shown as the issue headline
                    detail          TEXT,             -- long description
                    enabled         INTEGER DEFAULT 1,
                    created_at      TEXT,
                    updated_at      TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_custom_rules_enabled ON custom_rules(enabled);
            """)
        _initialized = True


# ─── Files ────────────────────────────────────────────────────────────────────

def upsert_file(record):
    now = datetime.now().isoformat()
    fields = {
        "path": record["path"], "name": record.get("name"),
        "extension": record.get("extension"), "size_bytes": record.get("size_bytes", 0),
        "mtime": record.get("mtime"), "category": record.get("category", "junk"),
        "hash_sha256": record.get("hash"), "codec": record.get("codec"),
        "audio_codec": record.get("audio_codec"), "resolution": record.get("resolution"),
        "container": record.get("container"), "dovi_profile": record.get("dovi_profile"),
        "duration_sec": record.get("duration_sec"), "bitrate": record.get("bitrate"),
        "probe_json": json.dumps(record.get("probe")) if record.get("probe") else None,
        "scan_status": record.get("scan_status", "ok"),
        "last_scanned": now, "last_evaluated": now,
    }
    with cursor() as c:
        existing = c.execute("SELECT id FROM files WHERE path=?", (record["path"],)).fetchone()
        if existing:
            sets = ", ".join(f"{k}=:{k}" for k in fields)
            fields["id"] = existing["id"]
            c.execute(f"UPDATE files SET {sets} WHERE id=:id", fields)
            return existing["id"]
        fields["first_scanned"] = now
        cols = ", ".join(fields.keys())
        ph = ", ".join(":" + k for k in fields)
        c.execute(f"INSERT INTO files ({cols}) VALUES ({ph})", fields)
        return c.lastrowid


def get_file(path):
    with cursor() as c:
        r = c.execute("SELECT * FROM files WHERE path=?", (path,)).fetchone()
        return dict(r) if r else None


def get_file_by_id(file_id):
    with cursor() as c:
        r = c.execute("SELECT * FROM files WHERE id=?", (file_id,)).fetchone()
        return dict(r) if r else None


def all_file_paths():
    with cursor() as c:
        return {r["path"] for r in c.execute("SELECT path FROM files")}


def delete_file_by_path(path):
    with cursor() as c:
        c.execute("DELETE FROM files WHERE path=?", (path,))


def update_file_path(old_path, new_path):
    with cursor() as c:
        c.execute("UPDATE files SET path=?, name=? WHERE path=?", (new_path, Path(new_path).name, old_path))


def update_file_paired(file_id, paired_media_id):
    with cursor() as c:
        c.execute("UPDATE files SET paired_media_id=? WHERE id=?", (paired_media_id, file_id))


def update_file_monitored(file_id, monitored):
    with cursor() as c:
        c.execute("UPDATE files SET monitored=? WHERE id=?", (1 if monitored else 0, file_id))


def find_media_in_folder(folder, base_name):
    with cursor() as c:
        rows = c.execute("""
            SELECT id, path, name FROM files
            WHERE category='media' AND path LIKE ? AND name LIKE ?
        """, (folder + "/%", base_name + "%")).fetchall()
        return [dict(r) for r in rows]


def list_files_filtered(severity=None, category=None, file_category=None,
                        codec=None, arr_kind=None, arr_id=None, q=None,
                        monitored=None, limit=10000, offset=0):
    sql = """
    SELECT f.*,
           (SELECT COUNT(*) FROM evaluations e WHERE e.file_id=f.id) AS issue_count,
           (SELECT json_group_array(json_object(
                'severity', e.severity, 'category', e.category,
                'rule_key', e.rule_key, 'message', e.message,
                'detail', e.detail, 'affected', e.affected))
            FROM evaluations e WHERE e.file_id=f.id) AS issues_json,
           (SELECT MAX(CASE e.severity
              WHEN 'unplayable' THEN 5 WHEN 'always_transcode' THEN 4
              WHEN 'possible_transcode' THEN 3 WHEN 'high_bitrate' THEN 2
              WHEN 'info' THEN 1 ELSE 0 END)
            FROM evaluations e WHERE e.file_id=f.id) AS sev_rank
    FROM files f
    """
    where, params = [], []
    if severity:
        where.append("EXISTS(SELECT 1 FROM evaluations e WHERE e.file_id=f.id AND e.severity=?)")
        params.append(severity)
    if category:
        where.append("EXISTS(SELECT 1 FROM evaluations e WHERE e.file_id=f.id AND e.category=?)")
        params.append(category)
    if file_category:
        where.append("f.category=?"); params.append(file_category)
    if codec:
        where.append("f.codec=?"); params.append(codec)
    if arr_kind:
        where.append("f.arr_kind=?"); params.append(arr_kind)
    if arr_id is not None:
        where.append("f.arr_id=?"); params.append(arr_id)
    if monitored is not None:
        where.append("f.monitored=?"); params.append(1 if monitored else 0)
    if q:
        where.append("(LOWER(f.path) LIKE ? OR EXISTS(SELECT 1 FROM evaluations e WHERE e.file_id=f.id AND LOWER(e.message) LIKE ?))")
        like = f"%{q.lower()}%"
        params.extend([like, like])
    if where: sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY sev_rank DESC, f.path ASC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with cursor() as c:
        return [dict(r) for r in c.execute(sql, params)]


def stats_summary():
    with cursor() as c:
        total = c.execute("SELECT COUNT(*) AS n FROM files").fetchone()["n"]
        size_b = c.execute("SELECT COALESCE(SUM(size_bytes),0) AS s FROM files").fetchone()["s"]

        sev_counts = {s: 0 for s in SEVERITY}
        rows = c.execute("""
            SELECT CASE
                WHEN sev_rank=5 THEN 'unplayable'
                WHEN sev_rank=4 THEN 'always_transcode'
                WHEN sev_rank=3 THEN 'possible_transcode'
                WHEN sev_rank=2 THEN 'high_bitrate'
                WHEN sev_rank=1 THEN 'info'
                ELSE 'ok' END AS sev, COUNT(*) AS n
            FROM (SELECT MAX(CASE e.severity
                WHEN 'unplayable' THEN 5 WHEN 'always_transcode' THEN 4
                WHEN 'possible_transcode' THEN 3 WHEN 'high_bitrate' THEN 2
                WHEN 'info' THEN 1 ELSE 0 END) AS sev_rank
                FROM files f LEFT JOIN evaluations e ON e.file_id=f.id GROUP BY f.id)
            GROUP BY sev
        """).fetchall()
        for r in rows: sev_counts[r["sev"]] = r["n"]

        category_counts = {}
        for r in c.execute("SELECT category, COUNT(*) AS n FROM files WHERE category IS NOT NULL GROUP BY category"):
            category_counts[r["category"]] = r["n"]

        # Per-category severity (key feature: dashboard tabs show per-category)
        per_category = {}
        for cat in CATEGORIES:
            per_category[cat] = {
                "total": 0,
                "size_gb": 0,
                "severity": {s: 0 for s in SEVERITY},
            }
        rows = c.execute("""
            SELECT f.category,
                   CASE
                     WHEN sev_rank=5 THEN 'unplayable'
                     WHEN sev_rank=4 THEN 'always_transcode'
                     WHEN sev_rank=3 THEN 'possible_transcode'
                     WHEN sev_rank=2 THEN 'high_bitrate'
                     WHEN sev_rank=1 THEN 'info'
                     ELSE 'ok' END AS sev,
                   COUNT(*) AS n,
                   SUM(size_bytes) AS sz
            FROM (
              SELECT f.id, f.category, f.size_bytes,
                MAX(CASE e.severity
                  WHEN 'unplayable' THEN 5 WHEN 'always_transcode' THEN 4
                  WHEN 'possible_transcode' THEN 3 WHEN 'high_bitrate' THEN 2
                  WHEN 'info' THEN 1 ELSE 0 END) AS sev_rank
              FROM files f LEFT JOIN evaluations e ON e.file_id=f.id
              GROUP BY f.id
            ) f
            WHERE f.category IS NOT NULL
            GROUP BY f.category, sev
        """).fetchall()
        for r in rows:
            cat = r["category"]
            if cat not in per_category: continue
            per_category[cat]["severity"][r["sev"]] = r["n"]
            per_category[cat]["total"] += r["n"]
            per_category[cat]["size_gb"] += (r["sz"] or 0) / (1024**3)

        for cat in per_category:
            per_category[cat]["size_gb"] = round(per_category[cat]["size_gb"], 2)

        issue_cat_counts = {}
        for r in c.execute("SELECT category, COUNT(DISTINCT file_id) AS n FROM evaluations GROUP BY category"):
            issue_cat_counts[r["category"]] = r["n"]

        codec_counts = {r["codec"]: r["n"] for r in c.execute("SELECT codec, COUNT(*) AS n FROM files WHERE codec IS NOT NULL GROUP BY codec")}
        audio_counts = {r["audio_codec"]: r["n"] for r in c.execute("SELECT audio_codec, COUNT(*) AS n FROM files WHERE audio_codec IS NOT NULL GROUP BY audio_codec")}
        res_counts = {r["resolution"]: r["n"] for r in c.execute("SELECT resolution, COUNT(*) AS n FROM files WHERE resolution IS NOT NULL GROUP BY resolution")}

        def cn(sql, *p): return c.execute(sql, p).fetchone()["n"]
        named = {
            "dovi_p5": cn("SELECT COUNT(*) AS n FROM files WHERE dovi_profile LIKE '%Profile 5%'"),
            "dovi_other": cn("SELECT COUNT(*) AS n FROM files WHERE dovi_profile IS NOT NULL AND dovi_profile NOT LIKE '%Profile 5%'"),
            "av1": cn("SELECT COUNT(*) AS n FROM files WHERE codec='av1'"),
            "hevc": cn("SELECT COUNT(*) AS n FROM files WHERE codec='hevc'"),
        }

    return {
        "total": total, "total_size_gb": round(size_b / (1024**3), 2),
        "severity": sev_counts, "file_categories": category_counts,
        "per_category": per_category,
        "issue_categories": issue_cat_counts, "codecs": codec_counts,
        "audio_codecs": audio_counts, "resolutions": res_counts, **named,
    }


# ─── Evaluations ──────────────────────────────────────────────────────────────

def replace_evaluations(file_id, issues):
    now = datetime.now().isoformat()
    with cursor() as c:
        c.execute("DELETE FROM evaluations WHERE file_id=?", (file_id,))
        for iss in issues:
            c.execute("""INSERT INTO evaluations (file_id, severity, category, rule_key, message, detail, affected, evaluated_at)
                         VALUES (?,?,?,?,?,?,?,?)""",
                      (file_id, iss.get("severity","info"), iss.get("category","unknown"),
                       iss.get("rule_key"), iss.get("message",""), iss.get("detail",""),
                       json.dumps(iss.get("affected",[])), now))
        c.execute("UPDATE files SET last_evaluated=? WHERE id=?", (now, file_id))


def get_evaluations(file_id):
    with cursor() as c:
        rows = c.execute("SELECT * FROM evaluations WHERE file_id=?", (file_id,)).fetchall()
        return [dict(r) for r in rows]


def all_files_for_eval():
    sql = "SELECT id, path, extension, size_bytes, category, probe_json, scan_status, paired_media_id FROM files"
    with cursor() as c:
        for r in c.execute(sql):
            row = dict(r)
            try: row["probe"] = json.loads(row["probe_json"]) if row["probe_json"] else None
            except Exception: row["probe"] = None
            yield row


def worst_severity_for_file(file_id):
    with cursor() as c:
        r = c.execute("""SELECT severity FROM evaluations WHERE file_id=?
                         ORDER BY CASE severity
                            WHEN 'unplayable' THEN 5 WHEN 'always_transcode' THEN 4
                            WHEN 'possible_transcode' THEN 3 WHEN 'high_bitrate' THEN 2
                            WHEN 'info' THEN 1 ELSE 0 END DESC LIMIT 1""", (file_id,)).fetchone()
        return r["severity"] if r else "ok"


# ─── Scans ────────────────────────────────────────────────────────────────────

def create_scan(job_id, kind, config):
    with cursor() as c:
        c.execute("""INSERT INTO scans (job_id, kind, status, started_at, config_json)
                     VALUES (?,?,?,?,?)""",
                  (job_id, kind, "queued", datetime.now().isoformat(), json.dumps(config)))
        return c.lastrowid

def update_scan(job_id, **fields):
    if not fields: return
    sets = ", ".join(f"{k}=?" for k in fields)
    with cursor() as c:
        c.execute(f"UPDATE scans SET {sets} WHERE job_id=?", (*fields.values(), job_id))

def get_scan(job_id):
    with cursor() as c:
        r = c.execute("SELECT * FROM scans WHERE job_id=?", (job_id,)).fetchone()
        return dict(r) if r else None

def list_scans(limit=20):
    with cursor() as c:
        return [dict(r) for r in c.execute("SELECT * FROM scans ORDER BY id DESC LIMIT ?", (limit,))]


# ─── Integrations ─────────────────────────────────────────────────────────────

def list_integrations(kind=None):
    sql = "SELECT id,kind,name,base_url,enabled,last_sync,last_error,poll_interval,options FROM integrations"
    params = []
    if kind: sql += " WHERE kind=?"; params.append(kind)
    with cursor() as c:
        rows = [dict(r) for r in c.execute(sql, params)]
        for r in rows:
            try: r["options"] = json.loads(r["options"]) if r["options"] else {}
            except Exception: r["options"] = {}
        return rows

def add_integration(kind, name, base_url, api_key, poll_interval=900, options=None):
    with cursor() as c:
        c.execute("""INSERT INTO integrations (kind, name, base_url, api_key, poll_interval, options)
                     VALUES (?,?,?,?,?,?)""",
                  (kind, name, base_url.rstrip("/") if base_url else "", api_key,
                   poll_interval, json.dumps(options or {})))
        return c.lastrowid

def get_integration(integration_id):
    with cursor() as c:
        r = c.execute("SELECT * FROM integrations WHERE id=?", (integration_id,)).fetchone()
        if not r: return None
        d = dict(r)
        try: d["options"] = json.loads(d["options"]) if d["options"] else {}
        except Exception: d["options"] = {}
        return d

def delete_integration(integration_id):
    with cursor() as c: c.execute("DELETE FROM integrations WHERE id=?", (integration_id,))

def update_integration(integration_id, **fields):
    if not fields: return
    if "options" in fields and isinstance(fields["options"], dict):
        fields["options"] = json.dumps(fields["options"])
    sets = ", ".join(f"{k}=?" for k in fields)
    with cursor() as c:
        c.execute(f"UPDATE integrations SET {sets} WHERE id=?", (*fields.values(), integration_id))

def all_enabled_integrations(kind=None):
    sql = "SELECT * FROM integrations WHERE enabled=1"
    params = []
    if kind: sql += " AND kind=?"; params.append(kind)
    with cursor() as c:
        return [dict(r) for r in c.execute(sql, params)]


# ─── Integration events ───────────────────────────────────────────────────────

def add_integration_event(integration_id, event_type, kind, payload, file_paths):
    with cursor() as c:
        c.execute("""INSERT INTO integration_events (integration_id, event_type, kind, payload, file_paths, received_at)
                     VALUES (?,?,?,?,?,?)""",
                  (integration_id, event_type, kind, json.dumps(payload),
                   json.dumps(file_paths), datetime.now().isoformat()))
        return c.lastrowid

def list_integration_events(limit=50):
    with cursor() as c:
        return [dict(r) for r in c.execute("SELECT * FROM integration_events ORDER BY id DESC LIMIT ?", (limit,))]


def link_file_to_arr(path, kind, arr_id, metadata, arr_file_id=None, monitored=None):
    with cursor() as c:
        sets = "arr_kind=?, arr_id=?, arr_metadata=?"
        params = [kind, arr_id, json.dumps(metadata)]
        if arr_file_id is not None:
            sets += ", arr_file_id=?"; params.append(arr_file_id)
        if monitored is not None:
            sets += ", monitored=?"; params.append(1 if monitored else 0)
        params.append(path)
        c.execute(f"UPDATE files SET {sets} WHERE path=?", params)


# ─── Automation rules ─────────────────────────────────────────────────────────

def list_automation_rules(integration_id=None):
    sql = "SELECT * FROM automation_rules"
    params = []
    if integration_id:
        sql += " WHERE integration_id=?"; params.append(integration_id)
    with cursor() as c:
        return [dict(r) for r in c.execute(sql, params)]

def add_automation_rule(integration_id, name, when_severity, comparison, action, enabled=1):
    with cursor() as c:
        c.execute("""INSERT INTO automation_rules
                     (integration_id, name, when_severity, comparison, action, enabled)
                     VALUES (?,?,?,?,?,?)""",
                  (integration_id, name, when_severity, comparison, action, enabled))
        return c.lastrowid

def delete_automation_rule(rule_id):
    with cursor() as c: c.execute("DELETE FROM automation_rules WHERE id=?", (rule_id,))

def update_automation_rule(rule_id, **fields):
    if not fields: return
    sets = ", ".join(f"{k}=?" for k in fields)
    with cursor() as c:
        c.execute(f"UPDATE automation_rules SET {sets} WHERE id=?", (*fields.values(), rule_id))


def files_for_automation(kind):
    """Files linked to a given *arr that have evaluations + arr_file_id."""
    with cursor() as c:
        rows = c.execute("""
            SELECT f.id, f.path, f.arr_file_id, f.arr_id, f.monitored,
                   (SELECT MAX(CASE e.severity
                       WHEN 'unplayable' THEN 5 WHEN 'always_transcode' THEN 4
                       WHEN 'possible_transcode' THEN 3 WHEN 'high_bitrate' THEN 2
                       WHEN 'info' THEN 1 ELSE 0 END)
                    FROM evaluations e WHERE e.file_id=f.id) AS sev_rank
            FROM files f
            WHERE f.arr_kind=? AND f.arr_file_id IS NOT NULL
        """, (kind,)).fetchall()
        return [dict(r) for r in rows]


# ─── Custom rules ─────────────────────────────────────────────────────────────

def list_custom_rules(only_enabled=False):
    sql = "SELECT * FROM custom_rules"
    if only_enabled: sql += " WHERE enabled=1"
    sql += " ORDER BY id"
    with cursor() as c:
        rows = [dict(r) for r in c.execute(sql)]
        for r in rows:
            try: r["spec"] = json.loads(r["spec_json"]) if r["spec_json"] else {}
            except Exception: r["spec"] = {}
            try: r["affected_devices_list"] = json.loads(r["affected_devices"]) if r["affected_devices"] else []
            except Exception: r["affected_devices_list"] = []
        return rows


def get_custom_rule(rule_id):
    with cursor() as c:
        r = c.execute("SELECT * FROM custom_rules WHERE id=?", (rule_id,)).fetchone()
        if not r: return None
        d = dict(r)
        try: d["spec"] = json.loads(d["spec_json"]) if d["spec_json"] else {}
        except Exception: d["spec"] = {}
        try: d["affected_devices_list"] = json.loads(d["affected_devices"]) if d["affected_devices"] else []
        except Exception: d["affected_devices_list"] = []
        return d


def add_custom_rule(name, description, severity, category, spec, affected_devices,
                    message, detail, enabled=1):
    now = datetime.now().isoformat()
    with cursor() as c:
        c.execute("""INSERT INTO custom_rules
                     (name, description, severity, category, spec_json, affected_devices,
                      message, detail, enabled, created_at, updated_at)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                  (name, description or "", severity, category or "custom",
                   json.dumps(spec or {}), json.dumps(affected_devices or []),
                   message or name, detail or "", 1 if enabled else 0, now, now))
        return c.lastrowid


def update_custom_rule(rule_id, **fields):
    if not fields: return
    if "spec" in fields:
        fields["spec_json"] = json.dumps(fields.pop("spec"))
    if "affected_devices_list" in fields:
        fields["affected_devices"] = json.dumps(fields.pop("affected_devices_list"))
    fields["updated_at"] = datetime.now().isoformat()
    sets = ", ".join(f"{k}=?" for k in fields)
    with cursor() as c:
        c.execute(f"UPDATE custom_rules SET {sets} WHERE id=?", (*fields.values(), rule_id))


def delete_custom_rule(rule_id):
    with cursor() as c:
        c.execute("DELETE FROM custom_rules WHERE id=?", (rule_id,))


def query_files_by_rule_spec(spec, limit=10000):
    """
    Translate a custom-rule spec into a SQL query and return matching file ids.
    spec = {match: 'all'|'any', conditions: [{field, op, value, ...}]}
    Used both for evaluation (insert issue rows) and live filtering.
    """
    conditions = (spec or {}).get("conditions") or []
    match = ((spec or {}).get("match") or "all").lower()
    join_op = " AND " if match == "all" else " OR "

    if not conditions:
        return []

    where_parts, params = [], []
    for cond in conditions:
        sql, p = _condition_to_sql(cond)
        if sql:
            where_parts.append(sql)
            params.extend(p)
    if not where_parts:
        return []

    sql = f"SELECT id FROM files WHERE {join_op.join(where_parts)} LIMIT ?"
    params.append(limit)
    with cursor() as c:
        return [r["id"] for r in c.execute(sql, params)]


# Map of allowed fields → SQL column expression
_RULE_FIELDS = {
    "extension":    "LOWER(extension)",
    "category":     "category",
    "codec":        "LOWER(codec)",
    "audio_codec":  "LOWER(audio_codec)",
    "container":    "LOWER(container)",
    "resolution":   "resolution",
    "dovi_profile": "dovi_profile",
    "size_bytes":   "size_bytes",
    "size_mb":      "(size_bytes / 1048576.0)",
    "size_gb":      "(size_bytes / 1073741824.0)",
    "bitrate":      "bitrate",
    "bitrate_mbps": "(bitrate / 1000000.0)",
    "duration_sec": "duration_sec",
    "name":         "LOWER(name)",
    "path":         "LOWER(path)",
    "scan_status":  "scan_status",
    "monitored":    "monitored",
    "arr_kind":     "arr_kind",
}

# Operators → SQL fragment
_RULE_OPS = {
    "eq":         "= ?",
    "neq":        "!= ?",
    "gt":         "> ?",
    "gte":        ">= ?",
    "lt":         "< ?",
    "lte":        "<= ?",
    "contains":   "LIKE ?",     # value wrapped in %...%
    "starts_with":"LIKE ?",     # value%
    "ends_with":  "LIKE ?",     # %value
    "in":         "IN (?)",     # list value
    "is_null":    "IS NULL",
    "not_null":   "IS NOT NULL",
}

def _condition_to_sql(cond):
    """Return (sql_fragment, params) for one condition."""
    field = cond.get("field")
    op    = cond.get("op", "eq")
    value = cond.get("value")
    col   = _RULE_FIELDS.get(field)
    if col is None or op not in _RULE_OPS:
        return "", []

    if op == "is_null":  return f"{col} IS NULL", []
    if op == "not_null": return f"{col} IS NOT NULL", []

    if op == "in":
        if not isinstance(value, list) or not value:
            return "", []
        placeholders = ",".join("?" for _ in value)
        # case-insensitive for string-ish columns
        norm = [str(v).lower() if "LOWER(" in col else v for v in value]
        return f"{col} IN ({placeholders})", norm

    if op == "contains":     val = f"%{str(value).lower()}%"
    elif op == "starts_with":val = f"{str(value).lower()}%"
    elif op == "ends_with":  val = f"%{str(value).lower()}"
    else:                    val = value if not isinstance(value, str) else (value.lower() if "LOWER(" in col else value)

    return f"{col} {_RULE_OPS[op]}", [val]


def custom_rule_field_options():
    """Return the schema for the visual rule builder."""
    return {
        "fields": [
            {"key":"extension",    "label":"File extension",  "type":"string", "examples":[".mkv",".mp4",".avi"]},
            {"key":"category",     "label":"File category",   "type":"enum", "options":["media","subtitle","image","metadata","junk"]},
            {"key":"codec",        "label":"Video codec",     "type":"string", "examples":["h264","hevc","av1","vp9","mpeg2video"]},
            {"key":"audio_codec",  "label":"Audio codec",     "type":"string", "examples":["aac","ac3","eac3","dts","truehd","flac"]},
            {"key":"container",    "label":"Container",       "type":"string", "examples":["matroska","mp4","avi"]},
            {"key":"resolution",   "label":"Resolution",      "type":"string", "examples":["1920x1080","3840x2160"]},
            {"key":"dovi_profile", "label":"DoVi profile",    "type":"string", "examples":["DoVi Profile 5","DoVi Profile 7","DoVi Profile 8"]},
            {"key":"size_mb",      "label":"Size (MB)",       "type":"number"},
            {"key":"size_gb",      "label":"Size (GB)",       "type":"number"},
            {"key":"bitrate_mbps", "label":"Bitrate (Mbps)",  "type":"number"},
            {"key":"duration_sec", "label":"Duration (s)",    "type":"number"},
            {"key":"name",         "label":"Filename",        "type":"string"},
            {"key":"path",         "label":"Full path",       "type":"string"},
            {"key":"scan_status",  "label":"Scan status",     "type":"enum", "options":["ok","probe_failed","missing"]},
            {"key":"monitored",    "label":"Monitored in *arr","type":"enum", "options":[0,1]},
            {"key":"arr_kind",     "label":"*arr type",       "type":"enum", "options":["sonarr","radarr"]},
        ],
        "ops_by_type": {
            "string": ["eq","neq","contains","starts_with","ends_with","is_null","not_null"],
            "number": ["eq","neq","gt","gte","lt","lte","is_null","not_null"],
            "enum":   ["eq","neq","in","is_null","not_null"],
        },
    }
