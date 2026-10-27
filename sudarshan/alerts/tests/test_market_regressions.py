"""Regressions from the independent review of the market alerts engine (wave 1).

Each test pins one finding, named as the review named it (F1a ... F12): it failed before the fix
and passes with it. The helpers are the review's own, so a probe and its test read side by side.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from sudarshan.alerts.market import clock, conditions, engine, sessions, spec, words
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


# ---- M5: openings and first fires after a pause -------------------------------------------


def _move(book: Book) -> spec.Alert:
    a = make({"kind": "move", "params": {"base": "close", "direction": "up", "percent": "2"}})
    book.put(a)
    t = ms(TUE, 15, 29)
    book.on_tick(frame(ltp=100.0, at=t, cp=100.0), t, market(TUE, t, MON))
    return a


def test_F5a_a_pause_before_the_open_marks_nothing():
    """A feed reconnect at 09:05-09:14 (nothing trades then): the 09:15 fire is plain."""
    book = Book()
    a = _move(book)
    pause = clock.Pause(ms(WED, 9, 5), ms(WED, 9, 14), "feed")
    t = ms(WED, 9, 15, 1)
    (fire,) = fires(book.on_tick(frame(ltp=102.5, at=t, cp=100.0), t,
                                 market(WED, t, TUE, [pause])))  # fmt: skip
    assert fire.found_later is None
    assert words.line(a, fire, WED) == (
        "Alert: SBIN up 2.50% on the day: ₹102.50 (previous close ₹100.00) (NSE)"
    )


def test_F5c_a_pause_across_the_open_is_told_from_the_open():
    """The service down from 08:00 to 09:20: the condition could only have held from 09:15."""
    book = Book()
    a = _move(book)
    pause = clock.Pause(ms(WED, 8, 0), ms(WED, 9, 20), "service")
    t = ms(WED, 9, 20, 1)
    (fire,) = fires(book.on_tick(frame(ltp=102.5, at=t, cp=100.0), t,
                                 market(WED, t, TUE, [pause])))  # fmt: skip
    assert words.line(a, fire, WED).endswith(
        "(seen after a pause in market alerts, 09:15 to 09:20)"
    )


def test_F5b_an_opening_seen_only_after_a_pause_is_a_marked_crossing_not_a_first_price():
    """Market alerts were down from before the open to 09:40. The first trade they see (103.00)
    is not the opening price, and the crossing may have happened at any time since 09:15."""
    book = Book()
    a = make({"kind": "price", "params": {"op": "crosses_above", "level": "100"},
              "frequency": "every_time"})  # fmt: skip
    book.put(a)
    t = ms(TUE, 15, 29)
    book.on_tick(frame(ltp=99.9, at=t), t, market(TUE, t, MON))
    pause = clock.Pause(ms(WED, 8, 0), ms(WED, 9, 40), "service")
    t = ms(WED, 9, 40, 1)
    (fire,) = fires(book.on_tick(frame(ltp=103.0, at=t), t, market(WED, t, TUE, [pause])))
    assert not fire.opening and fire.found_later == (ms(WED, 9, 15), ms(WED, 9, 40))
    assert words.line(a, fire, WED) == (
        "Alert: SBIN crossed above ₹100.00: now ₹103.00 (NSE) "
        "(seen after a pause in market alerts, 09:15 to 09:40)"
    )
    unpaused = Book()
    unpaused.put(a)
    t = ms(TUE, 15, 29)
    unpaused.on_tick(frame(ltp=99.9, at=t), t, market(TUE, t, MON))
    t = ms(WED, 9, 40, 1)  # an illiquid stock's first trade at 09:40, nothing missed
    (fire,) = fires(unpaused.on_tick(frame(ltp=103.0, at=t), t, market(WED, t, TUE)))
    assert fire.opening and words.line(a, fire, WED) == (
        "Alert: SBIN opened above ₹100.00: first price ₹103.00 (previous ₹99.90) (NSE)"
    )


# ---- the day's own candle, and every time on a day high --------------------------------------


def test_F4a_yesterdays_day_high_in_the_first_frame_is_not_todays():
    """The feed keeps a key's last 1d candle across days: today's first frame may still carry
    yesterday's high (105). Today's first new high after 09:30 (100.50) must be told."""
    book = Book()
    a = make({"kind": "day_break", "params": {"side": "high", "after": "09:30"}})
    book.put(a)
    yesterday = {"open": 103.0, "high": 105.0, "low": 102.0, "ts": ms(TUE, 0, 0)}
    t = ms(WED, 9, 15, 1)
    book.on_tick(frame(ltp=100.0, at=t, ohlc=yesterday), t, market(WED, t, TUE))
    today = {"open": 100.0, "high": 100.5, "low": 99.0, "ts": ms(WED, 0, 0)}
    t = ms(WED, 9, 40)
    (fire,) = fires(book.on_tick(frame(ltp=100.5, at=t, ohlc=today, ltq=2), t,
                                 market(WED, t, TUE)))  # fmt: skip
    assert words.describe(a, fire, WED) == ("SBIN broke today's high ₹100.00", "now ₹100.50")


def test_F4b_yesterdays_open_in_the_first_frame_is_not_todays_open():
    """'Up 2% from today's open' waits for today's candle: yesterday's open (98) made 100.00
    look 2.04% up, and the alert fired once for the day at the first trade."""
    book = Book()
    book.put(make({"kind": "move", "params": {"base": "open", "direction": "up", "percent": "2"}}))
    yesterday = {"open": 98.0, "high": 99.0, "low": 97.0, "ts": ms(TUE, 0, 0)}
    t = ms(WED, 9, 15, 1)
    out = book.on_tick(frame(ltp=100.0, at=t, ohlc=yesterday), t, market(WED, t, TUE))
    assert fires(out) == [] and book.waiting(1) == conditions.WAITING_OPEN
    today = {"open": 100.0, "high": 100.05, "low": 100.0, "ts": ms(WED, 0, 0)}
    out = book.on_tick(frame(ltp=100.05, at=t + 1000, ohlc=today, ltq=2), t + 1000,
                       market(WED, t + 1000, TUE))  # fmt: skip
    assert fires(out) == [] and book.waiting(1) is None


def test_F7_every_time_on_a_day_high_needs_a_minute_between_alerts():
    """On a trending morning every uptick is a new high: with no wait, 50 fires went out in
    under a minute and the alert paused itself."""
    fields = {"kind": "day_break", "params": {"side": "high", "after": "09:30"},
              "frequency": "every_time"}  # fmt: skip
    with pytest.raises(spec.SpecError) as caught:
        make(fields | {"cooldown_s": 0})
    assert (caught.value.field, caught.value.message) == (
        "cooldown_s",
        "Every time on 'Breaks the day's high' needs at least 1 minute between alerts.",
    )
    book = Book()
    book.put(make(fields | {"cooldown_s": 60}))
    price, told, stops = 100.0, [], []
    t = ms(WED, 9, 31)
    book.on_tick(frame(ltp=price, at=t, ohlc={"high": price, "ts": t}), t, market(WED, t, TUE))
    for i in range(120):
        price, t = round(price + 0.05, 2), t + 1000
        out = book.on_tick(frame(ltp=price, at=t, ohlc={"high": price, "ts": t}, ltq=i + 2), t,
                           market(WED, t, TUE))  # fmt: skip
        told += fires(out)
        stops += [o.status for o in out.outcomes if o.status]
    assert len(told) == 2 and stops == []  # 09:31:01 and 09:32:01


# ---- words, and the page's status line -------------------------------------------------------


def test_F6a_an_opening_on_the_bid_says_bid():
    book = Book()
    a = make({"kind": "price", "params": {"op": "crosses_above", "level": "100", "source": "bid"},
              "frequency": "every_time"})  # fmt: skip
    book.put(a)
    t = ms(TUE, 15, 29)
    book.on_tick(frame(at=t, bid=99.9), t, market(TUE, t, MON))
    t = ms(WED, 9, 15, 1)
    (fire,) = fires(book.on_tick(frame(at=t, bid=100.5), t, market(WED, t, TUE)))
    assert words.line(a, fire, WED) == (
        "Alert: SBIN bid opened above ₹100.00: first bid ₹100.50 (previous ₹99.90) (NSE)"
    )


def test_F6b_the_day_candle_reports_the_last_trade_never_the_close():
    """The Day candle judges the last trade before 15:30; the exchange's closing price (the last
    half hour's weighted average) can be on the other side of the level."""
    book = Book()
    a = make({"kind": "price", "params": {"op": "at_or_above", "level": "100"},
              "frequency": "at_close", "candle_min": "day"})  # fmt: skip
    book.put(a)
    t = ms(WED, 15, 29, 59)
    book.on_tick(frame(ltp=100.05, at=t), t, market(WED, t, TUE))
    t = ms(WED, 15, 30, 5)
    (out,) = book.on_clock(t, market(WED, t, TUE))
    line = words.line(a, out.fire, WED)
    assert line == (
        "Alert: SBIN ended the day's trading at or above ₹100.00: last trade ₹100.05 "
        "(09:15 to 15:30) (NSE)"
    )
    assert "close" not in line


OPT = {"instrument_key": "NSE_FO|49229", "symbol": "NIFTY25NOV2625900CE", "exchange": "NFO",
       "tick_size": "0.05", "expiry": "2026-11-25"}  # fmt: skip


def test_F12_the_status_line_comes_from_the_clock_and_the_feed_never_a_trade():
    """An illiquid option whose last trade was yesterday sends quotes all day: each frame's
    trade fails the gate as outside hours, but the market is open at noon."""
    book = Book()
    book.put(make({"kind": "price", "params": {"op": "crosses_above", "level": "100"}}, inst=OPT))
    t = ms(WED, 12, 0)
    now = market(WED, t, TUE)
    out = book.on_tick(frame(key=OPT["instrument_key"], ltp=95.0, at=ms(TUE, 15, 20), recv=t,
                             bid=94.0, ask=96.0), t, now)  # fmt: skip
    assert out.check == clock.OUTSIDE_HOURS  # the trade's own verdict, for the counters
    status = clock.status(now, "NSE_FO", t)
    assert status == clock.OK and words.check_words(status, now=t) == "Watching · Real-time"
    evening = ms(WED, 15, 45)
    assert clock.status(market(WED, evening, TUE), "NSE_FO", evening) == clock.OUTSIDE_HOURS
