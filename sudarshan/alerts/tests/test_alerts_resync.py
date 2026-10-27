"""After a pause the order book is read: what was missed is told once, and the pause too."""

from __future__ import annotations

import asyncio
import gc
import logging
from datetime import datetime, timedelta

import httpx
import pytest
from alerts_fakes import (
    API_KEY,
    OWNER,
    FakeOpenAlgo,
    FakeProxy,
    RunningClock,
    book_row,
    ist,
    trade_row,
    update,
    wait_until,
)

from sudarshan.alerts import resync, service
from sudarshan.alerts.owner import Owner
from sudarshan.alerts.resync import as_update, fills_by_order, read_books
from sudarshan.alerts.service import AlertService

NOTE = " (found after a pause in order alerts)"
UNREAD = "the order book could not be read; open Orders in the app to check."


class Clock:
    def __init__(self, at: datetime):
        self.at = at

    def __call__(self) -> datetime:
        return self.at


@pytest.fixture
def quick_resync(monkeypatch):
    monkeypatch.setattr(service, "RESYNC_RETRY_S", (0.05, 0.05))


async def _start(settings, clock=None):
    svc = AlertService(settings, owner_reader=lambda: Owner(OWNER, API_KEY), clock=clock)
    return svc, asyncio.create_task(svc.run())


async def _stop(svc, task) -> None:
    svc.stop("test")
    await asyncio.wait_for(task, 10)


def _lines(openalgo: FakeOpenAlgo) -> list[str]:
    """Every line sent, without the brand header and the time line."""
    return [line for message in openalgo.messages for line in message.split("\n")[1:-1]]


def test_a_fill_during_a_disconnect_is_told_after_the_reconnect(settings_for, fast, quick_resync):
    async def scenario():
        async with FakeProxy() as proxy, FakeOpenAlgo() as openalgo:
            clock = RunningClock(ist(2026, 10, 21, 10, 0))
            svc, task = await _start(settings_for(proxy, openalgo), clock)
            await wait_until(lambda: proxy.subscribed)
            await wait_until(lambda: openalgo.book_reads() == 1)  # the first subscription too
            await proxy.drop_all()  # OpenAlgo restarts; meanwhile the order fills
            openalgo.orders = [book_row("d1", "complete")]
            openalgo.trades = [trade_row("d1", 40, 123.0), trade_row("d1", 25, 124.0)]
            await wait_until(lambda: openalgo.bodies)
            assert _lines(openalgo) == [
                "Filled: BUY 65 NIFTY 21 OCT 25900 CE at ₹123.38 (MIS, NFO)" + NOTE
            ]
            await _stop(svc, task)

    asyncio.run(scenario())


def test_orders_already_told_are_not_repeated(settings_for, fast, quick_resync):
    async def scenario():
        async with FakeProxy() as proxy, FakeOpenAlgo() as openalgo:
            svc, task = await _start(settings_for(proxy, openalgo))
            await wait_until(lambda: proxy.subscribed)
            await proxy.push(update("t1", "complete", average_price=50.0))
            stop = {"action": "SELL", "pricetype": "SL", "trigger_price": 120}
            await proxy.push(update("t2", "trigger pending", **stop))
            await wait_until(lambda: openalgo.bodies)
            await proxy.drop_all()
            openalgo.orders = [
                book_row("t1", "complete"),
                book_row("t2", "open", **stop),  # the book's word for trigger pending
                book_row("t3", "open"),
            ]
            openalgo.trades = [trade_row("t1", 65, 50.0)]
            await wait_until(lambda: proxy.connections == 2 and openalgo.book_reads() == 2)
            await asyncio.sleep(0.3)
            assert len(openalgo.bodies) == 1 and svc.tracker._waiting == {}
            await _stop(svc, task)

    asyncio.run(scenario())


