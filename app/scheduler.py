from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.config import AppConfig


def report_due(now: datetime, config: AppConfig, last_date: str | None) -> bool:
    tz = ZoneInfo(config.timezone)
    local = now.astimezone(tz)
    hour_s, minute_s = config.report_time.split(":", 1)
    target_hour = int(hour_s)
    target_minute = int(minute_s)
    today = local.date().isoformat()
    if last_date == today:
        return False
    return local.hour > target_hour or (
        local.hour == target_hour and local.minute >= target_minute
    )


def utcnow() -> datetime:
    return datetime.now(UTC)
