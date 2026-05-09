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
# Media-file severity (the original 6-level scale)
MEDIA_SEVERITY = ["ok", "info", "high_bitrate", "possible_transcode", "always_transcode", "unplayable"]

# Non-media severity (subtitle, image, metadata, junk)
NON_MEDIA_SEVERITY = ["ok", "info", "warning", "corrupt", "possible_malicious"]

# Union — DB column accepts any of these
SEVERITY = list(dict.fromkeys(MEDIA_SEVERITY + NON_MEDIA_SEVERITY))
SEVERITY_RANK = {s: i for i, s in enumerate(MEDIA_SEVERITY)}  # media-only ranks for legacy auto rules
SEVERITY_RANK_NON_MEDIA = {s: i for i, s in enumerate(NON_MEDIA_SEVERITY)}


def severity_class_for_category(cat: str) -> str:
    """Return 'media' or 'non_media' for a file category."""
    return "media" if cat == "media" else "non_media"


def severity_scale_for_category(cat: str) -> list:
    return MEDIA_SEVERITY if cat == "media" else NON_MEDIA_SEVERITY


CATEGORIES = ["media", "subtitle", "image", "metadata", "junk", "ignored"]

DEFAULT_IGNORE_PATTERNS = [
    ".plexmatch", ".DS_Store", "Thumbs.db", "desktop.ini",
    ".nomedia", "@eaDir", ".AppleDouble", ".gitkeep",
]


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
        _create_schema()
        _run_migrations()
        _ensure_indexes()
        _initialized = True


