"""% move, day high or low and VWAP: their checks, their levels from the feed, the Book's notes."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from sudarshan.alerts.market import clock, conditions, sessions, spec
from sudarshan.alerts.market.book import BASE_CHANGED, Book
from sudarshan.alerts.market.engine import State
from sudarshan.autologin.schedule import IST

DAY = date(2026, 10, 21)
KEY = "NSE_EQ|INE062A01020"
SBIN = {"instrument_key": KEY, "symbol": "SBIN", "exchange": "NSE", "tick_size": "0.05"}
T0 = int(datetime(2026, 10, 21, 10, 0, tzinfo=IST).timestamp() * 1000)


def alert(kind: str, params: dict, id_=1, **fields) -> spec.Alert:
    row = spec.check(SBIN | {"kind": kind, "params": params} | fields, T0 - 3_600_000)
    return spec.Alert.of(row | {"id": id_, "version": 1, "status": "active"})


def market(now: int, today=20261021, day=DAY) -> clock.Market:
    found = {s: sessions.session(s, day, trading_day=True) for s in sessions.SEGMENTS}
    return clock.Market(today, found, 20261019, feed=clock.Feed("connected", True, now))


def frame(ltp: float, at: int = T0, **more) -> dict:
    return {"key": KEY, "ltp": ltp, "ltt": at, "ltq": 1, "ts_recv": at} | more


def test_kind_defaults_and_feed_modes():
    move = spec.check(SBIN | {"kind": "move", "params": {"direction": "up", "percent": "2"}}, T0)
    assert (move["frequency"], move["candle_min"], move["params"]["base"]) == (
        "per_candle",
        0,
        "close",
    )
    brk = spec.check(SBIN | {"kind": "day_break", "params": {"side": "low"}}, T0)
    assert brk["params"] == {"side": "low", "after": "09:30"} and brk["frequency"] == "per_candle"
    vwap = spec.check(SBIN | {"kind": "vwap", "params": {"direction": "either"}}, T0)
    assert (vwap["frequency"], vwap["cooldown_s"]) == ("every_time", 900)
    modes = {
        "close": alert("move", {"direction": "up", "percent": "1"}).cond.feed,
        "open": alert("move", {"base": "open", "direction": "up", "percent": "1"}).cond.feed,
        "break": alert("day_break", {"side": "high"}).cond.feed,
        "vwap": alert("vwap", {"direction": "above"}).cond.feed,
    }
    assert modes == {"close": "ltpc", "open": "full", "break": "full", "vwap": "full"}


@pytest.mark.parametrize("after", ["9:30", "09:60", "ab:cd", "09-30", 930, None, "24:00"])
def test_a_start_time_that_is_not_hh_mm_is_refused(after):
    with pytest.raises(spec.SpecError) as caught:
        spec.check(SBIN | {"kind": "day_break", "params": {"side": "high", "after": after}}, T0)
    assert caught.value.field == "after"


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"direction": "sideways", "percent": "1"}, "direction"),
        ({"direction": "up", "percent": "1001"}, "percent"),
        ({"direction": "either", "percent": "100"}, "percent"),
        ({"direction": "up", "percent": "x"}, "percent"),
    ],
)
def test_move_refusals(params, field):
    with pytest.raises(spec.SpecError) as caught:
        spec.check(SBIN | {"kind": "move", "params": params}, T0)
    assert caught.value.field == field


def test_at_candle_close_is_allowed_for_a_move_and_vwap():
    for kind, params in (
        ("move", {"direction": "up", "percent": "1"}),
        ("vwap", {"direction": "above"}),
    ):
        row = spec.check(
            SBIN | {"kind": kind, "params": params, "frequency": "at_close", "candle": 15}, T0
        )
        assert row["frequency"] == "at_close"
    with pytest.raises(spec.SpecError):
        spec.check(SBIN | {"kind": "vwap", "params": {"direction": "up"}}, T0)


def test_a_moves_level_is_worked_out_once_a_session():
    a = alert("move", {"direction": "either", "percent": "1.5"})
    got = conditions.edges(a, State(), frame(1420.0, cp=1399.8), 142000, T0, market(T0))
    assert (got.up, got.down, got.base) == (142080, 137880, 139980)  # 1420.797 and 1378.803
    again = conditions.edges(
        a, State(derived=got.derived), frame(1421.0, cp=1399.8), 142100, T0, market(T0)
    )
    assert again.derived is got.derived and again.value == 142100
    tomorrow = conditions.edges(
        a, State(derived=got.derived), frame(1421.0, cp=1399.8), 142100, T0, market(T0, 20261022)
    )
    assert tomorrow.derived is not got.derived and tomorrow.derived[0] == 20261022


@pytest.mark.parametrize(
    ("params", "extra", "code"),
    [
        ({"direction": "up", "percent": "2"}, {}, conditions.WAITING_CLOSE),
        ({"direction": "up", "percent": "2"}, {"cp": 0.0}, conditions.WAITING_CLOSE),
        ({"direction": "up", "percent": "2"}, {"cp": float("nan")}, conditions.WAITING_CLOSE),
        ({"base": "open", "direction": "up", "percent": "2"}, {}, conditions.WAITING_OPEN),
        (
            {"base": "open", "direction": "up", "percent": "2"},
            {"ohlc": [100]},
            conditions.WAITING_OPEN,
        ),
    ],
)
def test_a_move_without_its_base_waits(params, extra, code):
    a = alert("move", params)
    assert conditions.edges(a, State(), frame(101.0, **extra), 10100, T0, market(T0)) == code


def test_vwap_waits_for_the_average_price():
    a = alert("vwap", {"direction": "above"})
    assert (
        conditions.edges(a, State(), frame(100.0), 10000, T0, market(T0)) == conditions.WAITING_VWAP
    )
    assert (
        conditions.edges(a, State(), frame(100.0, atp=0.0), 10000, T0, market(T0))
        == conditions.WAITING_VWAP
    )
    got = conditions.edges(a, State(), frame(100.0, atp=100.21), 10000, T0, market(T0))
    assert (got.up, got.down) == (10021, 10021)  # on a 0.05 tick this was 100.20, below it
    got = conditions.edges(a, State(), frame(100.0, atp=100.226), 10000, T0, market(T0))
    assert (got.up, got.down) == (10023, 10022)  # off the paisa: above from 100.23, below 100.22


def test_a_break_is_not_live_before_the_start_time_or_without_a_high():
    a = alert("day_break", {"side": "high", "after": "10:00"})
    seen = State(20261021, 0, 10100, T0 - 1000)
    hi = {"high": 102.0}
    early = conditions.edges(a, seen, frame(102.0, ohlc=hi), 10200, T0 - 1, market(T0))
    assert early.up == conditions.NEVER and early.value == 10200
    live = conditions.edges(a, seen, frame(102.0, ohlc=hi), 10200, T0, market(T0))
    assert live.up == 10101 and live.base == 10100
    hi = {"high": 102.5}
    fresh = conditions.edges(a, State(), frame(102.0, ohlc=hi), 10200, T0, market(T0))
    assert fresh.up == conditions.NEVER and fresh.value == 10250  # the first value seeds the high
    yesterday = conditions.edges(a, State(20261020, 0, 10500), frame(102.0), 10200, T0, market(T0))
    assert yesterday.up == conditions.NEVER and yesterday.base is None


def test_the_book_says_a_wait_once_and_again_only_after_it_ended():
    book = Book()
    book.put(alert("move", {"direction": "up", "percent": "2"}))
    notes = [
        book.on_tick(frame(101.0, T0 + n * 1000, ltq=n), T0 + n * 1000, market(T0 + n * 1000))
        for n in range(3)
    ]
    assert [len(n.outcomes) for n in notes] == [1, 0, 0] and book.waiting(
        1
    ) == conditions.WAITING_CLOSE
    book.on_tick(frame(101.0, T0 + 4000, cp=100.0), T0 + 4000, market(T0 + 4000))
    assert book.waiting(1) is None
    out = book.on_tick(frame(101.0, T0 + 5000, ltq=9), T0 + 5000, market(T0 + 5000))
    assert out.outcomes[0].note.code == conditions.WAITING_CLOSE


def test_a_new_session_base_is_not_a_change():
    book = Book()
    book.put(alert("move", {"direction": "up", "percent": "50"}))
    book.on_tick(frame(101.0, cp=100.0), T0, market(T0))
    tomorrow = T0 + 86_400_000
    out = book.on_tick(
        frame(101.0, tomorrow, cp=101.0), tomorrow, market(tomorrow, 20261022, date(2026, 10, 22))
    )
    assert out.outcomes == ()
    later = tomorrow + 1000
    out = book.on_tick(
        frame(101.0, later, cp=100.5), later, market(later, 20261022, date(2026, 10, 22))
    )
    assert [o.note.code for o in out.outcomes] == [BASE_CHANGED]


def test_seed_sets_a_vwap_side_and_a_remembered_high():
    book = Book()
    book.put(alert("vwap", {"direction": "above"}, 1))
    book.put(alert("day_break", {"side": "high"}, 2))
    book.put(alert("move", {"direction": "up", "percent": "1"}, 3))
    snapshot = frame(100.0, T0 - 5000, atp=100.2, ohlc={"high": 100.5})
    assert book.seed(1, snapshot, market(T0)) and book.state(1).side == -1
    assert book.seed(2, snapshot, market(T0)) and book.state(2).ref == 10050
    assert not book.seed(3, snapshot, market(T0))  # a level check needs no side
    assert not book.seed(1, frame(100.0, T0 - 5000), market(T0))  # already seeded


def test_an_at_close_move_told_by_the_timer_keeps_its_base():
    book = Book()
    book.put(alert("move", {"direction": "up", "percent": "2"}, frequency="at_close", candle=15))
    book.on_tick(frame(102.5, T0 + 60_000, cp=100.0), T0 + 60_000, market(T0 + 60_000))
    due = T0 + 15 * 60_000 + 5000
    out = book.on_clock(due, market(due))
    assert out[0].fire.base == 10000 and out[0].fire.trade_time == T0 + 60_000
