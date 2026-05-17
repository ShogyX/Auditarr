"""VirusTotal integration plugin (Stage 10).

VirusTotal exposes a per-API-key REST surface at
``https://www.virustotal.com/api/v3``. Auditarr only uses the
free-tier ``GET /files/{hash}`` endpoint — we never upload
files (paid-tier capability and explicitly out of scope per the
plan).

This module is the Stage 10 home for the VT integration. Stage
19 audit follow-up shipped a thinner ``app/services/virustotal.py``
helper; Stage 10 moves the behaviour onto a proper plugin
backend so VT lives on the Integrations page rather than the
Plugins page (which is what the user asked for). The service-
layer helper continues to exist as the call site the scanner
uses, but its rate-limiting state is centralised in the new
:class:`_QuotaState` here so the operator-visible status
endpoint can report it.

What ships in this version (per plan §513-518 + addendum B.7):

* ``healthcheck`` — ``GET /users/me`` with the stored API
  key. ``status="ok"`` when VT accepts the key; ``"degraded"``
  when 401/403 (the operator's API key is wrong); ``"error"``
  on transport failure.
* ``lookup_by_hash`` — the actual VT lookup. Returns a small
  persistable result dict (the four severity counters + the
  permalink + a canonical ``vt_status`` string per addendum
  B.4), or ``None`` when quota is exhausted / 404 / transient
  error.
* **Three quota windows** (addendum B.7):
    - Per-minute: 4 lookups (VT free-tier ceiling).
    - Per-day:    500 lookups (default; operator-configurable
                  via ``daily_quota`` option).
    - Per-month:  15500 lookups (default; operator-configurable
                  via ``monthly_quota`` option).
  All three are enforced in ``_check_and_increment_quota``. The
  status endpoint surfaces remaining capacity in all three
  windows so the operator can see which limit they're closest
  to.
* **Canonical VT status strings** (addendum B.4): the plugin
  writes one of ``clean`` / ``malicious`` / ``suspicious`` /
  ``not_found`` / ``error`` to ``MediaFile.vt_status``. The
  built-in Stage 06 "VirusTotal non-clean" rule references
  these exact strings.
* **Bus events**: ``virustotal.result`` fired with the
  per-file outcome; ``virustotal.quota_exhausted`` fired (at
  most once per window) when any of the three quotas is hit.

Stage 11+ may layer on additional state (background polling
worker, retry-after honoring), but the contract here is
sufficient for plan §530 "Done when: the Stage 06 VT rule fires
on a fixture row."
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from app.core.http import async_client
from app.events.bus import EventBus
from app.events.types import DomainEvent
from app.integrations.types import (
    DiscoveredLibrary,
    HealthReport,
    IntegrationConfig,
    IntegrationProvider,
    TagSync,
)
from app.plugins import Plugin, PluginContext

_VT_BASE = "https://www.virustotal.com/api/v3"

# VT free-tier ceilings (addendum B.7). These are the *upper
# bound* — operators on the free tier can set their own
# ``daily_quota`` / ``monthly_quota`` lower than this if they
# share the key with another tool. The per-minute limit is a
# hard physical limit from VT's side; we don't expose an option
# for raising it because doing so would just trigger 429s.
VT_MINUTE_CEILING = 4
VT_DAILY_CEILING_DEFAULT = 500
VT_MONTHLY_CEILING_DEFAULT = 15500


# ── Canonical VT status strings (addendum B.4) ───────────────────
VT_STATUS_CLEAN = "clean"
VT_STATUS_MALICIOUS = "malicious"
VT_STATUS_SUSPICIOUS = "suspicious"
VT_STATUS_NOT_FOUND = "not_found"
VT_STATUS_ERROR = "error"


@dataclass
class _QuotaState:
    """Three-window VT submission counter (addendum B.7).

    Per-minute / per-day / per-month all reset independently:
    * ``minute_counter`` resets every 60s based on
      ``minute_window_started``.
    * ``day_counter`` resets at UTC midnight.
    * ``month_counter`` resets on the 1st of each UTC month.

    All counters share the same lock so a burst submission
    can't race past two windows simultaneously.

    The instance is module-level (process-wide) per plan §514
    "rate-limiting state stays as a process-wide singleton".
    The Stage 10 ``reset_quota_for_tests`` helper + the
    addendum-C.5 autouse fixture in conftest keep state from
    leaking across tests.
    """

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    minute_counter: int = 0
    minute_window_started: datetime = field(
        default_factory=lambda: datetime.now(UTC)
    )

    day_counter: int = 0
    day_window: date = field(default_factory=lambda: datetime.now(UTC).date())

    month_counter: int = 0
    month_window: tuple[int, int] = field(
        default_factory=lambda: (
            datetime.now(UTC).year,
            datetime.now(UTC).month,
        )
    )

    # Operator-visible last-check timestamp; surfaced on the
    # status endpoint.
    last_check_at: datetime | None = None

    # Track whether we've already emitted ``quota_exhausted``
    # for this window so the bus doesn't spam.
    minute_quota_alerted: bool = False
    day_quota_alerted: bool = False
    month_quota_alerted: bool = False

    def remaining(
        self,
        *,
        minute_cap: int,
        daily_cap: int,
        monthly_cap: int,
    ) -> dict[str, int]:
        """Snapshot of remaining capacity in each window."""
        return {
            "minute": max(0, minute_cap - self.minute_counter),
            "day": max(0, daily_cap - self.day_counter),
            "month": max(0, monthly_cap - self.month_counter),
        }


_quota = _QuotaState()


def _rotate_windows(state: _QuotaState, now: datetime) -> None:
    """Advance window counters that have rolled over.

    Called inside the lock so window rotation is atomic with the
    increment check. ``minute_quota_alerted`` etc. reset on
    rotation so the next exhaustion event fires fresh.
    """
    # Minute window: roll over once a full 60s has elapsed since
    # the current window opened.
    if now - state.minute_window_started >= timedelta(seconds=60):
        state.minute_window_started = now
        state.minute_counter = 0
        state.minute_quota_alerted = False

    # Day window: roll over at UTC midnight.
    today = now.date()
    if state.day_window != today:
        state.day_window = today
        state.day_counter = 0
        state.day_quota_alerted = False

    # Month window: roll over on the 1st of each UTC month.
    this_month = (now.year, now.month)
    if state.month_window != this_month:
        state.month_window = this_month
        state.month_counter = 0
        state.month_quota_alerted = False


async def _check_and_increment_quota(
    *,
    minute_cap: int,
    daily_cap: int,
    monthly_cap: int,
    event_bus: EventBus | None = None,
) -> tuple[bool, str | None]:
    """Atomically check whether we may spend one VT lookup
    against any window that's still under cap.

    Returns ``(True, None)`` when the caller may proceed.
    Returns ``(False, window_name)`` when at least one window
    is exhausted; ``window_name`` is ``"minute"``, ``"day"``,
    or ``"month"`` so the caller can surface which limit hit.

    When a window is freshly exhausted (the first call that
    hits the cap), fires the ``virustotal.quota_exhausted``
    event on the bus. Subsequent calls within the same window
    don't re-fire — operators only need one notification per
    window.
    """
    now = datetime.now(UTC)
    async with _quota.lock:
        _rotate_windows(_quota, now)

        # Each window enforced in turn — the cheapest cap (per-
        # minute) checked first so we fail fast on burst traffic.
        if _quota.minute_counter >= minute_cap:
            window = "minute"
            if not _quota.minute_quota_alerted:
                _quota.minute_quota_alerted = True
                if event_bus is not None:
                    await event_bus.publish(
                        DomainEvent(
                            name="virustotal.quota_exhausted",
                            source="virustotal",
                            payload={
                                "window": window,
                                "cap": minute_cap,
                            },
                        )
                    )
            return False, window
        if _quota.day_counter >= daily_cap:
            window = "day"
            if not _quota.day_quota_alerted:
                _quota.day_quota_alerted = True
                if event_bus is not None:
                    await event_bus.publish(
                        DomainEvent(
                            name="virustotal.quota_exhausted",
                            source="virustotal",
                            payload={
                                "window": window,
                                "cap": daily_cap,
                            },
                        )
                    )
            return False, window
        if _quota.month_counter >= monthly_cap:
            window = "month"
            if not _quota.month_quota_alerted:
                _quota.month_quota_alerted = True
                if event_bus is not None:
                    await event_bus.publish(
                        DomainEvent(
                            name="virustotal.quota_exhausted",
                            source="virustotal",
                            payload={
                                "window": window,
                                "cap": monthly_cap,
                            },
                        )
                    )
            return False, window

        _quota.minute_counter += 1
        _quota.day_counter += 1
        _quota.month_counter += 1
        _quota.last_check_at = now
        return True, None


def quota_snapshot(
    *,
    minute_cap: int = VT_MINUTE_CEILING,
    daily_cap: int = VT_DAILY_CEILING_DEFAULT,
    monthly_cap: int = VT_MONTHLY_CEILING_DEFAULT,
) -> dict[str, Any]:
    """Read-only snapshot of the quota state. Used by the
    status endpoint to surface usage to the operator without
    needing async coordination."""
    return {
        "minute_used": _quota.minute_counter,
        "minute_cap": minute_cap,
        "minute_remaining": max(0, minute_cap - _quota.minute_counter),
        "day_used": _quota.day_counter,
        "day_cap": daily_cap,
        "day_remaining": max(0, daily_cap - _quota.day_counter),
        "month_used": _quota.month_counter,
        "month_cap": monthly_cap,
        "month_remaining": max(0, monthly_cap - _quota.month_counter),
        "last_check_at": (
            _quota.last_check_at.isoformat()
            if _quota.last_check_at is not None
            else None
        ),
    }


def reset_quota_for_tests() -> None:
    """Test-only helper called by the autouse fixture in
    conftest (addendum C.5). Zeroes every window counter +
    last_check_at + the per-window alert flags. NOT exported
    via __all__ — tests reach for it explicitly."""
    now = datetime.now(UTC)
    _quota.minute_counter = 0
    _quota.minute_window_started = now
    _quota.minute_quota_alerted = False
    _quota.day_counter = 0
    _quota.day_window = now.date()
    _quota.day_quota_alerted = False
    _quota.month_counter = 0
    _quota.month_window = (now.year, now.month)
    _quota.month_quota_alerted = False
    _quota.last_check_at = None


# ── Lookup logic ─────────────────────────────────────────────────


def _classify_status(stats: dict[str, Any]) -> str:
    """Map VT's ``last_analysis_stats`` block to one of the
    canonical :data:`VT_STATUS_VALUES`.

    Order matters: a non-zero ``malicious`` count wins over
    ``suspicious``, which wins over the "all clean" case. The
    addendum-B.4 column ``vt_status`` is what the rule engine
    filters on, so this mapping is contract.
    """
    malicious = int(stats.get("malicious", 0))
    suspicious = int(stats.get("suspicious", 0))
    if malicious > 0:
        return VT_STATUS_MALICIOUS
    if suspicious > 0:
        return VT_STATUS_SUSPICIOUS
    # All-clean: at least one engine returned a result and none
    # raised a flag. We treat the all-undetected case as clean
    # too — VT's vocabulary is "engines that scanned but didn't
    # find anything bad", which for our purposes is fine.
    return VT_STATUS_CLEAN


async def lookup_by_hash(
    *,
    api_key: str,
    sha256: str,
    daily_quota: int = VT_DAILY_CEILING_DEFAULT,
    monthly_quota: int = VT_MONTHLY_CEILING_DEFAULT,
    timeout: float = 10.0,
    event_bus: EventBus | None = None,
) -> dict[str, Any] | None:
    """Look up a hash on VirusTotal.

    Returns a small persistable result dict with the canonical
    ``vt_status`` string (addendum B.4) PLUS the four
    severity-style counters for the Files page, or ``None`` if
    nothing should be persisted (quota exhausted, 404 → returns
    a not_found dict instead, or transient error).

    Fires ``virustotal.result`` on the event bus with the
    outcome so the rule engine + audit log can react.
    """
    if not api_key or not sha256:
        return None
    allowed, exhausted_window = await _check_and_increment_quota(
        minute_cap=VT_MINUTE_CEILING,
        daily_cap=daily_quota,
        monthly_cap=monthly_quota,
        event_bus=event_bus,
    )
    if not allowed:
        return None

    url = f"{_VT_BASE}/files/{sha256}"
    headers = {"x-apikey": api_key, "accept": "application/json"}
    try:
        async with async_client(timeout=timeout) as client:
            response = await client.get(url, headers=headers)
    except httpx.HTTPError:
        return None

    now_iso = datetime.now(UTC).isoformat()

    if response.status_code == 404:
        result = {
            "vt_status": VT_STATUS_NOT_FOUND,
            "status": "not_found",
            "checked_at": now_iso,
            "permalink": f"https://www.virustotal.com/gui/file/{sha256}",
        }
        if event_bus is not None:
            await event_bus.publish(
                DomainEvent(
                    name="virustotal.result",
                    source="virustotal",
                    payload={
                        "sha256": sha256,
                        "vt_status": VT_STATUS_NOT_FOUND,
                    },
                )
            )
        return result

    if response.status_code in (401, 403):
        # Auth rejected — the operator's API key is wrong.
        # Don't persist an "error" status because that would
        # poison the rule engine for every file; the operator
        # sees this via the healthcheck endpoint instead.
        return None
    if response.status_code == 429:
        # VT applied its own rate limit. Don't persist; let the
        # caller retry later. The 429 is independent from our
        # internal quota state (it can fire even when our
        # counters say there's room — VT's server-side count
        # may differ slightly).
        return None
    if response.status_code >= 400:
        return None

    try:
        body = response.json()
    except ValueError:
        return None

    attributes = (body.get("data") or {}).get("attributes") or {}
    stats = attributes.get("last_analysis_stats") or {}
    vt_status = _classify_status(stats)

    result = {
        "vt_status": vt_status,
        "status": "ok",
        "malicious": int(stats.get("malicious", 0)),
        "suspicious": int(stats.get("suspicious", 0)),
        "harmless": int(stats.get("harmless", 0)),
        "undetected": int(stats.get("undetected", 0)),
        "permalink": f"https://www.virustotal.com/gui/file/{sha256}",
        "checked_at": now_iso,
    }
    if event_bus is not None:
        await event_bus.publish(
            DomainEvent(
                name="virustotal.result",
                source="virustotal",
                payload={
                    "sha256": sha256,
                    "vt_status": vt_status,
                    "malicious": result["malicious"],
                    "suspicious": result["suspicious"],
                },
            )
        )
    return result


# ── Plugin provider ──────────────────────────────────────────────


class VirusTotalProvider(IntegrationProvider):
    """VT plugin integration surface.

    Distinct from the Sonarr/Plex/Tdarr providers in two ways:

    1. There's no ``base_url`` — VT lives at a fixed endpoint.
       The config schema only collects the API key (a secret)
       and the operator-configurable quota ceilings.
    2. ``discover_libraries`` returns ``[]`` — VT isn't a
       media library, just a lookup surface. The integration
       still slots into the same provider machinery so the
       Integrations page renders + manages it uniformly.
    """

    kind = "virustotal"
    label = "VirusTotal"
    config_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "daily_quota": {
                "type": "integer",
                "title": "Daily quota",
                "description": (
                    "Maximum lookups per UTC day. Free-tier ceiling is 500; "
                    "lower this if the API key is shared with another tool."
                ),
                "default": VT_DAILY_CEILING_DEFAULT,
                "minimum": 1,
                "maximum": VT_DAILY_CEILING_DEFAULT,
            },
            "monthly_quota": {
                "type": "integer",
                "title": "Monthly quota",
                "description": (
                    "Maximum lookups per UTC month. Free-tier ceiling is 15500."
                ),
                "default": VT_MONTHLY_CEILING_DEFAULT,
                "minimum": 1,
                "maximum": VT_MONTHLY_CEILING_DEFAULT,
            },
            "timeout_seconds": {
                "type": "integer",
                "title": "Lookup timeout (s)",
                "default": 10,
                "minimum": 1,
                "maximum": 60,
            },
        },
    }
    secret_fields: tuple[str, ...] = ("api_key",)

    def __init__(self, log: Any) -> None:
        self._log = log

    async def healthcheck(self, config: IntegrationConfig) -> HealthReport:
        """``GET /users/me`` against VT to verify the key.

        VirusTotal's v3 API exposes ``/users/me`` as an
        authenticated probe that returns the API key owner's
        user record (including quota information). We use it
        strictly as a key-validity check.

        v1.7.2 bug fix: the previous version hit
        ``/users/<self>`` literally — the angle-bracket
        placeholder from the docstring leaked into the
        f-string. VT returns 404 for that URL because there's
        no user with that literal name, which is exactly what
        the operator was seeing in the integration health
        panel.
        """
        api_key = str(config.secrets.get("api_key", "")).strip()
        if not api_key:
            return HealthReport(
                status="error",
                detail="VirusTotal API key is not configured.",
            )
        timeout = float(config.options.get("timeout_seconds", 10))
        try:
            async with async_client(timeout=timeout) as client:
                response = await client.get(
                    f"{_VT_BASE}/users/me",
                    headers={"x-apikey": api_key, "accept": "application/json"},
                )
        except httpx.HTTPError as exc:
            return HealthReport(
                status="error",
                detail=f"VirusTotal unreachable: {exc!s}",
            )
        if response.status_code in (401, 403):
            return HealthReport(
                status="degraded",
                detail=(
                    "VirusTotal rejected the API key. Generate a fresh "
                    "key at https://www.virustotal.com/gui/my-apikey and "
                    "re-save the integration."
                ),
            )
        if response.status_code >= 400:
            return HealthReport(
                status="degraded",
                detail=(
                    f"VirusTotal returned HTTP {response.status_code} on "
                    "the /users/me probe."
                ),
            )
        return HealthReport(
            status="ok",
            detail="VirusTotal API key accepted.",
        )

    async def discover_libraries(
        self, _config: IntegrationConfig
    ) -> list[DiscoveredLibrary]:
        # VT isn't a library source — return [] so the Library
        # auto-snapshot in Stage 17 doesn't write phantom rows.
        return []

    async def sync_tags(self, _config: IntegrationConfig) -> list[TagSync]:
        return []


def register(context: PluginContext) -> Plugin:
    """Register the VT plugin with the Auditarr loader.

    Mirrors the pattern in :mod:`backend.plugins.tdarr.backend`
    et al. — one provider, no routes, no settings page.
    """
    provider = VirusTotalProvider(log=context.logger())
    context.register_integration(provider)
    # v1.7.2: ``Plugin(id=..., version=...)`` was wrong — the base
    # class takes only ``context``. The id/version were already
    # declared at the class level via ``plugin.yaml``, so the
    # kwargs were redundant AND a TypeError. The bug shipped
    # because no test exercised the registration path end-to-end.
    return Plugin(context)


# ── Scanner enqueue helper (plan §515) ───────────────────────────


async def enqueue_for_vt_lookup(
    session: Any,
    *,
    media_file_id: str,
) -> bool:
    """Insert a row into ``vt_queue`` for the given media file.

    Plan §515: "when VT integration is enabled, the scanner
    enqueues files for VT lookup." The caller is responsible
    for the enablement check (i.e. for the "is there an
    enabled VT integration?" query). Keeping that check
    outside this helper keeps the helper trivially testable
    and avoids per-file SQL for the enablement state.

    Idempotent: inserts ON CONFLICT DO NOTHING so the same
    file can be enqueued multiple times across re-scans
    without violating the (media_file_id PK) constraint. The
    helper commits the insertion when it succeeds.

    Returns ``True`` when a new row was inserted, ``False``
    when the file was already in the queue or insertion
    failed (e.g. the media_file_id doesn't exist — FK
    violation). The bool is informational for the caller's
    metrics; failures don't raise.
    """
    from sqlalchemy import insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from sqlalchemy.exc import IntegrityError

    from app.models.vt_queue import VtQueueItem
    from app.utils.datetime import utcnow

    now = utcnow()

    # Prefer the dialect-aware ON CONFLICT pattern for SQLite;
    # fall through to a try/except on plain INSERT for other
    # backends. This keeps the helper portable while staying
    # idiomatic on the development/CI database.
    try:
        stmt = (
            sqlite_insert(VtQueueItem)
            .values(
                media_file_id=media_file_id,
                enqueued_at=now,
                attempt_count=0,
            )
            .on_conflict_do_nothing(index_elements=["media_file_id"])
        )
        result = await session.execute(stmt)
        await session.commit()
        # ``rowcount`` is 1 on insert, 0 on conflict skip.
        return bool(result.rowcount)
    except IntegrityError:
        # FK violation (media_file_id not present) or some
        # other constraint failure. Roll back so the session
        # is reusable; the caller's flow continues.
        await session.rollback()
        return False
    except Exception:  # noqa: BLE001
        # Generic fallback for non-SQLite dialects without
        # ON CONFLICT support — try a plain insert and let the
        # IntegrityError handler skip duplicates.
        await session.rollback()
        try:
            await session.execute(
                insert(VtQueueItem).values(
                    media_file_id=media_file_id,
                    enqueued_at=now,
                    attempt_count=0,
                )
            )
            await session.commit()
            return True
        except IntegrityError:
            await session.rollback()
            return False


# ── Drain worker (Stage 10 — completes plan §515 contract) ──────


#: Stop retrying a row after this many failed attempts. Keeps
#: the queue from churning forever on persistently unfetchable
#: hashes. The row stays in the queue so an operator can see it
#: + manually clear if needed; a future stage may add a UI
#: "give up" affordance.
VT_DRAIN_MAX_ATTEMPTS = 5

#: How many rows the drain pass pulls per invocation. Keeps
#: each tick bounded — at 4 lookups/minute the cap, draining
#: 20 rows fits comfortably inside the 5-minute window the
#: scheduler runs the job on. Operators on the paid tier with
#: a higher minute ceiling can raise this via the job arg.
VT_DRAIN_DEFAULT_BATCH_SIZE = 20


async def drain_vt_queue(
    session: Any,
    *,
    integration_id: str,
    api_key: str,
    daily_quota: int = VT_DAILY_CEILING_DEFAULT,
    monthly_quota: int = VT_MONTHLY_CEILING_DEFAULT,
    timeout: float = 10.0,
    batch_size: int = VT_DRAIN_DEFAULT_BATCH_SIZE,
    event_bus: Any = None,
) -> dict[str, int]:
    """Drain up to ``batch_size`` entries from ``vt_queue``.

    For each queued row:
      1. Load the :class:`MediaFile`. If missing (the file was
         deleted), drop the queue row and continue.
      2. If the file has no ``hash_sha256``, drop the queue
         row — there's nothing to look up. This shouldn't
         happen in practice because the scanner only enqueues
         hashed files, but defending here keeps the worker
         robust against schema drift.
      3. Call :func:`lookup_by_hash` with the integration's
         API key. The plugin's quota state gates the call;
         the call returns ``None`` when quota is exhausted.
      4. On ``None`` (quota exhausted, 401/403, 429, 5xx,
         transport error): bump ``attempt_count`` and update
         ``last_attempted_at``. If ``attempt_count`` reaches
         :data:`VT_DRAIN_MAX_ATTEMPTS`, drop the row so the
         queue doesn't churn forever on a persistently
         unfetchable hash.
      5. On a dict result (success, including ``not_found``):
         write ``vt_status`` + ``virustotal_result`` +
         ``virustotal_checked_at`` onto the MediaFile, then
         delete the queue row.

    Returns a counters dict so the job framework can surface
    a summary on the JobRun row.

    Side-effects via :func:`lookup_by_hash`: fires
    ``virustotal.result`` per lookup and
    ``virustotal.quota_exhausted`` once per window. Audit-log
    integration for VT-driven severity escalations to ``crit``
    (addendum A.5) happens downstream in the rule engine when
    the engine re-runs and observes the updated
    ``vt_status``.
    """
    from sqlalchemy import select
    from sqlalchemy import delete as sql_delete

    from app.models.media import MediaFile
    from app.models.vt_queue import VtQueueItem
    from app.utils.datetime import utcnow

    counters = {
        "examined": 0,
        "looked_up": 0,
        "persisted": 0,
        "skipped_missing_file": 0,
        "skipped_missing_hash": 0,
        "skipped_quota_exhausted": 0,
        "attempts_incremented": 0,
        "rows_abandoned_max_attempts": 0,
        "rows_deleted_after_lookup": 0,
    }

    # FIFO drain — oldest entries first via the
    # ix_vt_queue_enqueued_at index.
    queue_rows = (
        await session.execute(
            select(VtQueueItem)
            .order_by(VtQueueItem.enqueued_at.asc())
            .limit(batch_size)
        )
    ).scalars().all()

    for q in queue_rows:
        counters["examined"] += 1

        media = await session.get(MediaFile, q.media_file_id)
        if media is None:
            # The file's been deleted; drop the orphan queue
            # row. CASCADE should have handled this but a
            # defensive drop costs nothing.
            counters["skipped_missing_file"] += 1
            await session.execute(
                sql_delete(VtQueueItem).where(
                    VtQueueItem.media_file_id == q.media_file_id
                )
            )
            continue

        if not media.hash_sha256:
            counters["skipped_missing_hash"] += 1
            await session.execute(
                sql_delete(VtQueueItem).where(
                    VtQueueItem.media_file_id == q.media_file_id
                )
            )
            continue

        counters["looked_up"] += 1
        result = await lookup_by_hash(
            api_key=api_key,
            sha256=media.hash_sha256,
            daily_quota=daily_quota,
            monthly_quota=monthly_quota,
            timeout=timeout,
            event_bus=event_bus,
        )

        if result is None:
            # Transient error / quota exhausted / auth error.
            # Bump the attempt counter and let the next drain
            # tick try again — UNLESS we've crossed the
            # max-attempts ceiling, in which case drop the
            # row so the queue doesn't churn forever.
            q.attempt_count = (q.attempt_count or 0) + 1
            q.last_attempted_at = utcnow()
            counters["attempts_incremented"] += 1
            if q.attempt_count >= VT_DRAIN_MAX_ATTEMPTS:
                counters["rows_abandoned_max_attempts"] += 1
                await session.execute(
                    sql_delete(VtQueueItem).where(
                        VtQueueItem.media_file_id == q.media_file_id
                    )
                )
            else:
                counters["skipped_quota_exhausted"] += 1
            continue

        # Success — persist the result onto the MediaFile.
        # ``vt_status`` is the canonical addendum-B.4 string
        # the rule engine reads. ``virustotal_result`` keeps
        # the full payload for the Files page detail view.
        media.vt_status = result.get("vt_status")
        media.virustotal_result = result
        media.virustotal_checked_at = utcnow()
        counters["persisted"] += 1

        await session.execute(
            sql_delete(VtQueueItem).where(
                VtQueueItem.media_file_id == q.media_file_id
            )
        )
        counters["rows_deleted_after_lookup"] += 1

    await session.commit()
    return counters
