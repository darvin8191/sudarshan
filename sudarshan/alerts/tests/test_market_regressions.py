"""Regressions from the independent review of the market alerts engine (wave 1).

Each test pins one finding, named as the review named it (F1a ... F12): it failed before the fix
and passes with it. The helpers are the review's own, so a probe and its test read side by side.
"""

from __future__ import annotations

from datetime import date, datetime

from sudarshan.alerts.market import clock, engine, sessions, spec, words
from sudarshan.alerts.market.book import Book
from sudarshan.alerts.market.engine import State
from sudarshan.alerts.market.store import MarketStore
from sudarshan.autologin.schedule import IST

KEY = "NSE_EQ|INE062A01020"
SBIN = {"instrument_key": KEY, "symbol": "SBIN", "exchange": "NSE", "tick_size": "0.05"}
MON = date(2026, 10, 19)
TUE = date(2026, 10, 20)
WED = date(2026, 10, 21)


def ms(day: date, hh: int, mm: int, ss: float = 0) -> int:
    base = int(datetime(day.year, day.month, day.day, tzinfo=IST).timestamp()) * 1000
    return base + (hh * 3600 + mm * 60) * 1000 + round(ss * 1000)


def make(fields: dict, id_: int = 1, version: int = 1, status: str = "active", inst=SBIN):
    row = spec.check(inst | fields, ms(MON, 8, 0))
    return spec.Alert.of(row | {"id": id_, "version": version, "status": status})


def market(day: date, now: int, previous: date | None, pauses=(), feed=None) -> clock.Market:
    found = {s: sessions.session(s, day, trading_day=True) for s in sessions.SEGMENTS}
    feed = feed or clock.Feed("connected", True, now)
    prev = sessions.day_key(previous) if previous else 0
    return clock.Market(sessions.day_key(day), found, prev, {}, feed, tuple(pauses))


def frame(key=KEY, ltp=None, at=None, recv=None, ltq=1, **more) -> dict:
    out = {"key": key, "ts_recv": recv if recv is not None else at, "ltq": ltq} | more
    if ltp is not None:
        out |= {"ltp": ltp, "ltt": at}
    return out


def fires(result) -> list[engine.Fire]:
    return [o.fire for o in result.outcomes if o.fire is not None]


def trades(book: Book, prices, start: int, day: date = WED, previous: date = TUE, **more):
    """Feed one trade a second from ``start``; returns the fires."""
    told = []
    for n, price in enumerate(prices):
        t = start + n * 1000
        told += fires(book.on_tick(frame(ltp=price, at=t, ltq=n + 1, **more), t,
                                   market(day, t, previous)))  # fmt: skip
    return told


# ---- H1: every price and level in paise -----------------------------------------------------


def test_F3_a_price_below_the_level_never_fires_whatever_the_tick_has_become():
    """Set on a 0.05 tick; NSE moves the stock to a 0.01 tick (price band). 249.98 is below 250
    and must not fire; the first price at or above it fires and is shown as it traded."""
    book = Book()
    a = make({"kind": "price", "params": {"op": "crosses_above", "level": "250"}})
    book.put(a)
    assert trades(book, (249.90, 249.98, 249.99), ms(WED, 10, 0)) == []
    (fire,) = trades(book, (250.01,), ms(WED, 10, 1))
    assert words.line(a, fire, WED) == "Alert: SBIN crossed above ₹250.00: now ₹250.01 (NSE)"


def test_F2_a_trade_below_the_vwap_is_never_at_it():
    """atp 100.21: a trade at 100.20 is below the VWAP, whatever the tick."""
    book = Book()
    a = make({"kind": "vwap", "params": {"direction": "above"}})
    book.put(a)
    assert trades(book, (100.0, 100.20), ms(WED, 10, 0), atp=100.21) == []
    (fire,) = trades(book, (100.25,), ms(WED, 10, 1), atp=100.21)
    assert words.line(a, fire, WED) == "Alert: SBIN crossed above VWAP ₹100.21: now ₹100.25 (NSE)"


def test_F2b_the_vwap_drifting_alone_moves_no_side():
    """The price stands at 100.20 while the VWAP drifts 100.226 -> 100.224: nothing crossed."""
    book = Book()
    book.put(make({"kind": "vwap", "params": {"direction": "above"}}))
    t = ms(WED, 10, 0)
    book.on_tick(frame(ltp=100.20, at=t, atp=100.226), t, market(WED, t, TUE))
    t += 1000
    out = book.on_tick(frame(ltp=100.20, at=t, atp=100.224, ltq=2), t, market(WED, t, TUE))
    assert fires(out) == [] and book.state(1).side == -1
    t += 1000
    out = book.on_tick(frame(ltp=100.23, at=t, atp=100.224, ltq=3), t, market(WED, t, TUE))
    assert [f.level for f in fires(out)] == [10023]  # above 100.224 from the next paisa


