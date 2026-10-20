"""When the feed may hold its socket: NSE trading days, 08:58-15:40 IST by default."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from pathlib import Path

log = logging.getLogger("sudarshan.feed")

IST = timezone(timedelta(hours=5, minutes=30), "IST")  # India keeps no daylight saving
CORE_SESSION = (9 * 60 + 15, 15 * 60 + 30)  # continuous trading, for the data-stall watchdog


def iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, IST).isoformat(timespec="seconds")


def parse_holidays(text: str) -> tuple[set[date], int]:
    """One YYYY-MM-DD per line, ``#`` comments allowed; returns (dates, unreadable line count)."""
    days, bad = set(), 0
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            try:
                days.add(date.fromisoformat(line.split()[0]))
            except ValueError:
                bad += 1
    return days, bad


class MarketWindow:
    def __init__(self, holidays_file: Path, window: tuple[int, int] = (538, 940), always_on=False):
        self.file, (self.start, self.end), self.always_on = holidays_file, window, always_on
        self._mtime: int | None = None
        self._days: set[date] = set()
        self.note = "not read yet"

    def holidays(self) -> set[date]:
        """The holiday set, re-read whenever the file changes (no restart needed)."""
        try:
            mtime = self.file.stat().st_mtime_ns
            if mtime != self._mtime:
                self._days, bad = parse_holidays(self.file.read_text(encoding="utf-8"))
                self._mtime = mtime
                extra = f", {bad} unreadable lines" if bad else ""
                self.note = f"{len(self._days)} dates{extra}"
                log.info("NSE holidays from %s: %s", self.file, self.note)
        except OSError:
            if self.note != "missing":
                log.warning(
                    "holidays file %s not readable: only weekends count as closed", self.file
                )
            self._mtime, self._days, self.note = None, set(), "missing"
        return self._days

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.holidays()

    def status(self, now: float) -> tuple[bool, str]:
        if self.always_on:
            return True, "always on (SUDARSHAN_FEED_ALWAYS_ON=1)"
        t = datetime.fromtimestamp(now, IST)
        if t.weekday() >= 5:
            return False, "weekend"
        if t.date() in self.holidays():
            return False, "NSE holiday"
        minute = t.hour * 60 + t.minute
        if minute < self.start:
            return False, "before the market window"
        if minute >= self.end:
            return False, "after the market window"
        return True, "market window"

    def in_core_session(self, now: float) -> bool:
        t = datetime.fromtimestamp(now, IST)
        minute = t.hour * 60 + t.minute
        return CORE_SESSION[0] <= minute < CORE_SESSION[1] and self.is_trading_day(t.date())

    def next_open(self, now: float) -> float:
        today = datetime.fromtimestamp(now, IST).date()
        for offset in range(15):
            day = today + timedelta(days=offset)
            opens = datetime.combine(day, dtime(self.start // 60, self.start % 60), IST).timestamp()
            if opens > now and self.is_trading_day(day):
                return opens
        return now + 86400.0
