"""Общие объекты бота, которые нужны обработчикам и расписанию."""

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram import Bot

from .ai import Assistant
from .config import Config
from .notion import Notion
from .state import State
from .sync import Syncer


@dataclass
class App:
    config: Config
    bot: Bot
    notion: Notion
    state: State
    syncer: Syncer
    assistant: Assistant | None
    topics: dict[str, int]  # тот же словарь, что у syncer.topics
    checkin_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.config.timezone)

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def today(self) -> date:
        return self.now().date()