# ---- M1: the state put() is given is the state the flush saves -------------------------------


def test_F1a_an_edit_with_a_reset_queues_the_reset_not_the_state_before_it():
    book = Book()
    book.put(make({"kind": "price", "params": {"op": "crosses_above", "level": "100"}}))
    t = ms(WED, 10, 0)
    book.on_tick(frame(ltp=99.0, at=t), t, market(WED, t, TUE))  # side below, queued
    edited = make({"kind": "price", "params": {"op": "crosses_above", "level": "98"}}, version=2)
    book.put(edited, State())  # what the runtime does after store.update(reset_state=True)
    assert book.changed() == {1: (2, State())}


def _restart(store: MarketStore, alert_id: int) -> Book:
    """A new Book from what market.db holds, as the service builds it at start."""
    book = Book()
    book.put(spec.Alert.of(store.alert("owner", alert_id)), store.states("owner").get(alert_id))
    return book


def test_F1b_an_edit_before_the_flush_then_a_restart_fires_no_false_opening(tmp_path):
    """Edit 'crosses above 100' to 'above 98' while the price is 99 (already above the new level),
    before the 1 s flush; restart in the evening. The next open at 99.50 only seeds: the price
    never went below 98."""
    store = MarketStore(tmp_path / "market.db")
    level = {"kind": "price", "params": {"op": "crosses_above", "level": "100"}}
    row = store.create("owner", spec.check(SBIN | level, ms(MON, 8, 0)), ms(MON, 8, 0))
    book = Book()
    book.put(spec.Alert.of(row))
    t = ms(TUE, 14, 0)
    book.on_tick(frame(ltp=99.0, at=t), t, market(TUE, t, MON))
    new = spec.check(SBIN | {"kind": "price", "params": {"op": "crosses_above", "level": "98"}}, t)
    row = store.update("owner", row["id"], row["version"], new, t + 400, status="active",
                       reset_state=spec.changes_condition(row, new))  # fmt: skip
    book.put(spec.Alert.of(row), State())
    store.save_states("owner", book.changed(), t + 1000)  # the flush
    again = _restart(store, row["id"])
    t2 = ms(WED, 9, 15, 1)
    assert fires(again.on_tick(frame(ltp=99.5, at=t2), t2, market(WED, t2, TUE))) == []
    store.close()


def test_F1c_a_resume_reaches_market_db_so_a_restart_does_not_bring_back_the_old_side(tmp_path):
    """An at-close crossing closes below, is paused, resumed (the next close only seeds) and the
    service restarts that evening. The next day's close above must seed, not fire on a move made
    while the alert was paused."""
    store = MarketStore(tmp_path / "market.db")
    fields = {"kind": "price", "params": {"op": "crosses_above", "level": "100"},
              "frequency": "at_close", "candle_min": 15}  # fmt: skip
    row = store.create("owner", spec.check(SBIN | fields, ms(MON, 8, 0)), ms(MON, 8, 0))
    book = Book()
    book.put(spec.Alert.of(row))
    t = ms(TUE, 10, 1)
    book.on_tick(frame(ltp=99.0, at=t), t, market(TUE, t, MON))
    t = ms(TUE, 10, 15, 5)
    book.on_clock(t, market(TUE, t, MON))  # the 10:00 candle closes below: side -1
    store.save_states("owner", book.changed(), t)
    row = store.update("owner", row["id"], row["version"], row, ms(TUE, 11, 0), status="paused")
    book.put(spec.Alert.of(row))
    store.save_states("owner", book.changed(), ms(TUE, 11, 0, 1))
    row = store.update("owner", row["id"], row["version"], row, ms(TUE, 19, 0), status="active")
    book.put(spec.Alert.of(row), engine.resume(book.state(row["id"])))
    store.save_states("owner", book.changed(), ms(TUE, 19, 0, 1))
    again = _restart(store, row["id"])
    t = ms(WED, 9, 16)
    again.on_tick(frame(ltp=101.0, at=t), t, market(WED, t, TUE))
    t = ms(WED, 9, 30, 5)
    assert [o.fire for o in again.on_clock(t, market(WED, t, TUE)) if o.fire] == []
    store.close()


# ---- M3: a decision applies only to the definition it was made on ---------------------------


