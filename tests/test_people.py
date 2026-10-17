from types import SimpleNamespace

import pytest

from aa_bot import entries, ui
from aa_bot import notion as n
from aa_bot.ai import AIError, Assistant, fill_name

from .fakes import page

TODAY = "2026-10-17"


def person(page_id, name, status="Молюсь", days=0, last=None, created="2026-01-01T00:00:00.000Z"):
    props = {"Имя": ("title", name), "Молитва": ("status", status), "Дней подряд": ("number", days)}
    if last:
        props["Последняя молитва"] = ("date", last)
    p = page(page_id, **props)
    p["created_time"] = created
    return p


def buttons(keyboard):
    return [b for row in keyboard.inline_keyboard for b in row]


def test_prayer_mark_records_today_and_undo_clears_it():
    props, days = entries.person_after_prayer(person("f1", "Отец", days=3), TODAY)
    assert days == 4 and props["Последняя молитва"] == n.date(TODAY)
    assert entries.prayed_today(person("f1", "Отец", last=TODAY), TODAY)
    assert not entries.prayed_today(person("f1", "Отец", last="2026-10-16"), TODAY)

    undo, days = entries.person_undo_prayer(person("f1", "Отец", days=4, last=TODAY))
    assert days == 3 and undo["Последняя молитва"] == n.date(None)


def test_start_again_after_two_weeks_resets_counter():
    props = entries.person_start(person("f1", "Отец", status="Две недели прошли", days=14), TODAY)
    assert props["Дней подряд"] == n.number(0) and props["Начало"] == n.date(TODAY)
    paused = entries.person_start(person("f1", "Отец", status="Не начато", days=5), TODAY)
    assert "Дней подряд" not in paused  # после паузы счёт продолжается


def test_checklist_shows_today_marks_and_prayer_buttons():
    people = [
        person("f1", "Отец", days=4, last=TODAY, created="2026-01-01T00:00:00.000Z"),
        person("m1", "Мать", days=2, created="2026-01-01T00:01:00.000Z"),
        person("u1", "Дядя", status="Не начато", created="2026-01-01T00:02:00.000Z"),
    ]
    text, keyboard = ui.people_main(people, TODAY)
    assert "Сегодня: <b>1 из 2</b>" in text
    labels = [b.text for b in buttons(keyboard)]
    assert "✅ Отец · 4/14" in labels and "⬜ Мать · 2/14" in labels
    assert "Дядя" not in " ".join(labels)  # не в ежедневном списке
    data = [b.callback_data for b in buttons(keyboard)]
    assert "pp:t:f1" in data and "pp:pr:m1" in data and "pp:prall" in data


def test_pick_list_offers_only_people_not_in_prayer():
    people = [person("f1", "Отец"), person("u1", "Дядя", status="Не начато")]
    _text, keyboard = ui.people_pick(people)
    assert [b.callback_data for b in buttons(keyboard)] == ["pp:s:u1", "pp:m"]


def test_person_view_buttons_follow_status():
    _t, kb = ui.person_view(person("f1", "Отец", days=4, last=TODAY), TODAY)
    labels = [b.text for b in buttons(kb)]
    assert "↩️ Снять отметку" in labels and "⏸ Пауза" in labels
    _t, kb = ui.person_view(person("f1", "Отец", status="Две недели прошли", days=14), TODAY)
    assert "🔁 Начать заново" in [b.text for b in buttons(kb)]


def test_personal_prayer_has_mark_button_per_person():
    text, kb = ui.personal_prayer([("f1", "Отец"), ("m1", "Мать")], "Боже, помоги Отцу и Матери")
    assert "За: Отец, Мать" in text
    assert [b.callback_data for b in buttons(kb)] == ["pray:f1", "pray:m1"]


def test_fill_name_replaces_placeholder_without_declension():
    assert fill_name("проявить к (имя) терпение", "Отец") == "проявить к Отец терпение"


class FakeModels:
    def __init__(self, text):
        self.text = text

    async def generate_content(self, model, contents, config=None):
        return SimpleNamespace(text=self.text)


async def test_personalize_accepts_declined_name_and_rejects_rewrites():
    template = "Боже, помоги мне проявить к (имя) ту же терпимость, что и к больному другу."
    good = Assistant(
        "k", "m", SimpleNamespace(aio=SimpleNamespace(models=FakeModels(template.replace("(имя)", "Отцу"))))
    )
    assert "к Отцу" in await good.personalize(template, "Отец")

    rewritten = Assistant("k", "m", SimpleNamespace(aio=SimpleNamespace(models=FakeModels("Совсем другой текст"))))
    with pytest.raises(AIError):
        await rewritten.personalize(template, "Отец")
