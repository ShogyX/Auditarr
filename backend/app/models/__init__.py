"""ORM models.

Models must be imported here so Alembic autogenerate can see them via
``Base.metadata``.
"""

from app.models.audit_log import AuditLogEntry
from app.models.extension_rule import MediaExtensionRule
from app.models.housekeeping_run import HousekeepingRun
from app.models.integration import Integration
from app.models.job_run import JobRun
from app.models.library import Library
from app.models.media import MediaFile
from app.models.notification_channel import NotificationChannel
from app.models.notification_delivery import NotificationDelivery
from app.models.optimization import OptimizationItem
from app.models.optimization_profile import OptimizationProfile
from app.models.password_reset import PasswordResetToken
from app.models.path_mapping import GlobalPathMapping
from app.models.playback import IntegrationPollingCursor, PlaybackEvent
from app.models.plugin_settings import PluginSettings
from app.models.rule import Rule
from app.models.rule_evaluation import RuleEvaluation
from app.models.rule_suggestion import RuleSuggestion
from app.models.runtime_setting import (
    EncryptedSecret,
    RuntimeSettingChange,
    RuntimeSettingOverride,
)
from app.models.scan_run import ScanRun
from app.models.schedule import Schedule
from app.models.session import RefreshSession
from app.models.tag import MediaTag
from app.models.update_apply import UpdateApply
from app.models.update_check import UpdateCheck
from app.models.user import User

__all__ = [
    "AuditLogEntry",
    "MediaExtensionRule",
    "HousekeepingRun",
    "Integration",
    "IntegrationPollingCursor",
    "JobRun",
    "Library",
    "MediaFile",
    "MediaTag",
    "NotificationChannel",
    "NotificationDelivery",
    "OptimizationItem",
    "OptimizationProfile",
    "PasswordResetToken",
    "PlaybackEvent",
    "PluginSettings",
    "RefreshSession",
    "Rule",
    "RuleEvaluation",
    "RuleSuggestion",
    "RuntimeSettingChange",
    "RuntimeSettingOverride",
    "EncryptedSecret",
    "GlobalPathMapping",
    "ScanRun",
    "Schedule",
    "UpdateApply",
    "UpdateCheck",
    "User",
]
