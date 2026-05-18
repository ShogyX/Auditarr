"""Application settings.

Configuration precedence (lowest -> highest):
  1. hardcoded defaults (this module)
  2. config files (none in stage 1)
  3. environment variables (prefix ``AUDITARR_``)
  4. database overrides (stage 2+)
  5. runtime overrides (stage 2+)
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def _default_app_version() -> str:
    """Read ``app.__version__`` at call time so the Settings default
    tracks the package version automatically. Falls back to
    ``"0.0.0-dev"`` if the import fails (e.g. settings imported from
    a context where ``app`` isn't on the path yet — shouldn't happen
    in production but is friendly to tests).
    """
    try:
        from app import __version__

        return __version__
    except ImportError:
        return "0.0.0-dev"


class Settings(BaseSettings):
    """Top-level Auditarr configuration."""

    model_config = SettingsConfigDict(
        env_prefix="AUDITARR_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ────────────────────────────────────────────────────
    env: Literal["development", "staging", "production", "test"] = "development"
    debug: bool = False
    host: str = "0.0.0.0"
    port: int = 8000
    api_prefix: str = "/api"
    api_version: str = "v1"

    # ── Logging ────────────────────────────────────────────────
    log_level: Literal["debug", "info", "warning", "error", "critical"] = "info"
    log_format: Literal["console", "json"] = "console"

    # ── Security ───────────────────────────────────────────────
    secret_key: str = Field(default="dev-insecure-change-me", min_length=16)
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 30
    refresh_token_ttl_days: int = 14
    allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

    # ── Database ───────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://auditarr:auditarr@localhost:5432/auditarr"
    database_pool_size: int = 10
    database_max_overflow: int = 20
    # Recycle pooled connections after this many seconds of inactivity.
    # (Stage 1 / L3) Without this, Postgres connections idle past the
    # server-side ``idle_in_transaction_session_timeout`` (or a TCP
    # keepalive ceiling on a NAT) come back stale and the next request
    # blows up with "connection has been closed". 30 minutes is the
    # SQLAlchemy-recommended default. Setting <=0 disables recycling.
    database_pool_recycle: int = 1800
    database_echo: bool = False

    # ── Redis / queue ──────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    queue_name: str = "auditarr:queue"

    # ── Paths ──────────────────────────────────────────────────
    data_dir: Path = Path("./data")
    plugin_dir: Path = Path("./plugins")
    builtin_plugin_dir: Path = Path("./plugins")
    docs_dir: Path = Path("./docs")
    frontend_dist: Path | None = None

    # ── Updater (Stage 11) ─────────────────────────────────────
    # The version this build identifies as. v1.8.1: defaults to the
    # ``app.__version__`` constant so we don't drift — pre-1.8.1 the
    # default was a hardcoded "1.6.0" that every release forgot to
    # bump, with the result that the updater always reported "update
    # available" for any newer feed value AND wrote a stale
    # ``from_version`` into the apply sentinel.
    #
    # Operators can still override via AUDITARR_APP_VERSION if their
    # deployment tooling needs a different value (e.g. ``rc-<git-sha>``
    # for staging environments).
    app_version: str = Field(
        default_factory=lambda: _default_app_version()
    )
    # Where the updater pulls release metadata from. The default points
    # at the project's GitHub Releases JSON; operators can swap to a
    # private mirror by setting AUDITARR_UPDATE_FEED_URL.
    update_feed_url: str = (
        "https://api.github.com/repos/ShogyX/Auditarr/releases/latest"
    )
    # Polling interval for the cron tick that checks the feed.
    update_check_interval_minutes: int = 60
    # Inside-container path where the apply request sentinel is written.
    # The host-side helper (docker/updater/auditarr-update.sh) watches
    # this and runs the actual ``docker compose pull && up -d``.
    update_apply_sentinel: Path = Path("./data/updater/apply.request")
    update_apply_status_path: Path = Path("./data/updater/apply.status")
    # Stage 19: which install environment we're running under, so the
    # backend can return appropriate copy and the appropriate helper
    # script knows it should respond to apply requests.
    #
    # Values:
    #   "auto"        — detect on every check (default)
    #   "docker"      — Docker / docker-compose; host-side helper is
    #                   docker/updater/auditarr-update.sh
    #   "bare-metal"  — systemd + native postgres + redis; host-side
    #                   helper is updater/auditarr-update-bare-metal.sh
    #   "unmanaged"   — apply is disabled. UI shows "check for updates"
    #                   only. For installs the operator wants to manage
    #                   by hand (e.g. running under ansible).
    update_install_mode: str = "auto"
    # v1.9 Stage 1.2 — how long an update apply may sit in
    # ``requested`` or ``running`` before the reaper considers it
    # stale and force-marks it ``failed``. The host helper writes
    # status transitions at the end of the apply, so a healthy run
    # is typically tens of seconds to a few minutes; 30 minutes
    # leaves generous headroom for slow networks pulling a fresh
    # image. The reaper runs on every ``has_open()`` poll so the
    # next ``request_apply`` after a wedge can succeed instead of
    # 409-ing forever.
    update_apply_timeout_seconds: int = 1800

    # ── Plugin gallery (Stage 12) ──────────────────────────────
    # JSON manifest URL listing community plugins. Default points at
    # the project's gallery; operators on air-gapped networks set this
    # to an internal mirror. Setting to an empty string disables the
    # gallery UI.
    plugin_gallery_url: str = (
        "https://raw.githubusercontent.com/auditarr/plugins/main/gallery.json"
    )

    # ── Housekeeping (Stage 13) ────────────────────────────────
    # Retention windows for noisy audit tables. 0 disables the trim
    # (useful for development; in production a value <90 days is fine
    # for most home-lab volumes).
    housekeeping_delivery_retention_days: int = 30
    housekeeping_update_check_retention_days: int = 90
    housekeeping_rule_evaluation_retention_days: int = 0  # kept indefinitely
    housekeeping_job_run_retention_days: int = 60

    # ── Rate limiting (Stage 13) ───────────────────────────────
    # Sliding-window rate limit on auth endpoints. The default — 10
    # attempts per 5 minutes per IP — is permissive enough not to lock
    # out a fat-fingered operator but tight enough to slow a brute
    # forcer to ~3000 guesses/day, which is useless against argon2id.
    auth_rate_limit_attempts: int = 10
    auth_rate_limit_window_seconds: int = 300

    # ── WebSocket auth (Stage 14) ─────────────────────────────
    # Require a valid access JWT in the ``?token=`` query parameter on
    # WebSocket upgrades. Default True. Tests that don't care about WS
    # auth can override to False.
    ws_require_auth: bool = True

    # ── Scanner tunables (Stage 21) ───────────────────────────
    # Per-file ffprobe timeout. ffprobe occasionally hangs on
    # malformed or DRM-tied containers; this is the hard ceiling.
    scanner_ffprobe_timeout_seconds: int = 30
    # Number of parallel files the scanner can process. Higher
    # values utilize more CPU + IO; values above 8 rarely pay off
    # on consumer storage and risk blowing the FD limit.
    scanner_worker_concurrency: int = 4
    # Skip files larger than this. Defaults to 50 GB which covers
    # remuxes; raise it for archival 8K masters.
    scanner_max_file_size_mb: int = 51200

    # ── Webhook notification tunables (Stage 21) ──────────────
    # Default HTTP timeout when delivering a webhook notification.
    # Per-channel overrides take precedence; this is the fallback.
    notifications_webhook_default_timeout_seconds: int = 10
    # How many times we retry a webhook before giving up. Each
    # retry waits with exponential backoff (1s, 2s, 4s, ...).
    notifications_webhook_max_retries: int = 3

    # ── Dashboard tunables (Stage 4) ──────────────────────────
    # Minimum severity that counts as an "open issue" on the
    # dashboard tile and sidebar badge. Default ``warn`` means
    # ``ok`` and ``info`` files do NOT inflate the issues-open
    # counter — operators reported that informational rows being
    # counted as issues drowned out the real signal. Threshold is
    # a label that maps to the standard severity rank scale
    # (ok=10, info=20, warn=40, high=60, error=80, crit=100);
    # the dashboard service compares ``severity_rank >= threshold_rank``.
    dashboard_issue_min_severity: str = "warn"

    # ── VirusTotal integration (Stage 21) ─────────────────────
    # Enable the VirusTotal integration. The API key is stored as
    # an encrypted secret (key="virustotal_api_key"), not here.
    # When this is False the integration is fully disabled regardless
    # of the API key state.
    virustotal_enabled: bool = False
    # Submit new files for scanning when they're first imported.
    # When False, files only get scanned if explicitly requested
    # via the API.
    virustotal_scan_on_import: bool = False
    # Rescan a file's hash if the last verdict is older than this.
    # 0 disables rescanning. Long values match VirusTotal's free
    # tier where verdicts are rarely revised.
    virustotal_rescan_interval_days: int = 30
    # Hard cap on daily submissions. VirusTotal's free tier is
    # 500/day; we default well below that so the operator can run
    # other tools against the same account.
    virustotal_daily_quota: int = 250

    # ── Optimization (Stage 07 v1.7) ──────────────────────────
    # Master switch for the in-process ffmpeg worker. When False,
    # the worker fails any item whose profile has
    # ``routing_target='in_process'`` with a clear error. Profiles
    # routed to plex/jellyfin/tdarr are unaffected. Override-able
    # at runtime via the runtime-settings API.
    optimization_in_process_runner_enabled: bool = True
    # Stage 08 (v1.7): operator-controlled dismissal of the
    # "no hardware acceleration detected" banner. False by default
    # (banner shows). The dashboard reads this; the startup probe
    # writes nothing to it.
    optimization_hwaccel_warning_acknowledged: bool = False

    # ── Stage 9.5.7 (OP-8 / OP-9) — Language preferences ───────
    # Operator-configurable list of ISO language codes (3-letter
    # ISO 639-2 preferred — ``eng``, ``fra``, etc — matches what
    # ffprobe + most container metadata emits). Used by two
    # dashboard surfaces:
    #
    #   * Foreign audio without preferred subs: counts media files
    #     whose primary audio track's language is NOT in
    #     ``preferred_audio_languages`` AND that carry no subtitle
    #     track in any of ``preferred_subtitle_languages``.
    #
    #   * Anything in the rules engine can also key off these
    #     (operators can build their own "warn when not in
    #     preferred lang" rule).
    #
    # Empty list disables the check. Defaults match the
    # English-speaking majority of the operator base; non-English
    # operators override via the Settings → Workspace tab.
    preferred_audio_languages: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["eng"]
    )
    preferred_subtitle_languages: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["eng"]
    )

    @field_validator(
        "preferred_audio_languages",
        "preferred_subtitle_languages",
        mode="before",
    )
    @classmethod
    def _split_language_list(cls, v: object) -> object:
        if isinstance(v, str):
            return [s.strip().lower() for s in v.split(",") if s.strip()]
        if isinstance(v, list):
            return [str(s).strip().lower() for s in v if str(s).strip()]
        return v

    @field_validator("allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    # Consolidated audit follow-up: be lenient about case for the
    # Literal-typed string fields. pydantic_settings is strict about
    # Literals — a raw ``AUDITARR_LOG_LEVEL=INFO`` env var would be
    # rejected because the Literal only lists lowercase values. Most
    # operators (and the install script's first iteration) write
    # uppercase log levels by convention from the Python stdlib's
    # ``logging`` module; lowercasing here keeps both forms valid
    # without widening the Literal's accepted value set.
    @field_validator("log_level", "log_format", "env", mode="before")
    @classmethod
    def _lowercase_literal(cls, v: object) -> object:
        if isinstance(v, str):
            return v.lower()
        return v

    @field_validator(
        "data_dir",
        "plugin_dir",
        "builtin_plugin_dir",
        "docs_dir",
        "update_apply_sentinel",
        "update_apply_status_path",
        mode="after",
    )
    @classmethod
    def _resolve_path(cls, v: Path) -> Path:
        return v.expanduser().resolve()

    @property
    def plugin_directories(self) -> list[Path]:
        """All directories the plugin loader should scan, deduped + ordered.

        Built-in plugins (shipped in the image) are scanned first so that
        user-supplied plugins on a mounted volume cannot accidentally shadow
        the canonical reference plugins by id collision.
        """
        seen: set[Path] = set()
        ordered: list[Path] = []
        for path in (self.builtin_plugin_dir, self.plugin_dir):
            if path in seen:
                continue
            seen.add(path)
            ordered.append(path)
        return ordered

    # ── Derived properties ─────────────────────────────────────
    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def api_root(self) -> str:
        return f"{self.api_prefix}/{self.api_version}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached settings instance."""
    return Settings()
