"""When the service is connected: trading days, 08:45-16:00 IST by default.

Trading days come from auto-login's calendar (sudarshan/autologin/schedule.py): weekends and
``/etc/sudarshan/nse_holidays.txt`` are closed, a date in ``nse_extra_trading_days.txt`` (a
Muhurat session) is open; both files are read again when they change.

A line of the extra-days file may give that day's own window after the date, for a session
outside the usual hours::

    2026-11-08 17:45-19:30  # Muhurat

Auto-login reads only the first word of a line and the deploy tool only the date at its start,
so both keep working with the window there (a test holds each to it).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path

from sudarshan.autologin.schedule import IST, TradingCalendar, minute_of_day, parse_window

log = logging.getLogger("sudarshan.alerts")


def parse_day_windows(text: str) -> dict[date, tuple[int, int]]:
    """{date: (start, end) minutes} for the lines of an extra-days file that give a window."""
    windows: dict[date, tuple[int, int]] = {}
    for raw in text.splitlines():
        words = raw.split("#", 1)[0].split()
        if len(words) < 2:
            continue
        try:
            windows[date.fromisoformat(words[0])] = parse_window(words[1])
        except ValueError:
            log.warning("extra trading day %r: the window is not HH:MM-HH:MM; usual hours", raw)
    return windows


class _DayWindows:
    """The extra-days file's own windows, read again when the file changes."""

    def __init__(self, path: Path | None):
        self.path = path
        self._mtime: int | None = None
        self._windows: dict[date, tuple[int, int]] = {}

    def get(self, day: date) -> tuple[int, int] | None:
        if self.path is None:
            return None
        try:
            mtime = self.path.stat().st_mtime_ns
            if mtime != self._mtime:
                self._windows = parse_day_windows(self.path.read_text(encoding="utf-8"))
                self._mtime = mtime
        except (OSError, UnicodeError):
            self._mtime, self._windows = None, {}
        return self._windows.get(day)


class AlertWindow:
    def __init__(
        self,
        calendar: TradingCalendar,
        window: tuple[int, int],
        always_on: bool,
        extra_days_file: Path | None = None,
    ):
        self.calendar = calendar
        self.default = window
        self.always_on = always_on
        self._days = _DayWindows(extra_days_file)

    def span(self, day: date) -> tuple[int, int]:
        """The day's window in IST minutes: its own from the extra-days file, else the usual."""
        return self._days.get(day) or self.default

    def status(self, now: datetime) -> tuple[bool, str]:
        if self.always_on:
            return True, "always on (SUDARSHAN_ALERTS_ALWAYS_ON)"
        local = now.astimezone(IST)
        if not self.calendar.is_trading_day(local.date()):
            return False, "not a trading day"
        start, end = self.span(local.date())
        minute = minute_of_day(local)
        if minute < start:
            return False, "before the alert window"
        if minute >= end:
            return False, "after the alert window"
        return True, "alert window"

    def opened_at(self, now: datetime) -> datetime | None:
        """When today's window opened (None when always on): a pause starts no earlier."""
        if self.always_on:
            return None
        day = now.astimezone(IST).date()
        start = self.span(day)[0]
        return datetime.combine(day, dtime(start // 60, start % 60), IST)

    def next_open(self, now: datetime) -> datetime | None:
        """The next start of the window strictly after ``now``, within two weeks."""
        today = now.astimezone(IST).date()
        for offset in range(15):
            day = today + timedelta(days=offset)
            start = self.span(day)[0]
            opens = datetime.combine(day, dtime(start // 60, start % 60), IST)
            if opens > now and self.calendar.is_trading_day(day):
                return opens
        return None
