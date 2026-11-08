"""The price book against a fake feed and a fake governor on loopback (M7.24)."""

from __future__ import annotations

import asyncio
import logging

import pytest
from portfolio_fakes import FakeFeed, FakeResolver, make_symtoken
from sqlalchemy import insert

from sudarshan.autologin.schedule import TradingCalendar
from sudarshan.portfolio import store as store_module
from sudarshan.portfolio.book import Book
from sudarshan.portfolio.prices import CLIENT, SNAPSHOT_BATCH, PriceBook, price_u
from sudarshan.portfolio.resolver import Resolver
from sudarshan.portfolio.store import DB_FILENAME, PortfolioStore, Scope
from sudarshan.portfolio.views import NO_PRICE

NOW = 1_793_000_000_000  # Mon 26 Oct 2026, 13:03 IST
MORNING = 1_792_980_000_000  # Mon 26 Oct 2026, 07:30 IST
OWNER = Scope.owner()
TCS = "NSE_EQ|INE467B01029"
DAY = 20261026


@pytest.fixture(autouse=True)
def _stop_thread():
    yield
    store_module.stop()


@pytest.fixture
def feed():
    fake = FakeFeed()
    fake.url = fake.start()
    yield fake
    fake.stop()


class Rig:
    def __init__(self, tmp_path, feed, resolver=None):
        self.store = PortfolioStore(tmp_path / "s" / DB_FILENAME, now=NOW)
        self.book = Book(self.store)
        self.calendar = TradingCalendar(
            tmp_path / "no-holidays.json", logger=logging.getLogger("t")
        )
        self.pb = PriceBook(feed.url, resolver or FakeResolver(), self.calendar)

    def share(self, rid="a-1", symbol="TCS", key=TCS):
        return self.book.asset_create(
            OWNER, rid, "listed", now=NOW, exchange="NSE", symbol=symbol,
            isin=key.partition("|")[2], name=symbol, instrument_key=key,
        )  # fmt: skip

    def eod(self, asset, day, price, at, prev=None):
        def run(tx):
            tx.conn.execute(
                insert(tx._t("price_eod")).values(
                    asset_id=asset["id"],
                    day=day,
                    principal=tx.who,
                    price_u=price,
                    prev_close_u=prev,
                    basis="last",
                    source="upstox",
                    late=0,
                    at=at,
                    created_at=at,
                    updated_at=at,
                )
            )

        self.store.write(OWNER, run)

    def now(self, asset, today=DAY):
        row = self.store.read(OWNER, lambda tx: tx.get("asset", asset["id"]))
        return self.store.read(OWNER, lambda tx: self.pb.now(tx, row, today))

    async def warm(self, at=NOW):
        await self.pb.warm(self.store, at)


def run(coro):
    return asyncio.run(coro)


def test_a_feed_price_is_whole_paise_times_one_hundred():
    assert price_u(3500.55) == 35_005_500 and price_u(0.1 + 0.2) == 3000 and price_u(None) is None


def test_a_tick_newer_than_the_last_closing_record_is_the_price(tmp_path, feed):
    rig = Rig(tmp_path, feed)
    tcs = rig.share()
    rig.eod(tcs, 20261023, 34_000_000, NOW - 3 * 86_400_000, prev=33_000_000)
    feed.ticks[TCS] = {"key": TCS, "ltp": 3500.55, "ltt": NOW - 1000, "cp": 3450.0}

    async def go():
        await rig.warm()
        await rig.pb.aclose()

    run(go())
    got = rig.now(tcs)
    assert got["price_u"] == 35_005_500 and got["prev_close_u"] == 34_500_000
    assert got["source"] == "feed" and got["day"] == DAY and got["late"] is False


def test_a_tick_older_than_the_closing_record_loses_to_it(tmp_path, feed):
    rig = Rig(tmp_path, feed)
    tcs = rig.share()
    rig.eod(tcs, DAY, 35_100_000, NOW, prev=34_000_000)
    feed.ticks[TCS] = {"key": TCS, "ltp": 3500.0, "ltt": NOW - 60_000, "cp": 3400.0}
    run(rig.warm())
    got = rig.now(tcs)
    assert got["price_u"] == 35_100_000 and got["source"] == "upstox"
    assert got["prev_close_u"] == 34_000_000


