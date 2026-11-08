"""The daily timers: when each job is due, and the loop that asks (M7.12, M7.24; cross-design R5,
R6, X11, P22).

Every time comes from ``sudarshan/alerts/market/sessions.py`` through the exchange's calendar
(``TradingCalendar`` and its day windows), never from a weekday or a constant such as 15:55: a
special day (Muhurat trading on Sunday 8 November 2026) has its own window, and the same code
gives its own times.

* The closing record is due at the day's last close plus ``eod_after_min`` (15:40 + 15 = 15:55).
* The settle is due at the day's first session start plus 5 minutes (09:15 + 5 = 09:20).
* A job runs once a day: ``job_run`` holds one row per (job, day), written when it ran.

Each job module registers its own loop (P22); ``register_all`` is the one place the entry point
calls.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from typing import Any

from sudarshan.alerts.market import sessions

from .core.days import to_date

log = logging.getLogger("sudarshan.portfolio.timers")

CHECK_EVERY_S = 30.0
SETTLE_AFTER_START_MIN = 5
#: History does not move once a record is more than this many trading days old.
FROZEN_AFTER_TRADING_DAYS = 3
MS_PER_MIN = 60_000


class Schedule:
    """Trading days and the job times of a day, from the calendar and sessions.py."""

    def __init__(self, calendar: Any, *, eod_after_min: int = 15):
        self.calendar, self.eod_after_min = calendar, eod_after_min

    def trading(self, day: date) -> bool:
        return bool(self.calendar.is_trading_day(day))

    def _sessions(self, day: date) -> list[Any]:
        found = sessions.sessions_of(
            day, trading_day=self.trading(day), window=self.calendar.day_window(day)
        )
        return [s for key, s in found.items() if "+" not in key]  # the auction keys repeat them

    def eod_at(self, day: date) -> int | None:
        """The day's last close plus the delay: when the closing record is taken."""
        found = self._sessions(day)
        return max(s.end for s in found) + self.eod_after_min * MS_PER_MIN if found else None

    def close_at(self, day: date) -> int | None:
        """The day's last close (15:30 cash, 15:40 F&O; a special day's own)."""
        found = self._sessions(day)
        return max(s.end for s in found) if found else None

    def settle_at(self, day: date) -> int | None:
        """The day's first session start plus 5 minutes: the official closes are in by then."""
        found = self._sessions(day)
        return min(s.start for s in found) + SETTLE_AFTER_START_MIN * MS_PER_MIN if found else None

    def previous_trading_day(self, day: date) -> date | None:
        return sessions.previous_trading_day(day, self.calendar.is_trading_day)

    def frozen(self, day: int, today: int) -> bool:
        """Whether the record of ``day`` can no longer change: more than 3 trading days have
        passed after it, counting ``today``. (Mutation target 22.)"""
        start, end = to_date(day), to_date(today)
        if (end - start).days > 20:
            return True
        passed, cursor = 0, start
        while cursor < end:
            cursor += timedelta(days=1)
            passed += self.trading(cursor)
        return passed > FROZEN_AFTER_TRADING_DAYS


Step = Callable[[Any, int], Awaitable[Any]]


async def run_forever(service: Any, name: str, step: Step, every: float = CHECK_EVERY_S) -> None:
    """Ask ``step(service, now)`` every ``every`` seconds; its failure is logged and the loop goes
    on (a job that cannot run today may run at the next check)."""
    while True:
        try:
            await step(service, service.clock())
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("the job %s failed this time", name)
        await asyncio.sleep(every)


def register_all() -> None:
    """Register every job of the service, each from its own module. Called once by the entry
    point; a job module imported here adds one line."""
    from . import benchmark, eod, feedlink, reports, resolver

    resolver.register_job()
    feedlink.register_job()
    eod.register_jobs()
    benchmark.register_jobs()
    reports.register_jobs()
    try:
        from . import amfi
    except ImportError:  # pragma: no cover - the NAV job lands with PB7
        return
    amfi.register_jobs()
