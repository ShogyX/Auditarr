"""
updater.py — GitHub commit poller for "new version available" banner.

User picked: notify-only (no auto-pull). We just check the public GitHub API
periodically and surface the result via /api/update/check.

Repo is public so no token needed. Rate limit for unauth'd is 60 req/hr/IP,
so polling every 6 hours is plenty safe.
"""
import json
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = "ShogyX/Auditarr"
BRANCH = "main"
VERSION_FILE = Path(__file__).parent / ".auditarr_version.json"
POLL_INTERVAL = 6 * 60 * 60  # 6 hours

_state = {
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
}
_lock = threading.Lock()


def _load_current() -> str | None:
    """Load the SHA the user is running. Set when 'mark current' is called."""
    if VERSION_FILE.exists():
        try:
            d = json.loads(VERSION_FILE.read_text())
            return d.get("current_sha")
        except Exception:
            return None
    return None


def _save_current(sha: str):
    VERSION_FILE.write_text(json.dumps({"current_sha": sha, "updated": datetime.now().isoformat()}, indent=2))


def mark_current(sha: str):
    """Tell the updater 'this is the SHA you have'. Called once after deploy."""
    _save_current(sha)
    with _lock:
        _state["current_sha"] = sha
        _state["current_short"] = sha[:7] if sha else None
        _recompute_available()


def _recompute_available():
    cs = _state.get("current_sha")
    ls = _state.get("latest_sha")
    _state["available"] = bool(cs and ls and cs != ls)


def get_state() -> dict:
    with _lock:
        return dict(_state)


def fetch_latest() -> dict:
    """One-shot fetch of the latest commit on the default branch."""
    url = f"https://api.github.com/repos/{REPO}/commits/{BRANCH}"
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
            if _state.get("current_sha") is None:
                _state["current_sha"] = _load_current()
                _state["current_short"] = (_state["current_sha"] or "")[:7] or None
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
    """Manual trigger from the UI."""
    return fetch_latest()


# ─── Background poller ────────────────────────────────────────────────────────

class UpdatePoller(threading.Thread):
    def __init__(self, interval=POLL_INTERVAL):
        super().__init__(daemon=True)
        self.interval = interval
        self._stop = threading.Event()

    def stop(self): self._stop.set()

    def run(self):
        # First check after 30s to give server a chance to come up
        if self._stop.wait(30): return
        while not self._stop.is_set():
            try: fetch_latest()
            except Exception: pass
            if self._stop.wait(self.interval): return


def initial_load():
    """Called on startup — populate the state with the stored SHA."""
    sha = _load_current()
    with _lock:
        _state["current_sha"] = sha
        _state["current_short"] = sha[:7] if sha else None
