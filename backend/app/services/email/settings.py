"""Email subsystem settings.

Kept separate from :class:`Settings` so the email subsystem can evolve without
churning core config. ``BaseSettings`` reads the same env file with a more
specific prefix (``AUDITARR_SMTP_*``).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import EmailStr, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class EmailSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AUDITARR_SMTP_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    enabled: bool = False
    backend: Literal["smtp", "console"] = "console"
    host: str = "localhost"
    port: int = 25
    username: str | None = None
    password: str | None = None
    use_tls: bool = False
    use_ssl: bool = False
    from_email: EmailStr = Field(default="noreply@example.com")
    from_name: str = "Auditarr"
    reset_link_base: str = Field(
        default="http://localhost:8000",
        description="Public URL the reset link points back to (no trailing slash).",
    )


@lru_cache(maxsize=1)
def get_email_settings() -> EmailSettings:
    return EmailSettings()
