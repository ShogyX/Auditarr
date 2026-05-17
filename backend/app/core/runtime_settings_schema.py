"""Runtime settings validation schema (Stage 21).

The safety contract for runtime-editable settings. Two principles:

1. **Whitelist, never blacklist.** A key must appear in
   :data:`RUNTIME_EDITABLE` to be editable at all. Anything not on
   the list is restart-required; the API rejects writes with a 422
   that names the env var the operator should edit instead.

2. **Validate before write.** Every accepted value runs through
   :func:`validate_runtime_setting`, which builds a per-key pydantic
   model with the type + constraints pinned in the schema entry. The
   DB row is only written after validation succeeds.

The same module describes the editable surface for the UI
(:func:`describe_runtime_settings`) and the secret surface
(:data:`SECRETS`). Both share a category vocabulary so the front-end
can group them coherently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, create_model

Category = Literal[
    "logging",
    "auth",
    "rate_limiting",
    "scanner",
    "updater",
    "plugins",
    "housekeeping",
    "webhooks",
    "integrations",
    # Stage 4 (audit follow-up): dashboard surface-level tunables.
    # Currently a single field — the issues-open severity threshold —
    # but other dashboard knobs (default sparkline window, suggested
    # tile order) are reasonable future additions under the same
    # category banner.
    "dashboard",
    # Stage 07 (v1.7): optimization-worker tunables. Today the only
    # setting under this category is the in-process runner
    # kill-switch (per plan §401); Stage 08 / Stage 13 may add a
    # concurrency limit + queue-pause toggle here.
    "optimization",
]


@dataclass(frozen=True, slots=True)
class RuntimeFieldSpec:
    """Metadata pinning one runtime-editable field."""

    key: str
    label: str
    description: str
    category: Category
    field_type: type
    field_default: Any
    field_constraints: dict[str, Any]
    # ``immediate`` — the next request sees the new value.
    # ``next_tick`` — a background scheduler picks it up on its
    #                 next interval (could be up to N minutes).
    impact: Literal["immediate", "next_tick"]
    requires_warning: str | None = None
    # ── Stage 2 metadata extensions ──────────────────────────────
    # ``group`` — optional sub-category within a category. The UI
    # uses it to group fields inside a category section. ``None``
    # means "no sub-grouping" — the field renders at the top level
    # of its category.
    group: str | None = None
    # ``sensitivity`` — gating signal for the UI. ``"normal"`` fields
    # render with the standard form chrome. ``"elevated"`` fields
    # display an extra confirmation step (a la "type the field name
    # to confirm") because the consequences of an accidental change
    # are operationally costly. Today no regular spec is elevated;
    # secrets always render with the elevated treatment regardless.
    sensitivity: Literal["normal", "elevated"] = "normal"
    # ``restart_required`` — reserved. When ``True``, the runtime
    # override row is accepted but the new value only takes effect
    # after the next process restart. Useful for fields where a
    # mid-flight change would corrupt in-flight state. No current
    # entry sets this to ``True``; the field exists so the
    # describe-payload contract is forward-compatible.
    restart_required: bool = False


RUNTIME_EDITABLE: tuple[RuntimeFieldSpec, ...] = (
    # ── Logging ───────────────────────────────────────────────
    RuntimeFieldSpec(
        key="log_level",
        label="Log level",
        description=(
            "Minimum log severity emitted by the API and worker. "
            "DEBUG is verbose enough to slow down production a touch "
            "and noisy in journalctl; use it only for short debugging "
            "windows."
        ),
        category="logging",
        field_type=str,
        field_default="info",
        field_constraints={"pattern": r"^(debug|info|warning|error|critical)$"},
        impact="immediate",
        requires_warning=(
            "Setting this to DEBUG will produce a lot of journal noise "
            "and may slightly slow down request handling."
        ),
    ),
    # ── Auth ──────────────────────────────────────────────────
    RuntimeFieldSpec(
        key="access_token_ttl_minutes",
        label="Access token TTL (minutes)",
        description=(
            "How long an access token stays valid after issue. Shorter "
            "windows tighten the blast radius of a leaked token but "
            "force more refresh round-trips."
        ),
        category="auth",
        field_type=int,
        field_default=30,
        field_constraints={"ge": 1, "le": 1440},
        impact="immediate",
        group="tokens",
    ),
    RuntimeFieldSpec(
        key="refresh_token_ttl_days",
        label="Refresh token TTL (days)",
        description=(
            "How long a refresh token stays valid after issue. After "
            "this window users are forced to log in again."
        ),
        category="auth",
        field_type=int,
        field_default=14,
        field_constraints={"ge": 1, "le": 90},
        impact="immediate",
        group="tokens",
    ),
    RuntimeFieldSpec(
        key="ws_require_auth",
        label="Require auth on WebSocket upgrade",
        description=(
            "When enabled, WebSocket clients must present a valid "
            "access JWT in the ?token= query param. Disabling this is "
            "a debugging-only override; never disable in production."
        ),
        category="auth",
        field_type=bool,
        field_default=True,
        field_constraints={},
        impact="immediate",
        requires_warning=(
            "Disabling WebSocket auth makes the live-event stream open "
            "to anyone who can reach the server. Only do this in a "
            "trusted local environment."
        ),
        group="websocket",
    ),
    # ── Rate limiting ─────────────────────────────────────────
    RuntimeFieldSpec(
        key="auth_rate_limit_attempts",
        label="Login attempts per window",
        description=(
            "Maximum failed login attempts allowed per IP within the "
            "rate-limit window. Successful logins do not count."
        ),
        category="rate_limiting",
        field_type=int,
        field_default=10,
        field_constraints={"ge": 1, "le": 1000},
        impact="immediate",
        group="login",
    ),
    RuntimeFieldSpec(
        key="auth_rate_limit_window_seconds",
        label="Rate-limit window (seconds)",
        description="Width of the sliding window the login rate limit uses.",
        category="rate_limiting",
        field_type=int,
        field_default=300,
        field_constraints={"ge": 10, "le": 86400},
        impact="immediate",
        group="login",
    ),
    # ── Scanner ───────────────────────────────────────────────
    RuntimeFieldSpec(
        key="scanner_ffprobe_timeout_seconds",
        label="ffprobe timeout (seconds)",
        description=(
            "Hard ceiling on per-file ffprobe runtime. ffprobe "
            "occasionally hangs on malformed or DRM-tied containers; "
            "this kills it and marks the file unscannable."
        ),
        category="scanner",
        field_type=int,
        field_default=30,
        field_constraints={"ge": 1, "le": 600},
        impact="next_tick",
    ),
    RuntimeFieldSpec(
        key="scanner_worker_concurrency",
        label="Scanner worker concurrency",
        description=(
            "Number of files the scanner processes in parallel. "
            "Higher values use more CPU and IO. Values above 8 rarely "
            "pay off on consumer storage."
        ),
        category="scanner",
        field_type=int,
        field_default=4,
        field_constraints={"ge": 1, "le": 32},
        impact="next_tick",
        requires_warning=(
            "Increasing this may saturate slow storage. Monitor disk "
            "throughput after raising."
        ),
    ),
    RuntimeFieldSpec(
        key="scanner_max_file_size_mb",
        label="Scanner max file size (MB)",
        description=(
            "Skip files larger than this when scanning. Default 50 GB "
            "covers remuxes; raise for archival 8K masters."
        ),
        category="scanner",
        field_type=int,
        field_default=51200,
        field_constraints={"ge": 1, "le": 1_048_576},
        impact="next_tick",
    ),
    # ── Updater ───────────────────────────────────────────────
    RuntimeFieldSpec(
        key="update_feed_url",
        label="Update feed URL",
        description=(
            "JSON endpoint the updater hits to learn about new "
            "releases."
        ),
        category="updater",
        field_type=str,
        field_default="https://api.github.com/repos/ShogyX/Auditarr/releases/latest",
        field_constraints={
            "min_length": 1,
            "max_length": 1024,
            "pattern": r"^https?://",
        },
        impact="next_tick",
    ),
    RuntimeFieldSpec(
        key="update_check_interval_minutes",
        label="Update check interval (minutes)",
        description="How often the background scheduler hits the feed.",
        category="updater",
        field_type=int,
        field_default=60,
        field_constraints={"ge": 5, "le": 1440},
        impact="next_tick",
    ),
    RuntimeFieldSpec(
        key="update_install_mode",
        label="Install mode",
        description=(
            "Override the auto-detected install environment. ``auto`` "
            "lets the backend detect Docker vs bare-metal at startup."
        ),
        category="updater",
        field_type=str,
        field_default="auto",
        field_constraints={"pattern": r"^(auto|docker|bare-metal|unmanaged)$"},
        impact="immediate",
        requires_warning=(
            "Setting the wrong install mode routes apply requests to "
            "the wrong helper. Use 'auto' unless you have a specific "
            "reason to override."
        ),
    ),
    # ── Plugins ───────────────────────────────────────────────
    RuntimeFieldSpec(
        key="plugin_gallery_url",
        label="Plugin gallery URL",
        description=(
            "JSON manifest URL listing community plugins. Empty "
            "string disables the gallery UI."
        ),
        category="plugins",
        field_type=str,
        field_default="https://raw.githubusercontent.com/auditarr/plugins/main/gallery.json",
        field_constraints={"max_length": 1024},
        impact="next_tick",
    ),
    # ── Housekeeping ──────────────────────────────────────────
    RuntimeFieldSpec(
        key="housekeeping_delivery_retention_days",
        label="Notification delivery retention (days)",
        description="How long to keep notification delivery rows. 0 = keep forever.",
        category="housekeeping",
        field_type=int,
        field_default=30,
        field_constraints={"ge": 0, "le": 3650},
        impact="next_tick",
        group="retention",
    ),
    RuntimeFieldSpec(
        key="housekeeping_update_check_retention_days",
        label="Update-feed check retention (days)",
        description="How long to keep update-feed-check rows. 0 = keep forever.",
        category="housekeeping",
        field_type=int,
        field_default=90,
        field_constraints={"ge": 0, "le": 3650},
        impact="next_tick",
        group="retention",
    ),
    RuntimeFieldSpec(
        key="housekeeping_rule_evaluation_retention_days",
        label="Rule evaluation retention (days)",
        description=(
            "How long to keep rule-evaluation rows. 0 = keep forever. "
            "Grows with library size × rule count; trim aggressively "
            "on large libraries."
        ),
        category="housekeeping",
        field_type=int,
        field_default=0,
        field_constraints={"ge": 0, "le": 3650},
        impact="next_tick",
        group="retention",
    ),
    RuntimeFieldSpec(
        key="housekeeping_job_run_retention_days",
        label="Job run retention (days)",
        description="How long to keep background job-run rows. 0 = keep forever.",
        category="housekeeping",
        field_type=int,
        field_default=60,
        field_constraints={"ge": 0, "le": 3650},
        impact="next_tick",
        group="retention",
    ),
    # ── Webhooks ──────────────────────────────────────────────
    RuntimeFieldSpec(
        key="notifications_webhook_default_timeout_seconds",
        label="Webhook default timeout (seconds)",
        description=(
            "HTTP timeout used when no per-channel override is set."
        ),
        category="webhooks",
        field_type=int,
        field_default=10,
        field_constraints={"ge": 1, "le": 120},
        impact="immediate",
    ),
    RuntimeFieldSpec(
        key="notifications_webhook_max_retries",
        label="Webhook max retries",
        description=(
            "How many times to retry a failed webhook before giving "
            "up. Each retry waits with exponential backoff."
        ),
        category="webhooks",
        field_type=int,
        field_default=3,
        field_constraints={"ge": 0, "le": 10},
        impact="immediate",
    ),
    # ── VirusTotal toggles ────────────────────────────────────
    RuntimeFieldSpec(
        key="virustotal_enabled",
        label="VirusTotal integration enabled",
        description=(
            "Master switch for the VirusTotal integration. When off, "
            "no submissions or lookups happen regardless of the API "
            "key state."
        ),
        category="integrations",
        field_type=bool,
        field_default=False,
        field_constraints={},
        impact="immediate",
        group="virustotal",
    ),
    RuntimeFieldSpec(
        key="virustotal_scan_on_import",
        label="Submit new files on import",
        description=(
            "Submit a file's SHA256 hash for lookup as soon as it's "
            "imported. When off, files only get checked if requested "
            "explicitly via the API."
        ),
        category="integrations",
        field_type=bool,
        field_default=False,
        field_constraints={},
        impact="immediate",
        group="virustotal",
    ),
    RuntimeFieldSpec(
        key="virustotal_rescan_interval_days",
        label="Rescan interval (days)",
        description=(
            "Re-check a file's hash if the last verdict is older than "
            "this. 0 disables rescanning."
        ),
        category="integrations",
        field_type=int,
        field_default=30,
        field_constraints={"ge": 0, "le": 365},
        impact="next_tick",
        group="virustotal",
    ),
    RuntimeFieldSpec(
        key="virustotal_daily_quota",
        label="Daily submission quota",
        description=(
            "Hard cap on hash submissions per UTC day. VirusTotal's "
            "free tier is 500/day; the default leaves headroom for "
            "other tools sharing the account."
        ),
        category="integrations",
        field_type=int,
        field_default=250,
        field_constraints={"ge": 0, "le": 100_000},
        impact="immediate",
        group="virustotal",
    ),
    # ── Dashboard (Stage 4) ───────────────────────────────────
    RuntimeFieldSpec(
        key="dashboard_issue_min_severity",
        label="Open-issues threshold",
        description=(
            "Minimum severity a file must have to count as an "
            "\"open issue\" on the dashboard tile and sidebar badge. "
            "The default 'warn' excludes 'ok' and 'info' rows so "
            "informational signal doesn't drown out actionable ones. "
            "Raise to 'high' for operators who only care about the "
            "most actionable rows; lower to 'info' to include every "
            "non-ok file."
        ),
        category="dashboard",
        field_type=str,
        field_default="warn",
        field_constraints={"pattern": r"^(info|warn|high|error|crit)$"},
        impact="immediate",
    ),
    # ── Optimization (Stage 07 v1.7) ────────────────────────────
    RuntimeFieldSpec(
        key="optimization_in_process_runner_enabled",
        label="Run transcodes in-process",
        description=(
            "Master switch for the local ffmpeg worker. When ON "
            "(the default), profiles whose routing_target is "
            "'in_process' execute on this Auditarr host. When OFF, "
            "in-process jobs fail with a clear error directing the "
            "operator to reconfigure the profile to route to plex / "
            "jellyfin / tdarr. Useful when you want Auditarr to "
            "queue + coordinate transcodes but offload the actual "
            "encoding to a beefier box that's running one of those "
            "integrations."
        ),
        category="optimization",
        field_type=bool,
        field_default=True,
        field_constraints={},
        impact="next_tick",
        requires_warning=(
            "Disabling the in-process runner will fail any queued "
            "in-process items on the next tick. Reconfigure those "
            "profiles to a non-in_process routing_target first."
        ),
    ),
    # Stage 08 (v1.7): hwaccel warning dismissal. The startup probe
    # surfaces ``system.hwaccel_missing`` when ffmpeg reports no
    # acceleration; the dashboard renders a banner unless this
    # toggle is True. The operator clicks "Don't show again" to
    # dismiss; the field exists so the dashboard can read the
    # current state.
    RuntimeFieldSpec(
        key="optimization_hwaccel_warning_acknowledged",
        label="Hide 'no hardware acceleration' banner",
        description=(
            "When ON, the dashboard hides the 'no hardware "
            "acceleration detected' banner. The startup probe still "
            "runs and the result is still logged; only the banner "
            "is suppressed. Set this when you've intentionally "
            "deployed Auditarr on a CPU-only host and don't want "
            "the reminder."
        ),
        category="optimization",
        field_type=bool,
        field_default=False,
        field_constraints={},
        impact="immediate",
    ),
)

RUNTIME_EDITABLE_BY_KEY: dict[str, RuntimeFieldSpec] = {
    spec.key: spec for spec in RUNTIME_EDITABLE
}


def is_runtime_editable(key: str) -> bool:
    return key in RUNTIME_EDITABLE_BY_KEY


# ── Secrets ───────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class SecretSpec:
    """Metadata describing one encrypted secret slot."""

    key: str
    label: str
    description: str
    category: Category
    min_length: int
    max_length: int
    test_handler: str | None


SECRETS: tuple[SecretSpec, ...] = (
    SecretSpec(
        key="virustotal_api_key",
        label="VirusTotal API key",
        description=(
            "API key from your VirusTotal account. Stored encrypted; "
            "never returned via the API after it's set. Test below "
            "after entering to confirm it works."
        ),
        category="integrations",
        min_length=32,
        max_length=128,
        test_handler="virustotal_api_key",
    ),
)

SECRETS_BY_KEY: dict[str, SecretSpec] = {s.key: s for s in SECRETS}


# ── Validation ────────────────────────────────────────────────
class RuntimeSettingValidationError(ValueError):
    """Raised when a write fails the field's schema check."""


