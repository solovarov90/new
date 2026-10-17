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


def dashboard(
    today: date,
    sobriety: date | None,
    checkin_page: dict | None,
    people: list[dict],
    chat_id: int,
    topics: dict[str, int],
) -> tuple[str, InlineKeyboardMarkup]:
    done = checkin_state(checkin_page)
    lines = [f"🕊 <b>Сегодня {today.strftime('%d.%m')}, {WEEKDAYS[today.weekday()]}</b>"]
    if sobriety:
        lines.append(f"Трезвость: <b>{plural_days((today - sobriety).days)}</b>")
    lines.append(f"Практики дня: {sum(done.values())} из {len(done)}")
    marked, total = people_today(people, today.isoformat())
    if total:
        lines.append(f"Промаливание: {marked} из {total}")
    toggles = [_cb(f"{'✅' if done[key] else '⬜'} {CHECKIN_LABELS[key]}", f"tg:{key}") for key in CHECKIN_FIELDS]
    rows = [toggles[i : i + 2] for i in range(0, len(toggles), 2)]
    if total:
        rows.append([_cb(f"🙏 Промаливание по очереди · {marked}/{total}", "pw:first")])
    rows.append([_cb("📖 Молитвы", "prl"), _cb("🆘 SOS", "sos"), _cb("🔄 Обновить", "dash")])
    rows += topic_buttons(chat_id, topics)
    return "\n".join(lines), _kb(rows)


# --- утро, вечер, молитвы ---------------------------------------------------------


def prayer_buttons(prayers: list[dict]) -> list[list[InlineKeyboardButton]]:
    return [[_cb(f"📖 {_short(read(p, 'Название') or 'Молитва')}", f"pr:{pid(p['id'])}")] for p in prayers]


def morning(
    today: date, sobriety: date | None, prayers: list[dict], people: list[dict]
) -> tuple[str, InlineKeyboardMarkup]:
    lines = [f"☀️ <b>Доброе утро!</b> {today.strftime('%d.%m.%Y')}"]
    if sobriety:
        lines.append(f"Трезвость: <b>{plural_days((today - sobriety).days)}</b>")
    lines.append("\nУтренние молитвы открываются по кнопкам ниже.")
    _marked, total = people_today(people, today.isoformat())
    if total:
        lines.append(f"Промаливание: {total} человек, по очереди с молитвой для каждого.")
    rows = prayer_buttons(prayers)
    if total:
        rows.append([_cb("🙏 Промаливание по очереди", "pw:first")])
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

PAUSED = "Пауза"
PERSON_ICON = {"Молюсь": "🔵", "Две недели прошли": "🟢", "Не начато": "⚪", PAUSED: "⏸"}


def _by_created(pages: list[dict]) -> list[dict]:
    return sorted(pages, key=lambda p: p["created_time"])


def _days(page: dict) -> int:
    return int(read(page, "Дней подряд") or 0)


def _prayed(page: dict, today: str) -> bool:
    return (read(page, "Последняя молитва") or "")[:10] == today


def daily_list(people: list[dict]) -> list[dict]:
    """Все, за кого молюсь каждый день: весь список, кроме поставленных на паузу."""
    return [p for p in _by_created(people) if read(p, "Молитва") != PAUSED]


def progress(days: int) -> str:
    shown = min(days, PRAYER_GOAL_DAYS)
    return "🟩" * shown + "⬜" * (PRAYER_GOAL_DAYS - shown) + f" {days}/{PRAYER_GOAL_DAYS}"


def people_today(people: list[dict], today: str) -> tuple[int, int]:
    daily = daily_list(people)
    return sum(1 for p in daily if _prayed(p, today)), len(daily)


def people_main(people: list[dict], today: str) -> tuple[str, InlineKeyboardMarkup]:
    daily = daily_list(people)
    marked, total = people_today(people, today)
    paused = len(people) - total
    lines = [
        "👥 <b>Промаливание</b>",
        "Каждый день прошу для каждого всего того хорошего, чего хочу себе.",
        "",
        f"Сегодня: <b>{marked} из {total}</b>" + (" 🎉" if total and marked == total else ""),
        "Нажми на имя, чтобы отметить, или «🙏 По очереди»: молитва с именем для каждого подряд.",
    ]
    names = []
    for page in daily:
        mark = "✅" if _prayed(page, today) else "⬜"
        done = " 🟢" if _days(page) >= PRAYER_GOAL_DAYS else ""
        names.append(_cb(f"{mark} {_short(read(page, 'Имя') or '', 22)}{done}", f"pp:t:{pid(page['id'])}"))
    rows = [names[i : i + 2] for i in range(0, len(names), 2)]
    if daily:
        rows.append([_cb("🙏 По очереди ▶️", "pw:first"), _cb("📖 Одна за всех", "pp:prall")])
    rows.append([_cb("👥 Все люди", "pp:all")] + ([_cb(f"⏸ На паузе · {paused}", "pp:add")] if paused else []))
    rows.append([_cb("✍️ Добавить человека", "pp:new")])
    return "\n".join(lines), _kb(rows)


