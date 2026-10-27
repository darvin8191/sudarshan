"""Market alerts at run time (M10.1, M10.9) against a fake feed and a fake OpenAlgo /notify.

The clock is the test's own and moves only when a test moves it; the fakes' heartbeat and the
runtime's timers run in real time (shrunk by ``quick``).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

import pytest
from alerts_fakes import API_KEY, OWNER, FakeOpenAlgo, ist, wait_until
from market_fakes import FakeFeed, ms

from sudarshan.alerts import outbox
from sudarshan.alerts.config import Settings
from sudarshan.alerts.market import lane, runtime, spec
from sudarshan.alerts.market.config import MarketSettings
from sudarshan.alerts.market.engine import Fire, State
from sudarshan.alerts.market.runtime import MarketRuntime
from sudarshan.alerts.market.store import MarketStore
from sudarshan.alerts.outbox import Pacer
from sudarshan.alerts.owner import Owner

SBIN = "NSE_EQ|INE062A01020"
HEADER = "सुदर्शन / SUDARSHAN"


class Clock:
    def __init__(self, at: datetime):
        self.at = at

    def __call__(self) -> datetime:
        return self.at

    def advance(self, seconds: float) -> None:
        self.at += timedelta(seconds=seconds)


def market_of(feed: FakeFeed) -> MarketSettings:
    return MarketSettings.from_env({"SUDARSHAN_MARKET_ALERTS": "on", "SUDARSHAN_ALERTS_FEED_URL":
                                    feed.url, "SUDARSHAN_ALERTS_FEED_ZMQ": feed.endpoint})  # fmt: skip


def create(settings: Settings, at: datetime, **fields) -> dict:
    store = MarketStore(settings.state_dir / "market.db")
    try:
        definition = {"instrument_key": SBIN, "symbol": "SBIN", "exchange": "NSE",
                      "tick_size": "0.05", "kind": "price",
                      "params": {"op": "crosses_above", "level": "100"}} | fields  # fmt: skip
        return store.create("owner", spec.check(definition, ms(at)), ms(at))
    finally:
        store.close()


def record(settings: Settings, method: str, *args, **kwargs):
    """One call on market.db from outside the runtime, as the replay check or a test reads it."""
    store = MarketStore(settings.state_dir / "market.db")
    try:
        return getattr(store, method)("owner", *args, **kwargs)
    finally:
        store.close()


async def started(settings, feed, clock, **more) -> tuple[MarketRuntime, asyncio.Task]:
    rt = MarketRuntime(settings, market_of(feed), owner_reader=lambda: Owner(OWNER, API_KEY),
                       pacer=Pacer(), clock=clock, **more)  # fmt: skip
    task = asyncio.create_task(rt.run())
    await wait_until(lambda: rt.state == "running")
    while rt._health is None:  # a slow heartbeat: send health until the SUB has joined
        await feed.send_health()
        await asyncio.sleep(0.02)
    await wait_until(lambda: SBIN in feed.keys or not rt.book.alerts())
    await asyncio.sleep(0.15)  # the topic subscriptions reach the publisher
    return rt, task


async def stopped(rt: MarketRuntime, task: asyncio.Task) -> None:
    rt.stop("test")
    await asyncio.wait_for(task, 10)


async def trade(feed, clock, price, step: float = 1.0, **fields) -> None:
    clock.advance(step)
    await feed.tick(SBIN, price, **fields)


def test_a_crossing_in_the_feed_reaches_telegram_once_and_is_recorded(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            create(settings, clock())
            rt, task = await started(settings, feed, clock)
            assert feed.keys == {SBIN: "ltpc"}  # its claim: the key and the mode it needs
            await trade(feed, clock, 99.95)
            await wait_until(lambda: rt.stats["judged"] == 1)
            await trade(feed, clock, 100.05, ltq=2)
            await wait_until(lambda: len(notify.bodies) == 1)
            lines = notify.messages[0].split("\n")
            assert lines[0] == HEADER and lines[1] == (
                "Alert: SBIN crossed above ₹100.00: now ₹100.05 (NSE)"
            )
            await trade(feed, clock, 99.9, ltq=3)  # Once: stopped, never told again
            await trade(feed, clock, 100.2, ltq=4)
            await asyncio.sleep(0.2)
            await wait_until(lambda: record(settings, "events")[0]["deliveries"]["telegram"][
                "status"] == "sent")  # fmt: skip
            await stopped(rt, task)
            assert len(notify.bodies) == 1 and record(settings, "alert", 1)["status"] == "stopped"
            assert feed.keys == {}  # a stopped alert claims nothing

    asyncio.run(scenario())


def test_an_exchange_status_pushed_by_the_feed_opens_the_gate_at_once(alerts, quick):
    """Review A7.d: the first seconds of a session are not lost to the 5 s health cycle."""

    async def scenario():
        clock = Clock(ist(2026, 10, 21, 9, 15, 0))
        async with FakeFeed(clock, beat_s=3600) as feed, FakeOpenAlgo() as notify:
            feed.health["market_status"]["NSE_EQ"] = "PRE_OPEN_END"
            settings = alerts(notify)
            create(settings, clock(), frequency="every_time")
            await feed.send_health()
            rt, task = await started(settings, feed, clock)
            await trade(feed, clock, 99.95)
            await asyncio.sleep(0.1)
            assert rt.stats["judged"] == 0  # the exchange has not opened the segment yet
            feed.health["market_status"]["NSE_EQ"] = "NORMAL_OPEN"
            await feed.send_health()  # pushed on the change, not 5 s later
            await wait_until(lambda: rt.market.status.get("NSE_EQ") == "NORMAL_OPEN")
            await trade(feed, clock, 99.95, ltq=2)
            await trade(feed, clock, 100.05, ltq=3)
            await wait_until(lambda: len(notify.bodies) == 1)
            await stopped(rt, task)

    asyncio.run(scenario())


def test_a_feed_outage_is_a_pause_told_on_telegram_and_marks_what_it_hid(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            create(settings, clock(), frequency="every_time")
            rt, task = await started(settings, feed, clock)
            await trade(feed, clock, 99.95)
            await wait_until(lambda: rt.stats["judged"] == 1)
            feed.health |= {"state": "backoff", "subscriptions_live": False}
            await wait_until(lambda: rt._open is not None)
            for _ in range(7):  # over a minute down, in steps the heartbeat keeps up with
                clock.advance(10)
                await asyncio.sleep(0.06)
            await wait_until(lambda: len(notify.bodies) == 1)
            assert notify.messages[0].split("\n")[1] == (
                "Market alerts paused since 10:00:01 IST: the price feed is reconnecting."
            )
            feed.health |= {"state": "connected", "subscriptions_live": True}
            await wait_until(lambda: rt._open is None)
            await trade(feed, clock, 100.1, ltq=2)
            await wait_until(lambda: "now ₹100.10" in "\n".join(notify.messages))
            told = "\n".join(notify.messages[1:])
            assert "Market alerts paused 10:00:01 to 10:01:11 IST: the price feed was" in told
            assert (
                "Alert: SBIN crossed above ₹100.00: now ₹100.10 (NSE) (seen after a pause in "
                "market alerts, 10:00 to 10:01)" in told
            )
            await stopped(rt, task)
            pauses = record(settings, "pauses", 0)
            assert [(p.cause, p.end - p.start) for p in pauses] == [("feed", 70_000)]

    asyncio.run(scenario())


def test_silence_on_the_whole_socket_is_a_pause(alerts, quick):
    """Review A7.e: connected and live, but no message from Upstox for over 10 s."""

    async def scenario():
        clock = Clock(ist(2026, 10, 21, 11, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            create(settings, clock())
            rt, task = await started(settings, feed, clock)
            feed.health["last_message_age_s"] = 15.0
            await wait_until(lambda: rt._open is not None)
            assert rt._open[1] == ms(clock()) - 15_000 and rt._open[2] == "feed"
            clock.advance(5)
            feed.health["last_message_age_s"] = 0.2
            await wait_until(lambda: rt._open is None)
            await stopped(rt, task)
            assert [(p.cause, p.end - p.start) for p in record(settings, "pauses", 0)] == [
                ("feed", 20_000)
            ]

    asyncio.run(scenario())


def test_a_restart_tells_nothing_twice_and_marks_what_its_pause_hid(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            create(settings, clock(), frequency="every_time")
            rt, task = await started(settings, feed, clock)
            await trade(feed, clock, 99.95)
            await trade(feed, clock, 100.05, ltq=2)
            await wait_until(lambda: len(notify.bodies) == 1)
            await trade(feed, clock, 99.9, ltq=3)
            await wait_until(lambda: rt.stats["judged"] == 3)
            await stopped(rt, task)  # a clean stop flushes the side: below
            clock.advance(120)
            rt, task = await started(settings, feed, clock)
            await trade(feed, clock, 100.15, ltq=4)
            await wait_until(lambda: "now ₹100.15" in "\n".join(notify.messages))
            told = "\n".join(notify.messages[1:])
            assert (
                "Market alerts paused 10:00:03 to 10:02:03 IST: market alerts were not running"
                in told
            )
            assert "now ₹100.15 (NSE) (seen after a pause in market alerts, 10:00 to 10:02)" in told
            await stopped(rt, task)
            events = record(settings, "events")
            assert [e["value"] for e in events] == ["₹100.15", "₹100.05"]  # each once
            assert [p.cause for p in record(settings, "pauses", 0)] == ["service"]

    asyncio.run(scenario())


def test_a_feed_that_restarts_gets_the_claim_again(alerts, quick):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            create(settings, clock())
            rt, task = await started(settings, feed, clock)
            claimed = len(feed.claims)
            feed.health["started_at"] = "2026-10-21T10:00:30+05:30"
            await wait_until(lambda: len(feed.claims) == claimed + 1)
            assert feed.keys == {SBIN: "ltpc"}
            await stopped(rt, task)

    asyncio.run(scenario())


def test_deliveries_left_pending_go_if_fresh_and_never_late(alerts, quick):
    """Review A7.o: more than 5 minutes after the event, a line is not sent at a restart."""

    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 30))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            row = create(settings, clock() - timedelta(hours=1), frequency="every_time")
            for minutes, n in ((10, 1), (1, 2)):
                at = ms(clock() - timedelta(minutes=minutes))
                fire = Fire(row["id"], f"1:v1:x{n}", 20261021, 0, at, at, 10005, 10000, 1, n,
                            version=1)  # fmt: skip
                record(settings, "record_fire", fire, State(20261021, 1), title="t",
                       line=f"Alert: line {n}", value="v", level="l",
                       channels={"telegram": True})  # fmt: skip
            rt, task = await started(settings, feed, clock)
            await wait_until(lambda: len(notify.bodies) == 1)
            assert "Alert: line 2" in notify.messages[0] and "line 1" not in notify.messages[0]
            await stopped(rt, task)
            by_line = {e["line"]: e["deliveries"]["telegram"] for e in record(settings, "events")}
            assert by_line["Alert: line 1"]["status"] == "not_sent"
            assert by_line["Alert: line 1"]["reason"] == runtime.RESTARTED
            assert by_line["Alert: line 2"]["status"] == "sent"

    asyncio.run(scenario())


def test_a_fire_for_an_alert_deleted_meanwhile_is_never_told(alerts, quick, caplog):
    """Review round 2, item 1: record_fire raises NotFound; the runtime drops that fire."""

    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            row = create(settings, clock())
            rt, task = await started(settings, feed, clock)
            await trade(feed, clock, 99.95)
            await wait_until(lambda: rt.stats["judged"] == 1)
            record(settings, "delete", row["id"])  # the store first, as the API deletes
            await trade(feed, clock, 100.05, ltq=2)
            await wait_until(lambda: "fired as it was deleted" in caplog.text)
            await stopped(rt, task)
            assert notify.bodies == [] and record(settings, "events") == []

    caplog.set_level(logging.INFO, logger="sudarshan.alerts.market")
    asyncio.run(scenario())


def test_a_cas_stocks_quiet_before_its_auction_is_no_pause(alerts, quick):
    """Cross-design X6g: SBIN closes by auction; nothing trades 15:15-15:31 while the rest of the
    socket ticks; its print at 15:31:10 is told as the auction's, not as seen after a pause."""

    async def scenario():
        clock = Clock(ist(2026, 10, 21, 15, 14))
        async with FakeFeed(clock, beat_s=0.005) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            create(settings, clock())
            rt, task = await started(settings, feed, clock)
            rt._fno = frozenset({"SBIN"})  # the master's stock futures, as the morning read gives
            rt._rebuild()
            await trade(feed, clock, 99.95, step=0)
            await wait_until(lambda: rt.stats["judged"] == 1)
            while clock() < ist(2026, 10, 21, 15, 31, 10):
                clock.advance(10)
                await wait_until(lambda: rt._health.heard_at == ms(clock()))
            assert rt._open is None
            await trade(feed, clock, 100.2, step=0, ltq=2)
            await wait_until(lambda: len(notify.bodies) == 1)
            assert notify.messages[0].split("\n")[1] == (
                "Alert: SBIN crossed above ₹100.00 in the closing auction: closing auction price "
                "₹100.20 (NSE)"
            )
            await stopped(rt, task)
            assert record(settings, "pauses", 0) == ()

    asyncio.run(scenario())


