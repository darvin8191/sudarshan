"""M7.31 (P7 lane R): a tab's day returns, and beta, correlation and excess return against an
index. Vectors: vectors/index_stats.json (make_index_stats.py states the arithmetic)."""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

import pytest

from sudarshan.portfolio.core import history, kinds
from sudarshan.portfolio.core.history import DayReturn, day_returns, index_stats
from sudarshan.portfolio.core.performance import Period, Point, periods

FILE = json.loads((Path(__file__).parent / "vectors" / "index_stats.json").read_text())
BY_ID = {case["id"]: case for case in FILE["stats"]}


def stats_of(case, **changes):
    case = {**case, **changes}
    return index_stats(
        case["benchmark"],
        [DayReturn(*row) for row in case["returns"]],
        [tuple(pair) for pair in case["closes"]],
        case["as_of_day"],
        case["twr_pct"],
        case["index_twr_pct"],
        case["holidays"],
    ).as_dict()


@pytest.mark.parametrize("case", FILE["returns"], ids=lambda case: case["id"])
def test_day_returns_are_the_vectors(case):
    got = day_returns([Period(*row) for row in case["periods"]], case["holidays"])
    assert [list(found) for found in got] == case["expect"]


@pytest.mark.parametrize("case", FILE["stats"], ids=lambda case: case["id"])
def test_index_statistics_are_the_vectors(case):
    assert stats_of(case) == case["expect"]


def test_the_limits_are_the_contracts():
    assert (kinds.STAT_DAYS, kinds.STAT_MIN_DAYS) == (60, 20)
    assert stats_of(BY_ID["X2"])["stat_days"] == kinds.STAT_MIN_DAYS - 1


def test_nineteen_days_show_no_figure_at_all():
    """Mutation 16: beta shown under 20 days."""
    got = stats_of(BY_ID["X2"])
    assert got["beta"] is None and got["correlation"] is None


def test_a_days_return_is_its_growth_less_one():
    assert DayReturn(20261102, 20261103, 99, 100).rate == Fraction(-1, 100)


def test_day_returns_read_the_periods_of_real_points():
    """Money put in on a day is at work from its start and is no gain: 1,000 becomes 1,010, then
    1,000 more is added and the 2,010 at work closes at 2,030.10 (+1%): two returns of 1%."""
    points = [
        Point(20261102, 100_000, in_paise=100_000),
        Point(20261103, 101_000),
        Point(20261104, 203_010, in_paise=100_000),
    ]
    got = day_returns(periods(points, 0))
    assert [(found.start, found.day, found.rate) for found in got] == [
        (20261102, 20261103, Fraction(1, 100)),
        (20261103, 20261104, Fraction(1, 100)),
    ]


def test_the_report_carries_the_statistics_beside_the_same_money_figures():
    case = BY_ID["X1"]
    got = history.compared(
        "nifty50", [], [tuple(pair) for pair in case["closes"]], case["as_of_day"], None, None,
        [DayReturn(*row) for row in case["returns"]], None,
    )  # fmt: skip
    assert (got["beta"], got["correlation"], got["stat_days"]) == ("1.2308", "0.9253", 20)
    assert got["benchmark"] == "nifty50" and got["excess_pct"] is None
    assert list(got)[:7] == list(
        history.Benchmark("nifty50", "", None, None, None, False, []).as_dict()
    )


def test_the_excess_return_is_the_two_shown_figures_apart():
    span = (20261005, 20261102)
    case = BY_ID["X1"]
    closes = [tuple(pair) for pair in case["closes"]]
    got = history.compared("nifty50", [], closes, case["as_of_day"], None, span, [], "30.0000")
    assert got["twr_pct"] is not None
    assert Fraction(got["excess_pct"]) == Fraction("30.0000") - Fraction(got["twr_pct"])
    assert (
        history.compared("nifty50", [], closes, 20261102, None, None, [], "30.0000")["excess_pct"]
        is None
    )  # no index figure over the span: nothing to subtract
