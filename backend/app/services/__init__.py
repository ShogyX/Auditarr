"""Business-logic service layer.

Stage 2 introduces:
* :class:`AuthService` — login, logout, refresh, password reset
* :class:`AuditService` — audit log writes
* :class:`EmailService` — Jinja-rendered transactional email
"""

from app.services.audit_service import AuditService
from app.services.auth_service import AuthContext, AuthService, TokenPair
from app.services.email import EmailService

__all__ = [
    "AuditService",
    "AuthContext",
    "AuthService",
    "EmailService",
    "TokenPair",
]
