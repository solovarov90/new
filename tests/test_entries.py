from aa_bot import entries, render
from aa_bot import notion as n
from aa_bot.ai import Analysis

from .fakes import page


def test_analysis_drops_values_outside_allowed_lists():
    analysis = Analysis.from_dict(
        {
            "situation": " Не позвал на Новый год ",
            "feelings": ["Обида", "Радость", "Обида"],
            "affected": ["Самоуважение", "Карьера"],
            "defects": ["Эгоизм"],
            "questions": ["Чего ты хотел?", "", 5, "Чего боялся?", "Где твоя часть?", "Лишний"],
            "support_needed": True,
        }
    )
    assert analysis.situation == "Не позвал на Новый год"
    assert analysis.feelings == ["Обида"]
    assert analysis.affected == ["Самоуважение"]
    assert analysis.questions == ["Чего ты хотел?", "Чего боялся?", "Где твоя часть?"]
    assert analysis.support_needed is True


def test_journal_properties_without_ai_keeps_story_title():
    props = entries.journal_properties(None, "Дети опять шумят\nи не дают спать", "Текст", "2026-10-17")
    assert props["Ситуация"] == n.title("Дети опять шумят")
    assert props["Статус"] == n.status("Новый")
    assert props["Дата"] == n.date("2026-10-17")


def test_journal_properties_with_open_questions_stay_in_progress():
    analysis = Analysis(situation="Обида на жену", who="Жена", questions=["Где твоя часть?"])
    props = entries.journal_properties(analysis, "...", "Голосовое", "2026-10-17")
    assert props["Статус"] == n.status("Разбираю")
    assert props["Кто"] == n.text("Жена")
    assert props["Уточнить"] == n.text("Где твоя часть?")


def test_inventory_row_copies_columns_from_journal():
    entry = page(
        "j1",
        Ситуация=("title", "Обесценивал"),
        Кто=("rich_text", "Дядя"),
        Задеты=("multi_select", ["Самоуважение"]),
        Эгоизм=("rich_text", "Я считал, что он должен"),
        Вид=("select", "Голосовое"),
    )
    props = entries.inventory_from_journal(entry)
    assert props["За что (причина)"] == n.title("Обесценивал")
    assert props["Кто"] == n.select("Дядя")
    assert props["Эгоизм"] == n.text("Я считал, что он должен")
    assert props["Источник"] == n.select("Голосовое")


def test_prayer_counter_reaches_two_weeks():
    first, days = entries.person_after_prayer(page("h1", Имя=("title", "Отец")), "2026-10-17")
    assert days == 1 and first["Начало"] == n.date("2026-10-17")
    assert first["Молитва"] == n.status("Молюсь")

    last, days = entries.person_after_prayer(
        page("h1", Имя=("title", "Отец"), **{"Дней подряд": ("number", 13), "Начало": ("date", "2026-10-04")}),
        "2026-10-17",
    )
    assert days == 14 and "Начало" not in last
    assert last["Молитва"] == n.status("Две недели прошли")


def test_prayer_from_message_uses_first_line_as_title():
    props, body = entries.prayer_from_message("Молитва о смирении\nБоже, дай мне смирение")
    assert props["Название"] == n.title("Молитва о смирении")
    assert body == "Боже, дай мне смирение"


def test_plural_days():
    assert [entries.plural_days(x) for x in (1, 2, 5, 11, 21, 124)] == [
        "1 день",
        "2 дня",
        "5 дней",
        "11 дней",
        "21 день",
        "124 дня",
    ]


def test_long_text_is_split_for_notion():
    chunks = n.rich_text("а" * 4500)
    assert [len(c["text"]["content"]) for c in chunks] == [2000, 2000, 500]


def test_cards_escape_html_and_fit_telegram_limit():
    entry = page("j1", Ситуация=("title", "<b>не тег</b>"), Моя_роль=("rich_text", "x"))
    text, keyboard = render.journal(entry)
    assert "&lt;b&gt;не тег&lt;/b&gt;" in text
    buttons = [b.callback_data for row in keyboard.inline_keyboard for b in row]
    assert buttons == ["jed:j1", "jok:j1", "inv:j1"]

    huge = page("p1", Название=("title", "Длинная"))
    text, _ = render.prayer(huge, "слово " * 2000)
    assert len(text) <= render.MESSAGE_LIMIT
