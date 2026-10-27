"""Days as the integer YYYYMMDD (M7.19): the session key of sudarshan.alerts.market.sessions.

A day is an IST calendar day, whether or not the exchange traded: a dividend can be credited on a
Sunday and a deposit can mature on a holiday, so a date the owner types is a day in the same unit
as a session (20261108). Nothing here knows the time of day or the trading calendar; "today" is
always an argument.
"""

from __future__ import annotations

from datetime import date, timedelta

FIRST_DAY = 19900101
LAST_DAY = 22001231


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
