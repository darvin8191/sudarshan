"""Candles of every timeframe from the day's 1-minute bars (M9.1). Pure.

The grid is the session's, as the charts bucket it: candle k of N minutes covers
[start + kN, start + (k+1)N), the last one ends at the session's end, and the day is the whole
session (clock.candle). A candle with no 1-minute bar does not exist; one with bars is the
aggregate of those that ended by the boundary, and is "forming" when its own end is later (the
screener reads sealed bars only, so a day candle at the 10:15 run is the aggregate of 09:15 to
10:15).

A candle's open is its first bar's, its close the last bar's, its volume the sum, its open
interest and VWAP the last the feed gave (-1 and 0 when it never did). Its flags are the bars':
UNVERIFIED when any bar a pause touched has not been replaced from REST, REPAIRED when any was,
LATE and FROM_I1 as the bars say. A candle whose span a pause touched is UNVERIFIED too, though no
bar of it was in the pause (the market alerts' rule): the minutes after its last bar were not
watched, by a quiet feed, by a stopped screener, or because no screen followed the key then (its
gap, bars.Bars.follow_from). Weeks are the days from the week's first trading day.

Today's day candle is the exchange's (``day``, MEDIUM-B5): its open, high and low are the day candle
the feed carried with the latest trade judged (the pre-open price as its open), widened by the
bars after that trade, and its volume is the exchange's running total there, the pre-open
included. Before any frame carried that candle its open, high and low are not known
(NO_EXCHANGE_DAY), never the first trade's. The exchange's candle covers every minute before it,
so only a pause after it leaves the day UNVERIFIED: a stretch nobody watched earlier in the day
(a feed pause, a restart, a key first followed at 11:00) no longer blanks the day's levels once a
trade after it has come.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from sudarshan.alerts.market.clock import Market, candle, paused_during, within
from sudarshan.alerts.market.sessions import AUCTION, Session, key_day, segment_of
from sudarshan.screener.bars import Bar, flags_of, touched
from sudarshan.screener.types import (
    DAY,
    MINUTE_TFS,
    NO_EXCHANGE_DAY,
    REPAIRED,
    TF,
    UNVERIFIED,
    Candle,
    Series,
)

if TYPE_CHECKING:
    from sudarshan.alerts.market.clock import Pause
    from sudarshan.screener.bars import Bars


def aggregate(
    bars: Sequence[Bar],
    minutes: int,
    grid: Session,
    boundary: int,
    pauses: Sequence[Pause] = (),
) -> tuple[list[Candle], bool]:
    """The ``minutes`` candles (0: the day) of ``bars`` that ended by ``boundary``, oldest first,
    and whether the last one is still forming. ``pauses`` are today's, inside the key's session."""
    marks = touched(bars, pauses, grid.start)
    out: list[list] = []
    for bar, hit in zip(bars, marks, strict=True):
        if bar.end > boundary:
            break
        k, start, end = candle(grid, minutes, bar.start)
        flags = flags_of(bar, hit)
        if out and out[-1][0] == k:
            row = out[-1]
            row[4], row[5], row[6] = max(row[4], bar.h), min(row[5], bar.low), bar.c
            row[7] += bar.v
            row[8] = bar.oi if bar.oi >= 0 else row[8]
            row[9] = bar.atp if bar.atp > 0 else row[9]
            row[10] |= flags
        else:
            out.append([k, start, end, bar.o, bar.h, bar.low, bar.c, bar.v, bar.oi, bar.atp, flags])
    for row in out:
        # the market alerts' rule (clock.paused_during): a candle a pause over GAP_S touched is not
        # whole, though no bar of it was in the pause (no trade came after it in the candle, or the
        # screener itself was stopped then, S14); a repaired candle is the repair's (MEDIUM-5)
        span_end = min(row[2], boundary)
        if pauses and not row[10] & REPAIRED and paused_during(tuple(pauses), row[1], span_end):
            row[10] |= UNVERIFIED
    candles = [
        Candle(
            row[1],
            row[2],
            grid.key,
            row[3],
            row[4],
            row[5],
            row[6],
            row[7],
            row[8],
            row[9],
            row[10],
        )
        for row in out
    ]
    return candles, bool(candles) and candles[-1].end > boundary


def series(
    bars: Sequence[Bar],
    tf: TF,
    grid: Session,
    boundary: int,
    pauses: Sequence[Pause] = (),
) -> Series:
    """The engine's series for one timeframe of today (a minute timeframe or the day)."""
    if tf == DAY:
        minutes = 0
    elif tf in MINUTE_TFS:
        minutes = int(tf)
    else:
        raise ValueError(f"not a timeframe built from minutes: {tf!r}")
    found, forming = aggregate(bars, minutes, grid, boundary, pauses)
    return Series.of(tf, found, forming)


