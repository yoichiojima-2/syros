"""Standard 5-field cron: parse an expression, answer "when does it next fire".

Workflow schedules need exactly that and nothing more, so it is a couple of
hundred lines here rather than a dependency in the sandbox image. Syntax is the
familiar one — `minute hour day-of-month month day-of-week`, with `*`, `a-b`,
`*/n`, `a-b/n`, comma lists, three-letter month/day names, and the `@daily`
family of aliases.

Cron's day rule is inherited too: when *both* day-of-month and day-of-week are
restricted the day matches if *either* does (so `0 0 1 * MON` is "the 1st and
every Monday", not "Mondays that fall on the 1st").

Firing times are wall-clock in the schedule's IANA timezone, so a `0 9 * * *`
workflow stays at 9am across a DST shift.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import SyrosError

# How far ahead next_after() will look before calling an expression unfirable.
# Four years and change clears the Feb-29 case, the longest legitimate gap.
MAX_LOOKAHEAD_DAYS = 1500

MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
DAYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")

ALIASES = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}


class CronError(SyrosError):
    """A cron expression or timezone that cannot be used as a schedule."""


@dataclass(frozen=True)
class Cron:
    """A parsed expression: the set of values each field admits."""

    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]  # day of month, 1-31
    months: frozenset[int]  # 1-12
    weekdays: frozenset[int]  # 0-6, Sunday = 0
    # Cron's OR rule only applies when both day fields are restricted; keeping
    # the "was it a *" answer is the only way to tell 0-6 from an explicit
    # "0,1,2,3,4,5,6" after the sets are built.
    day_of_month_restricted: bool
    day_of_week_restricted: bool

    def matches_day(self, day: dt.date) -> bool:
        if day.month not in self.months:
            return False
        # date.weekday() is Monday=0; cron is Sunday=0
        by_month = day.day in self.days
        by_week = (day.weekday() + 1) % 7 in self.weekdays
        if self.day_of_month_restricted and self.day_of_week_restricted:
            return by_month or by_week
        return by_month and by_week


def _field(spec: str, low: int, high: int, names: tuple[str, ...], label: str) -> frozenset[int]:
    values: set[int] = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, _, step_text = part.partition("/")
            if not step_text.isdigit() or int(step_text) < 1:
                raise CronError(f"{label}: bad step in {spec!r}")
            step = int(step_text)
        if part in ("*", "?"):
            start, end = low, high
        elif "-" in part:
            start_text, _, end_text = part.partition("-")
            start = _value(start_text, low, names, label)
            end = _value(end_text, low, names, label)
        else:
            start = _value(part, low, names, label)
            # A bare value with a step means "from here to the top of the range"
            # (`5/15` is 5,20,35,50), which is how cron reads it.
            end = high if step > 1 else start
        if not (low <= start <= high and low <= end <= high) or start > end:
            raise CronError(f"{label}: {part!r} is outside {low}-{high}")
        values.update(range(start, end + 1, step))
    if not values:
        raise CronError(f"{label}: {spec!r} matches nothing")
    return frozenset(values)


def _value(text: str, low: int, names: tuple[str, ...], label: str) -> int:
    """A field value: a number, or a three-letter name indexed from `low`."""
    text = text.strip().lower()
    if text in names:
        return names.index(text) + low
    try:
        return int(text)
    except ValueError as exc:
        raise CronError(f"{label}: {text!r} is not a number{' or name' if names else ''}") from exc


def parse(expression: str) -> Cron:
    """Parse a 5-field expression (or an @alias). Raises CronError."""
    if not isinstance(expression, str) or not expression.strip():
        raise CronError("cron expression is empty")
    text = ALIASES.get(expression.strip().lower(), expression).strip()
    fields = text.split()
    if len(fields) != 5:
        raise CronError(
            f"cron needs 5 fields (minute hour day-of-month month day-of-week), got {len(fields)}"
            f" in {expression!r}"
        )
    minute, hour, day, month, weekday = fields
    weekdays = _field(weekday, 0, 7, DAYS, "day-of-week")
    return Cron(
        minutes=_field(minute, 0, 59, (), "minute"),
        hours=_field(hour, 0, 23, (), "hour"),
        days=_field(day, 1, 31, (), "day-of-month"),
        months=_field(month, 1, 12, MONTHS, "month"),
        # both 0 and 7 mean Sunday; normalize so matches_day only checks 0-6
        weekdays=frozenset(0 if d == 7 else d for d in weekdays),
        day_of_month_restricted=day.strip() not in ("*", "?"),
        day_of_week_restricted=weekday.strip() not in ("*", "?"),
    )


def zone(timezone: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise CronError(f"unknown timezone {timezone!r}") from exc


def validate(expression: str, timezone: str = "UTC") -> None:
    """Reject at creation what the tick would otherwise choke on later."""
    next_after(expression, 0.0, timezone)


def next_after(expression: str, after: float, timezone: str = "UTC") -> float:
    """The first firing time strictly after `after` (both epoch seconds).

    Scanning day by day and only then by (hour, minute) keeps this cheap even
    for the sparse expressions — `0 3 1 1 *` is 4 comparisons a year, not
    525,600.
    """
    cron = parse(expression)
    tz = zone(timezone)
    cursor = dt.datetime.fromtimestamp(after, tz=tz).replace(second=0, microsecond=0)
    day = cursor.date()
    hours, minutes = sorted(cron.hours), sorted(cron.minutes)
    for offset in range(MAX_LOOKAHEAD_DAYS):
        candidate_day = day + dt.timedelta(days=offset)
        if not cron.matches_day(candidate_day):
            continue
        for hour in hours:
            for minute in minutes:
                fire = dt.datetime.combine(candidate_day, dt.time(hour, minute), tzinfo=tz)
                # The comparison is on the instant, not the wall clock: a DST
                # jump can make a later wall-clock time an earlier instant, and
                # a schedule must never fire backwards.
                if fire.timestamp() > after:
                    return fire.timestamp()
    raise CronError(f"{expression!r} has no firing time in the next {MAX_LOOKAHEAD_DAYS} days")


def describe(expression: str) -> str:
    """The expression as stored, with @aliases expanded — for display only."""
    return ALIASES.get(expression.strip().lower(), expression.strip())
