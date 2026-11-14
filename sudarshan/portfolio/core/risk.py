"""Risk and X-ray of a tab (M7.32; design p7 4.7): how far it fell, how much it swings, what
that swing earned, and where it is concentrated.

Pure: every input is an argument and every figure an answer; nothing is read, logged or timed.
The arithmetic is exact fractions (sudarshan.common.stats) with one square root at the end of a
figure, and every figure is text of four decimals, as every percentage of the portfolio is.

* **Days.** The figures rest on the tab's day returns (history.day_returns): one for each
  trading day that follows a trading day with a complete value. With fewer than 60 of them every
  figure is withheld and says how many more it needs.
* **Largest fall.** On the time-weighted index (1 at the start, times each period's growth), so
  money put in is never a recovery and money taken out never a fall: ``(peak - low) / peak`` at
  its deepest, with the day of the peak, of the low, and of the first close back at the peak.
  Every period counts here, also one that spans days without a complete value.
* **Volatility** is the population deviation of the day returns times the square root of 252.
* **Sharpe** is ``(annualised TWR - risk-free rate) / volatility``; **Sortino** the same over the
  downside deviation (a day at or above 0 counts as 0, and still counts as a day); **Calmar** is
  the annualised TWR over the largest fall. The annualised TWR is the one the Performance view
  shows, so all three wait for a year of history: annualising 60 days misleads.
* **Concentration.** Each holding's share of what is held, and each sector tag's; a share above
  the owner's limit is flagged in words. Holdings with no sector tag are a line of their own, not
  a sector. What is owed (a value not above nothing) is no holding here.
* **Overlap** of two tabs is the sum, over the instruments both hold, of the smaller of the two
  weights. Direct holdings only: a mutual fund is one instrument, not what it holds.
* **Emergency fund.** Cash-like value over the owner's monthly expenses, against his target.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from fractions import Fraction
from typing import NamedTuple

from sudarshan.common import stats

from . import words
from .history import day_returns
from .kinds import PCT_DECIMALS, STAT_DAYS
from .money import pct
from .performance import Period, Point

#: Trading days in a year, for turning a daily deviation into a yearly one.
YEAR_SESSIONS = 252
#: The fewest day returns a figure is shown from (design p7 4.7: 60 complete days).
MIN_DAYS = STAT_DAYS
#: How many bars the concentration lists show; a share over its limit is always listed.
TOP = 5
#: What the owner's settings are until he changes them (design p7, owner decision 12).
DEFAULTS = {
    "risk_free_bp": 650,
    "max_holding_pct": "15.0000",
    "max_sector_pct": "35.0000",
    "monthly_expenses_paise": None,
    "months_target": 6,
}


class Holding(NamedTuple):
    """One instrument as a tab holds it: ``key`` is what makes it the same instrument in another
    tab, ``sector`` its sector tag or None."""

    key: str
    label: str
    value_paise: int
    sector: str | None = None


def _text(value: Fraction) -> str:
    return stats.fraction_str(value, PCT_DECIMALS)


def _root_text(square: Fraction, negative: bool = False) -> str:
    root = stats.sqrt_ratio(square)
    return stats.decimal_str(root.copy_negate() if negative else root, PCT_DECIMALS)


def _stat(value: str | None = None, note: str | None = None) -> dict:
    return {"value": value, "note": note}


# ---------------------------------------------------------------- the series figures


def fall(periods: Sequence[Period]) -> stats.Drawdown:
    """The largest fall of the tab's time-weighted index over ``periods`` (oldest first)."""
    if not periods:
        return stats.Drawdown(Fraction(0), None, None, None)
    return stats.drawdown(periods[0].start, ((p.day, p.grown, p.base) for p in periods))


def _ratio(excess: Fraction, yearly_variance: Fraction, flat_note: str) -> dict:
    """``excess / sqrt(yearly_variance)`` with its sign; no figure over a deviation of 0."""
    if yearly_variance == 0:
        return _stat(note=flat_note)
    return _stat(_root_text(excess * excess / yearly_variance, excess < 0))


def series_figures(
    periods: Sequence[Period],
    annualised_pct: str | None,
    risk_free_bp: int,
    holidays: Iterable[int] = (),
) -> dict:
    """``days``, ``drawdown``, ``volatility``, ``sharpe``, ``sortino`` and ``calmar`` of
    ``RiskFigures`` (types.ts) from the tab's TWR periods, the annualised TWR the Performance
    view shows (None under a year) and the risk-free rate in basis points a year."""
    rates = [found.rate for found in day_returns(periods, holidays)]
    out: dict = {"days": len(rates)}
    if len(rates) < MIN_DAYS:
        wait = words.risk_days_more(MIN_DAYS, len(rates))
        out["drawdown"] = {"pct": None, "peak_day": None, "trough_day": None,
                           "recovered_day": None, "note": wait}  # fmt: skip
        out.update(
            {name: _stat(note=wait) for name in ("volatility", "sharpe", "sortino", "calmar")}
        )
        return out
    fell = fall(periods)
    out["drawdown"] = {"pct": _text(fell.depth * 100), "peak_day": fell.peak_day,
                       "trough_day": fell.trough_day, "recovered_day": fell.recovered_day,
                       "note": None if fell.depth else words.RISK_NO_FALL}  # fmt: skip
    yearly = stats.variance(rates) * YEAR_SESSIONS
    out["volatility"] = _stat(_root_text(yearly * 100 * 100))
    if annualised_pct is None:
        out.update(
            {name: _stat(note=words.RISK_NEEDS_YEAR) for name in ("sharpe", "sortino", "calmar")}
        )
        return out
    annual = Fraction(annualised_pct) / 100
    excess = annual - Fraction(risk_free_bp, 10_000)
    out["sharpe"] = _ratio(excess, yearly, words.RISK_NO_SWING)
    out["sortino"] = _ratio(
        excess, stats.downside_variance(rates) * YEAR_SESSIONS, words.RISK_NO_DOWN_DAY
    )
    out["calmar"] = (
        _stat(_text(annual / fell.depth)) if fell.depth else _stat(note=words.RISK_NO_FALL)
    )
    return out


