from datetime import datetime

from bot.handlers.suggest.helpers import _format_dashboard_time, _format_schedule
from db.crud.time_utils import app_timezone


def test_format_schedule_accepts_datetime_and_string():
    dt = datetime(2026, 7, 30, 15, 30, tzinfo=app_timezone())
    expected = dt.strftime("%Y-%m-%d %H:%M")
    assert _format_schedule(dt) == expected
    assert _format_schedule(expected) == expected
    import pytest
    with pytest.raises(ValueError):
        _format_schedule("invalid")
    assert _format_schedule(None) == "не выбрано"

def test_format_dashboard_time():
    dt = datetime(2026, 7, 30, 15, 30, tzinfo=app_timezone())
    expected_time_str = dt.strftime("%Y-%m-%d %H:%M")
    
    # Not selected
    assert _format_dashboard_time({}) == "Не выбрано"
    
    # Manual selection
    assert _format_dashboard_time({"schedule_time": dt.isoformat()}) == expected_time_str
    
    # Auto selection
    assert _format_dashboard_time({"is_auto_scheduled": True, "schedule_time": dt.isoformat()}) == f"Автоматически ({expected_time_str})"
    assert _format_dashboard_time({"is_auto_scheduled": True, "schedule_time": None}) == "Автоматически"
