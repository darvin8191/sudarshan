"""The engine's decision on one observation: sides, frequencies, the wait, the stops, resets."""

from __future__ import annotations

import dataclasses
from datetime import datetime

from sudarshan.alerts.market import engine, spec
from sudarshan.alerts.market.engine import Obs, State
from sudarshan.autologin.schedule import IST

DAY = 20261021
T0 = int(datetime(2026, 10, 21, 10, 0, tzinfo=IST).timestamp() * 1000)
MIN = 60_000
SBIN = {
    "instrument_key": "NSE_EQ|INE062A01020",
    "symbol": "SBIN",
    "exchange": "NSE",
    "tick_size": "0.05",
}


def alert(op="crosses_above", level="100", **fields) -> spec.Alert:
    row = spec.check(
        SBIN | {"kind": "price", "params": {"op": op, "level": level}} | fields, T0 - 60 * MIN
    )
    return spec.Alert.of(row | {"id": 9, "version": 4, "status": "active"})


def obs(a: spec.Alert, price: float, at: int, *, candle=0, end=0, day=DAY) -> Obs:
    return Obs(a.grain.ticks(price), at, day, a.cond.up, a.cond.down, candle, end)


def feed(a: spec.Alert, ticks, state=None):
    """Run (price, at, candle, end) values through ``step``; returns (state, fires, last outcome)."""
    state, fires, last = state or State(), [], None
    for price, at, *candle in ticks:
        k, end = candle or (0, 0)
        last = engine.step(a, state, obs(a, price, at, candle=k, end=end), at)
        state = last.state
        fires += [last.fire] if last.fire else []
    return state, fires, last


def test_once_per_candle_fires_once_in_each_candle():
    a = alert(frequency="per_candle", candle=15)
    k0, k1 = (0, T0 + 15 * MIN), (1, T0 + 30 * MIN)
    state, fires, _ = feed(a, [
        (99.95, T0, *k0), (100.0, T0 + 1 * MIN, *k0), (99.95, T0 + 2 * MIN, *k0),
        (100.0, T0 + 3 * MIN, *k0), (99.95, T0 + 16 * MIN, *k1), (100.0, T0 + 17 * MIN, *k1),
    ])  # fmt: skip
    assert [f.at for f in fires] == [T0 + 1 * MIN, T0 + 17 * MIN]
    assert [f.claim for f in fires] == ["9:v4:20261021:c0", "9:v4:20261021:c1"]
    assert state.side == 0 and state.last_fired_candle == 1


def test_at_candle_close_judges_the_last_value_once_a_later_trade_comes():
    a = alert(frequency="at_close", candle=15)
    end0, end1 = T0 + 15 * MIN, T0 + 30 * MIN
    state, fires, _ = feed(a, [(99.95, T0 + 1 * MIN, 0, end0), (100.05, T0 + 2 * MIN, 0, end0)])
    assert not fires and state.side is None  # nothing is judged inside the candle
    state, fires, _ = feed(a, [(99.0, T0 + 16 * MIN, 1, end1)], state)
    assert not fires and state.side == 1  # candle 0 closed at 100.05: the first close seeds
    state, fires, _ = feed(a, [(100.10, T0 + 31 * MIN, 2, T0 + 45 * MIN)], state)
    assert not fires and state.side == -1  # candle 1 closed at 99.00: below, a fall
    state, fires, _ = feed(a, [(99.5, T0 + 46 * MIN, 3, T0 + 60 * MIN)], state)
    assert [(f.value, f.at, f.trade_time, f.claim) for f in fires] == [
        (2002, T0 + 46 * MIN, T0 + 31 * MIN, "9:v4:20261021:x2")
    ]  # candle 2 closed at 100.10 after 99.00: crossed above, judged at the next trade


def test_at_candle_close_by_the_timer_two_seconds_after_the_end():
    a = alert(op="at_or_above", frequency="at_close", candle=5)
    end = T0 + 5 * MIN
    state, _, _ = feed(a, [(100.05, T0 + 4 * MIN + 58_000, 0, end)])
    assert engine.clock(a, state, end + 1_999) is None
    out = engine.clock(a, state, end + 2_000)
    assert out.fire.value == 2001 and out.fire.trade_time == T0 + 4 * MIN + 58_000
    assert out.fire.at == end + 2_000 and out.state.forming is None
    assert engine.clock(a, out.state, end + 60_000) is None  # judged once


