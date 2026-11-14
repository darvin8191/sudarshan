"""M7.34 (P7 lane R): the goals core. Vectors: vectors/goals.json (make_goals.py states the sums)."""

from __future__ import annotations

import json
from decimal import ROUND_DOWN, Inexact, localcontext
from pathlib import Path

import pytest

from sudarshan.portfolio.core import goals

FILE = json.loads((Path(__file__).parent / "vectors" / "goals.json").read_text(encoding="utf-8"))


def by_id(group):
    return pytest.mark.parametrize("case", FILE[group], ids=lambda case: case["id"])


def monthly(case):
    return goals.required_monthly(
        case["target_paise"], case["value_now_paise"], case["months"], case["rate_bp"]
    )


@by_id("monthly")
def test_the_amount_to_add_each_month_is_the_vectors(case):
    assert monthly(case) == case["required_monthly_paise"]


@by_id("monthly")
def test_the_amount_is_whole_rupees_and_never_below_nothing(case):
    """Mutation 17: a negative amount when on track."""
    got = monthly(case)
    assert got >= 0 and got % 100 == 0 and got.__class__ is int


def test_no_months_left_counts_as_one_month():
    """Mutation 17: months of 0."""
    cases = {case["id"]: case for case in FILE["monthly"]}
    assert monthly(cases["GL5"]) == monthly(cases["GL5b"]) == monthly(cases["GL4"]) > 0


@by_id("months")
def test_whole_months_are_the_vectors(case):
    assert goals.months_left(case["as_of_day"], case["target_day"]) == case["months"]


@by_id("cards")
def test_a_goals_figures_are_the_vectors(case):
    got = goals.figures(
        case["target_paise"], case["value_now_paise"], case["as_of_day"], case["target_day"],
        case["rate_bp"],
    )  # fmt: skip
    assert got == case["expect"]


def test_the_default_return_is_ten_percent_a_year():
    assert goals.DEFAULT_RATE_BP == 1000


def test_more_time_a_higher_return_or_more_saved_never_asks_for_more():
    base = goals.required_monthly(100_000_000, 20_000_000, 60, 1000)
    assert goals.required_monthly(100_000_000, 20_000_000, 72, 1000) < base
    assert goals.required_monthly(100_000_000, 20_000_000, 60, 1200) < base
    assert goals.required_monthly(100_000_000, 30_000_000, 60, 1000) < base
    assert goals.required_monthly(120_000_000, 20_000_000, 60, 1000) > base


def test_paying_the_amount_reaches_the_target_and_a_rupee_less_does_not():
    """The amount is rounded up, so 60 payments of it at 12% do reach the target (the check is in
    exact fractions with a monthly rate rounded down, then up)."""
    from fractions import Fraction

    target, now, months = 100_000_000, 20_000_000, 60
    paid = goals.required_monthly(target, now, months, 1200)
    low, high = Fraction(94887929, 10**10), Fraction(94887930, 10**10)  # i, cut and raised

    def reached(amount, i):
        growth = (1 + i) ** months
        return now * growth + amount * (growth - 1) / i

    assert reached(paid, high) >= target
    assert reached(paid - 100, low) < target


def test_the_arithmetic_context_of_the_process_changes_nothing():
    case = FILE["monthly"][0]
    with localcontext() as ctx:
        ctx.prec, ctx.rounding = 4, ROUND_DOWN
        ctx.traps[Inexact] = True
        assert monthly(case) == case["required_monthly_paise"]
