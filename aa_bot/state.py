"""Локальное состояние бота: какие сообщения в Telegram соответствуют каким страницам Notion."""

import sqlite3
from dataclasses import dataclass


@dataclass
class Card:
    page_id: str
    kind: str
    thread_id: int | None
    message_id: int
    edited: str
    digest: str


class State:
    def __init__(self, path: str):
        self._db = sqlite3.connect(path)
        self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS cards (
                page_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                thread_id INTEGER,
                message_id INTEGER NOT NULL,
                edited TEXT NOT NULL,
                digest TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS cards_message ON cards (message_id);
            """
        )
        self._db.commit()

    def get(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def set(self, key: str, value: str) -> None:
        self._db.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, value))
        self._db.commit()

    def delete(self, key: str) -> None:
        self._db.execute("DELETE FROM kv WHERE key = ?", (key,))
        self._db.commit()

    def card(self, page_id: str) -> Card | None:
        row = self._db.execute("SELECT * FROM cards WHERE page_id = ?", (page_id,)).fetchone()
        return Card(*row) if row else None

    def card_by_message(self, message_id: int) -> Card | None:
        row = self._db.execute("SELECT * FROM cards WHERE message_id = ?", (message_id,)).fetchone()
        return Card(*row) if row else None

    def cards(self, kind: str) -> list[Card]:
        rows = self._db.execute("SELECT * FROM cards WHERE kind = ?", (kind,)).fetchall()
        return [Card(*row) for row in rows]

    def save_card(self, card: Card) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO cards VALUES (?, ?, ?, ?, ?, ?)",
            (card.page_id, card.kind, card.thread_id, card.message_id, card.edited, card.digest),
        )
        self._db.commit()

    def drop_card(self, page_id: str) -> None:
        self._db.execute("DELETE FROM cards WHERE page_id = ?", (page_id,))
        self._db.commit()