def test_a_value_older_than_the_forming_candle_is_ignored():
    a = alert(frequency="at_close", candle=5)
    state, _, _ = feed(a, [(100.0, T0 + 6 * MIN, 1, T0 + 10 * MIN)])
    out = engine.step(
        a, state, obs(a, 120.0, T0 + 4 * MIN, candle=0, end=T0 + 5 * MIN), T0 + 6 * MIN
    )
    assert out.state == state and out.fire is None


def test_an_end_judges_a_candle_that_closed_by_then_and_drops_one_that_did_not():
    ends = "2026-10-21T10:15:00+05:30"
    a = alert(op="at_or_above", frequency="at_close", candle=15, ends=ends)
    state, _, _ = feed(a, [(100.05, T0 + 14 * MIN, 0, T0 + 15 * MIN)])
    out = engine.clock(a, state, T0 + 15 * MIN)  # the end, before the 2 s timer
    assert out.fire is not None and (out.status, out.reason.code) == ("stopped", "ended")
    b = alert(op="at_or_above", frequency="at_close", candle=30, ends=ends)
    state, _, _ = feed(b, [(100.05, T0 + 14 * MIN, 0, T0 + 30 * MIN)])
    out = engine.clock(b, state, T0 + 15 * MIN)
    assert out.fire is None and out.status == "stopped"


def test_once_at_close_stops_when_its_close_fires():
    a = alert(op="at_or_above", frequency="at_close", candle=1, ends="2026-10-21T10:01:00+05:30")
    state, _, _ = feed(a, [(100.05, T0 + 30_000, 0, T0 + MIN)])
    a = dataclasses.replace(a, frequency=spec.ONCE)
    out = engine.clock(a, state, T0 + MIN)
    assert out.reason.code == "fired_once" and out.fire is not None


def test_a_new_session_seeds_again_and_starts_the_counts():
    a = alert(frequency="every_time")
    state, fires, _ = feed(a, [(99.95, T0), (100.0, T0 + 1000)])
    assert state.fires_session == 1 and state.fires == 1
    out = engine.step(a, state, obs(a, 100.05, T0 + 86_400_000, day=DAY + 1), T0 + 86_400_000)
    assert out.fire is None and out.state.side == 1  # seeded, not fired
    assert (out.state.session, out.state.fires_session, out.state.fires) == (DAY + 1, 0, 1)
    assert out.state.last_fired_candle is None and out.state.ref == 2001


def test_resume_seeds_and_gives_a_new_allowance():
    a = alert(frequency="every_time")
    state = State(DAY, -1, 1999, T0, T0, 0, 60, 50, 0)
    paused = engine.step(a, state, obs(a, 100.0, T0 + 1000), T0 + 1000)
    assert (paused.status, paused.reason.code, paused.fire) == ("paused", "fire_limit", None)
    resumed = engine.resume(paused.state)
    assert resumed.side is None and resumed.quota_base == 50 and resumed.fires_session == 50
    state, fires, _ = feed(
        a, [(100.05, T0 + 2000), (99.95, T0 + 3000), (100.0, T0 + 4000)], resumed
    )
    assert [f.n for f in fires] == [51] and fires[0].claim == "9:v4:20261021:51"


def test_claims_per_frequency():
    for frequency, expected in [
        (spec.ONCE, "9:v4:once"),
        (spec.EVERY_TIME, "9:v4:20261021:3"),
        (spec.PER_CANDLE, "9:v4:20261021:c7"),
        (spec.AT_CLOSE, "9:v4:20261021:x7"),
    ]:
        assert (
            engine.claim(dataclasses.replace(alert(), frequency=frequency), DAY, 7, 3) == expected
        )


def test_an_alert_that_is_not_active_never_fires():
    a = dataclasses.replace(alert(op="at_or_above"), status="paused")
    out = engine.step(a, State(), obs(a, 101.0, T0), T0)
    assert out == engine.Outcome(9, State())
    assert engine.clock(a, State(), T0 + 10**12) is None


def test_the_wait_counts_from_the_fire_inclusive():
    a = alert(op="at_or_above", frequency="every_time", cooldown_s=60)
    _, fires, _ = feed(a, [(100.0, T0), (100.0, T0 + 59_999), (100.0, T0 + 60_000)])
    assert [f.at for f in fires] == [T0, T0 + 60_000]
