"""
integrations/tdarr.py — Full Tdarr integration with profile management and
remote path mapping.

Tdarr's API isn't perfectly documented; this uses the public endpoints used
by the Tdarr web UI.

What this provides:
  - test_connection()
  - sync():               pull libraries (a.k.a. "DBs"), running plugins, etc.
  - list_libraries():     for the UI to populate a dropdown
  - list_plugins():       global Tdarr plugins (the "wrapper" scripts)
  - queue_transcode():    enqueue a file for transcoding using either:
                            - a Tdarr library (with its existing flow), OR
                            - an inline profile {codec, container, ...}
  - list_jobs():          show what's currently queued / processing
  - parse_webhook():      Tdarr completion notifications (best-effort)

Remote path mapping
-------------------
Auditarr's library_paths might not match what Tdarr sees inside its
container. When sending file paths to Tdarr we apply a list of mappings
configured in the integration's `options.path_mappings`:

    [{"local": "/mnt/media/movies", "remote": "/media/Movies"}, ...]

Longest-prefix-match wins; if no mapping applies the path is sent as-is.
"""
import json
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import PurePosixPath

import db
from integrations.base import Integration, register


def _api(base_url: str, endpoint: str, method: str = "POST",
         body=None, timeout=30):
    """Tdarr's API uses POST with JSON bodies for most calls."""
    url = f"{base_url}/api/v2/{endpoint.lstrip('/')}"
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if not raw: return {}
        try: return json.loads(raw)
        except json.JSONDecodeError:
            return {"_raw": raw.decode("utf-8", errors="replace")}


def apply_path_mapping(local_path: str, mappings: list) -> str:
    """Translate an Auditarr local path to a Tdarr-visible path."""
    if not mappings: return local_path
    # Longest-prefix-match
    best = None
    for m in mappings:
        local = (m.get("local") or "").rstrip("/")
        if not local: continue
        if local_path == local or local_path.startswith(local + "/"):
            if best is None or len(local) > len(best.get("local", "")):
                best = m
    if not best: return local_path
    local = best["local"].rstrip("/")
    remote = (best.get("remote") or "").rstrip("/")
    return remote + local_path[len(local):]


