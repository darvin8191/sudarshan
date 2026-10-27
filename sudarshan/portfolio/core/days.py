"""Days as the integer YYYYMMDD (M7.19): the session key of sudarshan.alerts.market.sessions.

A day is an IST calendar day, whether or not the exchange traded: a dividend can be credited on a
Sunday and a deposit can mature on a holiday, so a date the owner types is a day in the same unit
as a session (20261108). Nothing here reads the clock or knows the trading calendar: "today" is
always an argument, and so are the holidays that business days leave out. An instant (epoch
milliseconds) is turned into its IST day and time of day, never the other way round.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta

FIRST_DAY = 19900101
LAST_DAY = 22001231
#: India keeps no daylight saving: IST is always five and a half hours ahead of UTC.
IST_OFFSET_MS = 19_800_000
MS_PER_DAY = 86_400_000
EPOCH = date(1970, 1, 1)


def is_day(value) -> bool:
    """True for a real calendar day written YYYYMMDD between 1990 and 2200 (a bool is no day)."""
    if value.__class__ is not int or not FIRST_DAY <= value <= LAST_DAY:
        return False
    try:
        to_date(value)
    except ValueError:
        return False
    return True


def to_date(day: int) -> date:
    return date(day // 10000, day // 100 % 100, day % 100)


def from_date(when: date) -> int:
    return when.year * 10000 + when.month * 100 + when.day


def add_days(day: int, count: int) -> int:
    return from_date(to_date(day) + timedelta(days=count))


def days_between(start: int, end: int) -> int:
    """Whole days from ``start`` to ``end``; negative when ``end`` is the earlier day."""
    return (to_date(end) - to_date(start)).days


def is_leap(year: int) -> bool:
    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)


def add_years(day: int, count: int = 1) -> int:
    """The same date ``count`` years on; 29 February in a year without one becomes 28 February."""
    year, month, number = day // 10000 + count, day // 100 % 100, day % 100
    if month == 2 and number == 29 and not is_leap(year):
        number = 28
    return year * 10000 + month * 100 + number


def held_over_year(acquired: int, on: int) -> bool:
    """Whether a lot bought on ``acquired`` has been held for more than a year on ``on``.

    The first anniversary itself is not yet "more than a year": bought 5 Jan 2024, the lot is over
    a year old from 6 Jan 2025. A figure for the screen, not tax advice.
    """
    return on > add_years(acquired, 1)


def financial_year(day: int) -> int:
    """The year the financial year (April to March) of ``day`` starts in: 2026 is FY 2026-27."""
    return day // 10000 if day // 100 % 100 >= 4 else day // 10000 - 1


def month_of(day: int) -> int:
    """YYYYMM."""
    return day // 100


def is_weekday(day: int) -> bool:
    """Monday to Friday; the exchange's holidays are for the caller to say."""
    return to_date(day).weekday() < 5


def _weekdays_through(ordinal: int) -> int:
    """Weekdays among the calendar days numbered 1 to ``ordinal`` (day 1 is a Monday)."""
    return ordinal // 7 * 5 + min(ordinal % 7, 5)


def business_days_between(start: int, end: int, holidays: Iterable[int] = ()) -> int:
    """Weekdays after ``start`` up to and including ``end``, less any of ``holidays`` among them.

    How far behind ``end`` something dated ``start`` is; 0 when ``end`` is not after ``start``.
    A holiday on a weekend, outside the range, or that is no real day is left out of the count.
    """
    first, last = to_date(start).toordinal(), to_date(end).toordinal()
    if last <= first:
        return 0
    count = _weekdays_through(last) - _weekdays_through(first)
    shut = {day for day in holidays if is_day(day) and start < day <= end and is_weekday(day)}
    return count - len(shut)


def day_of_instant(epoch_ms: int) -> int:
    """The IST day an instant (epoch milliseconds) falls on."""
    return from_date(EPOCH + timedelta(days=(epoch_ms + IST_OFFSET_MS) // MS_PER_DAY))


def clock_of_instant(epoch_ms: int) -> str:
    """The IST time of day of an instant as HH:MM:SS; the milliseconds are dropped, not rounded."""
    seconds = (epoch_ms + IST_OFFSET_MS) % MS_PER_DAY // 1000
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
