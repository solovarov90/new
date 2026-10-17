"""Закреплённые сообщения в темах: пульт дня, меню 4 шага и пояснения. Бот держит их актуальными."""

import logging

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup

from . import ui
from .app import App
from .notion import read
from .sync import digest

log = logging.getLogger(__name__)

DASHBOARD = "panel:dashboard"
INVENTORY = "panel:inventory"


def view_key(message_id: int) -> str:
    """Какую обиду показывает сообщение меню 4 шага (пусто — главный экран или список человека)."""
    return f"view:{message_id}"


class Panels:
    def __init__(self, app: App):
        self.app = app

    # --- данные -----------------------------------------------------------------

    async def inventory_pages(self) -> list[dict]:
        return await self.app.notion.query(self.app.config.databases.inventory)

    async def dashboard_view(self) -> tuple[str, InlineKeyboardMarkup]:
        app, dbs = self.app, self.app.config.databases
        today = app.today()
        checkin = await app.notion.query(
            dbs.checkin, filter={"property": "Дата", "date": {"equals": today.isoformat()}}
        )
        people = await app.notion.query(dbs.people)
        praying = [p for p in people if read(p, "Молитва") == "Молюсь"]
        return ui.dashboard(
            today, app.config.sobriety_date, checkin[0] if checkin else None, praying, app.config.chat_id, app.topics
        )

    async def inventory_main_view(self) -> tuple[str, InlineKeyboardMarkup]:
        return ui.inventory_main(await self.inventory_pages())

    # --- закрепление ---------------------------------------------------------------

    async def _pin(self, message_id: int) -> None:
        try:
            await self.app.bot.pin_chat_message(self.app.config.chat_id, message_id, disable_notification=True)
        except TelegramBadRequest as exc:
            log.info("Не закрепил сообщение %s: %s", message_id, exc.message)

    async def post_pinned(self, key: str, topic: str, text: str, keyboard: InlineKeyboardMarkup | None) -> int:
        message_id = await self.app.syncer.send(self.app.topics.get(topic), text, keyboard)
        await self._pin(message_id)
        self.app.state.set(key, str(message_id))
        self.app.state.set(f"{key}:digest", digest(text, keyboard))
        return message_id

    async def ensure(self) -> None:
        """Создаёт пояснения, пульт и меню 4 шага там, где их ещё нет."""
        cfg = self.app.config
        for topic, intro in ui.INTROS.items():
            key = f"intro:{topic}"
            if topic in self.app.topics and not self.app.state.get(key):
                await self.post_pinned(
                    key, topic, intro.format(morning=cfg.morning_time, evening=cfg.evening_time), None
                )
        if "chat" in self.app.topics and not self.app.state.get(DASHBOARD):
            await self.post_pinned(DASHBOARD, "chat", *await self.dashboard_view())
        if "inventory" in self.app.topics and not self.app.state.get(INVENTORY):
            await self.post_pinned(INVENTORY, "inventory", *await self.inventory_main_view())

    # --- обновление ----------------------------------------------------------------

    async def _refresh(self, key: str, topic: str, text: str, keyboard: InlineKeyboardMarkup) -> None:
        stored = self.app.state.get(key)
        new_digest = digest(text, keyboard)
        if stored and self.app.state.get(f"{key}:digest") == new_digest:
            return
        if stored and await self.app.syncer.edit_message(int(stored), text, keyboard):
            self.app.state.set(f"{key}:digest", new_digest)
            return
        # Сообщение удалили вручную или его ещё не было: отправляем заново и закрепляем.
        await self.post_pinned(key, topic, text, keyboard)

    async def refresh_dashboard(self) -> None:
        if "chat" in self.app.topics:
            await self._refresh(DASHBOARD, "chat", *await self.dashboard_view())

    async def refresh_inventory(self) -> None:
        stored = self.app.state.get(INVENTORY)
        if "inventory" not in self.app.topics:
            return
        # Если в меню сейчас открыта обида или список человека, не сбрасываем экран.
        if stored and self.app.state.get(view_key(int(stored))):
            return
        await self._refresh(INVENTORY, "inventory", *await self.inventory_main_view())

    async def refresh_all(self) -> None:
        for refresh in (self.refresh_dashboard, self.refresh_inventory):
            try:
                await refresh()
            except Exception:
                log.exception("Не обновилась панель")
