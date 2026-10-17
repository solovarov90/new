"""Утренняя и вечерняя рассылка в темы по расписанию."""

import asyncio
import logging
from datetime import date, datetime, time, timedelta

from . import ui
from .app import App

log = logging.getLogger(__name__)

# Если бот был выключен в момент рассылки, он догонит её в течение этого окна, но не позже.
WINDOW = timedelta(hours=1)


class Scheduler:
    def __init__(self, app: App):
        self.app = app

    async def run_forever(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("Ошибка рассылки")
            await asyncio.sleep(30)

    async def tick(self, now: datetime | None = None) -> None:
        if not self.app.topics:
            return
        cfg = self.app.config
        now = now or self.app.now()
        jobs = (("morning", cfg.morning_time, self.morning), ("evening", cfg.evening_time, self.evening))
        for key, at, job in jobs:
            start = datetime.combine(now.date(), time.fromisoformat(at), tzinfo=now.tzinfo)
            already = self.app.state.get(f"sent:{key}") == now.date().isoformat()
            if start <= now < start + WINDOW and not already:
                # Сначала отмечаем, чтобы сбой на середине не повторял рассылку каждые 30 секунд.
                self.app.state.set(f"sent:{key}", now.date().isoformat())
                await job(now.date())

    async def _prayers(self, when: str) -> list[dict]:
        return await self.app.notion.query(
            self.app.config.databases.prayers,
            filter={"property": "Когда", "select": {"equals": when}},
            sorts=[{"property": "Порядок", "direction": "ascending"}],
        )

    async def morning(self, day: date) -> None:
        app = self.app
        prayers = await self._prayers("Утро")
        people = await app.notion.query(app.config.databases.people)
        text, keyboard = ui.morning(day, app.config.sobriety_date, prayers, people)
        await app.syncer.send(app.topics.get("morning"), text, keyboard)

    async def evening(self, day: date) -> None:
        app = self.app
        text, keyboard = ui.evening(await self._prayers("Вечер"))
        await app.syncer.send(app.topics.get("evening"), text, keyboard)
