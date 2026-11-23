"""A refused token is asked again (M13.12, v0.17.0 verification V2).

While Upstox refuses the Analytics Token at a holdings or positions read, every read pauses. One
holdings read goes again 15 minutes after the last refusal or try when that falls in market hours
(09:15 to 15:30 on a trading day, the exchange's calendar), else at the next session's start;
never more often, whatever the tab asks meanwhile. Its 200 ends every pause and the token line
says accepted again. The job's checks run on a fake clock against a fake Upstox on loopback.
"""

from __future__ import annotations

import asyncio
from datetime import date

from test_portfolio_orders_trades import ProbeService, ist, make, run
from test_portfolio_orders_trades import fake as fake  # noqa: F401  (fixtures)
from test_portfolio_orders_trades import token_file as token_file  # noqa: F401

from sudarshan.portfolio import probe
from sudarshan.portfolio import service as service_module
from sudarshan.portfolio import upstox as up
from sudarshan.tasks import cancel_and_wait

MIN = 60_000


class Calendar:
    """The exchange's calendar as the service reads it: weekdays trade, but for ``holidays``."""

    def __init__(self, holidays=()):
        self.holidays = set(holidays)

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.holidays

    def day_window(self, day: date):
        return None


class RetryService(ProbeService):
    """What the retry job reads of the service: the reader, the clock, the calendar."""

    def __init__(self, folder, reader, clock, holidays=()):
        super().__init__(folder, reader, clock)
        self.upstox, self.calendar = reader, Calendar(holidays)

    def ensure_calendar(self):
        return self.calendar


def _checks(service, fake, until_ms, *, step_s=30, tab=True, refuse=("holdings",), at=None):
    """The job's check every ``step_s`` seconds up to ``until_ms``, the Upstox tab asking for
    holdings and positions after each when ``tab``; ``at`` maps a time to a change of what Upstox
    answers. The times holdings were asked of Upstox, the first refusal included."""
    reader, clock = service.upstox, service.clock

    async def go():
        asked = []
        try:
            for name in refuse:
                fake.status[name] = 401
            await reader.book(["holdings"], 0)  # refused: every read pauses
            asked += [clock()] * fake.count("holdings")
            while clock() < until_ms:
                clock.advance(step_s)
                for when, change in (at or {}).items():
                    if clock() == when:
                        change()
                before = fake.count("holdings")
                await probe.retry_step(service, clock())
                if tab:
                    await reader.book(["holdings", "positions"])
                asked += [clock()] * (fake.count("holdings") - before)
            return asked
        finally:
            await reader.aclose()

    return run(go())


def _quarters(day, first, last):
    """Every 15 minutes from ``first`` to ``last`` (hours and minutes) on ``day``, both in."""
    start, end = ist(*first, day=day), ist(*last, day=day)
    return list(range(start, end + 1, 15 * MIN))


def test_a_refused_token_is_asked_again_once_every_15_minutes_and_never_more(
    fake, token_file, tmp_path
):
    """Refused at 10:00 on Monday 26 Oct 2026: the job checks every 30 seconds and the tab asks
    every 30 seconds for a whole day. Holdings are asked of Upstox at 10:15, 10:30 ... 15:15, then
    not before Tuesday's 09:15; positions not at all."""
    reader, clock = make(fake, token_file, at=ist(10, 0))
    service = RetryService(tmp_path, reader, clock)
    asked = _checks(service, fake, ist(10, 0, day=27))
    monday, tuesday = _quarters(26, (10, 15), (15, 15)), _quarters(27, (9, 15), (10, 0))
    assert len(monday) == 21 and len(tuesday) == 4
    assert asked == [ist(10, 0), *monday, *tuesday]
    assert fake.count("positions") == 0  # the pause holds them
    assert probe.view(service.token_check, token_file)["state"] == "failed"


