"""Domain event types and canonical event names.

The constants below are the single source of truth for "what events
exist". Call sites should import the constant rather than retype the
string literal — that way a typo surfaces at import time, and tooling
that needs to enumerate the vocabulary (WS docs, AI context payload,
event-name validators) has exactly one place to look.

Every constant value is a dotted-lowercase string matching the regex
``^[a-z]+\\.[a-z_]+(\\.[a-z_]+)?$``. The ``EVENT_NAMES`` tuple is
derived from those constants so the two never drift.
"""

from __future__ import annotations

import datetime as _dt
import uuid
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

# ── Media ──────────────────────────────────────────────────────
MEDIA_DETECTED: Final = "media.detected"
MEDIA_ADDED: Final = "media.added"
MEDIA_UPDATED: Final = "media.updated"
MEDIA_DELETED: Final = "media.deleted"
MEDIA_REMOVED: Final = "media.removed"
MEDIA_REPROBED: Final = "media.reprobed"

# ── Scans ──────────────────────────────────────────────────────
SCAN_STARTED: Final = "scan.started"
SCAN_PROGRESS: Final = "scan.progress"
SCAN_COMPLETED: Final = "scan.completed"
SCAN_FAILED: Final = "scan.failed"
SCAN_REAPED: Final = "scan.reaped"

# ── Rules ──────────────────────────────────────────────────────
RULE_MATCHED: Final = "rule.matched"
RULE_TRIGGERED: Final = "rule.triggered"
RULE_FAILED: Final = "rule.failed"
RULE_THROTTLED: Final = "rule.throttled"

# ── Optimization ───────────────────────────────────────────────
OPTIMIZATION_STARTED: Final = "optimization.started"
OPTIMIZATION_PROGRESS: Final = "optimization.progress"
OPTIMIZATION_COMPLETED: Final = "optimization.completed"
OPTIMIZATION_FAILED: Final = "optimization.failed"
OPTIMIZATION_ROUTED: Final = "optimization.routed"
OPTIMIZATION_ROUTED_COMPLETED: Final = "optimization.routed_completed"
OPTIMIZATION_ROUTED_FAILED: Final = "optimization.routed_failed"
OPTIMIZATION_SKIPPED_WINDOW: Final = "optimization.skipped_window"

# ── Jobs ───────────────────────────────────────────────────────
JOB_STARTED: Final = "job.started"
JOB_COMPLETED: Final = "job.completed"
JOB_FAILED: Final = "job.failed"

# ── Notifications ──────────────────────────────────────────────
NOTIFICATION_SENT: Final = "notification.sent"
NOTIFICATION_FAILED: Final = "notification.failed"

# ── Integrations ───────────────────────────────────────────────
INTEGRATION_SYNC_COMPLETED: Final = "integration.sync.completed"
INTEGRATION_UNHEALTHY: Final = "integration.unhealthy"
INTEGRATION_HEALTH_CHANGED: Final = "integration.health_changed"
INTEGRATION_TAGS_SYNCED: Final = "integration.tags_synced"
INTEGRATION_PATH_DRIFT: Final = "integration.path_drift"

# ── Updates ────────────────────────────────────────────────────
UPDATE_AVAILABLE: Final = "update.available"
UPDATE_INSTALLED: Final = "update.installed"
UPDATE_FAILED: Final = "update.failed"

# ── System ─────────────────────────────────────────────────────
SYSTEM_STARTUP: Final = "system.startup"
SYSTEM_SHUTDOWN: Final = "system.shutdown"
SYSTEM_USER_REGISTERED: Final = "system.user_registered"
SYSTEM_HWACCEL_MISSING: Final = "system.hwaccel_missing"

# ── Plugins ────────────────────────────────────────────────────
PLUGIN_LOADED: Final = "plugin.loaded"
PLUGIN_UNLOADED: Final = "plugin.unloaded"
PLUGIN_RELOADED: Final = "plugin.reloaded"
PLUGIN_INSTALLED: Final = "plugin.installed"
PLUGIN_UNINSTALLED: Final = "plugin.uninstalled"
PLUGIN_ERROR: Final = "plugin.error"

# ── VirusTotal (plugin-namespaced) ─────────────────────────────
VIRUSTOTAL_RESULT: Final = "virustotal.result"
VIRUSTOTAL_QUOTA_EXHAUSTED: Final = "virustotal.quota_exhausted"


# The canonical, dotted-lowercase event vocabulary. Plugins MUST use
# names from here or register their own under a plugin-prefixed
# namespace. This tuple is derived from the constants above so adding
# a new event is a one-line change (define the constant) — there's
# no second list to keep in sync.
EVENT_NAMES: Final[tuple[str, ...]] = (
    # media
    MEDIA_DETECTED,
    MEDIA_ADDED,
    MEDIA_UPDATED,
    MEDIA_DELETED,
    MEDIA_REMOVED,
    MEDIA_REPROBED,
    # scans
    SCAN_STARTED,
    SCAN_PROGRESS,
    SCAN_COMPLETED,
    SCAN_FAILED,
    SCAN_REAPED,
    # rules
    RULE_MATCHED,
    RULE_TRIGGERED,
    RULE_FAILED,
    RULE_THROTTLED,
    # optimization
    OPTIMIZATION_STARTED,
    OPTIMIZATION_PROGRESS,
    OPTIMIZATION_COMPLETED,
    OPTIMIZATION_FAILED,
    OPTIMIZATION_ROUTED,
    OPTIMIZATION_ROUTED_COMPLETED,
    OPTIMIZATION_ROUTED_FAILED,
    OPTIMIZATION_SKIPPED_WINDOW,
    # jobs
    JOB_STARTED,
    JOB_COMPLETED,
    JOB_FAILED,
    # notifications
    NOTIFICATION_SENT,
    NOTIFICATION_FAILED,
    # integrations
    INTEGRATION_SYNC_COMPLETED,
    INTEGRATION_UNHEALTHY,
    INTEGRATION_HEALTH_CHANGED,
    INTEGRATION_TAGS_SYNCED,
    INTEGRATION_PATH_DRIFT,
    # updates
    UPDATE_AVAILABLE,
    UPDATE_INSTALLED,
    UPDATE_FAILED,
    # system
    SYSTEM_STARTUP,
    SYSTEM_SHUTDOWN,
    SYSTEM_USER_REGISTERED,
    SYSTEM_HWACCEL_MISSING,
    # plugins
    PLUGIN_LOADED,
    PLUGIN_UNLOADED,
    PLUGIN_RELOADED,
    PLUGIN_INSTALLED,
    PLUGIN_UNINSTALLED,
    PLUGIN_ERROR,
    # virustotal (plugin namespace, but listed here because the
    # plugin is shipped in-tree)
    VIRUSTOTAL_RESULT,
    VIRUSTOTAL_QUOTA_EXHAUSTED,
)

EventName = str  # alias kept for typed signatures; validated at publish time


class DomainEvent(BaseModel):
    """A normalized event emitted by any subsystem."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: EventName
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str = Field(default="core", description="emitter identifier")
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: _dt.datetime = Field(
        default_factory=lambda: _dt.datetime.now(_dt.UTC)
    )
    correlation_id: str | None = None

    def is_known(self) -> bool:
        return self.name in EVENT_NAMES or "." in self.name
