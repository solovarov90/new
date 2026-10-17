"""Синхронизация Notion → Telegram: каждая страница баз становится карточкой в своей теме.

Новые страницы появляются в теме, изменённые редактируются на месте, удалённые из Notion
пропадают из Telegram. Сообщения в обратную сторону обрабатывает handlers.py.
"""

import asyncio
import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.types import InlineKeyboardMarkup

from . import render
from .config import Databases
from .notion import Notion, NotionError, read
from .state import Card, State

log = logging.getLogger(__name__)

# last_edited_time в Notion округляется до минуты, поэтому недавно изменённые страницы
# перерисовываем каждый раз и сравниваем по содержимому.
RECENT = timedelta(minutes=3)

Renderer = Callable[[dict, str], tuple[str, InlineKeyboardMarkup | None]]


@dataclass(frozen=True)
class Spec:
    kind: str
    database: str  # имя поля в Databases
    topic: str  # ключ темы из topics.TOPICS
    render: Renderer
    with_body: bool = False


def _by_order(page: dict):
    return (read(page, "Порядок") or 999, page["created_time"])


SPECS = (
    Spec("prayer", "prayers", "prayers", render.prayer, with_body=True),
    Spec("practice", "practices", "practices", render.practice),
    Spec("person", "people", "people", render.person),
    Spec("journal", "journal", "review", render.journal),
)
# Раньше обиды были отдельными карточками, теперь их заменило меню 4 шага (panels.py).
RETIRED = {"inventory": "📝 <i>Обиды теперь в меню, закреплённом вверху этой темы.</i>"}


