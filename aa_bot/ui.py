"""Экраны с кнопками: пульт дня, меню 4 шага, утро, вечер и пояснения к темам. Без сети."""

import hashlib
from datetime import date
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from . import texts
from .entries import CHECKIN_FIELDS, plural_days
from .notion import read
from .render import PRAYER_GOAL_DAYS
from .render import inventory as inventory_card

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
STATUS_ICON = {"Черновик": "⚪", "Дописать": "🟡", "Готово к 5 шагу": "🟢"}
READY = "Готово к 5 шагу"
CHECKIN_LABELS = {
    "morning": "Утренняя молитва",
    "people": "Молитва за людей",
    "review": "Итог дня",
    "evening": "Вечерняя молитва",
    "meeting": "Встреча / звонок",
    "sober": "Трезвый день",
}
BUTTON_TEXT = 40

TOPIC_BUTTONS = (
    ("chat", "💬 Помощник"),
    ("review", "🎙 Разбор"),
    ("inventory", "📝 4 шаг"),
    ("prayers", "🙏 Молитвы"),
    ("people", "👥 Люди"),
    ("sos", "🆘 SOS"),
)

INTROS = {
    "morning": "☀️ <b>Утро</b>\nВ {morning} здесь утренние молитвы и счётчик трезвости. "
    "Можно наговорить сюда намерение на день.",
    "prayers": "🙏 <b>Молитвы</b>\nВсе молитвы из Notion. Чтобы добавить новую, напиши её сюда: "
    "первая строка станет названием.",
    "review": "🎙 <b>Разбор ситуаций</b>\nНаговори или напиши, что случилось. Я разложу по схеме 4 шага "
    "и запишу в журнал. Под карточкой разбора: «✏️ Дописать», «✅ Разобран», «➕ В инвентаризацию».",
    "practices": "🧭 <b>Практики</b>\nЧто я делаю утром, днём и вечером. Правится в Notion или через помощника.",
    "evening": "🌙 <b>Вечер: 10 шаг</b>\nВ {evening} здесь вечерняя молитва и вопросы итога дня. "
    "Отвечать можно голосом прямо сюда.",
    "sos": "🆘 <b>SOS</b>\nКогда тяжело, напиши сюда что угодно или /sos в любой теме.",
}


def _kb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[row for row in rows if row])


