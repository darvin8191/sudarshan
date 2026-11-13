"""When the journal's jobs are due, and the once-a-day rule (JB5, M15.8; design 3.5 as amended by
cross-design Y20, Y21 and Y22).

Every time comes from the exchange's calendar through ``sudarshan/common/schedule.py`` and
``sudarshan/alerts/market/sessions.py``, never from a constant such as 08:45: a special day (the
Muhurat session of Sunday 8 November 2026) has its own window and so its own times.

    job        due (IST)                                         runs, if missed, until
    brief      the first session's start - 30 min (08:45; on     the day's last close
               8 Nov 2026 13:50: mutation target 19)
    eod        the last close + 5 min (15:45; 15:50 from         the day's end
               7 Dec 2026): the day report, from the fills
               recorded live and Upstox's positions (Y22)
    check      the cash post-close session's end + 5 min         the day's end
               (16:05): the close check (Y22)
    excursion  17:00: the recording's fallback (Y21)             the day's end
    catchup    22:00, every day: today's trades read once more   the day's end
               whatever the mark, the history of days no check
               marked, Upstox's P&L rows (Y22)
    weekly     Sunday 18:00, any week                            the day's end
    monthly    the 1st at 07:00                                  the day's end
    fy         1 April at 07:00                                  the day's end

A trading job (brief, eod, check, excursion) is never due on a day without a session (mutation
target 20). A job missed while the service was down runs at its next start that day and is marked
late; nothing runs after midnight for the day before (M15.8: never 00:00-03:00). New jobs keep out
of 16:00-16:35 unless they need under a second of CPU (Y20): the close check is one read through
the portfolio and one day paired again.

``job_run`` holds one row per job and day (``once_a_day``): ok, late, or failed, which is asked
again every ``RETRY_MS`` while the job's window lasts.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from sudarshan.alerts.market import sessions
from sudarshan.common.schedule import CHECK_EVERY_S, MS_PER_MIN, Schedule, run_forever

from .core.money import ist_day, to_date, to_day
from .store import Scope

log = logging.getLogger("sudarshan.journal")

OWNER = Scope.owner()
TRADING_JOBS = ("brief", "eod", "check", "excursion")
CALENDAR_JOBS = ("catchup", "weekly", "monthly", "fy")
JOB_NAMES = TRADING_JOBS + CALENDAR_JOBS
EXCURSION_AT_MIN = 17 * 60
CATCHUP_AT_MIN = 22 * 60
FY_AT = (4, 1, 7 * 60)  # 1 April at 07:00
#: A run more than this long after its time is "late".
LATE_AFTER_MS = 5 * MS_PER_MIN
#: A failed run is asked again this long after it, while its window lasts.
RETRY_MS = 15 * MS_PER_MIN


@dataclass(frozen=True)
class Due:
    """One job's run on one day: due at ``at``, missed after ``until`` (epoch ms, not included)."""

    name: str
    day: int
    at: int
    until: int


class JobFailed(Exception):
    """A run that could not do its work (a read refused, a service down): the sentence is kept in
    the job log and the run is asked again later."""


