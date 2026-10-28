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
    socket ticks; its print at 15:31:10 is told as the auction's, not as seen after a pause. Its
    key is claimed in full mode, for the volume that tells the print (X6i)."""

    async def scenario():
        clock = Clock(ist(2026, 10, 21, 15, 14))
        async with FakeFeed(clock, beat_s=0.005) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            create(settings, clock())
            rt, task = await started(settings, feed, clock)
            assert feed.keys == {SBIN: "ltpc"}
            rt._fno = frozenset({"SBIN"})  # the master's stock futures, as the morning read gives
            rt._rebuild()
            await wait_until(lambda: feed.keys == {SBIN: "full"})
            await trade(feed, clock, 99.95, step=0, vtt=1000)
            await wait_until(lambda: rt.stats["judged"] == 1)
            while clock() < ist(2026, 10, 21, 15, 31, 10):
                clock.advance(10)
                await wait_until(lambda: rt._health.heard_at == ms(clock()))
            assert rt._open is None
            await trade(feed, clock, 100.2, step=0, ltq=2, vtt=3000)
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


def test_the_master_is_read_again_at_9_and_a_bad_read_keeps_the_list(alerts, monkeypatch, tmp_path):
    """Review M4: a start before 09:00 stood for the whole day, so the list the day's login
    downloads at 08:45 was never read; a failed read was not tried again that day."""
    from sudarshan.alerts.market.days import MasterUnavailable

    answers = [frozenset({"SBIN"}), MasterUnavailable("busy"), frozenset({"SBIN", "INFY"})]
    reads = []

    def fno_stocks(path):
        reads.append(path)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(runtime, "fno_stocks", fno_stocks)
    monkeypatch.setattr(runtime, "MASTER_RETRY_S", 0.2)

    async def scenario():
        clock = Clock(ist(2026, 10, 21, 8, 40))
        async with FakeOpenAlgo() as notify:
            rt = MarketRuntime(alerts(notify), MarketSettings(enabled=True),
                               owner_reader=lambda: Owner(OWNER, API_KEY), pacer=Pacer(),
                               openalgo_db=tmp_path / "openalgo.db", clock=clock)  # fmt: skip

            async def at(moment):
                clock.at = moment
                rt._roll(ms(moment), force=True)
                rt._read_master(ms(moment))
                await asyncio.sleep(0.05)

            await at(ist(2026, 10, 21, 8, 40))
            await at(ist(2026, 10, 21, 8, 50))  # still before 09:00: the 08:40 read stands
            assert len(reads) == 1 and rt._fno == {"SBIN"}
            await at(ist(2026, 10, 21, 9, 5))  # the day's download: read again, refused
            assert len(reads) == 2 and rt._fno == {"SBIN"}  # the list it had is kept
            await at(ist(2026, 10, 21, 9, 5, 1))  # not at once
            assert len(reads) == 2
            await asyncio.sleep(0.25)
            await at(ist(2026, 10, 21, 9, 10))
            assert len(reads) == 3 and rt._fno == {"SBIN", "INFY"}
            await at(ist(2026, 10, 21, 14, 0))  # settled for the day
            assert len(reads) == 3
            rt._writer.shutdown()

    asyncio.run(scenario())


async def heard(rt, feed, clock, seconds: int, step: int = 10) -> None:
    """Time passing with the feed's health heard at each step."""
    for _ in range(seconds // step):
        clock.advance(step)
        await wait_until(lambda: rt._health.heard_at == ms(clock()))


def test_a_restart_inside_a_feed_pause_closes_it_and_keeps_the_days_pauses(
    alerts, quick, monkeypatch
):
    """Review M6 (A7.f): the run that stops while the feed is down leaves its pause open; the
    next one never read it, so the "paused since" line got no closing line, the pause stayed open
    for ever, and the next fire spoke of 10:03 to 10:04 only."""

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
            await heard(rt, feed, clock, 180)  # three minutes down
            await asyncio.sleep(0.2)

            async def dies(self):  # the process dies: nothing is flushed or closed
                self.link.close()

            monkeypatch.setattr(MarketRuntime, "_shutdown", dies)
            await stopped(rt, task)
            monkeypatch.undo()
            clock.advance(60)  # back up a minute later, the feed with it
            feed.health |= {"state": "connected", "subscriptions_live": True}
            rt, task = await started(settings, feed, clock)
            assert [(p.start, p.end is not None) for p in rt.market.pauses][0] == (
                ms(ist(2026, 10, 21, 10, 0, 1)), True)  # fmt: skip
            await trade(feed, clock, 100.1, ltq=2)
            await wait_until(lambda: "now ₹100.10" in "\n".join(notify.messages))
            told = "\n".join(notify.messages)
            await stopped(rt, task)
        assert [p for p in record(settings, "pauses", 0) if p.end is None] == []
        assert "Market alerts paused 10:00:01 to 10:03:01 IST: the price feed was" in told
        assert "(seen after a pause in market alerts, 10:00 to 10:04)" in told

    asyncio.run(scenario())


def test_an_outage_after_the_sessions_its_alerts_are_in_is_no_pause(alerts, quick):
    """Review L3: with only cash alerts, the F&O session (to 15:40) made a 15:31 outage a pause
    told on Telegram; and outside every session (mutant R6) nothing is a pause."""

    async def scenario():
        clock = Clock(ist(2026, 10, 21, 15, 31))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            create(settings, clock())
            rt, task = await started(settings, feed, clock)
            feed.health |= {"state": "backoff", "subscriptions_live": False}
            await heard(rt, feed, clock, 120)
            clock.at = ist(2026, 10, 21, 18, 0)
            await heard(rt, feed, clock, 120)
            assert rt._open is None
            await stopped(rt, task)
        assert notify.messages == [] and record(settings, "pauses", 0) == ()

    asyncio.run(scenario())


def test_a_feed_down_before_the_session_is_a_pause_from_its_start(alerts, quick):
    """Mutant R16: the pause starts when the session does, not when the feed went down."""

    async def scenario():
        clock = Clock(ist(2026, 10, 21, 9, 5))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            create(settings, clock())
            feed.health |= {"state": "backoff", "subscriptions_live": False}
            rt, task = await started(settings, feed, clock)
            await heard(rt, feed, clock, 600)  # 09:15: the session opens, the feed still down
            await wait_until(lambda: rt._open is not None)
            assert rt._open[1] == ms(ist(2026, 10, 21, 9, 15))
            await stopped(rt, task)

    asyncio.run(scenario())


def test_a_pause_lasts_until_the_feed_is_back_inside_the_arrival_grace(alerts, quick):
    """Mutant R19: a trade made before the close may arrive up to 5 s after it, so a feed back
    within that grace ends the pause when it came back, not at the close."""

    async def scenario():
        clock = Clock(ist(2026, 10, 21, 15, 28, 30))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            create(settings, clock())
            rt, task = await started(settings, feed, clock)
            feed.health |= {"state": "backoff", "subscriptions_live": False}
            await wait_until(lambda: rt._open is not None)
            await heard(rt, feed, clock, 90)  # 15:30:00
            clock.advance(3)  # 15:30:03, inside the grace
            feed.health |= {"state": "connected", "subscriptions_live": True}
            await wait_until(lambda: rt._open is None)
            await stopped(rt, task)
        [pause] = record(settings, "pauses", 0)
        assert pause.end == ms(ist(2026, 10, 21, 15, 30, 3))

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "health",
    [
        {"state": "backoff"},  # mutant R2: the feed's state ignored
        {"subscriptions_live": False},  # mutant R3: its subscriptions ignored
        "silent",  # mutant R1: overdue health taken as heard
    ],
    ids=["state", "subscriptions", "no health"],
)
def test_each_sign_of_a_feed_down_is_a_pause_on_its_own(alerts, quick, health):
    async def scenario():
        clock = Clock(ist(2026, 10, 21, 11, 0))
        beat = 3600 if health == "silent" else 0.05
        async with FakeFeed(clock, beat_s=beat) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            create(settings, clock())
            await feed.send_health()
            rt, task = await started(settings, feed, clock)
            if health == "silent":  # no health for longer than it may be overdue
                clock.advance(13)
            else:
                feed.health |= health
            await wait_until(lambda: rt._open is not None)
            await stopped(rt, task)

    asyncio.run(scenario())


