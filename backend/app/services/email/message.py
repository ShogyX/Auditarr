"""Email message + provider protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(slots=True)
class EmailMessage:
    """Structured email message ready for delivery."""

    to: list[str]
    subject: str
    text_body: str
    html_body: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.to:
            raise ValueError("EmailMessage requires at least one recipient")
        self.to = [addr.strip() for addr in self.to if addr.strip()]


class EmailProvider(Protocol):
    """Pluggable email backend."""

    name: str

    async def send(self, message: EmailMessage) -> None: ...

    async def healthcheck(self) -> bool: ...
