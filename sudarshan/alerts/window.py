"""When the service is connected: trading days, 08:45-16:00 IST by default.

Trading days come from auto-login's calendar (sudarshan/autologin/schedule.py): weekends and
``/etc/sudarshan/nse_holidays.txt`` are closed, a date in ``nse_extra_trading_days.txt`` (a
Muhurat session) is open; both files are read again when they change.

A line of the extra-days file may give that day's own window after the date: the exchange's
session from its pre-open to its F&O close, as its circulars give it (Muhurat 2026, as the
deploy tool's calendar list ships it)::

    2026-11-08 14:05-15:40  # Muhurat, NSE circulars 149/2026 (cash) and 142/2026 (F&O to 15:40)

The alerts keep their usual margins around that session, as around 09:00-15:40 on any day (15
minutes before, 20 after: 13:50-16:00 here), so an update that follows the close, such as the
exchange cancelling an open order, is still told. On a weekday that trades anyway the window is
added to the usual hours. Auto-login reads only the first word of a line and the deploy tool only
the date at its start, so both keep working with the window there (a test holds each to it).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path

from sudarshan.autologin.schedule import (
    IST,
    SESSION,
    TradingCalendar,
    day_windows,
    minute_of_day,
    read_calendar_file,
    union,
    with_margins,
)

log = logging.getLogger("sudarshan.alerts")


def parse_day_windows(text: str) -> dict[date, tuple[int, int]]:
    """{date: (start, end) minutes} for the lines of an extra-days file that give a window."""
    windows, problems = day_windows(text)  # the parser the feed uses too
    for problem in problems:
        log.warning("%s", problem)
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
                self._windows = parse_day_windows(read_calendar_file(self.path))
                self._mtime = mtime
        except OSError:
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

    def spans(self, day: date) -> tuple[tuple[int, int], ...]:
        """When the alerts are connected on a trading day, in IST minutes: the usual window, or a
        special session's own window with the usual margins, added to the usual window on a day
        that trades anyway."""
        own = self._days.get(day)
        if own is None:
            return (self.default,)
        session = with_margins(own, self.default)
        if self.calendar.trades_anyway(day):
            return union([self.default, session])
        return (session,)

    def status(self, now: datetime) -> tuple[bool, str]:
        if self.always_on:
            return True, "always on (SUDARSHAN_ALERTS_ALWAYS_ON)"
        local = now.astimezone(IST)
        if not self.calendar.is_trading_day(local.date()):
            return False, "not a trading day"
        spans = self.spans(local.date())
        minute = minute_of_day(local)
        if any(start <= minute < end for start, end in spans):
            return True, "alert window"
        if minute < spans[0][0]:
            return False, "before the alert window"
        if minute >= spans[-1][1]:
            return False, "after the alert window"
        return False, "between the day's alert windows"

    def opened_at(self, now: datetime) -> datetime | None:
        """When the window first opened that day (None when always on): a pause starts no earlier,
        so the hours between two windows of one day count as a pause."""
        if self.always_on:
            return None
        day = now.astimezone(IST).date()
        start = self.spans(day)[0][0]
        return datetime.combine(day, dtime(start // 60, start % 60), IST)

    def market_opens(self, now: datetime) -> datetime | None:
        """When the exchange's day starts, for the day of ``now`` (None when always on): the 09:00
        pre-open, or a special session's own start (the earlier of the two on a day that trades
        anyway). Nothing trades before it, so a pause that ends by then is told only when the
        order book shows something."""
        if self.always_on:
            return None
        day = now.astimezone(IST).date()
        own = self._days.get(day)
        if own is None:
            start = SESSION[0]
        elif self.calendar.trades_anyway(day):
            start = min(SESSION[0], own[0])
        else:
            start = own[0]
        return datetime.combine(day, dtime(start // 60, start % 60), IST)

    def next_open(self, now: datetime) -> datetime | None:
        """The next start of a window strictly after ``now``, within two weeks."""
        today = now.astimezone(IST).date()
        for offset in range(15):
            day = today + timedelta(days=offset)
            if not self.calendar.is_trading_day(day):
                continue
            for start, _ in self.spans(day):
                opens = datetime.combine(day, dtime(start // 60, start % 60), IST)
                if opens > now:
                    return opens
        return None
