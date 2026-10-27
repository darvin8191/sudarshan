"""Trading sessions per segment: the one table every part of market alerts reads (M10).

Continuous trading in IST on a trading day, the end not included:

    NSE_EQ, BSE_EQ, NSE_INDEX, BSE_INDEX   09:15-15:30
    NSE_FO, BSE_FO                         09:15-15:40 (equity derivatives close at 15:40 since
                                                        3 Aug 2026)

It decides when a value is inside the session (the gate), where the last candle of a day ends,
what "today" means for a % move or a day high, when a contract's alerts stop on its expiry day
and what "Market closed" says.

A special day (a Muhurat session) gives its own window in the extra-days file, as the exchange
announces the whole day: from the start of its pre-open to the F&O close, for example
``2026-11-08 14:05-15:40``. That day each segment starts with normal trading, 15 minutes after the
window opens, and ends as long before the window's end as it usually ends before the F&O close:
cash and indices 10 minutes before it, F&O at it. So 8 Nov 2026 trades 14:20-15:30 in cash and
14:20-15:40 in F&O.

Pure: whether a day trades and its own window are arguments. The runtime reads both through
sudarshan.autologin.schedule (``TradingCalendar.is_trading_day`` and ``day_windows``); nothing
here opens a file or reads the clock.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sudarshan.autologin.schedule import IST

#: Continuous trading per segment, in IST minutes after midnight: (start, end).
SEGMENTS: dict[str, tuple[int, int]] = {
    "NSE_EQ": (555, 930),
    "BSE_EQ": (555, 930),
    "NSE_INDEX": (555, 930),
    "BSE_INDEX": (555, 930),
    "NSE_FO": (555, 940),
    "BSE_FO": (555, 940),
}
INDEX_SEGMENTS = frozenset({"NSE_INDEX", "BSE_INDEX"})
FO_SEGMENTS = frozenset({"NSE_FO", "BSE_FO"})
#: A special day's window starts with its pre-open; normal trading starts this much later.
PRE_OPEN_MIN = 15
#: The latest usual close (F&O, 15:40): a special day's window ends there.
LAST_CLOSE = max(end for _, end in SEGMENTS.values())


@dataclass(frozen=True, slots=True)
class Session:
    segment: str
    #: The trading day as YYYYMMDD (20261108), the key every per-session record uses.
    key: int
    #: Epoch milliseconds; ``end`` is not part of the session.
    start: int
    end: int


def segment_of(instrument_key: str) -> str:
    """'NSE_EQ|INE002A01018' -> 'NSE_EQ'."""
    return instrument_key.partition("|")[0]


def day_key(day: date) -> int:
    return day.year * 10000 + day.month * 100 + day.day


def key_day(key: int) -> date:
    return date(key // 10000, key // 100 % 100, key % 100)


def epoch_ms(day: date, minute: int) -> int:
    """``minute`` minutes after midnight IST on ``day``, in epoch milliseconds."""
    midnight = datetime(day.year, day.month, day.day, tzinfo=IST)
    return int((midnight + timedelta(minutes=minute)).timestamp()) * 1000


def hours(segment: str, window: tuple[int, int] | None = None) -> tuple[int, int] | None:
    """The segment's session in IST minutes: its usual hours, or its share of a special window.

    None for a segment market alerts do not cover, and for a window too short to hold a session.
    """
    usual = SEGMENTS.get(segment)
    if usual is None or window is None:
        return usual
    opens, closes = window
    start = opens + PRE_OPEN_MIN
    end = min(closes, closes - (LAST_CLOSE - usual[1]))  # never past the window's end
    return (start, end) if start < end else None


def session(
    segment: str, day: date, *, trading_day: bool, window: tuple[int, int] | None = None
) -> Session | None:
    """The segment's session on ``day``, or None when it does not trade that day."""
    span = hours(segment, window) if trading_day else None
    if span is None:
        return None
    return Session(segment, day_key(day), epoch_ms(day, span[0]), epoch_ms(day, span[1]))


def previous_trading_day(
    day: date, is_trading_day: Callable[[date], bool], horizon: int = 15
) -> date | None:
    """The trading day before ``day`` (the session an opening gap compares with)."""
    for back in range(1, horizon + 1):
        earlier = day - timedelta(days=back)
        if is_trading_day(earlier):
            return earlier
    return None


def next_session(
    segment: str,
    after_ms: int,
    *,
    is_trading_day: Callable[[date], bool],
    window_of: Callable[[date], tuple[int, int] | None],
    horizon: int = 15,
) -> Session | None:
    """The segment's first session starting after ``after_ms``: "checks from Mon 09:15"."""
    first = datetime.fromtimestamp(after_ms / 1000, IST).date()
    for offset in range(horizon + 1):
        day = first + timedelta(days=offset)
        found = session(segment, day, trading_day=is_trading_day(day), window=window_of(day))
        if found is not None and found.start > after_ms:
            return found
    return None