class JournalSchedule:
    """The journal's job times on a day, from the calendar and sessions.py."""

    def __init__(self, calendar: Any, settings: Any):
        self.calendar, self.settings = calendar, settings
        self.base = Schedule(calendar, eod_after_min=settings.eod_after_min)

    def trading(self, day: date) -> bool:
        return self.base.trading(day)

    def _sessions(self, day: date) -> list[sessions.Session]:
        found = sessions.sessions_of(
            day, trading_day=self.trading(day), window=self.calendar.day_window(day)
        )
        return [s for key, s in found.items() if "+" not in key]  # the auction keys repeat them

    def first_start(self, day: date) -> int | None:
        found = self._sessions(day)
        return min(s.start for s in found) if found else None

    def last_close(self, day: date) -> int | None:
        return self.base.close_at(day)

    def brief_at(self, day: date) -> int | None:
        """The first session's start less the lead: 08:45, and 13:50 on 8 Nov 2026."""
        start = self.first_start(day)
        return None if start is None else start - self.settings.brief_before_min * MS_PER_MIN

    def eod_at(self, day: date) -> int | None:
        return self.base.eod_at(day)

    def check_at(self, day: date) -> int | None:
        end = sessions.post_close_end(
            day, trading_day=self.trading(day), window=self.calendar.day_window(day)
        )
        return None if end is None else end + self.settings.check_after_min * MS_PER_MIN

    def previous_trading_days(self, day: date, count: int) -> list[date]:
        """The ``count`` trading days before ``day``, newest first."""
        found, cursor = [], day
        while len(found) < count:
            cursor = self.base.previous_trading_day(cursor)
            if cursor is None:
                break
            found.append(cursor)
        return found

    def due(self, name: str, day: date) -> Due | None:
        """When ``name`` runs on ``day``, or None when it does not run that day."""
        midnight = sessions.epoch_ms(day + timedelta(days=1), 0)
        key = to_day(day)
        if name in TRADING_JOBS:
            if not self.trading(day):
                return None
            if name == "brief":
                at, close = self.brief_at(day), self.last_close(day)
                return None if at is None or close is None else Due(name, key, at, close)
            at = {
                "eod": self.eod_at,
                "check": self.check_at,
                "excursion": lambda d: sessions.epoch_ms(d, EXCURSION_AT_MIN),
            }[name](day)
            return None if at is None else Due(name, key, at, midnight)
        if name == "catchup":
            return Due(name, key, sessions.epoch_ms(day, CATCHUP_AT_MIN), midnight)
        if name == "weekly":
            weekday, minute = self.settings.weekly_at
            if day.weekday() != weekday:
                return None
            return Due(name, key, sessions.epoch_ms(day, minute), midnight)
        if name == "monthly":
            mday, minute = self.settings.monthly_at
            if day.day != mday:
                return None
            return Due(name, key, sessions.epoch_ms(day, minute), midnight)
        if name == "fy":
            month, mday, minute = FY_AT
            if (day.month, day.day) != (month, mday):
                return None
            return Due(name, key, sessions.epoch_ms(day, minute), midnight)
        raise ValueError(f"no such journal job: {name}")


def schedule_for(service: Any) -> JournalSchedule:
    """The service's schedule, on the trading calendar the other services read (built once)."""

    def make(svc: Any) -> JournalSchedule:
        from sudarshan.autologin.schedule import TradingCalendar

        calendar = TradingCalendar(
            svc.settings.holidays_file, svc.settings.extra_days_file, logger=log
        )
        return JournalSchedule(calendar, svc.settings)

    return service.part("schedule", make)


# ---- the once-a-day rule -----------------------------------------------------------------------
Run = Callable[[Any, int, int, bool], Awaitable[str]]


def _read(service: Any, fn: Callable[[Any], Any]) -> Awaitable[Any]:
    return asyncio.get_running_loop().run_in_executor(None, lambda: service.store.read(OWNER, fn))


def _write(service: Any, fn: Callable[[Any], Any]) -> Awaitable[Any]:
    return asyncio.get_running_loop().run_in_executor(None, lambda: service.store.write(OWNER, fn))


async def once_a_day(service: Any, name: str, now: int, run: Run) -> str | None:
    """Run ``name`` for today when it is due, inside its window and not yet done today (a job_run
    row of ok or late stops a second run: vector S6). ``run(service, day, now, late)`` returns the
    line the job log keeps, or raises JobFailed. Returns the status written, or None."""
    if service.store is None:
        return None
    today = ist_day(now)
    due = schedule_for(service).due(name, to_date(today))
    if due is None or not due.at <= now < due.until:
        return None
    row = await _read(service, lambda tx: tx.job_get(name, today))
    if row is not None and (row["status"] != "failed" or now - int(row["at"]) < RETRY_MS):
        return None
    late = now - due.at > LATE_AFTER_MS
    try:
        detail = await run(service, today, now, late)
        status = "late" if late else "ok"
    except JobFailed as exc:
        detail, status = str(exc), "failed"
    except Exception:
        log.exception("the journal's %s job failed", name)
        detail, status = "The job stopped on a fault; it is tried again later.", "failed"
    await _write(service, lambda tx: tx.job_put(name, today, status, detail, now))
    if status == "failed":
        log.warning("job %s for %d failed; asked again in %d min", name, today,
                    RETRY_MS // MS_PER_MIN)  # fmt: skip
    else:
        log.info("job %s for %d %s", name, today, status)
    return status


def register_job(name: str, run: Run, every: float = CHECK_EVERY_S) -> None:
    """One loop for the journal job ``name``: asks the once-a-day rule every ``every`` seconds."""
    from .service import job

    async def step(service: Any, now: int) -> str | None:
        return await once_a_day(service, name, now, run)

    @job(name)
    async def loop(service: Any) -> None:
        await run_forever(service, name, step, every)
