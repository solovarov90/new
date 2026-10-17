import pytest

from aa_bot.config import Databases
from aa_bot.state import State
from aa_bot.sync import Syncer

from .fakes import FakeBot, FakeNotion, page

DBS = Databases(inventory="inv", journal="jr", people="ppl", prayers="pr", practices="pc", checkin="ch")
TOPICS = {"prayers": 11, "practices": 12, "people": 13, "inventory": 14, "review": 15}
OLD = "2026-01-01T00:00:00.000Z"
LATER = "2026-01-02T00:00:00.000Z"


def prayer(page_id="p1", name="Молитва о начале дня", edited=OLD, order=1):
    return page(page_id, edited, Название=("title", name), Когда=("select", "Утро"), Порядок=("number", order))


def person(page_id="h1", name="Отец", edited=OLD):
    return page(page_id, edited, Имя=("title", name), Молитва=("status", "Не начато"))


@pytest.fixture
def setup(tmp_path):
    notion = FakeNotion({"pr": [prayer()], "ppl": [person()]}, bodies={"p1": "Боже, направь мои мысли"})
    bot = FakeBot()
    syncer = Syncer(bot, -100, notion, DBS, State(str(tmp_path / "s.db")), dict(TOPICS), pause=0)
    return notion, bot, syncer


async def test_first_sync_posts_each_page_to_its_topic(setup):
    _notion, bot, syncer = setup
    totals = await syncer.sync_once()
    assert totals == {"new": 2, "edited": 0, "removed": 0}
    by_thread = {m["thread"]: m for m in bot.sent}
    assert "Боже, направь мои мысли" in by_thread[11]["text"]
    assert "Отец" in by_thread[13]["text"]
    assert by_thread[13]["keyboard"].inline_keyboard[0][0].callback_data == "pray:h1"


async def test_second_sync_without_changes_does_nothing(setup):
    _notion, bot, syncer = setup
    await syncer.sync_once()
    totals = await syncer.sync_once()
    assert totals == {"new": 0, "edited": 0, "removed": 0}
    assert len(bot.sent) == 2 and bot.edited == []


async def test_edit_in_notion_edits_card_in_place(setup):
    notion, bot, syncer = setup
    await syncer.sync_once()
    notion.databases["pr"] = [prayer(name="Утренняя молитва", edited=LATER)]
    totals = await syncer.sync_once()
    assert totals["edited"] == 1 and len(bot.sent) == 2
    assert "Утренняя молитва" in bot.edited[0]["text"]


async def test_touched_page_with_same_content_is_not_edited(setup):
    notion, bot, syncer = setup
    await syncer.sync_once()
    notion.databases["pr"] = [prayer(edited=LATER)]
    await syncer.sync_once()
    assert bot.edited == []


async def test_page_removed_in_notion_is_removed_from_telegram(setup):
    notion, bot, syncer = setup
    await syncer.sync_once()
    sent_person = next(m for m in bot.sent if m["thread"] == 13)
    notion.databases["ppl"] = []
    totals = await syncer.sync_once()
    assert totals["removed"] == 1
    assert bot.deleted == [sent_person["id"]]


async def test_card_deleted_in_telegram_is_sent_again(setup):
    notion, bot, syncer = setup
    await syncer.sync_once()
    first = next(m for m in bot.sent if m["thread"] == 11)
    bot.missing.add(first["id"])
    notion.databases["pr"] = [prayer(name="Новое название", edited=LATER)]
    await syncer.sync_once()
    assert len(bot.sent) == 3
    assert "Новое название" in bot.sent[-1]["text"]


async def test_card_sent_by_handler_is_not_duplicated(setup):
    notion, bot, syncer = setup
    entry = page("j1", OLD, Ситуация=("title", "Обида на дядю"), Статус=("status", "Новый"))
    from aa_bot import render

    text, keyboard = render.journal(entry)
    syncer.remember(entry, "journal", 15, 555, text, keyboard)
    notion.databases["jr"] = [entry]
    await syncer.sync_once()
    assert not any(m["thread"] == 15 for m in bot.sent)


async def test_card_of_page_created_during_sync_is_kept(setup):
    notion, bot, syncer = setup
    await syncer.sync_once()
    entry = page("j2", OLD, Ситуация=("title", "Новая запись"), Статус=("status", "Новый"))
    from aa_bot import render

    text, keyboard = render.journal(entry)
    syncer.remember(entry, "journal", 15, 777, text, keyboard)
    # Список журнала уже прочитан без этой страницы, но в Notion она есть.
    notion.databases["other"] = [entry]
    totals = await syncer.sync_once()
    assert totals["removed"] == 0 and 777 not in bot.deleted


async def test_old_inventory_cards_are_removed_and_hooks_run(setup):
    _notion, bot, syncer = setup
    from aa_bot.state import Card

    syncer.state.save_card(Card("old", "inventory", 14, 4242, OLD, "x"))
    calls = []

    async def hook():
        calls.append(1)

    syncer.after_sync.append(hook)
    await syncer.sync_once()
    assert 4242 in bot.deleted and syncer.state.cards("inventory") == []
    assert calls == [1]
