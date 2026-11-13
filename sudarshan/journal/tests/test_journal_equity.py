"""Equity curve, drawdown, heat map and the expiry split (JA3): vectors E1-E5."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sudarshan.journal.core import equity, kinds, metrics, splits

DATA = json.loads((Path(__file__).parent / "vectors" / "equity.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("vec", DATA["equity"], ids=[v["id"] for v in DATA["equity"]])
def test_equity_vector(vec):
    got = equity.equity_curve(vec["trips"], vec["days"], vec["capital_base_paise"])
    assert [[p["equity_paise"], p["peak_paise"], p["drawdown_paise"]] for p in got["points"]] == vec["points"]  # fmt: skip
    assert got["drawdown"] == vec["drawdown"]


def test_the_record_shapes_match_the_contract():
    got = equity.equity_curve([{"close_day": 20261104, "net_paise": 1}], [20261104])
    assert set(got["points"][0]) == {f.name for f in kinds.record("EquityPoint")}
    assert set(got["drawdown"]) == {f.name for f in kinds.record("Drawdown")}


def test_e1_duration_counts_trading_days_not_calendar_days():
    """Mutation 11: 4 Nov to 11 Nov is 7 calendar days but 4 trading days."""
    vec = DATA["equity"][0]
    got = equity.equity_curve(vec["trips"], vec["days"])
    assert got["drawdown"]["duration_days"] == 4


def test_e3_the_peak_starts_at_zero_not_at_the_first_equity():
    """Mutation 10: a peak taken from the first equity value would read E3 as no drawdown."""
    vec = DATA["equity"][2]
    got = equity.equity_curve(vec["trips"], vec["days"])
    assert got["points"][0]["peak_paise"] == 0
    assert got["drawdown"]["max_drawdown_paise"] == -30000


def test_a_trade_between_listed_days_counts_on_the_next_listed_day():
    got = equity.equity_curve([{"close_day": 20261108, "net_paise": 700}], [20261106, 20261109])
    assert [p["net_paise"] for p in got["points"]] == [0, 700]


def test_a_trade_after_the_last_day_is_not_counted():
    got = equity.equity_curve([{"close_day": 20261201, "net_paise": 700}], [20261106])
    assert got["points"][0]["equity_paise"] == 0


def test_days_must_be_ascending_and_distinct():
    for bad in ([20261106, 20261105], [20261106, 20261106]):
        with pytest.raises(ValueError):
            equity.equity_curve([], bad)


def test_no_days_is_an_empty_curve():
    got = equity.equity_curve([], [])
    assert got["points"] == [] and got["drawdown"]["duration_state"] == "none"


def test_heat_map_cells():
    heat = DATA["heat"]
    assert equity.heat_map(heat["trips"]) == heat["cells"]


def test_the_heat_map_uses_ist_not_utc():
    """09:20 IST is 03:50 UTC: the cell is hour 9, not 3."""
    cell = equity.heat_map(DATA["heat"]["trips"][:1])[0]
    assert cell["hour"] == 9


def test_expiry_split_blocks():
    trips = [
        {"net_paise": 500, "index_expiry_day": True, "own_expiry_day": True},
        {"net_paise": -200, "index_expiry_day": True, "own_expiry_day": False},
        {"net_paise": 900, "index_expiry_day": False, "own_expiry_day": False},
        {"net_paise": -100},
    ]
    got = splits.expiry_split(trips)
    assert set(got) == {f.name for f in kinds.record("ExpirySplit")}
    assert got["index_expiry"]["net_paise"] == 300 and got["index_expiry"]["trips"] == 2
    assert got["not_index_expiry"]["net_paise"] == 800
    assert got["own_expiry"]["trips"] == 1
    assert got["not_own_expiry"]["trips"] == 3
    assert got["index_expiry"] == metrics.compute(trips[:2])


def test_expiry_flags():
    assert splits.expiry_flags(20261110, 20261110, [20261110, 20261117]) == (True, True)
    assert splits.expiry_flags(20261109, 20261112, [20261110]) == (False, False)
    assert splits.expiry_flags(20261109, None, []) == (False, False)
