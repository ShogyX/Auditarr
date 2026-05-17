"""Plugin gallery client.

Reads a JSON manifest of community plugins from a configurable URL.
The manifest shape is:

    {
      "plugins": [
        {
          "id": "fingerprint",
          "name": "Audio fingerprinting",
          "description": "...",
          "author": "@someone",
          "version": "0.3.0",
          "source_url": "https://github.com/.../fingerprint",
          "install_url": "https://.../fingerprint-0.3.0.tar.gz",
          "install_instructions": "Extract into ./plugins/ and restart.",
          "categories": ["analysis"]
        }
      ]
    }

We don't install plugins automatically. The gallery is a *directory* —
operators see what's out there, then either follow ``install_instructions``
manually or download the tarball, drop it in their plugin volume, and
restart. Auto-install is intentionally out of scope: dropping a remote
tarball onto disk and executing it is a supply-chain footgun that
self-hosted operators shouldn't have to opt out of.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from app.core.http import async_client

from app.core.logging import get_logger

log = get_logger("auditarr.plugins.gallery", category="plugins")


@dataclass(slots=True)
class GalleryPlugin:
    id: str
    name: str
    description: str | None = None
    author: str | None = None
    version: str | None = None
    source_url: str | None = None
    install_url: str | None = None
    install_instructions: str | None = None
    categories: list[str] = field(default_factory=list)


@dataclass(slots=True)
class GalleryFeed:
    ok: bool
    plugins: list[GalleryPlugin] = field(default_factory=list)
    detail: str | None = None


def _coerce_plugin(item: dict[str, Any]) -> GalleryPlugin | None:
    """Best-effort coercion. Anything malformed is silently skipped so
    one bad entry doesn't poison the whole feed."""
    raw_id = item.get("id")
    name = item.get("name")
    if not isinstance(raw_id, str) or not raw_id.strip():
        return None
    if not isinstance(name, str) or not name.strip():
        return None
    cats = item.get("categories") or []
    if not isinstance(cats, list):
        cats = []
    return GalleryPlugin(
        id=raw_id.strip(),
        name=name.strip(),
        description=item.get("description") if isinstance(item.get("description"), str) else None,
        author=item.get("author") if isinstance(item.get("author"), str) else None,
        version=item.get("version") if isinstance(item.get("version"), str) else None,
        source_url=item.get("source_url") if isinstance(item.get("source_url"), str) else None,
        install_url=item.get("install_url") if isinstance(item.get("install_url"), str) else None,
        install_instructions=(
            item.get("install_instructions")
            if isinstance(item.get("install_instructions"), str)
            else None
        ),
        categories=[c for c in cats if isinstance(c, str)],
    )


async def fetch_gallery(url: str, *, timeout: float = 10.0) -> GalleryFeed:
    """Hit ``url`` and normalize the response."""
    try:
        async with async_client(
            timeout=timeout,
            headers={"User-Agent": "auditarr-plugin-gallery"},
        ) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        log.warning("gallery.unreachable", url=url, error=str(exc))
        return GalleryFeed(ok=False, detail=f"gallery unreachable: {exc!s}"[:500])

    if response.status_code >= 400:
        return GalleryFeed(
            ok=False, detail=f"gallery returned HTTP {response.status_code}"
        )
    try:
        payload = response.json()
    except ValueError:
        return GalleryFeed(ok=False, detail="gallery returned non-JSON body")
    if not isinstance(payload, dict):
        return GalleryFeed(ok=False, detail="gallery root is not an object")
    raw = payload.get("plugins")
    if not isinstance(raw, list):
        return GalleryFeed(ok=False, detail="gallery 'plugins' must be a list")
    plugins = [p for p in (_coerce_plugin(item) for item in raw if isinstance(item, dict)) if p is not None]
    return GalleryFeed(ok=True, plugins=plugins)
