"""core/money.py (PA1): the one place a figure is rounded, and the checks on a number's range.

The golden file is vectors/money.json; the rest compares the code with Decimal's own rounding on
many numbers, and the feed price with the alerts' ``grain.paise``, which this package may not
import.
"""

from __future__ import annotations

import json
import math
import random
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import pytest

from sudarshan.alerts.market import grain
from sudarshan.portfolio.core import kinds, money

VECTORS = json.loads((Path(__file__).parent / "vectors" / "money.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", VECTORS["round_div"], ids=lambda c: f"{c['n']}/{c['d']}")
def test_round_div_vectors(case):
    assert money.round_div(case["n"], case["d"]) == case["expect"]


def test_round_div_agrees_with_decimal_half_up_on_many_numbers():
    rng = random.Random(20261027)
    checked = 0
    for _ in range(4000):
        d = rng.choice([1, 2, 3, 7, 10, 100, 1000, 10**8, rng.randint(1, 10**12)])
        n = rng.randint(-(10**15), 10**15)
        want = (Decimal(n) / Decimal(d)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        assert money.round_div(n, d) == int(want), (n, d)
        checked += 1
    for _ in range(2000):  # exact ties: n / d = k + 1/2
        d = 2 * rng.randint(1, 10**6)
        n = rng.randint(-(10**9), 10**9) * d + d // 2 * rng.choice([1, -1])
        want = (Decimal(n) / Decimal(d)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        assert money.round_div(n, d) == int(want), (n, d)
        checked += 1
    assert checked == 6000


def test_a_tie_goes_away_from_zero_so_a_loss_mirrors_a_gain():
    for n in range(1, 200, 2):
        assert money.round_div(n, 2) == -money.round_div(-n, 2) == (n + 1) // 2


@pytest.mark.parametrize("case", VECTORS["value"], ids=lambda c: c["id"])
def test_value_vectors(case):
    assert money.value_paise(case["qty_u"], case["price_u"]) == case["expect"]
    assert case["why"]


def test_value_is_exact_beyond_a_browser_number():
    # The product is far past 2^53; nothing is lost in it.
    qty, price = kinds.MAX_QTY_U, kinds.MAX_PRICE_U
    assert qty * price > 2**53
    assert money.value_paise(qty, price) == qty * price // kinds.VALUE_DIVISOR


@pytest.mark.parametrize("case", VECTORS["unit_price"], ids=lambda c: f"{c['cost_paise']}")
def test_unit_price_vectors(case):
    assert money.unit_price_u(case["cost_paise"], case["qty_u"]) == case["expect"]


@pytest.mark.parametrize(
    "case", VECTORS["share_of_cost"], ids=lambda c: f"{c['cost_paise']}-{c['part_qty_u']}"
)
def test_share_of_cost_vectors(case):
    got = money.share_of_cost(case["cost_paise"], case["part_qty_u"], case["whole_qty_u"])
    assert got == case["expect"]


def test_the_last_unit_takes_what_is_left_whatever_the_rounding_did():
    rng = random.Random(7)
    for _ in range(300):
        cost = rng.randint(0, 10**7)
        units = rng.randint(1, 40)
        left_cost, left_units, taken = cost, units * kinds.QTY_SCALE, 0
        while left_units:
            part = rng.randint(1, left_units // kinds.QTY_SCALE) * kinds.QTY_SCALE
            share = money.share_of_cost(left_cost, part, left_units)
            assert 0 <= share <= left_cost
            left_cost, left_units, taken = left_cost - share, left_units - part, taken + share
        assert left_cost == 0
        assert taken == cost


@pytest.mark.parametrize("case", VECTORS["pct"], ids=lambda c: f"{c['n']}/{c['d']}")
def test_pct_vectors(case):
    assert money.pct(case["n"], case["d"]) == case["expect"]


@pytest.mark.parametrize("case", VECTORS["pct_of_rate"], ids=lambda c: c["rate"])
def test_pct_of_rate_vectors(case):
    assert money.pct_of_rate(Decimal(case["rate"])) == case["expect"]


def test_a_percentage_is_never_minus_zero_and_always_has_four_decimals():
    for scaled in range(-30, 31):
        text = money.format_pct(scaled)
        assert text != "-0.0000"
        assert len(text.split(".")[1]) == kinds.PCT_DECIMALS
    assert money.format_pct(0) == "0.0000"
    assert money.format_pct(-1) == "-0.0001"
    assert money.format_pct(80000) == "8.0000"


def test_pct_agrees_with_decimal_on_many_ratios():
    rng = random.Random(3)
    for _ in range(3000):
        d = rng.choice([1, 3, 7, 100, 12345, rng.randint(1, 10**9)])
        n = rng.randint(-(10**9), 10**9)
        exact = (Decimal(n) * 100 / Decimal(d)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        text = format(exact, "f")
        want = "0.0000" if Decimal(text) == 0 else text
        assert money.pct(n, d) == want, (n, d)


@pytest.mark.parametrize("case", VECTORS["feed_price_u"], ids=lambda c: repr(c["v"]))
def test_feed_price_vectors(case):
    assert money.feed_price_u(case["v"]) == case["expect"]


def test_feed_price_is_the_alerts_paise_times_a_hundred():
    rng = random.Random(11)
    values = [None, True, False, "12", [1], 0, 0.0, -1, -0.5, math.nan, math.inf, -math.inf]
    values += [0.004, 0.005, 0.015, 0.025, 0.995, 1e-9, 1e18, 249.98, 1428.25, 25900.0]
    values += [round(rng.uniform(0, 100_000), 2) for _ in range(3000)]
    values += [rng.randint(0, 10**7) for _ in range(500)]
    values += [round(rng.uniform(0, 100_000), 2) * (1 + 1e-13) for _ in range(500)]
    for value in values:
        paise = grain.paise(value)
        assert money.feed_price_u(value) == (None if paise is None else paise * 100), value


def test_the_scales_the_helpers_use_are_the_contracts():
    assert 10**money.QTY_DECIMALS == kinds.QTY_SCALE
    assert money.PAISE_TO_PRICE_U == 100
    assert money.PCT_SCALE == 10_000
    assert money.value_paise(kinds.QTY_SCALE, kinds.PRICE_SCALE) == kinds.PAISE_PER_RUPEE


def test_range_checks_have_exact_edges_and_refuse_bools_and_floats():
    assert money.qty_ok(1) and money.qty_ok(kinds.MAX_QTY_U)
    assert not money.qty_ok(0) and not money.qty_ok(kinds.MAX_QTY_U + 1) and not money.qty_ok(-1)
    assert money.price_ok(0) and money.price_ok(kinds.MAX_PRICE_U)
    assert not money.price_ok(-1) and not money.price_ok(kinds.MAX_PRICE_U + 1)
    assert money.paise_ok(0) and money.paise_ok(kinds.MAX_PAISE)
    assert not money.paise_ok(-1) and not money.paise_ok(kinds.MAX_PAISE + 1)
    for check in (money.qty_ok, money.price_ok, money.paise_ok):
        for bad in (True, 1.0, "1", None, [1]):
            assert not check(bad), (check.__name__, bad)


def test_step_ok_counts_decimals():
    assert money.step_ok(10_000_000, 0) and not money.step_ok(10_500_000, 0)
    assert money.step_ok(109_459_000, 3) and not money.step_ok(109_459_100, 3)
    assert money.step_ok(109_459_100, 4) and not money.step_ok(109_459_010, 4)
    assert money.step_ok(1, 6) and not money.step_ok(1, 5)
    assert not money.step_ok(1_000_000, 7) and not money.step_ok(1_000_000, -1)
