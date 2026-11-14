"""The daily value of a tab, and the index it is held against (M7.30, M7.31).

The Performance view draws what a tab was worth on each day beside the net money put in, gives its
time-weighted return and compares its return with the same money put into an index. All of it is
worked out here, on demand, from the entries and the closing prices; no value of a tab is ever
stored, so a back-dated entry corrects history wherever prices exist (design 5.6).

**The daily series.** The replayed ledger says how many units each account held of each asset at the
end of every day. A day is *complete* when every one of those lines has a price for it:

    listed   the closing price of that very day
    mf       the newest NAV, if it is no more than 3 business days behind the day
    manual   the newest price point on or before the day

The value of a complete day is the sum of its lines, each rounded on its own (valuation.py), so it
is what the screen would have shown. A day on which something is held without a price is not
complete: it has no value, and its money joins the next complete day's period in the time-weighted
return. An asset sold since the last complete day needs no price: its sale is in the money. The
series starts at the first complete day; when that is not the first day with money, it says so.

**The index.** Each day's net money put in (in + fees - out) buys or sells units of the index at its
close on that day or the last one before it. The index portfolio's value is its units at the index
now, and the XIRR of the same money with that value is the benchmark's. Units are kept exactly (as
fractions) and a negative count is kept too, and flagged. The index's time-weighted return is its
close at the end of the tab's over its close at the start. Both indices are price indices.

**Beta, correlation, excess return (M7.31).** A tab's *day return* runs from one trading day's
closing value to the next trading day's (a weekday that is no holiday the caller named), money
that moved counted as the time-weighted return counts it. A return that would span a trading day
with no complete value is left out, and so is the tab's first day (it has no close before it).
Each is paired with the index's ``close / previous close - 1`` over the very same two days; the
last 60 pairs give beta and correlation (sudarshan.common.stats), shown from 20. The excess
return is the tab's time-weighted return less the index's over the same span, as shown.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from fractions import Fraction
from typing import NamedTuple

from sudarshan.common import stats

from . import performance, words
from .days import add_days, business_days_between, is_weekday
from .kinds import (
    BENCHMARK_LABELS,
    BENCHMARKS,
    HOLDING_KINDS,
    NAV_OLD_BUSINESS_DAYS,
    PCT_DECIMALS,
    STAT_DAYS,
    STAT_MIN_DAYS,
)
from .model import Ledger
from .money import format_pct, pct, round_div, value_paise
from .performance import Flow, Period, Point, Twr

# ---------------------------------------------------------------- closing prices


class Quotes(NamedTuple):
    """One asset's closing prices, oldest first, and which of them a day may use (its kind)."""

    kind: str
    days: tuple[int, ...]
    prices_u: tuple[int, ...]

    @classmethod
    def of(cls, row: Mapping) -> Quotes:
        """From ``{"kind": "listed" | "mf" | "manual", "prices": [[day, price_u], ...]}``; the
        prices in any order, and the later of two for one day wins.

        A kind that holds no value (a liability) or that nobody has heard of is refused: its price
        would be read by the rule of some other kind, and an old price would pass for today's. A
        price that is not above nothing is a gap in the data, not a price: the holding would show
        as lost on that day. It is left out, and the day takes the price the rules give it without.
        """
        if row["kind"] not in HOLDING_KINDS:
            raise ValueError(f"no prices are kept for the kind {row['kind']!r}")
        found = sorted((pair for pair in row["prices"] if pair[1] > 0), key=lambda pair: pair[0])
        return cls(row["kind"], tuple(day for day, _ in found), tuple(price for _, price in found))


def price_on(quotes: Quotes, day: int, holidays: Iterable[int] = ()) -> int | None:
    """The price a holding is valued at on ``day``, or None when its kind of asset has none."""
    return _price(quotes, day, sorted(holidays))


def _price(quotes: Quotes, day: int, shut: Sequence[int]) -> int | None:
    """``price_on`` with the holidays already sorted, so that a long history looks up only the
    few that fall between a NAV and the day instead of reading the whole calendar every time."""
    at = bisect_right(quotes.days, day) - 1
    if at < 0:
        return None
    priced = quotes.days[at]
    if quotes.kind == "listed" and priced != day:
        return None
    if quotes.kind == "mf":
        near = shut[bisect_right(shut, priced) : bisect_right(shut, day)]
        if business_days_between(priced, day, near) > NAV_OLD_BUSINESS_DAYS:
            return None
    return quotes.prices_u[at]