def _cb(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _short(text: str, limit: int = BUTTON_TEXT) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def pid(page_id: str) -> str:
    """Id страницы без дефисов: так короче в данных кнопки, а Notion понимает оба вида."""
    return page_id.replace("-", "")


def person_key(name: str) -> str:
    return hashlib.sha1(name.encode()).hexdigest()[:10]


def topic_url(chat_id: int, thread: int) -> str:
    internal = str(chat_id).removeprefix("-100")
    return f"https://t.me/c/{internal}/{thread}"


def topic_buttons(chat_id: int, topics: dict[str, int]) -> list[list[InlineKeyboardButton]]:
    buttons = [
        InlineKeyboardButton(text=label, url=topic_url(chat_id, topics[key]))
        for key, label in TOPIC_BUTTONS
        if topics.get(key)
    ]
    return [buttons[i : i + 3] for i in range(0, len(buttons), 3)]


# --- 4 шаг ----------------------------------------------------------------------


def group_by_person(pages: list[dict]) -> list[tuple[str, list[dict]]]:
    groups: dict[str, list[dict]] = {}
    for page in sorted(pages, key=lambda p: p["created_time"]):
        groups.setdefault(read(page, "Кто") or "Без имени", []).append(page)
    return list(groups.items())


def inventory_main(pages: list[dict]) -> tuple[str, InlineKeyboardMarkup]:
    groups = group_by_person(pages)
    counts = {status: 0 for status in STATUS_ICON}
    for page in pages:
        status = read(page, "Статус") or "Черновик"
        counts[status] = counts.get(status, 0) + 1
    text = (
        "📝 <b>4 шаг: инвентаризация обид</b>\n"
        f"Обид: {len(pages)} · людей: {len(groups)}\n"
        + " · ".join(f"{STATUS_ICON[s]} {s.lower()}: {counts[s]}" for s in STATUS_ICON)
        + "\n\nВыбери человека:"
    )
    people = []
    for name, items in groups:
        ready = all(read(p, "Статус") == READY for p in items)
        label = f"{'🟢 ' if ready else ''}{_short(name, 28)} · {len(items)}"
        people.append(_cb(label, f"ib:p:{person_key(name)}"))
    rows = [people[i : i + 2] for i in range(0, len(people), 2)]
    rows.append([_cb("➕ Новая обида", "ib:new"), _cb("🔍 Закономерности", "ib:pat")])
    return text, _kb(rows)


def inventory_person(name: str, items: list[dict]) -> tuple[str, InlineKeyboardMarkup]:
    lines = [f"📝 <b>{escape(name)}</b> · {len(items)}"]
    rows = []
    for number, page in enumerate(items, 1):
        icon = STATUS_ICON.get(read(page, "Статус") or "Черновик", "⚪")
        reason = read(page, "За что (причина)") or "Без названия"
        lines.append(f"{icon} {number}. {escape(_short(reason, 120))}")
        rows.append([_cb(f"{number}. {_short(reason)}", f"ib:r:{pid(page['id'])}")])
    rows.append([_cb("⬅️ Все люди", "ib:m")])
    return "\n".join(lines), _kb(rows)


def inventory_record(page: dict, siblings: list[dict]) -> tuple[str, InlineKeyboardMarkup]:
    text, _ = inventory_card(page)
    ids = [pid(p["id"]) for p in siblings]
    here = ids.index(pid(page["id"])) if pid(page["id"]) in ids else 0
    name = read(page, "Кто") or "Без имени"
    text += f"\n\n<i>Обида {here + 1} из {len(ids)}. Ответь на это сообщение, чтобы поправить словами.</i>"
    nav = []
    if here > 0:
        nav.append(_cb("◀️", f"ib:r:{ids[here - 1]}"))
    if here < len(ids) - 1:
        nav.append(_cb("▶️", f"ib:r:{ids[here + 1]}"))
    ready = read(page, "Статус") == READY
    rows = [
        nav,
        [
            _cb("↩️ Вернуть в черновик" if ready else "✅ Готово к 5 шагу", f"ib:ok:{pid(page['id'])}"),
            _cb("✏️ Поправить", f"ib:ed:{pid(page['id'])}"),
        ],
        [_cb(f"⬅️ {_short(name, 24)}", f"ib:p:{person_key(name)}"), _cb("🏠 Все люди", "ib:m")],
    ]
    return text, _kb(rows)


# --- пульт дня ------------------------------------------------------------------


def checkin_state(checkin_page: dict | None) -> dict[str, bool]:
    return {key: bool(checkin_page and read(checkin_page, label)) for key, label in CHECKIN_FIELDS.items()}


def _praying_buttons(praying: list[dict]) -> list[list[InlineKeyboardButton]]:
    buttons = []
    for page in praying[:8]:
        days = int(read(page, "Дней подряд") or 0)
        buttons.append(
            _cb(f"🙏 {_short(read(page, 'Имя') or '', 18)} {days}/{PRAYER_GOAL_DAYS}", f"pray:{pid(page['id'])}")
        )
    return [buttons[i : i + 2] for i in range(0, len(buttons), 2)]


def dashboard(
    today: date,
    sobriety: date | None,
    checkin_page: dict | None,
    praying: list[dict],
    chat_id: int,
    topics: dict[str, int],
) -> tuple[str, InlineKeyboardMarkup]:
    done = checkin_state(checkin_page)
    lines = [f"🕊 <b>Сегодня {today.strftime('%d.%m')}, {WEEKDAYS[today.weekday()]}</b>"]
    if sobriety:
        lines.append(f"Трезвость: <b>{plural_days((today - sobriety).days)}</b>")
    lines.append(f"Практики дня: {sum(done.values())} из {len(done)}")
    if praying:
        lines.append("Молюсь за: " + ", ".join(escape(read(p, "Имя") or "") for p in praying))
    toggles = [_cb(f"{'✅' if done[key] else '⬜'} {CHECKIN_LABELS[key]}", f"tg:{key}") for key in CHECKIN_FIELDS]
    rows = [toggles[i : i + 2] for i in range(0, len(toggles), 2)]
    rows += _praying_buttons(praying)
    rows.append([_cb("📖 Молитвы", "prl"), _cb("🆘 SOS", "sos"), _cb("🔄 Обновить", "dash")])
    rows += topic_buttons(chat_id, topics)
    return "\n".join(lines), _kb(rows)


# --- утро, вечер, молитвы ---------------------------------------------------------


def prayer_buttons(prayers: list[dict]) -> list[list[InlineKeyboardButton]]:
    return [[_cb(f"📖 {_short(read(p, 'Название') or 'Молитва')}", f"pr:{pid(p['id'])}")] for p in prayers]


def morning(
    today: date, sobriety: date | None, prayers: list[dict], praying: list[dict]
) -> tuple[str, InlineKeyboardMarkup]:
    lines = [f"☀️ <b>Доброе утро!</b> {today.strftime('%d.%m.%Y')}"]
    if sobriety:
        lines.append(f"Трезвость: <b>{plural_days((today - sobriety).days)}</b>")
    lines.append("\nУтренние молитвы открываются по кнопкам ниже.")
    if praying:
        lines.append("Сегодня молюсь за: " + ", ".join(escape(read(p, "Имя") or "") for p in praying))
        lines.append("Прошу для каждого всего того хорошего, чего хочу себе.")
    rows = prayer_buttons(prayers)
    rows += _praying_buttons(praying)
    rows.append([_cb("✅ Помолился утром", "chk:morning")])
    return "\n".join(lines), _kb(rows)


def evening(prayers: list[dict]) -> tuple[str, InlineKeyboardMarkup]:
    rows = prayer_buttons(prayers)
    rows.append([_cb("✅ Итог дня сделан", "chk:review"), _cb("🙏 Вечерняя молитва", "chk:evening")])
    rows.append([_cb("🍃 Трезвый день", "chk:sober"), _cb("🤝 Встреча или звонок", "chk:meeting")])
    return texts.EVENING_QUESTIONS, _kb(rows)


def prayer_list(prayers: list[dict]) -> tuple[str, InlineKeyboardMarkup]:
    return "📖 <b>Какую молитву открыть?</b>", _kb(prayer_buttons(prayers))


# --- промаливание ---------------------------------------------------------------

PERSON_ICON = {"Молюсь": "🔵", "Две недели прошли": "🟢", "Не начато": "⚪"}


def _by_created(pages: list[dict]) -> list[dict]:
    return sorted(pages, key=lambda p: p["created_time"])


def _days(page: dict) -> int:
    return int(read(page, "Дней подряд") or 0)


def _prayed(page: dict, today: str) -> bool:
    return (read(page, "Последняя молитва") or "")[:10] == today


def progress(days: int) -> str:
    days = min(days, PRAYER_GOAL_DAYS)
    return "🟩" * days + "⬜" * (PRAYER_GOAL_DAYS - days) + f" {days}/{PRAYER_GOAL_DAYS}"


def people_main(people: list[dict], today: str) -> tuple[str, InlineKeyboardMarkup]:
    praying = [p for p in _by_created(people) if read(p, "Молитва") == "Молюсь"]
    done = sum(1 for p in people if read(p, "Молитва") == "Две недели прошли")
    lines = [
        "👥 <b>Промаливание</b>",
        "Прошу для каждого всего того хорошего, чего хочу себе. 14 дней подряд.",
        "",
    ]
    if praying:
        marked = sum(1 for p in praying if _prayed(p, today))
        lines.append(f"Сегодня: <b>{marked} из {len(praying)}</b>. Нажми на имя, когда помолишься.")
    else:
        lines.append("Сейчас ни за кого не молюсь. Нажми «➕ Начать молиться за…».")
    lines.append(f"\nЗавершено: {done} · всего людей: {len(people)}")
    rows = []
    for page in praying:
        mark = "✅" if _prayed(page, today) else "⬜"
        name = _short(read(page, "Имя") or "", 24)
        rows.append(
            [
                _cb(f"{mark} {name} · {_days(page)}/{PRAYER_GOAL_DAYS}", f"pp:t:{pid(page['id'])}"),
                _cb("📖", f"pp:pr:{pid(page['id'])}"),
            ]
        )
    if len(praying) > 1:
        rows.append([_cb("📖 Одна молитва за всех", "pp:prall")])
    rows.append([_cb("➕ Начать молиться за…", "pp:add"), _cb("👥 Все люди", "pp:all")])
    rows.append([_cb("✍️ Добавить человека", "pp:new")])
    return "\n".join(lines), _kb(rows)


def people_all(people: list[dict]) -> tuple[str, InlineKeyboardMarkup]:
    text = f"👥 <b>Все люди</b> · {len(people)}\n🔵 молюсь · 🟢 две недели прошли · ⚪ не начато\n\nВыбери человека:"
    buttons = []
    for page in _by_created(people):
        icon = PERSON_ICON.get(read(page, "Молитва") or "Не начато", "⚪")
        days = _days(page)
        suffix = f" {days}/{PRAYER_GOAL_DAYS}" if days else ""
        buttons.append(_cb(f"{icon} {_short(read(page, 'Имя') or '', 22)}{suffix}", f"pp:v:{pid(page['id'])}"))
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    rows.append([_cb("🏠 Сегодня", "pp:m"), _cb("✍️ Добавить человека", "pp:new")])
    return text, _kb(rows)


def people_pick(people: list[dict]) -> tuple[str, InlineKeyboardMarkup]:
    candidates = [p for p in _by_created(people) if read(p, "Молитва") != "Молюсь"]
    text = "➕ <b>За кого начать молиться?</b>"
    if not candidates:
        text += "\nСейчас ты молишься за всех из списка."
    buttons = [
        _cb(
            f"{PERSON_ICON.get(read(p, 'Молитва') or 'Не начато', '⚪')} {_short(read(p, 'Имя') or '', 22)}",
            f"pp:s:{pid(p['id'])}",
        )
        for p in candidates
    ]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    rows.append([_cb("⬅️ Назад", "pp:m")])
    return text, _kb(rows)


def person_view(page: dict, today: str) -> tuple[str, InlineKeyboardMarkup]:
    name = read(page, "Имя") or "Без имени"
    status = read(page, "Молитва") or "Не начато"
    prayed = _prayed(page, today)
    lines = [
        f"👥 <b>{escape(name)}</b>",
        f"{PERSON_ICON.get(status, '⚪')} {escape(status)}",
        progress(_days(page)),
        f"Сегодня: {'✅ помолился' if prayed else '⬜ ещё нет'}",
    ]
    for label in ("Начало", "Категория", "В таблице обид как", "Заметки"):
        value = read(page, label)
        if value:
            lines.append(f"<b>{label}:</b> {escape(_short(str(value), 300))}")
    lines.append("\n<i>Ответь на это сообщение, чтобы что-то поправить словами.</i>")
    page_id = pid(page["id"])
    if status == "Молюсь":
        switch = _cb("⏸ Пауза", f"pp:x:{page_id}")
    elif status == "Две недели прошли":
        switch = _cb("🔁 Начать заново", f"pp:s:{page_id}")
    else:
        switch = _cb("▶️ Начать молиться", f"pp:s:{page_id}")
    rows = [
        [_cb("↩️ Снять отметку" if prayed else "✅ Помолился сегодня", f"pp:t:{page_id}")],
        [_cb("📖 Молитва с именем", f"pp:pr:{page_id}"), switch],
        [_cb("⬅️ Все люди", "pp:all"), _cb("🏠 Сегодня", "pp:m")],
    ]
    return "\n".join(lines), _kb(rows)


def personal_prayer(names: list[tuple[str, str]], text: str) -> tuple[str, InlineKeyboardMarkup]:
    """Молитва с подставленными именами и кнопки «помолился» для каждого. names: (id, имя)."""
    title = ", ".join(escape(name) for _id, name in names)
    buttons = [_cb(f"✅ Помолился за {_short(name, 24)}", f"pray:{pid(page_id)}") for page_id, name in names]
    return f"🙏 <b>За: {title}</b>\n\n{escape(text)}", _kb([[b] for b in buttons])