def test_a_pause_open_at_the_close_ends_at_the_close(alerts, quick):
    """Mutant R6: outside the session nothing is a pause; one that runs past the close ends
    there, though the feed is still down."""

    async def scenario():
        clock = Clock(ist(2026, 10, 21, 15, 29))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            create(settings, clock())
            rt, task = await started(settings, feed, clock)
            feed.health |= {"state": "backoff", "subscriptions_live": False}
            await wait_until(lambda: rt._open is not None)
            await heard(rt, feed, clock, 120)  # 15:31, still down
            await wait_until(lambda: rt._open is None)
            await stopped(rt, task)
        [pause] = record(settings, "pauses", 0)
        assert pause.end == ms(ist(2026, 10, 21, 15, 30))

    asyncio.run(scenario())


def failing_record(monkeypatch, *, commit: bool, then=None):
    """MarketStore.record_fire failing once: after it committed (``commit``), or before."""
    from sudarshan.alerts.market.store import StoreError

    real, calls = MarketStore.record_fire, []

    def record_fire(self, *args, **kwargs):
        calls.append(args)
        if len(calls) > 1:
            return real(self, *args, **kwargs)
        if commit:
            event_id = real(self, *args, **kwargs)
            if then is not None:
                then(self, event_id)
        raise StoreError("This could not be saved.", "disk I/O error")

    monkeypatch.setattr(MarketStore, "record_fire", record_fire)
    return calls


