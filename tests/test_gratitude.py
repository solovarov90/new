from types import SimpleNamespace

import pytest

from aa_bot import gratitude as gr
from aa_bot.ai import AIError, Assistant
from aa_bot.state import State


def buttons(keyboard):
    return [b.callback_data for row in keyboard.inline_keyboard for b in row]


def test_items_fill_god_then_people_then_self():
    thanks = gr.Gratitude("2026-10-18")
    assert thanks.current() == "god"
    thanks.add([f"бог {i}" for i in range(6)])  # шестая уходит людям
    assert len(thanks.god) == 5 and thanks.people == ["бог 5"] and thanks.current() == "people"
    thanks.add(["мама", "спонсор", "водитель", "себе 1", "себе 2", "себе 3"])
    assert thanks.people[-1] == "водитель" and thanks.self_ == ["себе 1", "себе 2", "себе 3"]
    assert thanks.complete and thanks.total == 12


def test_sorted_items_go_to_their_sections_and_undo_takes_last():
    thanks = gr.Gratitude("2026-10-18")
    thanks.add_sorted({"god": ["за трезвый день"], "people": ["маме за звонок"], "self": ["за зарядку"]})
    assert thanks.total == 3 and thanks.current() == "god"
    assert thanks.undo() == "за зарядку" and thanks.self_ == []


def test_split_items_drops_numbering_and_empty_lines():
    assert gr.split_items("1. за утро\n\n- маме\n• себе за честность") == ["за утро", "маме", "себе за честность"]


def test_state_round_trip(tmp_path):
    state = State(str(tmp_path / "s.db"))
    thanks = gr.Gratitude("2026-10-18", god=["за день"], message_id=5)
    gr.save(state, thanks)
    loaded = gr.load(state, "2026-10-18")
    assert loaded.god == ["за день"] and loaded.message_id == 5 and loaded.active


def test_screen_asks_for_current_section_then_offers_save():
    thanks = gr.Gratitude("2026-10-18", god=["за день"])
    text, kb = gr.screen(thanks)
    assert "1 из 12" in text and "Богу</b> · 1/5" in text and "Ещё 4" in text
    assert buttons(kb) == ["gr:hint", "gr:undo", "gr:stop"]

    thanks.add([str(i) for i in range(11)])
    text, kb = gr.screen(thanks)
    assert "12 из 12" in text and buttons(kb) == ["gr:save"]
    thanks.saved = True
    _text, kb = gr.screen(thanks)
    assert buttons(kb) == []

    paused = gr.Gratitude("2026-10-18", active=False)
    assert buttons(gr.screen(paused)[1]) == ["gr:go"]


def test_notion_text_lists_all_sections():
    thanks = gr.Gratitude("2026-10-18", god=["за день"], people=["маме"], self_=["за честность"])
    text = gr.notion_text(thanks)
    assert "Благодарности за 18.10.2026" in text and "Людям:\n1. маме" in text


def test_hints_rotate():
    assert gr.hints("self", 1) != gr.hints("self", 2) and len(gr.hints("god", 0)) == 3


class FakeModels:
    def __init__(self, text):
        self.text = text

    async def generate_content(self, model, contents, config=None):
        return SimpleNamespace(text=self.text)


def assistant(text):
    return Assistant("k", "m", SimpleNamespace(aio=SimpleNamespace(models=FakeModels(text))))


async def test_sort_gratitude_reads_sections_and_rejects_empty():
    result = await assistant('{"god": ["за день"], "people": ["маме"], "self": []}').sort_gratitude("…", "god")
    assert result == {"god": ["за день"], "people": ["маме"], "self": []}
    with pytest.raises(AIError):
        await assistant('{"god": [], "people": [], "self": []}').sort_gratitude("…", "god")


async def test_gratitude_hints_returns_three():
    hints = await assistant('["за a", "за b", "за c", "за d"]').gratitude_hints("god", "")
    assert hints == ["за a", "за b", "за c"]
