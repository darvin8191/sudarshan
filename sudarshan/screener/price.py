"""What a candle and a session say about price: levels, ranges and ratios, computed exactly.

A price is a paise integer and stays one. A ratio is a ``Fraction`` in the unit the owner types it
in (2% is ``Fraction(2)``), so ``change_pct >= 2`` is decided without a float. A thing that cannot
be read is a ``Why`` (``types``), never a guess: the engine turns it into UNKNOWN, and a root that
is UNKNOWN is "Not checked", not a miss.

The functions take what they read as arguments (a ``Series``, a boundary time, a session key) and
return a value or a ``Why``. Pure: no I/O, no clock, no logging. Definitions:

- ``change_pct`` and ``gap_pct``: against the previous close of the candle's own session. For
  today that is the feed's frozen ``cp``; for an earlier session it is the close of the day candle
  before it. A candle's session is the one it starts in, so a weekly candle is measured against
  the close of the day before its week began.
- ``turnover``: the candle's close times its volume, in rupees (not the sum of its trades).
- ``prev_day_*``: the day candle of the session before today's, whatever the calendar between them
  (after the Muhurat Sunday it is the Muhurat candle). ``day_*``: today's day candle so far.
- ``year_high`` and ``year_low``: the 08:50 snapshot, widened by today's high and low.
- ``pivot``: the levels of the session from the previous day candle, in exact fractions of a
  paisa. Classic: P = (H + L + C) / 3, R1 = 2P - L, S1 = 2P - H, R2 = P + (H - L),
  S2 = P - (H - L), R3 = H + 2(P - L), S3 = L - 2(H - P). CPR: BC = (H + L) / 2, TC = 2P - BC. A
  day that closed below its midpoint puts TC below BC; the two names are not swapped.
- ``orb``: the high or low of the session's first N minutes (N a multiple of 5 and of the candle
  size). Not known until the range has ended: a clause that reads it earlier is "Not checked".
- ``highest`` and ``lowest``: the extreme of a source over the last n candles, the candle itself
  included, as Pine's ta.highest does.
- ``relative_strength``: how far the instrument's n-candle return is ahead of the index's, in
  percent: ((close / close n ago) / (index close / index close n ago) - 1) x 100. The index
  candle is the one that starts when the instrument's does, so a thin stock with a missing minute
  is still measured against the same minutes of the index.
- ``unverified_in``: PAUSED when a candle a clause reads was built while the price feed paused and
  has not been repaired. The engine passes the span each operand reads (``orb_span`` gives the
  opening range's).
"""

from __future__ import annotations

from collections.abc import Callable
from fractions import Fraction

import numpy as np

from sudarshan.screener.rolling import rolling_max, rolling_min
from sudarshan.screener.types import (
    NO_CANDLE,
    NO_DATA,
    PAUSED,
    UNVERIFIED,
    WARMING,
    WINDOW_OPEN,
    ZERO,
    Series,
    Why,
)

#: The columns a source can name.
COLUMNS = {"open": "o", "high": "h", "low": "l", "close": "c", "volume": "v"}
MINUTE_MS = 60_000

Value = int | Fraction | Why


# ---- finding candles by session ---------------------------------------------------------------
def previous_index(sessions: np.ndarray, session: int) -> int | None:
    """The last candle that belongs to a session before ``session`` (candles are oldest first)."""
    at = int(np.searchsorted(sessions, session, side="left")) - 1
    return at if at >= 0 else None


def today_index(days: Series | None, session: int) -> int | None:
    """Today's day candle: the last one, when it belongs to ``session``."""
    if days is None or not len(days) or int(days.session[-1]) != session:
        return None
    return len(days) - 1


def reference_close(days: Series | None, session: int, today: int, cp: int | None) -> int | None:
    """The previous close the candles of ``session`` are measured against."""
    if session == today and cp:
        return cp
    if days is None:
        return None
    at = previous_index(days.session, session)
    return int(days.c[at]) if at is not None else None


