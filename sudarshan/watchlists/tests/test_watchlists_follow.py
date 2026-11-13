"""Follow rows resolved (M8.10, M8.11, W15): the rules over the master with the calendar's closes,
the price sources in their order, the band's edges, a side the master lacks, the feed that does not
answer, and the op through the real bridge."""

from __future__ import annotations

import socket
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from watchlists_follow_master import build_master

from sudarshan.watchlists import feedsnap, resolver, search
from sudarshan.watchlists.store import rows as store

IST = timezone(timedelta(hours=5, minutes=30))


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso + "+05:30").timestamp() * 1000)


def rule(**more):
    base = {"kind": "atm_option", "underlying": "NIFTY", "underlying_exchange": "NSE_INDEX",
            "expiry_rule": "nearest", "strike_rule": "atm", "offset": 0, "strike": None,
            "opt_right": "CE"}  # fmt: skip
    return store.normalize(None, {**base, **more})


def index_of(snap):
    b = search.IndexBuilder()
    b.add(list(snap.by_exchange["ALL"]))
    return b.finish()


class World:
    """The outside of ``resolve_rows``: a master, a calendar, a market and a feed, all typed."""

    def __init__(self, drop=(), closes=None, open_=True, feed=None):
        self.snap = build_master(drop)
        self.ix = index_of(self.snap)
        self.cal = {k: ms(v) for k, v in (closes or {}).items()}
        self.open_, self.feed_has, self.asked = open_, feed or {}, []

    def feed(self, keys):
        self.asked.append(list(keys))
        return {k: v for k, v in self.feed_has.items() if k in keys}

    def run(self, rules, items, now, connected=True):
        return resolver.resolve_rows(
            rules, items, now_ms=ms(now), connected=connected, ix=self.ix,
            row_of=lambda x, s: self.snap.by_symbol_exchange.get((s, x)), isin=lambda r: None,
            closes_of=lambda fo, dates: {d: self.cal[d] for d in dates if d in self.cal},
            is_open=lambda exch, at: self.open_, feed=self.feed,
        )  # fmt: skip


def item(i=1, price=25912.35, source="tick", at="2026-11-20T11:00:00", held=None):
    return {"id": i, "price": price, "price_at": None if at is None else ms(at),
            "source": source, "held": held}  # fmt: skip


def one(world, r, it, now, connected=True):
    (got,) = world.run({it["id"]: r}, {it["id"]: it}, now, connected)
    return got


# -- the calendar's closes --------------------------------------------------------------------------
def test_a_normal_day_follows_the_nearest_expiry_to_its_close():
    got = one(World(), rule(), item(), "2026-11-20T11:00:05")
    assert got["state"] == "ok" and got["strike"] == 25900.0
    assert got["ce"]["symbol"] == "NIFTY24NOV2625900CE" and got["pe"] is None
    assert got["ce"]["expiry"] == "2026-11-24" and got["valid_until"] == ms("2026-11-24T15:40:00")
    assert got["words"] == "Now: NIFTY 25,900 CE, 24 Nov 2026 (NIFTY at 25,912.35)"
    assert got["price"] == 25912.35 and got["price_at"] == ms("2026-11-20T11:00:00")
    assert got["band"] == {"low": 25870.0, "high": 25930.0}


@pytest.mark.parametrize(
    ("now", "expiry", "words_start"),
    [
        ("2026-11-24T11:00:00", "2026-11-24", "Now: NIFTY 25,900 CE, 24 Nov 2026"),
        ("2026-11-24T15:39:59.999", "2026-11-24", "Now: NIFTY 25,900 CE, 24 Nov 2026"),
        ("2026-11-24T15:40:00", "2026-12-01", "Rolled to 1 Dec 2026 at 15:40"),
        ("2026-11-24T15:40:59", "2026-12-01", "Rolled to 1 Dec 2026 at 15:40"),
        ("2026-11-24T15:41:01", "2026-12-01", "Now: NIFTY 25,900 CE, 1 Dec 2026"),
        ("2026-11-25T09:20:00", "2026-12-01", "Now: NIFTY 25,900 CE, 1 Dec 2026"),
    ],
)
def test_expiry_day_before_and_after_the_close(now, expiry, words_start):
    got = one(World(), rule(), item(at=None), now)
    assert got["ce"]["expiry"] == expiry and got["words"].startswith(words_start)