def _create_schema():
    """Create all tables if they don't exist. Indexes are created AFTER
    migrations so they don't fail when columns don't exist on old DBs.
    """
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
                    action_config   TEXT,         -- JSON: extra parameters per action type
                    file_category   TEXT,         -- restrict to media|subtitle|...|null=all
                    enabled         INTEGER DEFAULT 1,
                    last_run        TEXT,
                    runs_count      INTEGER DEFAULT 0,
                    last_action_count INTEGER DEFAULT 0,
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

                -- Schema version tracker
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key   TEXT PRIMARY KEY,
                    value TEXT
                );
            """)


def _ensure_indexes():
    """Create all indexes. Runs after migrations so all referenced columns exist."""
    with cursor() as c:
        c.executescript("""
            CREATE INDEX IF NOT EXISTS idx_files_path     ON files(path);
            CREATE INDEX IF NOT EXISTS idx_files_category ON files(category);
            CREATE INDEX IF NOT EXISTS idx_files_codec    ON files(codec);
            CREATE INDEX IF NOT EXISTS idx_files_dovi     ON files(dovi_profile);
            CREATE INDEX IF NOT EXISTS idx_files_arr      ON files(arr_kind, arr_id);
            CREATE INDEX IF NOT EXISTS idx_eval_file      ON evaluations(file_id);
            CREATE INDEX IF NOT EXISTS idx_eval_severity  ON evaluations(severity);
            CREATE INDEX IF NOT EXISTS idx_eval_category  ON evaluations(category);
            CREATE INDEX IF NOT EXISTS idx_custom_rules_enabled ON custom_rules(enabled);
        """)


# ─── Migrations ──────────────────────────────────────────────────────────────
# Each migration is a (version, fn) tuple. Migrations run in order. Each
# migration must be idempotent: it should check whether its work is already
# done before doing it. This is important because users on old DBs may have
# partial schemas where some columns already exist.
#
# To add a migration: append a new (N, fn) tuple. Don't reorder, don't
# renumber. Migrations only apply once, tracked in schema_meta.

def _column_exists(c, table, column) -> bool:
    rows = c.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == column for r in rows)


def _ensure_column(c, table, column, type_decl):
    if not _column_exists(c, table, column):
        c.execute(f"ALTER TABLE {table} ADD COLUMN {column} {type_decl}")


def _migrate_v0_base_columns(c):
    """v0: ensure all 'base' columns from v4 exist on the core tables.

    Pre-migration users may have an older schema. We add any missing column.
    """
    file_cols = {
        "name": "TEXT", "extension": "TEXT", "size_bytes": "INTEGER",
        "mtime": "REAL", "category": "TEXT", "hash_sha256": "TEXT",
        "codec": "TEXT", "audio_codec": "TEXT", "resolution": "TEXT",
        "container": "TEXT", "dovi_profile": "TEXT", "duration_sec": "REAL",
        "bitrate": "INTEGER", "probe_json": "TEXT", "paired_media_id": "INTEGER",
        "arr_kind": "TEXT", "arr_id": "INTEGER", "arr_file_id": "INTEGER",
        "arr_metadata": "TEXT", "monitored": "INTEGER",
        "first_scanned": "TEXT", "last_scanned": "TEXT",
        "last_evaluated": "TEXT", "scan_status": "TEXT",
    }
    for col, typ in file_cols.items():
        _ensure_column(c, "files", col, typ)

    eval_cols = {
        "severity": "TEXT", "category": "TEXT", "rule_key": "TEXT",
        "message": "TEXT", "detail": "TEXT", "affected": "TEXT",
        "created_at": "TEXT",
    }
    for col, typ in eval_cols.items():
        _ensure_column(c, "evaluations", col, typ)

    scan_cols = {
        "status": "TEXT", "kind": "TEXT", "started_at": "TEXT",
        "finished_at": "TEXT", "processed": "INTEGER DEFAULT 0",
        "total": "INTEGER DEFAULT 0", "error": "TEXT",
    }
    for col, typ in scan_cols.items():
        _ensure_column(c, "scans", col, typ)

    integ_cols = {
        "kind": "TEXT", "name": "TEXT", "base_url": "TEXT", "api_key": "TEXT",
        "options": "TEXT", "enabled": "INTEGER DEFAULT 1",
        "poll_interval": "INTEGER DEFAULT 900", "last_sync": "TEXT",
        "last_error": "TEXT", "created_at": "TEXT",
    }
    for col, typ in integ_cols.items():
        _ensure_column(c, "integrations", col, typ)

    ie_cols = {
        "kind": "TEXT", "event_type": "TEXT", "file_paths": "TEXT",
        "payload": "TEXT", "received_at": "TEXT",
    }
    for col, typ in ie_cols.items():
        _ensure_column(c, "integration_events", col, typ)

    cust_cols = {
        "name": "TEXT", "description": "TEXT", "severity": "TEXT",
        "category": "TEXT", "spec_json": "TEXT", "affected_devices": "TEXT",
        "message": "TEXT", "detail": "TEXT", "enabled": "INTEGER DEFAULT 1",
        "created_at": "TEXT", "updated_at": "TEXT",
    }
    for col, typ in cust_cols.items():
        _ensure_column(c, "custom_rules", col, typ)


def _migrate_v1_extend_automation_rules(c):
    """v1: ensure new columns on automation_rules exist (action_config, file_category, runs_count, last_action_count)."""
    _ensure_column(c, "automation_rules", "action_config", "TEXT")
    _ensure_column(c, "automation_rules", "file_category", "TEXT")
    _ensure_column(c, "automation_rules", "runs_count", "INTEGER DEFAULT 0")
    _ensure_column(c, "automation_rules", "last_action_count", "INTEGER DEFAULT 0")


def _migrate_v2_severity_match_mode(c):
    """v2: how to compare severity when a file has multiple issues."""
    _ensure_column(c, "automation_rules", "severity_match", "TEXT DEFAULT 'highest'")
    # Backfill existing rules with 'highest' so behavior is unchanged
    c.execute("UPDATE automation_rules SET severity_match='highest' WHERE severity_match IS NULL")


def _migrate_v3_purge_ignored(c):
    """v3: belt-and-braces — purge any 'ignored' rows that snuck in on old DBs."""
    c.execute("DELETE FROM evaluations WHERE file_id IN (SELECT id FROM files WHERE category='ignored')")
    c.execute("DELETE FROM files WHERE category='ignored'")


def _migrate_v4_dropped_custom_rules(c):
    """v4: track custom rules a user has explicitly dropped (separate from 'disabled')."""
    _ensure_column(c, "custom_rules", "dropped", "INTEGER DEFAULT 0")
    _ensure_column(c, "custom_rules", "rule_kind", "TEXT DEFAULT 'custom'")  # 'custom' or 'builtin'


_MIGRATIONS = [
    (0, _migrate_v0_base_columns),
    (1, _migrate_v1_extend_automation_rules),
    (2, _migrate_v2_severity_match_mode),
    (3, _migrate_v3_purge_ignored),
    (4, _migrate_v4_dropped_custom_rules),
]


def _run_migrations():
    with cursor() as c:
        row = c.execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()
        # current=-1 means "never migrated", so v0 will run on the first init.
        # On a freshly created DB, all migrations apply (and are idempotent).
        current = int(row["value"]) if row else -1
        for version, fn in _MIGRATIONS:
            if version > current:
                try:
                    fn(c)
                    c.execute("INSERT OR REPLACE INTO schema_meta(key,value) VALUES('version', ?)",
                              (str(version),))
                    print(f"[db] migration {version} applied: {fn.__name__}")
                except Exception as e:
                    print(f"[db] migration {version} FAILED: {e}")
                    raise


def schema_version() -> int:
    with cursor() as c:
        row = c.execute("SELECT value FROM schema_meta WHERE key='version'").fetchone()
        return int(row["value"]) if row else -1


# ─── Backup & restore ────────────────────────────────────────────────────────

def backup_to(dest_path: str):
    """Create a consistent backup using SQLite's online backup API.

    Safe to call while other connections are reading/writing.
    """
    import sqlite3
    src = _connect()
    dst = sqlite3.connect(dest_path)
    try:
        with dst:
            src.backup(dst)
    finally:
        dst.close()


def restore_from(src_path: str):
    """Replace the live DB with the contents of the file at src_path.

    Uses SQLite's backup API to copy from source into the live DB, which
    handles WAL state correctly (vs raw file copy which can leave the live
    DB in an inconsistent state).
    """
    import sqlite3, os
    global _initialized, _local
    # Verify the candidate file is a valid SQLite DB and has the expected tables
    test = sqlite3.connect(src_path)
    try:
        rows = test.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        names = {r[0] for r in rows}
        required = {"files", "evaluations", "scans", "integrations",
                    "automation_rules", "custom_rules"}
        missing = required - names
        if missing:
            raise ValueError(f"Backup is missing required tables: {sorted(missing)}")
    finally:
        test.close()

    with _init_lock:
        # Close any cached connection on this thread, force a checkpoint, drop
        # the WAL files, then use the backup API to copy contents.
        try:
            if hasattr(_local, "conn"):
                try: _local.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                except Exception: pass
                try: _local.conn.close()
                except Exception: pass
                del _local.conn
        except Exception: pass

        # Wipe stale journal files; safer than copying over them
        for ext in ("-wal", "-shm", "-journal"):
            leftover = str(DB_PATH) + ext
            if os.path.exists(leftover):
                try: os.remove(leftover)
                except Exception: pass

        # Use sqlite3.backup API to populate the live file from the backup.
        # This works even if the file already exists and is more robust than
        # raw shutil.copyfile against partial pages.
        src_conn = sqlite3.connect(src_path)
        # Open the live file fresh — no WAL yet because we wiped it
        live_conn = sqlite3.connect(str(DB_PATH))
        try:
            with live_conn:
                src_conn.backup(live_conn)
        finally:
            src_conn.close()
            live_conn.close()

        _initialized = False
    init()


def vacuum():
    """Reclaim space and rebuild the file. Safe to call any time."""
    with cursor() as c:
        c.execute("VACUUM")


def integrity_check() -> tuple[bool, str]:
    with cursor() as c:
        rows = c.execute("PRAGMA integrity_check").fetchall()
        msg = "; ".join(r[0] for r in rows)
        return msg.lower() == "ok", msg


def db_stats() -> dict:
    """Surface counts + file size for the Settings UI."""
    import os
    with cursor() as c:
        n_files = c.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        n_evals = c.execute("SELECT COUNT(*) FROM evaluations").fetchone()[0]
        n_rules = c.execute("SELECT COUNT(*) FROM custom_rules").fetchone()[0]
        n_int   = c.execute("SELECT COUNT(*) FROM integrations").fetchone()[0]
        n_auto  = c.execute("SELECT COUNT(*) FROM automation_rules").fetchone()[0]
    try: size = os.path.getsize(DB_PATH)
    except Exception: size = 0
    return {
        "path": str(DB_PATH),
        "size_bytes": size,
        "files": n_files,
        "evaluations": n_evals,
        "custom_rules": n_rules,
        "integrations": n_int,
        "automation_rules": n_auto,
    }


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
              WHEN 'unplayable' THEN 5
              WHEN 'possible_malicious' THEN 5
              WHEN 'always_transcode' THEN 4
              WHEN 'corrupt' THEN 4
              WHEN 'possible_transcode' THEN 3
              WHEN 'high_bitrate' THEN 2
              WHEN 'warning' THEN 2
              WHEN 'info' THEN 1
              ELSE 0 END)
            FROM evaluations e WHERE e.file_id=f.id) AS sev_rank,
           (SELECT e.severity FROM evaluations e WHERE e.file_id=f.id
            ORDER BY CASE e.severity
              WHEN 'unplayable' THEN 5
              WHEN 'possible_malicious' THEN 5
              WHEN 'always_transcode' THEN 4
              WHEN 'corrupt' THEN 4
              WHEN 'possible_transcode' THEN 3
              WHEN 'high_bitrate' THEN 2
              WHEN 'warning' THEN 2
              WHEN 'info' THEN 1
              ELSE 0 END DESC LIMIT 1) AS headline_severity
    FROM files f
    """
    where, params = [], []
    # Safety: ignored files should never be in the DB, but exclude them
    # explicitly in case a user upgrades from an older version.
    where.append("(f.category IS NULL OR f.category != 'ignored')")
    if severity:
        if severity == "ok":
            # Clean files have NO non-ok evaluations
            where.append("""NOT EXISTS(SELECT 1 FROM evaluations e WHERE e.file_id=f.id
                                       AND e.severity != 'ok')""")
        else:
            # Match files where this is the headline (worst) severity. The
            # rank tables for media and non_media are different, but the
            # severity NAME is unique across both scales, so we can rank by
            # name directly.
            sev_rank_all = {
                # Media scale
                "info": 1, "high_bitrate": 2, "possible_transcode": 3,
                "always_transcode": 4, "unplayable": 5,
                # Non-media scale (separate axis but ranked similarly)
                "warning": 2, "corrupt": 4, "possible_malicious": 5,
            }
            target_rank = sev_rank_all.get(severity, 0)
            where.append("EXISTS(SELECT 1 FROM evaluations e WHERE e.file_id=f.id AND e.severity=?)")
            params.append(severity)
            if target_rank < 5:
                # CASE expression matches both scales
                where.append("""NOT EXISTS(
                    SELECT 1 FROM evaluations e2 WHERE e2.file_id=f.id
                    AND CASE e2.severity
                          WHEN 'unplayable' THEN 5
                          WHEN 'possible_malicious' THEN 5
                          WHEN 'always_transcode' THEN 4
                          WHEN 'corrupt' THEN 4
                          WHEN 'possible_transcode' THEN 3
                          WHEN 'high_bitrate' THEN 2
                          WHEN 'warning' THEN 2
                          WHEN 'info' THEN 1
                          ELSE 0 END > ?
                )""")
                params.append(target_rank)
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
    sql += " ORDER BY COALESCE(sev_rank, 0) DESC, f.path ASC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with cursor() as c:
        return [dict(r) for r in c.execute(sql, params)]


