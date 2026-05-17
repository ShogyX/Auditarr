"""Per-secret test handlers (Stage 21).

A handler probes the upstream API with the supplied secret to
confirm it works, without exposing the secret to anything outside
this process. Returns ``(ok, detail)`` — the detail string is
surfaced in the UI ("API key works", "401 unauthorized", "rate
limited") so the operator gets a meaningful error.

Adding a new handler:

1. Write an ``async def _test_<key>(plaintext) -> tuple[bool, str]``
   function below.
2. Wire it into :data:`_HANDLERS`.
3. Add the matching ``test_handler`` string to the SecretSpec in
   :mod:`app.core.runtime_settings_schema`.

Handlers must:
* Never raise — they always return ``(bool, str)``.
* Not log the plaintext.
* Be cheap (sub-second). The endpoint runs synchronously in the
  request lifecycle.
"""

from __future__ import annotations

from typing import Awaitable, Callable

import httpx

from app.core.http import async_client
import structlog

log = structlog.get_logger(category="secrets")


async def _test_virustotal_api_key(plaintext: str) -> tuple[bool, str]:
    """Probe ``GET /users/me`` on the VirusTotal v3 API.

    A valid free-tier key returns 200 with a JSON document; an
    invalid key returns 401. We don't surface anything about the
    user object back to the caller — just the status code mapped to
    a friendly message. The plaintext is never logged.
    """
    url = "https://www.virustotal.com/api/v3/users/me"
    headers = {"x-apikey": plaintext, "Accept": "application/json"}
    try:
        async with async_client(timeout=10) as client:
            r = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        # Network failure, DNS, timeout — distinguishable from an
        # auth failure so the operator knows whether to blame their
        # network or their key.
        log.warning(
            "secret_test.virustotal.network", error=str(exc)
        )
        return False, f"Network error reaching VirusTotal: {exc}"

    if r.status_code == 200:
        return True, "Authenticated to VirusTotal."
    if r.status_code in (401, 403):
        return False, "VirusTotal rejected the API key (401/403)."
    if r.status_code == 429:
        return False, "VirusTotal rate-limited the test request."
    return False, f"Unexpected VirusTotal response: HTTP {r.status_code}"


_HANDLERS: dict[str, Callable[[str], Awaitable[tuple[bool, str]]]] = {
    "virustotal_api_key": _test_virustotal_api_key,
}


async def run_secret_test(key: str, plaintext: str) -> tuple[bool, str]:
    """Dispatch to the registered handler for ``key``.

    Returns ``(False, msg)`` rather than raising if no handler is
    registered, so the API can surface "no test available" without
    a 500.
    """
    handler = _HANDLERS.get(key)
    if handler is None:
        return False, f"No test handler is registered for {key!r}."
    return await handler(plaintext)