def test_a_special_close_on_the_calendar_moves_the_roll():
    w = World(closes={"2026-11-24": "2026-11-24T13:00:00"})
    before = one(w, rule(), item(at=None), "2026-11-24T12:59:59")
    assert before["ce"]["expiry"] == "2026-11-24"
    assert before["valid_until"] == ms("2026-11-24T13:00:00")
    after = one(w, rule(), item(at=None), "2026-11-24T14:00:00")
    assert after["ce"]["expiry"] == "2026-12-01" and after["valid_until"] == ms(
        "2026-12-01T15:40:00"
    )
    late = World(closes={"2026-11-24": "2026-11-24T18:00:00"})
    still = one(late, rule(), item(at=None), "2026-11-24T16:00:00")
    assert still["ce"]["expiry"] == "2026-11-24" and still["valid_until"] == ms(
        "2026-11-24T18:00:00"
    )


def test_the_rolled_line_names_the_close_the_calendar_gave():
    w = World(closes={"2026-11-24": "2026-11-24T13:00:00"})
    got = one(w, rule(), item(at=None), "2026-11-24T13:00:20")
    assert got["words"] == "Rolled to 1 Dec 2026 at 13:00"


def test_expiry_rules_pick_next_month_and_next_month_after_the_close():
    w = World()
    now = "2026-11-24T10:00:00"
    expect = {"nearest": "2026-11-24", "next": "2026-12-01", "month": "2026-11-24",
              "next_month": "2026-12-29"}  # fmt: skip
    for name, iso in expect.items():
        assert one(w, rule(expiry_rule=name), item(at=None), now)["ce"]["expiry"] == iso, name


def test_a_future_row_follows_the_month_and_names_its_roll():
    r = rule(kind="month_future")
    got = one(World(), r, item(), "2026-11-20T11:00:00")
    assert got["future"]["symbol"] == "NIFTY24NOV26FUT" and got["ce"] is None and got["pe"] is None
    assert got["strike"] is None and got["band"] is None and got["price"] is None
    assert got["words"] == "Now: NIFTY future, 24 Nov 2026"
    assert got["valid_until"] == ms("2026-11-24T15:40:00")
    after = one(World(), r, item(), "2026-11-24T15:40:30")
    assert after["future"]["symbol"] == "NIFTY01DEC26FUT"
    assert after["words"] == "Rolled to 1 Dec 2026 at 15:40"


# -- a contract the master lacks --------------------------------------------------------------------
def test_a_side_the_master_lacks_is_said_and_no_contract_is_built():
    w = World(drop=("NIFTY24NOV2625900CE",))
    got = one(w, rule(), item(), "2026-11-20T11:00:00")
    assert got["state"] == "missing" and got["ce"] is None and got["strike"] == 25900.0
    assert got["words"] == "NIFTY 25,900 CE is not in today's contract list."
    pe = one(w, rule(opt_right="PE"), item(), "2026-11-20T11:00:00")
    assert pe["state"] == "ok" and pe["pe"]["symbol"] == "NIFTY24NOV2625900PE"


def test_a_strike_row_returns_both_contracts_or_names_the_missing_side():
    pair = rule(kind="strike_pair")
    both = one(World(), pair, item(), "2026-11-20T11:00:00")
    assert both["state"] == "ok"
    assert (both["ce"]["symbol"], both["pe"]["symbol"]) == (
        "NIFTY24NOV2625900CE", "NIFTY24NOV2625900PE")  # fmt: skip
    assert both["words"] == "Now: NIFTY 25,900 CE and PE, 24 Nov 2026 (NIFTY at 25,912.35)"
    for gone, side in (("NIFTY24NOV2625900PE", "PE"), ("NIFTY24NOV2625900CE", "CE")):
        got = one(World(drop=(gone,)), pair, item(), "2026-11-20T11:00:00")
        assert got["state"] == "missing" and got["ce"] is None and got["pe"] is None
        assert got["words"] == f"NIFTY 25,900 {side} is not in today's contract list."


def test_a_strike_past_the_end_of_the_ladder_and_a_fixed_strike_not_listed_say_so():
    got = one(World(), rule(offset=10), item(), "2026-11-20T11:00:00")
    assert (
        got["state"] == "missing"
        and got["words"] == "No strike 10 above the ATM in the 24 Nov 2026 expiry."
    )
    fixed = one(World(), rule(strike_rule="fixed", strike=25925), item(), "2026-11-20T11:00:00")
    assert fixed["state"] == "missing" and "25925" not in fixed["words"] and fixed["ce"] is None
    ok = one(
        World(), rule(strike_rule="fixed", strike=25950), item(price=None), "2026-11-20T11:00:00"
    )
    assert (
        ok["state"] == "ok" and ok["ce"]["symbol"] == "NIFTY24NOV2625950CE" and ok["band"] is None
    )


