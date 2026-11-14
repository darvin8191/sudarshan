"""``charges_reconcile``: the journal's estimate of charges against Upstox's own, month by month
for a financial year (P7 J4, M7.42; design p7 4.6 and 8). Behind ``SUDARSHAN_TAX``.

For each month of the year up to the one running, and each of Upstox's segments the journal holds
live orders of in it: the estimate is the sum of those orders' estimates (the same
``core/charges.py`` the trade sheet uses, on the plan in the settings), the actual is the month's
row the catch-up keeps (``jobs/charges.py``), and ``core/reconcile.py`` compares them. A month
whose row is not there yet says why: it has not ended, or Upstox has not been asked or has not
answered. Nothing is read of Upstox here.

Upstox's month is the whole account's; the estimate is of the orders the journal holds. So a
month the journal does not cover whole (``actual.covers``: its first month, or one with a trading
day it never checked) is not compared at all: no actual, no difference, no "Check", and a
sentence saying why. Advice built on such a gap would send the owner to the wrong place.
"""

from __future__ import annotations

import calendar
from typing import Any

from .. import actual
from ..core import charges as charges_core
from ..core import kinds, reconcile
from ..core.money import ist_day
from ..errors import ApiError
from ..jobs.charges import financial_year, month_due
from ..schedule import schedule_for
from . import op
from .common import OWNER, need, whole, work
from .settings_ops import rate_card

SWITCHED_OFF = (
    "Comparing estimated charges with Upstox's own is switched off on the server. It comes with "
    "the tax working paper."
)
NOTHING = "The journal holds no trades of this year, so there is nothing to compare yet."
SEGMENTS = ("EQ", "FO", "COM", "CD")


def months_of(fy: int, today: int) -> list[tuple[int, int, int, str]]:
    """The months of the financial year starting in ``fy`` up to today's: (YYYYMM, first day,
    last day, label)."""
    out = []
    for n in range(12):
        year, month = (fy, 4 + n) if n < 9 else (fy + 1, n - 8)
        first = year * 10000 + month * 100 + 1
        if first > today:
            break
        last = first - 1 + calendar.monthrange(year, month)[1]
        out.append((year * 100 + month, first, last, f"{calendar.month_name[month]} {year}"))
    return out


def month_estimates(fills: list[dict[str, Any]], refs: dict[str, dict[str, Any]],
                    plan: str) -> dict[str, dict[str, Any]]:  # fmt: skip
    """The estimate of a month's fills by Upstox's segment: the parts added up over the orders
    (an order is the fills of one order id on one day), how many orders, and whether every
    order is in a segment the card covers."""
    orders: dict[tuple[str, int, str], list[dict[str, Any]]] = {}
    for fill in fills:
        segment = actual.SEGMENT_OF.get(fill["exchange"])
        if segment:
            orders.setdefault((segment, int(fill["day"]), str(fill["order_id"])), []).append(fill)
    out: dict[str, dict[str, Any]] = {}
    for (segment, _, order_id), rows in orders.items():
        surface = (refs.get(order_id) or {}).get("surface")
        parts, modelled = charges_core.estimate_order([{**f, "surface": surface} for f in rows],
                                                      plan)  # fmt: skip
        found = out.setdefault(segment, {"parts": dict.fromkeys(parts, 0), "orders": 0,
                                         "modelled": False})  # fmt: skip
        for name, paise in parts.items():
            found["parts"][name] += paise
        found["orders"] += 1
        # A segment is compared when the card covers any order of it; the orders it does not
        # cover (BSE shares beside NSE ones) add nothing to the estimate, and the gap says so.
        found["modelled"] = found["modelled"] or modelled
    return out


def reconcile_year(tx: Any, fy: int, today: int, sched: Any) -> dict[str, Any]:
    plan = tx.settings().get("charges_plan") or charges_core.DEFAULT_PLAN
    plan = plan if plan in kinds.CHARGE_PLANS else charges_core.DEFAULT_PLAN
    months = []
    for key, first, last, label in months_of(fy, today):
        fills = tx.fills_between(actual.LIVE, first, last)
        refs = tx.order_refs(actual.LIVE, {str(f["order_id"]) for f in fills}) if fills else {}
        estimates = month_estimates(fills, refs, plan)
        due = month_due(first, today)
        waiting = reconcile.NOT_READ if due else reconcile.NOT_ENDED
        kept = {s: tx.upstox_charges_get(s, first, last) for s in SEGMENTS if s in estimates}
        covered = True
        if any(actual.ready(row) for row in kept.values()):
            covered = actual.covers(tx, actual.trading_days_of(sched, first, last))
            if not covered:
                waiting = reconcile.NOT_WHOLE
        segments = []
        for segment in SEGMENTS:
            if segment not in estimates:
                continue
            found = estimates[segment]
            row = kept[segment]
            ok = covered and actual.ready(row)
            parts = actual.parts_of(row) if ok else None
            total = None
            if ok:
                total = row.get("total_paise")
                if total is None:
                    total = sum(int(v) for v in parts.values() if v is not None)
            segments.append({
                **reconcile.segment_row(segment, found["parts"], found["orders"],
                                        found["modelled"], parts, total, waiting),
                "read_at": row.get("read_at") if ok else None,
            })  # fmt: skip
        months.append(reconcile.month_row(key, label, segments, waiting))
    last_day = (fy + 1) * 10000 + 331
    return {
        "fy": fy,
        "fy_label": f"FY {fy}-{(fy + 1) % 100:02d}",
        "running": today <= last_day,
        "months": months,
        "year": reconcile.year_row(months),
        "note": None if any(m["orders"] for m in months) else NOTHING,
        "rate_card": rate_card(today, plan),
    }


@op("charges_reconcile")
async def charges_reconcile(ctx, args):
    if not getattr(ctx.settings, "tax", False):
        raise ApiError(501, "not_ready", SWITCHED_OFF)
    store = need(ctx)
    today = ist_day(ctx.clock())
    this = financial_year(today)
    fy = whole(args, "fy", low=2000, high=this, default=this)
    sched = schedule_for(ctx)
    return await work(lambda: store.read(OWNER, lambda tx: reconcile_year(tx, fy, today, sched)))