# ---- one candle -------------------------------------------------------------------------------
def candle_field(series: Series, index: int, name: str) -> Value:
    """Open, high, low, close, volume, open interest, VWAP or turnover of one candle."""
    if name in COLUMNS:
        return int(series.column(COLUMNS[name])[index])
    if name == "oi":
        oi = int(series.oi[index])
        return oi if oi >= 0 else Why(NO_DATA, ("oi",))
    if name == "vwap":
        atp = int(series.atp[index])
        return atp if atp > 0 else Why(NO_DATA, ("vwap",))
    if name == "turnover":
        return turnover(int(series.c[index]), int(series.v[index]))
    raise KeyError(name)


def turnover(close: int, volume: int) -> Fraction:
    """Close times volume, in rupees (the close is in paise)."""
    return Fraction(close * volume, 100)


def percent_from(value: int, reference: int | None, name: str = "prev_close") -> Fraction | Why:
    """How far ``value`` is from ``reference``, in percent of ``reference``."""
    if reference is None:
        return Why(NO_DATA, (name,))
    if reference == 0:
        return Why(ZERO)
    return Fraction((value - reference) * 100, reference)


def change_pct(
    series: Series, index: int, days: Series | None, today: int, cp: int | None
) -> Value:
    """The candle's close against the previous close of its own session."""
    reference = reference_close(days, int(series.session[index]), today, cp)
    return percent_from(int(series.c[index]), reference)


# ---- the session ------------------------------------------------------------------------------
SESSION_NAMES = (
    "prev_close", "day_open", "day_high", "day_low", "prev_day_high", "prev_day_low",
    "prev_day_close", "year_high", "year_low", "gap_pct",
)  # fmt: skip


def session_level(
    name: str,
    days: Series | None,
    session: int,
    cp: int | None = None,
    year: tuple[int, int] | None = None,
) -> Value:
    """A level of the session ``session`` (today's, at a run) from its day candles.

    ``cp`` is the feed's frozen previous close; ``year`` the 08:50 snapshot's (high, low).
    """
    if name == "prev_close":
        reference = reference_close(days, session, session, cp)
        return reference if reference is not None else Why(NO_DATA, ("prev_close",))
    if name in ("day_open", "day_high", "day_low", "gap_pct"):
        at = today_index(days, session)
        if at is None:
            return Why(NO_CANDLE)
        if name == "gap_pct":
            return percent_from(int(days.o[at]), reference_close(days, session, session, cp))
        return int(days.column({"day_open": "o", "day_high": "h", "day_low": "l"}[name])[at])
    if name in ("prev_day_high", "prev_day_low", "prev_day_close"):
        at = previous_index(days.session, session) if days is not None else None
        if at is None:
            return Why(NO_CANDLE)
        column = {"prev_day_high": "h", "prev_day_low": "l", "prev_day_close": "c"}[name]
        return int(days.column(column)[at])
    if name in ("year_high", "year_low"):
        if year is None:
            return Why(NO_DATA, (name,))
        high = name == "year_high"
        base = year[0] if high else year[1]
        at = today_index(days, session)
        if at is None:
            return base
        today = int(days.h[at]) if high else int(days.l[at])
        return max(base, today) if high else min(base, today)
    raise KeyError(name)


def pivot_levels(kind: str, high: int, low: int, close: int) -> dict[str, Fraction]:
    """The pivot levels of the next session from a day's high, low and close, in paise, exact."""
    p = Fraction(high + low + close, 3)
    if kind == "cpr":
        bc = Fraction(high + low, 2)
        return {"P": p, "TC": 2 * p - bc, "BC": bc}
    return {
        "P": p, "R1": 2 * p - low, "S1": 2 * p - high,
        "R2": p + (high - low), "S2": p - (high - low),
        "R3": high + 2 * (p - low), "S3": low - 2 * (high - p),
    }  # fmt: skip


