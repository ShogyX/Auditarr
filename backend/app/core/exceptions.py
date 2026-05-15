"""Domain exceptions.

All exceptions inherit from :class:`AuditarrError` so the global error handler
can map them to consistent JSON responses without leaking internals.
"""

from __future__ import annotations

from typing import Any


class AuditarrError(Exception):
    """Base class for all Auditarr domain errors."""

    status_code: int = 500
    code: str = "internal_error"

    def __init__(
        self,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}
        self.__cause__ = cause

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


class ConfigurationError(AuditarrError):
    status_code = 500
    code = "configuration_error"


class NotFoundError(AuditarrError):
    status_code = 404
    code = "not_found"


class ConflictError(AuditarrError):
    status_code = 409
    code = "conflict"


class ValidationError(AuditarrError):
    status_code = 422
    code = "validation_error"


class AuthenticationError(AuditarrError):
    status_code = 401
    code = "authentication_required"


class AuthorizationError(AuditarrError):
    status_code = 403
    code = "forbidden"


class RateLimitError(AuditarrError):
    status_code = 429
    code = "rate_limited"


class IntegrationError(AuditarrError):
    status_code = 502
    code = "integration_error"


class PluginError(AuditarrError):
    status_code = 500
    code = "plugin_error"


class ServiceUnavailableError(AuditarrError):
    status_code = 503
    code = "service_unavailable"
