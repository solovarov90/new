"""Как страницы Notion выглядят в Telegram: текст карточки (HTML) и кнопки."""

from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .notion import read

MESSAGE_LIMIT = 4096
FIELD_LIMIT = 900
PRAYER_GOAL_DAYS = 14


def _clip(value: str, limit: int = FIELD_LIMIT) -> str:
    value = (value or "").strip()
    return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"


def _field(label: str, value) -> str:
    if isinstance(value, list):
        value = ", ".join(value)
    if not value:
        return ""
    return f"<b>{escape(label)}:</b> {escape(_clip(str(value)))}"


def _link(page: dict) -> str:
    return f'<a href="{escape(page.get("url", ""), quote=True)}">Открыть в Notion</a>'


def _card(*parts: str, page: dict) -> str:
    body = "\n".join(p for p in parts if p)
    link = _link(page)
    room = MESSAGE_LIMIT - len(link) - 2
    if len(body) > room:
        body = body[: room - 1] + "…"
    return f"{body}\n\n{link}"


def _buttons(*rows: list[tuple[str, str]]) -> InlineKeyboardMarkup | None:
    keyboard = [[InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows if row]
    return InlineKeyboardMarkup(inline_keyboard=keyboard) if keyboard else None


def prayer(page: dict, body: str) -> tuple[str, InlineKeyboardMarkup | None]:
    name = read(page, "Название") or "Без названия"
    when = read(page, "Когда")
    head = f"🙏 <b>{escape(name)}</b>" + (f"\n<i>{escape(when)}</i>" if when else "")
    # Молитву показываем целиком, без обрезки по FIELD_LIMIT.
    return _card(head, "", escape(body.strip()), page=page), None


def practice(page: dict, _body: str = "") -> tuple[str, InlineKeyboardMarkup | None]:
    name = read(page, "Практика") or "Без названия"
    active = read(page, "Активна")
    head = f"🧭 <b>{escape(name)}</b>" + ("" if active else " <i>(не активна)</i>")
    return (
        _card(
            head,
            _field("Когда", read(page, "Когда")),
            _field("Шаг", read(page, "Шаг")),
            "",
            escape(_clip(read(page, "Описание") or "")),
            page=page,
        ),
        None,
    )


def person(page: dict, _body: str = "") -> tuple[str, InlineKeyboardMarkup | None]:
    name = read(page, "Имя") or "Без имени"
    days = int(read(page, "Дней подряд") or 0)
    progress = f"{days} из {PRAYER_GOAL_DAYS} дней" if days else ""
    text = _card(
        f"👥 <b>{escape(name)}</b>",
        _field("Молитва", read(page, "Молитва")),
        _field("Прогресс", progress),
        _field("Начало", read(page, "Начало")),
        _field("Категория", read(page, "Категория")),
        _field("В таблице обид", read(page, "В таблице обид как")),
        _field("Заметки", read(page, "Заметки")),
        page=page,
    )
    return text, _buttons([("🙏 Помолился сегодня", f"pray:{page['id']}")])


def inventory(page: dict, _body: str = "") -> tuple[str, InlineKeyboardMarkup | None]:
    who = read(page, "Кто") or "Кто-то"
    reason = read(page, "За что (причина)") or ""
    text = _card(
        f"📝 <b>{escape(who)}</b> — {escape(_clip(reason, 300))}",
        _field("Статус", read(page, "Статус")),
        _field("Задеты", read(page, "Задеты")),
        "",
        _field("Эгоизм", read(page, "Эгоизм")),
        _field("Нечестность", read(page, "Нечестность")),
        _field("Корысть", read(page, "Корысть")),
        _field("Страх", read(page, "Страх (моя роль)")),
        _field("Заметка", read(page, "Заметка")),
        page=page,
    )
    return text, None


def journal(page: dict, _body: str = "") -> tuple[str, InlineKeyboardMarkup | None]:
    situation = read(page, "Ситуация") or "Разбор"
    current = read(page, "Статус") or ""
    text = _card(
        f"🎙 <b>{escape(_clip(situation, 300))}</b>",
        _field("Кто", read(page, "Кто")),
        _field("Чувства", read(page, "Чувства")),
        _field("Задеты", read(page, "Задеты")),
        "",
        _field("Эгоизм", read(page, "Эгоизм")),
        _field("Нечестность", read(page, "Нечестность")),
        _field("Корысть", read(page, "Корысть")),
        _field("Страх", read(page, "Страх (моя роль)")),
        _field("Моя роль", read(page, "Моя роль")),
        _field("Следующий шаг", read(page, "Следующий шаг")),
        _field("Уточнить", read(page, "Уточнить")),
        _field("Статус", current),
        page=page,
    )
    if current == "Внесён в инвентаризацию":
        return text, None
    work = [] if current == "Разобран" else [("✏️ Дописать", f"jed:{page['id']}"), ("✅ Разобран", f"jok:{page['id']}")]
    return text, _buttons(work, [("➕ В 4 шаг", f"inv:{page['id']}")])