def test_a_first_start_mid_day_tells_only_what_is_new(settings_for, fast, quick_resync):
    async def scenario():
        async with FakeProxy() as proxy, FakeOpenAlgo() as openalgo:
            settings = settings_for(proxy, openalgo, SUDARSHAN_ALERTS_ALWAYS_ON="0")
            openalgo.orders = [book_row("a1", "complete")]
            openalgo.trades = [trade_row("a1", 65, 10.0)]
            # The very first start, at 10:00: everything since the window opened is new.
            svc, task = await _start(settings, Clock(ist(2026, 10, 21, 10, 0)))
            await wait_until(lambda: openalgo.bodies)
            assert _lines(openalgo) == [
                "Order alerts paused 08:45:00 to 10:00:00 IST; the order book was checked.",
                "Filled: BUY 65 NIFTY 21 OCT 25900 CE at ₹10.00 (MIS, NFO)" + NOTE,
            ]
            await _stop(svc, task)
            # A restart at 11:00: only what the record does not hold yet.
            openalgo.orders.append(book_row("a2", "complete"))
            openalgo.trades.append(trade_row("a2", 65, 20.0))
            svc, task = await _start(settings, Clock(ist(2026, 10, 21, 11, 0)))
            await wait_until(lambda: len(openalgo.bodies) == 2)
            assert _lines(openalgo)[2:] == [
                "Order alerts paused 10:00:00 to 11:00:00 IST; the order book was checked.",
                "Filled: BUY 65 NIFTY 21 OCT 25900 CE at ₹20.00 (MIS, NFO)" + NOTE,
            ]
            await _stop(svc, task)

    asyncio.run(scenario())


def test_a_book_that_cannot_be_read_gives_the_gap_line(settings_for, fast, quick_resync, caplog):
    caplog.set_level(logging.INFO, logger="sudarshan.alerts")

    async def scenario():
        async with FakeProxy() as proxy, FakeOpenAlgo() as openalgo:
            clock = Clock(ist(2026, 10, 21, 10, 0))
            svc, task = await _start(settings_for(proxy, openalgo), clock)
            await wait_until(lambda: openalgo.book_reads() == 1)
            openalgo.book_status = 500
            await proxy.outage(svc)
            clock.at += timedelta(seconds=8)  # a short pause: the journal only
            proxy.refusing = False
            await wait_until(lambda: openalgo.book_reads() == 4)  # three tries
            await asyncio.sleep(0.2)
            assert openalgo.bodies == []
            await proxy.outage(svc)
            clock.at += timedelta(seconds=30)  # a longer one: the owner is told
            proxy.refusing = False
            await wait_until(lambda: openalgo.bodies)
            assert _lines(openalgo) == ["Order alerts paused 10:00:08 to 10:00:38 IST; " + UNREAD]
            await _stop(svc, task)
        assert "The order book could not be read after the pause" in caplog.text

    asyncio.run(scenario())


def test_a_pause_with_nothing_found_is_only_logged(settings_for, fast, quick_resync, caplog):
    caplog.set_level(logging.INFO, logger="sudarshan.alerts")

    async def scenario():
        async with FakeProxy() as proxy, FakeOpenAlgo() as openalgo:
            clock = Clock(ist(2026, 10, 21, 10, 0))
            svc, task = await _start(settings_for(proxy, openalgo), clock)
            await wait_until(lambda: openalgo.book_reads() == 1)
            openalgo.orders = [book_row("n1", "open")]
            await proxy.outage(svc)
            clock.at += timedelta(seconds=40)
            proxy.refusing = False
            await wait_until(lambda: openalgo.book_reads() == 2)
            await asyncio.sleep(0.2)
            assert openalgo.bodies == []
            await _stop(svc, task)
        assert "Order alerts paused 10:00:00 to 10:00:40 IST; the order book was checked. " \
               "Nothing had changed." in caplog.text  # fmt: skip

    asyncio.run(scenario())


