"""
integrations/scaffolds.py — Stub integrations for Plex, Jellyfin, Tdarr, Bazarr.

These provide:
  - test_connection() that pings the right endpoint and returns version info
  - registration so they appear in the UI's "Add integration" picker
  - real sync/webhook/automation TBD in a future iteration
"""
import json
import urllib.error
import urllib.request

from integrations.base import Integration, register


# ─── Plex ─────────────────────────────────────────────────────────────────────

@register
class PlexIntegration(Integration):
    KIND = "plex"
    DISPLAY_NAME = "Plex Media Server"
    SUPPORTS_SYNC = False
    SUPPORTS_WEBHOOK = False
    SUPPORTS_AUTOMATION = False
    DESCRIPTION = "Plex Media Server — for direct playback testing and library cross-reference (read-only sync coming soon)"

    def test_connection(self):
        """Plex uses X-Plex-Token instead of API key. Check /identity endpoint."""
        try:
            url = f"{self.base_url}/identity"
            req = urllib.request.Request(url, headers={
                "Accept": "application/json",
                "X-Plex-Token": self.api_key,
            })
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            mc = data.get("MediaContainer", {})
            ver = mc.get("version", "?")
            name = mc.get("friendlyName", "")
            return True, f"Connected — Plex {ver}{(' — ' + name) if name else ''}"
        except urllib.error.HTTPError as e:
            if e.code == 401:
                return False, "Unauthorized — check your X-Plex-Token"
            return False, f"HTTP {e.code}"
        except urllib.error.URLError as e:
            return False, f"Cannot reach server: {e.reason}"
        except Exception as e:
            return False, f"Error: {e}"


# ─── Jellyfin ─────────────────────────────────────────────────────────────────

@register
class JellyfinIntegration(Integration):
    KIND = "jellyfin"
    DISPLAY_NAME = "Jellyfin"
    SUPPORTS_SYNC = False
    SUPPORTS_WEBHOOK = False
    SUPPORTS_AUTOMATION = False
    DESCRIPTION = "Jellyfin — alternative media server. Will be used for cross-library validation (library sync coming soon)"

    def test_connection(self):
        try:
            url = f"{self.base_url}/System/Info/Public"
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            ver = data.get("Version", "?")
            name = data.get("ServerName", "")
            return True, f"Connected — Jellyfin {ver}{(' — ' + name) if name else ''}"
        except urllib.error.HTTPError as e:
            return False, f"HTTP {e.code}"
        except urllib.error.URLError as e:
            return False, f"Cannot reach server: {e.reason}"
        except Exception as e:
            return False, f"Error: {e}"


# ─── Tdarr — moved to tdarr.py with full implementation ─────────────────────


# ─── Bazarr — moved to bazarr.py with full implementation ────────────────────
