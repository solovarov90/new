"""Подставные Notion и Telegram для тестов без сети."""

import itertools
from types import SimpleNamespace

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText

from aa_bot.notion import NotionError


def prop(kind: str, value):
    if kind in ("title", "rich_text"):
        return {"type": kind, kind: [{"plain_text": value}] if value else []}
    if kind in ("select", "status"):
        return {"type": kind, kind: {"name": value} if value else None}
    if kind == "multi_select":
        return {"type": kind, kind: [{"name": v} for v in value]}
    if kind == "date":
        return {"type": kind, kind: {"start": value} if value else None}
    return {"type": kind, kind: value}


def page(page_id: str, edited: str = "2026-01-01T00:00:00.000Z", **props) -> dict:
    return {
        "id": page_id,
        "url": f"https://www.notion.so/{page_id}",
        "created_time": "2026-01-01T00:00:00.000Z",
        "last_edited_time": edited,
        "properties": {name: prop(kind, value) for name, (kind, value) in props.items()},
    }


class FakeNotion:
    def __init__(self, databases: dict[str, list[dict]], bodies: dict[str, str] | None = None):
        self.databases = databases
        self.bodies = bodies or {}
        self.created: list[tuple[str, dict, str | None]] = []

    async def query(self, database_id, filter=None, sorts=None):
        return [dict(p) for p in self.databases.get(database_id, [])]

    async def get_page(self, page_id):
        for pages in self.databases.values():
            for p in pages:
                if p["id"] == page_id:
                    return dict(p)
        raise NotionError(f"GET /pages/{page_id}: 404", 404)

    async def page_text(self, page_id):
        return self.bodies.get(page_id, "")

    async def create_page(self, database_id, properties, text=None):
        self.created.append((database_id, properties, text))
        return page(f"new-{len(self.created)}")


class FakeBot:
    def __init__(self):
        self.ids = itertools.count(100)
        self.sent: list[dict] = []
        self.edited: list[dict] = []
        self.deleted: list[int] = []
        self.missing: set[int] = set()

    async def send_message(self, chat_id, text, message_thread_id=None, reply_markup=None):
        message_id = next(self.ids)
        self.sent.append({"id": message_id, "thread": message_thread_id, "text": text, "keyboard": reply_markup})
        return SimpleNamespace(message_id=message_id)

    async def edit_message_text(self, text, chat_id, message_id, reply_markup=None):
        if message_id in self.missing:
            raise TelegramBadRequest(EditMessageText(text=text), "Bad Request: message to edit not found")
        self.edited.append({"id": message_id, "text": text})

    async def delete_message(self, chat_id, message_id):
        self.deleted.append(message_id)
