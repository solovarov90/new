"""Сборка свойств страниц Notion из разборов, молитв и отметок. Без сети, поэтому легко тестируется."""

from . import notion as n
from .ai import Analysis
from .notion import read
from .render import PRAYER_GOAL_DAYS

CHECKIN_FIELDS = {
    "morning": "Утренняя молитва",
    "people": "Молитва за людей",
    "review": "Вечерний итог (10 шаг)",
    "evening": "Вечерняя молитва",
    "meeting": "Встреча или звонок",
    "sober": "Трезвый день",
}


def journal_properties(analysis: Analysis | None, story: str, kind: str, today: str) -> dict:
    """Свойства новой записи журнала. kind: «Голосовое» или «Текст»."""
    props = {
        "Дата": n.date(today),
        "Вид": n.select(kind),
        "Источник": n.select("Telegram"),
        "Статус": n.status("Новый"),
    }
    if analysis is None:
        first_line = story.strip().split("\n", 1)[0]
        props["Ситуация"] = n.title(first_line[:80] or "Запись")
        return props
    props.update(analysis_properties(analysis))
    props["Статус"] = n.status("Разбираю" if analysis.questions else "Разобран")
    return props


def analysis_properties(analysis: Analysis) -> dict:
    return {
        "Ситуация": n.title(analysis.situation or "Разбор"),
        "Кто": n.text(analysis.who),
        "Чувства": n.multi(analysis.feelings),
        "Задеты": n.multi(analysis.affected),
        "Дефекты": n.multi(analysis.defects),
        "Эгоизм": n.text(analysis.selfishness),
        "Нечестность": n.text(analysis.dishonesty),
        "Корысть": n.text(analysis.self_seeking),
        "Страх (моя роль)": n.text(analysis.fear),
        "Моя роль": n.text(analysis.my_role),
        "Следующий шаг": n.text(analysis.next_step),
        "Уточнить": n.text("\n".join(analysis.questions)),
    }


def inventory_from_journal(page: dict) -> dict:
    """Строка инвентаризации из записи журнала."""
    who = read(page, "Кто") or ""
    props = {
        "За что (причина)": n.title(read(page, "Ситуация") or "Разбор"),
        "Задеты": n.multi(read(page, "Задеты") or []),
        "Эгоизм": n.text(read(page, "Эгоизм") or ""),
        "Нечестность": n.text(read(page, "Нечестность") or ""),
        "Корысть": n.text(read(page, "Корысть") or ""),
        "Страх (моя роль)": n.text(read(page, "Страх (моя роль)") or ""),
        "Заметка": n.text(f"Из журнала: {page.get('url', '')}"),
        "Статус": n.status("Черновик"),
        "Источник": n.select("Голосовое" if read(page, "Вид") == "Голосовое" else "Текст"),
    }
    if who:
        props["Кто"] = n.select(who[:100])
    return props


def prayer_from_message(message_text: str) -> tuple[dict, str]:
    """Первая строка сообщения становится названием молитвы, остальное её текстом."""
    head, _, body = message_text.strip().partition("\n")
    props = {"Название": n.title(head.strip()[:200]), "Когда": n.select("В течение дня")}
    return props, (body.strip() or head.strip())


def person_from_name(name: str) -> dict:
    return {"Имя": n.title(name.strip()[:200]), "Молитва": n.status("Не начато")}


LAST_PRAYER = "Последняя молитва"
PRAYING = "Молюсь"
DONE = "Две недели прошли"
NOT_STARTED = "Не начато"
PAUSED = "Пауза"


def prayed_today(page: dict, today: str) -> bool:
    return (read(page, LAST_PRAYER) or "")[:10] == today


def person_after_prayer(page: dict, today: str) -> tuple[dict, int]:
    """Отметка «помолился сегодня»: счётчик дней растёт, через 14 дней молитва завершена."""
    days = int(read(page, "Дней подряд") or 0) + 1
    props = {
        "Дней подряд": n.number(days),
        "Молитва": n.status(DONE if days >= PRAYER_GOAL_DAYS else PRAYING),
        LAST_PRAYER: n.date(today),
    }
    if not read(page, "Начало"):
        props["Начало"] = n.date(today)
    return props, days


def person_undo_prayer(page: dict) -> tuple[dict, int]:
    """Снимает сегодняшнюю отметку, если нажал по ошибке."""
    days = max(int(read(page, "Дней подряд") or 0) - 1, 0)
    status = DONE if days >= PRAYER_GOAL_DAYS else PRAYING
    return {"Дней подряд": n.number(days), "Молитва": n.status(status), LAST_PRAYER: n.date(None)}, days


def person_start(page: dict, today: str) -> dict:
    """Вернуть человека с паузы в ежедневный список. Счётчик дней продолжается."""
    days = int(read(page, "Дней подряд") or 0)
    props = {"Молитва": n.status(DONE if days >= PRAYER_GOAL_DAYS else PRAYING)}
    if not read(page, "Начало"):
        props["Начало"] = n.date(today)
    return props


def person_pause(page: dict) -> dict:
    """Пауза: человек уходит из ежедневного списка, счётчик дней сохраняется."""
    return {"Молитва": n.status(PAUSED)}


def checkin_properties(field_key: str) -> dict:
    return {CHECKIN_FIELDS[field_key]: n.checkbox(True)}


def plural_days(count: int) -> str:
    if count % 10 == 1 and count % 100 != 11:
        word = "день"
    elif 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        word = "дня"
    else:
        word = "дней"
    return f"{count} {word}"
