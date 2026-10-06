from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.scheduler import Scheduler
    from core.telegram import TelegramClient


@dataclass(frozen=True)
class Services:
    """Side-effecting clients, swapped for fakes in tests and for in-process scheduling locally."""

    telegram: TelegramClient
    scheduler: Scheduler


@dataclass(frozen=True)
class Context:
    """One Telegram update reduced to what handlers use. `data` is set for button clicks, `text` for messages."""

    user_id: int
    chat_id: int
    text: str
    data: str
    message_id: int | None
    telegram: TelegramClient
    scheduler: Scheduler