@register
class TdarrIntegration(Integration):
    KIND = "tdarr"
    DISPLAY_NAME = "Tdarr"
    SUPPORTS_SYNC = True
    SUPPORTS_WEBHOOK = True
    SUPPORTS_AUTOMATION = True
    DESCRIPTION = (
        "Tdarr — distributed transcoding orchestrator. Auditarr can queue "
        "files for transcode using either a Tdarr library/plugin or an "
        "inline profile defined here."
    )

    # ─── Connection ──────────────────────────────────────────────────────────
    def test_connection(self):
        try:
            data = _api(self.base_url, "status", method="POST", timeout=10)
            ver = data.get("version") or data.get("nodeVersion") or "OK"
            return True, f"Connected — Tdarr {ver}"
        except urllib.error.HTTPError as e:
            # Some Tdarr versions return 404 for /status; try the cruddb endpoint
            try:
                data = _api(self.base_url, "cruddb",
                            body={"data": {"collection": "SettingsGlobalJSONDB",
                                           "mode": "getAll"}}, timeout=10)
                return True, "Connected — Tdarr (status endpoint legacy)"
            except Exception:
                return False, f"HTTP {e.code}"
        except urllib.error.URLError as e:
            return False, f"Cannot reach server: {e.reason}"
        except Exception as e:
            return False, f"Error: {e}"

    # ─── Sync libraries + plugin list ────────────────────────────────────────
    def sync(self):
        try:
            libs = self.list_libraries()
            plugins = self.list_plugins()
            opts = dict(self.options or {})
            opts["libraries"] = libs
            opts["plugins"] = plugins[:200]  # cap to avoid massive options blob
            opts["last_synced_at"] = datetime.now().isoformat()
            db.update_integration(self.id, options=opts,
                                  last_sync=datetime.now().isoformat(),
                                  last_error=None)
            return len(libs), f"Synced {len(libs)} libraries, {len(plugins)} plugins"
        except Exception as e:
            db.update_integration(self.id, last_error=str(e))
            return 0, f"Sync failed: {e}"

    def list_libraries(self):
        """Return list of {id, name, paths}."""
        try:
            data = _api(self.base_url, "cruddb",
                        body={"data": {"collection": "SettingsGlobalJSONDB",
                                       "mode": "getAll"}})
            settings = data if isinstance(data, list) else data.get("data", [])
            for entry in (settings or []):
                if isinstance(entry, dict) and "libraries" in entry:
                    libs = entry["libraries"]
                    out = []
                    for lid, lcfg in (libs or {}).items():
                        out.append({
                            "id":     lid,
                            "name":   lcfg.get("name", lid),
                            "folder": lcfg.get("folder", ""),
                            "scanFound":   lcfg.get("scanFound", 0),
                            "scanFreshFound":   lcfg.get("scanFreshFound", 0),
                        })
                    return out
            return []
        except Exception:
            return []

    def list_plugins(self):
        """Return list of community plugin {id, type, description}."""
        try:
            data = _api(self.base_url, "cruddb",
                        body={"data": {"collection": "FlowsCommunity",
                                       "mode": "getAll"}})
            flows = data if isinstance(data, list) else data.get("data", [])
            out = []
            for f in (flows or []):
                if not isinstance(f, dict): continue
                out.append({
                    "id":   f.get("_id") or f.get("name"),
                    "name": f.get("name") or f.get("_id"),
                    "type": f.get("type", "flow"),
                    "description": (f.get("description") or "")[:200],
                })
            return out
        except Exception:
            return []

    # ─── Queue a transcode ───────────────────────────────────────────────────
    def queue_transcode(self, file_path: str, *, library_id: str = None,
                        plugin_id: str = None, inline_profile: dict = None,
                        priority: int = 5):
        """
        Send a file to Tdarr's processing queue.

        Three modes:
        1. library_id — file is added to that library's queue (Tdarr applies the
           library's configured plugins)
        2. plugin_id  — file is processed by a specific Flow/plugin
        3. inline_profile — Auditarr-defined target spec; we tell Tdarr to encode
           with these parameters via the GenericTranscode flow (must be installed)

        inline_profile supports:
          {
            "codec": "hevc" | "h264" | "av1",
            "container": "mkv" | "mp4",
            "resolution_max": "1080p" | "4k",
            "audio_codec": "aac" | "ac3" | "copy",
            "audio_bitrate": "128k",
            "video_bitrate": "5M",       # or use crf
            "crf": 22,
            "hardware_accel": "qsv" | "nvenc" | "vaapi" | null
          }
        """
        mappings = (self.options or {}).get("path_mappings") or []
        remote_path = apply_path_mapping(file_path, mappings)

        body = {"data": {"file": remote_path, "priority": priority}}
        if library_id:
            body["data"]["library_id"] = library_id
            endpoint = "queue/insert"
        elif plugin_id:
            body["data"]["flow_id"] = plugin_id
            endpoint = "queue/insert"
        elif inline_profile:
            body["data"]["custom_profile"] = inline_profile
            endpoint = "queue/insert"
        else:
            return False, "Must provide library_id, plugin_id, or inline_profile"

        try:
            data = _api(self.base_url, endpoint, body=body)
            return True, f"Queued {remote_path}"
        except urllib.error.HTTPError as e:
            return False, f"HTTP {e.code}"
        except Exception as e:
            return False, str(e)

    def list_jobs(self):
        """Return what's currently in the queue / being processed."""
        try:
            data = _api(self.base_url, "cruddb",
                        body={"data": {"collection": "JobReportJSONDB",
                                       "mode": "getAll"}})
            jobs = data if isinstance(data, list) else data.get("data", [])
            return jobs[:100]
        except Exception:
            return []

    # ─── Webhook (Tdarr completion) ──────────────────────────────────────────
    def parse_webhook(self, payload):
        """
        Tdarr completion webhook is custom — users wire one with the
        community 'Webhook on completion' flow. Common keys:
          - file: original file path
          - new_file: transcoded output path
          - status: success | failed
        """
        ftype = payload.get("status") or payload.get("event") or "Tdarr Event"
        f = payload.get("file") or payload.get("source_file") or ""
        nf = payload.get("new_file") or payload.get("output_file") or ""
        paths = list({p for p in (f, nf) if p})
        return {"kind": "tdarr", "event_type": ftype, "file_paths": paths}