# ---------------------------------------------------------------- the daily series


class SeriesPoint(NamedTuple):
    """One complete day (types.ts ``SeriesPoint``)."""

    day: int
    value_paise: int
    net_put_in_paise: int

    def as_dict(self) -> dict:
        return self._asdict()


@dataclass(frozen=True, slots=True)
class Daily:
    """A tab day by day, from its first money to the day asked for."""

    #: What the time-weighted return reads: each complete day, and each day money moved on.
    points: tuple[Point, ...] = ()
    #: The complete days.
    series: tuple[SeriesPoint, ...] = ()
    #: The value before the first point: 0 when the series begins with the tab (so that its first
    #: day's own return counts), else not known.
    opening_paise: int | None = None
    #: Where the series begins, when that needs saying; or that it cannot begin yet.
    words: str | None = None

    def twr(self) -> Twr:
        return performance.twr(self.points, self.opening_paise)

    def span(self) -> tuple[int, int] | None:
        """The days the time-weighted return runs from and to; None without a period."""
        found = performance.periods(self.points, self.opening_paise)
        return (found[0].start, found[-1].day) if found else None


def _value(
    lines: Mapping[tuple[int, int], int],
    quotes: Mapping[int, Quotes],
    day: int,
    shut: Sequence[int],
) -> int | None:
    """The sum of the lines at the day's prices, or None when any of them has none."""
    total = 0
    priced: dict[int, int] = {}
    for (_, asset), qty_u in lines.items():
        if asset not in priced:
            found = quotes.get(asset)
            price = None if found is None else _price(found, day, shut)
            if price is None:
                return None
            priced[asset] = price
        total += value_paise(qty_u, priced[asset])
    return total


def daily(
    ledger: Ledger,
    quotes: Mapping[int, Quotes],
    last_day: int,
    holidays: Iterable[int] = (),
) -> Daily:
    """The days of a replayed tab from its first day with money to ``last_day``.

    ``quotes`` are the closing prices by asset id; an asset with none, or none that day, leaves the
    day incomplete. ``holidays`` are the exchange's, for the age of a NAV.
    """
    flows = {flow.day: flow for flow in performance.flows_of(ledger) if flow.day <= last_day}
    if not flows:
        return Daily()
    first = min(flows)
    changes = sorted(
        (
            (move.day, move.account_id, move.asset_id, move.qty_u)
            for move in ledger.journal
            if move.asset_id is not None and move.qty_u and move.day <= last_day
        ),
        key=lambda change: change[0],
    )
    shut = sorted(holidays)
    lines: dict[tuple[int, int], int] = {}
    points: list[Point] = []
    series: list[SeriesPoint] = []
    net = reached = 0
    day = first
    while day <= last_day:
        while reached < len(changes) and changes[reached][0] <= day:
            _, account, asset, change = changes[reached]
            held = lines.get((account, asset), 0) + change
            if held:
                lines[account, asset] = held
            else:
                lines.pop((account, asset), None)
            reached += 1
        flow = flows.get(day)
        if flow is not None:
            net += flow.in_paise + flow.fee_paise - flow.out_paise
        value = _value(lines, quotes, day, shut)
        if value is not None:
            series.append(SeriesPoint(day, value, net))
        if flow is not None:
            points.append(Point(day, value, flow.in_paise, flow.out_paise, flow.fee_paise))
        elif value is not None:
            points.append(Point(day, value))
        day = add_days(day, 1)
    begins = series[0].day if series else None
    if begins is None:
        said = words.SERIES_WAITING
    else:
        said = words.series_from(begins) if begins > first else None
    return Daily(tuple(points), tuple(series), 0 if begins == first else None, said)


# ---------------------------------------------------------------- the index


@dataclass(frozen=True, slots=True)
class Benchmark:
    """The tab's money put into an index (types.ts ``BenchmarkFigure``)."""

    benchmark: str
    label: str
    value_paise: int | None
    xirr_pct: str | None
    twr_pct: str | None
    flagged: bool
    words: list[str]

    def as_dict(self) -> dict:
        return asdict(self)