def _build_validator_model(spec: RuntimeFieldSpec) -> type[BaseModel]:
    field_info = Field(default=spec.field_default, **spec.field_constraints)
    return create_model(
        f"RuntimeValidator_{spec.key}",
        value=(spec.field_type, field_info),
    )


_VALIDATOR_CACHE: dict[str, type[BaseModel]] = {}


def validate_runtime_setting(key: str, value: Any) -> Any:
    """Validate + coerce a write to ``key``. Returns the coerced value."""
    spec = RUNTIME_EDITABLE_BY_KEY.get(key)
    if spec is None:
        raise RuntimeSettingValidationError(
            f"{key!r} is not a runtime-editable setting. "
            f"Edit the env file and restart the service to change it."
        )

    if key == "plugin_gallery_url":
        if isinstance(value, str) and value and not value.startswith(
            ("http://", "https://")
        ):
            raise RuntimeSettingValidationError(
                "plugin_gallery_url must be empty (to disable) or an "
                "http(s) URL."
            )

    validator_cls = _VALIDATOR_CACHE.get(key)
    if validator_cls is None:
        validator_cls = _build_validator_model(spec)
        _VALIDATOR_CACHE[key] = validator_cls

    try:
        validated = validator_cls(value=value)
    except ValidationError as exc:
        first = exc.errors()[0]
        msg = first.get("msg", "invalid value")
        if msg.startswith("Value error, "):
            msg = msg[len("Value error, ") :]
        raise RuntimeSettingValidationError(f"{key}: {msg}") from exc

    return validated.value


