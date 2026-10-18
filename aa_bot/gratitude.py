"""Вечерние благодарности 5·4·3: пять Богу, четыре людям, три себе. Без сети, поэтому легко тестируется.

Записи дня лежат в локальном состоянии под ключом «gratitude:ГГГГ-ММ-ДД», а когда все двенадцать
собраны, бот переносит их в «Ежедневный чек-ин» в Notion и ставит отметку «Благодарности».
"""

import json
import re
from dataclasses import dataclass, field
from datetime import date
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .state import State

SECTIONS = ("god", "people", "self")
GOALS = {"god": 5, "people": 4, "self": 3}
TITLES = {"god": "🙏 Богу", "people": "🤝 Людям", "self": "🌱 Себе"}
ASK = {
    "god": "За что ты сегодня благодарен Богу?",
    "people": "Кому из людей ты сегодня благодарен и за что?",
    "self": "За что ты можешь сегодня поблагодарить себя?",
}
# Подсказки, если в голову ничего не идёт. Работают и без Gemini.
HINTS = {
    "god": [
        "за ещё один трезвый день",
        "за то, что проснулся живым и здоровым",
        "за крышу над головой, еду и воду",
        "за момент, когда удалось остановиться и не сорваться",
        "за людей, которых Он поставил рядом",
        "за программу и группу",
        "за то, что получилось помолиться",
    ],
    "people": [
        "кто позвонил или написал",
        "кто выслушал, не перебивая",
        "кто сделал свою работу, и мне стало легче (продавец, водитель, коллега)",
        "спонсору и товарищам по программе",
        "семье, даже если сегодня было непросто",
        "человеку, который меня задел: он показал, над чем мне работать",
    ],
    "self": [
        "что не выпил сегодня",
        "что сделал зарядку, поел, лёг вовремя",
        "что сдержался и не сказал лишнего",
        "что сделал то, что откладывал",
        "что попросил о помощи",
        "что был честен, хотя было неудобно",
        "что помолился и сделал вечерний итог",
    ],
}

KEY = "gratitude:"
# Строки вида «1. …», «- …», «• …» в начале убираем, чтобы нумерация была одна, наша.
BULLET = re.compile(r"^\s*(?:\d+[.)]\s*|[-–—•*]\s*)")


def key(day: date | str) -> str:
    return KEY + (day if isinstance(day, str) else day.isoformat())


