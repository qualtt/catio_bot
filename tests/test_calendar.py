from datetime import date

from bot.keyboards.calendar import build_month_calendar, slot_marker


def test_slot_marker_reflects_availability():
    min_date = date(2026, 7, 1)
    max_date = date(2026, 7, 31)

    # Outside range
    assert slot_marker(date(2026, 6, 30), min_date, max_date, 3, 3) == ("⚫️", "black")
    # No free slots
    assert slot_marker(date(2026, 7, 15), min_date, max_date, 0, 3) == ("🔴", "red")
    # Some free slots (ratio < 0.5)
    assert slot_marker(date(2026, 7, 15), min_date, max_date, 1, 3) == ("🟡", "yellow")
    # All or most free slots
    assert slot_marker(date(2026, 7, 15), min_date, max_date, 2, 3) == ("🟢", "green")


def test_month_calendar_adds_footer_buttons_without_breaking_grid():
    markup = build_month_calendar(
        year=2026,
        month=7,
        availability={date(2026, 7, 6): 1},
        min_date=date(2026, 7, 1),
        max_date=date(2026, 7, 31),
        max_slots=1,
        footer_buttons=[("Auto", "album_auto_current")],
    )

    assert markup.inline_keyboard[0][1].text == "Июль 2026"
    assert markup.inline_keyboard[-1][0].text == "Auto"
    assert markup.inline_keyboard[-1][0].callback_data == "album_auto_current"