def test_F8_a_fire_recorded_after_an_edit_leaves_the_new_definition_alone(tmp_path):
    """A 'Once' fire decided on version 1 is recorded after the owner's edit (version 2, a new
    level, state reset). The fire happened while the alert said 100, so it is told; the new
    definition stays Active and its reset stands."""
    store = MarketStore(tmp_path / "market.db")
    t = ms(WED, 10, 0)
    level = {"kind": "price", "params": {"op": "crosses_above", "level": "100"}}
    row = store.create("owner", spec.check(SBIN | level, t), t)
    book = Book()
    book.put(spec.Alert.of(row))
    book.on_tick(frame(ltp=99.95, at=t), t, market(WED, t, TUE))
    out = book.on_tick(frame(ltp=100.05, at=t + 500, ltq=2), t + 500, market(WED, t + 500, TUE))
    (o,) = out.outcomes
    assert o.status == "stopped"
    new = spec.check(SBIN | {"kind": "price", "params": {"op": "crosses_above", "level": "110"}}, t)
    store.update("owner", row["id"], 1, new, t + 600, status="active", reset_state=True)
    event = store.record_fire("owner", o.fire, o.state, title="x", line="x", value="100.05",
                              level="100.00", channels={"telegram": True, "app": True},
                              status="stopped", status_reason="Fired once")  # fmt: skip
    assert event is not None
    after = store.alert("owner", row["id"])
    assert (after["status"], after["version"], after["params"]["level"]) == ("active", 2, "110")
    assert row["id"] not in store.states("owner")
    store.close()


# ---- M4: a candle is judged once, after the grace, and never a day late ---------------------


def _at_close(op: str = "at_or_above", **more):
    fields = {"kind": "price", "params": {"op": op, "level": "100"}, "frequency": "at_close",
              "candle_min": 15} | more  # fmt: skip
    return make(fields)


def _clock(book: Book, t: int, day: date = WED, previous: date = TUE) -> list[engine.Fire]:
    return [o.fire for o in book.on_clock(t, market(day, t, previous)) if o.fire is not None]


def test_F9_a_candle_is_judged_once_after_the_grace_and_a_late_trade_never_reopens_it():
    """A trade stamped 10:14:59.9 that arrives at 10:15:02.5 is the 10:00 candle's close: the
    timer waits the gate's 5 s grace. One that arrives after the judging is not counted."""
    book = Book()
    book.put(_at_close("crosses", cooldown_s=0))
    t = ms(WED, 9, 50)
    book.on_tick(frame(ltp=99.0, at=t), t, market(WED, t, TUE))
    assert _clock(book, ms(WED, 10, 0, 5)) == []  # the 09:45 candle closes below: seeds
    t = ms(WED, 10, 14)
    book.on_tick(frame(ltp=100.5, at=t, ltq=2), t, market(WED, t, TUE))
    assert _clock(book, ms(WED, 10, 15, 2)) == []  # inside the grace: not judged yet
    late, recv = ms(WED, 10, 14, 59.9), ms(WED, 10, 15, 2.5)
    book.on_tick(frame(ltp=99.5, at=late, recv=recv, ltq=3), recv, market(WED, recv, TUE))
    assert _clock(book, ms(WED, 10, 15, 5)) == []  # closed at 99.50: still below
    later, recv = ms(WED, 10, 14, 59.95), ms(WED, 10, 15, 6)
    book.on_tick(frame(ltp=100.8, at=later, recv=recv, ltq=4), recv, market(WED, recv, TUE))
    assert _clock(book, ms(WED, 10, 15, 7)) == []  # judged once: never again


def test_F10_yesterdays_last_candle_is_noted_not_told_at_todays_open():
    """No timer ran at yesterday's 15:30 close. Today's first trade must not tell yesterday's
    15:15 candle as a fire decided at 09:15; the alert's card notes it instead."""
    book = Book()
    book.put(_at_close())
    t = ms(TUE, 15, 29, 50)
    book.on_tick(frame(ltp=100.05, at=t), t, market(TUE, t, MON))
    t = ms(WED, 9, 15, 1)
    out = book.on_tick(frame(ltp=99.0, at=t, ltq=2), t, market(WED, t, TUE))
    assert fires(out) == [] and [o.note.code for o in out.outcomes] == [engine.NOT_JUDGED]
    assert words.note_words(out.outcomes[0].note) == (
        "Not checked: the close of the 15:15 candle on 20 Oct"
    )


def test_F11_a_wait_as_long_as_the_candle_lets_consecutive_closes_fire():
    """The wait is measured between candle ends (mutant e31 measured it from the last trade
    inside each candle, and the second close fell inside the wait)."""
    book = Book()
    book.put(_at_close(cooldown_s=900))
    told = []
    for hh, mm, ss in ((10, 14, 58), (10, 29, 30)):
        t = ms(WED, hh, mm, ss)
        book.on_tick(frame(ltp=100.5, at=t, ltq=mm), t, market(WED, t, TUE))
        told += _clock(book, ms(WED, hh, mm + 1, 5))
    when = [datetime.fromtimestamp(f.at / 1000, IST).strftime("%H:%M:%S") for f in told]
    assert when == ["10:15:05", "10:30:05"]
