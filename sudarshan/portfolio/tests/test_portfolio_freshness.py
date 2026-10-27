"""core/freshness.py and the calendar helpers under it (PA3): how old a price is, and what to say.

The golden vectors (vectors/freshness.json) pin each rule at its edge and are what the phone runs
too. Walked day by day, every boundary of the two numeric rules is then checked against a plain
count of days, the listed-price sentences are checked against a second reading of the README, and
the words are held to the platform's vocabulary: no icon, no developer word, and never "Live" or
"Real-time", which belong to the feed line and not to a price's age.
"""

from __future__ import annotations

import itertools
import json
import random
import re
from collections import Counter
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from portfolio_oracle import business_days_walked
from portfolio_ts import interfaces, members

from sudarshan.portfolio.core import days, kinds
from sudarshan.portfolio.core.freshness import (
    FRESH,
    NO_PRICE,
    OLD,
    Freshness,
    manual,
    nav,
    of_price,
    when,
)
from sudarshan.portfolio.core.words import DOT

VECTORS = Path(__file__).parent / "vectors"
FILE = json.loads((VECTORS / "freshness.json").read_text(encoding="utf-8"))
TYPES_TS = Path(__file__).resolve().parents[3] / "frontend/src/sudarshan/portfolio/types.ts"
TODAY = 20261027  # a Tuesday
IST = timezone(timedelta(hours=5, minutes=30))
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


# -- the golden vectors ---------------------------------------------------------------------------


def test_the_file_has_what_the_design_needs():
    ids = {case["id"] for case in FILE["prices"]}
    assert {"FR1", "FR2", "FR3", "FR4"} <= ids
    assert all(case["title"] and case["why"] for case in FILE["prices"])
    assert all(case["why"] for case in FILE["business_days"] + FILE["instants"])


@pytest.mark.parametrize("case", FILE["prices"], ids=lambda case: case["id"])
def test_the_freshness_of_a_price_is_the_vectors(case):
    assert of_price(case["price"], **case["context"]).as_dict() == case["expect"]


@pytest.mark.parametrize(
    "case", FILE["business_days"], ids=lambda case: f"{case['start']}-{case['end']}"
)
def test_the_business_days_between_two_days_are_the_vectors(case):
    got = days.business_days_between(case["start"], case["end"], case["holidays"])
    assert got == case["expect"]


@pytest.mark.parametrize("case", FILE["instants"], ids=lambda case: str(case["at"]))
def test_the_ist_day_and_clock_of_an_instant_are_the_vectors(case):
    assert days.day_of_instant(case["at"]) == case["day"]
    assert days.clock_of_instant(case["at"]) == case["clock"]


# -- a fund's NAV ---------------------------------------------------------------------------------


def nav_price(day):
    return {
        "price_u": 456_789,
        "prev_close_u": 455_000,
        "day": day,
        "at": None,
        "source": "amfi",
        "basis": "nav",
        "late": False,
    }


def test_the_number_of_days_a_nav_may_be_behind_is_three():
    assert kinds.NAV_OLD_BUSINESS_DAYS == 3


def test_a_nav_is_old_after_three_business_days_whatever_the_calendar():
    rng = random.Random(14)
    seen = Counter()
    for _ in range(800):
        last = days.add_days(20250101, rng.randint(0, 700))
        nav_day = days.add_days(last, -rng.randint(-2, 16))
        around = [days.add_days(last, -n) for n in range(0, 20)]
        holidays = rng.sample(around, rng.randint(0, 5))
        got = of_price(
            nav_price(nav_day),
            today=days.add_days(last, rng.randint(0, 3)),
            last_session_day=last,
            holidays=holidays,
        )
        behind = business_days_walked(nav_day, last, holidays)
        want = OLD if behind > kinds.NAV_OLD_BUSINESS_DAYS else FRESH
        assert got.state == want, (nav_day, last, holidays)
        assert got.words.startswith("NAV of ")
        assert got.words.endswith("AMFI has not published a newer one.") == (want == OLD)
        seen[want] += 1
    assert seen[OLD] >= 100 and seen[FRESH] >= 100


def test_once_a_nav_is_old_every_older_one_is_too():
    rng = random.Random(15)
    for _ in range(100):
        last = days.add_days(20250101, rng.randint(0, 700))
        holidays = rng.sample([days.add_days(last, -n) for n in range(0, 40)], rng.randint(0, 12))
        states = [nav(days.add_days(last, -n), last, holidays).state for n in range(0, 40)]
        assert states == sorted(states, key=[FRESH, OLD].index)
        assert states[0] == FRESH and states[-1] == OLD