def test_a_row_whose_id_is_gone_and_an_underlying_with_no_expiry_say_so():
    w = World()
    (gone,) = w.run({}, {7: item(7)}, "2026-11-20T11:00:00")
    assert (gone["id"], gone["state"]) == (7, "missing") and "no longer" in gone["words"]
    late = one(w, rule(), item(), "2026-12-30T10:00:00")
    assert late["state"] == "missing" and late["ce"] is None
    assert late["words"] == "NIFTY has no matching expiry in today's contract list."


def test_a_bse_underlying_reads_the_bfo_side_only():
    r = rule(
        underlying="SENSEX", underlying_exchange="BSE_INDEX", strike_rule="fixed", strike=80000
    )
    got = one(World(), r, item(price=None), "2026-11-20T11:00:00")
    assert got["ce"]["exchange"] == "BFO" and got["ce"]["expiry"] == "2026-11-26"


# -- the price sources, in order ----------------------------------------------------------------------
def test_the_pages_tick_beats_its_snapshot_beats_the_feed_and_the_feed_is_not_asked():
    w = World(feed={"NSE_INDEX|NIFTY": (26000.0, ms("2026-11-20T10:59:00"))})
    now = "2026-11-20T11:00:00"
    tick = {"id": 1, "price": 25851.0, "price_at": ms("2026-11-20T09:00:00"), "source": "tick",
            "held": None}  # fmt: skip
    got = one(w, rule(), tick, now)
    assert got["strike"] == 25850.0 and got["price"] == 25851.0  # a quiet index: however old
    snap = {**tick, "source": "snapshot", "price": 25949.0}
    assert one(w, rule(), snap, now)["strike"] == 25950.0
    assert w.asked == []


def test_a_tick_on_a_dropped_socket_is_not_followed_the_snapshot_or_the_feed_is():
    w = World(feed={"NSE_INDEX|NIFTY": (26000.0, ms("2026-11-20T10:59:00"))})
    now = "2026-11-20T11:00:00"
    stale = item(price=25851.0, source="tick")
    assert one(w, rule(), stale, now, connected=False)["strike"] == 26000.0  # the feed's
    assert w.asked == [["NSE_INDEX|NIFTY"]]
    snap = [item(price=25851.0, source="tick"), item(2, price=25949.0, source="snapshot")]
    (a, b) = w.run({1: rule(), 2: rule()}, {1: snap[0], 2: snap[1]}, now, connected=False)
    assert (a["strike"], b["strike"]) == (26000.0, 25950.0)
    assert a["words"].endswith(
        "(NIFTY at 26,000, price from 10:59:00)"
    )  # an old price shows its time


def test_one_asking_of_the_feed_serves_every_row_that_needs_it_and_a_future_asks_none():
    w = World(feed={"NSE_INDEX|NIFTY": (25912.35, ms("2026-11-20T10:59:58"))})
    rules = {
        1: rule(),
        2: rule(offset=1),
        3: rule(kind="strike_pair"),
        4: rule(kind="month_future"),
    }
    items = {i: item(i, price=None, source=None, at=None) for i in rules}
    got = w.run(rules, items, "2026-11-20T11:00:00")
    assert w.asked == [["NSE_INDEX|NIFTY"]]
    assert [g["strike"] for g in got] == [25900.0, 25950.0, 25900.0, None]
    assert got[0]["words"] == "Now: NIFTY 25,900 CE, 24 Nov 2026 (NIFTY at 25,912.35)"


def test_a_closed_market_says_the_last_price_it_picked_the_strike_from():
    w = World(open_=False)
    now = "2026-11-20T18:00:00"
    page = item(price=25912.35, source="tick", at="2026-11-20T15:29:59")
    got = one(w, rule(), page, now)
    assert got["state"] == "closed" and got["strike"] == 25900.0
    assert got["words"] == "Market closed · strike from NIFTY's last price 25,912.35 (15:29:59)"
    kept = one(w, rule(), item(price=25912.35, source="last", at="2026-11-20T15:29:59"), now, False)
    assert kept["state"] == "closed" and kept["words"] == got["words"]
    from_feed = World(open_=False, feed={"NSE_INDEX|NIFTY": (25912.35, ms("2026-11-20T15:29:59"))})
    assert (
        one(from_feed, rule(), item(price=None, source=None, at=None), now)["words"] == got["words"]
    )


def test_no_price_at_all_waits_and_a_closed_market_with_none_waits_too():
    for w in (World(), World(open_=False)):
        got = one(w, rule(), item(price=None, source=None, at=None), "2026-11-20T11:00:00")
        assert got["state"] == "waiting" and got["ce"] is None and got["strike"] is None
        assert got["words"] == "Waiting for NIFTY's price to pick the strike"
        assert got["valid_until"] == ms("2026-11-24T15:40:00")