def benchmark(
    name: str,
    flows: Iterable[Flow],
    closes: Iterable[tuple[int, int]],
    valuation_day: int,
    now_u: int | None = None,
    span: tuple[int, int] | None = None,
) -> Benchmark:
    """The same money, day for day, put into the index ``name``.

    ``closes`` are the index's closing levels as ``(day, price_u)``, in any order (the later of
    two for one day wins; a level not above nothing is a gap in the data and is left out).
    ``now_u`` is the level to value the units at (the latest close on or before ``valuation_day``
    when None or not above nothing) and ``span`` the days the tab's time-weighted return runs
    from and to.
    """
    label = BENCHMARK_LABELS[name]
    notes = [words.DIVIDENDS_NOT_INCLUDED]
    # Stable: of two closes for one day the later in the list stays last, and wins.
    levels = sorted((pair for pair in closes if pair[1] > 0), key=lambda pair: pair[0])
    if not levels:
        return Benchmark(name, label, None, None, None, True, [*notes, words.index_missing(label)])
    days = [day for day, _ in levels]

    def close_on(day: int) -> int | None:
        """The close on that day or the last one before it."""
        at = bisect_right(days, day) - 1
        return levels[at][1] if at >= 0 else None

    live = sorted(
        (flow for flow in flows if flow.day <= valuation_day and flow.moved), key=lambda f: f.day
    )
    units, short, early = Fraction(0), False, False
    for flow in live:
        net = flow.in_paise + flow.fee_paise - flow.out_paise
        if not net:
            continue
        close = close_on(flow.day)
        if close is None:
            early = True
            break
        units += Fraction(net, close)
        short = short or units < 0
    level = close_on(valuation_day) if now_u is None or now_u <= 0 else now_u
    value = xirr_pct = twr_pct = None
    if live and not early and level is not None:
        value = round_div(units.numerator * level, units.denominator)
        # A rate only once a year has passed and the money went both ways: else None.
        xirr_pct = performance.xirr(live, value, valuation_day).pct
    if span is not None and close_on(span[0]) and close_on(span[1]):
        # Both are above nothing, as every level kept is.
        twr_pct = pct(close_on(span[1]) - close_on(span[0]), close_on(span[0]))
    if early:
        notes.append(words.index_starts(label, days[0]))
    elif short:
        notes.append(words.INDEX_COULD_NOT_PAY)
    return Benchmark(name, label, value, xirr_pct, twr_pct, early or short, notes)


# ---------------------------------------------------------------- beta, correlation, excess


class DayReturn(NamedTuple):
    """A tab's growth from the close of one trading day (``start``) to the close of the next."""

    start: int
    day: int
    grown: int
    base: int

    @property
    def rate(self) -> Fraction:
        return Fraction(self.grown, self.base) - 1


def day_returns(periods: Iterable[Period], holidays: Iterable[int] = ()) -> list[DayReturn]:
    """One return for each trading day that follows a trading day with a complete value.

    ``periods`` are ``performance.periods`` of the tab, oldest first. A value on a day the
    exchange was shut (a fund's NAV on a Saturday) joins the next trading day's return. A return
    that spans a trading day without a complete value is left out, as is a day the series only
    begins on: neither is the move of one day.
    """
    shut = frozenset(holidays)
    found: list[DayReturn] = []
    start = last = None
    grown = base = 1
    for period in periods:
        if start is None or period.start != last:
            start, grown, base = period.start, 1, 1
        grown, base, last = grown * period.grown, base * period.base, period.day
        if is_weekday(period.day) and period.day not in shut:
            if business_days_between(start, period.day, shut) == 1:
                found.append(DayReturn(start, period.day, grown, base))
            start = None
    return found


@dataclass(frozen=True, slots=True)
class IndexStats:
    """What a ``BenchmarkFigure`` (types.ts) says beside the same-money figures."""

    beta: str | None = None
    correlation: str | None = None
    excess_pct: str | None = None
    stat_days: int = 0
    stat_note: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _session_after(day: int, count: int, shut: frozenset[int]) -> int:
    """The ``count``-th trading day after ``day``, by the weekdays and the holidays named."""
    while count > 0:
        day = add_days(day, 1)
        count -= is_weekday(day) and day not in shut
    return day


def _scaled(pct_text: str) -> int:
    """A percentage text of four decimals in ten-thousandths of a percent, exactly."""
    return int(Fraction(pct_text) * 10**PCT_DECIMALS)


