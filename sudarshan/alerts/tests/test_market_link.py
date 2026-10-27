"""Market alerts' settings, their link to the feed and the market's day (M10.1, review A7.a-e)."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime

import httpx
import pytest
from alerts_fakes import RunningClock, ist, wait_until
from market_fakes import FakeFeed

from sudarshan.alerts.config import ConfigError
from sudarshan.alerts.market import days, sessions
from sudarshan.alerts.market.config import MarketSettings, market_on
from sudarshan.alerts.market.feedlink import FeedLink, FeedRefused, FeedUnreachable, parse_health
from sudarshan.autologin.schedule import IST, TradingCalendar

NIFTY, NIFTY500 = "NSE_INDEX|Nifty 50", "NSE_INDEX|Nifty 500"
SBIN = "NSE_EQ|INE062A01020"


# ---- settings ------------------------------------------------------------------------------


def test_market_alerts_are_off_unless_switched_on():
    assert market_on({}) is False and MarketSettings.from_env({}).enabled is False
    on = MarketSettings.from_env({"SUDARSHAN_MARKET_ALERTS": "on"})
    assert (on.enabled, on.http_port, on.feed_url, on.feed_zmq) == (
        True, 5582, "http://127.0.0.1:5580", "tcp://127.0.0.1:5581",
    )  # fmt: skip
    assert (on.max_instruments, on.max_active, on.keep_days, on.replay) == (200, 500, 100, False)


@pytest.mark.parametrize(
    ("name", "value", "words"),
    [
        ("SUDARSHAN_MARKET_ALERTS", "maybe", "must be on or off"),
        ("SUDARSHAN_ALERTS_FEED_URL", "http://192.0.2.1:5580", "on this server"),
        ("SUDARSHAN_ALERTS_FEED_URL", "https://127.0.0.1:5580", "on this server"),
        ("SUDARSHAN_ALERTS_FEED_ZMQ", "tcp://0.0.0.0:5581", "tcp://127.0.0.1:<port>"),
        ("SUDARSHAN_ALERTS_FEED_ZMQ", "ipc:///tmp/feed", "tcp://127.0.0.1:<port>"),
        ("SUDARSHAN_MARKET_ALERTS_MAX_INSTRUMENTS", "301", "from 1 to 300"),
        ("SUDARSHAN_MARKET_ALERTS_KEEP_DAYS", "30", "from 90 to 3650"),
        ("SUDARSHAN_ALERTS_HTTP_PORT", "port", "whole number"),
    ],
)
def test_settings_that_cannot_work_are_refused_in_words(name, value, words):
    with pytest.raises(ConfigError, match=words):
        MarketSettings.from_env({"SUDARSHAN_MARKET_ALERTS": "on", name: value})


# ---- health --------------------------------------------------------------------------------


def test_health_is_read_as_the_feed_sends_it():
    doc = {"state": "connected", "subscriptions_live": True, "started_at": "s1",
           "market_status": {"NSE_EQ": "NORMAL_OPEN", "X": 7}, "last_message_age_s": 2.5}  # fmt: skip
    health = parse_health(json.dumps(doc).encode(), 123)
    assert (health.state, health.live, health.heard_at, health.started_at) == (
        "connected", True, 123, "s1",
    )  # fmt: skip
    assert health.status == {"NSE_EQ": "NORMAL_OPEN"} and health.message_age_s == 2.5
    assert parse_health(b"not json", 1) is None and parse_health(b"[1]", 1) is None
    bare = parse_health(b"{}", 5)
    assert (bare.state, bare.live, bare.status, bare.message_age_s) == ("unknown", False, {}, None)


# ---- the link ------------------------------------------------------------------------------


def test_the_link_takes_exact_topics_and_talks_to_the_feed():
    async def scenario():
        clock = RunningClock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, httpx.AsyncClient(trust_env=False) as client:
            link = FeedLink(feed.endpoint, feed.url, client)
            try:
                link.follow([NIFTY])
                got: list = []

                async def drain():
                    while True:
                        got.extend(await link.receive())

                reader = asyncio.create_task(drain())
                await wait_until(lambda: any(topic == "__health__" for topic, _ in got))
                await asyncio.sleep(0.1)  # the topic subscription reaches the PUB
                await feed.tick(NIFTY500, 23000.0)  # matches "NSE_INDEX|Nifty 50" as a prefix
                await feed.tick(NIFTY, 25900.0)
                await wait_until(lambda: any(topic == NIFTY for topic, _ in got))
                reader.cancel()
                assert NIFTY500 not in {topic for topic, _ in got}
                link.follow([SBIN])
                assert link.topics == {SBIN}

                answer = await link.put_claims({SBIN: "ltpc", NIFTY: "full"})
                assert answer["claimed"] == 2 and feed.keys == {SBIN: "ltpc", NIFTY: "full"}
                feed.snapshot = {NIFTY: {"ltp": 25900.0, "ltt": 1}}
                keys = [f"NSE_FO|{n}" for n in range(150)] + [NIFTY]
                found = await link.snapshot(keys)
                assert found == {NIFTY: {"ltp": 25900.0, "ltt": 1, "key": NIFTY}}
                assert [len(ask) for ask in feed.snapshot_asks] == [100, 51]
                assert NIFTY in feed.snapshot_asks[-1]  # the space and "|" survive the URL
                feed.refuse = (409, "sudarshan-alerts may claim at most 300 keys; it asked for 301")
                with pytest.raises(FeedRefused, match="at most 300 keys"):
                    await link.put_claims({SBIN: "ltpc"})
                await link.drop_claims()
                assert feed.drops == 1
            finally:
                link.close()
            down = FeedLink(feed.endpoint, "http://127.0.0.1:9", client)
            try:
                with pytest.raises(FeedUnreachable):
                    await down.put_claims({})
            finally:
                down.close()

    asyncio.run(scenario())


# ---- the day -------------------------------------------------------------------------------


@pytest.fixture
def calendar(tmp_path):
    holidays, extra = tmp_path / "holidays.txt", tmp_path / "extra.txt"
    holidays.write_text("2026-11-10\n")
    extra.write_text("2026-11-08 14:05-15:40  # Muhurat\n2026-10-22 17:45-19:30  # added\n")
    return TradingCalendar(holidays, extra)


def clock_of(session) -> tuple[str, ...]:
    return tuple(_hm(t) for t in (session.start, session.end))


def _hm(at: int) -> str:
    return datetime.fromtimestamp(at / 1000, IST).strftime("%H:%M")


def test_a_day_comes_from_the_calendar_files(calendar, caplog):
    built = days.Days(calendar)
    usual, previous = built.of(date(2026, 10, 21))
    assert clock_of(usual["NSE_EQ"]) == ("09:15", "15:30") and previous == 20261020
    muhurat, previous = built.of(date(2026, 11, 8))
    assert clock_of(muhurat["NSE_FO"]) == ("14:20", "15:40") and previous == 20261106
    assert built.of(date(2026, 11, 9))[1] == 20261108  # Monday's day before is the Sunday
    assert built.of(date(2026, 11, 10)) == ({}, 20261109)  # a holiday
    with caplog.at_level(logging.ERROR, logger="sudarshan.alerts.market"):
        added, _ = built.of(date(2026, 10, 22))
        built.of(date(2026, 10, 22))
    assert clock_of(added["NSE_EQ"]) == ("09:15", "15:30")  # never the window alone
    assert caplog.text.count("market alerts check the usual hours only") == 1


def test_cas_keys_follow_the_master_and_the_7_december_rule():
    class A:
        def __init__(self, key, symbol):
            self.key, self.symbol, self.segment = key, symbol, sessions.segment_of(key)

    alerts = [A(SBIN, "SBIN"), A("NSE_EQ|INE000X", "SMALLCO"), A(NIFTY, "NIFTY"),
              A("BSE_EQ|500112", "SBIN")]  # fmt: skip
    oct21 = days.cas_keys(alerts, date(2026, 10, 21), frozenset({"SBIN"}))
    assert oct21 == {SBIN, "BSE_EQ|500112"}
    dec7 = days.cas_keys(alerts, date(2026, 12, 7), frozenset())
    assert dec7 == {SBIN, "NSE_EQ|INE000X", "BSE_EQ|500112"}


def test_the_master_gives_the_companies_with_stock_futures(tmp_path):
    from sqlalchemy.orm import Session

    from database.engine_factory import create_db_engine
    from database.symbol import Base, SymToken

    path = tmp_path / "openalgo.db"
    engine = create_db_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine, tables=[SymToken.__table__])
    rows = [("SBIN30DEC26FUT", "NFO"), ("M&M30DEC26FUT", "NFO"), ("NIFTY30DEC26FUT", "NFO"),
            ("BANKNIFTY30DEC26FUT", "NFO"), ("SBIN30DEC26800CE", "NFO"), ("SBIN", "NSE"),
            ("SENSEX24DEC26FUT", "BFO")]  # fmt: skip
    with Session(engine) as session:
        for symbol, exchange in rows:
            session.add(SymToken(symbol=symbol, brsymbol=symbol, exchange=exchange, token=symbol))
        session.commit()
    engine.dispose()
    assert days.fno_stocks(path) == {"SBIN", "M&M"}
    with pytest.raises(days.MasterUnavailable):
        days.fno_stocks(tmp_path / "missing" / "openalgo.db")