def test_an_analyze_mode_book_is_not_the_live_book(settings_for, fast, quick_resync):
    async def scenario():
        async with FakeProxy() as proxy, FakeOpenAlgo() as openalgo:
            clock = Clock(ist(2026, 10, 21, 10, 0))
            svc, task = await _start(settings_for(proxy, openalgo), clock)
            await wait_until(lambda: openalgo.book_reads() == 1)
            openalgo.analyze = True  # the REST books now answer from the sandbox
            openalgo.orders = [book_row("s1", "complete", filled_quantity=65, average_price=7.0)]
            await proxy.outage(svc)
            clock.at += timedelta(seconds=40)
            proxy.refusing = False
            await wait_until(lambda: openalgo.bodies)
            assert _lines(openalgo) == [
                "Order alerts paused 10:00:00 to 10:00:40 IST; OpenAlgo is in Analyze mode, so "
                "the Upstox order book could not be checked; open it in the Upstox app."
            ]
            await _stop(svc, task)

    asyncio.run(scenario())


def test_the_tries_back_off_over_a_few_minutes():
    """OpenAlgo can take a few minutes to answer after a restart: the tries outlast that."""
    waits = service.RESYNC_RETRY_S
    assert list(waits) == sorted(waits) and 120 <= sum(waits) <= 300, waits


@pytest.fixture
def tries(monkeypatch):
    """Seven quick tries: the first read and six more, as in use but in milliseconds."""
    monkeypatch.setattr(service, "RESYNC_RETRY_S", (0.05,) * 6)
    return 1 + 6


def _trading_day(settings_for, tmp_path, proxy, openalgo):
    (tmp_path / "nse_holidays.txt").write_text("")
    return settings_for(proxy, openalgo, SUDARSHAN_ALERTS_ALWAYS_ON="0")


def test_an_install_before_the_pre_open_sends_nothing(settings_for, fast, tries, tmp_path, caplog):
    """27 Oct 2026: an install restarted OpenAlgo at 08:42, the stream came up at 08:45:14 and the
    book answered from 08:46. Nothing trades before the 09:00 pre-open, so nothing is sent."""
    caplog.set_level(logging.INFO, logger="sudarshan.alerts")

    async def scenario():
        async with FakeProxy() as proxy, FakeOpenAlgo() as openalgo:
            clock = Clock(ist(2026, 10, 27, 8, 42))
            proxy.refusing, openalgo.book_status = True, 503  # OpenAlgo is starting
            svc, task = await _start(_trading_day(settings_for, tmp_path, proxy, openalgo), clock)
            await wait_until(lambda: svc.state == "closed")
            clock.at = ist(2026, 10, 27, 8, 45)  # the window opens; the proxy is not up yet
            await asyncio.sleep(0.3)  # refused, again and again
            clock.at = ist(2026, 10, 27, 8, 45, 14)
            proxy.refusing = False
            await wait_until(lambda: openalgo.book_reads() >= 2)  # the book is not up yet either
            clock.at = ist(2026, 10, 27, 8, 46)
            reads, openalgo.book_status = openalgo.book_reads(), 200
            await wait_until(lambda: openalgo.book_reads() > reads)
            await asyncio.sleep(0.3)
            assert openalgo.bodies == []
            await _stop(svc, task)
        assert "Order alerts paused 08:45:00 to 08:45:14 IST; the order book was checked. " \
               "Nothing had changed." in caplog.text  # fmt: skip

    asyncio.run(scenario())