def stats_summary():
    EXCLUDE_IGNORED = "(category IS NULL OR category != 'ignored')"
    # Unified rank-to-severity-name mapping (for global rollup)
    RANK_CASE = """
        CASE e.severity
          WHEN 'unplayable' THEN 5
          WHEN 'possible_malicious' THEN 5
          WHEN 'always_transcode' THEN 4
          WHEN 'corrupt' THEN 4
          WHEN 'possible_transcode' THEN 3
          WHEN 'high_bitrate' THEN 2
          WHEN 'warning' THEN 2
          WHEN 'info' THEN 1
          ELSE 0
        END
    """
    with cursor() as c:
        total = c.execute(f"SELECT COUNT(*) AS n FROM files WHERE {EXCLUDE_IGNORED}").fetchone()["n"]
        size_b = c.execute(f"SELECT COALESCE(SUM(size_bytes),0) AS s FROM files WHERE {EXCLUDE_IGNORED}").fetchone()["s"]

        # Global severity counts — pick the headline (highest-rank) severity
        # per file and group by name. This works across both scales.
        sev_counts = {s: 0 for s in SEVERITY}
        rows = c.execute(f"""
            SELECT headline, COUNT(*) AS n FROM (
              SELECT f.id,
                (SELECT e.severity FROM evaluations e WHERE e.file_id=f.id
                 ORDER BY {RANK_CASE} DESC LIMIT 1) AS headline
              FROM files f
              WHERE (f.category IS NULL OR f.category != 'ignored')
            )
            GROUP BY headline
        """).fetchall()
        for r in rows:
            sev = r["headline"] or "ok"
            sev_counts[sev] = sev_counts.get(sev, 0) + r["n"]

        category_counts = {}
        for r in c.execute("SELECT category, COUNT(*) AS n FROM files WHERE category IS NOT NULL AND category != 'ignored' GROUP BY category"):
            category_counts[r["category"]] = r["n"]

        # Per-category severity — uses headline-severity per file
        per_category = {}
        for cat in CATEGORIES:
            if cat == "ignored": continue
            per_category[cat] = {
                "total": 0, "size_gb": 0,
                "severity_class": severity_class_for_category(cat),
                "severity": {s: 0 for s in severity_scale_for_category(cat)},
            }

        rows = c.execute(f"""
            SELECT f.category, COALESCE(t.headline, 'ok') AS sev,
                   COUNT(*) AS n, SUM(f.size_bytes) AS sz
            FROM files f
            LEFT JOIN (
              SELECT e.file_id,
                (SELECT e2.severity FROM evaluations e2 WHERE e2.file_id=e.file_id
                 ORDER BY CASE e2.severity
                   WHEN 'unplayable' THEN 5 WHEN 'possible_malicious' THEN 5
                   WHEN 'always_transcode' THEN 4 WHEN 'corrupt' THEN 4
                   WHEN 'possible_transcode' THEN 3
                   WHEN 'high_bitrate' THEN 2 WHEN 'warning' THEN 2
                   WHEN 'info' THEN 1 ELSE 0 END DESC LIMIT 1) AS headline
              FROM evaluations e GROUP BY e.file_id
            ) t ON t.file_id = f.id
            WHERE f.category IS NOT NULL AND f.category != 'ignored'
            GROUP BY f.category, sev
        """).fetchall()
        for r in rows:
            cat = r["category"]
            if cat not in per_category: continue
            sev = r["sev"]
            # Only count if this severity is in the cat's scale
            if sev in per_category[cat]["severity"]:
                per_category[cat]["severity"][sev] += r["n"]
            else:
                # Severity from wrong scale — shouldn't happen but bucket as info
                per_category[cat]["severity"]["info"] = per_category[cat]["severity"].get("info", 0) + r["n"]
            per_category[cat]["total"] += r["n"]
            per_category[cat]["size_gb"] += (r["sz"] or 0) / (1024**3)

        for cat in per_category:
            per_category[cat]["size_gb"] = round(per_category[cat]["size_gb"], 2)

        issue_cat_counts = {}
        for r in c.execute("SELECT category, COUNT(DISTINCT file_id) AS n FROM evaluations GROUP BY category"):
            issue_cat_counts[r["category"]] = r["n"]

        codec_counts = {r["codec"]: r["n"] for r in c.execute("SELECT codec, COUNT(*) AS n FROM files WHERE codec IS NOT NULL AND codec != '' GROUP BY codec")}
        audio_counts = {r["audio_codec"]: r["n"] for r in c.execute("SELECT audio_codec, COUNT(*) AS n FROM files WHERE audio_codec IS NOT NULL AND audio_codec != '' GROUP BY audio_codec")}
        res_counts = {r["resolution"]: r["n"] for r in c.execute("SELECT resolution, COUNT(*) AS n FROM files WHERE resolution IS NOT NULL AND resolution != '' GROUP BY resolution")}

        # Unique rule_keys triggered per severity (used by dashboard tiles)
        unique_rules_by_severity = {}
        for r in c.execute("""
            SELECT severity, COUNT(DISTINCT rule_key) AS n
            FROM evaluations
            WHERE rule_key IS NOT NULL
            GROUP BY severity
        """):
            unique_rules_by_severity[r["severity"]] = r["n"]

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
        "audio_codecs": audio_counts, "resolutions": res_counts,
        "unique_rules_by_severity": unique_rules_by_severity,
        **named,
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
        rows = [dict(r) for r in c.execute(sql, params)]
        for r in rows:
            try: r["action_config_obj"] = json.loads(r.get("action_config") or "{}")
            except Exception: r["action_config_obj"] = {}
        return rows

