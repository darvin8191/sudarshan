"""Judging one instrument at one boundary: offsets, forming candles, operators, three-valued logic.

``judge(plan, subject, ctx)`` takes the screen's compiled plan, one instrument's candles (cut for the
boundary) and what the run knows, and returns an ``Outcome``: a hit, a miss, "not checked" with the
reasons, or "not due". Pure, like the rest of the engine: every input is an argument, nothing is
read from a clock, a file or the feed, and a value that cannot be computed is a ``Why``, never a
guess.

Which candle an operand reads. ``ctx.boundary`` is the end of the candle being judged (16:05 for a
check after the close). On the run's timeframe ``at: 0`` is the candle that ends at the boundary; on a
higher timeframe it is the forming candle up to the boundary, marked "forming" (a candle that has
not ended is the aggregate of the 1-minute bars that have). ``ago n`` goes n candles back,
``first_today`` is the first candle of the session, ``last_prev_day`` the last candle of the
session before it. A candle that ends after the boundary is never read (no look-ahead), whatever
the series holds. An instrument is judged only when it has a candle that ends at the boundary;
otherwise it is not due, which is neither a hit nor a miss.

Three-valued logic. An operand that cannot be computed makes its line UNKNOWN. ``all``: any FALSE
gives FALSE, else any UNKNOWN gives UNKNOWN. ``any``: any TRUE gives TRUE, else any UNKNOWN gives
UNKNOWN. ``not`` of UNKNOWN is UNKNOWN. A root that stays UNKNOWN is "Not checked" with the reasons
of the lines that left it so (only those on a path that decided nothing), never a miss: a gap in
the data can never empty a watchlist.

Numbers. A price is a paise integer, a ratio an exact ``Fraction``, an indicator a float. Exact
against exact is compared exactly; a float on either side compares as floats and two floats that
differ by less than ``tolerance`` are the same number, so an indicator of a flat series (an average
that is one rounding away from the price it averages) never produces a crossing out of nothing.
Arithmetic stays exact until a float enters it; dividing by zero is "cannot be computed".

Crossings follow Pine's ``ta.crossover``: ``crosses_above`` is ``l[0] > r[0] and l[1] <= r[1]``,
where ``[1]`` is the candle before the one read (touching the line then counts as being on it).
``rising(n)`` is n strict comparisons between n + 1 neighbouring candles.

A candle built while the price feed paused and not repaired (``UNVERIFIED``) makes any line that
reads it UNKNOWN with ``PAUSED``; what a line reads is the candle it names (a window operand reads
the window, an indicator only the candle it is read at). A closing-auction stock is not judged on
the day's last candle until its auction price has come: "Not checked: no closing auction price yet".

Marks. A hit or a miss says whether a figure it stands on came from a candle that was still forming
(``forming``) or was repaired after a pause (``repaired``). Only lines that decided something count:
a line that could not be computed showed no figure, and a "Not checked" outcome carries no marks.

Candle timing (when a boundary is judged, what a late trade does) belongs to ``bars``, which takes it
from the market alerts clock. Nothing here knows a second.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from fractions import Fraction
from typing import NamedTuple

import numpy as np

from sudarshan.screener import catalog as cat
from sudarshan.screener import library, options, patterns, price
from sudarshan.screener.types import (
    DAY,
    FALSE,
    FIRST_TODAY,
    HIT,
    LAST_PREV_DAY,
    MISS,
    NO_AUCTION,
    NO_CANDLE,
    NO_DATA,
    NOT_A_NUMBER,
    NOT_CHECKED,
    NOT_DUE,
    OTHER_DAY,
    PAISE,
    QTY,
    REPAIRED,
    RUPEES,
    TF,
    TRUE,
    UNKNOWN,
    WARMING,
    WEEK,
    ZERO,
    Clause,
    Field,
    Group,
    Ind,
    Math,
    Not,
    Oi,
    Opt,
    Outcome,
    Plan,
    Series,
    Tri,
    Why,
    all_of,
    any_of,
    none_of,
    not_,
    tri,
)

#: Stored on every run so a rewind knows which rules judged it. Raise it when a rule changes.
ENGINE_VERSION = 1
#: Two floats closer than this are the same number: ``FLOAT_ABS`` (paise or ratio units) plus
#: ``FLOAT_REL`` of the larger. Far below a paisa, far above a rounding error of a recursive average.
FLOAT_ABS = 1e-6
FLOAT_REL = 1e-11

Value = int | float | Fraction | str | Why
Number = int | float | Fraction

#: Session levels read from today's day candle, and from the one before it.
TODAY_LEVELS = frozenset({"day_open", "day_high", "day_low", "gap_pct", "year_high", "year_low"})
PREVIOUS_LEVELS = frozenset({"prev_day_high", "prev_day_low", "prev_day_close"})
_GROUPS = {"all": all_of, "any": any_of, "none": none_of}


# ---- what the run knows ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Subject:
    """One instrument at one run.

    ``series`` holds its candles by timeframe, oldest first, cut for the boundary: the last candle of
    a higher timeframe may be the forming one (``Series.forming``). ``cp`` is the frozen previous
    close (paise), ``year`` the 52-week (high, low) of the morning snapshot, ``prev_oi`` yesterday's
    open interest, ``underlying`` the name its options chain is kept under. ``auction_end`` is the
    end of the last candle of a stock that closes by auction (the cash close) and ``printed`` says
    whether its auction price has come.
    """

    symbol: str
    series: Mapping[TF, Series]
    cp: int | None = None
    year: tuple[int, int] | None = None
    prev_oi: int | None = None
    underlying: str | None = None
    auction_end: int | None = None
    printed: bool = False


def _no_opens(session: int) -> int | None:
    return None


def _no_bench(name: str, tf: TF) -> Series | None:
    return None


@dataclass(frozen=True, slots=True)
class Context:
    """What the run knows besides the instrument.

    ``boundary`` is the end of the candle judged (epoch ms) and ``session`` its trading day
    (YYYYMMDD); ``previous`` is the trading day before it (0: not known). ``opens`` maps a session
    to its start; ``bench`` returns an index's candles on a timeframe; ``options`` answers an option
    operand (``options.View``).
    """

    boundary: int
    session: int
    previous: int = 0
    opens: Callable[[int], int | None] = _no_opens
    bench: Callable[[str, TF], Series | None] = _no_bench
    options: Callable[..., Value] | None = None


# ---- numbers --------------------------------------------------------------------------------------
def tolerance(x: float, y: float) -> float:
    return FLOAT_ABS + FLOAT_REL * max(abs(x), abs(y))


def compare(op: str, a: Number, b: Number) -> bool:
    """``a <op> b`` for gt, gte, lt, lte and eq: exact when both are exact, else within tolerance."""
    if isinstance(a, float) or isinstance(b, float):
        x, y = float(a), float(b)
        gap, tol = x - y, tolerance(x, y)
        if op == "gt":
            return gap > tol
        if op == "gte":
            return gap >= -tol
        if op == "lt":
            return gap < -tol
        if op == "lte":
            return gap <= tol
        if op == "eq":
            return abs(gap) <= tol
    else:
        if op == "gt":
            return a > b
        if op == "gte":
            return a >= b
        if op == "lt":
            return a < b
        if op == "lte":
            return a <= b
        if op == "eq":
            return a == b
    raise ValueError(f"not a comparison: {op!r}")


def arithmetic(op: str, a: Number, b: Number) -> Number | Why:
    """add, sub, mul or div: exact while both sides are, a float once one is."""
    if isinstance(a, float) or isinstance(b, float):
        x, y = float(a), float(b)
        if op == "div":
            if y == 0:
                return Why(ZERO)
            result = x / y
        else:
            result = {"add": x + y, "sub": x - y, "mul": x * y}[op]
        return result if math.isfinite(result) else Why(NOT_A_NUMBER)
    if op == "div":
        return Why(ZERO) if b == 0 else Fraction(a) / Fraction(b)
    return {"add": a + b, "sub": a - b, "mul": a * b}[op]


def within(a: Number, b: Number, pct: Fraction) -> bool | Why:
    """Whether ``a`` is within ``pct`` percent of ``b`` (the distance measured against ``b``)."""
    if b == 0:
        return Why(ZERO)
    if isinstance(a, float) or isinstance(b, float):
        x, y = float(a), float(b)
        return abs(x - y) * 100 <= float(pct) * abs(y) + tolerance(x, y) * 100
    return abs(a - b) * 100 <= pct * abs(b)


# ---- words for a value ----------------------------------------------------------------------------
def _rounded(value: Fraction, places: int) -> Fraction:
    """Round half away from zero."""
    scale = 10**places
    scaled = value * scale
    whole = math.floor(abs(scaled) + Fraction(1, 2))
    return Fraction(whole if scaled >= 0 else -whole, scale)


def _fixed(value: Fraction, places: int) -> str:
    rounded = _rounded(value, places)
    digits = str(int(abs(rounded) * 10**places)).rjust(places + 1, "0")
    text = f"{digits[:-places]}.{digits[-places:]}"
    return f"-{text}" if rounded < 0 else text


def _trimmed(text: str) -> str:
    return text.rstrip("0").rstrip(".")


def show(value: Number | str, unit: str) -> str:
    """A value as the plain decimal text the page shows beside the rule.

    Prices and turnover in rupees with two places, volumes whole, percentages and ratios with up to
    four places and no trailing zeros; a state is its name. Rounded half away from zero, for the
    text only: nothing is decided on it.
    """
    if isinstance(value, str):
        return value
    exact = Fraction(value)
    if unit == PAISE:
        return _fixed(exact / 100, 2)
    if unit == RUPEES:
        return _fixed(exact, 2)
    if unit == QTY:
        return str(int(exact)) if exact.denominator == 1 else _trimmed(_fixed(exact, 2))
    return _trimmed(_fixed(exact, 4))


# ---- finding a candle -----------------------------------------------------------------------------
class Located(NamedTuple):
    series: Series
    index: int
    forming: bool


def base_index(series: Series, boundary: int) -> tuple[int, bool] | None:
    """(index, forming) of the candle ``at: 0`` reads at ``boundary``, or None.

    The last candle that ended by the boundary, or the forming one after it when the series says
    its last candle has not ended (and has started). Candles that end later are never read.
    """
    if not len(series):
        return None
    ended = int(np.searchsorted(series.end, boundary, side="right"))
    if series.forming and ended < len(series) and int(series.start[ended]) < boundary:
        return ended, True
    return (ended - 1, False) if ended else None


def ends_at(series: Series, boundary: int) -> bool:
    """Whether a candle of the series ends exactly at the boundary (the run's candle exists)."""
    ended = int(np.searchsorted(series.end, boundary, side="right"))
    return ended > 0 and int(series.end[ended - 1]) == boundary


def fold(node, leaf: Callable[[Clause], Tri]) -> Tri:
    """The truth of a condition tree when each line is worth ``leaf(line)``."""
    if isinstance(node, Clause):
        return leaf(node)
    if isinstance(node, Not):
        return not_(fold(node.item, leaf))
    return _GROUPS[node.kind](fold(item, leaf) for item in node.items)


# ---- one line's result ----------------------------------------------------------------------------
@dataclass(slots=True)
class Result:
    tri: Tri
    why: Why | None = None
    left: Value | None = None
    right: Value | None = None
    forming: bool = False
    repaired: bool = False


Part = tuple[Tri, Why | None]


def _test(op: str, a: Value, b: Value, pct: Fraction | None = None) -> Part:
    if isinstance(a, Why):
        return UNKNOWN, a
    if isinstance(b, Why):
        return UNKNOWN, b
    if op == "within_pct":
        found = within(a, b, pct)
        return (UNKNOWN, found) if isinstance(found, Why) else (tri(found), None)
    return tri(compare(op, a, b)), None


def _figure(value: Value | None) -> Value | None:
    """A value worth showing: not a reason."""
    return None if isinstance(value, Why) else value


def _every(parts: Iterable[Part]) -> Part:
    """All of the parts, three-valued; an UNKNOWN carries the first reason."""
    reason = None
    for state, why in parts:
        if state is FALSE:
            return FALSE, None
        if state is UNKNOWN and reason is None:
            reason = why
    return (UNKNOWN, reason) if reason is not None else (TRUE, None)


def _walk(node, results: Mapping[str, Result]) -> tuple[Tri, list[Why]]:
    """(truth, reasons): the reasons are those of the UNKNOWN lines on UNKNOWN paths only."""
    if isinstance(node, Clause):
        found = results[node.id]
        return found.tri, ([found.why] if found.tri is UNKNOWN and found.why else [])
    if isinstance(node, Not):
        state, whys = _walk(node.item, results)
        return not_(state), whys
    parts = [_walk(item, results) for item in node.items]
    state = _GROUPS[node.kind](part[0] for part in parts)
    if state is not UNKNOWN:
        return state, []
    return state, [why for part in parts for why in part[1]]


def _unique(whys: Iterable[Why]) -> tuple[Why, ...]:
    seen: list[Why] = []
    for why in whys:
        if why not in seen:
            seen.append(why)
    return tuple(seen)


# ---- the reading of one instrument ----------------------------------------------------------------
class Reading:
    """One instrument at one boundary: reads operands and judges plans.

    Indicators are computed once per distinct instance (name, settings, timeframe) and kept in
    ``memo``, so a screen with five lines on one EMA computes it once. Pass the same ``memo`` for
    every plan judged on the same instrument at the same boundary, and never for another.
    """

    def __init__(self, subject: Subject, ctx: Context, memo: dict | None = None):
        self.subject = subject
        self.ctx = ctx
        self._memo = memo if memo is not None else {}
        self._forming = False
        self._flags = 0

    # -- candles --
    def locate(self, tf: TF, at: int | str, back: int = 0) -> Located | Why:
        """The candle an operand names, ``back`` candles earlier still."""
        series = self.subject.series.get(tf)
        found = base_index(series, self.ctx.boundary) if series is not None else None
        if found is None:
            return Why(NO_CANDLE)
        base, forming = found
        session = self.ctx.session
        if at != LAST_PREV_DAY and tf != WEEK and int(series.session[base]) != session:
            return Why(NO_CANDLE)  # no candle of today on this timeframe: yesterday's is not "now"
        if at.__class__ is int:
            index = base - at
        elif at == FIRST_TODAY:
            index = int(np.searchsorted(series.session, session, side="left"))
        elif at == LAST_PREV_DAY:
            index = int(np.searchsorted(series.session, session, side="left")) - 1
            if index >= 0 and self.ctx.previous and int(series.session[index]) != self.ctx.previous:
                return Why(NO_CANDLE)
        else:
            raise ValueError(f"not an offset: {at!r}")
        index -= back
        if index < 0 or index > base:
            return Why(NO_CANDLE)
        return Located(series, index, forming and index == base)

    def _read(self, series: Series, first: int, after: int, forming: bool = False) -> Why | None:
        """Note the candles ``[first, after)`` as read; PAUSED when one was never verified."""
        pause = price.unverified_in(series, first, after)
        if pause is not None:
            return pause
        self._flags |= int(np.bitwise_or.reduce(series.flags[first:after]))
        self._forming = self._forming or forming
        return None

    # -- operands --
    def value(self, op, back: int = 0) -> Value:
        """An operand's value, ``back`` candles earlier than its own offset."""
        kind = op.kind
        if kind == "num":
            return op.value
        if kind == "field":
            return self._field(op, back)
        if kind == "ind":
            return self._ind(op, back)
        if kind == "oi":
            return self._oi(op, back)
        if kind == "opt":
            return self._opt(op, back)
        return self._math(op, back)

    def _math(self, op: Math, back: int) -> Value:
        a = self.value(op.a, back)
        if isinstance(a, Why):
            return a
        b = self.value(op.b, back)
        if isinstance(b, Why):
            return b
        return arithmetic(op.op, a, b)

    def _field(self, field: Field, back: int) -> Value:
        if field.tf is None:
            return self._level(field.name)
        found = self.locate(field.tf, field.at, back)
        if isinstance(found, Why):
            return found
        series, index = found.series, found.index
        if (pause := self._read(series, index, index + 1, found.forming)) is not None:
            return pause
        if field.name == "change_pct":
            days = self.subject.series.get(DAY)
            return price.change_pct(series, index, days, self.ctx.session, self.subject.cp)
        return price.candle_field(series, index, field.name)

    def _previous_candle(self, days: Series | None) -> int | Why | None:
        """The day candle before today's: its index, None when there is none, NO_CANDLE when it
        is not the trading day before today (a stock that did not trade yesterday)."""
        if days is None or not len(days):
            return None
        at = price.previous_index(days.session, self.ctx.session)
        if at is None:
            return None
        if self.ctx.previous and int(days.session[at]) != self.ctx.previous:
            return Why(NO_CANDLE)
        return at

    def _level(self, name: str) -> Value:
        days, session, cp = self.subject.series.get(DAY), self.ctx.session, self.subject.cp
        at: int | Why | None = None
        if name in TODAY_LEVELS:
            at = price.today_index(days, session)
        elif name in PREVIOUS_LEVELS or (name == "prev_close" and not cp):
            at = self._previous_candle(days)
            if isinstance(at, Why):
                return at if name != "prev_close" else Why(NO_DATA, ("prev_close",))
        if isinstance(at, int):
            forming = days.forming and at == len(days) - 1
            if (pause := self._read(days, at, at + 1, forming)) is not None:
                return pause
        return price.session_level(name, days, session, cp, self.subject.year)

    def _ind(self, ind: Ind, back: int) -> Value:
        if cat.IND[ind.name].family == "library":
            return self._library(ind, back)
        if ind.name == "pivot":
            return self._pivot(ind)
        if ind.name == "pattern":
            raise TypeError("a candle pattern is checked with 'is', not read as a value")
        found = self.locate(ind.tf, ind.at, back)
        if isinstance(found, Why):
            return found
        params = dict(ind.params)
        if ind.name in ("highest", "lowest"):
            return self._extremum(ind, found, params)
        if ind.name in ("orb_high", "orb_low"):
            return self._opening_range(ind, found, params)
        return self._strength(ind, found, params)

    def _library(self, ind: Ind, back: int) -> Value:
        found = self.locate(ind.tf, ind.at, back)
        if isinstance(found, Why):
            return found
        series, index = found.series, found.index
        params = dict(ind.params)
        need, have = cat.IND[ind.name].warmup(params, ind.out), index + 1
        if have < need:
            return Why(WARMING, (need, have))
        if (pause := self._read(series, index, index + 1, found.forming)) is not None:
            return pause
        arrays = self._memo.get(ind.instance)
        if arrays is None:
            arrays = self._memo[ind.instance] = library.compute(ind.name, params, series)
        result = float(arrays[ind.out][index])
        return result if math.isfinite(result) else Why(NOT_A_NUMBER)

    def _extremum(self, ind: Ind, found: Located, params: Mapping) -> Value:
        series, index, n = found.series, found.index, params["length"]
        side = "high" if ind.name == "highest" else "low"
        result = price.extremum_at(series, side, n, params["source"], index)
        if isinstance(result, Why):
            return result
        pause = self._read(series, index - n + 1, index + 1, found.forming)
        return pause if pause is not None else result

    def _opening_range(self, ind: Ind, found: Located, params: Mapping) -> Value:
        series, index, minutes = found.series, found.index, params["minutes"]
        span = price.orb_span(series, index, minutes, self.ctx.opens, self.ctx.boundary)
        if isinstance(span, Why):
            return span
        if (pause := self._read(series, span[0], span[1])) is not None:
            return pause
        side = "high" if ind.name == "orb_high" else "low"
        return price.orb(series, index, minutes, self.ctx.opens, self.ctx.boundary, side)

    def _strength(self, ind: Ind, found: Located, params: Mapping) -> Value:
        series, index, n = found.series, found.index, params["n"]
        bench = self.ctx.bench(params["index"], ind.tf)
        result = price.relative_strength(series, bench, index, n)
        if isinstance(result, Why):
            return result
        for at in (index - n, index):
            if (pause := self._read(series, at, at + 1, found.forming and at == index)) is not None:
                return pause
            where = bench.index_of_start(int(series.start[at]))
            if (pause := self._read(bench, where, where + 1)) is not None:
                return pause
        return result

    def _pivot(self, ind: Ind) -> Value:
        days = self.subject.series.get(DAY)
        at = self._previous_candle(days)
        if at is None:
            return Why(NO_CANDLE)
        if isinstance(at, Why):
            return at
        if (pause := self._read(days, at, at + 1)) is not None:
            return pause
        return price.pivot(dict(ind.params)["kind"], ind.out, days, self.ctx.session)

    def _oi(self, oi: Oi, back: int) -> Value:
        found = self.locate(oi.tf, 0, back)
        if isinstance(found, Why):
            return found
        series, index = found.series, found.index
        oi_now, price_now = int(series.oi[index]), int(series.c[index])
        if oi_now < 0:
            return Why(NO_DATA, ("oi",))
        if oi.window[0] == "day":
            if int(series.session[index]) != self.ctx.session:
                return Why(OTHER_DAY, (oi.what,))
            days, cp = self.subject.series.get(DAY), self.subject.cp
            price_ref = price.reference_close(days, self.ctx.session, self.ctx.session, cp)
            oi_ref, first = self.subject.prev_oi, index
            if price_ref is None:
                return Why(NO_DATA, ("prev_close",))
            if oi_ref is None:
                return Why(NO_DATA, ("oi",))
        else:
            first = index - oi.window[1]
            if first < 0:
                return Why(WARMING, (oi.window[1] + 1, index + 1))
            oi_ref, price_ref = int(series.oi[first]), int(series.c[first])
            if oi_ref < 0:
                return Why(NO_DATA, ("oi",))
        for at in (first, index):
            if (pause := self._read(series, at, at + 1, found.forming and at == index)) is not None:
                return pause
        if oi.what == "buildup":
            return options.buildup(price_now, price_ref, oi_now, oi_ref, oi.min_price, oi.min_oi)
        return options.oi_change_pct(oi_now, oi_ref)

    def _opt(self, opt: Opt, back: int) -> Value:
        if back:
            return Why(NO_DATA, ("earlier option values",))
        view = self.ctx.options if self.ctx.options is not None else options.View()
        return view(opt, self.subject.underlying)

    # -- lines --
    def clause(self, line: Clause) -> Result:
        """One line's truth, and the figures behind it."""
        self._forming, self._flags = False, 0
        result = self._decide(line)
        result.forming, result.repaired = self._forming, bool(self._flags & REPAIRED)
        return result

    def _decide(self, line: Clause) -> Result:
        op = line.op
        if op == "is":
            return self._is(line)
        if op in ("rising", "falling"):
            return self._run(line)
        if op == "between":
            return self._between(line)
        if op in ("crosses_above", "crosses_below"):
            return self._cross(line)
        left, right = self.value(line.left), self.value(line.right)
        state, why = _test(op, left, right, line.arg("pct"))
        return self._result(state, why, left, right)

    @staticmethod
    def _result(state: Tri, why: Why | None, left: Value, right: Value | None = None) -> Result:
        if state is UNKNOWN:
            return Result(UNKNOWN, why)
        return Result(state, None, _figure(left), _figure(right))

    def _between(self, line: Clause) -> Result:
        left = self.value(line.left)
        low, high = line.arg("low"), line.arg("high")
        state, why = _every([_test("gte", left, low), _test("lte", left, high)])
        return self._result(state, why, left)

    def _cross(self, line: Clause) -> Result:
        above = line.op == "crosses_above"
        l0, r0 = self.value(line.left), self.value(line.right)
        l1, r1 = self.value(line.left, 1), self.value(line.right, 1)
        state, why = _every(
            [_test("gt" if above else "lt", l0, r0), _test("lte" if above else "gte", l1, r1)]
        )
        return self._result(state, why, l0, r0)

    def _run(self, line: Clause) -> Result:
        n, op = line.arg("n"), "gt" if line.op == "rising" else "lt"
        values = [self.value(line.left, k) for k in range(n + 1)]
        state, why = _every([_test(op, values[k], values[k + 1]) for k in range(n)])
        return self._result(state, why, values[0])

    def _is(self, line: Clause) -> Result:
        name, left = line.arg("name"), line.left
        if left.kind == "oi":
            found = self.value(left)
            if isinstance(found, Why):
                return Result(UNKNOWN, found)
            return Result(tri(found == name), None, found)
        located = self.locate(left.tf, left.at)
        if isinstance(located, Why):
            return Result(UNKNOWN, located)
        series, index = located.series, located.index
        need = cat.PATTERN_NEEDS[name]
        if index + 1 >= need and (
            pause := self._read(series, index - need + 1, index + 1, located.forming)
        ):
            return Result(UNKNOWN, pause)
        held = patterns.holds(name, series, index)
        if isinstance(held, Why):
            return Result(UNKNOWN, held)
        return Result(tri(held), None, name)

    # -- a plan --
    def judge(self, plan: Plan) -> Outcome:
        early = self._early(plan)
        if early is not None:
            return early
        results = {line.id: self.clause(line) for line in plan.clauses}
        state, whys = _walk(plan.root, results)
        if state is UNKNOWN:
            return Outcome(NOT_CHECKED, _unique(whys))
        decided = [found for found in results.values() if found.tri is not UNKNOWN]
        forming = any(found.forming for found in decided)
        repaired = any(found.repaired for found in decided)
        if state is FALSE:
            return Outcome(MISS, (), None, None, repaired, forming)
        values = {
            line.id: self._shown(line, results[line.id])
            for line in plan.clauses
            if results[line.id].tri is not UNKNOWN
        }
        return Outcome(HIT, (), values, self._rank(plan), repaired, forming)

    def _shown(self, line: Clause, found: Result) -> dict:
        right = None
        if line.right is not None and found.right is not None:
            right = show(found.right, line.right.unit)
        return {"l": show(found.left, line.left.unit), "r": right, "forming": found.forming}

    def _rank(self, plan: Plan) -> Number | None:
        if plan.rank is None:
            return None
        found = self.value(plan.rank)
        return None if isinstance(found, Why) else found

    def _early(self, plan: Plan) -> Outcome | None:
        """NOT_DUE or NO_AUCTION when the instrument is not to be judged at this boundary."""
        subject, ctx = self.subject, self.ctx
        waiting = subject.auction_end is not None and not subject.printed
        if plan.on == "after_close":
            days = subject.series.get(DAY)
            found = base_index(days, ctx.boundary) if days is not None else None
            if found is None or found[1] or int(days.session[found[0]]) != ctx.session:
                return Outcome(NOT_DUE)
            return Outcome(NOT_CHECKED, (Why(NO_AUCTION),)) if waiting else None
        if waiting and ctx.boundary == subject.auction_end:
            return Outcome(NOT_CHECKED, (Why(NO_AUCTION),))
        series = subject.series.get(plan.tf)
        if series is None or not ends_at(series, ctx.boundary):
            return Outcome(NOT_DUE)
        return None


def judge(plan: Plan, subject: Subject, ctx: Context, memo: dict | None = None) -> Outcome:
    """What ``plan`` says about ``subject`` at ``ctx.boundary``."""
    return Reading(subject, ctx, memo).judge(plan)


def shortfall(plan: Plan, subject: Subject) -> dict[TF, tuple[int, int]]:
    """Timeframe -> (candles needed, candles held) for each the plan is not yet ready on."""
    short = {}
    for tf, need in plan.need.items():
        series = subject.series.get(tf)
        have = len(series) if series is not None else 0
        if have < need:
            short[tf] = (need, have)
    return short


def ranked(hits: Iterable[tuple[str, Outcome]], order: str) -> list[str]:
    """Symbols of ``hits`` by their rank value, ties by symbol ascending, unranked last.

    ``order`` is "desc" (highest first) or "asc". Exact numbers and floats sort together by their
    exact value.
    """
    known, unknown = [], []
    for symbol, outcome in hits:
        if outcome.rank is None:
            unknown.append(symbol)
        else:
            exact = Fraction(outcome.rank)
            known.append((-exact if order == "desc" else exact, symbol))
    known.sort()
    return [symbol for _, symbol in known] + sorted(unknown)


__all__ = [
    "ENGINE_VERSION",
    "Context",
    "Reading",
    "Subject",
    "judge",
    "ranked",
    "shortfall",
    "show",
]
