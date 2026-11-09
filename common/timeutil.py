"""The application's day. Stored times stay in UTC; "today" and daily totals follow Türkiye time (GMT+3, all year)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def local_midnight(now: datetime, utc_offset_hours: int) -> datetime:
    """The start of ``now``'s day in that offset, as an aware datetime."""
    local = now.astimezone(timezone(timedelta(hours=utc_offset_hours)))
    return local.replace(hour=0, minute=0, second=0, microsecond=0)
