"""core/days.py (PA1): days as YYYYMMDD, the year-held rule, financial years."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

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
