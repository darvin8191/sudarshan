"""Equity curve, drawdown and the entry-time heat map (JA3, M14.3; design 5.3 and 5.2).

* Equity ``E_d`` is the sum of the net of trips closed on or before trading day ``d``; a day with
  no trades repeats the day before. A trip counts on the first trading day of the list on or
  after its ``close_day`` (one closed after the last listed day is not counted).
* The peak ``P_d`` is the highest equity so far and starts at 0, not at the first equity value, so
  a first trade that loses is a drawdown from the start.
* Drawdown ``D_d = E_d - P_d`` is never above zero. The maximum drawdown is the lowest ``D_d``,
  in paise and as a percentage of the capital base when one is set.
* Its duration is counted in trading days (positions in the day list, so a weekend is no extra
  day) from the peak that the trough fell from to the first day equity reaches that peak again.
  A peak of 0 that was never exceeded counts from the day before the list begins.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from sudarshan.journal.core import money

Trip = Mapping[str, Any]


def equity_curve(
    trips: Iterable[Trip], days: Sequence[int], capital_base_paise: int | None = None
) -> dict[str, Any]:
    """The daily equity points and the maximum drawdown of ``trips`` over ``days`` (ascending)."""
    order = list(days)
    if order != sorted(order) or len(set(order)) != len(order):
        raise ValueError("the trading days must be ascending and distinct")
    per_day = [0] * len(order)
    for trip in trips:
        close = int(trip["close_day"])
        slot = next((i for i, d in enumerate(order) if d >= close), None)
        if slot is not None:
            per_day[slot] += int(trip["net_paise"])

    points: list[dict[str, int]] = []
    equity = peak = 0
    peak_idx = -1  # -1: the opening 0, before the first day
    peak_idxs: list[int] = []
    for i, day in enumerate(order):
        equity += per_day[i]
        if equity > peak:
            peak, peak_idx = equity, i
        peak_idxs.append(peak_idx)
        points.append(
            {
                "day": day,
                "net_paise": per_day[i],
                "equity_paise": equity,
                "peak_paise": peak,
                "drawdown_paise": equity - peak,
            }
        )

    worst = min((p["drawdown_paise"] for p in points), default=0)
    out: dict[str, Any] = {
        "max_drawdown_paise": worst,
        "max_drawdown_pct": None,
        "peak_day": None,
        "trough_day": None,
        "recovered_day": None,
        "duration_days": None,
        "duration_state": "none",
    }
    if worst < 0:
        trough = next(i for i, p in enumerate(points) if p["drawdown_paise"] == worst)
        p_idx = peak_idxs[trough]
        peak_value = points[trough]["peak_paise"]
        recovered = next(
            (i for i in range(trough + 1, len(points)) if points[i]["equity_paise"] >= peak_value),
            None,
        )
        out["peak_day"] = order[p_idx] if p_idx >= 0 else None
        out["trough_day"] = order[trough]
        if recovered is None:
            out["duration_state"] = "still_in_drawdown"
        else:
            out["recovered_day"] = order[recovered]
            out["duration_days"] = recovered - p_idx
            out["duration_state"] = "recovered"
        if capital_base_paise:
            out["max_drawdown_pct"] = money.ratio_str(worst * 100, capital_base_paise, 2)
    elif capital_base_paise:
        out["max_drawdown_pct"] = money.ratio_str(0, 1, 2)
    return {"points": points, "drawdown": out}


def heat_map(trips: Iterable[Trip]) -> list[dict[str, int]]:
    """Net and count per (weekday, entry hour) in IST; weekday 0 is Monday. Empty cells are left
    out, cells come in (weekday, hour) order. The 09:15-09:59 cell is hour 9, 15:00-15:40 is 15."""
    cells: dict[tuple[int, int], dict[str, int]] = {}
    for trip in trips:
        stamp = datetime.fromtimestamp(int(trip["opened_ms"]) // 1000, tz=money.IST)
        key = (stamp.weekday(), stamp.hour)
        cell = cells.setdefault(
            key, {"weekday": key[0], "hour": key[1], "trips": 0, "net_paise": 0}
        )
        cell["trips"] += 1
        cell["net_paise"] += int(trip["net_paise"])
    return [cells[k] for k in sorted(cells)]
