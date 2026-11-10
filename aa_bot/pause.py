"""Пауза: /stop останавливает сообщения по расписанию и счёт дней трезвости, /resume включает их обратно.

Отметка паузы лежит в локальном состоянии бота, поэтому переживает перезапуск.
"""

from datetime import date, datetime

from .state import State

KEY = "paused"


def since(state: State) -> str | None:
    """Когда поставили паузу (ГГГГ-ММ-ДДTЧЧ:ММ+ЧЧ:ММ) или None, если всё работает."""
    return state.get(KEY)


def stop(state: State, now: datetime) -> bool:
    """Ставит паузу. False, если пауза уже стоит."""
    if since(state):
        return False
    state.set(KEY, now.isoformat(timespec="minutes"))
    return True


def resume(state: State) -> bool:
    """Снимает паузу. False, если паузы не было."""
    if not since(state):
        return False
    state.delete(KEY)
    return True


def sober_days(sobriety: date | None, state: State, today: date) -> int | None:
    """Дней трезвости для показа. None, если дата не задана или стоит пауза."""
    if sobriety is None or since(state):
        return None
    return (today - sobriety).days


def since_text(state: State) -> str:
    """«с 10.11 14:30» для ответов бота."""
    value = since(state)
    if not value:
        return ""
    return f"с {datetime.fromisoformat(value).strftime('%d.%m %H:%M')}"
