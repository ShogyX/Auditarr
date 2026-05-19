"""Update feed client.

Knows how to fetch the configured ``update_feed_url`` and normalize the
response into a :class:`FeedResult`. Three response shapes are accepted:

* **GitHub commits** (``https://api.github.com/repos/.../commits/<branch>``)
  — looks for ``sha`` + ``commit.message`` + ``commit.committer.date``.
  This is the v1.9.x default — "any newer commit on main" rather than
  "any newer release tag".
* **GitHub Releases** (``.../releases/latest``) — looks for ``tag_name``
  and ``body``. Operators who prefer release-tag cadence can swap back.
* **Generic** — any JSON object with ``version`` and optional
  ``changelog`` keys. This is the shape self-hosted mirrors should
  emit. ``commit_sha``/``commit_date``/``commit_message`` keys are
  accepted alongside ``version`` so commit-style mirrors work too.

We pick the shape by inspecting the top-level keys rather than the URL,
so swapping mirrors doesn't require code changes.

All shapes are read-only. The updater never POSTs to the feed.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.http import async_client
from app.core.logging import get_logger

log = get_logger("auditarr.updater.feed", category="updater")


@dataclass(slots=True)
class FeedResult:
    """Normalized "what's the latest upstream build" response.

    Either ``commit_sha`` (commit-based feed) or ``version`` (tag- or
    generic-feed) — or both, when the upstream feed carries both —
    will be populated when ``ok=True``. The service layer prefers
    commit-based comparison whenever ``commit_sha`` is present.
    """

    ok: bool
    version: str | None = None
    changelog: str | None = None
    detail: str | None = None
    commit_sha: str | None = None
    commit_date: _dt.datetime | None = None
    commit_message: str | None = None


def _parse_iso(value: object) -> _dt.datetime | None:
    """Parse an ISO-8601 timestamp ("...Z" or "...+00:00"), return None
    when the value is missing or malformed. Stays in UTC."""
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip().replace("Z", "+00:00")
    try:
        dt = _dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt


def _normalize_github_commits(payload: dict[str, Any]) -> FeedResult:
    """GitHub's ``/commits/<branch>`` (single-commit) shape:

    ``{"sha": "...", "commit": {"message": "...", "committer":
    {"date": "..."}, "author": {"date": "..."}}, ...}``.

    Falls back from committer date to author date — GitHub returns
    both and they're usually identical, but a force-push can rewrite
    one without the other.
    """
    sha = payload.get("sha")
    if not isinstance(sha, str) or not sha.strip():
        return FeedResult(ok=False, detail="commits feed missing 'sha'")
    commit = payload.get("commit") or {}
    message = commit.get("message") if isinstance(commit, dict) else None
    committer = commit.get("committer") if isinstance(commit, dict) else {}
    author = commit.get("author") if isinstance(commit, dict) else {}
    date = _parse_iso(
        (committer or {}).get("date") if isinstance(committer, dict) else None
    ) or _parse_iso(
        (author or {}).get("date") if isinstance(author, dict) else None
    )
    return FeedResult(
        ok=True,
        commit_sha=sha.strip(),
        commit_date=date,
        commit_message=message if isinstance(message, str) else None,
        # ``changelog`` is the field the UI already renders — surface
        # the commit message there too so the existing "release notes"
        # panel keeps working in commit mode.
        changelog=message if isinstance(message, str) else None,
    )


def _normalize_github_release(payload: dict[str, Any]) -> FeedResult:
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
    """Generic ``{"version": "...", "changelog": "..."}`` shape — plus
    optional ``commit_sha``/``commit_date``/``commit_message`` for
    mirrors that want to expose commit identity too."""
    version = payload.get("version")
    sha = payload.get("commit_sha") or payload.get("sha")
    if (
        (not isinstance(version, str) or not version.strip())
        and (not isinstance(sha, str) or not sha.strip())
    ):
        return FeedResult(
            ok=False,
            detail="generic feed missing 'version' or 'commit_sha'",
        )
    changelog = payload.get("changelog")
    return FeedResult(
        ok=True,
        version=version.strip().lstrip("v") if isinstance(version, str) else None,
        changelog=changelog if isinstance(changelog, str) else None,
        commit_sha=sha.strip() if isinstance(sha, str) else None,
        commit_date=_parse_iso(payload.get("commit_date")),
        commit_message=payload.get("commit_message")
        if isinstance(payload.get("commit_message"), str)
        else None,
    )


def _detect_shape(payload: dict[str, Any]) -> str:
    """Pick which normalizer to apply.

    ``sha`` + ``commit`` (with commit metadata) is the commits-API
    signature. ``tag_name`` is the releases-API signature. Otherwise
    fall through to the generic shape.
    """
    if isinstance(payload.get("sha"), str) and isinstance(
        payload.get("commit"), dict
    ):
        return "commits"
    if "tag_name" in payload:
        return "release"
    return "generic"


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

    shape = _detect_shape(payload)
    if shape == "commits":
        return _normalize_github_commits(payload)
    if shape == "release":
        return _normalize_github_release(payload)
    return _normalize_generic(payload)