@pytest.mark.parametrize("minute", [(8, 59), (9, 0)])  # by 09:00, not only before it
def test_before_the_pre_open_a_book_that_never_answers_is_only_logged(
    settings_for, fast, tries, tmp_path, caplog, minute
):
    caplog.set_level(logging.INFO, logger="sudarshan.alerts")

    async def scenario():
        async with FakeProxy() as proxy, FakeOpenAlgo() as openalgo:
            openalgo.book_status = 503
            settings = _trading_day(settings_for, tmp_path, proxy, openalgo)
            svc, task = await _start(settings, Clock(ist(2026, 10, 27, *minute)))
            await wait_until(lambda: openalgo.book_reads() == tries)
            await asyncio.sleep(0.3)
            assert openalgo.bodies == []
            await _stop(svc, task)
        end = f"{minute[0]:02d}:{minute[1]:02d}:00"
        assert f"Order alerts paused 08:45:00 to {end} IST; {UNREAD} Not sent: it ended before " \
               "the market's start at 09:00." in caplog.text  # fmt: skip

    asyncio.run(scenario())


def test_a_pause_before_the_pre_open_that_finds_a_fill_tells_it(
    settings_for, fast, tries, tmp_path
):
    async def scenario():
        async with FakeProxy() as proxy, FakeOpenAlgo() as openalgo:
            openalgo.orders = [book_row("e1", "complete", timestamp="27-Oct-2026 08:48:00")]
            openalgo.trades = [trade_row("e1", 65, 10.0)]
            settings = _trading_day(settings_for, tmp_path, proxy, openalgo)
            svc, task = await _start(settings, Clock(ist(2026, 10, 27, 8, 50)))
            await wait_until(lambda: openalgo.bodies)
            assert _lines(openalgo) == [
                "Order alerts paused 08:45:00 to 08:50:00 IST; the order book was checked.",
                "Filled: BUY 65 NIFTY 21 OCT 25900 CE at ₹10.00 (MIS, NFO)" + NOTE,
            ]
            await _stop(svc, task)

    asyncio.run(scenario())


def test_a_pause_in_the_session_is_told_after_every_try_with_what_to_do(
    settings_for, fast, tries, tmp_path, caplog
):
    caplog.set_level(logging.INFO, logger="sudarshan.alerts")

    async def scenario():
        async with FakeProxy() as proxy, FakeOpenAlgo() as openalgo:
            clock = Clock(ist(2026, 10, 27, 10, 0))
            svc, task = await _start(_trading_day(settings_for, tmp_path, proxy, openalgo), clock)
            # The day's first connection: its book read to the end, nothing new (a drop before
            # that would carry its pause, from 08:45, into the next one).
            await wait_until(lambda: "Nothing had changed." in caplog.text)
            openalgo.book_status = 500
            await proxy.outage(svc)
            clock.at = ist(2026, 10, 27, 10, 2)
            proxy.refusing = False
            await wait_until(lambda: openalgo.bodies)
            assert openalgo.book_reads() == 1 + tries  # told only after the last try
            assert _lines(openalgo) == ["Order alerts paused 10:00:00 to 10:02:00 IST; " + UNREAD]
            await _stop(svc, task)

    asyncio.run(scenario())


def test_a_drop_during_the_tries_keeps_the_pause_for_the_next_connection(
    settings_for, fast, monkeypatch
):
    monkeypatch.setattr(service, "RESYNC_RETRY_S", (0.3,) * 3)  # slow enough to drop in between

    async def scenario():
        async with FakeProxy() as proxy, FakeOpenAlgo() as openalgo:
            clock = Clock(ist(2026, 10, 27, 10, 0))
            svc, task = await _start(settings_for(proxy, openalgo), clock)
            await wait_until(lambda: openalgo.book_reads() == 1)
            openalgo.book_status = 500
            await proxy.outage(svc)
            clock.at = ist(2026, 10, 27, 10, 1)  # a real pause, from 10:00:00
            proxy.refusing = False
            await wait_until(lambda: openalgo.book_reads() == 2)  # its first try fails
            await proxy.outage(svc)  # OpenAlgo restarts again before the tries are done
            clock.at = ist(2026, 10, 27, 10, 1, 5)
            proxy.refusing = False
            await wait_until(lambda: openalgo.bodies, timeout=10)
            assert _lines(openalgo) == ["Order alerts paused 10:00:00 to 10:01:05 IST; " + UNREAD]
            await _stop(svc, task)

    asyncio.run(scenario())