def validate_secret(key: str, plaintext: str) -> str:
    """Validate a secret plaintext against its declared length bounds."""
    spec = SECRETS_BY_KEY.get(key)
    if spec is None:
        raise RuntimeSettingValidationError(
            f"{key!r} is not a managed secret."
        )
    if not isinstance(plaintext, str):
        raise RuntimeSettingValidationError(f"{key}: must be a string")
    if len(plaintext) < spec.min_length:
        raise RuntimeSettingValidationError(
            f"{key}: must be at least {spec.min_length} characters"
        )
    if len(plaintext) > spec.max_length:
        raise RuntimeSettingValidationError(
            f"{key}: must be at most {spec.max_length} characters"
        )
    return plaintext


# ── UI-facing descriptors ─────────────────────────────────────
def describe_runtime_settings() -> list[dict[str, Any]]:
    return [
        {
            "key": spec.key,
            "label": spec.label,
            "description": spec.description,
            "category": spec.category,
            "type": _type_name(spec.field_type),
            "default": spec.field_default,
            "constraints": spec.field_constraints,
            "impact": spec.impact,
            "requires_warning": spec.requires_warning,
            # Stage 2 additions. ``group`` is null on fields that
            # don't declare a sub-grouping; the UI renders those at
            # the top level of their category. ``sensitivity``
            # defaults to "normal" on every current entry.
            # ``restart_required`` is False everywhere today; the
            # field is in the contract so future entries that do
            # require restart don't force a schema rev.
            "group": spec.group,
            "sensitivity": spec.sensitivity,
            "restart_required": spec.restart_required,
        }
        for spec in RUNTIME_EDITABLE
    ]


def describe_secrets() -> list[dict[str, Any]]:
    return [
        {
            "key": spec.key,
            "label": spec.label,
            "description": spec.description,
            "category": spec.category,
            "min_length": spec.min_length,
            "max_length": spec.max_length,
            "has_test_handler": spec.test_handler is not None,
        }
        for spec in SECRETS
    ]


def _type_name(t: type) -> str:
    if t is bool:
        return "boolean"
    if t is int:
        return "integer"
    if t is float:
        return "number"
    if t is str:
        return "string"
    return "string"


def _verify_keys_match_settings() -> list[str]:
    """Test-only: report any whitelisted key missing from the Settings
    model. The test suite fails CI if this returns anything."""
    from app.core.settings import Settings

    settings_fields = set(Settings.model_fields.keys())
    return [
        spec.key
        for spec in RUNTIME_EDITABLE
        if spec.key not in settings_fields
    ]
