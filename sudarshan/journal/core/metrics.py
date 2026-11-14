"""Journal metrics over closed trips (JA2, M14.3; design 5.2).

A trip is a dict: ``net_paise`` (gross minus charges: an estimate until the actual arrives),
optional ``gross_paise`` and ``charges_paise`` (default: net and 0), optional
``charges_actual_paise`` (None while the charges are an estimate) and ``charges_modelled`` (False
for a segment the estimate does not cover; default True), ``risk_paise`` (planned stop
distance x quantity, or None), ``open_day``, ``close_day``, ``hold_s``, ``strategy`` and
``strategy_override``, ``tags`` (names) and ``playbook`` (a name or None). Only closed trips are
passed in.

* win: net > 0; loss: net < 0; flat: net == 0 (outside the win rate; the code's word is "scratch",
  the screen's word is "Flat").
* a ratio with nothing to divide by has no number, only a state: never infinity, never zero.
* R is worked out only where a planned risk above zero is known; the count of trips without one
  is part of the answer.

All sums are of integers, all ratios of exact fractions, rounded half-up once at the end.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from fractions import Fraction
from typing import Any

from sudarshan.journal.core import kinds, money

Trip = Mapping[str, Any]
FLAT_PLACES = kinds.RATIO_PLACES


def net_of(trip: Trip) -> int:
    """The trip's net in paise."""
    return int(trip["net_paise"])


def _gross(trip: Trip) -> int:
    if trip.get("gross_paise") is not None:
        return int(trip["gross_paise"])
    return net_of(trip) + int(trip.get("charges_paise") or 0)


def strategy_of(trip: Trip) -> str:
    """The name a trip counts under: the owner's override, else the strategy label."""
    return trip.get("strategy_override") or trip.get("strategy") or "Manual"


def compute(trips: Iterable[Trip]) -> dict[str, Any]:
    """The metrics block of ``trips`` (see ``Metrics`` in kinds.py)."""
    rows = list(trips)
    nets = [net_of(t) for t in rows]
    wins = [n for n in nets if n > 0]
    losses = [n for n in nets if n < 0]
    flats = len(nets) - len(wins) - len(losses)
    gross = sum(_gross(t) for t in rows)
    charges = sum(int(t.get("charges_paise") or 0) for t in rows)
    win_sum, loss_sum = sum(wins), sum(losses)
    decided = len(wins) + len(losses)

    out: dict[str, Any] = {
        "trips": len(rows),
        "wins": len(wins),
        "losses": len(losses),
        "flats": flats,
        "net_paise": sum(nets),
        "gross_paise": gross,
        "charges_paise": charges,
    }
    if decided:
        out["win_rate"] = money.ratio_str(len(wins) * 100, decided, FLAT_PLACES["win_rate"])
        out["win_rate_state"] = "ok"
    else:
        out["win_rate"], out["win_rate_state"] = None, "no_trades"

    if not decided:
        out["profit_factor"], out["profit_factor_state"] = None, "no_trades"
    elif not losses:
        out["profit_factor"], out["profit_factor_state"] = None, "no_losing_trades"
    else:
        out["profit_factor"] = money.ratio_str(win_sum, -loss_sum, FLAT_PLACES["profit_factor"])
        out["profit_factor_state"] = "ok"

    out["expectancy_paise"] = money.div_half_up(sum(nets), len(rows)) if rows else None
    out["avg_win_paise"] = money.div_half_up(win_sum, len(wins)) if wins else None
    out["avg_loss_paise"] = money.div_half_up(loss_sum, len(losses)) if losses else None
    out["largest_win_paise"] = max(wins) if wins else None
    out["largest_loss_paise"] = min(losses) if losses else None

    if not decided:
        out["payoff"], out["payoff_state"] = None, "no_trades"
    elif not losses:
        out["payoff"], out["payoff_state"] = None, "no_losing_trades"
    elif not wins:
        out["payoff"], out["payoff_state"] = money.ratio_str(0, 1, FLAT_PLACES["payoff"]), "ok"
    else:
        avg_win = Fraction(win_sum, len(wins))
        avg_loss = Fraction(-loss_sum, len(losses))
        out["payoff"] = money.fraction_str(avg_win / avg_loss, FLAT_PLACES["payoff"])
        out["payoff_state"] = "ok"

    known = [(net_of(t), int(t["risk_paise"])) for t in rows if (t.get("risk_paise") or 0) > 0]
    out["r_known"] = len(known)
    if known:
        total = sum((Fraction(n, r) for n, r in known), Fraction(0))
        out["r_total"] = money.fraction_str(total, FLAT_PLACES["r"])
        out["r_avg"] = money.fraction_str(total / len(known), FLAT_PLACES["r"])
    else:
        out["r_total"], out["r_avg"] = None, None

    win_gross = sum(_gross(t) for t in rows if net_of(t) > 0)
    if win_gross > 0:
        out["charges_impact_pct"] = money.ratio_str(charges * 100, win_gross, FLAT_PLACES["pct"])
    else:
        out["charges_impact_pct"] = None
    actual = [t.get("charges_actual_paise") is not None for t in rows]
    modelled = [t.get("charges_modelled", True) is not False for t in rows]
    out["charges_estimated"] = any(m and not a for m, a in zip(modelled, actual, strict=True))
    out["charges_not_estimated"] = sum(
        1 for m, a in zip(modelled, actual, strict=True) if not m and not a
    )
    return out


