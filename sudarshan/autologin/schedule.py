"""Trading days, the morning schedule and market hours, all in IST.

The schedule here is mirrored in ``sudarshan/ops/systemd/sudarshan-autologin.timer``; a test
reads that file and fails if the two disagree. The timer only wakes the worker; the worker decides
from this module whether a wake-up is a real attempt (so a deploy restarting the unit at 16:00, a
weekend or an NSE holiday does nothing).

The two calendar files are read here the way the deploy tool's ``listed()`` reads them, so every
reader on the server agrees on every byte: a line ends at a newline only, a byte-order mark may
start it, words are separated by ASCII spaces, tabs, \\v, \\f and \\r, ``#`` starts a comment, a
date is YYYY-MM-DD in ASCII digits, and a stray byte spoils only its own line. A line of the
extra-days file may give the day's own window after the date, for a special session (NSE's
circulars give the times; the window runs from the start of the pre-open to the F&O close)::

    2026-11-08 14:05-15:40  # Muhurat, NSE circulars 149/2026 (cash) and 142/2026 (F&O to 15:40)
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
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
#: NSE's usual session in IST minutes: the 09:00 pre-open to the 15:40 F&O close. A reader's usual
#: hours keep their own margins around it (the feed 2 and 10 minutes, the alerts 15 and 20), and
#: keep the same margins around a special session's own window (with_margins).
SESSION = (9 * 60, 15 * 60 + 40)


_HHMM = re.compile(r"(\d{1,2}):(\d{2})", re.ASCII)
_ASCII_SPACE = " \t\n\v\f\r"
#: A line's words, split as the deploy tool's grep splits them: ``[[:space:]]`` in the C locale.
_WORD = re.compile(r"[^ \t\v\f\r]+")
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)


def minutes(hhmm: str) -> int:
    """'08:35' -> 515: H:MM or HH:MM in ASCII digits, minutes below 60, 24 only as 24:00."""
    match = _HHMM.fullmatch(hhmm.strip(_ASCII_SPACE))
    if match:
        hours, mins = int(match[1]), int(match[2])
        if mins < 60 and (hours < 24 or (hours, mins) == (24, 0)):
            return hours * 60 + mins
    raise ValueError(f"not a time of day: {hhmm!r}")


def parse_window(text: str) -> tuple[int, int]:
    """'14:05-15:40' -> (845, 940); the start must come before the end, which may be 24:00."""
    start, _, end = text.partition("-")
    window = (minutes(start), minutes(end))
    if window[0] >= window[1]:
        raise ValueError("the window must be HH:MM-HH:MM with the start before the end")
    return window


def window_text(window: tuple[int, int]) -> str:
    """(845, 940) -> '14:05-15:40'."""
    return "-".join(f"{minute // 60:02d}:{minute % 60:02d}" for minute in window)


def with_margins(own: tuple[int, int], usual: tuple[int, int]) -> tuple[int, int]:
    """A special session's own window with the margins ``usual`` keeps around SESSION, clamped
    to the day; the window as it stands when those margins would leave nothing of it."""
    start = max(0, own[0] - (SESSION[0] - usual[0]))
    end = min(24 * 60, own[1] + (usual[1] - SESSION[1]))
    return (start, end) if start < end else own


def union(spans: list[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    """The spans in order, overlapping or touching ones merged, empty ones left out."""
    merged: list[tuple[int, int]] = []
    for start, end in sorted(span for span in spans if span[0] < span[1]):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)


def read_calendar_file(path: Path) -> str:
    """The file's text as the deploy tool's grep sees its lines: bytes, not universal newlines (a
    lone \\r ends no line), a leading byte-order mark dropped, and a stray byte spoiling only its
    own line. Raises OSError."""
    return path.read_bytes().decode("utf-8-sig", errors="replace")


def _words(line: str) -> list[str]:
    """The words of one line before any ``#``, after a byte-order mark that may start it."""
    return _WORD.findall(line.removeprefix("\ufeff").split("#", 1)[0])


