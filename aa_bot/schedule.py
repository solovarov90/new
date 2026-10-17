"""Утренняя и вечерняя рассылка в темы по расписанию."""

import asyncio
import logging
from datetime import date, datetime, time, timedelta

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from . import render, texts
from .app import App
from .entries import plural_days
from .notion import read

log = logging.getLogger(__name__)

# Если бот был выключен в момент рассылки, он догонит её в течение этого окна, но не позже.
WINDOW = timedelta(hours=1)


def _keyboard(*rows: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows]
    )


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

    async def _prayers(self, when: str) -> list[tuple[dict, str]]:
        pages = await self.app.notion.query(
            self.app.config.databases.prayers,
            filter={"property": "Когда", "select": {"equals": when}},
            sorts=[{"property": "Порядок", "direction": "ascending"}],
        )
        return [(page, await self.app.notion.page_text(page["id"])) for page in pages]

    async def morning(self, day: date) -> None:
        app, cfg = self.app, self.app.config
        thread = app.topics.get("morning")
        header = f"☀️ <b>Доброе утро!</b> {day.strftime('%d.%m.%Y')}"
        if cfg.sobriety_date:
            header += f"\nТрезвость: <b>{plural_days((day - cfg.sobriety_date).days)}</b>"
        await app.syncer.send(thread, header, None)
        for page, body in await self._prayers("Утро"):
            text, _ = render.prayer(page, body)
            await app.syncer.send(thread, text, None)
        await app.syncer.send(
            thread,
            "Отметь, когда помолишься. Можно наговорить сюда голосовое о намерении на день.",
            _keyboard([("✅ Утренняя молитва", "chk:morning")]),
        )
        await self._people_of_the_day()

    async def _people_of_the_day(self) -> None:
        app = self.app
        people = await app.notion.query(app.config.databases.people)
        praying = [read(p, "Имя") for p in people if read(p, "Молитва") == "Молюсь"]
        if praying:
            text = "👥 <b>Сегодня молюсь за:</b> " + ", ".join(praying)
            text += "\nПрошу для каждого всего того хорошего, чего хочу себе. На карточке человека нажми «Помолился сегодня»."
        else:
            waiting = [read(p, "Имя") for p in people if read(p, "Молитва") == "Не начато"]
            if not waiting:
                return
            text = (
                f"👥 Сейчас ни за кого не идёт молитва. Можно начать с: <b>{waiting[0]}</b>. "
                "Найди его карточку в этой теме и нажми «Помолился сегодня»."
            )
        await app.syncer.send(app.topics.get("people"), text, None)

    async def evening(self, day: date) -> None:
        app = self.app
        thread = app.topics.get("evening")
        for page, body in await self._prayers("Вечер"):
            text, _ = render.prayer(page, body)
            await app.syncer.send(thread, text, None)
        await app.syncer.send(
            thread,
            texts.EVENING_QUESTIONS,
            _keyboard(
                [("✅ Итог дня сделан", "chk:review"), ("🙏 Вечерняя молитва", "chk:evening")],
                [("🍃 Трезвый день", "chk:sober"), ("🤝 Встреча или звонок", "chk:meeting")],
            ),
        )