def day(
    bars: Sequence[Bar],
    exchange: Mapping[int, tuple[int, int, int]],
    cums: Mapping[int, int],
    grid: Session,
    boundary: int,
    pauses: Sequence[Pause] = (),
) -> Series:
    """Today's day candle up to ``boundary`` as the exchange counts it (see the module notes):
    ``exchange`` maps a bar's start to the exchange's (open, high, low) at its latest trade,
    ``cums`` to the running total through it."""
    found, forming = aggregate(bars, 0, grid, boundary)
    if not found:
        return Series.of(DAY, [], False)
    upto = [bar for bar in bars if bar.end <= boundary]
    last = upto[-1]
    known = [i for i, bar in enumerate(upto) if bar.start in exchange]
    candle = found[-1]
    o, h, low, flags = candle.o, candle.h, candle.low, candle.flags
    since = max(last.until, last.start)  # the latest trade judged
    if known:
        first = known[-1]
        o, h, low = exchange[upto[first].start]
        h = max(h, *(bar.h for bar in upto[first:]))
        low = min(low, *(bar.low for bar in upto[first:]))
        since = max(upto[first].until, upto[first].start)
    else:
        flags |= NO_EXCHANGE_DAY
    if pauses and paused_during(tuple(pauses), since, min(grid.end, boundary)):
        flags |= UNVERIFIED
    volume = cums.get(last.start, candle.v)
    today = candle._replace(o=o, h=h, low=low, v=volume, flags=flags)
    return Series.of(DAY, [today], forming)


def read(store: Bars, key: str, tf: TF, market: Market, boundary: int) -> Series:
    """``key``'s candles of ``tf`` up to ``boundary``, from the bars ``store`` holds: the cash grid
    (a stock that closes by auction is read on it too), the pauses (the feed's and the service's,
    and the key's own gaps: stretches the screener did not follow it) as they fall inside the
    key's own session. A store still on an earlier day (no frame or clock tick has rolled it yet)
    has no candles for today, never yesterday's."""
    segment = segment_of(key)
    grid = market.sessions.get(segment)
    own = market.sessions.get(segment + AUCTION if key in market.auction else segment)
    if grid is None or own is None or store.day != market.today:
        return Series.of(tf, [])
    pauses = market.pauses + store.gaps(key)
    pauses = within(pauses, own.start, own.end) if pauses else ()
    if tf == DAY:
        held = store.bars(key)
        return day(held, store.exchange_days(key), store.cums(key), grid, boundary, pauses)
    return series(store.bars(key), tf, grid, boundary, pauses)


def weekly(days: Sequence[Candle]) -> list[Candle]:
    """Day candles (oldest first) as week candles: a week starts with its first trading day."""
    weeks: list[list] = []
    for day in days:
        week = key_day(day.session).isocalendar()[:2]
        if weeks and weeks[-1][0] == week:
            row = weeks[-1]
            row[4], row[5], row[6] = max(row[4], day.h), min(row[5], day.low), day.c
            row[7] += day.v
            row[8] = day.oi if day.oi >= 0 else row[8]
            row[9] |= day.flags
            row[2] = day.end
        else:
            weeks.append([week, day.start, day.end, day.o, day.h, day.low, day.c, day.v,
                          day.oi, day.flags, day.session])  # fmt: skip
    return [
        Candle(row[1], row[2], row[10], row[3], row[4], row[5], row[6], row[7], row[8], 0, row[9])
        for row in weeks
    ]
