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


def test_pause_and_resume_keep_the_counter():
    assert entries.person_pause(person("f1", "Отец", days=5)) == {"Молитва": n.status("Пауза")}
    resumed = entries.person_start(person("f1", "Отец", status="Пауза", days=5), TODAY)
    assert resumed["Молитва"] == n.status("Молюсь") and "Дней подряд" not in resumed
    veteran = entries.person_start(person("f1", "Отец", status="Пауза", days=20), TODAY)
    assert veteran["Молитва"] == n.status("Две недели прошли")


PEOPLE = [
    person("f1", "Отец", days=4, last=TODAY, created="2026-01-01T00:00:00.000Z"),
    person("m1", "Мать", status="Не начато", created="2026-01-01T00:01:00.000Z"),
    person("u1", "Дядя", status="Две недели прошли", days=15, created="2026-01-01T00:02:00.000Z"),
    person("s1", "Серёга", status="Пауза", days=3, created="2026-01-01T00:03:00.000Z"),
]


def test_daily_list_is_everyone_except_paused():
    assert [p["id"] for p in ui.daily_list(PEOPLE)] == ["f1", "m1", "u1"]
    assert ui.people_today(PEOPLE, TODAY) == (1, 3)


def test_checklist_covers_everyone_every_day():
    text, keyboard = ui.people_main(PEOPLE, TODAY)
    assert "Сегодня: <b>1 из 3</b>" in text
    labels = [b.text for b in buttons(keyboard)]
    assert "✅ Отец" in labels and "⬜ Мать" in labels and "⬜ Дядя 🟢" in labels
    assert not any("Серёга" in label for label in labels)
    assert "⏸ На паузе · 1" in labels
    assert "pw:first" in [b.callback_data for b in buttons(keyboard)]


def test_paused_list_returns_people_to_daily():
    _text, keyboard = ui.people_pick(PEOPLE)
    assert [b.callback_data for b in buttons(keyboard)] == ["pp:s:s1", "pp:m"]


def test_person_view_pause_or_resume():
    _t, kb = ui.person_view(PEOPLE[0], TODAY)
    labels = [b.text for b in buttons(kb)]
    assert "↩️ Снять отметку" in labels and "⏸ Пауза" in labels
    _t, kb = ui.person_view(PEOPLE[3], TODAY)
    assert "▶️ Вернуть в список" in [b.text for b in buttons(kb)]


def test_walk_goes_to_next_unprayed_and_wraps_around():
    daily = ui.daily_list(PEOPLE)
    assert ui.next_unprayed(daily, TODAY)["id"] == "m1"  # Отец уже отмечен
    assert ui.next_unprayed(daily, TODAY, after="m1")["id"] == "u1"
    assert ui.next_unprayed(daily, TODAY, after="u1")["id"] == "m1"  # по кругу
    everyone = [person("f1", "Отец", last=TODAY), person("m1", "Мать", last=TODAY)]
    assert ui.next_unprayed(everyone, TODAY) is None


def test_walk_screen_has_mark_and_navigation():
    daily = ui.daily_list(PEOPLE)
    text, kb = ui.prayer_walk(daily[1], daily, "Боже, помоги Матери", TODAY)
    assert "2 из 3 · Мать" in text and "Сегодня отмечено: 1 из 3" in text
    data = [b.callback_data for b in buttons(kb)]
    assert data == ["pwd:m1", "pw:f1", "pw:u1"]


def test_walk_done_lists_who_is_left():
    daily = ui.daily_list(PEOPLE)
    text, kb = ui.prayer_walk_done(daily, TODAY)
    assert "Мать" in text and [b.callback_data for b in buttons(kb)] == ["pw:first"]
    all_done = [person("f1", "Отец", last=TODAY)]
    text, _ = ui.prayer_walk_done(all_done, TODAY)
    assert "На сегодня всё" in text


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