def test_market_alerts_run_with_order_alerts_off_and_stop_with_them(alerts, quick):
    from sudarshan.alerts import service

    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            pacer = Pacer()
            svc = service.AlertService(settings, owner_reader=lambda: Owner(OWNER, API_KEY),
                                       pacer=pacer)  # fmt: skip
            rt = MarketRuntime(settings, market_of(feed), owner_reader=lambda: Owner(OWNER, API_KEY),
                               pacer=pacer, clock=clock)  # fmt: skip
            serving = asyncio.create_task(service._serve(svc, rt))
            await wait_until(lambda: rt.state == "running" and svc.state == "off")
            assert not serving.done()  # order alerts off: market alerts carry the process
            service._stop(svc, rt, "test")
            await asyncio.wait_for(serving, 10)
            assert rt.state == "stopped"

    asyncio.run(scenario())


def test_with_market_alerts_off_the_feed_drops_their_keys(alerts, quick):
    from sudarshan.alerts.market.config import feed_url_of
    from sudarshan.alerts.market.runtime import ReleasedClaims

    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed:
            feed.answering = False  # the first ask finds it busy; asked again later
            released = ReleasedClaims(feed.url)
            released.WAITS_S = (0.0, 0.05, 0.05)
            running = asyncio.create_task(released.run())
            await asyncio.sleep(0.02)
            feed.answering = True
            await asyncio.wait_for(running, 5)
            assert feed.drops == 1

    asyncio.run(scenario())
    assert feed_url_of({"SUDARSHAN_ALERTS_FEED_URL": "http://192.0.2.1:5580"}) == (
        "http://127.0.0.1:5580"
    )