def people_all(people: list[dict]) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        f"👥 <b>Все люди</b> · {len(people)}\n"
        f"🟢 больше {PRAYER_GOAL_DAYS} дней · 🔵 молюсь · ⚪ ещё не начинал · ⏸ пауза\n\nВыбери человека:"
    )
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
    """Люди на паузе: нажатие возвращает человека в ежедневный список."""
    paused = [p for p in _by_created(people) if read(p, "Молитва") == PAUSED]
    text = "⏸ <b>На паузе</b>\nНажми на имя, чтобы вернуть человека в ежедневный список."
    if not paused:
        text = "⏸ <b>На паузе никого нет</b>\nКаждый день молюсь за всех из списка."
    buttons = [_cb(f"▶️ {_short(read(p, 'Имя') or '', 24)}", f"pp:s:{pid(p['id'])}") for p in paused]
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
    if status == PAUSED:
        switch = _cb("▶️ Вернуть в список", f"pp:s:{page_id}")
    else:
        switch = _cb("⏸ Пауза", f"pp:x:{page_id}")
    rows = [
        [_cb("↩️ Снять отметку" if prayed else "✅ Помолился сегодня", f"pp:t:{page_id}")],
        [_cb("📖 Молитва с именем", f"pp:pr:{page_id}"), switch],
        [_cb("⬅️ Все люди", "pp:all"), _cb("🏠 Сегодня", "pp:m")],
    ]
    return "\n".join(lines), _kb(rows)


def personal_prayer(names: list[tuple[str, str]], text: str) -> tuple[str, InlineKeyboardMarkup]:
    """Одна молитва сразу за нескольких людей и кнопки «помолился» для каждого. names: (id, имя)."""
    title = ", ".join(escape(name) for _id, name in names)
    label = "✅ Помолился" if len(names) == 1 else "✅ {}"
    buttons = [_cb(label.format(_short(name, 24)), f"pray:{pid(page_id)}") for page_id, name in names]
    return f"🙏 <b>За: {title}</b>\n\n{escape(text)}", _kb([buttons[i : i + 2] for i in range(0, len(buttons), 2)])


def prayer_walk(page: dict, daily: list[dict], text: str, today: str) -> tuple[str, InlineKeyboardMarkup]:
    """Молитва за одного человека с переходом к следующему в том же сообщении."""
    ids = [pid(p["id"]) for p in daily]
    here = pid(page["id"])
    name = escape(read(page, "Имя") or "")
    prayed = _prayed(page, today)
    marked = sum(1 for p in daily if _prayed(p, today))
    if here in ids:
        position = ids.index(here)
        head = f"🙏 <b>{position + 1} из {len(ids)} · {name}</b>"
    else:
        position, head = -1, f"🙏 <b>{name}</b>"
    head += (" ✅" if prayed else "") + f"\n<i>Сегодня отмечено: {marked} из {len(ids)}</i>"
    rows = [[_cb("▶️ Дальше" if prayed else "✅ Помолился · дальше ▶️", f"pwd:{here}")]]
    if position >= 0:
        nav = []
        if position > 0:
            nav.append(_cb("◀️", f"pw:{ids[position - 1]}"))
        if position < len(ids) - 1:
            nav.append(_cb("Пропустить ▶️", f"pw:{ids[position + 1]}"))
        rows.append(nav)
    return f"{head}\n\n{escape(text)}", _kb(rows)


def prayer_walk_done(daily: list[dict], today: str) -> tuple[str, InlineKeyboardMarkup]:
    marked = sum(1 for p in daily if _prayed(p, today))
    left = [escape(read(p, "Имя") or "") for p in daily if not _prayed(p, today)]
    if left:
        text = f"🙏 Отмечено {marked} из {len(daily)}. Ещё остались: {', '.join(left)}."
        rows = [[_cb("▶️ Продолжить", "pw:first")]]
    else:
        text = f"🎉 <b>На сегодня всё:</b> помолился за всех, {marked} из {len(daily)}. Спасибо за эту практику."
        rows = []
    return text, _kb(rows)


def next_unprayed(daily: list[dict], today: str, after: str | None = None) -> dict | None:
    """Следующий после after, за кого сегодня ещё не молился; по кругу с начала списка."""
    ids = [pid(p["id"]) for p in daily]
    start = ids.index(after) + 1 if after in ids else 0
    for page in daily[start:] + daily[:start]:
        if not _prayed(page, today):
            return page
    return None
