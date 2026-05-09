"""
updater.py — GitHub commit poller + tarball-based seamless self-update.

Branches: "main" (stable) and "dev". User can pick which branch to track.
The selected branch is persisted in .auditarr_version.json.

Update flow:
  1. Poll GitHub commits/<branch> every 6 hours.
  2. If a newer SHA is detected, surface via /api/update/check.
  3. User clicks "Install update": we download the tarball for that ref,
     stage it under .auditarr_update/, copy files into the install dir
     atomically (preserving config.json, auth.json, media_audit.db, etc.),
     mark the new SHA as current, and tell the user to restart.
"""
import json
import os
import shutil
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = "ShogyX/Auditarr"
DEFAULT_BRANCH = "main"
ALLOWED_BRANCHES = ("main", "dev")
INSTALL_ROOT = Path(__file__).resolve().parent
VERSION_FILE = INSTALL_ROOT / ".auditarr_version.json"
POLL_INTERVAL = 6 * 60 * 60  # 6 hours

# Files we never overwrite during update — user data stays on the live install
PROTECTED_FILES = {
    "config.json", "auth.json", "media_audit.db",
    "media_audit.db-shm", "media_audit.db-wal",
    ".auditarr_version.json",
}
PROTECTED_DIRS = {".auditarr_update", "__pycache__"}

_state = {
    "branch": DEFAULT_BRANCH,
    "current_sha": None,
    "current_short": None,
    "latest_sha": None,
    "latest_short": None,
    "latest_message": None,
    "latest_url": None,
    "latest_committed_at": None,
    "available": False,
    "last_checked": None,
    "last_error": None,
    "installing": False,
    "last_install_at": None,
    "last_install_error": None,
}
_lock = threading.Lock()
_install_lock = threading.Lock()


# ─── Persistent state ────────────────────────────────────────────────────────

def _load_state_file() -> dict:
    if not VERSION_FILE.exists(): return {}
    try: return json.loads(VERSION_FILE.read_text())
    except Exception: return {}


def _save_state_file(d: dict):
    payload = {
        "current_sha": d.get("current_sha"),
        "branch": d.get("branch") or DEFAULT_BRANCH,
        "updated": datetime.now().isoformat(),
        "last_install_at": d.get("last_install_at"),
    }
    VERSION_FILE.write_text(json.dumps(payload, indent=2))


def initial_load():
    persisted = _load_state_file()
    with _lock:
        _state["current_sha"] = persisted.get("current_sha")
        _state["current_short"] = (persisted.get("current_sha") or "")[:7] or None
        _state["branch"] = persisted.get("branch") or DEFAULT_BRANCH
        _state["last_install_at"] = persisted.get("last_install_at")


def get_state() -> dict:
    with _lock:
        return dict(_state)


def set_branch(branch: str) -> dict:
    if branch not in ALLOWED_BRANCHES:
        raise ValueError(f"Branch must be one of {ALLOWED_BRANCHES}")
    with _lock:
        _state["branch"] = branch
        # Force re-check on next poll
        _state["latest_sha"] = None
        _state["available"] = False
        _save_state_file(_state)
    return fetch_latest()


def mark_current(sha: str):
    with _lock:
        _state["current_sha"] = sha
        _state["current_short"] = sha[:7] if sha else None
        _save_state_file(_state)
        _recompute_available()


def _recompute_available():
    cs = _state.get("current_sha")
    ls = _state.get("latest_sha")
    _state["available"] = bool(cs and ls and cs != ls)


# ─── GitHub API ──────────────────────────────────────────────────────────────

