"""Общие объекты бота, которые нужны обработчикам и расписанию."""

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram import Bot

from .agent import Agent
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
    agent: Agent | None = None
    checkin_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Один разговор за раз, иначе ответы перепутают историю.
    agent_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _background: set = field(default_factory=set)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.config.timezone)

    def now(self) -> datetime:
        return datetime.now(self.tz)

    def today(self) -> date:
        return self.now().date()

    def run_background(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._background.add(task)
        task.add_done_callback(self._background.discard)
