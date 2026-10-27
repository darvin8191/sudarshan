"""Regressions from the independent review of the market alerts engine (wave 1).

Each test pins one finding, named as the review named it (F1a ... F12): it failed before the fix
and passes with it. The helpers are the review's own, so a probe and its test read side by side.
"""

from __future__ import annotations

from datetime import date, datetime

from sudarshan.alerts.market import clock, engine, sessions, spec, words
from sudarshan.alerts.market.book import Book
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
