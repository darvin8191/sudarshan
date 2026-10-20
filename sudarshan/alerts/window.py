"""When the service is connected: trading days, 08:45-16:00 IST by default.

Trading days come from auto-login's calendar (sudarshan/autologin/schedule.py): weekends and
``/etc/sudarshan/nse_holidays.txt`` are closed, a date in ``nse_extra_trading_days.txt`` (a
Muhurat session) is open; both files are re-read when they change.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from datetime import time as dtime

from sudarshan.autologin.schedule import IST, TradingCalendar, minute_of_day


class AlertWindow:
    def __init__(self, calendar: TradingCalendar, window: tuple[int, int], always_on: bool):
        self.calendar = calendar
        self.start, self.end = window
        self.always_on = always_on

    def status(self, now: datetime) -> tuple[bool, str]:
        if self.always_on:
            return True, "always on (SUDARSHAN_ALERTS_ALWAYS_ON)"
        local = now.astimezone(IST)
        if not self.calendar.is_trading_day(local.date()):
            return False, "not a trading day"
        minute = minute_of_day(local)
        if minute < self.start:
            return False, "before the alert window"
        if minute >= self.end:
            return False, "after the alert window"
        return True, "alert window"

    def next_open(self, now: datetime) -> datetime | None:
        """The next start of the window strictly after ``now``, within two weeks."""
        today = now.astimezone(IST).date()
        start = dtime(self.start // 60, self.start % 60)
        for offset in range(15):
            day = today + timedelta(days=offset)
            opens = datetime.combine(day, start, IST)
            if opens > now and self.calendar.is_trading_day(day):
                return opens
        return None
