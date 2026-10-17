"""Запуск: python -m aa_bot"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from google import genai

from .agent import Agent
from .ai import Assistant
from .app import App
from .config import load_config
from .handlers import build_router
from .notion import Notion
from .schedule import Scheduler
from .state import State
from .sync import Syncer
from .topics import ensure_topics
from .workspace import Workspace

log = logging.getLogger("aa_bot")


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    bot = Bot(cfg.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True))
    notion = Notion(cfg.notion_token)
    state = State(cfg.state_path)

    topics: dict[str, int] = {}
    if cfg.owner_id is None:
        log.warning("OWNER_ID не задан. Напишите /id в группе и впишите число в .env")
    else:
        try:
            topics.update(await ensure_topics(bot, cfg.chat_id, state, cfg.topic_overrides))
        except TelegramBadRequest as exc:
            log.error(
                "Не получилось создать темы (%s). Сделайте бота админом с правом управлять темами "
                "и отправьте /setup в группе.",
                exc.message,
            )

    assistant = agent = None
    if cfg.gemini_api_key:
        client = genai.Client(api_key=cfg.gemini_api_key)
        assistant = Assistant(cfg.gemini_api_key, cfg.gemini_model, client)
        agent = Agent(client, cfg.gemini_model, Workspace(notion, cfg), state, cfg)
    else:
        log.warning("GEMINI_API_KEY не задан: нет разговора, голосовые не расшифровываются")

    syncer = Syncer(bot, cfg.chat_id, notion, cfg.databases, state, topics)
    app = App(cfg, bot, notion, state, syncer, assistant, topics, agent)
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(app))

    background = [
        asyncio.create_task(syncer.run_forever(cfg.sync_interval)),
        asyncio.create_task(Scheduler(app).run_forever()),
    ]
    try:
        await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        for task in background:
            task.cancel()
        await notion.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