def fetch_latest() -> dict:
    branch = _state.get("branch") or DEFAULT_BRANCH
    url = f"https://api.github.com/repos/{REPO}/commits/{branch}"
    try:
        req = urllib.request.Request(url, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "Auditarr-update-checker",
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
        sha = data.get("sha", "")
        commit = data.get("commit", {}) or {}
        msg = (commit.get("message") or "").splitlines()[0][:200]
        committer = (commit.get("committer") or {}).get("date")
        html_url = data.get("html_url") or f"https://github.com/{REPO}/commit/{sha}"
        with _lock:
            _state["latest_sha"] = sha
            _state["latest_short"] = sha[:7] if sha else None
            _state["latest_message"] = msg
            _state["latest_url"] = html_url
            _state["latest_committed_at"] = committer
            _state["last_checked"] = datetime.now().isoformat()
            _state["last_error"] = None
            _recompute_available()
            return dict(_state)
    except urllib.error.HTTPError as e:
        with _lock:
            _state["last_error"] = f"GitHub HTTP {e.code}"
            _state["last_checked"] = datetime.now().isoformat()
            return dict(_state)
    except urllib.error.URLError as e:
        with _lock:
            _state["last_error"] = f"Network error: {e.reason}"
            _state["last_checked"] = datetime.now().isoformat()
            return dict(_state)
    except Exception as e:
        with _lock:
            _state["last_error"] = f"Error: {e}"
            _state["last_checked"] = datetime.now().isoformat()
            return dict(_state)


def force_check() -> dict:
    return fetch_latest()


def list_branches() -> list:
    """Return supported branches with whether each exists on the remote."""
    out = []
    for b in ALLOWED_BRANCHES:
        url = f"https://api.github.com/repos/{REPO}/branches/{b}"
        try:
            req = urllib.request.Request(url, headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "Auditarr-update-checker",
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            out.append({
                "name": b,
                "exists": True,
                "sha": data.get("commit", {}).get("sha"),
                "is_current": b == _state.get("branch"),
            })
        except urllib.error.HTTPError as e:
            out.append({"name": b, "exists": e.code != 404, "sha": None,
                        "is_current": b == _state.get("branch"),
                        "error": f"HTTP {e.code}"})
        except Exception as e:
            out.append({"name": b, "exists": False, "sha": None,
                        "is_current": b == _state.get("branch"),
                        "error": str(e)})
    return out


# ─── Install update from tarball ─────────────────────────────────────────────

def install_update(target_sha: str = None) -> dict:
    """Download tarball for the chosen branch (or specific SHA) and install.

    Returns {ok, message, restart_required, error?}.
    """
    if not _install_lock.acquire(blocking=False):
        return {"ok": False, "error": "An install is already in progress"}
    try:
        with _lock: _state["installing"] = True; _state["last_install_error"] = None

        branch = _state.get("branch") or DEFAULT_BRANCH
        ref = target_sha or _state.get("latest_sha") or branch
        # GitHub tarball URL: codeload.github.com (no API rate limit)
        url = f"https://codeload.github.com/{REPO}/tar.gz/{ref}"

        tmp_dir = tempfile.mkdtemp(prefix="auditarr-update-")
        tar_path = os.path.join(tmp_dir, "src.tar.gz")
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": "Auditarr-update-checker",
            })
            with urllib.request.urlopen(req, timeout=60) as resp, \
                 open(tar_path, "wb") as out:
                shutil.copyfileobj(resp, out, length=1 << 16)

            with tarfile.open(tar_path, "r:gz") as tar:
                # Tarball has a single top-level directory like Auditarr-<sha>/
                members = tar.getmembers()
                if not members:
                    raise RuntimeError("Empty tarball")
                top = members[0].name.split("/", 1)[0]
                tar.extractall(tmp_dir, filter="data" if hasattr(tarfile, "data_filter") else None)

            src_root = os.path.join(tmp_dir, top)
            if not os.path.isdir(src_root):
                raise RuntimeError(f"Extracted top-level dir not found: {top}")

            # Copy files over the live install — skipping protected user data
            count = _copy_install(src_root, str(INSTALL_ROOT))

            # Mark new SHA as current
            actual_sha = target_sha or _state.get("latest_sha")
            now = datetime.now().isoformat()
            with _lock:
                if actual_sha:
                    _state["current_sha"] = actual_sha
                    _state["current_short"] = actual_sha[:7]
                _state["last_install_at"] = now
                _state["last_install_error"] = None
                _save_state_file(_state)
                _recompute_available()

            return {
                "ok": True,
                "files_installed": count,
                "branch": branch,
                "sha": actual_sha,
                "restart_required": True,
                "message": f"Installed {count} files. Restart Auditarr to apply.",
            }
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception as e:
        with _lock:
            _state["last_install_error"] = str(e)
        return {"ok": False, "error": str(e)}
    finally:
        with _lock: _state["installing"] = False
        _install_lock.release()


def _copy_install(src_root: str, dst_root: str) -> int:
    """Copy files from src_root to dst_root, preserving protected user data.

    For directories like `frontend/` and `integrations/` we copy individual
    files (so e.g. a deleted file in src is removed from dst). For the install
    root we never delete arbitrary files — too dangerous.
    """
    count = 0
    for dirpath, dirnames, filenames in os.walk(src_root):
        # Skip protected dirs at any level
        dirnames[:] = [d for d in dirnames if d not in PROTECTED_DIRS]
        rel = os.path.relpath(dirpath, src_root)
        target_dir = dst_root if rel == "." else os.path.join(dst_root, rel)
        os.makedirs(target_dir, exist_ok=True)
        for fn in filenames:
            if fn in PROTECTED_FILES: continue
            src = os.path.join(dirpath, fn)
            dst = os.path.join(target_dir, fn)
            # Atomic-ish: write to temp then rename
            tmp = dst + ".new"
            shutil.copy2(src, tmp)
            os.replace(tmp, dst)
            count += 1
    return count


# ─── Background poller ───────────────────────────────────────────────────────

class UpdatePoller(threading.Thread):
    def __init__(self, interval=POLL_INTERVAL):
        super().__init__(daemon=True)
        self.interval = interval
        self._stop = threading.Event()

    def stop(self): self._stop.set()

    def run(self):
        if self._stop.wait(30): return
        while not self._stop.is_set():
            try: fetch_latest()
            except Exception: pass
            if self._stop.wait(self.interval): return
