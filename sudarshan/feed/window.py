"""When the feed may hold its socket: NSE trading days, 08:58-15:50 IST by default.

Trading days come from auto-login's calendar (sudarshan/autologin/schedule.py), the one the alerts
use: weekends and ``/etc/sudarshan/nse_holidays.txt`` are closed, a date in
``nse_extra_trading_days.txt`` is open, even one the holiday file lists. Each file is read again,
in one read, when it changes.

NSE's session starts with the 09:00 pre-open; continuous trading runs to 15:30 in the cash market
and to 15:40 in equity derivatives (since 3 Aug 2026). The usual window keeps two margins around
it: it opens 2 minutes before the pre-open, so the socket is authorised, connected and subscribed
when the session's first data comes, and it closes 10 minutes after the F&O close, so the updates
that follow the close still arrive. The stall watchdog runs only in continuous trading,
09:15-15:40, where a quiet socket means a stalled one.

A special session's line gives its own window, from the start of its pre-open to its F&O close::

    2026-11-08 14:05-15:40  # Muhurat, NSE circulars 149/2026 (cash) and 142/2026 (F&O to 15:40)

- On a weekend or a holiday the usual day is moved onto that window, margins and all: the feed
  holds 14:03-15:50 and the watchdog runs 14:20-15:40 (15 minutes in, the pre-open's length).
- On a day that trades anyway, the window is added to the usual hours, never put in their place.
- A reopened weekend or holiday with no usable window keeps the usual hours, and its watchdog
  waits for the day's first trade: the session may come only in the afternoon, and a closed
  market's silence is not a stall.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from pathlib import Path

from sudarshan.autologin.schedule import (  # noqa: F401  parse_holidays: importable from here
    SESSION,
    TradingCalendar,
    parse_holidays,
    union,
    window_text,
    with_margins,
)

log = logging.getLogger("sudarshan.feed")

IST = timezone(timedelta(hours=5, minutes=30), "IST")  # India keeps no daylight saving
CORE_SESSION = (9 * 60 + 15, 15 * 60 + 40)  # continuous trading, F&O included: the stall watchdog
Span = tuple[int, int]  # IST minutes after midnight, [start, end)


def iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(epoch, IST).isoformat(timespec="seconds")


def ist_date(epoch: float) -> date:
    return datetime.fromtimestamp(epoch, IST).date()


def _spans_text(spans: tuple[Span, ...]) -> str:
    return ", ".join(window_text(span) for span in spans)


@dataclass(frozen=True)
class DayPlan:
    """What the feed does on one IST date."""

    closed: str = ""  # "weekend" or "NSE holiday": no socket that day
    spans: tuple[Span, ...] = ()  # when the socket may be held
    cores: tuple[Span, ...] = ()  # when a quiet socket counts as stalled
    first_trade: bool = False  # the stall watchdog waits for the day's first trade
    label: str = ""  # what a listed day adds to the reason


class MarketWindow:
    def __init__(
        self,
        holidays_file: Path,
        window: Span = (538, 950),
        always_on: bool = False,
        extra_days_file: Path | None = None,
    ):
        self.calendar = TradingCalendar(holidays_file, extra_days_file, logger=log, report=True)
        (self.start, self.end), self.always_on = window, always_on

    @property
    def note(self) -> str:
        """The holiday file, for /health: "15 dates", "2 dates, 1 unreadable line", "missing"."""
        return self.calendar.holidays_note()

    @property
    def extra_note(self) -> str:
        """The extra-days file, for /health: "1 date, 1 own window", "missing", ..."""
        return self.calendar.extra_days_note()

    def is_trading_day(self, day: date) -> bool:
        return self.calendar.is_trading_day(day)

    def _moved(self, own: Span) -> tuple[Span, Span]:
        """The usual window and watchdog span moved onto a day's own window (09:00-15:40 there)."""
        core = (own[0] + CORE_SESSION[0] - SESSION[0], own[1] + CORE_SESSION[1] - SESSION[1])
        return with_margins(own, (self.start, self.end)), core

    def plan(self, day: date) -> DayPlan:
        calendar = self.calendar
        if not calendar.is_trading_day(day):
            return DayPlan(closed="weekend" if day.weekday() >= 5 else "NSE holiday")
        usual = DayPlan(spans=((self.start, self.end),), cores=(CORE_SESSION,))
        if day not in calendar.extra_days():
            return usual
        trades_anyway = calendar.trades_anyway(day)
        own = calendar.day_window(day)
        if own is None:
            if trades_anyway:
                return usual
            hours = window_text(usual.spans[0])
            label = f": extra trading day, usual hours {hours}, watchdog after the first trade"
            return replace(usual, first_trade=True, label=label)
        window, core = self._moved(own)
        if trades_anyway:
            spans = union([*usual.spans, window])
            label = f": own window {window_text(own)} added to the usual hours, feed "
            return DayPlan(
                "", spans, union([CORE_SESSION, core]), False, label + _spans_text(spans)
            )
        label = f": extra trading day, own window {window_text(own)}, feed {window_text(window)}"
        return DayPlan("", (window,), union([core]), False, label)

    def status(self, now: float) -> tuple[bool, str]:
        if self.always_on:
            return True, "always on (SUDARSHAN_FEED_ALWAYS_ON=1)"
        t = datetime.fromtimestamp(now, IST)
        plan = self.plan(t.date())
        if plan.closed:
            return False, plan.closed
        minute = t.hour * 60 + t.minute
        if any(start <= minute < end for start, end in plan.spans):
            why = "market window"
        elif minute < plan.spans[0][0]:
            why = "before the market window"
        elif minute >= plan.spans[-1][1]:
            why = "after the market window"
        else:
            why = "between the day's market windows"
        return why == "market window", why + plan.label

    def in_core_session(self, now: float) -> bool:
        t = datetime.fromtimestamp(now, IST)
        minute = t.hour * 60 + t.minute
        return any(start <= minute < end for start, end in self.plan(t.date()).cores)

    def waits_for_first_trade(self, now: float) -> bool:
        """A reopened day at the usual hours: its watchdog runs only once the exchange trades."""
        return self.plan(ist_date(now)).first_trade

    def next_open(self, now: float) -> float:
        today = ist_date(now)
        for offset in range(15):
            day = today + timedelta(days=offset)
            for start, _ in self.plan(day).spans:
                opens = datetime.combine(day, dtime(start // 60, start % 60), IST).timestamp()
                if opens > now:
                    return opens
        return now + 86400.0

    def describe(self, now: float) -> dict:
        """/health's market_window: open or not and why, the feed's hours today, both files."""
        is_open, why = self.status(now)
        plan = self.plan(ist_date(now))
        return {
            "open": is_open,
            "reason": why,
            "hours": None if self.always_on or plan.closed else _spans_text(plan.spans),
            "holidays": self.note,
            "extra_days": self.extra_note,
            "next_open": None if is_open else iso(self.next_open(now)),
        }
