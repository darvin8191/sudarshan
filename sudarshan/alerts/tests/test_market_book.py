"""The Book: feed ticks to observations for every Active alert on a key, and what comes back."""

from __future__ import annotations

import dataclasses
from datetime import date, datetime

from sudarshan.alerts.market import clock, sessions, spec
from sudarshan.alerts.market.book import BAD_VALUE, NOTHING_NEW, Book
from sudarshan.alerts.market.engine import State
from sudarshan.autologin.schedule import IST

DAY = date(2026, 10, 21)
KEY = "NSE_EQ|INE062A01020"
SBIN = {"instrument_key": KEY, "symbol": "SBIN", "exchange": "NSE", "tick_size": "0.05"}
T0 = int(datetime(2026, 10, 21, 10, 0, tzinfo=IST).timestamp() * 1000)


def alert(id_=1, op="crosses_above", level="100", **fields) -> spec.Alert:
    params = {"op": op, "level": level} | fields.pop("params", {})
    row = spec.check(SBIN | {"kind": "price", "params": params} | fields, T0 - 3_600_000)
    return spec.Alert.of(row | {"id": id_, "version": 1, "status": "active"})


def market(now: int, **more) -> clock.Market:
    found = {s: sessions.session(s, DAY, trading_day=True) for s in sessions.SEGMENTS}
    more.setdefault("feed", clock.Feed("connected", True, now))
    return clock.Market(20261021, found, 20261019, **more)


def tick(ltp=None, at=T0, recv=None, ltq=1, **more) -> dict:
    """A feed frame as the bus carries it."""
    frame = {"key": KEY, "ts_recv": at if recv is None else recv, "ltq": ltq} | more
    if ltp is not None:
        frame |= {"ltp": ltp, "ltt": at}
    return frame


def test_a_frame_with_junk_in_it_is_never_judged():
    book = Book()
    book.put(alert(op="at_or_above"))
    book.put(alert(2, level="120", params={"source": "bid"}))
    junk = {"key": KEY, "ltp": "812", "ltt": T0, "bid": float("nan"), "ts_recv": T0}
    assert book.on_tick(junk, T0, market(T0)).check == BAD_VALUE
    no_time = {"key": KEY, "ltp": 101.0, "ltt": True, "ts_recv": float("inf")}
    assert book.on_tick(no_time, T0, market(T0)).check == NOTHING_NEW
    assert book.state(1) == State()


def test_one_trade_is_judged_once():
    book = Book()
    book.put(alert(op="at_or_above", frequency="every_time", cooldown_s=60))
    first = book.on_tick(tick(100.05), T0, market(T0))
    again = book.on_tick(tick(100.05), T0 + 61_000, market(T0 + 61_000))  # the same trade
    assert first.check == clock.OK and len(first.outcomes) == 1
    assert again == (NOTHING_NEW, ())
    later = book.on_tick(tick(100.05, at=T0 + 61_000, ltq=3), T0 + 61_000, market(T0 + 61_000))
    assert later.outcomes[0].fire.at == T0 + 61_000


def test_a_trade_the_gate_held_back_is_judged_when_it_comes_again():
    book = Book()
    book.put(alert(op="at_or_above"))
    down = market(T0, feed=clock.Feed("backoff", False, T0))
    assert book.on_tick(tick(100.05), T0, down).check == clock.FEED_DOWN
    out = book.on_tick(tick(100.05, recv=T0 + 2000), T0 + 2000, market(T0 + 2000))
    assert out.outcomes[0].fire.value == 2001  # the feed's resend after reconnecting


def test_a_quote_change_is_judged_only_by_bid_and_ask_alerts():
    book = Book()
    book.put(alert(1))
    book.put(alert(2, level="120", params={"source": "bid"}))
    book.put(alert(3, op="crosses_below", level="120", params={"source": "ask"}))
    book.on_tick(tick(99.95, bid=119.95, ask=120.1), T0, market(T0))
    out = book.on_tick(
        tick(99.95, recv=T0 + 300, bid=120.0, ask=119.95), T0 + 300, market(T0 + 300)
    )
    assert [o.alert_id for o in out.outcomes] == [2, 3]
    assert {o.fire.trade_time for o in out.outcomes} == {T0 + 300}  # timed by arrival
    assert book.state(1).seen_at == T0  # the trade did not change: not judged again