# -- the band's edges ---------------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("price", "held", "offset", "shown"),
    [
        (25929.95, 25900.0, 0, 25900.0),  # inside the band: held (a fresh ATM would say 25950)
        (25930.0, 25900.0, 0, 25950.0),  # on the edge: out
        (25870.05, 25900.0, 0, 25900.0),
        (25870.0, 25900.0, 0, 25850.0),
        (25929.95, 25950.0, 1, 25950.0),  # ATM+1 held on the strike it showed: the same band
        (25930.0, 25950.0, 1, 26000.0),
        (25929.95, 25900.0, 0, 25900.0),
        (25929.95, None, 0, 25950.0),  # nothing held: found afresh
        (25929.95, 12345.0, 0, 25950.0),  # a strike not on the ladder: found afresh
        (25000.0, None, 0, 25800.0),  # below the ladder: the lowest strike
    ],
)
def test_the_band_holds_the_strike_and_its_edges_are_open(price, held, offset, shown):
    got = one(World(), rule(offset=offset), item(price=price, held=held), "2026-11-20T11:00:00")
    assert got["strike"] == shown


def test_the_answer_carries_the_band_of_the_atm_not_of_the_offset_strike():
    got = one(World(), rule(offset=2), item(price=25912.35), "2026-11-20T11:00:00")
    assert got["strike"] == 26000.0 and got["band"] == {"low": 25870.0, "high": 25930.0}
    lowest = one(World(), rule(), item(price=25800.0), "2026-11-20T11:00:00")
    assert lowest["band"] == {"low": None, "high": 25830.0}


# -- the feed that does not answer ----------------------------------------------------------------------
class Stalled:
    """A loopback server that takes a connection and never answers, or answers as told."""

    def __init__(self, reply: bytes | None = None):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.reply, self.held, self.seen = reply, [], []
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            data = conn.recv(4096)
            self.seen.append(data)
            if self.reply is None:
                self.held.append(conn)  # never answered, never closed
            else:
                conn.sendall(self.reply)
                conn.close()

    def close(self):
        self.sock.close()
        for c in self.held:
            c.close()


def http(body: str, status: str = "200 OK") -> bytes:
    raw = body.encode()
    return (f"HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {len(raw)}"
            f"\r\nConnection: close\r\n\r\n").encode() + raw  # fmt: skip


def test_a_feed_that_never_answers_costs_the_request_half_a_second_and_no_more(monkeypatch):
    server = Stalled()
    try:
        monkeypatch.setenv(feedsnap.PORT_ENV, str(server.port))
        t0 = time.monotonic()
        assert feedsnap.read(["NSE_INDEX|Nifty 50"]) == {}
        took = time.monotonic() - t0
    finally:
        server.close()
    assert 0.4 <= took < 1.0, took
    assert server.seen and b"GET /snapshot?keys=NSE_INDEX%7CNifty+50" in server.seen[0]


def test_a_row_waits_when_the_feed_times_out_and_the_request_is_bounded(monkeypatch):
    server = Stalled()
    try:
        monkeypatch.setenv(feedsnap.PORT_ENV, str(server.port))
        w = World()
        w.feed = feedsnap.read
        t0 = time.monotonic()
        got = one(w, rule(), item(price=None, source=None, at=None), "2026-11-20T11:00:00")
        took = time.monotonic() - t0
    finally:
        server.close()
    assert got["state"] == "waiting" and took < 1.0, took


def test_a_feed_that_is_down_or_wrong_answers_nothing_and_a_good_one_answers_ticks(monkeypatch):
    free = socket.socket()
    free.bind(("127.0.0.1", 0))
    port = free.getsockname()[1]
    free.close()
    monkeypatch.setenv(feedsnap.PORT_ENV, str(port))
    assert feedsnap.read(["K"]) == {}  # refused at once
    for body, status in (("not json", "200 OK"), ('{"ticks": []}', "200 OK"),
                         ('{"ticks": {}}', "500 Error")):  # fmt: skip
        server = Stalled(http(body, status))
        monkeypatch.setenv(feedsnap.PORT_ENV, str(server.port))
        try:
            assert feedsnap.read(["K"]) == {}
        finally:
            server.close()
    good = '{"ticks": {"A": {"key": "A", "ltp": 25912.35, "ltt": 1700000000000, "ts_recv": 5}, ' \
        '"B": null, "C": {"ltp": 0}, "D": {"ltp": "x"}, "E": {"ltp": 7.5, "ts_recv": 9}}}'  # fmt: skip
    server = Stalled(http(good))
    monkeypatch.setenv(feedsnap.PORT_ENV, str(server.port))
    try:
        assert feedsnap.read(["A", "B", "C", "D", "E", ""]) == {
            "A": (25912.35, 1700000000000), "E": (7.5, 9)}  # fmt: skip
    finally:
        server.close()
    assert feedsnap.read([]) == {}