def _date(word: str) -> date | None:
    """YYYY-MM-DD in ASCII digits. Python also takes 20261108 and 2026-W45-7 for the same day,
    which the deploy tool would never match, so those are refused here."""
    if _ISO_DATE.fullmatch(word):
        try:
            return date.fromisoformat(word)
        except ValueError:
            pass
    return None


def parse_holidays(text: str) -> tuple[set[date], int]:
    """A date first on each line, ``#`` comments allowed; returns (dates, unreadable line count)."""
    days, bad = set(), 0
    for line in text.split("\n"):
        words = _words(line)
        if words:
            day = _date(words[0])
            if day is None:
                bad += 1
            else:
                days.add(day)
    return days, bad


def day_windows(text: str) -> tuple[dict[date, tuple[int, int]], list[str]]:
    """Each extra day's own window, given after its date, and what was left out.

    ``2026-11-08 14:05-15:40  # Muhurat`` gives {2026-11-08: (845, 940)}. The date is read first:
    a line whose date cannot be read is skipped here (the calendar counts it). A later line for
    the same date replaces an earlier one. A line with the date alone is an extra day at the usual
    hours. Returns the windows and one sentence per problem, worded by what then happens; each
    reader logs them under its own name. Auto-login itself reads only the dates.
    """
    windows: dict[date, tuple[int, int]] = {}
    unusable: list[tuple[date, str]] = []
    repeated: set[date] = set()
    for line in text.split("\n"):
        words = _words(line)
        if len(words) < 2 or (day := _date(words[0])) is None:
            continue
        try:
            window = parse_window(words[1])
        except ValueError:
            unusable.append((day, words[1]))
            continue
        if windows.get(day, window) != window:
            repeated.add(day)
        windows[day] = window
    problems = []
    for day, word in unusable:
        then = (
            f"{window_text(windows[day])} from another line applies"
            if day in windows
            else "the day keeps the usual hours"
        )
        problems.append(
            f"extra trading day {day}: {word!r} is not a window (HH:MM-HH:MM, the start before "
            f"the end), so {then}"
        )
    for day in sorted(repeated):
        problems.append(
            f"extra trading day {day} is given more than one window; the last one, "
            f"{window_text(windows[day])}, applies"
        )
    return windows, problems


def minute_of_day(at: datetime) -> int:
    local = at.astimezone(IST)
    return local.hour * 60 + local.minute


def _count(number: int, noun: str) -> str:
    """_count(1, "date") -> '1 date', _count(2, "date") -> '2 dates'."""
    return f"{number} {noun}{'' if number == 1 else 's'}"


@dataclass(frozen=True)
class _Read:
    """What one read of a calendar file found, published whole (``_DayFile._read``): a reader on
    another thread gets this read or the next one, never one's stamp with another's dates."""

    mtime: int | None = None  # the file's stamp when read; None: not read, missing or unreadable
    days: frozenset[date] = frozenset()
    windows: Mapping[date, tuple[int, int]] = field(default_factory=dict)
    note: str = "not read yet"  # for a status page: "15 dates", "missing", "not readable"


