"""The event operands' counts (M9.13, gap audit GA8.3; events.py): trading days to a share's next
ex-date and to a contract's expiry, on the exchange's calendar.

Every date is fixed and every calendar is the test's own (a predicate over days): no clock is
read. The calendar below shuts weekends and Thursday 12 Nov 2026 (a holiday made for the tests),
and opens Sunday 8 Nov 2026 (a special session, as the extra-days file lists one).
"""

from __future__ import annotations

from datetime import date
from fractions import Fraction

import pytest

from sudarshan.screener import catalog as cat
from sudarshan.screener import engine, events
from sudarshan.screener.types import DAYS, NO_EVENTS, Why

HOLIDAY = date(2026, 11, 12)
SPECIAL = date(2026, 11, 8)


def trades(day: date) -> bool:
    return day == SPECIAL or (day.weekday() < 5 and day != HOLIDAY)


def days_from(day: date, reach: int = events.REACH_DAYS) -> tuple[int, ...]:
    return events.ahead(day, trades, reach)


WED = days_from(date(2026, 10, 28))  # the run's session: Wednesday 28 Oct 2026
PREVIOUS = 20261027


# ---- the sessions ahead --------------------------------------------------------------------------
def test_the_sessions_ahead_follow_the_calendar_and_start_on_the_run():
    assert WED[:8] == (20261028, 20261029, 20261030, 20261102, 20261103, 20261104, 20261105,
                       20261106)  # fmt: skip
    assert 20261108 in WED and 20261112 not in WED and 20261107 not in WED
    assert events.ahead(date(2026, 10, 31), trades, 3) == (20261102,)  # a Saturday is no session
    # Across the year's end of the books: Tuesday 30 Mar to Friday 2 Apr 2027, every one a session.
    assert days_from(date(2027, 3, 30), 4) == (20270330, 20270331, 20270401, 20270402)
    assert len(WED) == sum(1 for d in range(events.REACH_DAYS) if trades(
        date.fromordinal(date(2026, 10, 28).toordinal() + d)))  # fmt: skip


# ---- days to an ex-date --------------------------------------------------------------------------
ALL = cat.CA_KINDS


@pytest.mark.parametrize(
    "ex_day, count, why",
    [
        (20261028, 0, "the ex-date is today's session"),
        (20261029, 1, "tomorrow: today is the last day to buy and be on the record"),
        (20261031, 3, "a Saturday's ex-date takes effect at Monday's session"),
        (20261108, 8, "a special session on a Sunday is a session"),
        (20261112, 12, "a holiday's ex-date takes effect at the next session"),
        (20261027, events.NONE_LISTED, "yesterday's session: past"),
        (20261020, events.NONE_LISTED, "last week: past"),
    ],
)
def test_days_to_an_ex_date(ex_day, count, why):
    assert events.ex_days((("dividend", ex_day),), ALL, WED, PREVIOUS) == count, why


def test_a_weekend_ex_date_before_the_run_counts_as_its_session_until_a_session_passes():
    monday = days_from(date(2026, 11, 2))
    saturday = (("dividend", 20261031),)
    assert events.ex_days(saturday, ALL, monday, 20261030) == 0  # Friday was the last before it
    assert events.ex_days(saturday, ALL, monday, 0) == events.NONE_LISTED  # no word: taken as past
    assert events.ex_days((("dividend", 20261030),), ALL, monday, 20261030) == events.NONE_LISTED


def test_over_the_31_march_boundary():
    tuesday = days_from(date(2027, 3, 30))
    assert events.ex_days((("dividend", 20270401),), ALL, tuesday, 20270329) == 2
    assert events.expiry_days(20270331, tuesday) == 1
    assert events.expiry_of_symbol("BANKNIFTY30MAR2752000PE") == 20270330


def test_the_kind_asked_for_and_the_soonest_of_it():
    listed = (("bonus", 20261029), ("dividend", 20261106), ("dividend", 20261104))
    assert events.ex_days(listed, ("dividend",), WED, PREVIOUS) == 5
    assert events.ex_days(listed, ALL, WED, PREVIOUS) == 1
    assert events.ex_days(listed, ("split",), WED, PREVIOUS) == events.NONE_LISTED


def test_none_listed_is_a_count_and_not_known_is_a_reason():
    assert events.ex_days((), ALL, WED, PREVIOUS) == events.NONE_LISTED  # read: nothing to come
    beyond = (("dividend", 20280110),)  # listed past the days counted
    assert events.ex_days(beyond, ALL, WED, PREVIOUS) == events.NONE_LISTED
    assert events.ex_days(None, ALL, WED, PREVIOUS) == Why(NO_EVENTS, ("ex_dates",))
    assert events.ex_days((("dividend", 20261029),), ALL, (), PREVIOUS) == Why(
        NO_EVENTS, ("calendar",)
    )


# ---- days to an expiry ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "expiry, count, why",
    [
        (20261028, 0, "the expiry day itself"),
        (20261103, 4, "next Tuesday"),
        (20261112, 11, "an expiry on a holiday is the session before it (Wednesday 11 Nov)"),
        (20261114, 12, "an expiry on a Saturday is Friday's session"),
        (20261124, 19, "four weeks on, the special session and the holiday counted as they are"),
    ],
)
def test_days_to_an_expiry(expiry, count, why):
    assert events.expiry_days(expiry, WED) == count, why


def test_an_expiry_that_is_not_known_is_a_reason():
    assert events.expiry_days(None, WED) == Why(NO_EVENTS, ("expiry",))  # a share: no expiry
    assert events.expiry_days(20261027, WED) == Why(NO_EVENTS, ("expiry",))  # expired yesterday
    assert events.expiry_days(20281228, WED) == Why(NO_EVENTS, ("far",))  # past the days counted
    assert events.expiry_days(20261103, ()) == Why(NO_EVENTS, ("calendar",))


@pytest.mark.parametrize(
    "symbol, expiry",
    [
        ("NIFTY24NOV26FUT", 20261124),
        ("SBIN24NOV26800CE", 20261124),
        ("VEDL25APR27292.5CE", 20270425),
        ("M&M29DEC26FUT", 20261229),
        ("SENSEX26NOV2680000CE", 20261126),
        ("NIFTY31FEB27FUT", None),  # not a day
        ("NIFTY24nov26FUT", None),
        ("RELIANCE", None),
        ("NIFTY", None),
        (None, None),
    ],
)
def test_a_contracts_expiry_is_read_from_its_symbol(symbol, expiry):
    assert events.expiry_of_symbol(symbol) == expiry


# ---- how a count is shown, and the kinds -----------------------------------------------------------
def test_a_count_of_days_is_shown_whole_and_none_listed_in_words():
    assert engine.show(3, DAYS) == "3"
    assert engine.show(0, DAYS) == "0"
    assert engine.show(Fraction(5, 2), DAYS) == "2.5"
    assert engine.show(events.NONE_LISTED, DAYS) == "none listed"


def test_the_kinds_are_the_portfolios():
    from sudarshan.portfolio.core.kinds import CA_KINDS

    assert cat.CA_KINDS == tuple(CA_KINDS)
    assert cat.EVENT_KINDS == ("any", *CA_KINDS)
