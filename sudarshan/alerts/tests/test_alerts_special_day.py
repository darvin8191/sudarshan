"""Order alerts on a special session's day: the usual margins around its own window, so the
exchange's cancellations after the close are told, and the order book read after any pause.

Sunday 8 Nov 2026 is NSE's Muhurat session (circular 149/2026: pre-open 14:05, equity derivatives
to 15:40). The alerts keep 08:45-16:00's margins around it: 13:50 to 16:00.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest
from alerts_fakes import API_KEY, OWNER, FakeOpenAlgo, FakeProxy, book_row, ist, update, wait_until

from sudarshan.alerts import service
from sudarshan.alerts.owner import Owner
from sudarshan.alerts.service import AlertService

MUHURAT = "2026-11-08 14:05-15:40  # Muhurat, NSE circular 149/2026 (F&O to 15:40)\n"
NOTE = " (found after a pause in order alerts)"
CANCELLED = "Cancelled: BUY 65 NIFTY 21 OCT 25900 CE, none filled (MIS, NFO)"


class Clock:
    def __init__(self, at: datetime):
        self.at = at

    def __call__(self) -> datetime:
        return self.at


@pytest.fixture
def quick_resync(monkeypatch):
    monkeypatch.setattr(service, "RESYNC_RETRY_S", (0.05, 0.05))


@pytest.fixture
def special(settings_for, tmp_path):
    """Settings on the real calendar files: Monday 9 Nov a holiday, ``extra`` the extra days."""

    def make(proxy, openalgo, extra: str = MUHURAT):
        (tmp_path / "nse_holidays.txt").write_text("2026-11-09\n")
        (tmp_path / "nse_extra_trading_days.txt").write_text(extra)
        return settings_for(proxy, openalgo, SUDARSHAN_ALERTS_ALWAYS_ON="0")

    return make


async def _start(settings, clock: Clock) -> tuple[AlertService, asyncio.Task]:
    svc = AlertService(settings, owner_reader=lambda: Owner(OWNER, API_KEY), clock=clock)
    return svc, asyncio.create_task(svc.run())


async def _stop(svc: AlertService, task: asyncio.Task) -> None:
    svc.stop("test")
    await asyncio.wait_for(task, 10)


def _lines(openalgo: FakeOpenAlgo) -> list[str]:
    """Every line sent, without the brand header and the time line."""
    return [line for message in openalgo.messages for line in message.split("\n")[1:-1]]


def test_the_exchange_cancelling_an_order_after_the_close_is_told(special, fast, quick_resync):
    async def scenario():
        async with FakeProxy() as proxy, FakeOpenAlgo() as openalgo:
            clock = Clock(ist(2026, 11, 8, 15, 30))
            svc, task = await _start(special(proxy, openalgo), clock)
            await wait_until(lambda: proxy.subscribed)
            clock.at = ist(2026, 11, 8, 15, 45)  # five minutes after the F&O close
            await proxy.push(update("m1", "cancelled"))
            await wait_until(lambda: openalgo.bodies)
            assert _lines(openalgo) == [CANCELLED] and proxy.close_codes == []
            await _stop(svc, task)

    asyncio.run(scenario())


def test_a_pause_on_the_special_day_is_caught_up_that_day(special, fast, quick_resync):
    async def scenario():
        async with FakeProxy() as proxy, FakeOpenAlgo() as openalgo:
            openalgo.orders = [book_row("m2", "cancelled", timestamp="08-Nov-2026 15:41:02")]
            # The first start that day comes late (OpenAlgo was restarting): the book is read.
            svc, task = await _start(special(proxy, openalgo), Clock(ist(2026, 11, 8, 15, 50)))
            await wait_until(lambda: openalgo.bodies)
            assert _lines(openalgo) == [
                "Order alerts paused 13:50:00 to 15:50:00 IST; the order book was checked.",
                CANCELLED + NOTE,
            ]
            await _stop(svc, task)

    asyncio.run(scenario())


def test_the_next_trading_day_looks_back_to_its_own_opening(special, fast, quick_resync):
    """The catch-up reads that day's order book and counts its pause from that day's opening:
    nothing after the special day's 16:00 is looked for again, so the margins must cover it."""

    async def scenario():
        async with FakeProxy() as proxy, FakeOpenAlgo() as openalgo:
            settings = special(proxy, openalgo)
            clock = Clock(ist(2026, 11, 8, 15, 59, 58))
            svc, task = await _start(settings, clock)
            await wait_until(lambda: proxy.subscribed)
            clock.at = ist(2026, 11, 8, 16, 0, 1)  # the special day's window closes
            await wait_until(lambda: proxy.close_codes == [1000])
            await _stop(svc, task)
            openalgo.orders = [book_row("t1", "cancelled", timestamp="10-Nov-2026 09:20:00")]
            # Tuesday: Monday 9 Nov is a holiday.
            svc, task = await _start(settings, Clock(ist(2026, 11, 10, 10, 0)))
            await wait_until(lambda: openalgo.bodies)
            assert _lines(openalgo) == [
                "Order alerts paused 08:45:00 to 10:00:00 IST; the order book was checked.",
                CANCELLED + NOTE,
            ]
            await _stop(svc, task)

    asyncio.run(scenario())


def test_the_hours_between_two_windows_of_a_day_are_caught_up(special, fast, quick_resync):
    """An evening session on a day that trades anyway: both windows, and the book in between."""

    async def scenario():
        async with FakeProxy() as proxy, FakeOpenAlgo() as openalgo:
            settings = special(proxy, openalgo, "2026-11-10 18:00-19:15  # an evening session\n")
            clock = Clock(ist(2026, 11, 10, 15, 59, 58))  # Tuesday, at the usual close
            svc, task = await _start(settings, clock)
            await wait_until(lambda: proxy.subscribed)
            clock.at = ist(2026, 11, 10, 16, 0, 1)
            await wait_until(lambda: proxy.close_codes == [1000])
            await wait_until(lambda: svc.state == "closed")
            openalgo.orders = [book_row("e1", "cancelled", timestamp="10-Nov-2026 17:05:00")]
            clock.at = ist(2026, 11, 10, 17, 45)  # the evening session's window opens
            await wait_until(lambda: openalgo.bodies)
            assert _lines(openalgo) == [
                "Order alerts paused 16:00:01 to 17:45:00 IST; the order book was checked.",
                CANCELLED + NOTE,
            ]
            clock.at = ist(2026, 11, 10, 19, 34)  # 19 minutes after its close: still connected
            await asyncio.sleep(0.3)
            assert proxy.close_codes == [1000]
            clock.at = ist(2026, 11, 10, 19, 35)
            await wait_until(lambda: proxy.close_codes == [1000, 1000])
            await _stop(svc, task)

    asyncio.run(scenario())
