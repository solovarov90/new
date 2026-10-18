from datetime import date

from aa_bot import ui

from .fakes import page


def resentment(page_id, who, reason, status="Черновик", created="2026-01-01T00:00:00.000Z"):
    p = page(page_id, Кто=("select", who), **{"За что (причина)": ("title", reason), "Статус": ("status", status)})
    p["created_time"] = created
    return p


PAGES = [
    resentment("a1-aa", "Отец", "Ушел из семьи", created="2026-01-01T00:00:00.000Z"),
    resentment("a2-aa", "Отец", "Не звонил", status="Готово к 5 шагу", created="2026-01-01T00:01:00.000Z"),
    resentment("b1-bb", "Мать", "Не навещала", created="2026-01-01T00:02:00.000Z"),
]


def buttons(keyboard):
    return [b for row in keyboard.inline_keyboard for b in row]


def test_every_button_fits_telegram_limits():
    long_id = "3fcdfdb1-4822-8105-943b-f6e5a89ca638"
    pages = [resentment(long_id, "Друзья из компании (в драке)", "x" * 200)]
    views = [
        ui.inventory_main(pages),
        ui.inventory_person("Друзья из компании (в драке)", pages),
        ui.inventory_record(pages[0], pages),
        ui.dashboard(
            date(2026, 10, 17),
            date(2026, 6, 15),
            None,
            [page(long_id, Имя=("title", "Отец"))],
            -1003990082908,
            {"chat": 72},
        ),
    ]
    for _text, keyboard in views:
        for button in buttons(keyboard):
            assert button.callback_data is None or len(button.callback_data.encode()) <= 64
            assert len(button.text) <= 64


def test_inventory_main_groups_by_person_with_counts():
    text, keyboard = ui.inventory_main(PAGES)
    assert "Обид: 3 · людей: 2" in text
    labels = [b.text for b in buttons(keyboard)]
    assert labels[:2] == ["Отец · 2", "Мать · 1"]
    assert "➕ Новая обида" in labels


def test_person_view_lists_resentments_with_status():
    name, items = ui.group_by_person(PAGES)[0]
    text, keyboard = ui.inventory_person(name, items)
    assert "⚪ 1. Ушел из семьи" in text and "🟢 2. Не звонил" in text
    assert buttons(keyboard)[-1].callback_data == "ib:m"


def test_record_view_navigates_inside_person():
    _name, items = ui.group_by_person(PAGES)[0]
    text, keyboard = ui.inventory_record(items[0], items)
    data = [b.callback_data for b in buttons(keyboard)]
    assert "Обида 1 из 2" in text
    assert "ib:r:a2aa" in data  # ▶️ к следующей
    assert not any(d == "ib:r:a1aa" for d in data)  # ◀️ нет у первой
    assert "ib:ok:a1aa" in data and "ib:ed:a1aa" in data

    _text, keyboard = ui.inventory_record(items[1], items)
    assert "↩️ Вернуть в черновик" in [b.text for b in buttons(keyboard)]


def test_dashboard_shows_checkin_state_and_topic_links():
    checkin = page("c1", **{"Утренняя молитва": ("checkbox", True), "Трезвый день": ("checkbox", False)})
    father = page(
        "f1", Имя=("title", "Отец"), **{"Молитва": ("status", "Молюсь"), "Последняя молитва": ("date", "2026-10-17")}
    )
    mother = page("m1", Имя=("title", "Мать"), Молитва=("status", "Не начато"))
    text, keyboard = ui.dashboard(
        date(2026, 10, 17), date(2026, 6, 15), checkin, [father, mother], -1003990082908, {"chat": 72, "sos": 9}
    )
    assert "Трезвость: <b>124 дня</b>" in text and "Практики дня: 1 из 7" in text
    assert "Промаливание: 1 из 2" in text
    labels = [b.text for b in buttons(keyboard)]
    assert "✅ Утренняя молитва" in labels and "⬜ Трезвый день" in labels
    assert "🙏 Промаливание по очереди · 1/2" in labels
    urls = [b.url for b in buttons(keyboard) if b.url]
    assert "https://t.me/c/3990082908/72" in urls


def test_morning_is_one_message_with_prayer_buttons():
    prayer = page("p1", Название=("title", "Молитва о начале дня"))
    text, keyboard = ui.morning(date(2026, 10, 17), date(2026, 6, 15), [prayer], [])
    data = [b.callback_data for b in buttons(keyboard)]
    assert "Трезвость" in text
    assert data == ["pr:p1", "chk:morning"]