def test_the_feed_port_comes_from_the_feed_services_own_setting(monkeypatch):
    for raw, port in (("", 5580), ("abc", 5580), ("0", 5580), ("70000", 5580), ("5599", 5599)):
        monkeypatch.setenv(feedsnap.PORT_ENV, raw)
        assert feedsnap._url() == f"http://127.0.0.1:{port}/snapshot"


# -- the calendar --------------------------------------------------------------------------------------
@pytest.fixture
def calendar(tmp_path):
    from database import market_calendar_db as cal

    cal.Base.metadata.create_all(cal.engine)
    for model in (cal.HolidayExchange, cal.Holiday):
        cal.db_session.query(model).delete()
    cal.db_session.commit()
    cal.clear_market_calendar_cache()
    yield cal
    for model in (cal.HolidayExchange, cal.Holiday):
        cal.db_session.query(model).delete()
    cal.db_session.commit()
    cal.db_session.remove()
    cal.clear_market_calendar_cache()


def put(cal, iso, kind, exchange, is_open, start=None, end=None):
    from datetime import date

    day = date.fromisoformat(iso)
    h = cal.Holiday(holiday_date=day, description="x", holiday_type=kind, year=day.year)
    cal.db_session.add(h)
    cal.db_session.flush()
    cal.db_session.add(cal.HolidayExchange(holiday_id=h.id, exchange_code=exchange,
                                           is_open=is_open, start_time=start, end_time=end))  # fmt: skip
    cal.db_session.commit()
    cal.clear_market_calendar_cache()


def test_the_calendar_gives_a_dated_close_and_nothing_for_an_ordinary_day(calendar):
    from sudarshan.watchlists import sessions

    put(calendar, "2026-11-24", "SPECIAL_SESSION", "NFO", True, ms("2026-11-24T10:00:00"),
        ms("2026-11-24T13:00:00"))  # fmt: skip
    put(calendar, "2026-12-01", "TRADING_HOLIDAY", "NFO", True, ms("2026-12-01T17:00:00"),
        ms("2026-12-01T23:55:00"))  # fmt: skip
    got = sessions.closes(["2026-11-24", "2026-12-01", "2026-12-29"], "NFO")
    assert got == {"2026-11-24": ms("2026-11-24T13:00:00"), "2026-12-01": ms("2026-12-01T23:55:00")}
    assert sessions.closes([], "NFO") == {} and sessions.closes(["2026-11-24"], "BFO") == {}


def test_the_calendars_default_f_and_o_close_of_15_40_is_not_the_roll(calendar):
    from sudarshan.watchlists import sessions

    assert (
        sessions.closes(["2026-11-25"], "NFO") == {}
    )  # no dated row: the regime stop by expiry.stop_ms


def test_an_exchange_is_open_inside_the_calendars_window_only(calendar):
    from sudarshan.watchlists import sessions

    assert sessions.market_open("NSE", ms("2026-11-20T11:00:00"))  # a Friday
    assert not sessions.market_open("NSE", ms("2026-11-20T08:00:00"))
    assert not sessions.market_open("NSE", ms("2026-11-20T16:00:00"))
    assert not sessions.market_open("NSE", ms("2026-11-21T11:00:00"))  # a Saturday
    put(calendar, "2026-11-21", "SPECIAL_SESSION", "NSE", True, ms("2026-11-21T18:00:00"),
        ms("2026-11-21T19:00:00"))  # fmt: skip
    assert sessions.market_open("NSE", ms("2026-11-21T18:30:00"))
    put(calendar, "2026-11-23", "TRADING_HOLIDAY", "NSE", False)
    assert not sessions.market_open("NSE", ms("2026-11-23T11:00:00"))


def test_a_calendar_that_cannot_be_read_closes_nothing_and_calls_nothing_closed(monkeypatch):
    from database import market_calendar_db as cal
    from sudarshan.watchlists import sessions

    def broken(*a, **k):
        raise RuntimeError("no calendar")

    monkeypatch.setattr(cal, "get_special_session", broken)
    monkeypatch.setattr(cal, "get_effective_session_window", broken)
    assert sessions.closes(["2026-11-24"], "NFO") == {}
    assert sessions.market_open("NSE", ms("2026-11-20T03:00:00")) is True
