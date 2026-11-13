"""Metrics (JA2): vectors M1-M9, the live open-trade cases L1-L4, buckets, calendar, blocks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sudarshan.journal.core import kinds, metrics

DATA = json.loads((Path(__file__).parent / "vectors" / "metrics.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("vec", DATA["metrics"], ids=[v["id"] for v in DATA["metrics"]])
def test_metric_vector(vec):
    got = metrics.compute(vec["trips"])
    for key, want in vec["expect"].items():
        assert got[key] == want, (vec["id"], key, got[key], want)


def test_the_block_has_every_field_of_the_contract():
    got = metrics.compute([{"net_paise": 100}])
    assert set(got) == {f.name for f in kinds.record("Metrics")}


def test_m1_counts_a_flat_trade_as_neither_win_nor_loss():
    """Mutation 6: a flat counted as a loss would make M1's win rate 50.00."""
    got = metrics.compute(DATA["metrics"][0]["trips"])
    assert (got["wins"], got["losses"], got["flats"]) == (3, 2, 1)
    assert got["win_rate"] == "60.00"


def test_no_losses_is_never_infinity_and_never_a_number():
    got = metrics.compute([{"net_paise": 100}, {"net_paise": 200}])
    assert got["profit_factor"] is None
    assert got["profit_factor_state"] == "no_losing_trades"


def test_expectancy_divides_by_closed_trips_not_wins():
    got = metrics.compute(DATA["metrics"][0]["trips"])
    assert got["expectancy_paise"] == 6625  # 397.50 / 6, not / 3 (132.50)


def test_r_is_not_worked_out_from_a_missing_or_zero_risk():
    for risk in (None, 0, -5):
        got = metrics.compute([{"net_paise": 500, "risk_paise": risk}])
        assert got["r_known"] == 0 and got["r_total"] is None and got["r_avg"] is None
        assert metrics.r_multiple({"net_paise": 500, "risk_paise": risk}) is None
    assert metrics.r_multiple({"net_paise": 81250, "risk_paise": 50000}) == "1.6250"
    assert metrics.r_multiple({"net_paise": -32500, "risk_paise": 50000}) == "-0.6500"


@pytest.mark.parametrize("vec", DATA["live"], ids=[v["id"] for v in DATA["live"]])
def test_live_open_pnl(vec):
    for tick in vec["ticks"]:
        got = metrics.open_pnl_paise(vec["side"], vec["qty"], vec["entry_avg_u"], tick["ltp_u"])
        assert got == tick["pnl_paise"], (vec["id"], tick)


def test_open_pnl_refuses_an_unknown_side():
    with pytest.raises(ValueError):
        metrics.open_pnl_paise("flat", 1, 1, 1)


def hold(open_day, close_day, hold_s):
    return {"net_paise": 100, "open_day": open_day, "close_day": close_day, "hold_s": hold_s}


@pytest.mark.parametrize(
    ("trip", "bucket"),
    [
        (hold(20261109, 20261109, 0), "under_5m"),
        (hold(20261109, 20261109, 299), "under_5m"),
        (hold(20261109, 20261109, 300), "5_30m"),
        (hold(20261109, 20261109, 1799), "5_30m"),
        (hold(20261109, 20261109, 1800), "30m_2h"),
        (hold(20261109, 20261109, 7199), "30m_2h"),
        (hold(20261109, 20261109, 7200), "over_2h"),
        (hold(20261102, 20261103, 80000), "1_5d"),
        (hold(20261102, 20261105, 262800), "1_5d"),
        (hold(20261102, 20261107, 500000), "1_5d"),
        (hold(20261102, 20261108, 600000), "over_5d"),
    ],
)
def test_hold_buckets(trip, bucket):
    assert metrics.hold_bucket(trip) == bucket


def test_hold_bucket_rows_are_all_there_in_order():
    rows = metrics.hold_buckets([hold(20261109, 20261109, 60), hold(20261109, 20261109, 90)])
    assert [r["bucket"] for r in rows] == list(kinds.HOLD_BUCKETS)
    assert rows[0] == {"bucket": "under_5m", "trips": 2, "wins": 2, "net_paise": 200}
    assert all(r["trips"] == 0 for r in rows[1:])


def test_calendar_periods():
    trips = [
        {"net_paise": 500, "close_day": 20261102},
        {"net_paise": -200, "close_day": 20261102},
        {"net_paise": 300, "close_day": 20261108},  # Sunday: the Muhurat session, ISO week 45
        {"net_paise": 100, "close_day": 20261109},  # Monday: week 46
        {"net_paise": 100, "close_day": 20261201},
    ]
    cal = metrics.calendar(trips)
    assert cal["days"][0] == {"key": "20261102", "trips": 2, "wins": 1, "net_paise": 300}
    assert [(w["key"], w["net_paise"]) for w in cal["weeks"]] == [
        ("2026W45", 600),
        ("2026W46", 100),
        ("2026W49", 100),
    ]
    assert [(m["key"], m["trips"]) for m in cal["months"]] == [("202611", 4), ("202612", 1)]


def test_a_week_across_the_new_year_uses_the_iso_year():
    cal = metrics.calendar([{"net_paise": 1, "close_day": 20270101}])
    assert cal["weeks"][0]["key"] == "2026W53"


def test_blocks_by_strategy_tag_and_playbook():
    trips = [
        {"net_paise": 100, "strategy": "ORB", "tags": ["fomo", "late"], "playbook": "Breakout"},
        {"net_paise": -50, "strategy": "ORB", "strategy_override": "Manual", "tags": ["fomo"]},
        {"net_paise": 70, "strategy": "ORB"},
        {"net_paise": 10},
    ]
    by_strategy = {b["name"]: b["metrics"]["net_paise"] for b in metrics.blocks(trips, "strategy")}
    assert by_strategy == {"ORB": 170, "Manual": -40}
    by_tag = {b["name"]: b["metrics"]["trips"] for b in metrics.blocks(trips, "tag")}
    assert by_tag == {"fomo": 2, "late": 1}
    assert [b["name"] for b in metrics.blocks(trips, "playbook")] == ["Breakout"]
    with pytest.raises(ValueError):
        metrics.blocks(trips, "colour")
