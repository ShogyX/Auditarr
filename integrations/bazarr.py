"""
integrations/bazarr.py — Full Bazarr integration.

What this does:
  - test_connection(): pings /api/system/status
  - sync():            walks Bazarr's series + movies subtitle inventory and
                       links any external subs we recognise to the *arr file
  - parse_webhook():   handles "Sub.Added" / "Sub.Removed" custom webhooks,
                       triggers targeted scans / DB updates as appropriate
  - delete_subtitle(): tells Bazarr to remove a subtitle (used when Auditarr
                       flags an orphan or invalid sub the user wants gone)
  - search_subtitles():tells Bazarr to re-search subs for a media item

Bazarr's REST API is documented at /api/swagger on the user's instance.
Auth: header "X-API-KEY".
"""
import json
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import db
from integrations.base import Integration, register


def _api(base_url: str, api_key: str, endpoint: str, method: str = "GET",
         body=None, timeout=30):
    url = f"{base_url}/api/{endpoint.lstrip('/')}"
    headers = {"X-API-KEY": api_key, "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        if not raw: return {}
        try: return json.loads(raw)
        except json.JSONDecodeError: return {}


@register
class BazarrIntegration(Integration):
    KIND = "bazarr"
    DISPLAY_NAME = "Bazarr"
    SUPPORTS_SYNC = True
    SUPPORTS_WEBHOOK = True
    SUPPORTS_AUTOMATION = True
    DESCRIPTION = (
        "Bazarr — subtitle management. Auditarr can sync subtitle inventory, "
        "react to add/remove webhooks, and trigger re-search or deletion when "
        "files are flagged."
    )

    def test_connection(self):
        try:
            data = _api(self.base_url, self.api_key, "system/status", timeout=10)
            d = data.get("data", data) if isinstance(data, dict) else {}
            ver = d.get("bazarr_version") or d.get("version", "?")
            return True, f"Connected — Bazarr {ver}"
        except urllib.error.HTTPError as e:
            return False, f"HTTP {e.code}: check API key"
        except urllib.error.URLError as e:
            return False, f"Cannot reach server: {e.reason}"
        except Exception as e:
            return False, f"Error: {e}"

    # ─── Sync ────────────────────────────────────────────────────────────────
    def sync(self):
        try:
            sa = self._sync_series()
        except Exception as e:
            sa = (0, f"series sync failed: {e}")
        try:
            ma = self._sync_movies()
        except Exception as e:
            ma = (0, f"movies sync failed: {e}")

        total_linked = sa[0] + ma[0]
        msg = f"Series: {sa[1]} · Movies: {ma[1]}"
        db.update_integration(self.id, last_sync=datetime.now().isoformat(),
                              last_error=None if total_linked else msg)
        return total_linked, msg

    def _sync_series(self):
        """Pull all series episodes and link subtitles."""
        # /api/series returns list, /api/episodes/wanted etc - we want all subs
        try:
            series = _api(self.base_url, self.api_key, "series").get("data", [])
        except Exception as e:
            return 0, f"series API failed: {e}"
        linked = 0
        for s in series:
            sid = s.get("sonarrSeriesId") or s.get("id")
            if not sid: continue
            try:
                episodes = _api(self.base_url, self.api_key,
                                f"episodes?seriesid[]={sid}").get("data", [])
            except Exception:
                continue
            for ep in episodes:
                # Each episode has 'subtitles' (embedded) and external sub paths
                ep_path = ep.get("path")
                ext_subs = ep.get("subtitles", []) or []
                for sub in ext_subs:
                    sub_path = sub.get("path") if isinstance(sub, dict) else None
                    if not sub_path: continue
                    # Look up the file in our DB; if present, attach Bazarr metadata
                    f = db.get_file(sub_path)
                    if f:
                        meta = {
                            "bazarr_sub_id": sub.get("id"),
                            "bazarr_lang":   sub.get("language"),
                            "bazarr_forced": sub.get("forced"),
                            "bazarr_hi":     sub.get("hi"),
                            "media_path":    ep_path,
                        }
                        db.link_file_to_arr(sub_path, "bazarr",
                                            sid, meta, arr_file_id=ep.get("id"))
                        linked += 1
        return linked, f"linked {linked} episode subs"

    def _sync_movies(self):
        try:
            movies = _api(self.base_url, self.api_key, "movies").get("data", [])
        except Exception as e:
            return 0, f"movies API failed: {e}"
        linked = 0
        for m in movies:
            mid = m.get("radarrId") or m.get("id")
            mov_path = m.get("path")
            ext_subs = m.get("subtitles", []) or []
            for sub in ext_subs:
                sub_path = sub.get("path") if isinstance(sub, dict) else None
                if not sub_path: continue
                f = db.get_file(sub_path)
                if f:
                    meta = {
                        "bazarr_sub_id": sub.get("id"),
                        "bazarr_lang":   sub.get("language"),
                        "bazarr_forced": sub.get("forced"),
                        "bazarr_hi":     sub.get("hi"),
                        "media_path":    mov_path,
                    }
                    db.link_file_to_arr(sub_path, "bazarr", mid, meta,
                                        arr_file_id=mid)
                    linked += 1
        return linked, f"linked {linked} movie subs"

    # ─── Webhook (Bazarr custom webhook, Notifier setup in Bazarr UI) ────────
    def parse_webhook(self, payload):
        """
        Bazarr's custom webhook payload (set up under Settings → Notifications).
        Common keys:
          - notification_type: "Subtitle Downloaded" | "Subtitle Removed" | ...
          - file: full path to the subtitle file
          - language: "English" or similar
          - media_type: "movie" | "series"
        """
        ntype = (payload.get("notification_type")
                 or payload.get("event_type")
                 or "Unknown")
        file_path = payload.get("file") or payload.get("subtitle_path") or ""
        return {
            "kind": "bazarr",
            "event_type": ntype,
            "file_paths": [file_path] if file_path else [],
        }

    # ─── Outbound actions ────────────────────────────────────────────────────
    def delete_subtitle(self, sub_path: str, media_type: str = "episode",
                        bazarr_id=None) -> tuple[bool, str]:
        """Tell Bazarr to delete an external sub.

        Bazarr API: POST /api/{episodes,movies}/subtitles with action=delete.
        The exact spec depends on Bazarr version; we use the modern v2 endpoint.
        """
        endpoint = "episodes/subtitles" if media_type == "episode" else "movies/subtitles"
        try:
            body = {
                "action": "delete",
                "language": "all",
                "path": sub_path,
            }
            if bazarr_id is not None:
                key = "seriesid" if media_type == "episode" else "radarrid"
                body[key] = int(bazarr_id)
            _api(self.base_url, self.api_key, endpoint, method="PATCH", body=body)
            return True, "Subtitle deletion requested"
        except urllib.error.HTTPError as e:
            return False, f"HTTP {e.code}"
        except Exception as e:
            return False, str(e)

    def search_subtitles(self, media_type: str, bazarr_id: int) -> tuple[bool, str]:
        endpoint = "episodes/subtitles" if media_type == "episode" else "movies/subtitles"
        try:
            body = {"action": "search"}
            key = "seriesid" if media_type == "episode" else "radarrid"
            body[key] = int(bazarr_id)
            _api(self.base_url, self.api_key, endpoint, method="PATCH", body=body)
            return True, "Search queued"
        except Exception as e:
            return False, str(e)