def r_multiple(trip: Trip) -> str | None:
    """R of one trip as a 4-decimal string, or None where no planned risk is known."""
    risk = trip.get("risk_paise") or 0
    if risk <= 0:
        return None
    return money.ratio_str(net_of(trip), int(risk), FLAT_PLACES["r"])


def hold_bucket(trip: Trip) -> str:
    """The hold-time bucket: by seconds within a day, by calendar days across days."""
    open_day, close_day = int(trip["open_day"]), int(trip["close_day"])
    if close_day != open_day:
        days = money.calendar_days_between(open_day, close_day)
        return "1_5d" if days <= 5 else "over_5d"
    held = int(trip["hold_s"])
    if held < 300:
        return "under_5m"
    if held < 1800:
        return "5_30m"
    if held < 7200:
        return "30m_2h"
    return "over_2h"


def hold_buckets(trips: Iterable[Trip]) -> list[dict[str, Any]]:
    """Every bucket in order, with its trips, wins and net (empty buckets included)."""
    acc = {b: {"bucket": b, "trips": 0, "wins": 0, "net_paise": 0} for b in kinds.HOLD_BUCKETS}
    for t in trips:
        row = acc[hold_bucket(t)]
        row["trips"] += 1
        row["wins"] += 1 if net_of(t) > 0 else 0
        row["net_paise"] += net_of(t)
    return list(acc.values())


def _periods(trips: Iterable[Trip], key_of) -> list[dict[str, Any]]:
    acc: dict[str, dict[str, Any]] = {}
    for t in trips:
        key = key_of(int(t["close_day"]))
        row = acc.setdefault(key, {"key": key, "trips": 0, "wins": 0, "net_paise": 0})
        row["trips"] += 1
        row["wins"] += 1 if net_of(t) > 0 else 0
        row["net_paise"] += net_of(t)
    return [acc[k] for k in sorted(acc)]


def _week_key(day: int) -> str:
    iso = money.to_date(day).isocalendar()
    return f"{iso.year}W{iso.week:02d}"


def calendar(trips: Iterable[Trip]) -> dict[str, list[dict[str, Any]]]:
    """Net, trips and wins per close day, ISO week (Monday to Sunday) and month, ascending."""
    rows = list(trips)
    return {
        "days": _periods(rows, str),
        "weeks": _periods(rows, _week_key),
        "months": _periods(rows, lambda d: str(d // 100)),
    }


def blocks(trips: Iterable[Trip], by: str) -> list[dict[str, Any]]:
    """The metrics block per strategy, tag or playbook, by name.

    A trip counts under its override else its strategy; under each of its tags; under its
    playbook. A trip with no tag or playbook is left out of that view.
    """
    groups: dict[str, list[Trip]] = {}
    for t in trips:
        if by == "strategy":
            names = [strategy_of(t)]
        elif by == "tag":
            names = list(dict.fromkeys(t.get("tags") or []))
        elif by == "playbook":
            names = [t["playbook"]] if t.get("playbook") else []
        else:
            raise ValueError(f"unknown grouping: {by!r}")
        for name in names:
            groups.setdefault(name, []).append(t)
    return [{"name": n, "metrics": compute(groups[n])} for n in sorted(groups)]


def open_pnl_paise(side: str, qty: int, entry_avg_u: int, ltp_u: int) -> int:
    """The live P&L of an open trip: ``(ltp - entry) x qty``, negated for a short, in paise.

    Exact in 10^-4 rupee, then half-up to the paisa (a tie goes away from zero); the phone's
    ``useTripLive`` does the same sum from ``metrics.json``.
    """
    if side not in kinds.SIDES:
        raise ValueError(f"unknown side: {side!r}")
    diff = (ltp_u - entry_avg_u) * qty
    return money.u_to_paise(diff if side == "long" else -diff)
