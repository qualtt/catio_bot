from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from bot.config import config


def app_timezone() -> ZoneInfo:
    return ZoneInfo(config.TIMEZONE)


def now_in_app_tz() -> datetime:
    return datetime.now(app_timezone())


def parse_daily_slot_times() -> list[time]:
    slots: list[time] = []
    for raw_slot in config.DAILY_SLOT_TIMES.split(","):
        raw_slot = raw_slot.strip()
        if not raw_slot:
            continue
        hour_raw, minute_raw = raw_slot.split(":", 1)
        slots.append(time(hour=int(hour_raw), minute=int(minute_raw)))

    if slots:
        return sorted(slots)

    return [time(hour=config.AUTO_POST_TIME_HOUR, minute=config.AUTO_POST_TIME_MINUTE)]


def combine_slot(target_date: date, slot_time: time) -> datetime:
    return datetime.combine(target_date, slot_time, tzinfo=app_timezone())


def ensure_app_timezone(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=app_timezone())
    return value.astimezone(app_timezone())