@dataclass
class Gratitude:
    day: str
    god: list[str] = field(default_factory=list)
    people: list[str] = field(default_factory=list)
    self_: list[str] = field(default_factory=list)
    message_id: int | None = None
    active: bool = True
    saved: bool = False

    def items(self, section: str) -> list[str]:
        return self.self_ if section == "self" else getattr(self, section)

    def current(self) -> str | None:
        """Раздел, который заполняем сейчас: первый незаполненный по порядку Бог → люди → себе."""
        for section in SECTIONS:
            if len(self.items(section)) < GOALS[section]:
                return section
        return None

    @property
    def complete(self) -> bool:
        return self.current() is None

    @property
    def total(self) -> int:
        return sum(min(len(self.items(s)), GOALS[s]) for s in SECTIONS)

    def add(self, items: list[str], section: str | None = None) -> list[tuple[str, str]]:
        """Добавляет записи. Без раздела каждая идёт в текущий незаполненный, с переходом в следующий."""
        added = []
        for item in items:
            target = section or self.current() or "self"
            self.items(target).append(item)
            added.append((target, item))
        return added

    def add_sorted(self, sorted_items: dict[str, list[str]]) -> list[tuple[str, str]]:
        """Записи, уже разложенные по разделам (например, Gemini понял «спасибо маме» как людям)."""
        added = []
        for section in SECTIONS:
            for item in sorted_items.get(section) or []:
                self.items(section).append(item)
                added.append((section, item))
        return added

    def undo(self) -> str | None:
        """Убирает последнюю добавленную запись. Возвращает её текст."""
        for section in reversed(SECTIONS):
            if self.items(section):
                return self.items(section).pop()
        return None

    def to_json(self) -> str:
        return json.dumps(
            {
                "day": self.day,
                "god": self.god,
                "people": self.people,
                "self": self.self_,
                "message_id": self.message_id,
                "active": self.active,
                "saved": self.saved,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, raw: str) -> "Gratitude":
        data = json.loads(raw)
        return cls(
            day=data["day"],
            god=list(data.get("god") or []),
            people=list(data.get("people") or []),
            self_=list(data.get("self") or []),
            message_id=data.get("message_id"),
            active=bool(data.get("active", True)),
            saved=bool(data.get("saved")),
        )


def load(state: State, day: date) -> Gratitude | None:
    raw = state.get(key(day))
    return Gratitude.from_json(raw) if raw else None


def save(state: State, gratitude: Gratitude) -> None:
    state.set(key(gratitude.day), gratitude.to_json())


def split_items(text: str) -> list[str]:
    """Каждая непустая строка сообщения — одна благодарность."""
    items = []
    for line in (text or "").split("\n"):
        line = BULLET.sub("", line).strip()
        if line:
            items.append(line)
    return items


def hints(section: str, seed: int) -> list[str]:
    """Три подсказки для раздела. seed сдвигает выбор, чтобы при повторном нажатии были другие."""
    pool = HINTS[section]
    return [pool[(seed * 3 + i) % len(pool)] for i in range(3)]


# --- экраны ---------------------------------------------------------------------


def _cb(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def _section_lines(gratitude: Gratitude, section: str) -> list[str]:
    items = gratitude.items(section)
    done = len(items) >= GOALS[section]
    head = f"<b>{TITLES[section]}</b> · {len(items)}/{GOALS[section]}" + (" ✅" if done else "")
    return [head] + [f"{n}. {escape(item)}" for n, item in enumerate(items, 1)]


def screen(gratitude: Gratitude, extra: str = "") -> tuple[str, InlineKeyboardMarkup]:
    day = date.fromisoformat(gratitude.day)
    lines = [f"✨ <b>Благодарности за {day.strftime('%d.%m')}</b> · {gratitude.total} из 12", ""]
    for section in SECTIONS:
        lines += _section_lines(gratitude, section) + [""]
    section = gratitude.current()
    rows: list[list[InlineKeyboardButton]] = []
    if gratitude.saved:
        lines.append("🕊 <b>Все двенадцать записаны в Notion.</b> Спасибо за этот день.")
    elif section is None:
        lines.append("🎉 Все двенадцать собраны.")
        rows.append([_cb("💾 Сохранить в Notion", "gr:save")])
    elif not gratitude.active:
        lines.append("⏸ Отложено. Нажми «▶️ Продолжить», когда будешь готов.")
        rows.append([_cb("▶️ Продолжить", "gr:go")])
    else:
        left = GOALS[section] - len(gratitude.items(section))
        lines.append(f"👉 <b>{ASK[section]}</b> Ещё {left}.")
        lines.append(
            "<i>Напиши или наговори сюда, в эту тему. Можно сразу несколько, каждую с новой строки, "
            "или одним голосовым про всё сразу: я разложу.</i>"
        )
        rows.append([_cb("💡 Подскажи", "gr:hint"), _cb("↩️ Убрать последнюю", "gr:undo")])
        rows.append([_cb("⏸ Отложить", "gr:stop")])
    if extra:
        lines += ["", extra]
    return "\n".join(lines).strip(), InlineKeyboardMarkup(inline_keyboard=rows)


def hint_text(section: str, seed: int, personal: list[str] | None = None) -> str:
    lines = [f"💡 <b>{ASK[section]}</b>"]
    if personal:
        lines += [f"• {escape(item)}" for item in personal]
    else:
        lines += [f"• {escape(item)}" for item in hints(section, seed)]
    lines.append("<i>Это только подсказки. Напиши своими словами то, что откликается.</i>")
    return "\n".join(lines)


def notion_text(gratitude: Gratitude) -> str:
    """Текст для страницы чек-ина в Notion."""
    day = date.fromisoformat(gratitude.day)
    lines = [f"Благодарности за {day.strftime('%d.%m.%Y')}"]
    names = {"god": "Богу", "people": "Людям", "self": "Себе"}
    for section in SECTIONS:
        lines.append(f"{names[section]}:")
        lines += [f"{n}. {item}" for n, item in enumerate(gratitude.items(section), 1)]
    return "\n".join(lines)
