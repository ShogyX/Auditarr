"""Update feed client.

Knows how to fetch the configured ``update_feed_url`` and normalize the
response into a :class:`FeedResult`. Two response shapes are accepted:

* **GitHub Releases** (``https://api.github.com/repos/.../releases/latest``)
  — looks for ``tag_name`` and ``body``.
* **Generic** — any JSON object with ``version`` and optional ``changelog``
  keys. This is the shape self-hosted mirrors should emit.

We pick the shape by inspecting the top-level keys rather than the URL,
so swapping mirrors doesn't require code changes.

Both shapes are read-only. The updater never POSTs to the feed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.core.http import async_client

from app.core.logging import get_logger

log = get_logger("auditarr.updater.feed", category="updater")


@dataclass(slots=True)
class FeedResult:
    """Normalized "what's the latest release" response."""

    ok: bool
    version: str | None = None
    changelog: str | None = None
    detail: str | None = None


def _normalize_github(payload: dict[str, Any]) -> FeedResult:
    """Pull tag + body out of a GitHub Releases JSON payload.

    GitHub strips the leading ``v`` on ``tag_name`` only when the tag
    itself doesn't carry one — most repos do, so we strip it explicitly.
    """
    tag = payload.get("tag_name")
    if not isinstance(tag, str) or not tag.strip():
        return FeedResult(
            ok=False,
            detail="GitHub feed missing tag_name",
        )
    version = tag.strip().lstrip("v")
    body = payload.get("body")
    return FeedResult(
        ok=True,
        version=version,
        changelog=body if isinstance(body, str) else None,
    )


def _normalize_generic(payload: dict[str, Any]) -> FeedResult:
    version = payload.get("version")
    if not isinstance(version, str) or not version.strip():
        return FeedResult(ok=False, detail="Generic feed missing 'version'")
    changelog = payload.get("changelog")
    return FeedResult(
        ok=True,
        version=version.strip().lstrip("v"),
        changelog=changelog if isinstance(changelog, str) else None,
    )


async def fetch_feed(url: str, *, timeout: float = 10.0) -> FeedResult:
    """Fetch + normalize. Network/HTTP errors return ``ok=False``."""
    try:
        async with async_client(
            timeout=timeout,
            headers={
                # Identify ourselves so GitHub doesn't 403 us on shared
                # IPs that have hit the unauthenticated rate limit
                # window unusually hard.
                "User-Agent": "auditarr-updater",
                # Ask for the v3 API explicitly so GitHub stops switching
                # behaviour on us when they roll new defaults.
                "Accept": "application/vnd.github+json",
            },
        ) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        log.warning("updater.feed_unreachable", url=url, error=str(exc))
        return FeedResult(ok=False, detail=f"feed unreachable: {exc!s}"[:500])

    if response.status_code >= 400:
        return FeedResult(
            ok=False,
            detail=f"feed returned HTTP {response.status_code}",
        )
    try:
        payload = response.json()
    except ValueError:
        return FeedResult(ok=False, detail="feed returned non-JSON body")
    if not isinstance(payload, dict):
        return FeedResult(ok=False, detail="feed root is not a JSON object")

    # Shape detection: GitHub releases always have ``tag_name``.
    if "tag_name" in payload:
        return _normalize_github(payload)
    if "version" in payload:
        return _normalize_generic(payload)
    return FeedResult(
        ok=False,
        detail="feed payload had neither 'tag_name' nor 'version'",
    )
