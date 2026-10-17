"""Telegram → Notion: сообщения, голосовые и кнопки владельца."""

import hashlib
import json
import logging
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, ForceReply, Message, User

from . import entries, render, texts, ui
from . import notion as n
from .agent import split_message, to_html
from .ai import AIError, Analysis, fill_name, has_name_placeholder
from .app import App
from .notion import NotionError, read
from .panels import DASHBOARD, INVENTORY, PEOPLE, Panels, view_key
from .sync import digest
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
PATTERNS_REQUEST = (
    "Посмотри мою инвентаризацию 4 шага целиком и покажи, какие страхи, дефекты и задетые сферы "
    "повторяются чаще всего и у каких людей. Коротко, по пунктам, и в конце один вопрос для разговора со спонсором."
)


class UserFacingError(RuntimeError):
    pass


def prompt_key(message_id: int) -> str:
    return f"prompt:{message_id}"


def build_router(app: App, panels: Panels) -> Router:
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

    async def allowed(query: CallbackQuery) -> bool:
        if is_owner(query.from_user):
            return True
        await query.answer("Эта кнопка только для владельца.")
        return False

    def thread_of(query: CallbackQuery) -> int | None:
        return getattr(query.message, "message_thread_id", None)

    async def send_html(thread: int | None, text: str, reply_to: int | None = None) -> None:
        """Отправляет ответ помощника, разбивая длинный текст на части."""
        for chunk in split_message(text):
            try:
                await app.bot.send_message(
                    cfg.chat_id, to_html(chunk), message_thread_id=thread, reply_to_message_id=reply_to
                )
            except TelegramBadRequest:
                await app.bot.send_message(
                    cfg.chat_id, chunk, message_thread_id=thread, reply_to_message_id=reply_to, parse_mode=None
                )

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
        await panels.ensure()
        totals = await app.syncer.sync_once()
        await message.reply(f"Готово: новых карточек {totals['new']}.")

    @router.message(Command("menu", "today"))
    async def cmd_menu(message: Message) -> None:
        if not is_owner(message.from_user) or message.chat.id != cfg.chat_id:
            return
        text, keyboard = await panels.dashboard_view()
        sent = await message.answer(text, reply_markup=keyboard)
        if topic_of(message) == "chat":
            app.state.set(DASHBOARD, str(sent.message_id))
            app.state.set(f"{DASHBOARD}:digest", digest(text, keyboard))

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
        reply = message.reply_to_message
        try:
            prompt = app.state.get(prompt_key(reply.message_id)) if reply else None
            view = app.state.get(view_key(reply.message_id)) if reply else None
            card = app.state.card_by_message(reply.message_id) if reply else None
            if prompt:
                await answer_prompt(message, json.loads(prompt), reply.message_id)
            elif view and view.startswith("ppl:") and view != "ppl:list":
                await talk(message, view[4:], "people")
            elif view and not view.startswith(("person:", "ppl:")):
                await talk(message, view, "inventory")
            elif card and card.kind == "journal":
                await continue_entry(message, card.page_id)
            elif card:
                await talk(message, card.page_id, CARD_COLLECTIONS.get(card.kind, card.kind))
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

    async def answer_prompt(message: Message, prompt: dict, prompt_message_id: int) -> None:
        mode, page_id = prompt.get("mode"), prompt.get("page")
        if mode == "edit":
            await talk(message, page_id, prompt.get("collection", "inventory"))
        elif mode == "append":
            await continue_entry(message, page_id)
        elif mode == "story":
            await new_entry(message)
        elif mode == "person" and message.text:
            await add_people(message)
        app.state.delete(prompt_key(prompt_message_id))
        try:
            await app.bot.delete_message(cfg.chat_id, prompt_message_id)
        except TelegramBadRequest:
            pass

    async def ask(query: CallbackQuery, text: str, placeholder: str, prompt: dict) -> None:
        """Просит ответить на сообщение: так бот понимает, к какой записи относится ответ."""
        sent = await app.bot.send_message(
            cfg.chat_id,
            text,
            message_thread_id=thread_of(query),
            reply_markup=ForceReply(force_reply=True, input_field_placeholder=placeholder),
        )
        app.state.set(prompt_key(sent.message_id), json.dumps(prompt))

    async def talk(message: Message, page_id: str | None = None, collection: str | None = None) -> None:
        if app.agent is None:
            raise UserFacingError("Чтобы разговаривать, добавьте GEMINI_API_KEY в настройки бота.")
        if not (message.text or _audio(message)):
            return
        await app.bot.send_chat_action(cfg.chat_id, "typing", message_thread_id=message.message_thread_id)
        text, _kind = await story_of(message)
        context = f"[Ответ на запись из базы {collection}, id записи {page_id}]" if page_id else None
        await run_agent(text, message.message_thread_id, message.message_id, context)

    async def run_agent(text: str, thread: int | None, reply_to: int | None, context: str | None = None) -> None:
        async with app.agent_lock:
            try:
                reply = await app.agent.reply(text, app.now(), context)
            except AIError as exc:
                raise UserFacingError(str(exc)) from exc
        await send_html(thread, reply.text, reply_to)
        if reply.changed:
            # Карточки и панели обновятся сразу, не дожидаясь следующей минуты.
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

    async def continue_entry(message: Message, page_id: str) -> None:
        status = await message.reply("⏳ Дописываю разбор…")
        try:
            story, _kind = await story_of(message)
        except UserFacingError as exc:
            await status.edit_text(f"⚠️ {escape(str(exc))}")
            return
        earlier = await app.notion.page_text(page_id)
        analysis = await analyze(f"{earlier}\n\n{story}".strip())
        async with app.syncer.lock:
            await app.notion.append_text(page_id, story)
            if analysis:
                props = entries.analysis_properties(analysis)
                props["Статус"] = n.status("Разбираю" if analysis.questions else "Разобран")
                page = await app.notion.update_page(page_id, props)
            else:
                page = await app.notion.get_page(page_id)
            await refresh_card(page, "journal")
        await status.edit_text("✅ Дописал в разбор, карточка обновлена.")
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
        for name in names:
            await app.notion.create_page(db.people, entries.person_from_name(name))
        await message.reply(
            f"✍️ Добавил: {escape(', '.join(names))}. Они в меню «👥 Промаливание» → «➕ Начать молиться за…»."
        )
        await panels.refresh_people()

    async def refresh_card(page: dict, kind: str) -> None:
        """Перерисовывает карточку страницы сразу, не дожидаясь синхронизации. Вызывать под lock."""
        renderers = {"journal": render.journal}
        text, keyboard = renderers[kind](page, "")
        card = app.state.card(page["id"])
        if card and await app.syncer.edit(card, text, keyboard):
            app.syncer.remember(page, kind, card.thread_id, card.message_id, text, keyboard)
            return
        thread = app.topics.get({"journal": "review"}[kind])
        message_id = await app.syncer.send(thread, text, keyboard)
        app.syncer.remember(page, kind, thread, message_id, text, keyboard)

    async def set_checkin(field_key: str, value: bool | None = True) -> bool:
        """Ставит отметку чек-ина. value=None переключает её. Возвращает новое значение."""
        today = app.today()
        label = entries.CHECKIN_FIELDS[field_key]
        async with app.checkin_lock:
            found = await app.notion.query(
                db.checkin, filter={"property": "Дата", "date": {"equals": today.isoformat()}}
            )
            if value is None:
                value = not (found and read(found[0], label))
            props = {label: n.checkbox(value)}
            if found:
                await app.notion.update_page(found[0]["id"], props)
            else:
                props["День"] = n.title(today.strftime("%d.%m.%Y"))
                props["Дата"] = n.date(today.isoformat())
                await app.notion.create_page(db.checkin, props)
        return value

    async def show_dashboard(query: CallbackQuery) -> None:
        text, keyboard = await panels.dashboard_view()
        message_id = query.message.message_id
        edited = await app.syncer.edit_message(message_id, text, keyboard, pause=False)
        if edited and app.state.get(DASHBOARD) == str(message_id):
            app.state.set(f"{DASHBOARD}:digest", digest(text, keyboard))

    # --- кнопки: пульт, молитвы, чек-ин ------------------------------------------

    @router.callback_query(F.data == "dash")
    async def on_dashboard(query: CallbackQuery) -> None:
        if await allowed(query):
            await query.answer("Обновлено")
            await show_dashboard(query)

    @router.callback_query(F.data.startswith("tg:"))
    async def on_toggle(query: CallbackQuery) -> None:
        if not await allowed(query):
            return
        field_key = query.data.split(":", 1)[1]
        if field_key not in entries.CHECKIN_FIELDS:
            await query.answer()
            return
        value = await set_checkin(field_key, None)
        await query.answer(("✅ " if value else "⬜ ") + entries.CHECKIN_FIELDS[field_key])
        await show_dashboard(query)

    @router.callback_query(F.data.startswith("chk:"))
    async def on_checkin(query: CallbackQuery) -> None:
        if not await allowed(query):
            return
        field_key = query.data.split(":", 1)[1]
        if field_key not in entries.CHECKIN_FIELDS:
            await query.answer()
            return
        await set_checkin(field_key, True)
        await query.answer(f"✅ {entries.CHECKIN_FIELDS[field_key]}: отмечено")
        app.run_background(panels.refresh_dashboard())

    @router.callback_query(F.data.startswith("pr:"))
    async def on_prayer(query: CallbackQuery) -> None:
        if not await allowed(query):
            return
        await query.answer()
        page_id = query.data.split(":", 1)[1]
        page = await app.notion.get_page(page_id)
        text, _ = render.prayer(page, await app.notion.page_text(page_id))
        await app.bot.send_message(cfg.chat_id, text, message_thread_id=thread_of(query))

    @router.callback_query(F.data == "prl")
    async def on_prayer_list(query: CallbackQuery) -> None:
        if not await allowed(query):
            return
        await query.answer()
        prayers = await app.notion.query(db.prayers, sorts=[{"property": "Порядок", "direction": "ascending"}])
        text, keyboard = ui.prayer_list(prayers)
        await app.bot.send_message(cfg.chat_id, text, message_thread_id=thread_of(query), reply_markup=keyboard)

    @router.callback_query(F.data == "sos")
    async def on_sos(query: CallbackQuery) -> None:
        if await allowed(query):
            await query.answer()
            await app.bot.send_message(cfg.chat_id, texts.sos(cfg.sponsor_contact), message_thread_id=thread_of(query))

    async def mark_prayed(page_id: str) -> tuple[dict, int, bool]:
        """Отмечает молитву за человека сегодня. Возвращает страницу, дни и признак «уже было»."""
        today = app.today().isoformat()
        async with app.syncer.lock:
            page = await app.notion.get_page(page_id)
            if entries.prayed_today(page, today):
                return page, int(read(page, "Дней подряд") or 0), True
            props, days = entries.person_after_prayer(page, today)
            page = await app.notion.update_page(page_id, props)
        await set_checkin("people", True)
        return page, days, False

    async def prayed_answer(query: CallbackQuery, page: dict, days: int, already: bool) -> None:
        if already:
            await query.answer("Сегодня уже отмечено 🙏")
        elif days >= render.PRAYER_GOAL_DAYS:
            await query.answer(
                f"🙏 Две недели за {read(page, 'Имя')} пройдены. Можно взять следующего.", show_alert=True
            )
        else:
            await query.answer(f"🙏 {read(page, 'Имя')}: день {days} из {render.PRAYER_GOAL_DAYS}")

    @router.callback_query(F.data.startswith("pray:"))
    async def on_pray(query: CallbackQuery) -> None:
        if not await allowed(query):
            return
        page, days, already = await mark_prayed(query.data.split(":", 1)[1])
        await prayed_answer(query, page, days, already)
        if already:
            return
        if app.state.get(DASHBOARD) == str(query.message.message_id):
            await show_dashboard(query)
        else:
            app.run_background(panels.refresh_dashboard())
        app.run_background(panels.refresh_people())

    async def prayer_template() -> str:
        """Молитва с «(имя)» из базы: сначала среди молитв «За людей и обиды»."""
        prayers = await app.notion.query(db.prayers)
        prayers.sort(key=lambda p: read(p, "Когда") != "За людей и обиды")
        for prayer in prayers:
            body = await app.notion.page_text(prayer["id"])
            if has_name_placeholder(body):
                return body
        return texts.DEFAULT_PERSON_PRAYER

    async def named_prayer(names: list[str]) -> str:
        template = await prayer_template()
        joined = ", ".join(names)
        key = "named:" + hashlib.sha1(f"{template}\n{joined}".encode()).hexdigest()
        cached = app.state.get(key)
        if cached:
            return cached
        text = fill_name(template, joined)
        if app.assistant is not None:
            try:
                text = await app.assistant.personalize(template, joined)
            except AIError:
                log.info("Имя подставлено без склонения")
        app.state.set(key, text)
        return text

    # --- кнопки: меню промаливания ---------------------------------------------

    @router.callback_query(F.data.startswith("pp:"))
    async def on_people_menu(query: CallbackQuery) -> None:
        if not await allowed(query):
            return
        _, action, *rest = query.data.split(":")
        arg = rest[0] if rest else ""
        message_id = query.message.message_id
        today = app.today().isoformat()
        current = app.state.get(view_key(message_id)) or ""
        on_person = current.startswith("ppl:") and current != "ppl:list"

        if action == "new":
            await query.answer()
            await ask(
                query,
                "✍️ Напиши имя человека ответом на это сообщение. Можно несколько, каждое с новой строки.",
                "Имя человека",
                {"mode": "person"},
            )
            return
        if action in ("pr", "prall"):
            await query.answer("Готовлю молитву…")
            if action == "pr":
                person = await app.notion.get_page(arg)
                names = [(person["id"], read(person, "Имя") or "")]
            else:
                people = await panels.people_pages()
                names = [(p["id"], read(p, "Имя") or "") for p in people if read(p, "Молитва") == entries.PRAYING]
            if not names:
                return
            text, keyboard = ui.personal_prayer(names, await named_prayer([name for _id, name in names]))
            await app.bot.send_message(cfg.chat_id, text, message_thread_id=thread_of(query), reply_markup=keyboard)
            return

        if action == "t":
            page = await app.notion.get_page(arg)
            if entries.prayed_today(page, today):
                props, days = entries.person_undo_prayer(page)
                await app.notion.update_page(arg, props)
                await query.answer("↩️ Отметка снята")
            else:
                page, days, already = await mark_prayed(arg)
                await prayed_answer(query, page, days, already)
            action = "v" if on_person else "m"
            app.run_background(panels.refresh_dashboard())
        elif action == "s":
            page = await app.notion.get_page(arg)
            await app.notion.update_page(arg, entries.person_start(page, today))
            await query.answer(f"▶️ Молюсь за {read(page, 'Имя')}")
            action = "v" if on_person else "m"
            app.run_background(panels.refresh_dashboard())
        elif action == "x":
            page = await app.notion.get_page(arg)
            await app.notion.update_page(arg, entries.person_pause(page))
            await query.answer("⏸ Пауза, счётчик сохранён")
            action = "v"
            app.run_background(panels.refresh_dashboard())
        else:
            await query.answer()

        if action == "v":
            text, keyboard = ui.person_view(await app.notion.get_page(arg), today)
            app.state.set(view_key(message_id), f"ppl:{ui.pid(arg)}")
        elif action in ("all", "add"):
            people = await panels.people_pages()
            text, keyboard = ui.people_all(people) if action == "all" else ui.people_pick(people)
            app.state.set(view_key(message_id), "ppl:list")
        else:
            text, keyboard = ui.people_main(await panels.people_pages(), today)
            app.state.delete(view_key(message_id))
        await app.syncer.edit_message(message_id, text, keyboard, pause=False)
        if app.state.get(PEOPLE) == str(message_id):
            main = app.state.get(view_key(message_id)) is None
            app.state.set(f"{PEOPLE}:digest", digest(text, keyboard) if main else "")

    # --- кнопки: разборы ---------------------------------------------------------

    @router.callback_query(F.data.startswith("inv:"))
    async def on_to_inventory(query: CallbackQuery) -> None:
        if not await allowed(query):
            return
        page_id = query.data.split(":", 1)[1]
        async with app.syncer.lock:
            page = await app.notion.get_page(page_id)
            if read(page, "Статус") == "Внесён в инвентаризацию":
                await query.answer("Уже в инвентаризации.")
                return
            props = entries.inventory_from_journal(page)
            try:
                await app.notion.create_page(db.inventory, props)
            except NotionError:
                # Например, Notion не принял новое имя в «Кто». Тогда имя уходит в заметку.
                if props.pop("Кто", None) is None:
                    raise
                props["Заметка"] = n.text(f"Кто: {read(page, 'Кто')}. Из журнала: {page.get('url', '')}")
                await app.notion.create_page(db.inventory, props)
            page = await app.notion.update_page(page_id, {"Статус": n.status("Внесён в инвентаризацию")})
            await refresh_card(page, "journal")
        await query.answer("➕ Добавлено в 4 шаг")
        app.run_background(panels.refresh_inventory())

    @router.callback_query(F.data.startswith("jed:"))
    async def on_journal_append(query: CallbackQuery) -> None:
        if not await allowed(query):
            return
        await query.answer()
        await ask(
            query,
            "✏️ Что дописать к разбору? Ответь на это сообщение голосом или текстом.",
            "Дописать к разбору",
            {"mode": "append", "page": query.data.split(":", 1)[1]},
        )

    @router.callback_query(F.data.startswith("jok:"))
    async def on_journal_done(query: CallbackQuery) -> None:
        if not await allowed(query):
            return
        page_id = query.data.split(":", 1)[1]
        async with app.syncer.lock:
            page = await app.notion.update_page(page_id, {"Статус": n.status("Разобран")})
            await refresh_card(page, "journal")
        await query.answer("✅ Разобран")

    # --- кнопки: меню 4 шага -----------------------------------------------------

    @router.callback_query(F.data.startswith("ib:"))
    async def on_inventory_menu(query: CallbackQuery) -> None:
        if not await allowed(query):
            return
        _, action, *rest = query.data.split(":")
        arg = rest[0] if rest else ""
        message_id = query.message.message_id

        if action == "ed":
            await query.answer()
            await ask(
                query,
                "✏️ Что поправить в этой обиде? Ответь на это сообщение голосом или текстом, "
                "например: «корысть: хотел, чтобы меня пожалели».",
                "Что поправить",
                {"mode": "edit", "page": arg, "collection": "inventory"},
            )
            return
        if action == "new":
            await query.answer()
            await ask(
                query,
                "➕ Расскажи обиду ответом на это сообщение: на кого и за что. Я разложу по колонкам, "
                "а потом её можно будет добавить в 4 шаг кнопкой.",
                "На кого и за что обида",
                {"mode": "story"},
            )
            return
        if action == "pat":
            if app.agent is None:
                await query.answer("Нужен GEMINI_API_KEY", show_alert=True)
                return
            await query.answer("Смотрю инвентаризацию…")
            await app.bot.send_chat_action(cfg.chat_id, "typing", message_thread_id=thread_of(query))
            try:
                await run_agent(PATTERNS_REQUEST, thread_of(query), message_id)
            except UserFacingError as exc:
                await app.bot.send_message(cfg.chat_id, f"⚠️ {escape(str(exc))}", message_thread_id=thread_of(query))
            return

        if action == "ok":
            page = await app.notion.get_page(arg)
            ready = read(page, "Статус") == ui.READY
            await app.notion.update_page(arg, {"Статус": n.status("Черновик" if ready else ui.READY)})
            await query.answer("↩️ В черновике" if ready else "🟢 Готово к 5 шагу")
            action = "r"
        else:
            await query.answer()

        pages = await panels.inventory_pages()
        groups = ui.group_by_person(pages)
        text = keyboard = None
        if action == "p":
            for name, items in groups:
                if ui.person_key(name) == arg:
                    text, keyboard = ui.inventory_person(name, items)
                    app.state.set(view_key(message_id), f"person:{arg}")
        elif action == "r":
            for _name, items in groups:
                match = [p for p in items if ui.pid(p["id"]) == ui.pid(arg)]
                if match:
                    text, keyboard = ui.inventory_record(match[0], items)
                    app.state.set(view_key(message_id), ui.pid(arg))
        if text is None:
            text, keyboard = ui.inventory_main(pages)
            app.state.delete(view_key(message_id))
        await app.syncer.edit_message(message_id, text, keyboard, pause=False)
        if app.state.get(INVENTORY) == str(message_id):
            main = app.state.get(view_key(message_id)) is None
            app.state.set(f"{INVENTORY}:digest", digest(text, keyboard) if main else "")

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
