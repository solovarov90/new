"""Темы (вкладки) Telegram-группы. Бот создаёт недостающие сам."""

import logging
from dataclasses import dataclass

from aiogram import Bot

from .state import State

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Topic:
    key: str
    name: str
    icon_color: int  # Telegram разрешает только шесть цветов значка темы


TOPICS = (
    Topic("chat", "💬 Помощник", 0x8EEE98),
    Topic("morning", "☀️ Утро", 0xFFD67E),
    Topic("prayers", "🙏 Молитвы", 0x6FB9F0),
    Topic("people", "👥 Промаливание", 0xCB86DB),
    Topic("review", "🎙 Разбор ситуаций", 0xFF93B2),
    Topic("inventory", "📝 4 шаг: инвентаризация", 0xFB6F5F),
    Topic("practices", "🧭 Практики", 0x8EEE98),
    Topic("evening", "🌙 Вечер: 10 шаг", 0x6FB9F0),
    Topic("sos", "🆘 SOS", 0xFB6F5F),
)


async def ensure_topics(bot: Bot, chat_id: int, state: State, overrides: dict[str, int]) -> dict[str, int]:
    threads = {}
    for topic in TOPICS:
        thread = overrides.get(topic.key)
        if thread is None:
            stored = state.get(f"topic:{topic.key}")
            thread = int(stored) if stored else None
        if thread is None:
            created = await bot.create_forum_topic(chat_id, topic.name, icon_color=topic.icon_color)
            thread = created.message_thread_id
            state.set(f"topic:{topic.key}", str(thread))
            log.info("Создана тема %s (%s)", topic.name, thread)
        threads[topic.key] = thread
    return threads