def together(tabs: Sequence[Sequence[Point]]) -> tuple[list[Point], int | None]:
    """Several tabs' points (``history.Daily.points``, each oldest first) as one tab's, and the
    value before the first of them (``performance.periods`` reads both).

    A day is complete only when every tab that has begun is: a tab adds nothing before its first
    day with money, and after it a day it has no complete value for leaves the whole day without
    one. The money of a day is the sum of the tabs'.
    """
    live = [{point.day: point for point in points} for points in tabs if points]
    if not live:
        return [], None
    firsts = [min(by_day) for by_day in live]
    found: list[Point] = []
    for day in sorted(set().union(*live)):
        value: int | None = 0
        money_in = money_out = fees = 0
        for by_day, first in zip(live, firsts, strict=True):
            if day < first:
                continue
            point = by_day.get(day)
            if point is None or point.value_paise is None or value is None:
                value = None
            else:
                value += point.value_paise
            if point is not None:
                money_in += point.in_paise
                money_out += point.out_paise
                fees += point.fee_paise
        found.append(Point(day, value, money_in, money_out, fees))
    return found, 0 if found[0].value_paise is not None else None


# ---------------------------------------------------------------- concentration and overlap


def _held(holdings: Iterable[Holding]) -> list[Holding]:
    """What is held: one line per instrument, values above nothing only."""
    merged: dict[str, Holding] = {}
    for each in holdings:
        if each.value_paise > 0:
            have = merged.get(each.key)
            merged[each.key] = each._replace(
                value_paise=each.value_paise + (have.value_paise if have else 0)
            )
    return list(merged.values())


def _bars(sums: Mapping[str, int], total: int, limit: Fraction) -> list[dict]:
    """The largest ``TOP`` shares and any further one over the limit, largest first (a tie by
    name), each ``{"label", "weight_pct", "over_limit"}``."""
    rows = sorted(sums.items(), key=lambda item: (-item[1], item[0]))
    bars = [
        {"label": label, "weight_pct": pct(value, total), "over_limit": value * 100 > limit * total}
        for label, value in rows
    ]
    return [bar for at, bar in enumerate(bars) if at < TOP or bar["over_limit"]]


def concentration(holdings: Iterable[Holding], max_holding_pct: str, max_sector_pct: str) -> dict:
    """``RiskConcentration`` (types.ts): the shares of holdings and of sector tags, the share
    with no sector tag, and a sentence for each share above its limit."""
    held = _held(holdings)
    total = sum(each.value_paise for each in held)
    if not total:
        return {"holdings": [], "sectors": [], "untagged_pct": None, "flags": []}
    names: dict[str, int] = {}
    sectors: dict[str, int] = {}
    untagged = 0
    for each in held:
        names[each.label] = names.get(each.label, 0) + each.value_paise
        if each.sector:
            sectors[each.sector] = sectors.get(each.sector, 0) + each.value_paise
        else:
            untagged += each.value_paise
    by_name = _bars(names, total, Fraction(max_holding_pct))
    by_sector = _bars(sectors, total, Fraction(max_sector_pct))
    flags = [
        words.risk_over(bar["label"], bar["weight_pct"], max_holding_pct, sector=False)
        for bar in by_name
        if bar["over_limit"]
    ] + [
        words.risk_over(bar["label"], bar["weight_pct"], max_sector_pct, sector=True)
        for bar in by_sector
        if bar["over_limit"]
    ]
    if untagged:
        flags.append(words.risk_untagged(pct(untagged, total)))
    return {"holdings": by_name, "sectors": by_sector, "untagged_pct": pct(untagged, total),
            "flags": flags}  # fmt: skip


def overlap(tab_id: int, mine: Iterable[Holding], theirs: Iterable[Holding]) -> dict:
    """``RiskOverlap`` (types.ts) of this tab with the tab ``tab_id``: the sum, over the
    instruments both hold, of the smaller of the two weights; the names by that share."""
    a, b = _held(mine), _held(theirs)
    total_a, total_b = sum(h.value_paise for h in a), sum(h.value_paise for h in b)
    other = {each.key: each for each in b}
    shared: list[tuple[Fraction, str]] = []
    for each in a:
        if each.key in other:
            smaller = min(
                Fraction(each.value_paise, total_a), Fraction(other[each.key].value_paise, total_b)
            )
            shared.append((smaller, each.label))
    shared.sort(key=lambda item: (-item[0], item[1]))
    weight = sum((share for share, _ in shared), Fraction(0))
    return {"tab_id": tab_id, "weight_pct": _text(weight * 100),
            "names": [name for _, name in shared]}  # fmt: skip


# ---------------------------------------------------------------- the emergency fund


def emergency(liquid_paise: int, monthly_expenses_paise: int | None, months_target: int) -> dict:
    """``RiskEmergency`` (types.ts): how many months of the owner's expenses the cash-like value
    covers, against his target. Without his expenses there is no figure, and it says so."""
    if not monthly_expenses_paise or monthly_expenses_paise < 0:
        return {"months": None, "target_months": months_target, "note": words.RISK_SET_EXPENSES}
    months = Fraction(max(0, liquid_paise), monthly_expenses_paise)
    return {"months": _text(months), "target_months": months_target,
            "note": words.risk_months(_text(months), months_target, months >= months_target)}  # fmt: skip
