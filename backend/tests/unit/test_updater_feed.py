"""Update-feed client tests.

We mount httpx.MockTransport to avoid network access. The feed client
should normalize both GitHub releases and a generic ``{"version", "changelog"}``
shape into a single ``FeedResult``.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.updater.feed import fetch_feed


def _build_transport(
    body: dict[str, Any] | str | None,
    *,
    status_code: int = 200,
    raise_exc: Exception | None = None,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if raise_exc is not None:
            raise raise_exc
        if body is None:
            return httpx.Response(status_code)
        if isinstance(body, str):
            return httpx.Response(status_code, content=body.encode("utf-8"))
        return httpx.Response(status_code, json=body)

    return httpx.MockTransport(handler)


@pytest.fixture(autouse=True)
def patch_httpx(monkeypatch: pytest.MonkeyPatch):
    """Each test reassigns this fixture's _transport via the request fixture
    in the test function. Default is an unreachable transport that fails
    every request — tests must opt in to a working one."""
    state: dict[str, Any] = {
        "transport": _build_transport(None, raise_exc=httpx.ConnectError("no network")),
    }
    real_init = httpx.AsyncClient.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = state["transport"]
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", patched_init)
    return state


# ── GitHub shape ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_github_shape_strips_leading_v(patch_httpx) -> None:
    patch_httpx["transport"] = _build_transport(
        {"tag_name": "v1.4.2", "body": "Lots of fixes."}
    )
    result = await fetch_feed("https://example.test/feed")
    assert result.ok is True
    assert result.version == "1.4.2"
    assert result.changelog == "Lots of fixes."


@pytest.mark.asyncio
async def test_github_shape_without_v(patch_httpx) -> None:
    patch_httpx["transport"] = _build_transport(
        {"tag_name": "1.0.0", "body": ""}
    )
    result = await fetch_feed("https://example.test/feed")
    assert result.ok is True
    assert result.version == "1.0.0"
    # Empty body is fine — comes back as an empty string.
    assert result.changelog == ""


@pytest.mark.asyncio
async def test_github_shape_missing_tag(patch_httpx) -> None:
    patch_httpx["transport"] = _build_transport(
        {"tag_name": "", "body": "Body"}
    )
    result = await fetch_feed("https://example.test/feed")
    assert result.ok is False
    assert "tag_name" in (result.detail or "")


# ── Generic shape ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_generic_shape(patch_httpx) -> None:
    patch_httpx["transport"] = _build_transport(
        {"version": "2.0.0", "changelog": "Big rewrite."}
    )
    result = await fetch_feed("https://mirror.test/feed")
    assert result.ok is True
    assert result.version == "2.0.0"
    assert result.changelog == "Big rewrite."


@pytest.mark.asyncio
async def test_generic_shape_strips_leading_v(patch_httpx) -> None:
    patch_httpx["transport"] = _build_transport(
        {"version": "v2.0.0"}
    )
    result = await fetch_feed("https://mirror.test/feed")
    assert result.ok is True
    assert result.version == "2.0.0"
    assert result.changelog is None


@pytest.mark.asyncio
async def test_generic_shape_missing_version(patch_httpx) -> None:
    patch_httpx["transport"] = _build_transport(
        {"version": "", "changelog": "Nothing"}
    )
    result = await fetch_feed("https://mirror.test/feed")
    assert result.ok is False
    assert "version" in (result.detail or "")


# ── Error paths ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_http_error_status(patch_httpx) -> None:
    patch_httpx["transport"] = _build_transport(
        {"detail": "rate limited"}, status_code=403
    )
    result = await fetch_feed("https://example.test/feed")
    assert result.ok is False
    assert "403" in (result.detail or "")


@pytest.mark.asyncio
async def test_network_error(patch_httpx) -> None:
    patch_httpx["transport"] = _build_transport(
        None, raise_exc=httpx.ConnectError("dns gone")
    )
    result = await fetch_feed("https://nowhere.test/feed")
    assert result.ok is False
    assert "unreachable" in (result.detail or "").lower()


@pytest.mark.asyncio
async def test_non_json_body(patch_httpx) -> None:
    patch_httpx["transport"] = _build_transport("<html>404</html>")
    result = await fetch_feed("https://example.test/feed")
    assert result.ok is False
    assert "non-JSON" in (result.detail or "")


@pytest.mark.asyncio
async def test_array_root_rejected(patch_httpx) -> None:
    """A bare array root (some feeds list every release) isn't supported."""
    patch_httpx["transport"] = httpx.MockTransport(
        lambda req: httpx.Response(
            200,
            content=json.dumps([{"tag_name": "v1.0"}]).encode("utf-8"),
            headers={"content-type": "application/json"},
        )
    )
    result = await fetch_feed("https://example.test/feed")
    assert result.ok is False
    assert "object" in (result.detail or "")


