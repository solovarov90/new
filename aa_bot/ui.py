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
    "people": "👥 <b>Промаливание</b>\nКарточки людей. «🙏 Помолился сегодня» считает дни до 14. "
    "Чтобы добавить человека, напиши сюда его имя.",
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