def index_stats(
    name: str,
    returns: Sequence[DayReturn],
    closes: Iterable[tuple[int, int]],
    as_of_day: int,
    twr_pct: str | None = None,
    index_twr_pct: str | None = None,
    holidays: Iterable[int] = (),
) -> IndexStats:
    """Beta and correlation of the tab's day returns against the index ``name`` over the last
    ``STAT_DAYS`` days both have, and the tab's time-weighted return less the index's.

    A day counts only when the index closed on it and on the trading day before it, so both
    returns are of the same one day. Fewer than ``STAT_MIN_DAYS`` such days show nothing and say
    from when (``as_of_day`` is the day asked on). Texts of four decimals, like every figure here.
    """
    label, shut = BENCHMARK_LABELS[name], frozenset(holidays)
    levels = dict(sorted((pair for pair in closes if pair[1] > 0), key=lambda pair: pair[0]))
    days = list(levels)
    r_p: list[Fraction] = []
    r_m: list[Fraction] = []
    last = None
    for found in reversed(returns):
        at = bisect_right(days, found.start) - 1
        if found.day not in levels or at < 0:
            continue
        if business_days_between(days[at], found.day, shut) != 1:
            continue  # the index has no close for the day before: its move is of two days
        r_p.append(found.rate)
        r_m.append(Fraction(levels[found.day], levels[days[at]]) - 1)
        last = last or found.day
        if len(r_p) == STAT_DAYS:
            break
    r_p.reverse()
    r_m.reverse()
    excess = None
    if twr_pct is not None and index_twr_pct is not None:
        excess = format_pct(_scaled(twr_pct) - _scaled(index_twr_pct))
    got = stats.correlation_beta(r_p, r_m, STAT_MIN_DAYS, STAT_DAYS)
    if got["state"] == "short":
        ready = _session_after(as_of_day, STAT_MIN_DAYS - len(r_p), shut)
        return IndexStats(None, None, excess, len(r_p), words.stats_after(STAT_MIN_DAYS, ready))
    if got["state"] == "no_variation":
        return IndexStats(None, None, excess, len(r_p), words.stats_flat(label))
    beta = stats.fraction_str(got["beta"], PCT_DECIMALS)
    if got["correlation"] is None:
        return IndexStats(beta, None, excess, len(r_p), words.STATS_TAB_FLAT)
    correlation = stats.decimal_str(got["correlation"], PCT_DECIMALS)
    return IndexStats(beta, correlation, excess, len(r_p), words.stats_on(len(r_p), last))


# ---------------------------------------------------------------- the whole report


def compared(
    name: str,
    flows: Iterable[Flow],
    closes: Iterable[tuple[int, int]],
    valuation_day: int,
    now_u: int | None,
    span: tuple[int, int] | None,
    moves: Sequence[DayReturn],
    twr_pct: str | None,
    holidays: Iterable[int] = (),
) -> dict:
    """One ``BenchmarkFigure`` (types.ts): the same money in the index, and the statistics."""
    levels = list(closes)
    same = benchmark(name, flows, levels, valuation_day, now_u, span)
    figures = index_stats(name, moves, levels, valuation_day, twr_pct, same.twr_pct, holidays)
    return {**same.as_dict(), **figures.as_dict()}


def report(
    ledger: Ledger,
    valuation_day: int,
    value_now_paise: int,
    quotes: Mapping[int, Quotes],
    closes: Mapping[str, Iterable[tuple[int, int]]],
    now_u: Mapping[str, int] | None = None,
    holidays: Iterable[int] = (),
) -> dict:
    """A tab's ``PerformanceReport`` (types.ts) on ``valuation_day``, from the ledger replayed up
    to that day, the value now, the closing prices of its assets and the closes of each index."""
    flows = performance.flows_of(ledger)
    out = performance.summary(flows, value_now_paise, valuation_day)
    days = daily(ledger, quotes, valuation_day, holidays)
    span = days.span()
    shut = sorted(holidays)
    twr = days.twr()
    moves = day_returns(performance.periods(days.points, days.opening_paise), shut)
    out["twr"] = twr.as_dict()
    out["benchmarks"] = [
        compared(
            name, flows, closes.get(name, ()), valuation_day, (now_u or {}).get(name), span,
            moves, twr.pct, shut,
        )
        for name in BENCHMARKS
    ]  # fmt: skip
    out["series"] = [point.as_dict() for point in days.series]
    out["by_fy"] = [fy.as_dict() for fy in performance.by_financial_year(ledger)]
    out["income"] = performance.income_report(ledger, valuation_day)
    out["series_words"] = days.words
    return out