def test_the_first_answer_ends_the_pause_and_the_line_says_accepted_again(
    fake, token_file, tmp_path
):
    reader, clock = make(fake, token_file, at=ist(10, 0))
    service = RetryService(tmp_path, reader, clock)
    fake.status["day_trades"] = 401

    def mended():
        fake.status["holdings"] = fake.status["day_trades"] = 200

    async def trades():
        try:
            return (await reader.book(["trades"], 0))["trades"]
        finally:
            await reader.aclose()

    assert run(trades())["note"] == up.WORDS["not_allowed_trades"]  # refused alone at 10:00
    asked = _checks(service, fake, ist(11, 0), tab=False, at={ist(10, 20): mended})
    assert asked == [ist(10, 0), ist(10, 15), ist(10, 30)]  # none after the answer
    assert probe.view(service.token_check, token_file) == {"state": "ok", "at": ist(10, 30) // 1000}
    assert reader.paused_since() is None and reader._paused == {}

    async def tab():
        try:
            return await reader.book(["positions"], 0)
        finally:
            await reader.aclose()

    assert run(tab())["positions"]["state"] == "ok" and fake.count("positions") == 1
    assert run(trades())["state"] == "ok"  # what was refused alone is asked again too


def test_outside_market_hours_one_try_at_the_next_session_start(fake, token_file, tmp_path):
    """Refused at 18:00 on Friday 30 Oct, with Monday 2 Nov a holiday: nothing over the weekend
    or the holiday, one try at Tuesday's 09:15."""
    reader, clock = make(fake, token_file, at=ist(18, 0, day=30))
    service = RetryService(tmp_path, reader, clock, holidays=[date(2026, 11, 2)])
    until = ist(9, 16) + 8 * 24 * 60 * MIN  # Tuesday 3 Nov, 09:16
    asked = _checks(service, fake, until, step_s=60, tab=False)
    assert asked == [ist(18, 0, day=30), ist(9, 15) + 8 * 24 * 60 * MIN]


def test_a_try_upstox_did_not_answer_waits_its_15_minutes_too(fake, token_file, tmp_path):
    """No storm when the try fails some other way (a fault on Upstox's side): every 15 minutes,
    and the pause holds, since only a 200 says the token is accepted."""
    reader, clock = make(fake, token_file, at=ist(10, 0))
    service = RetryService(tmp_path, reader, clock)
    asked = _checks(
        service, fake, ist(11, 0), at={ist(10, 5): lambda: fake.status.update(holdings=503)}
    )
    assert asked == [ist(10, 0), ist(10, 15), ist(10, 30), ist(10, 45), ist(11, 0)]
    assert reader.paused_since() == ist(11, 0)


def test_a_try_is_never_made_while_the_token_is_not_refused(fake, token_file, tmp_path):
    reader, clock = make(fake, token_file, at=ist(10, 0))
    service = RetryService(tmp_path, reader, clock)
    asked = _checks(service, fake, ist(11, 0), tab=False, refuse=())
    assert asked == [ist(10, 0)] and reader.paused_since() is None
    service.upstox = None  # before the reader exists: nothing to ask
    assert run(probe.retry_step(service, ist(12, 0))) is False


def test_the_next_try_follows_the_market_s_hours():
    calendar = Calendar()
    assert probe.next_try(calendar, ist(10, 0)) == ist(10, 15)
    assert probe.next_try(calendar, ist(9, 5)) == ist(9, 20)  # never sooner than 15 minutes
    assert probe.next_try(calendar, ist(8, 0)) == ist(9, 15)
    assert probe.next_try(calendar, ist(15, 14, 59)) == ist(15, 29, 59)
    assert probe.next_try(calendar, ist(15, 15)) == ist(9, 15, day=27)  # 15:30 is closed
    assert probe.next_try(calendar, ist(18, 0, day=30)) == ist(9, 15) + 7 * 24 * 60 * MIN  # Mon


def test_the_job_is_registered_and_ends_with_the_service(monkeypatch, fake, token_file, tmp_path):
    monkeypatch.setattr(service_module, "JOBS", {})
    probe.register_job()
    assert {"probe", "token_retry"} <= set(service_module.JOBS)
    reader, clock = make(fake, token_file, at=ist(10, 0))
    service = RetryService(tmp_path, reader, clock)

    async def go():
        task = asyncio.create_task(service_module.JOBS["token_retry"](service))
        await asyncio.sleep(0.05)  # its first check has run; it waits for the next
        await cancel_and_wait(task)
        await reader.aclose()
        return task

    task = run(go())
    assert task.done() and task.cancelled()