def test_part_fills_and_rejections_found_later(settings_for, fast, quick_resync):
    async def scenario():
        async with FakeProxy() as proxy, FakeOpenAlgo() as openalgo:
            clock = RunningClock(ist(2026, 10, 21, 10, 0))
            svc, task = await _start(settings_for(proxy, openalgo), clock)
            await wait_until(lambda: openalgo.book_reads() == 1)
            await proxy.drop_all()
            openalgo.orders = [book_row("p1", "open"), book_row("p2", "rejected", action="SELL")]
            openalgo.trades = [trade_row("p1", 40, 99.0)]
            await wait_until(lambda: openalgo.bodies)
            assert _lines(openalgo) == [
                "Part filled: BUY 40 of 65 NIFTY 21 OCT 25900 CE at ₹99.00 (MIS, NFO)" + NOTE,
                "Rejected: SELL 65 NIFTY 21 OCT 25900 CE (MIS, NFO). "
                "The reason is in the Upstox app's order book." + NOTE,
            ]
            # The stream carries on from there.
            await proxy.push(update("p1", "complete", average_price=99.5))
            await wait_until(lambda: len(openalgo.bodies) == 2)
            assert (
                _lines(openalgo)[2] == "Filled: BUY 65 NIFTY 21 OCT 25900 CE at ₹99.50 (MIS, NFO)"
            )
            await _stop(svc, task)

    asyncio.run(scenario())


def test_a_resync_cut_short_leaves_no_connection_open():
    """A drop cancels the re-sync. httpx cancelled before it has sent its request can keep the
    socket open until garbage collection, so the book reads are left to finish by themselves."""

    async def scenario():
        async with FakeOpenAlgo() as openalgo:
            async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
                for n in range(80):
                    read = asyncio.create_task(read_books(client, openalgo.url, API_KEY))
                    await asyncio.sleep(n % 40 / 10_000)  # cut short at every stage
                    read.cancel()
                    await asyncio.gather(read, return_exceptions=True)

                def settled() -> bool:
                    return not resync._LEFT_RUNNING and openalgo.open_connections == 0

                await wait_until(settled, timeout=3)

    collecting = gc.isenabled()
    gc.disable()  # only the garbage collector would close a socket left open: keep it out
    try:
        asyncio.run(scenario())
    finally:
        if collecting:
            gc.enable()


def test_trades_are_summed_per_order_with_a_weighted_price():
    fills = fills_by_order([
        trade_row("x", 40, 100.0), trade_row("x", 20, 103.0), trade_row("y", 65, 1.0),
        {"orderid": "z", "quantity": "junk"}, "not a row", {"orderid": "", "quantity": 5},
    ])  # fmt: skip
    assert fills == {"x": (60, 101.0), "y": (65, 1.0)}


def test_a_book_row_becomes_a_stream_message():
    message = as_update(book_row("r1", "complete"), {"r1": (65, 12.5)}, sandbox=False)
    assert message["mode"] == "live" and message["filled_quantity"] == 65
    assert message["average_price"] == 12.5 and message["order_status"] == "complete"
    sandbox = as_update(book_row("r2", "complete", filled_quantity=10, average_price=3.0), {},
                        sandbox=True)  # fmt: skip
    assert sandbox["mode"] == "analyze" and sandbox["filled_quantity"] == 10


def test_a_rejection_found_later_carries_the_reason_the_book_names():
    """Upstox's book carries a rejection's message (M6.9); a row without one names none."""
    named = as_update(book_row("r3", "rejected", rejection_reason="RMS:Margin Exceeds"), {},
                      sandbox=False)  # fmt: skip
    assert named["rejection_reason"] == "RMS:Margin Exceeds"
    assert as_update(book_row("r4", "rejected"), {}, sandbox=False)["rejection_reason"] == ""
