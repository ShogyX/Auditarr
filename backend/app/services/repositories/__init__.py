"""Repository pattern — DB access isolated from services."""

from app.services.repositories.audit import AuditRepository
from app.services.repositories.automation import (
    JobRunRepository,
    OptimizationProfileRepository,
    OptimizationRepository,
    ScheduleRepository,
)
from app.services.repositories.extension_rule import (
    MediaExtensionRuleRepository,
)
from app.services.repositories.integration import IntegrationRepository
from app.services.repositories.library import LibraryRepository
from app.services.repositories.media import (
    MatchedRuleSummary,
    MediaFilter,
    MediaPage,
    MediaRepository,
)
from app.services.repositories.notification import (
    NotificationChannelRepository,
    NotificationDeliveryRepository,
)
from app.services.repositories.password_reset import PasswordResetRepository
from app.services.repositories.path_mapping import GlobalPathMappingRepository
from app.services.repositories.plugin_settings import PluginSettingsRepository
from app.services.repositories.rule import (
    RuleEvaluationRepository,
    RuleRepository,
)
from app.services.repositories.rule_suggestion import RuleSuggestionRepository
from app.services.repositories.scan import ScanRepository
from app.services.repositories.session import RefreshSessionRepository
from app.services.repositories.updater import (
    UpdateApplyRepository,
    UpdateCheckRepository,
)
from app.services.repositories.user import UserRepository

__all__ = [
    "AuditRepository",
    "GlobalPathMappingRepository",
    "IntegrationRepository",
    "JobRunRepository",
    "LibraryRepository",
    "MatchedRuleSummary",
    "MediaFilter",
    "MediaExtensionRuleRepository",
    "MediaPage",
    "MediaRepository",
    "NotificationChannelRepository",
    "NotificationDeliveryRepository",
    "OptimizationProfileRepository",
    "OptimizationRepository",
    "PasswordResetRepository",
    "PluginSettingsRepository",
    "RefreshSessionRepository",
    "RuleEvaluationRepository",
    "RuleRepository",
    "RuleSuggestionRepository",
    "ScanRepository",
    "ScheduleRepository",
    "UpdateApplyRepository",
    "UpdateCheckRepository",
    "UserRepository",
]