def test_a_fire_that_committed_despite_an_error_still_reaches_the_app(alerts, quick, monkeypatch):
    """Review L9: found with Telegram off, a fire that committed returned nothing, so its push
    to the open apps was skipped too."""
    import os

    from sudarshan.alerts.market.push import ALERT_EVENT
    from sudarshan.vault import ALERTS_PUSH_INFO, fernet_for

    failing_record(monkeypatch, commit=True)

    def pushed(notify) -> list[int]:
        return [
            i["data"]["event_id"] for b in notify.pushes for i in b if i["event"] == ALERT_EVENT
        ]

    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            create(settings, clock(), channels={"telegram": False, "app": True})
            fernet = fernet_for(os.environ["API_KEY_PEPPER"], ALERTS_PUSH_INFO)
            rt, task = await started(settings, feed, clock, push_fernet=fernet)
            await trade(feed, clock, 99.95)
            await trade(feed, clock, 100.05, ltq=2)
            await wait_until(lambda: pushed(notify))
            await asyncio.sleep(0.3)
            assert pushed(notify) == [1] and notify.bodies == []
            await stopped(rt, task)

    asyncio.run(scenario())


def test_a_fire_found_already_told_is_never_told_again(alerts, quick, monkeypatch):
    """Mutant R11: a committed fire whose Telegram delivery is settled is not told twice."""

    def sent(store, event_id):
        store.set_delivery("owner", event_id, "telegram", "sent", "", 0)

    failing_record(monkeypatch, commit=True, then=sent)

    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            create(settings, clock())
            rt, task = await started(settings, feed, clock)
            await trade(feed, clock, 99.95)
            await trade(feed, clock, 100.05, ltq=2)
            await wait_until(lambda: record(settings, "events"))
            await asyncio.sleep(0.5)
            assert notify.bodies == []
            await stopped(rt, task)

    asyncio.run(scenario())


def test_a_fire_recorded_again_keeps_the_newer_state(alerts, quick, monkeypatch):
    """Review L9: the fire recorded again after a failed write wrote its state as it was at the
    fire over the newer one the Book had saved meanwhile."""
    monkeypatch.setattr(runtime, "RECORD_RETRY_S", (1.0,))
    calls = failing_record(monkeypatch, commit=False)

    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            create(settings, clock(), frequency="every_time")
            rt, task = await started(settings, feed, clock)
            await trade(feed, clock, 99.95)
            await trade(feed, clock, 100.05, ltq=2)  # fires; its record fails
            await wait_until(lambda: calls)
            await trade(feed, clock, 99.9, ltq=3)  # below again: the Book's newer state
            clock.advance(2)
            await wait_until(lambda: record(settings, "states").get(1, State()).side == -1)
            await wait_until(lambda: len(calls) == 2 and record(settings, "events"))
            assert record(settings, "states")[1].side == -1  # not the fire's +1
            await stopped(rt, task)

    asyncio.run(scenario())


def test_states_a_failed_flush_could_not_save_are_saved_by_the_next(alerts, quick, monkeypatch):
    """Mutant R12: a flush that fails puts what it held back, for the next flush."""
    from sudarshan.alerts.market.store import StoreError

    real, calls = MarketStore.save_states, []

    def save_states(self, principal, states, now):
        below = any(state.side == -1 for _, state in states.values())
        if below and not calls:  # the flush that holds the trade's state: refused once
            calls.append(states)
            raise StoreError("This could not be saved.", "disk I/O error")
        return real(self, principal, states, now)

    monkeypatch.setattr(MarketStore, "save_states", save_states)

    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            create(settings, clock())
            rt, task = await started(settings, feed, clock)
            await trade(feed, clock, 99.95)
            clock.advance(2)
            await wait_until(lambda: calls)  # the first flush, refused
            clock.advance(2)
            await wait_until(lambda: record(settings, "states").get(1, State()).side == -1)
            await stopped(rt, task)

    asyncio.run(scenario())


def test_a_runtime_cancelled_in_its_stop_still_closes_its_feed_link(alerts, quick):
    """Review L7: the link was closed only at the end of a clean stop, so a cancel that landed
    in the stop's flush left its ZMQ context open, and garbage collection could hang the
    process at exit terming it."""

    async def scenario():
        clock = Clock(ist(2026, 10, 21, 10, 0))
        async with FakeFeed(clock) as feed, FakeOpenAlgo() as notify:
            settings = alerts(notify)
            create(settings, clock())
            rt, task = await started(settings, feed, clock)
            link, flush, held = rt.link, rt._flush, asyncio.Event()

            async def stuck(now, wait=False):  # the stop's own flush never returns
                if not wait:
                    return await flush(now, wait)
                held.set()
                await asyncio.Event().wait()

            rt._flush = stuck
            rt.stop("test")
            await asyncio.wait_for(held.wait(), 5)
            task.cancel()  # as a stop that took too long, or asyncio.run's own teardown, does
            await asyncio.gather(task, return_exceptions=True)
            closed = (link.sock.closed, link.ctx.closed)
            link.close()  # never left open: the old code must not hang this process at exit
            assert task.cancelled() and closed == (True, True)

    asyncio.run(scenario())