def pivot(kind: str, out: str, days: Series | None, session: int) -> Fraction | Why:
    """One pivot level of ``session``, from the day candle before it."""
    at = previous_index(days.session, session) if days is not None else None
    if at is None:
        return Why(NO_CANDLE)
    levels = pivot_levels(kind, int(days.h[at]), int(days.l[at]), int(days.c[at]))
    return levels[out]


# ---- ranges -----------------------------------------------------------------------------------
def orb_span(
    series: Series, index: int, minutes: int, opens: Callable[[int], int | None], now: int
) -> tuple[int, int] | Why:
    """The candles that make the opening range of candle ``index``'s session: (first, last + 1).

    ``opens`` maps a session key to its start (epoch ms), ``now`` is the boundary being judged. The
    range is not known until it has ended, for the candle read and for ``now``: reading it earlier
    would hand the candle a high or a low that had not happened yet.
    """
    start = opens(int(series.session[index]))
    if start is None:
        return Why(NO_DATA, ("opening range",))
    stop = start + minutes * MINUTE_MS
    if stop > now or stop > int(series.end[index]):
        return Why(WINDOW_OPEN, (minutes,))
    first = int(np.searchsorted(series.start, start, side="left"))
    after = int(np.searchsorted(series.start, stop, side="left"))
    if first == after:
        return Why(NO_CANDLE)
    if int(series.end[after - 1]) > stop:  # candles that do not divide the range (spec refuses)
        return Why(NO_DATA, ("opening range",))
    return first, after


def orb(
    series: Series,
    index: int,
    minutes: int,
    opens: Callable[[int], int | None],
    now: int,
    side: str,
) -> Value:
    """The high ("high") or low ("low") of the first ``minutes`` of the candle's session."""
    span = orb_span(series, index, minutes, opens, now)
    if isinstance(span, Why):
        return span
    first, after = span
    if side == "high":
        return int(series.h[first:after].max())
    return int(series.l[first:after].min())


def unverified_in(series: Series, first: int, after: int) -> Why | None:
    """PAUSED when a candle in ``[first, after)`` was built while the price feed paused and has
    not been repaired; ``detail`` is the start of the first such candle (epoch ms)."""
    flagged = np.flatnonzero(series.flags[first:after] & UNVERIFIED)
    if not flagged.size:
        return None
    return Why(PAUSED, (int(series.start[first + int(flagged[0])]),))


def extremum_at(series: Series, side: str, n: int, source: str, index: int) -> Value:
    """The highest ("high") or lowest ("low") ``source`` of the ``n`` candles up to ``index``."""
    if n < 1:
        raise ValueError("a window holds at least one candle")
    if index + 1 < n:
        return Why(WARMING, (n, index + 1))
    window = series.column(COLUMNS[source])[index - n + 1 : index + 1]
    return int(window.max() if side == "high" else window.min())


def extremum_array(series: Series, side: str, n: int, source: str) -> np.ndarray:
    """``extremum_at`` for every candle at once (NaN while fewer than ``n`` candles): a backtest or
    a chart reads whole arrays, a run reads one place and takes a slice."""
    column = series.column(COLUMNS[source]).astype(np.float64)
    return rolling_max(column, n) if side == "high" else rolling_min(column, n)


# ---- against the index ------------------------------------------------------------------------
def relative_strength(series: Series, bench: Series | None, index: int, n: int) -> Fraction | Why:
    """The instrument's n-candle return against the index's, in percent (see the module notes)."""
    if bench is None:
        return Why(NO_DATA, ("index",))
    back = index - n
    if back < 0:
        return Why(WARMING, (n + 1, index + 1))
    now = bench.index_of_start(int(series.start[index]))
    then = bench.index_of_start(int(series.start[back]))
    if now is None or then is None:
        return Why(NO_DATA, ("index",))
    close_now, close_then = int(series.c[index]), int(series.c[back])
    bench_now, bench_then = int(bench.c[now]), int(bench.c[then])
    if min(close_now, close_then, bench_now, bench_then) <= 0:
        return Why(ZERO)
    return Fraction(100 * (close_now * bench_then - close_then * bench_now), close_then * bench_now)
