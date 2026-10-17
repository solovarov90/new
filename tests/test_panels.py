from datetime import date
from types import SimpleNamespace

from aa_bot.config import Databases
from aa_bot.panels import DASHBOARD, INVENTORY, PEOPLE, Panels, view_key
from aa_bot.state import State
from aa_bot.sync import Syncer

from .fakes import FakeBot, FakeNotion, page

DBS = Databases(inventory="inv", journal="jr", people="ppl", prayers="pr", practices="pc", checkin="ch")


def make(tmp_path):
    notion = FakeNotion(
        {"inv": [page("a1", Кто=("select", "Отец"), **{"За что (причина)": ("title", "Ушел")})], "ppl": [], "ch": []}
    )
    bot = FakeBot()
    state = State(str(tmp_path / "s.db"))
    topics = {"chat": 72, "inventory": 14, "prayers": 11, "people": 13}
    syncer = Syncer(bot, -100, notion, DBS, state, topics, pause=0)
    config = SimpleNamespace(
        databases=DBS, chat_id=-100, sobriety_date=date(2026, 6, 15), morning_time="06:30", evening_time="22:30"
    )
    app = SimpleNamespace(
        notion=notion,
        bot=bot,
        state=state,
        syncer=syncer,
        topics=topics,
        config=config,
        today=lambda: date(2026, 10, 17),
    )
    return Panels(app), app, bot, notion


async def test_ensure_posts_and_pins_panels_once(tmp_path):
    panels, app, bot, _notion = make(tmp_path)
    await panels.ensure()
    first = len(bot.sent)
    assert app.state.get(DASHBOARD) and app.state.get(INVENTORY) and app.state.get("intro:prayers")
    assert app.state.get(PEOPLE) and not app.state.get("intro:people")
    assert len(bot.pinned) == first
    await panels.ensure()
    assert len(bot.sent) == first


async def test_refresh_edits_only_when_something_changed(tmp_path):
    panels, _app, bot, notion = make(tmp_path)
    await panels.ensure()
    await panels.refresh_all()
    assert bot.edited == []
    notion.databases["inv"].append(page("a2", Кто=("select", "Мать"), **{"За что (причина)": ("title", "Не навещала")}))
    await panels.refresh_all()
    assert len(bot.edited) == 1 and "Обид: 2" in bot.edited[0]["text"]


async def test_deleted_panel_is_posted_again(tmp_path):
    panels, app, bot, notion = make(tmp_path)
    await panels.ensure()
    old = int(app.state.get(INVENTORY))
    bot.missing.add(old)
    notion.databases["inv"] = []
    await panels.refresh_inventory()
    assert int(app.state.get(INVENTORY)) != old and int(app.state.get(INVENTORY)) in bot.pinned


async def test_open_record_in_menu_is_not_reset(tmp_path):
    panels, app, bot, notion = make(tmp_path)
    await panels.ensure()
    app.state.set(view_key(int(app.state.get(INVENTORY))), "a1")
    notion.databases["inv"] = []
    await panels.refresh_inventory()
    assert bot.edited == []