class _DayFile:
    """A calendar file's dates and, for the extra-days file, each day's own window, from one read
    that happens again only when the file changes.

    ``report`` (the feed): one INFO line per read saying what it found, a warning for every window
    that cannot be used, and an INFO line when the optional file is missing.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        optional: bool,
        missing_note: str,
        logger: logging.Logger = log,
        with_windows: bool = False,
        report: bool = False,
    ):
        self.file = Path(path)
        self.optional = optional
        self.missing_note = missing_note
        self.log = logger
        self.with_windows = with_windows
        self.report = report
        self._read = _Read()
        self._told: str | None = None  # the problem last logged; a good read clears it

    @property
    def note(self) -> str:
        """What the last read found, for a status page: "15 dates", "missing", "not readable"."""
        return self._read.note

    def days(self) -> frozenset[date]:
        return self.current().days

    def windows(self) -> Mapping[date, tuple[int, int]]:
        return self.current().windows

    def current(self) -> _Read:
        """The file as it is now: the last read while the file is unchanged, else a new one."""
        last = self._read
        try:
            mtime = self.file.stat().st_mtime_ns
            if mtime == last.mtime:
                return last
            text = read_calendar_file(self.file)
        except FileNotFoundError:
            if self._told != "missing" and (self.report or not self.optional):
                level = logging.INFO if self.optional else logging.WARNING
                self.log.log(level, "%s not found: %s", self.file, self.missing_note)
                self._told = "missing"
            return self._publish(_Read(note="missing"))
        except OSError:
            if self._told != "not readable":
                self.log.warning("%s not readable: %s", self.file, self.missing_note)
                self._told = "not readable"
            return self._publish(_Read(note="not readable"))
        # Everything from the text first, published after in one assignment, the stamp with it:
        # a second thread that sees this stamp also sees these dates (it rereads until then).
        days, bad = parse_holidays(text)
        note = _count(len(days), "date")
        if bad:
            note += f", {_count(bad, 'unreadable line')}"
            self.log.warning("%s: %s ignored", self.file, _count(bad, "unreadable line"))
        windows: dict[date, tuple[int, int]] = {}
        if self.with_windows:
            windows, problems = day_windows(text)
            if windows:
                note += f", {_count(len(windows), 'own window')}"
            if problems:
                note += f", {_count(len(problems), 'window problem')}"
            for problem in problems if self.report else ():
                self.log.warning("%s: %s", self.file, problem)
        read = self._publish(_Read(mtime, frozenset(days), windows, note))
        self._told = None
        if self.report:
            self.log.info("%s read: %s", self.file, self._found(read))
        return read

    def _publish(self, read: _Read) -> _Read:
        self._read = read
        return read

    def _found(self, read: _Read) -> str:
        if not self.with_windows:
            return read.note
        days = [
            f"{day} {window_text(read.windows[day])}" if day in read.windows else f"{day} (usual)"
            for day in sorted(read.days)
        ]
        return f"extra trading days {', '.join(days)}" if days else "no extra trading days"


class TradingCalendar:
    """Weekends and the NSE holiday file decide the trading days; the extra-days file reopens one.

    A missing or unreadable holiday file leaves only weekends closed (and is logged once until a
    good read): an attempt on a holiday is harmless, a skipped trading day is not. The extra-days
    file gives the dates and each day's own window from one read. ``logger`` lets another
    service's journal carry these lines under its own name; ``report`` adds what the feed logs.
    """

    def __init__(
        self,
        holidays_file: Path | str = HOLIDAYS_FILE,
        extra_days_file: Path | str | None = None,
        *,
        logger: logging.Logger = log,
        report: bool = False,
    ):
        self._holidays = _DayFile(
            holidays_file,
            optional=False,
            missing_note="only weekends count as closed",
            logger=logger,
            report=report,
        )
        self._extra = (
            _DayFile(
                extra_days_file,
                optional=True,
                missing_note="no extra trading days",
                logger=logger,
                with_windows=True,
                report=report,
            )
            if extra_days_file
            else None
        )
        self.file = self._holidays.file

    def holidays(self) -> frozenset[date]:
        return self._holidays.days()

    def extra_days(self) -> frozenset[date]:
        return self._extra.days() if self._extra else frozenset()

    def day_window(self, day: date) -> tuple[int, int] | None:
        """The day's own window, when its line in the extra-days file gives a usable one."""
        return self._extra.windows().get(day) if self._extra else None

    def trades_anyway(self, day: date) -> bool:
        """A weekday off the holiday file: it trades whether or not the extra-days file lists it,
        so a window given for it is added to the usual hours, never put in their place."""
        return day.weekday() < 5 and day not in self.holidays()

    def holidays_note(self) -> str:
        """The holiday file as read now, for a status page: "15 dates", "missing", ..."""
        return self._holidays.current().note

    def extra_days_note(self) -> str:
        """The extra-days file as read now: "1 date", "missing", "not readable" or "not set"."""
        if self._extra is None:
            return "not set"
        return self._extra.current().note

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
