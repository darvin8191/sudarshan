"""core/days.py (PA1, PA3): days as YYYYMMDD, the year-held rule, financial years, business days
and the IST day and clock of an instant."""

from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from portfolio_oracle import business_days_walked

from sudarshan.portfolio.core import days


@pytest.mark.parametrize("day", [19900101, 20240229, 20260101, 20261108, 22001231])
def test_real_days_are_days(day):
    assert days.is_day(day)


@pytest.mark.parametrize(
    "bad",
    [20250229, 20261301, 20260132, 20260100, 0, 19891231, 22010101, "20260101", 20260101.0]
    + [True, None, [20260101]],
)
def test_anything_else_is_not_a_day(bad):
    assert not days.is_day(bad)


def test_a_day_round_trips_through_a_date():
    for offset in range(0, 4000, 37):
        when = date(2020, 1, 1) + timedelta(days=offset)
        assert days.to_date(days.from_date(when)) == when
    assert days.from_date(date(2026, 11, 8)) == 20261108


@pytest.mark.parametrize(
    ("day", "count", "want"),
    [
        (20241231, 1, 20250101),
        (20250301, -1, 20250228),
        (20240228, 1, 20240229),
        (20240229, 365, 20250228),
        (20261108, 0, 20261108),
        (20260131, 30, 20260302),
    ],
)
def test_add_days(day, count, want):
    assert days.add_days(day, count) == want


def test_days_between_counts_whole_days_either_way():
    assert days.days_between(20240101, 20250101) == 366  # the design's X1 span
    assert days.days_between(20250101, 20240101) == -366
    assert days.days_between(20260501, 20261027) == 179  # the design's X3 span
    assert days.days_between(20261108, 20261108) == 0


@pytest.mark.parametrize(
    ("day", "years", "want"),
    [
        (20240229, 1, 20250228),
        (20240229, 4, 20280229),
        (20240229, -1, 20230228),
        (20250105, 1, 20260105),
        (20230315, 0, 20230315),
    ],
)
def test_add_years_never_invents_a_day(day, years, want):
    assert days.add_years(day, years) == want
    assert days.is_day(days.add_years(day, years))


@pytest.mark.parametrize(
    ("bought", "on", "over"),
    [
        (20240105, 20250105, False),  # exactly a year is not more than a year
        (20240105, 20250106, True),
        (20240105, 20240106, False),
        (20240229, 20250228, False),  # the anniversary of 29 Feb is 28 Feb
        (20240229, 20250301, True),
        (20230228, 20240228, False),
        (20230228, 20240229, True),
        (20240105, 20231231, False),  # not yet bought: never over a year
    ],
)
def test_held_over_a_year_is_after_the_first_anniversary(bought, on, over):
    assert days.held_over_year(bought, on) is over


def test_financial_year_runs_april_to_march():
    assert days.financial_year(20260331) == 2025
    assert days.financial_year(20260401) == 2026
    assert days.financial_year(20261231) == 2026
    assert days.financial_year(20270101) == 2026


def test_month_of_is_year_and_month():
    assert days.month_of(20261108) == 202611
    assert days.month_of(20260101) == 202601


# -- weekdays and business days (PA3) -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("day", "weekday"),
    [
        (20261023, True),  # Friday
        (20261024, False),  # Saturday
        (20261025, False),  # Sunday
        (20261026, True),  # Monday
        (20261027, True),  # Tuesday
        (20240229, True),  # a Thursday
        (20000101, False),  # a Saturday
        (19900101, True),  # a Monday
        (22001231, True),  # a Tuesday
    ],
)
def test_is_weekday_is_monday_to_friday(day, weekday):
    assert days.is_weekday(day) is weekday


@pytest.mark.parametrize(
    ("start", "end", "holidays", "want"),
    [
        (20261027, 20261027, (), 0),  # the same day
        (20261026, 20261027, (), 1),
        (20261023, 20261026, (), 1),  # Friday to Monday: only Monday
        (20261024, 20261026, (), 1),  # from a Saturday: only Monday
        (20261025, 20261026, (), 1),
        (20261023, 20261025, (), 0),  # Friday to Sunday: nothing
        (20261021, 20261027, (), 4),  # Wednesday to the next Tuesday
        (20261021, 20261027, (20261022,), 3),  # a Thursday holiday
        (20261021, 20261027, (20261022, 20261022), 3),  # the same holiday twice
        (20261021, 20261027, (20261024, 20261025), 4),  # holidays on a weekend change nothing
        (20261021, 20261027, (20261021,), 4),  # a holiday on the start day is not in the count
        (20261021, 20261027, (20261027,), 3),  # a holiday on the end day is
        (20261021, 20261027, (20261020, 20261028), 4),  # outside the range
        (20261021, 20261027, (20261340, 0, 20261022.5), 4),  # no real days
        (20261027, 20261021, (), 0),  # backwards is never negative
        (20261027, 20261021, (20261022,), 0),
        (20260101, 20261231, (), 260),  # a year of 261 weekdays, less the first day
    ],
)
def test_business_days_between(start, end, holidays, want):
    assert days.business_days_between(start, end, holidays) == want


