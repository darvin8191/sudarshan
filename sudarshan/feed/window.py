"""When the feed may hold its socket: NSE trading days, 08:58-15:40 IST by default.

Trading days come from auto-login's calendar (sudarshan/autologin/schedule.py), the one the alerts
use: weekends and ``/etc/sudarshan/nse_holidays.txt`` are closed, a date in
``nse_extra_trading_days.txt`` is open, even one the holiday file lists. Both files are read again
when they change.

The usual window keeps two margins around NSE's session (the pre-open from 09:00, continuous
trading 09:15-15:30). It opens 2 minutes before the pre-open, so the socket is authorised,
connected and subscribed when the session's first data comes, and it closes 10 minutes after the
close, so the updates that follow the close still arrive. A line of the extra-days file may give a
special session's own window after the date, from the start of its pre-open to its close::

    2026-11-08 17:45-19:30  # Muhurat

That day the usual window is moved onto it, margins and all: the feed holds 17:43-19:40, and the
stall watchdog runs from 15 minutes after the start (the pre-open's length) to the close,
18:00-19:30. A line whose window cannot be read keeps the usual hours, with a warning.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from pathlib import Path

from sudarshan.autologin.schedule import BAD_WINDOW, TradingCalendar, day_windows

log = logging.getLogger("sudarshan.feed")

IST = timezone(timedelta(hours=5, minutes=30), "IST")  # India keeps no daylight saving
SESSION = (9 * 60, 15 * 60 + 30)  # NSE's usual session, from the 09:00 pre-open to the close
CORE_SESSION = (9 * 60 + 15, 15 * 60 + 30)  # continuous trading, for the data-stall watchdog
DAY_END = 24 * 60


def iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, IST).isoformat(timespec="seconds")


def span(window: tuple[int, int]) -> str:
    """(1063, 1180) -> '17:43-19:40'."""
    return "-".join(f"{minute // 60:02d}:{minute % 60:02d}" for minute in window)


class _OwnWindows:
    """The extra-days file's own windows, read again when the file changes (as the alerts do)."""

    def __init__(self, path: Path | None):
        self.path = path
        self._mtime: int | None = None
        self._said_missing = False
        self.windows: dict[date, tuple[int, int]] = {}
        self.bad = 0  # lines whose window cannot be read

    def get(self, day: date) -> tuple[int, int] | None:
        self.refresh()
        return self.windows.get(day)

    def refresh(self) -> None:
        if self.path is None:
            return
        try:
            mtime = self.path.stat().st_mtime_ns
            if mtime != self._mtime:
                self.windows, bad = day_windows(self.path.read_text(encoding="utf-8"))
                self._mtime, self.bad, self._said_missing = mtime, len(bad), False
                for raw in bad:
                    log.warning(BAD_WINDOW, raw)
        except FileNotFoundError:  # as shipped; the calendar keeps quiet about it, so say it once
            if not self._said_missing:
                log.info("%s not found: no extra trading days", self.path)
                self._said_missing = True
            self._mtime, self.windows, self.bad = None, {}, 0
        except (OSError, UnicodeError):  # the calendar logs this once
            self._mtime, self.windows, self.bad = None, {}, 0


class MarketWindow:
    def __init__(
        self,
        holidays_file: Path,
        window: tuple[int, int] = (538, 940),
        always_on: bool = False,
        extra_days_file: Path | None = None,
    ):
        self.calendar = TradingCalendar(holidays_file, extra_days_file, logger=log)
        self._own = _OwnWindows(extra_days_file)
        (self.start, self.end), self.always_on = window, always_on

    @property
    def note(self) -> str:
        """The holiday file, for /health: "15 dates", "2 dates, 1 unreadable lines", "missing"."""
        return self.calendar.holidays_note()

    @property
    def extra_note(self) -> str:
        """The extra-days file, for /health: "1 dates, 1 own windows", "missing", ..."""
        note = self.calendar.extra_days_note()
        self._own.refresh()
        if self._own.windows:
            note += f", {len(self._own.windows)} own windows"
        if self._own.bad:
            note += f", {self._own.bad} unreadable windows"
        return note

    def is_trading_day(self, day: date) -> bool:
        return self.calendar.is_trading_day(day)

    def hours(self, day: date) -> tuple[int, int]:
        """The feed's window that day in IST minutes: the usual one, or moved onto the day's own."""
        own = self._own.get(day)
        if own is None:
            return self.start, self.end
        start = max(0, own[0] - (SESSION[0] - self.start))
        end = min(DAY_END, own[1] + (self.end - SESSION[1]))
        return (start, end) if start < end else own  # a usual window too narrow to move

    def _core(self, day: date) -> tuple[int, int]:
        own = self._own.get(day)
        if own is None:
            return CORE_SESSION
        return own[0] + CORE_SESSION[0] - SESSION[0], own[1] + CORE_SESSION[1] - SESSION[1]

    def _extra(self, day: date, hours: tuple[int, int]) -> str:
        """What a listed extra trading day adds to the reason; nothing on any other day."""
        if day not in self.calendar.extra_days():
            return ""
        own = self._own.get(day)
        if own is None:
            return f": extra trading day, usual hours {span(hours)}"
        return f": extra trading day, own window {span(own)}, feed {span(hours)}"

    def status(self, now: float) -> tuple[bool, str]:
        if self.always_on:
            return True, "always on (SUDARSHAN_FEED_ALWAYS_ON=1)"
        t = datetime.fromtimestamp(now, IST)
        day = t.date()
        if not self.calendar.is_trading_day(day):
            return False, "weekend" if day.weekday() >= 5 else "NSE holiday"
        start, end = self.hours(day)
        minute = t.hour * 60 + t.minute
        if minute < start:
            why = "before the market window"
        elif minute >= end:
            why = "after the market window"
        else:
            why = "market window"
        return start <= minute < end, why + self._extra(day, (start, end))

    def in_core_session(self, now: float) -> bool:
        t = datetime.fromtimestamp(now, IST)
        start, end = self._core(t.date())
        return start <= t.hour * 60 + t.minute < end and self.calendar.is_trading_day(t.date())

    def next_open(self, now: float) -> float:
        today = datetime.fromtimestamp(now, IST).date()
        for offset in range(15):
            day = today + timedelta(days=offset)
            if not self.calendar.is_trading_day(day):
                continue
            start = self.hours(day)[0]
            opens = datetime.combine(day, dtime(start // 60, start % 60), IST).timestamp()
            if opens > now:
                return opens
        return now + 86400.0

    def describe(self, now: float) -> dict:
        """/health's market_window: open or not and why, the feed's hours today, both files."""
        is_open, why = self.status(now)
        day = datetime.fromtimestamp(now, IST).date()
        trading = not self.always_on and self.calendar.is_trading_day(day)
        return {
            "open": is_open,
            "reason": why,
            "hours": span(self.hours(day)) if trading else None,
            "holidays": self.note,
            "extra_days": self.extra_note,
            "next_open": None if is_open else iso(self.next_open(now)),
        }
