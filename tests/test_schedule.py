from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from aa_bot.schedule import Scheduler
from aa_bot.state import State

TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def make(tmp_path):
    app = SimpleNamespace(
        topics={"morning": 1, "evening": 2},
        config=SimpleNamespace(morning_time="06:30", evening_time="22:30"),
        state=State(str(tmp_path / "s.db")),
    )
    scheduler = Scheduler(app)
    calls = []

    async def morning(day):
        calls.append(("morning", day))

    async def evening(day):
        calls.append(("evening", day))

    scheduler.morning, scheduler.evening = morning, evening
    return scheduler, calls


async def test_morning_fires_once_inside_window(tmp_path):
    scheduler, calls = make(tmp_path)
    await scheduler.tick(datetime(2026, 10, 18, 6, 29, tzinfo=TZ))
    assert calls == []
    await scheduler.tick(datetime(2026, 10, 18, 6, 31, tzinfo=TZ))
    await scheduler.tick(datetime(2026, 10, 18, 6, 45, tzinfo=TZ))
    assert [c[0] for c in calls] == ["morning"]


async def test_missed_window_is_not_sent_late(tmp_path):
    scheduler, calls = make(tmp_path)
    await scheduler.tick(datetime(2026, 10, 18, 9, 0, tzinfo=TZ))
    assert calls == []


async def test_next_day_fires_again(tmp_path):
    scheduler, calls = make(tmp_path)
    await scheduler.tick(datetime(2026, 10, 18, 22, 40, tzinfo=TZ))
    await scheduler.tick(datetime(2026, 10, 19, 22, 40, tzinfo=TZ))
    assert [c[0] for c in calls] == ["evening", "evening"]