def test_an_alert_that_stops_itself_is_judged_no_more():
    book = Book()
    book.put(alert())
    book.on_tick(tick(99.95), T0, market(T0))
    stop = book.on_tick(tick(100.0, at=T0 + 1000), T0 + 1000, market(T0 + 1000))
    assert stop.outcomes[0].status == "stopped" and book.alerts() == []
    assert book.on_tick(tick(101.0, at=T0 + 2000), T0 + 2000, market(T0 + 2000)) == (
        NOTHING_NEW,
        (),
    )
    assert book.state(1).fires == 1


def test_bad_values_are_counted_and_nothing_new_is_said():
    book = Book()
    book.put(alert())
    assert book.on_tick(tick(0.0), T0, market(T0)).check == BAD_VALUE
    other = {"key": "NSE_EQ|OTHER", "ltp": 5.0, "ltt": T0, "ts_recv": T0}
    assert book.on_tick(other, T0, market(T0)).check == NOTHING_NEW
    assert book.on_tick(tick(bid=100.0), T0, market(T0)).check == NOTHING_NEW  # no ltp alert's


def test_alerts_on_a_key_are_judged_in_id_order():
    book = Book()
    for id_ in (5, 2, 9):
        book.put(alert(id_, op="at_or_above"))
    out = book.on_tick(tick(100.05), T0, market(T0))
    assert [o.alert_id for o in out.outcomes] == [2, 5, 9]


def test_changed_hands_over_each_state_once():
    book = Book()
    book.put(alert(1))
    book.put(alert(2, level="200"))
    book.on_tick(tick(99.95), T0, market(T0))
    changed = book.changed()
    assert set(changed) == {1, 2} and changed[1].ref == 1999
    assert book.changed() == {}


def test_modes_are_the_highest_each_key_needs():
    book = Book()
    book.put(alert(1))
    assert book.modes() == {KEY: "ltpc"}
    book.put(alert(2, level="120", params={"source": "bid"}))
    assert book.modes() == {KEY: "full"}
    book.put(dataclasses.replace(alert(2), status="paused"))
    assert book.modes() == {KEY: "ltpc"}


def test_put_keeps_the_state_and_drop_forgets_it():
    book = Book()
    book.put(alert())
    book.on_tick(tick(99.95), T0, market(T0))
    book.put(dataclasses.replace(alert(), status="paused"))  # paused by the owner
    assert book.alerts() == [] and book.state(1).side == -1
    book.put(alert())
    assert book.state(1).side == -1  # kept unless the caller resets it
    assert book.drop(1).side == -1 and book.state(1) is None and book.alerts() == []
    assert book.drop(1) is None


def test_seed_only_a_crossing_and_never_at_close():
    book = Book()
    book.put(alert(1, op="at_or_above"))
    book.put(alert(2, frequency="at_close", candle=15))
    book.put(alert(3, level="120", params={"source": "ask"}))
    snapshot = {"key": KEY, "ts_recv": T0, "ltp": 99.0, "ltt": T0 - 5000, "ask": 119.95}
    assert not book.seed(1, snapshot, market(T0))
    assert not book.seed(2, snapshot, market(T0))
    assert book.seed(3, snapshot, market(T0)) and book.state(3).side == -1
    assert book.state(3).seen_at == T0  # a quote's time is its arrival
    assert not book.seed(4, snapshot, market(T0))  # not in the book


def test_on_clock_with_nothing_due():
    book = Book()
    book.put(alert(frequency="at_close", candle=15))
    assert book.on_clock(T0, market(T0)) == []
    assert State() == book.state(1)