def test_a_nav_says_which_day_it_is_of_and_the_year_only_when_it_is_another():
    assert nav(20261022, 20261027).words == "NAV of 22 Oct"
    assert nav(20251230, 20261027).words.startswith("NAV of 30 Dec 2025;")
    assert nav(20261231, 20270104).words == "NAV of 31 Dec 2026"  # Thursday to Monday: 2 days
    assert nav(20261231, 20260102).words == "NAV of 31 Dec"  # the year of the latest session


# -- a value kept by hand -------------------------------------------------------------------------


@pytest.mark.parametrize("old_days", [-5, 0, 3, 6, 7, 8, 30, 90, 364, 365, 366, 1000])
def test_a_value_kept_by_hand_is_old_past_its_limit_read_as_between_a_week_and_a_year(old_days):
    limit = min(max(old_days, kinds.MANUAL_OLD_DAYS_MIN), kinds.MANUAL_OLD_DAYS_MAX)
    for age in range(0, 400):
        got = manual(days.add_days(TODAY, -age), TODAY, old_days)
        assert got.state == (OLD if age > limit else FRESH), (age, limit)


def test_the_limits_of_a_value_are_the_contracts():
    assert (kinds.MANUAL_OLD_DAYS_MIN, kinds.MANUAL_OLD_DAYS, kinds.MANUAL_OLD_DAYS_MAX) == (
        7,
        30,
        365,
    )
    assert manual(days.add_days(TODAY, -30), TODAY).state == FRESH
    assert manual(days.add_days(TODAY, -31), TODAY).state == OLD


def test_a_longer_limit_never_makes_a_value_older():
    for age in (0, 1, 6, 7, 8, 29, 30, 31, 100, 364, 365, 366):
        states = [
            manual(days.add_days(TODAY, -age), TODAY, old_days).state for old_days in range(0, 500)
        ]
        assert states == sorted(states, key=[OLD, FRESH].index), age


def test_a_value_says_how_old_it_is_in_plain_days():
    def ends_with(age):
        return manual(days.add_days(TODAY, -age), TODAY).words.rsplit(DOT, 1)[1]

    assert ends_with(0) == "today"
    assert ends_with(1) == "1 day old"
    assert ends_with(2) == "2 days old"
    assert ends_with(45) == "45 days old"
    # A value dated in the future is read as today's, never as "-1 days old".
    assert manual(days.add_days(TODAY, 3), TODAY).words.endswith("today")


def test_a_loans_balance_says_balance_and_everything_else_says_value():
    assert manual(20260912, TODAY, noun="Balance").words.startswith("Balance from 12 Sep")
    assert manual(20260912, TODAY).words.startswith("Value from 12 Sep")


# -- a listed price: a tick, a quote or a closing record -------------------------------------------


def clock_of(epoch_ms):
    local = datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=epoch_ms)
    return local.astimezone(IST).strftime("%H:%M:%S")


def short(day, near):
    text = f"{day % 100} {MONTHS[day // 100 % 100 - 1]}"
    return text if day // 10000 == near // 10000 else f"{text} {day // 10000}"


def listed(day, at, source, basis, late):
    return {
        "price_u": 14_282_500,
        "prev_close_u": 14_000_000,
        "day": day,
        "at": at,
        "source": source,
        "basis": basis,
        "late": late,
    }


def expected_listed(price, today, last):
    """The README's rule for a listed price, read again: its state and sentence."""
    day, at = price["day"], price["at"]
    if day < last:
        return OLD, f"Price of {short(day, today)}; no newer price yet."
    if price["basis"] == "close":
        return FRESH, f"Close of {short(day, today)}"
    if price["source"] == "quote":
        what = "quote"
    elif price["late"]:
        what = "closing record, taken late"
    elif day < today:
        what = "closing record"
    else:
        return FRESH, None  # a tick: the feed line speaks
    stamp = ", ".join(
        [
            *([clock_of(at)] if at is not None else []),
            *([short(day, today)] if day != today else []),
        ]
    )
    return FRESH, f"Last price {stamp} ({what})" if stamp else f"Last price ({what})"


# (today, latest session): a session day, a weekend after it, and Monday before the open
CALENDARS = [(20261027, 20261027), (20261024, 20261023), (20261025, 20261023), (20261026, 20261023)]
AT = 1_792_749_598_000  # 23 Oct 2026, 15:29:58 IST