@pytest.mark.asyncio
async def test_payload_with_neither_known_key(patch_httpx) -> None:
    patch_httpx["transport"] = _build_transport({"foo": "bar"})
    result = await fetch_feed("https://example.test/feed")
    assert result.ok is False
    # The fallback path is the generic normalizer, which reports
    # "missing 'version' or 'commit_sha'".
    detail = (result.detail or "").lower()
    assert "version" in detail or "commit_sha" in detail


# ── GitHub commits shape (v1.9.x default) ──────────────────────


@pytest.mark.asyncio
async def test_github_commits_shape(patch_httpx) -> None:
    """``/repos/<owner>/<repo>/commits/<branch>`` returns one commit
    with a ``sha`` + ``commit`` envelope. The normalizer pulls SHA,
    message, and committer date out."""
    patch_httpx["transport"] = _build_transport({
        "sha": "abc1234567890abcdef",
        "html_url": "https://github.com/x/y/commit/abc1234567890abcdef",
        "commit": {
            "message": "Fix scan worker double-count",
            "committer": {"date": "2026-05-18T10:30:45Z"},
            "author": {"date": "2026-05-18T10:30:45Z"},
        },
    })
    result = await fetch_feed("https://api.github.com/x/y/commits/main")
    assert result.ok is True
    assert result.commit_sha == "abc1234567890abcdef"
    assert result.commit_message == "Fix scan worker double-count"
    assert result.commit_date is not None
    assert result.commit_date.year == 2026
    assert result.commit_date.month == 5
    assert result.commit_date.day == 18
    # Version-tag fields stay empty so the service knows it's a
    # commit-mode result.
    assert result.version is None
    # The commit message is mirrored into ``changelog`` so the UI
    # "release notes" panel keeps working unchanged.
    assert result.changelog == "Fix scan worker double-count"


@pytest.mark.asyncio
async def test_github_commits_falls_back_to_author_date(patch_httpx) -> None:
    """A force-push can leave only the author's date populated. The
    normalizer must still produce a parseable timestamp."""
    patch_httpx["transport"] = _build_transport({
        "sha": "feedface",
        "commit": {
            "message": "Force-pushed fixup",
            "committer": None,
            "author": {"date": "2026-04-01T00:00:00Z"},
        },
    })
    result = await fetch_feed("https://api.github.com/x/y/commits/main")
    assert result.ok is True
    assert result.commit_sha == "feedface"
    assert result.commit_date is not None and result.commit_date.month == 4


@pytest.mark.asyncio
async def test_github_commits_missing_sha(patch_httpx) -> None:
    patch_httpx["transport"] = _build_transport({
        "sha": "",
        "commit": {"message": "x", "committer": {"date": "2026-05-01T00:00:00Z"}},
    })
    result = await fetch_feed("https://api.github.com/x/y/commits/main")
    # Empty SHA → shape detection falls through to generic, which
    # rejects too. The point: we don't pass empty SHAs back as ``ok=True``.
    assert result.ok is False


@pytest.mark.asyncio
async def test_generic_feed_can_carry_commit_metadata(patch_httpx) -> None:
    """Self-hosted mirrors can expose commit identity alongside the
    legacy ``version`` field — the generic normalizer surfaces both."""
    patch_httpx["transport"] = _build_transport({
        "version": "1.9.0",
        "changelog": "Big rewrite.",
        "commit_sha": "0123abcd",
        "commit_date": "2026-05-18T10:30:45Z",
        "commit_message": "Tagged 1.9.0",
    })
    result = await fetch_feed("https://mirror.test/feed")
    assert result.ok is True
    assert result.version == "1.9.0"
    assert result.commit_sha == "0123abcd"
    assert result.commit_date is not None
    assert result.commit_message == "Tagged 1.9.0"