def add_automation_rule(integration_id, name, when_severity, comparison, action,
                        enabled=1, action_config=None, file_category=None,
                        severity_match="highest"):
    with cursor() as c:
        c.execute("""INSERT INTO automation_rules
                     (integration_id, name, when_severity, comparison, action,
                      action_config, file_category, enabled, severity_match)
                     VALUES (?,?,?,?,?,?,?,?,?)""",
                  (integration_id, name, when_severity, comparison, action,
                   json.dumps(action_config or {}), file_category, enabled,
                   severity_match))
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

def list_custom_rules(only_enabled=False, include_dropped=False, rule_kind="custom"):
    sql = "SELECT * FROM custom_rules WHERE 1=1"
    params = []
    if rule_kind:
        sql += " AND COALESCE(rule_kind, 'custom') = ?"
        params.append(rule_kind)
    if only_enabled: sql += " AND enabled=1"
    if not include_dropped: sql += " AND COALESCE(dropped, 0) = 0"
    sql += " ORDER BY id"
    with cursor() as c:
        rows = [dict(r) for r in c.execute(sql, params)]
        for r in rows:
            try: r["spec"] = json.loads(r["spec_json"]) if r["spec_json"] else {}
            except Exception: r["spec"] = {}
            try: r["affected_devices_list"] = json.loads(r["affected_devices"]) if r["affected_devices"] else []
            except Exception: r["affected_devices_list"] = []
        return rows


