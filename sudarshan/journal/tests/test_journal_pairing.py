"""Pairing fills into round trips (JA1): vectors P1-P14, properties, and money arithmetic."""

from __future__ import annotations

import itertools
import json
import random
from pathlib import Path

import pytest

from sudarshan.journal.core import money
from sudarshan.journal.core.pairing import pair_fills, product_class

VECTORS = json.loads(
    (Path(__file__).parent / "vectors" / "pairing.json").read_text(encoding="utf-8")
)["vectors"]
FIELDS = (
    "side",
    "status",
    "qty_max",
    "entry_qty",
    "entry_avg_u",
    "exit_qty",
    "exit_avg_u",
    "gross_paise",
    "hold_s",
    "product_class",
    "open_day",
    "close_day",
)


def shape(trip):
    out = {k: trip[k] for k in FIELDS}
    out["legs"] = [[leg["trade_id"], leg["role"], leg["qty"]] for leg in trip["legs"]]
    return out


@pytest.mark.parametrize("vector", VECTORS, ids=[v["id"] for v in VECTORS])
def test_vector(vector):
    got = [shape(t) for t in pair_fills(vector["fills"])]
    want = [dict(t) for t in vector["trips"]]
    key = lambda t: (t["side"], t["legs"][0][0])  # noqa: E731
    assert sorted(got, key=key) == sorted(want, key=key), vector["note"]


def by_id(i):
    return next(v for v in VECTORS if v["id"] == i)


def test_every_vector_p1_to_p11_is_present():
    assert {f"P{i}" for i in range(1, 12)} <= {v["id"] for v in VECTORS}


@pytest.mark.parametrize("vector", VECTORS, ids=[v["id"] for v in VECTORS])
def test_order_of_arrival_does_not_matter(vector):
    """The pairing is the same under any permutation of the fills (ties go by trade id)."""
    want = [shape(t) for t in pair_fills(vector["fills"])]
    fills = vector["fills"]
    perms = (
        list(itertools.permutations(fills))
        if len(fills) <= 4
        else [random.Random(7).sample(fills, len(fills)) for _ in range(5)]
    )
    for perm in perms:
        assert [shape(t) for t in pair_fills(perm)] == want


@pytest.mark.parametrize("vector", VECTORS, ids=[v["id"] for v in VECTORS])
def test_legs_add_up_to_the_fills(vector):
    """Sum of the legs of a trade id equals that fill's quantity, per key (withdrawn aside)."""
    fills = {}
    for f in vector["fills"]:
        if f.get("source") != "withdrawn":
            fills[(f.get("mode", "live"), f["trade_id"])] = f["qty"]
    legs = {}
    for trip in pair_fills(vector["fills"]):
        for leg in trip["legs"]:
            k = (trip["mode"], leg["trade_id"])
            legs[k] = legs.get(k, 0) + leg["qty"]
    assert legs == fills


def test_p4_the_flip_fill_is_split_in_two_legs():
    trips = pair_fills(by_id("P4")["fills"])
    legs = [
        (t["side"], leg["role"], leg["qty"])
        for t in trips
        for leg in t["legs"]
        if leg["trade_id"] == "a2"
    ]
    assert sorted(legs) == [("long", "exit", 65), ("short", "entry", 65)]


def test_a_flip_that_leaves_less_than_the_exit_does_not_open_a_trip():
    fills = [
        {**VECTORS[0]["fills"][0], "qty": 100},
        {**VECTORS[0]["fills"][1], "qty": 100},
    ]
    assert [t["status"] for t in pair_fills(fills)] == ["closed"]


def test_gross_comes_from_fill_values_not_rounded_averages():
    """P12: the average rounds to 83.0001 but the gross is 2,999.80, not 2,999.70."""
    trip = pair_fills(by_id("P12")["fills"])[0]
    rounded = (trip["exit_avg_u"] - trip["entry_avg_u"]) * trip["entry_qty"]
    assert money.u_to_paise(rounded) == 299970
    assert trip["gross_paise"] == 299980


def test_product_classes():
    assert [product_class(p) for p in ("MIS", "I", "mis")] == ["intraday"] * 3
    assert [product_class(p) for p in ("NRML", "CNC", "D")] == ["carry"] * 3
    with pytest.raises(ValueError):
        product_class("MTF")


def test_a_bad_fill_is_refused():
    base = dict(VECTORS[0]["fills"][0])
    for patch in ({"qty": 0}, {"action": "HOLD"}, {"product": "XX"}):
        with pytest.raises(ValueError):
            pair_fills([{**base, **patch}])


def test_the_day_defaults_to_the_ist_day_of_the_fill():
    fill = {k: v for k, v in VECTORS[0]["fills"][0].items() if k != "day"}
    assert pair_fills([fill])[0]["open_day"] == 20261109


def test_a_shuffled_week_pairs_the_same_as_in_order():
    fills = [f for v in VECTORS if v["id"] in ("P1", "P5") for f in v["fills"]]
    # distinct trade ids and the same instrument: rename the second vector's ids
    fills = [{**f, "trade_id": f["trade_id"] + str(i)} for i, f in enumerate(fills)]
    a = [shape(t) for t in pair_fills(fills)]
    b = [shape(t) for t in pair_fills(list(reversed(fills)))]
    assert a == b


# ---- money ------------------------------------------------------------------------------------
def test_half_up_ties_go_away_from_zero():
    assert money.div_half_up(5, 10) == 1
    assert money.div_half_up(-5, 10) == -1
    assert money.div_half_up(4, 10) == 0
    assert money.div_half_up(15, 10) == 2
    assert money.u_to_paise(50) == 1
    assert money.u_to_paise(49) == 0
    assert money.u_to_paise(-50) == -1


def test_fraction_and_ratio_strings():
    assert money.ratio_str(3, 5, 2) == "0.60"
    assert money.ratio_str(-1, 8, 2) == "-0.13"
    assert money.ratio_str(1, 1000, 2) == "0.00"
    assert money.ratio_str(1722_50, 1325_00, 2) == "1.30"
    assert money.ratio_str(7, 1, 0) == "7"
    assert money.ratio_str(-7, 2, 0) == "-4"


def test_days_and_instants():
    assert money.to_day(money.to_date(20261109)) == 20261109
    ms = VECTORS[0]["fills"][0]["exch_ts_ms"]
    assert money.ist_day(ms) == 20261109
    assert money.ist_clock(ms) == (10, 1, 0)
    assert money.calendar_days_between(20261102, 20261105) == 3
