from datetime import UTC, datetime, timedelta

from common.timeutil import local_midnight


def test_the_day_turns_at_midnight_turkiye_time():
    late_utc = datetime(2026, 11, 9, 22, 30, tzinfo=UTC)  # 01:30 on the 10th in Türkiye
    start = local_midnight(late_utc, 3)
    assert start == datetime(2026, 11, 9, 21, 0, tzinfo=UTC)
    assert start.utcoffset() == timedelta(hours=3)
    assert local_midnight(datetime(2026, 11, 9, 20, 59, tzinfo=UTC), 3) == datetime(2026, 11, 8, 21, 0, tzinfo=UTC)