def test_business_days_take_a_generator_of_holidays_too():
    assert days.business_days_between(20261021, 20261027, (d for d in [20261022])) == 3


def test_business_days_between_matches_a_walk_day_by_day():
    rng = random.Random(3)
    for _ in range(400):
        start = days.from_date(date(2019, 1, 1) + timedelta(days=rng.randint(0, 3000)))
        end = days.add_days(start, rng.randint(-5, 120))
        around = [days.add_days(start, n) for n in range(-3, 125)]
        holidays = rng.sample(around, rng.randint(0, 12))
        assert days.business_days_between(start, end, holidays) == business_days_walked(
            start, end, holidays
        ), (
            start,
            end,
            holidays,
        )


def test_business_days_match_a_walk_over_the_whole_supported_range():
    assert days.business_days_between(days.FIRST_DAY, days.LAST_DAY) == business_days_walked(
        days.FIRST_DAY, days.LAST_DAY
    )


def test_a_week_of_calendar_days_is_five_business_days_wherever_it_starts():
    for offset in range(14):
        start = days.add_days(20260105, offset)
        assert days.business_days_between(start, days.add_days(start, 7)) == 5


def test_business_days_add_up_across_a_split_point():
    rng = random.Random(8)
    for _ in range(200):
        start = days.from_date(date(2024, 1, 1) + timedelta(days=rng.randint(0, 900)))
        middle = days.add_days(start, rng.randint(0, 40))
        end = days.add_days(middle, rng.randint(0, 40))
        holidays = [days.add_days(start, rng.randint(1, 80)) for _ in range(6)]
        whole = days.business_days_between(start, end, holidays)
        parts = days.business_days_between(start, middle, holidays) + days.business_days_between(
            middle, end, holidays
        )
        assert whole == parts


# -- an instant's IST day and clock (PA3) --------------------------------------------------------------

IST = timezone(timedelta(hours=5, minutes=30))


def ist_of(epoch_ms):
    """The same instant through the standard library, without a float anywhere."""
    return (datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=epoch_ms)).astimezone(IST)


@pytest.mark.parametrize(
    ("epoch_ms", "day", "clock"),
    [
        (0, 19700101, "05:30:00"),
        (-1, 19700101, "05:29:59"),  # before the epoch the milliseconds still drop, not round
        (-19_800_000, 19700101, "00:00:00"),  # midnight IST of the first day
        (-19_800_001, 19691231, "23:59:59"),
        (1_793_077_865_000, 20261027, "10:41:05"),
        (1_793_077_865_999, 20261027, "10:41:05"),
        (1_793_039_400_000, 20261027, "00:00:00"),  # 27 Oct 2026, midnight IST
        (1_793_039_399_999, 20261026, "23:59:59"),
        (1_793_039_400_000 + 86_400_000 - 1, 20261027, "23:59:59"),
    ],
)
def test_the_ist_day_and_clock_of_an_instant(epoch_ms, day, clock):
    assert days.day_of_instant(epoch_ms) == day
    assert days.clock_of_instant(epoch_ms) == clock


def test_the_ist_day_and_clock_match_the_standard_library_on_random_instants():
    rng = random.Random(21)
    low = int(datetime(1990, 1, 1, tzinfo=UTC).timestamp() * 1000)
    high = int(datetime(2200, 12, 31, tzinfo=UTC).timestamp() * 1000)
    instants = [rng.randint(low, high) for _ in range(2000)]
    instants += [rng.randint(-5_000_000_000, 5_000_000_000) for _ in range(500)]
    for epoch_ms in instants:
        local = ist_of(epoch_ms)
        assert days.day_of_instant(epoch_ms) == days.from_date(local.date()), epoch_ms
        assert days.clock_of_instant(epoch_ms) == local.strftime("%H:%M:%S"), epoch_ms


def test_every_second_around_the_ist_midnight_belongs_to_the_right_day():
    midnight = int(datetime(2026, 10, 27, tzinfo=IST).timestamp()) * 1000
    assert days.day_of_instant(midnight - 1) == 20261026
    assert days.day_of_instant(midnight) == 20261027
    assert days.clock_of_instant(midnight) == "00:00:00"
    assert days.clock_of_instant(midnight - 1) == "23:59:59"
