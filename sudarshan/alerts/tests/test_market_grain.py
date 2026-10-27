"""Prices in paise: levels converted once with Decimal, feed prices rounded to the paisa, compares
in integers. The tick size is only the hint of where a trade meets an off-tick level."""

from __future__ import annotations

import random
from decimal import Decimal

import pytest

from sudarshan.alerts.market.grain import (
    INDEX_TICK,
    Grain,
    edge_down,
    edge_up,
    paise,
    rupees,
    to_decimal,
)

G05 = Grain.of("0.05")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (100, Decimal("100")),
        (0.15, Decimal("0.15")),  # repr, not the binary expansion 0.1499999999999999944...
        ("25,900.50", Decimal("25900.50")),
        (" 1399.8 ", Decimal("1399.8")),
        (Decimal("2.5"), Decimal("2.5")),
        ("NaN", None),
        (float("inf"), None),
        ("abc", None),
        ("", None),
        (None, None),
        (True, None),
        ([1], None),
    ],
)
def test_to_decimal(value, expected):
    assert to_decimal(value) == expected


@pytest.mark.parametrize(
    ("tick", "step", "whole"),
    [
        ("0.05", Decimal("0.05"), True),
        (0.05, Decimal("0.05"), True),
        ("0.050", Decimal("0.05"), True),
        ("0.01", Decimal("0.01"), True),
        ("0.1", Decimal("0.1"), True),
        ("1", Decimal("1"), True),
        ("10", Decimal("1E+1"), True),
        (INDEX_TICK, Decimal("0.01"), True),
        ("0.0025", Decimal("0.0025"), False),  # currency derivatives: below a paisa
        ("0.005", Decimal("0.005"), False),
    ],
)
def test_grain_of_reads_the_tick_and_whether_it_is_whole_paise(tick, step, whole):
    grain = Grain.of(tick)
    assert grain.step == step and grain.whole_paise is whole


@pytest.mark.parametrize("tick", [None, 0, "0", -0.05, "NaN", "x", float("inf")])
def test_grain_of_refuses_what_is_not_a_tick(tick):
    assert Grain.of(tick) is None


@pytest.mark.parametrize(
    ("level", "up", "down"),
    [
        ("100", 10000, 10000),
        ("100.03", 10003, 10003),  # off a 0.05 tick but on the paisa: checked as set
        ("0.15", 15, 15),
        ("250", 25000, 25000),
        ("1420.797", 142080, 142079),  # off the paisa: the next paisa in each direction
        ("249.995", 25000, 24999),
        ("0.01", 1, 1),
        ("0.005", 1, 0),
    ],
)
def test_levels_convert_to_paise_up_and_down_exactly(level, up, down):
    assert edge_up(Decimal(level)) == up and edge_down(Decimal(level)) == down


@pytest.mark.parametrize(
    ("price", "count"),
    [
        (100.05, 10005),
        (100.04999999999999, 10005),  # float noise around a paisa
        (0.15, 15),
        (0.01, 1),
        (99.95, 9995),
        (249.98, 24998),  # read on a 0.05 grid this was 250.00, a level it is below
        (100, 10000),
        (25903.15, 2590315),
    ],
)
def test_feed_prices_in_paise(price, count):
    assert paise(price) == count


@pytest.mark.parametrize(
    "price", [0, 0.0, -1.0, float("nan"), float("inf"), None, "100.05", True, False]
)
def test_a_feed_value_that_is_not_a_price_is_none(price):
    assert paise(price) is None


def test_a_price_below_the_level_is_below_it_whatever_the_tick():
    """NSE moves a stock from a 0.05 to a 0.01 tick when its price band changes: 249.98 stays
    below 250 although the alert was set when the tick was 0.05."""
    assert paise(249.98) < edge_up(Decimal("250")) <= paise(250.0)
    assert paise(250.01) > edge_down(Decimal("250"))


def test_every_paisa_price_reads_back_as_its_own_count():
    """A float carrying k paise (as the feed's JSON does) rounds back to exactly k."""
    rng = random.Random(20261021)
    counts = [*range(1, 30000), *(rng.randrange(1, 10**10) for _ in range(20000)), 10**10]
    for k in counts:
        assert paise(float(Decimal(k) / 100)) == k, k
        assert paise(k / 100) == k, k


@pytest.mark.parametrize(
    ("count", "text"),
    [(10005, "100.05"), (10000, "100.00"), (2590315, "25903.15"), (1, "0.01"), (15, "0.15")],
)
def test_rupees_is_exact_with_two_decimals(count, text):
    assert str(rupees(count)) == text


@pytest.mark.parametrize(
    ("tick", "level", "up", "down"),
    [
        ("0.05", "100.03", "100.05", "100.00"),
        ("0.05", "100", "100", "100"),
        ("0.05", "0.15", "0.15", "0.15"),  # exactly three ticks, though 0.15 / 0.05 in float
        ("0.1", "250.05", "250.1", "250.0"),
        ("1", "1234.5", "1235", "1234"),
    ],
)
def test_the_tick_hint_is_where_a_trade_meets_an_off_tick_level(tick, level, up, down):
    grain = Grain.of(tick)
    assert grain.up(Decimal(level)) == Decimal(up) and grain.down(Decimal(level)) == Decimal(down)