def list_dropped_rules():
    """Return rules a user has dropped (kind=custom or builtin), for the
    'Disabled / Discarded' tab."""
    with cursor() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM custom_rules WHERE COALESCE(dropped, 0) = 1 ORDER BY id")]
        for r in rows:
            try: r["spec"] = json.loads(r["spec_json"]) if r["spec_json"] else {}
            except Exception: r["spec"] = {}
        return rows


def disabled_rule_keys():
    """Return list of rule_keys that should be filtered out of evaluations."""
    with cursor() as c:
        # A built-in rule is "active" if it's NOT in the table OR it's there with
        # enabled=1 and dropped=0. A built-in rule is "disabled" if there's a
        # row marking enabled=0 OR dropped=1.
        rows = c.execute("""
            SELECT spec_json FROM custom_rules
            WHERE COALESCE(rule_kind,'custom') = 'builtin'
              AND (enabled = 0 OR COALESCE(dropped, 0) = 1)
        """).fetchall()
        keys = []
        for r in rows:
            try:
                spec = json.loads(r["spec_json"]) if r["spec_json"] else {}
                if spec.get("rule_key"): keys.append(spec["rule_key"])
            except Exception: pass
        return keys


def upsert_builtin_rule_state(rule_key: str, enabled: bool, dropped: bool = False,
                              severity_override: str = None):
    """Track a built-in rule's user-side state (disabled / dropped / severity override)."""
    now = datetime.now().isoformat()
    with cursor() as c:
        existing = c.execute(
            "SELECT id FROM custom_rules WHERE COALESCE(rule_kind,'custom')='builtin' "
            "AND spec_json LIKE ?",
            (f'%"{rule_key}"%',)
        ).fetchone()
        spec = json.dumps({"rule_key": rule_key, "is_builtin": True})
        if existing:
            updates = {"enabled": 1 if enabled else 0,
                       "dropped": 1 if dropped else 0,
                       "updated_at": now}
            if severity_override:
                updates["severity"] = severity_override
            sets = ", ".join(f"{k}=?" for k in updates)
            c.execute(f"UPDATE custom_rules SET {sets} WHERE id=?",
                      (*updates.values(), existing["id"]))
        else:
            c.execute("""INSERT INTO custom_rules
                         (name, description, severity, category, spec_json, affected_devices,
                          message, detail, enabled, dropped, rule_kind, created_at, updated_at)
                         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (rule_key, f"Built-in rule: {rule_key}",
                       severity_override or "info", "builtin",
                       spec, "[]", rule_key, "", 1 if enabled else 0,
                       1 if dropped else 0, "builtin", now, now))


def get_builtin_rule_state(rule_key: str) -> dict:
    """Return {enabled, dropped, severity_override} for a built-in rule, or
    a default if no row exists yet."""
    with cursor() as c:
        row = c.execute(
            "SELECT * FROM custom_rules WHERE COALESCE(rule_kind,'custom')='builtin' "
            "AND spec_json LIKE ?",
            (f'%"{rule_key}"%',)
        ).fetchone()
        if not row:
            return {"enabled": True, "dropped": False, "severity_override": None}
        return {
            "enabled": bool(row["enabled"]),
            "dropped": bool(row["dropped"]),
            "severity_override": row["severity"] if row["severity"] != "info" else None,
        }


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


def files_for_severity_filter(min_rank=0, file_category=None):
    """Return files with severity sev_rank ≥ min_rank, optionally filtered by category."""
    sql = """
        SELECT f.id, f.path, f.name, f.category, f.codec, f.bitrate, f.size_bytes,
               f.arr_kind, f.arr_id, f.arr_file_id, f.monitored,
               (SELECT MAX(CASE e.severity
                   WHEN 'unplayable' THEN 5 WHEN 'always_transcode' THEN 4
                   WHEN 'possible_transcode' THEN 3 WHEN 'high_bitrate' THEN 2
                   WHEN 'info' THEN 1 ELSE 0 END)
                FROM evaluations e WHERE e.file_id=f.id) AS sev_rank
        FROM files f
    """
    where = []
    params = []
    if file_category:
        where.append("f.category=?"); params.append(file_category)
    if where: sql += " WHERE " + " AND ".join(where)
    with cursor() as c:
        return [dict(r) for r in c.execute(sql, params)]
