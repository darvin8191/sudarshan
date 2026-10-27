"""Tick grain: levels snapped once with Decimal, feed prices in whole ticks, compares in integers."""

from __future__ import annotations

import random
from decimal import Decimal

import pytest

from sudarshan.alerts.market.grain import INDEX_TICK, Grain, to_decimal

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
    ("tick", "step", "places"),
    [
        ("0.05", Decimal("0.05"), 2),
        (0.05, Decimal("0.05"), 2),
        ("0.050", Decimal("0.05"), 2),
        ("0.0025", Decimal("0.0025"), 4),
        ("0.1", Decimal("0.1"), 2),
        ("1", Decimal("1"), 2),
        ("10", Decimal("1E+1"), 2),
        (INDEX_TICK, Decimal("0.01"), 2),
    ],
)
def test_grain_of_reads_the_tick_and_its_decimals(tick, step, places):
    grain = Grain.of(tick)
    assert grain.step == step and grain.places == places and grain.size == float(step)


@pytest.mark.parametrize("tick", [None, 0, "0", -0.05, "NaN", "x", float("inf")])
def test_grain_of_refuses_what_is_not_a_tick(tick):
    assert Grain.of(tick) is None


@pytest.mark.parametrize(
    ("level", "up", "down"),
    [
        ("100", 2000, 2000),
        ("100.03", 2001, 2000),  # off the grid: the next tick in each direction
        ("100.05", 2001, 2001),
        ("0.15", 3, 3),  # exactly three ticks; the float ratio is 2.9999999999999996
        ("1420.797", 28416, 28415),
        ("0.01", 1, 0),
    ],
)
def test_levels_snap_up_and_down_exactly(level, up, down):
    assert G05.up(Decimal(level)) == up and G05.down(Decimal(level)) == down


@pytest.mark.parametrize(
    ("price", "ticks"),
    [
        (100.05, 2001),
        (100.04999999999999, 2001),  # float noise around a grid price
        (0.15, 3),
        (0.05, 1),
        (99.95, 1999),
    ],
)
def test_feed_prices_in_whole_ticks(price, ticks):
    assert G05.ticks(price) == ticks


@pytest.mark.parametrize("price", [0, 0.0, -1.0, float("nan"), float("inf"), None, "100.05"])
def test_a_feed_value_that_is_not_a_price_is_none(price):
    assert G05.ticks(price) is None


@pytest.mark.parametrize("tick", ["0.05", "0.01", "0.0025", "0.1", "0.5", "1"])
def test_every_grid_price_reads_back_as_its_own_tick_count(tick):
    """A float built from k ticks (as the feed's JSON carries it) snaps back to exactly k."""
    grain = Grain.of(tick)
    rng = random.Random(20261021)
    counts = [*range(1, 3000), *(rng.randrange(1, 10**8) for _ in range(20000)), 10**9]
    for k in counts:
        assert grain.ticks(float(grain.step * k)) == k, (tick, k)


def test_price_is_exact_with_the_grains_decimals():
    assert str(G05.price(2001)) == "100.05"
    assert str(G05.price(2000)) == "100.00"
    assert str(Grain.of("0.0025").price(33249)) == "83.1225"
    assert str(Grain.of("1").price(1235)) == "1235.00"
    assert str(Grain.of("10").price(3)) == "30.00"
