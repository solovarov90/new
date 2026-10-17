"""Настройки бота. Все значения берутся из переменных окружения (файл .env)."""

import os
import re
from dataclasses import dataclass, field
from datetime import date


class ConfigError(RuntimeError):
    pass


def _get(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or default


def _require(name: str) -> str:
    value = _get(name)
    if not value:
        raise ConfigError(f"Не задана переменная окружения {name}")
    return value


def _time(name: str, default: str) -> str:
    value = _get(name, default)
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value):
        raise ConfigError(f"{name} должно быть в формате ЧЧ:ММ, сейчас {value!r}")
    return value


def _int(name: str, default: int | None = None) -> int | None:
    value = _get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} должно быть числом, сейчас {value!r}") from exc


def _require_int(name: str) -> int:
    _require(name)
    return _int(name)


@dataclass(frozen=True)
class Databases:
    inventory: str
    journal: str
    people: str
    prayers: str
    practices: str
    checkin: str


@dataclass(frozen=True)
class Config:
    bot_token: str
    chat_id: int
    owner_id: int | None
    notion_token: str
    databases: Databases
    gemini_api_key: str | None
    gemini_model: str
    timezone: str
    morning_time: str
    evening_time: str
    sobriety_date: date | None
    sponsor_contact: str | None
    sync_interval: int
    state_path: str
    topic_overrides: dict[str, int] = field(default_factory=dict)


def load_config() -> Config:
    sobriety = _get("SOBRIETY_DATE")
    try:
        sobriety_date = date.fromisoformat(sobriety) if sobriety else None
    except ValueError as exc:
        raise ConfigError(f"SOBRIETY_DATE должна быть в формате ГГГГ-ММ-ДД, сейчас {sobriety!r}") from exc

    # TOPIC_REVIEW=2 и т.п. позволяют привязать бота к уже созданной теме вместо новой.
    overrides = {}
    for name, value in os.environ.items():
        if name.startswith("TOPIC_") and value.strip():
            overrides[name[len("TOPIC_") :].lower()] = _int(name)

    return Config(
        bot_token=_require("TELEGRAM_BOT_TOKEN"),
        chat_id=_require_int("TELEGRAM_CHAT_ID"),
        owner_id=_int("OWNER_ID"),
        notion_token=_require("NOTION_TOKEN"),
        databases=Databases(
            inventory=_require("NOTION_DB_INVENTORY"),
            journal=_require("NOTION_DB_JOURNAL"),
            people=_require("NOTION_DB_PEOPLE"),
            prayers=_require("NOTION_DB_PRAYERS"),
            practices=_require("NOTION_DB_PRACTICES"),
            checkin=_require("NOTION_DB_CHECKIN"),
        ),
        gemini_api_key=_get("GEMINI_API_KEY"),
        gemini_model=_get("GEMINI_MODEL", "gemini-2.5-flash"),
        timezone=_get("TIMEZONE", "Asia/Ho_Chi_Minh"),
        morning_time=_time("MORNING_TIME", "06:30"),
        evening_time=_time("EVENING_TIME", "22:30"),
        sobriety_date=sobriety_date,
        sponsor_contact=_get("SPONSOR_CONTACT"),
        sync_interval=_int("SYNC_INTERVAL", 60),
        state_path=_get("STATE_PATH", "state.db"),
        topic_overrides=overrides,
    )
