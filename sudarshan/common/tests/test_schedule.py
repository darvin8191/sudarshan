"""PC1 (Y20): the schedule lives in sudarshan.common and portfolio.timers re-exports it."""

from __future__ import annotations

import asyncio
from datetime import date

from sudarshan.common import schedule
from sudarshan.portfolio import timers


class Calendar:
    """Weekdays trade; a window is the cash session 09:15 to 15:30 IST."""

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5

    def day_window(self, day: date):
        return None


def test_the_portfolio_names_are_the_commons():
    assert timers.Schedule is schedule.Schedule
    assert timers.run_forever is schedule.run_forever
    assert timers.CHECK_EVERY_S == schedule.CHECK_EVERY_S == 30.0
    assert timers.SETTLE_AFTER_START_MIN == schedule.SETTLE_AFTER_START_MIN == 5
    assert timers.FROZEN_AFTER_TRADING_DAYS == schedule.FROZEN_AFTER_TRADING_DAYS == 3


def test_frozen_counts_trading_days_after_the_day():
    plan = schedule.Schedule(Calendar())
    monday = 20261102
    assert not plan.frozen(monday, 20261105)  # Tue, Wed, Thu: three days passed
    assert plan.frozen(monday, 20261106)  # a fourth
    assert plan.frozen(monday, 20261110)
    assert plan.frozen(20260101, 20261110)  # more than 20 calendar days: no walk


def test_run_forever_logs_a_failed_step_and_goes_on():
    calls = []

    class Service:
        clock = staticmethod(lambda: 1)

    async def step(service, now):
        calls.append(now)
        if len(calls) == 1:
            raise RuntimeError("once")
        raise asyncio.CancelledError

    async def go():
        try:
            await schedule.run_forever(Service(), "t", step, every=0)
        except asyncio.CancelledError:
            return "cancelled"

    assert asyncio.run(go()) == "cancelled"
    assert calls == [1, 1]
