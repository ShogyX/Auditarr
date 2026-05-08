"""
integrations/sonarr_radarr.py — Sonarr & Radarr: full API + webhook + automation.
"""
import json
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import db
from integrations.base import Integration, register


def _api(base_url, api_key, endpoint, method="GET", body=None, timeout=30):
    url = f"{base_url}/api/v3/{endpoint.lstrip('/')}"
    headers = {"X-Api-Key": api_key}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        return json.loads(raw) if raw else {}


# ─── Sonarr ───────────────────────────────────────────────────────────────────

@register
class SonarrIntegration(Integration):
    KIND = "sonarr"
    DISPLAY_NAME = "Sonarr"
    SUPPORTS_SYNC = True
    SUPPORTS_WEBHOOK = True
    SUPPORTS_AUTOMATION = True
    DESCRIPTION = "TV show management — link episodes, react to webhook events, automate monitoring based on file severity"

    def test_connection(self):
        try:
            info = _api(self.base_url, self.api_key, "system/status", timeout=10)
            return True, f"Connected — Sonarr v{info.get('version','?')}"
        except urllib.error.HTTPError as e:
            return False, f"HTTP {e.code}: check API key and URL"
        except urllib.error.URLError as e:
            return False, f"Cannot reach server: {e.reason}"
        except Exception as e:
            return False, f"Error: {e}"

    def sync(self):
        try:
            series_list = _api(self.base_url, self.api_key, "series")
        except Exception as e:
            db.update_integration(self.id, last_error=str(e))
            return 0, f"Sync failed: {e}"

        linked = 0
        total = 0
        for s in series_list:
            sid = s.get("id")
            series_title = s.get("title", "")
            try:
                efiles = _api(self.base_url, self.api_key, f"episodefile?seriesId={sid}")
                episodes = _api(self.base_url, self.api_key, f"episode?seriesId={sid}")
            except Exception:
                continue
            ep_by_file = {}
            for ep in episodes:
                ef_id = ep.get("episodeFileId")
                if ef_id:
                    ep_by_file.setdefault(ef_id, []).append(ep)

            for ef in efiles:
                total += 1
                path = ef.get("path")
                if not path: continue
                eps = ep_by_file.get(ef.get("id"), [])
                monitored = any(ep.get("monitored") for ep in eps) if eps else None
                if db.get_file(path):
                    db.link_file_to_arr(path, "sonarr", sid, {
                        "series_id": sid, "series_title": series_title,
                        "season": ef.get("seasonNumber"),
                        "episode_file_id": ef.get("id"),
                        "size": ef.get("size", 0),
                        "quality": (ef.get("quality") or {}).get("quality", {}).get("name"),
                        "release_group": ef.get("releaseGroup"),
                        "languages": [l.get("name") for l in ef.get("languages") or []],
                        "episodes": [{"season": ep.get("seasonNumber"), "episode": ep.get("episodeNumber"), "title": ep.get("title")} for ep in eps],
                    }, arr_file_id=ef.get("id"), monitored=monitored)
                    linked += 1

        db.update_integration(self.id, last_sync=datetime.now().isoformat(), last_error=None)
        return linked, f"Linked {linked} of {total} episode files"

    def parse_webhook(self, payload):
        event_type = payload.get("eventType") or "Unknown"
        paths = []
        ef = payload.get("episodeFile") or {}
        if ef.get("path"): paths.append(ef["path"])
        for f in payload.get("episodeFiles", []) or []:
            if f.get("path"): paths.append(f["path"])
        for f in payload.get("renamedEpisodeFiles", []) or []:
            if f.get("path"): paths.append(f["path"])
            if f.get("previousPath"): paths.append(f["previousPath"])
        return {"kind": "sonarr", "event_type": event_type, "file_paths": list(set(paths))}

    def set_monitored(self, episode_file_id: int, monitored: bool):
        """Toggle monitoring on the episodes that own this file."""
        try:
            episodes = _api(self.base_url, self.api_key, f"episode?episodeFileId={episode_file_id}") or []
            ep_ids = [e.get("id") for e in episodes if e.get("id")]
            if not ep_ids: return False, "No episodes found for file"
            _api(self.base_url, self.api_key, "episode/monitor", method="PUT", body={
                "episodeIds": ep_ids,
                "monitored": monitored,
            })
            return True, f"Set monitored={monitored} on {len(ep_ids)} episode(s)"
        except Exception as e:
            return False, str(e)


# ─── Radarr ───────────────────────────────────────────────────────────────────

@register
class RadarrIntegration(Integration):
    KIND = "radarr"
    DISPLAY_NAME = "Radarr"
    SUPPORTS_SYNC = True
    SUPPORTS_WEBHOOK = True
    SUPPORTS_AUTOMATION = True
    DESCRIPTION = "Movie management — link movies, react to webhook events, automate monitoring based on file severity"

    def test_connection(self):
        try:
            info = _api(self.base_url, self.api_key, "system/status", timeout=10)
            return True, f"Connected — Radarr v{info.get('version','?')}"
        except urllib.error.HTTPError as e:
            return False, f"HTTP {e.code}: check API key and URL"
        except urllib.error.URLError as e:
            return False, f"Cannot reach server: {e.reason}"
        except Exception as e:
            return False, f"Error: {e}"

    def sync(self):
        try:
            movies = _api(self.base_url, self.api_key, "movie")
        except Exception as e:
            db.update_integration(self.id, last_error=str(e))
            return 0, f"Sync failed: {e}"

        linked, total = 0, 0
        for m in movies:
            mf = m.get("movieFile") or {}
            if not m.get("hasFile") or not mf:
                continue
            total += 1
            folder = m.get("path", "")
            rel = mf.get("relativePath", "")
            full = str(Path(folder) / rel) if folder and rel else mf.get("path")
            if not full: continue
            if db.get_file(full):
                db.link_file_to_arr(full, "radarr", m.get("id"), {
                    "movie_id": m.get("id"),
                    "title": m.get("title"), "year": m.get("year"),
                    "tmdb_id": m.get("tmdbId"), "imdb_id": m.get("imdbId"),
                    "movie_file_id": mf.get("id"),
                    "size": mf.get("size", 0),
                    "quality": (mf.get("quality") or {}).get("quality", {}).get("name"),
                    "release_group": mf.get("releaseGroup"),
                    "edition": mf.get("edition"),
                }, arr_file_id=mf.get("id"), monitored=m.get("monitored"))
                linked += 1

        db.update_integration(self.id, last_sync=datetime.now().isoformat(), last_error=None)
        return linked, f"Linked {linked} of {total} movie files"

    def parse_webhook(self, payload):
        event_type = payload.get("eventType") or "Unknown"
        paths = []
        mf = payload.get("movieFile") or {}
        if mf.get("path"): paths.append(mf["path"])
        for f in payload.get("renamedMovieFiles", []) or []:
            if f.get("path"): paths.append(f["path"])
            if f.get("previousPath"): paths.append(f["previousPath"])
        if not paths and payload.get("path"):
            paths.append(payload["path"])
        return {"kind": "radarr", "event_type": event_type, "file_paths": list(set(paths))}

    def set_monitored(self, movie_id: int, monitored: bool):
        try:
            movie = _api(self.base_url, self.api_key, f"movie/{movie_id}")
            movie["monitored"] = monitored
            _api(self.base_url, self.api_key, f"movie/{movie_id}", method="PUT", body=movie)
            return True, f"Movie monitored={monitored}"
        except Exception as e:
            return False, str(e)
