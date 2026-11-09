"""Candle patterns, each defined in whole numbers of paise so a boundary case has one answer.

A pattern is a statement about the shape of the candle at ``index`` (and, for three of them, the
candle or candles before it). The definitions are the screener's own and are the words the owner
reads on the phone; no library is asked.

Terms, for a candle with open o, high h, low l and close c:

    body   = |c - o|        range = h - l
    upper  = h - max(o, c)  lower = min(o, c) - l

    doji             the body is at most a tenth of the range (10 x body <= range). A candle that
                     traded at one price only (range 0) has no shape and is not one.
    hammer           a body that is not nothing, a lower shadow at least twice the body and an
                     upper shadow of at most a tenth of the range: small body at the top, long tail
                     below. Either colour.
    shooting_star    the mirror: an upper shadow at least twice the body, a lower shadow of at most
                     a tenth of the range, a body that is not nothing.
    bullish_engulfing  a down candle (close below open) followed by an up candle whose body covers
                     the whole body before it (open at or below the earlier close, close at or
                     above the earlier open) and is larger.
    bearish_engulfing  the mirror: an up candle, then a down candle that covers its body and is
                     larger.
    inside_bar       the high is below the high before it and the low above the low before it
                     (both strictly).
    nr7              the narrowest range of the last seven candles: this candle's range is above 0
                     and at most that of each of the six before it (a tie counts).

``holds`` answers True or False, or a ``Why`` (WARMING) while the series holds fewer candles than
the pattern needs (``catalog.PATTERN_NEEDS``). Pure: no I/O, no clock.
"""

from __future__ import annotations

from sudarshan.screener import catalog as cat
from sudarshan.screener.types import WARMING, Series, Why

#: Share of the range a doji's body may take, and a hammer's wrong-side shadow: 1 in 10.
TENTH = 10
#: How many times the body the long shadow of a hammer or a shooting star must be.
TAIL = 2
NR_WINDOW = 7


def _shape(series: Series, index: int) -> tuple[int, int, int, int]:
    """(body, upper shadow, lower shadow, range) of one candle."""
    o, h, low, c = (int(series.column(name)[index]) for name in ("o", "h", "l", "c"))
    return abs(c - o), h - max(o, c), min(o, c) - low, h - low


def doji(series: Series, index: int) -> bool:
    body, _, _, rng = _shape(series, index)
    return rng > 0 and TENTH * body <= rng


def hammer(series: Series, index: int) -> bool:
    body, upper, lower, rng = _shape(series, index)
    return rng > 0 and body > 0 and lower >= TAIL * body and TENTH * upper <= rng


def shooting_star(series: Series, index: int) -> bool:
    body, upper, lower, rng = _shape(series, index)
    return rng > 0 and body > 0 and upper >= TAIL * body and TENTH * lower <= rng


def _prices(series: Series, index: int) -> tuple[int, int]:
    return int(series.o[index]), int(series.c[index])


def bullish_engulfing(series: Series, index: int) -> bool:
    before_open, before_close = _prices(series, index - 1)
    now_open, now_close = _prices(series, index)
    return (
        before_close < before_open
        and now_close > now_open
        and now_open <= before_close
        and now_close >= before_open
        and now_close - now_open > before_open - before_close
    )


def bearish_engulfing(series: Series, index: int) -> bool:
    before_open, before_close = _prices(series, index - 1)
    now_open, now_close = _prices(series, index)
    return (
        before_close > before_open
        and now_close < now_open
        and now_open >= before_close
        and now_close <= before_open
        and now_open - now_close > before_close - before_open
    )


def inside_bar(series: Series, index: int) -> bool:
    return bool(series.h[index] < series.h[index - 1] and series.l[index] > series.l[index - 1])


def nr7(series: Series, index: int) -> bool:
    ranges = (
        series.h[index - NR_WINDOW + 1 : index + 1] - series.l[index - NR_WINDOW + 1 : index + 1]
    )
    return bool(ranges[-1] > 0 and (ranges[-1] <= ranges[:-1]).all())


_TESTS = {
    "bullish_engulfing": bullish_engulfing,
    "bearish_engulfing": bearish_engulfing,
    "doji": doji,
    "hammer": hammer,
    "shooting_star": shooting_star,
    "inside_bar": inside_bar,
    "nr7": nr7,
}


def holds(name: str, series: Series, index: int) -> bool | Why:
    """Whether pattern ``name`` is the shape of the candle at ``index``."""
    need = cat.PATTERN_NEEDS[name]
    if index + 1 < need:
        return Why(WARMING, (need, index + 1))
    return _TESTS[name](series, index)
