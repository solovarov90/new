"""Telegram → Notion: сообщения, голосовые и кнопки владельца."""

import logging
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message, User

from . import entries, render, texts
from . import notion as n
from .agent import split_message, to_html
from .ai import AIError, Analysis
from .app import App
from .notion import NotionError, read
from .state import Card
from .topics import ensure_topics

log = logging.getLogger(__name__)

STORY_TOPICS = ("review", "inventory", "evening", "morning")
# None — общая тема группы (General): там тоже можно просто разговаривать.
CHAT_TOPICS = ("chat", None)
CARD_COLLECTIONS = {
    "prayer": "prayers",
    "practice": "practices",
    "person": "people",
    "inventory": "inventory",
    "journal": "journal",
}


class UserFacingError(RuntimeError):
    pass


def build_router(app: App) -> Router:
    router = Router()
    cfg = app.config
    db = cfg.databases

    def is_owner(user: User | None) -> bool:
        return cfg.owner_id is not None and user is not None and user.id == cfg.owner_id

    def topic_of(message: Message) -> str | None:
        thread = message.message_thread_id if message.is_topic_message else None
        for key, value in app.topics.items():
            if value == thread:
                return key
        return None

    # --- команды ---------------------------------------------------------------

    @router.message(Command("id"))
    async def cmd_id(message: Message) -> None:
        user = message.from_user.id if message.from_user else "—"
        await message.reply(
            f"Ваш Telegram ID: <code>{user}</code>\nID чата: <code>{message.chat.id}</code>\n"
            f"ID темы: <code>{message.message_thread_id or '—'}</code>"
        )

    @router.message(Command("start", "help"))
    async def cmd_help(message: Message) -> None:
        if is_owner(message.from_user):
            await message.reply(texts.HELP.format(morning=cfg.morning_time, evening=cfg.evening_time))

    @router.message(Command("setup"))
    async def cmd_setup(message: Message) -> None:
        if not is_owner(message.from_user) or message.chat.id != cfg.chat_id:
            return
        try:
            threads = await ensure_topics(app.bot, cfg.chat_id, app.state, cfg.topic_overrides)
        except TelegramBadRequest as exc:
            await message.reply(
                f"⚠️ Не получилось создать темы: {escape(exc.message)}\n"
                "Сделайте бота администратором группы с правом «Управление темами»."
            )
            return
        app.topics.clear()
        app.topics.update(threads)
        await message.reply("✅ Темы готовы. Переношу всё из Notion, первый раз это займёт несколько минут.")
        totals = await app.syncer.sync_once()
        await message.reply(f"Готово: новых карточек {totals['new']}.")

    @router.message(Command("sync"))
    async def cmd_sync(message: Message) -> None:
        if not is_owner(message.from_user):
            return
        totals = await app.syncer.sync_once()
        await message.reply(f"🔄 Новых: {totals['new']}, обновлено: {totals['edited']}, удалено: {totals['removed']}.")

    @router.message(Command("sos"))
    async def cmd_sos(message: Message) -> None:
        if is_owner(message.from_user):
            await message.reply(texts.sos(cfg.sponsor_contact))

    @router.message(Command("new"))
    async def cmd_new(message: Message) -> None:
        if is_owner(message.from_user) and app.agent is not None:
            app.agent.reset()
            await message.reply("🧹 Начинаю разговор заново. Записи в Notion остались как были.")

    # --- сообщения в темах -----------------------------------------------------

    @router.message(F.chat.id == cfg.chat_id)
    async def on_message(message: Message) -> None:
        if not is_owner(message.from_user):
            return
        topic = topic_of(message)
        try:
            reply = message.reply_to_message
            card = app.state.card_by_message(reply.message_id) if reply else None
            if card and card.kind == "journal":
                await continue_entry(message, card)
            elif card:
                await talk(message, card)
            elif topic in CHAT_TOPICS:
                await talk(message)
            elif topic == "sos":
                await message.reply(texts.sos(cfg.sponsor_contact))
            elif topic == "prayers" and message.text:
                await add_prayer(message)
            elif topic == "people" and message.text:
                await add_people(message)
            elif topic in STORY_TOPICS and (message.text or _audio(message)):
                await new_entry(message)
        except UserFacingError as exc:
            await message.reply(f"⚠️ {escape(str(exc))}")
        except NotionError:
            log.exception("Ошибка Notion")
            await message.reply("⚠️ Notion сейчас не ответил. Сообщение здесь сохранилось, попробуйте ещё раз позже.")

    async def talk(message: Message, card: Card | None = None) -> None:
        if app.agent is None:
            raise UserFacingError("Чтобы разговаривать, добавьте GEMINI_API_KEY в настройки бота.")
        if not (message.text or _audio(message)):
            return
        await app.bot.send_chat_action(cfg.chat_id, "typing", message_thread_id=message.message_thread_id)
        text, _kind = await story_of(message)
        context = None
        if card:
            collection = CARD_COLLECTIONS.get(card.kind, card.kind)
            context = f"[Ответ на карточку из базы {collection}, id записи {card.page_id}]"
        async with app.agent_lock:
            try:
                reply = await app.agent.reply(text, app.now(), context)
            except AIError as exc:
                raise UserFacingError(str(exc)) from exc
        for chunk in split_message(reply.text):
            try:
                await message.reply(to_html(chunk))
            except TelegramBadRequest:
                await message.reply(chunk, parse_mode=None)
        if reply.changed:
            # Карточки в темах обновятся сразу, не дожидаясь следующей минуты.
            app.run_background(app.syncer.sync_once())

    async def story_of(message: Message) -> tuple[str, str]:
        if message.text:
            return message.text, "Текст"
        media = _audio(message)
        if media is None:
            raise UserFacingError("Это сообщение я не умею разбирать. Пришлите текст или голосовое.")
        if app.assistant is None:
            raise UserFacingError("Чтобы разбирать голосовые, добавьте GEMINI_API_KEY в настройки бота.")
        file, mime = media
        buffer = await app.bot.download(file)
        try:
            return await app.assistant.transcribe(buffer.read(), mime), "Голосовое"
        except AIError as exc:
            raise UserFacingError(str(exc)) from exc

    async def analyze(story: str) -> Analysis | None:
        if app.assistant is None:
            return None
        people = await app.notion.query(db.people)
        names = [read(p, "Имя") for p in people if read(p, "Имя")]
        try:
            return await app.assistant.analyze(story, names)
        except AIError:
            log.exception("Разбор не получился")
            return None

    async def new_entry(message: Message) -> None:
        status = await message.reply("⏳ Слушаю и разбираю…")
        try:
            story, kind = await story_of(message)
        except UserFacingError as exc:
            await status.edit_text(f"⚠️ {escape(str(exc))}")
            return
        analysis = await analyze(story)
        props = entries.journal_properties(analysis, story, kind, app.today().isoformat())
        async with app.syncer.lock:
            page = await app.notion.create_page(db.journal, props, text=story)
            text, keyboard = render.journal(page)
            await status.edit_text(text, reply_markup=keyboard)
            app.syncer.remember(page, "journal", status.message_thread_id, status.message_id, text, keyboard)
        if app.assistant is not None and analysis is None:
            await message.reply("Разбор не получился, но расшифровку я сохранил в журнал.")
        if analysis and analysis.support_needed:
            await message.reply(texts.sos(cfg.sponsor_contact))

    async def continue_entry(message: Message, card: Card) -> None:
        status = await message.reply("⏳ Дописываю разбор…")
        try:
            story, _kind = await story_of(message)
        except UserFacingError as exc:
            await status.edit_text(f"⚠️ {escape(str(exc))}")
            return
        earlier = await app.notion.page_text(card.page_id)
        analysis = await analyze(f"{earlier}\n\n{story}".strip())
        async with app.syncer.lock:
            await app.notion.append_text(card.page_id, story)
            if analysis:
                props = entries.analysis_properties(analysis)
                props["Статус"] = n.status("Разбираю" if analysis.questions else "Разобран")
                page = await app.notion.update_page(card.page_id, props)
            else:
                page = await app.notion.get_page(card.page_id)
            await refresh_card(page, "journal")
        await status.edit_text("✅ Дописал в разбор, карточка выше обновлена.")
        if analysis and analysis.support_needed:
            await message.reply(texts.sos(cfg.sponsor_contact))

    async def add_prayer(message: Message) -> None:
        props, body = entries.prayer_from_message(message.text)
        async with app.syncer.lock:
            page = await app.notion.create_page(db.prayers, props, text=body)
            text, keyboard = render.prayer(page, body)
            message_id = await app.syncer.send(message.message_thread_id, text, keyboard)
            app.syncer.remember(page, "prayer", message.message_thread_id, message_id, text, keyboard)
        await _delete_quietly(message)

    async def add_people(message: Message) -> None:
        names = [line.strip() for line in message.text.split("\n") if line.strip()]
        async with app.syncer.lock:
            for name in names:
                page = await app.notion.create_page(db.people, entries.person_from_name(name))
                text, keyboard = render.person(page)
                message_id = await app.syncer.send(message.message_thread_id, text, keyboard)
                app.syncer.remember(page, "person", message.message_thread_id, message_id, text, keyboard)
        await _delete_quietly(message)

    async def refresh_card(page: dict, kind: str) -> None:
        """Перерисовывает карточку страницы сразу, не дожидаясь синхронизации. Вызывать под lock."""
        renderers = {"journal": render.journal, "person": render.person, "inventory": render.inventory}
        text, keyboard = renderers[kind](page, "")
        card = app.state.card(page["id"])
        if card and await app.syncer.edit(card, text, keyboard):
            app.syncer.remember(page, kind, card.thread_id, card.message_id, text, keyboard)
            return
        topic = {"journal": "review", "person": "people", "inventory": "inventory"}[kind]
        thread = app.topics.get(topic)
        message_id = await app.syncer.send(thread, text, keyboard)
        app.syncer.remember(page, kind, thread, message_id, text, keyboard)

    async def mark_checkin(field_key: str) -> None:
        today = app.today()
        async with app.checkin_lock:
            found = await app.notion.query(
                db.checkin, filter={"property": "Дата", "date": {"equals": today.isoformat()}}
            )
            props = entries.checkin_properties(field_key)
            if found:
                await app.notion.update_page(found[0]["id"], props)
            else:
                props["День"] = n.title(today.strftime("%d.%m.%Y"))
                props["Дата"] = n.date(today.isoformat())
                await app.notion.create_page(db.checkin, props)

    # --- кнопки ----------------------------------------------------------------

    @router.callback_query(F.data.startswith("pray:"))
    async def on_pray(query: CallbackQuery) -> None:
        if not is_owner(query.from_user):
            await query.answer("Эта кнопка только для владельца.")
            return
        page_id = query.data.split(":", 1)[1]
        today = app.today().isoformat()
        guard = f"prayed:{page_id}:{today}"
        if app.state.get(guard):
            await query.answer("Сегодня уже отмечено 🙏")
            return
        async with app.syncer.lock:
            page = await app.notion.get_page(page_id)
            props, days = entries.person_after_prayer(page, today)
            page = await app.notion.update_page(page_id, props)
            app.state.set(guard, "1")
            await refresh_card(page, "person")
        await mark_checkin("people")
        if days >= render.PRAYER_GOAL_DAYS:
            await query.answer(
                f"🙏 Две недели за {read(page, 'Имя')} пройдены. Можно взять следующего.", show_alert=True
            )
        else:
            await query.answer(f"🙏 День {days} из {render.PRAYER_GOAL_DAYS}")

    @router.callback_query(F.data.startswith("inv:"))
    async def on_inventory(query: CallbackQuery) -> None:
        if not is_owner(query.from_user):
            await query.answer("Эта кнопка только для владельца.")
            return
        page_id = query.data.split(":", 1)[1]
        async with app.syncer.lock:
            page = await app.notion.get_page(page_id)
            if read(page, "Статус") == "Внесён в инвентаризацию":
                await query.answer("Уже в инвентаризации.")
                return
            props = entries.inventory_from_journal(page)
            try:
                row = await app.notion.create_page(db.inventory, props)
            except NotionError:
                # Например, Notion не принял новое имя в «Кто». Тогда имя уходит в заметку.
                who = props.pop("Кто", None)
                if who is None:
                    raise
                props["Заметка"] = n.text(f"Кто: {read(page, 'Кто')}. Из журнала: {page.get('url', '')}")
                row = await app.notion.create_page(db.inventory, props)
            page = await app.notion.update_page(page_id, {"Статус": n.status("Внесён в инвентаризацию")})
            await refresh_card(page, "journal")
            await refresh_card(row, "inventory")
        await query.answer("➕ Добавлено в инвентаризацию")

    @router.callback_query(F.data.startswith("chk:"))
    async def on_checkin(query: CallbackQuery) -> None:
        if not is_owner(query.from_user):
            await query.answer("Эта кнопка только для владельца.")
            return
        field_key = query.data.split(":", 1)[1]
        if field_key not in entries.CHECKIN_FIELDS:
            await query.answer()
            return
        await mark_checkin(field_key)
        await query.answer(f"✅ {entries.CHECKIN_FIELDS[field_key]}: отмечено")

    return router


def _audio(message: Message):
    if message.voice:
        return message.voice, message.voice.mime_type or "audio/ogg"
    if message.audio:
        return message.audio, message.audio.mime_type or "audio/mpeg"
    if message.video_note:
        return message.video_note, "video/mp4"
    return None


async def _delete_quietly(message: Message) -> None:
    """Убирает исходное сообщение: вместо него в теме уже стоит карточка из Notion."""
    try:
        await message.delete()
    except TelegramBadRequest:
        pass