def digest(text: str, keyboard: InlineKeyboardMarkup | None) -> str:
    raw = text + (keyboard.model_dump_json() if keyboard else "")
    return hashlib.sha1(raw.encode()).hexdigest()


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class Syncer:
    def __init__(
        self,
        bot: Bot,
        chat_id: int,
        notion: Notion,
        databases: Databases,
        state: State,
        topics: dict[str, int],
        pause: float = 3.0,
    ):
        self.bot = bot
        self.chat_id = chat_id
        self.notion = notion
        self.databases = databases
        self.state = state
        self.topics = topics
        self.pause = pause  # Telegram ограничивает бота ~20 сообщениями в минуту в группе
        # lock берут на одну страницу и обработчики сообщений, чтобы не отправить карточку дважды.
        # Весь проход держит _run_lock, иначе кнопки ждали бы минуты во время первого переноса.
        self.lock = asyncio.Lock()
        self._run_lock = asyncio.Lock()
        # Что сделать после каждого прохода (например, обновить пульт дня).
        self.after_sync: list[Callable[[], Awaitable[None]]] = []

    async def run_forever(self, interval: int) -> None:
        while True:
            if self.topics:
                try:
                    await self.sync_once()
                except Exception:
                    log.exception("Ошибка синхронизации")
            await asyncio.sleep(interval)

    async def sync_once(self) -> dict[str, int]:
        totals = {"new": 0, "edited": 0, "removed": 0}
        async with self._run_lock:
            for kind, note in RETIRED.items():
                for card in self.state.cards(kind):
                    async with self.lock:
                        await self.remove(card, note)
            for spec in SPECS:
                try:
                    counts = await self._sync_spec(spec)
                except NotionError:
                    log.exception("Не удалось прочитать базу %s", spec.database)
                    continue
                for key, value in counts.items():
                    totals[key] += value
            for hook in self.after_sync:
                await hook()
        return totals

    async def _sync_spec(self, spec: Spec) -> dict[str, int]:
        counts = {"new": 0, "edited": 0, "removed": 0}
        pages = await self.notion.query(getattr(self.databases, spec.database))
        pages.sort(key=_by_order if spec.kind == "prayer" else (lambda p: p["created_time"]))
        now = datetime.now(timezone.utc)
        seen = set()
        for page in pages:
            seen.add(page["id"])
            async with self.lock:
                result = await self._sync_page(spec, page, now)
            if result:
                counts[result] += 1
        for card in self.state.cards(spec.kind):
            # Страница могла появиться уже после запроса списка (например, из голосового),
            # поэтому перед удалением карточки проверяем саму страницу.
            if card.page_id not in seen and await self._is_gone(card.page_id):
                async with self.lock:
                    await self.remove(card)
                counts["removed"] += 1
        return counts

    async def _sync_page(self, spec: Spec, page: dict, now: datetime) -> str | None:
        card = self.state.card(page["id"])
        edited = page["last_edited_time"]
        recent = now - _parse_time(edited) < RECENT
        if card and card.edited == edited and not recent:
            return None
        body = await self.notion.page_text(page["id"]) if spec.with_body else ""
        text, keyboard = spec.render(page, body)
        new_digest = digest(text, keyboard)
        if card and card.digest == new_digest:
            card.edited = edited
            self.state.save_card(card)
            return None
        if card and await self.edit(card, text, keyboard):
            result = "edited"
        else:
            thread = self.topics.get(spec.topic)
            message_id = await self.send(thread, text, keyboard)
            card = Card(page["id"], spec.kind, thread, message_id, edited, new_digest)
            result = "new"
        card.edited, card.digest = edited, new_digest
        self.state.save_card(card)
        return result

    async def _is_gone(self, page_id: str) -> bool:
        try:
            page = await self.notion.get_page(page_id)
        except NotionError as exc:
            return exc.status == 404
        return bool(page.get("archived") or page.get("in_trash"))

    async def send(self, thread: int | None, text: str, keyboard: InlineKeyboardMarkup | None) -> int:
        while True:
            try:
                message = await self.bot.send_message(
                    self.chat_id, text, message_thread_id=thread, reply_markup=keyboard
                )
                await asyncio.sleep(self.pause)
                return message.message_id
            except TelegramRetryAfter as exc:
                await asyncio.sleep(exc.retry_after + 1)

    async def edit(self, card: Card, text: str, keyboard: InlineKeyboardMarkup | None) -> bool:
        """Правит карточку. False, если сообщения больше нет и карточку нужно отправить заново."""
        return await self.edit_message(card.message_id, text, keyboard)

    async def edit_message(
        self, message_id: int, text: str, keyboard: InlineKeyboardMarkup | None, pause: bool = True
    ) -> bool:
        """Правит сообщение. pause=False для ответа на нажатие кнопки, чтобы экран менялся сразу."""
        while True:
            try:
                await self.bot.edit_message_text(
                    text=text, chat_id=self.chat_id, message_id=message_id, reply_markup=keyboard
                )
                if pause:
                    await asyncio.sleep(self.pause)
                return True
            except TelegramRetryAfter as exc:
                await asyncio.sleep(exc.retry_after + 1)
            except TelegramBadRequest as exc:
                if "not modified" in exc.message:
                    return True
                log.info("Сообщение %s не редактируется: %s", message_id, exc.message)
                return False

    async def remove(self, card: Card, note: str = "🗑 <i>Удалено в Notion</i>") -> None:
        try:
            await self.bot.delete_message(self.chat_id, card.message_id)
        except TelegramBadRequest:
            # Бот не может удалять свои сообщения старше 48 часов, тогда оставляет пометку.
            try:
                await self.bot.edit_message_text(text=note, chat_id=self.chat_id, message_id=card.message_id)
            except TelegramBadRequest:
                pass
        self.state.drop_card(card.page_id)

    def remember(
        self,
        page: dict,
        kind: str,
        thread: int | None,
        message_id: int,
        text: str,
        keyboard: InlineKeyboardMarkup | None,
    ) -> None:
        """Запоминает карточку, которую бот отправил сам, чтобы синхронизация её не дублировала."""
        self.state.save_card(
            Card(page["id"], kind, thread, message_id, page["last_edited_time"], digest(text, keyboard))
        )
