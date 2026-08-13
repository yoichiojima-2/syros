import datetime as dt

import pytest

from syros.cron import CronError, next_after, parse, validate


def epoch(text: str) -> float:
    return dt.datetime.fromisoformat(text).timestamp()


def fires_at(expression: str, after: str, timezone: str = "UTC") -> str:
    fire = next_after(expression, epoch(after), timezone)
    return dt.datetime.fromtimestamp(fire, tz=dt.timezone.utc).isoformat()


def test_every_five_minutes():
    assert fires_at("*/5 * * * *", "2026-08-13T10:17:00+00:00") == "2026-08-13T10:20:00+00:00"


def test_strictly_after():
    # a firing exactly at `after` is not "after"
    assert fires_at("0 9 * * *", "2026-08-13T09:00:00+00:00") == "2026-08-14T09:00:00+00:00"


def test_timezone_wall_clock():
    # 9am Tokyo is midnight UTC
    assert (
        fires_at("0 9 * * *", "2026-08-13T10:00:00+00:00", "Asia/Tokyo")
        == "2026-08-14T00:00:00+00:00"
    )


def test_dst_spring_forward_keeps_wall_clock():
    # US DST starts 2026-03-08: 9am local moves from 17:00 to 16:00 UTC
    before = fires_at("0 9 * * *", "2026-03-07T00:00:00+00:00", "America/Los_Angeles")
    after = fires_at("0 9 * * *", "2026-03-09T00:00:00+00:00", "America/Los_Angeles")
    assert before == "2026-03-07T17:00:00+00:00"
    assert after == "2026-03-09T16:00:00+00:00"


def test_names_and_ranges():
    assert fires_at("30 3 * * MON-FRI", "2026-08-15T00:00:00+00:00").startswith("2026-08-17")
    assert fires_at("0 0 1 JAN *", "2026-02-01T00:00:00+00:00").startswith("2027-01-01")


def test_day_or_rule():
    # both fields restricted: the 1st OR any Monday (2026-08-17 is a Monday)
    assert fires_at("0 0 1 * MON", "2026-08-13T01:00:00+00:00").startswith("2026-08-17")
    # only day-of-week restricted: plain AND semantics
    assert fires_at("0 0 * * MON", "2026-08-13T01:00:00+00:00").startswith("2026-08-17")


def test_sunday_is_zero_and_seven():
    for day in ("0", "7", "SUN"):
        assert fires_at(f"0 0 * * {day}", "2026-08-13T01:00:00+00:00").startswith("2026-08-16")


def test_step_from_value():
    # `5/15` = 5,20,35,50
    assert fires_at("5/15 * * * *", "2026-08-13T10:21:00+00:00") == "2026-08-13T10:35:00+00:00"


def test_aliases():
    assert parse("@daily") == parse("0 0 * * *")
    assert parse("@hourly") == parse("0 * * * *")


def test_leap_day():
    assert fires_at("0 0 29 2 *", "2026-08-13T00:00:00+00:00").startswith("2028-02-29")


@pytest.mark.parametrize(
    "bad",
    ["", "x", "* * * *", "* * * * * *", "60 * * * *", "0 25 * * *", "*/0 * * * *", "0 0 32 * *"],
)
def test_rejects_bad_expressions(bad):
    with pytest.raises(CronError):
        parse(bad)


def test_rejects_unfirable():
    with pytest.raises(CronError):
        next_after("0 0 30 2 *", 0.0)  # Feb 30 never exists


def test_rejects_unknown_timezone():
    with pytest.raises(CronError):
        validate("0 0 * * *", "Mars/Olympus_Mons")


def test_validate_accepts_good():
    validate("0 9 * * MON-FRI", "Asia/Tokyo")