def test_a_recorded_day_replayed_through_the_feed_tool_fires(alerts, quick, tmp_path):
    """The real sudarshan.feed.replay publishes a recording; the runtime, in replay mode, judges
    it on the ticks' own clock and tells the crossing (the evening check's path, M10.1)."""
    import gzip
    import json
    import socket

    from sudarshan.feed.replay import replay

    async def scenario():
        start = ist(2026, 10, 21, 10, 0)
        recording = tmp_path / "2026-10-21.jsonl.gz"
        with gzip.open(recording, "wt") as out:
            for n, price in enumerate((99.90, 99.95, 100.05)):
                at = ms(start) + n * 1000
                out.write(json.dumps({"key": SBIN, "ltp": price, "ltt": at, "ltq": n + 1,
                                      "ts_recv": at + 20}) + "\n")  # fmt: skip
        async with FakeFeed(Clock(start)) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            create(settings, start - timedelta(hours=1))
            with socket.socket() as probe:  # a free port for the replay to bind
                probe.bind(("127.0.0.1", 0))
                port = probe.getsockname()[1]
            market = MarketSettings.from_env({
                "SUDARSHAN_MARKET_ALERTS": "on", "SUDARSHAN_MARKET_ALERTS_REPLAY": "1",
                "SUDARSHAN_ALERTS_FEED_URL": feed.url,
                "SUDARSHAN_ALERTS_FEED_ZMQ": f"tcp://127.0.0.1:{port}",
            })  # fmt: skip
            rt = MarketRuntime(settings, market, owner_reader=lambda: Owner(OWNER, API_KEY),
                               pacer=Pacer())  # fmt: skip
            task = asyncio.create_task(rt.run())
            await wait_until(lambda: rt.state == "running" and SBIN in rt.link.topics)
            sent = await asyncio.to_thread(replay, recording, port, 0, 10.0, 0.5)
            assert sent == 3
            await wait_until(lambda: len(notify.bodies) == 1)
            assert notify.messages[0].split("\n")[1] == (
                "Alert: SBIN crossed above ₹100.00: now ₹100.05 (NSE)"
            )
            assert notify.messages[0].endswith("10:00:02 IST")  # the recording's time
            await stopped(rt, task)

    asyncio.run(scenario())