def test_every_listed_price_gets_the_sentence_the_readme_gives_it():
    seen = Counter()
    for (today, last), back, source, basis, late, at in itertools.product(
        CALENDARS, (-3, -1, 0, 1), ("feed", "quote"), ("last", "close"), (False, True), (None, AT)
    ):
        day = days.add_days(last, back)
        price = listed(day, at, source, basis, late)
        want_state, want_words = expected_listed(price, today, last)
        got = of_price(price, today=today, last_session_day=last)
        assert (got.state, got.words) == (want_state, want_words), (today, last, price)
        seen[want_words and want_words.split(" (")[0].split(" ")[0]] += 1
        seen[want_state] += 1
    assert seen[OLD] > 0 and seen[FRESH] > 0 and seen[None] > 0  # a tick says nothing
    assert seen["Last"] > 0 and seen["Close"] > 0 and seen["Price"] > 0


def test_a_price_dated_after_the_latest_session_is_not_old():
    got = of_price(
        listed(20261028, None, "feed", "last", False), today=TODAY, last_session_day=TODAY
    )
    assert got.state == FRESH


@pytest.mark.parametrize("day", [None, TODAY])
@pytest.mark.parametrize("price_u", [None, 14_282_500])
def test_a_holding_with_no_price_or_no_day_for_it_says_so(day, price_u):
    price = {**listed(day, None, None, None, False), "price_u": price_u}
    got = of_price(price, today=TODAY, last_session_day=TODAY)
    if price_u is None or day is None:
        assert (got.state, got.words) == (OLD, NO_PRICE) == (OLD, "No price yet")
    else:
        assert got.state == FRESH


# -- the words ------------------------------------------------------------------------------------

#: Developer words and the feed's own words: a price's age says none of them.
NOT_HERE = re.compile(
    r"\b(arm|armed|arming|paper|virtual|live|real-time|http|exception|traceback|none|nan)\b",
    re.IGNORECASE,
)


def random_price(rng, last):
    """A price of any kind, usually a priced one, usually near the latest session."""
    near = rng.randint(-2, 3) if rng.random() < 0.5 else rng.randint(-2, 400)
    return {
        "price_u": None if rng.random() < 0.1 else rng.randint(1, 10**8),
        "prev_close_u": rng.choice([None, rng.randint(1, 10**8)]),
        "day": None if rng.random() < 0.1 else days.add_days(last, -near),
        "at": rng.choice([None, rng.randint(1_700_000_000_000, 1_800_000_000_000)]),
        "source": rng.choice(["feed", "quote", "amfi", "manual", None]),
        "basis": rng.choice(["last", "close", "nav", "manual", None]),
        "late": rng.random() < 0.3,
    }


def test_the_words_are_plain_and_an_old_price_always_says_why():
    rng = random.Random(16)
    seen = Counter()
    for _ in range(3000):
        today = days.add_days(20250101, rng.randint(0, 900))
        last = days.add_days(today, -rng.randint(0, 3))
        price = random_price(rng, last)
        got = of_price(
            price,
            today=today,
            last_session_day=last,
            old_days=rng.randint(-10, 500),
            holidays=[days.add_days(last, -n) for n in rng.sample(range(0, 30), 3)],
            noun=rng.choice(["Value", "Balance"]),
        )
        assert got.state in kinds.FRESHNESS_STATES
        seen[got.state] += 1
        if got.state == OLD:
            assert got.words, price  # never old in silence
        if got.words is None:
            continue
        seen["said"] += 1
        assert all(ch.isascii() or ch == DOT.strip() for ch in got.words), got.words
        assert "  " not in got.words and got.words == got.words.strip()
        assert not NOT_HERE.search(got.words), got.words
        assert "{" not in got.words and len(got.words) < 100
        assert got.as_dict() == {"state": got.state, "words": got.words}
    assert seen[OLD] > 500 and seen[FRESH] > 500 and seen["said"] > 1500


def test_a_day_is_written_as_the_day_and_a_short_month_and_the_year_only_when_it_differs():
    for month in range(1, 13):
        day = 20260005 + month * 100
        assert when(day, 20261001) == f"5 {MONTHS[month - 1]}"
        assert when(day - 10000, 20261001) == f"5 {MONTHS[month - 1]} 2025"
        assert when(day + 10000, 20261001) == f"5 {MONTHS[month - 1]} 2027"
    assert when(20261201, 20261027) == "1 Dec"  # later in the same year is still the same year
    assert when(20251231, 20260102) == "31 Dec 2025"
    assert when(20270101, 20261231) == "1 Jan 2027"


def test_the_freshness_has_the_fields_the_phone_declares():
    declared = {
        name: set(members(body))
        for name, body in interfaces(TYPES_TS.read_text(encoding="utf-8")).items()
    }
    assert set(Freshness(FRESH, None).as_dict()) == declared["Freshness"]
    assert (FRESH, OLD) == kinds.FRESHNESS_STATES