def test_the_previous_close_falls_back_to_the_day_before(tmp_path, feed):
    rig = Rig(tmp_path, feed)
    tcs = rig.share()
    rig.eod(tcs, 20261022, 33_000_000, NOW - 4 * 86_400_000)
    rig.eod(tcs, 20261023, 34_000_000, NOW - 3 * 86_400_000)
    got = rig.now(tcs)
    assert got["price_u"] == 34_000_000 and got["prev_close_u"] == 33_000_000
    feed.ticks[TCS] = {"key": TCS, "ltp": 3500.0, "ltt": NOW - 1000}  # a tick without cp
    run(rig.warm())
    assert rig.now(tcs)["prev_close_u"] == 34_000_000


def test_no_tick_and_no_record_and_a_governor_that_is_off_is_no_price_yet(tmp_path, feed, caplog):
    rig = Rig(tmp_path, feed)
    tcs = rig.share()
    with caplog.at_level(logging.WARNING):
        run(rig.warm())
        run(rig.warm())
    assert rig.now(tcs) == dict(NO_PRICE)
    told = [r.getMessage() for r in caplog.records if "switched off" in r.getMessage()]
    assert len(told) == 1  # said once
    quotes = feed.paths("GET", "/upstox/")
    assert quotes and all(q["_h"].get("X-Sudarshan-Client") == CLIENT for q in quotes)
    assert len(quotes) == 1  # asked once for the session, not again after being refused


def test_a_quote_through_the_governor_is_asked_once_per_key_per_session(tmp_path, feed):
    feed.governor = "on"
    feed.quotes[TCS] = {"instrument_token": TCS, "last_price": 3490.5, "cp": 3400.0}
    rig = Rig(tmp_path, feed)
    tcs = rig.share()

    async def go():
        await rig.warm()
        await rig.warm()
        await rig.warm(NOW + 5 * 60_000)

    run(go())
    got = rig.now(tcs)
    assert got["price_u"] == 34_905_000 and got["source"] == "quote"
    assert got["prev_close_u"] == 34_000_000
    assert len(feed.paths("GET", "/upstox/")) == 1
    # the next day it is asked again
    run(rig.warm(NOW + 86_400_000))
    assert len(feed.paths("GET", "/upstox/")) == 2


def test_a_key_with_a_closing_record_is_never_quoted(tmp_path, feed):
    feed.governor = "on"
    rig = Rig(tmp_path, feed)
    rig.eod(rig.share(), 20261023, 34_000_000, NOW - 86_400_000)
    run(rig.warm())
    assert feed.paths("GET", "/upstox/") == []


def test_snapshots_go_two_hundred_keys_at_a_time(tmp_path, feed):
    rig = Rig(tmp_path, feed)
    keys = [f"NSE_EQ|INE{n:09d}" for n in range(SNAPSHOT_BATCH * 2 + 50)]
    feed.ticks.update({k: {"key": k, "ltp": 10.0, "ltt": NOW} for k in keys[:5]})
    found = run(rig.pb._snapshot(keys))
    snaps = feed.paths("GET", "/snapshot")
    assert [len(s["keys"].split(",")) for s in snaps] == [200, 200, 50]
    assert set(found) == set(keys[:5])


def test_claims_follow_the_listed_keys_and_never_fall_back_to_subscribe(tmp_path, feed):
    rig = Rig(tmp_path, feed)
    rig.share()
    run(rig.warm())
    run(rig.warm())  # unchanged: not sent again
    puts = feed.paths("PUT", "/claims/")
    assert len(puts) == 1 and puts[0]["_body"] == {
        "keys": {TCS: "ltpc", "NSE_INDEX|Nifty 50": "ltpc", "NSE_INDEX|Nifty 500": "ltpc"}
    }
    feed.started_at = "2026-10-26T13:00:00"  # the feed restarted: claims are made again
    run(rig.warm())
    assert len(feed.paths("PUT", "/claims/")) == 2


def test_a_feed_that_takes_no_claims_is_said_once_and_prices_go_on(tmp_path, feed, caplog):
    feed.claim_status = 404
    feed.ticks[TCS] = {"key": TCS, "ltp": 3500.0, "ltt": NOW - 1}
    rig = Rig(tmp_path, feed)
    tcs = rig.share()
    with caplog.at_level(logging.WARNING):
        run(rig.warm())
        run(rig.warm())
    assert sum("does not take claims" in r.getMessage() for r in caplog.records) == 1
    assert rig.now(tcs)["source"] == "feed"
    assert feed.paths("POST", "/subscribe") == []


