"""The market's hours as the family's channels read them (M10.9; design 2.6, 3.1).

``MarketHours`` gives a day's market session from the exchange calendar the family keeps (Y1) and
``sessions.py``: from the first session's start to the last close (09:15 to 15:40; 15:45 from 7
Dec 2026; a special day its own window), with no pre-open, and none on a holiday. The bot's
``/status`` says from it whether the market is open and which day trades next.

Pure: the calendar answers ``is_trading_day`` and ``day_window``; every instant is epoch ms.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from sudarshan.alerts.market import sessions
from sudarshan.autologin.schedule import IST

#: How far ahead a next session is looked for (a long holiday run is under two weeks).
HORIZON_DAYS = 15


def day_of(at_ms: int) -> date:
    """The day in India an instant falls on."""
    return datetime.fromtimestamp(at_ms / 1000, UTC).astimezone(IST).date()


def at_minute(day: date, minute: int) -> int:
    """``minute`` minutes after midnight IST on ``day``, in epoch ms."""
    return sessions.epoch_ms(day, minute)


class MarketHours:
    """The market session of each day, from the calendar (see the module's words)."""

    def __init__(self, calendar: Any):
        self.calendar = calendar

    def trading(self, day: date) -> bool:
        return bool(self.calendar.is_trading_day(day))

    def span(self, day: date) -> tuple[int, int] | None:
        """The day's market session in epoch ms (start included, end not); None: no session."""
        found = sessions.sessions_of(day, trading_day=self.trading(day),
                                     window=self.calendar.day_window(day))  # fmt: skip
        spans = [s for key, s in found.items() if sessions.AUCTION not in key]
        if not spans:
            return None
        return min(s.start for s in spans), max(s.end for s in spans)

    def is_open(self, now: int) -> bool:
        span = self.span(day_of(now))
        return span is not None and span[0] <= now < span[1]

    def next_open(self, now: int) -> int | None:
        """The start of the first session that starts after ``now``."""
        day = day_of(now)
        for offset in range(HORIZON_DAYS + 1):
            span = self.span(day + timedelta(days=offset))
            if span is not None and span[0] > now:
                return span[0]
        return None

    def next_day(self, day: date) -> date | None:
        """The first trading day after ``day``."""
        for offset in range(1, HORIZON_DAYS + 1):
            if self.span(day + timedelta(days=offset)) is not None:
                return day + timedelta(days=offset)
        return None
