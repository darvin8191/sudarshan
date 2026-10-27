"""Trading days, the morning schedule and market hours, all in IST.

The schedule here is mirrored in ``sudarshan/ops/systemd/sudarshan-autologin.timer``; a test
reads that file and fails if the two disagree. The timer only wakes the worker; the worker decides
from this module whether a wake-up is a real attempt (so a deploy restarting the unit at 16:00, a
weekend or an NSE holiday does nothing).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from pathlib import Path

log = logging.getLogger("sudarshan.autologin")

IST = timezone(timedelta(hours=5, minutes=30), "IST")  # India keeps no daylight saving
#: The timer's three wake-ups on weekdays; the first that succeeds ends the day's work.
SCHEDULE = ("08:35", "08:45", "08:55")
#: Continuous trading, to the F&O close: NSE's equity derivatives trade to 15:40 since
#: 3 Aug 2026 (the cash market closes at 15:30). A new login then would cut off a live session.
MARKET_HOURS = ("09:15", "15:40")
#: From the pre-open to the F&O close: Test now, which replaces today's login, is refused then
#: while the login works.
TEST_BLOCKED = ("09:00", "15:40")
#: Upstox's daily access tokens stop working at 03:30 IST. OpenAlgo's own session rollover is
#: 03:00, so a login between the two is on record as today's while its token is already dead.
UPSTOX_TOKEN_EXPIRY = "03:30"
#: When a scheduled wake-up may log in: from before the first wake-up to the F&O close. A wake-up
#: outside it (a deploy restart in the evening, Persistent= catching up after a night-time boot)
#: is ignored.
DEFAULT_WINDOW = "08:30-15:40"
#: When the owner should have logged in by hand if auto-login could not.
HAND_LOGIN_DEADLINE = "09:15"
HOLIDAYS_FILE = "/etc/sudarshan/nse_holidays.txt"
#: Optional: days the exchange opens although they are a weekend or in the holiday file (a
#: Muhurat session, a special Saturday session). Same format; missing or empty means none.
EXTRA_TRADING_DAYS_FILE = "/etc/sudarshan/nse_extra_trading_days.txt"


def minutes(hhmm: str) -> int:
    """'08:35' -> 515."""
    hours, mins = hhmm.strip().split(":")
    value = int(hours) * 60 + int(mins)
    if not 0 <= value <= 24 * 60:
        raise ValueError(f"not a time of day: {hhmm!r}")
    return value


def parse_window(text: str) -> tuple[int, int]:
    start, _, end = text.partition("-")
    window = (minutes(start), minutes(end))
    if window[0] >= window[1]:
        raise ValueError("the window must be HH:MM-HH:MM with the start before the end")
    return window


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


#: How a reader of the extra-days file reports a line whose window it cannot use.
BAD_WINDOW = "extra trading day %r: the window is not HH:MM-HH:MM; usual hours"


def day_windows(text: str) -> tuple[dict[date, tuple[int, int]], list[str]]:
    """The extra-days lines that give the day's own window after the date, for a special session.

    ``2026-11-08 17:45-19:30  # Muhurat`` gives {2026-11-08: (1065, 1170)}. Returns the windows
    and the lines whose window cannot be read (that day keeps the usual hours; each reader logs
    them). A line with the date alone is an extra day at the usual hours, in neither. Shared by the
    alerts and the feed; auto-login itself reads only the date.
    """
    windows: dict[date, tuple[int, int]] = {}
    bad: list[str] = []
    for raw in text.splitlines():
        words = raw.split("#", 1)[0].split()
        if len(words) < 2:
            continue
        try:
            windows[date.fromisoformat(words[0])] = parse_window(words[1])
        except ValueError:
            bad.append(raw)
    return windows, bad


def minute_of_day(at: datetime) -> int:
    local = at.astimezone(IST)
    return local.hour * 60 + local.minute


class _DayFile:
    """A list of dates in a file, read again only when the file changes."""

    def __init__(
        self, path: Path | str, *, optional: bool, missing_note: str, logger: logging.Logger = log
    ):
        self.file = Path(path)
        self.optional = optional
        self.missing_note = missing_note
        self.log = logger
        self._mtime: int | None = None
        self._days: set[date] = set()
        self._warned = False
        #: What the last read found, for a status page: "15 dates", "missing", "not readable".
        self.note = "not read yet"

    def days(self) -> set[date]:
        try:
            mtime = self.file.stat().st_mtime_ns
            if mtime != self._mtime:
                self._days, bad = parse_holidays(self.file.read_text(encoding="utf-8"))
                self._mtime = mtime
                unreadable = f", {bad} unreadable lines" if bad else ""
                self.note = f"{len(self._days)} dates{unreadable}"
                if bad:
                    self.log.warning("%s: %d unreadable lines ignored", self.file, bad)
        except FileNotFoundError:
            if not (self.optional or self._warned):
                self.log.warning("%s not found: %s", self.file, self.missing_note)
                self._warned = True
            self._mtime, self._days, self.note = None, set(), "missing"
        except (OSError, UnicodeError):
            if not self._warned:
                self.log.warning("%s not readable: %s", self.file, self.missing_note)
                self._warned = True
            self._mtime, self._days, self.note = None, set(), "not readable"
        return self._days


class TradingCalendar:
    """Weekends and the NSE holiday file decide the trading days; the extra-days file reopens one.

    A missing or unreadable holiday file leaves only weekends closed (and is logged once): an
    attempt on a holiday is harmless, a skipped trading day is not. ``logger`` lets another
    service's journal carry these lines under its own name.
    """

    def __init__(
        self,
        holidays_file: Path | str = HOLIDAYS_FILE,
        extra_days_file: Path | str | None = None,
        *,
        logger: logging.Logger = log,
    ):
        self._holidays = _DayFile(
            holidays_file,
            optional=False,
            missing_note="only weekends count as closed",
            logger=logger,
        )
        self._extra = (
            _DayFile(
                extra_days_file, optional=True, missing_note="no extra trading days", logger=logger
            )
            if extra_days_file
            else None
        )
        self.file = self._holidays.file

    def holidays(self) -> set[date]:
        return self._holidays.days()

    def extra_days(self) -> set[date]:
        return self._extra.days() if self._extra else set()

    def holidays_note(self) -> str:
        """The holiday file as read now, for a status page: "15 dates", "missing", ..."""
        self._holidays.days()
        return self._holidays.note

    def extra_days_note(self) -> str:
        """The extra-days file as read now: "1 dates", "missing", "not readable" or "not set"."""
        if self._extra is None:
            return "not set"
        self._extra.days()
        return self._extra.note

    def is_trading_day(self, day: date) -> bool:
        if day in self.extra_days():
            return True
        return day.weekday() < 5 and day not in self.holidays()

    def _within(self, at: datetime, span: tuple[str, str]) -> bool:
        local = at.astimezone(IST)
        start, end = (minutes(t) for t in span)
        return self.is_trading_day(local.date()) and start <= minute_of_day(local) < end

    def in_market_hours(self, at: datetime) -> bool:
        return self._within(at, MARKET_HOURS)

    def blocks_test_now(self, at: datetime) -> bool:
        """09:00-15:40 on a trading day: Test now must not replace a working login then."""
        return self._within(at, TEST_BLOCKED)

    def slots(self, day: date) -> list[datetime]:
        return [
            datetime.combine(day, dtime(minutes(t) // 60, minutes(t) % 60), IST) for t in SCHEDULE
        ]

    def next_slot(self, after: datetime, *, skip_today: bool = False) -> datetime | None:
        """The first scheduled wake-up strictly after ``after`` on a trading day."""
        today = after.astimezone(IST).date()
        for offset in range(1 if skip_today else 0, 15):
            day = today + timedelta(days=offset)
            if not self.is_trading_day(day):
                continue
            for slot in self.slots(day):
                if slot > after:
                    return slot
        return None

    def later_slot_today(self, after: datetime) -> datetime | None:
        """The next wake-up today, if one is left (used for "will try again at ...")."""
        day = after.astimezone(IST).date()
        if not self.is_trading_day(day):
            return None
        return next((slot for slot in self.slots(day) if slot > after), None)


def trading_session_start(at: datetime, rollover: str = "03:00") -> datetime:
    """Start of the trading session ``at`` belongs to (OpenAlgo's SESSION_EXPIRY_TIME boundary)."""
    local = at.astimezone(IST)
    boundary = local.replace(
        hour=minutes(rollover) // 60, minute=minutes(rollover) % 60, second=0, microsecond=0
    )
    return boundary if local >= boundary else boundary - timedelta(days=1)


def hhmm(at: datetime) -> str:
    return at.astimezone(IST).strftime("%H:%M")
