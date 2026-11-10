from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from aa_bot import pause, ui
from aa_bot.agent import Agent
from aa_bot.state import State
from aa_bot.workspace import Workspace

from .fakes import FakeNotion
from .test_agent import SCHEMAS, FakeModels, _call, _text, make_config
from .test_panels import make as make_panels
from .test_schedule import make as make_scheduler

TZ = ZoneInfo("Asia/Ho_Chi_Minh")
NOW = datetime(2026, 11, 10, 14, 30, tzinfo=TZ)
SOBRIETY = date(2026, 6, 15)


def test_stop_and_resume_switch_once(tmp_path):
    state = State(str(tmp_path / "s.db"))
    assert pause.since(state) is None
    assert pause.stop(state, NOW)
    assert not pause.stop(state, NOW)  # второй /stop ничего не меняет
    assert pause.since_text(state) == "с 10.11 14:30"
    assert pause.resume(state)
    assert not pause.resume(state)
    assert pause.since(state) is None


def test_pause_survives_restart(tmp_path):
    path = str(tmp_path / "s.db")
    pause.stop(State(path), NOW)
    assert pause.since(State(path)) == "2026-11-10T14:30+07:00"


def test_sober_days_are_hidden_on_pause_and_counted_from_the_date_after(tmp_path):
    state = State(str(tmp_path / "s.db"))
    assert pause.sober_days(SOBRIETY, state, date(2026, 10, 17)) == 124
    assert pause.sober_days(None, state, date(2026, 10, 17)) is None
    pause.stop(state, NOW)
    assert pause.sober_days(SOBRIETY, state, date(2026, 10, 17)) is None
    pause.resume(state)
    # Дни паузы не вычитаются: счёт снова идёт от SOBRIETY_DATE.
    assert pause.sober_days(SOBRIETY, state, date(2026, 10, 20)) == 127


async def test_scheduler_is_silent_on_pause_and_sends_again_after_resume(tmp_path):
    scheduler, calls = make_scheduler(tmp_path)
    pause.stop(scheduler.app.state, NOW)
    await scheduler.tick(datetime(2026, 10, 18, 6, 31, tzinfo=TZ))
    await scheduler.tick(datetime(2026, 10, 18, 22, 31, tzinfo=TZ))
    assert calls == []

    pause.resume(scheduler.app.state)
    # Утреннее окно уже прошло, его не догоняем. Вечер внутри окна уходит сразу после /resume.
    await scheduler.tick(datetime(2026, 10, 18, 22, 40, tzinfo=TZ))
    await scheduler.tick(datetime(2026, 10, 19, 6, 31, tzinfo=TZ))
    assert [c[0] for c in calls] == ["evening", "morning"]


def test_dashboard_shows_pause_instead_of_days():
    text, _keyboard = ui.dashboard(date(2026, 10, 17), None, None, [], -100, {}, paused=True)
    assert ui.PAUSED_LINE in text
    assert "Трезвость" not in text


async def test_pinned_dashboard_follows_pause(tmp_path):
    panels, app, _bot, _notion = make_panels(tmp_path)
    pause.stop(app.state, NOW)
    text, _keyboard = await panels.dashboard_view()
    assert "Трезвость" not in text and "/resume" in text

    pause.resume(app.state)
    text, _keyboard = await panels.dashboard_view()
    assert "Трезвость: <b>124 дня</b>" in text and "/resume" not in text


async def test_assistant_does_not_name_days_on_pause(tmp_path):
    state = State(str(tmp_path / "s.db"))
    pause.stop(state, NOW)
    notion = FakeNotion({"inv": [], "ppl": []}, schemas=SCHEMAS)
    models = FakeModels([_call("overview", {}), _text("Пауза стоит.")])
    client = SimpleNamespace(aio=SimpleNamespace(models=models))
    agent = Agent(client, "m", Workspace(notion, make_config(), state), state, make_config())

    await agent.reply("сколько дней?", datetime(2026, 10, 17, 8, 0, tzinfo=TZ))

    prompt = models.requests[0][-1].parts[0].text
    assert "Трезвость" not in prompt and "/resume" in prompt
    overview = models.requests[1][-1].parts[0].function_response.response["result"]
    assert "sober_days" not in overview and "paused" in overview