def test_a_feed_that_is_down_leaves_the_closing_record(tmp_path, feed, caplog):
    rig = Rig(tmp_path, feed)
    tcs = rig.share()
    rig.eod(tcs, 20261023, 34_000_000, NOW - 86_400_000)
    feed.down = True
    with caplog.at_level(logging.WARNING):
        run(rig.warm())
        run(rig.warm())
    assert rig.now(tcs)["price_u"] == 34_000_000
    assert sum("not answering" in r.getMessage() for r in caplog.records) == 1


def test_a_stock_with_futures_closes_by_auction_and_one_without_does_not(tmp_path, feed):
    resolver = Resolver(make_symtoken(tmp_path / "o.db", futures=("RELIANCE",)))
    rig = Rig(tmp_path, feed, resolver)
    rel = rig.share("a-1", "RELIANCE", "NSE_EQ|INE002A01018")
    tcs = rig.share("a-2", "TCS", TCS)
    run(rig.warm())
    cas, start, end, close = rig.pb.auction(rel, DAY)
    assert cas is True and start < end and close is not None
    assert rig.pb.auction(tcs, DAY) == (False, None, None, None)
    assert rig.pb.auction({"id": 9999}, DAY) == (False, None, None, None)


def test_the_last_session_day_comes_from_the_calendar_not_the_weekdays(tmp_path, feed):
    holidays = tmp_path / "h.json"
    holidays.write_text("2026-10-23\n")
    rig = Rig(tmp_path, feed)
    rig.pb.calendar = TradingCalendar(holidays, logger=logging.getLogger("t"))
    assert rig.pb.last_session_day(DAY, NOW) == DAY  # Monday, session begun
    assert rig.pb.last_session_day(DAY, MORNING) == 20261022  # before the open: Friday was shut
    assert rig.pb.last_session_day(20261025, NOW) == 20261022  # a Sunday


def test_a_manual_asset_is_still_its_owners_value(tmp_path, feed):
    rig = Rig(tmp_path, feed)
    gold = rig.book.asset_create(
        OWNER, "m-1", "manual", now=NOW, name="Gold", asset_class="other",
        first_value={"day": 20261001, "price_u": 50_000_000},
    )  # fmt: skip
    got = rig.now(gold)
    assert got["price_u"] == 50_000_000 and got["source"] == "manual"


def test_a_nav_entered_by_hand_stands_while_it_is_newer_than_amfis(tmp_path, feed):
    """Design 4.5: a fund is priced by the newest AMFI NAV or by a NAV the owner typed (the
    Update button of an old NAV). The typed one wins only while it is newer; AMFI's previous NAV
    stays the base of the day's change."""
    rig = Rig(tmp_path, feed)
    fund = rig.book.asset_create(
        OWNER, "f-1", "mf", now=NOW, amfi_code="120503", name="Some Flexi Cap Fund"
    )
    assert rig.now(fund)["price_u"] is None  # nothing yet
    rig.store.write(OWNER, lambda tx: tx.set_price_point(fund["id"], 20261020, 123_4567, NOW))
    typed = rig.now(fund)
    assert (typed["price_u"], typed["day"], typed["basis"]) == (123_4567, 20261020, "manual")
    rig.eod(fund, 20261021, 125_0000, NOW)  # AMFI publishes a newer NAV: it wins
    got = rig.now(fund)
    assert (got["price_u"], got["day"]) == (125_0000, 20261021)
    rig.store.write(OWNER, lambda tx: tx.set_price_point(fund["id"], 20261023, 126_0000, NOW))
    again = rig.now(fund)
    assert (again["price_u"], again["day"], again["basis"]) == (126_0000, 20261023, "manual")
    assert again["prev_close_u"] == 125_0000


def test_a_read_does_not_wait_on_a_feed_that_never_answers(tmp_path):
    """A feed that takes the connection and says nothing costs a read about a second, not the
    five seconds of an ordinary call (the bridge gives up at three)."""
    import socket
    import threading
    import time

    held = []
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(16)

    def accept():
        while True:
            try:
                held.append(server.accept()[0])  # taken, never answered
            except OSError:
                return

    threading.Thread(target=accept, daemon=True).start()

    class Silent:
        url = f"http://127.0.0.1:{server.getsockname()[1]}"

    rig = Rig(tmp_path, Silent())
    rig.share()

    async def go():
        started = time.monotonic()
        await rig.pb.warm_for_read(rig.store, NOW)
        took = time.monotonic() - started
        await rig.pb.aclose()
        return took

    try:
        took = run(go())
    finally:
        server.close()
        for conn in held:
            conn.close()
    assert took < 2.0, took
