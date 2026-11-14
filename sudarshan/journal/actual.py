"""Upstox's actual charges on the journal's orders and trades (P7 J3, M14.2; design p7 4.6).

Upstox says what it charged for a segment and a period. The catch-up keeps one row a day and one
a month (``jobs/charges.py``); this module turns a row into each order's share of it
(``charges_order``, through the pure ``core/spread.py``) and each order's share into each trade's
(``round_trip.charges_actual_paise``, ``charges_actual_basis``, ``charges_actual_parts``), all
inside the caller's transaction.

* **A day** (``spread_day``): the day's row of a segment, shared among that day's live orders of
  the segment. What Upstox's total holds beyond the parts it names is shared too (``unlisted``),
  so the orders of the day add up to Upstox's total to the paisa.
* **A month** (``spread_month``), the fallback for days Upstox never answered day by day: once
  every day of a month that is not answered has been asked ``GIVE_UP`` nights, the month's row
  less the answered days is shared among the orders of the days left, marked ``month``.
  Only for a month the journal covers whole (``covers``): Upstox's month is the whole account's,
  so in a month with a trading day the journal never checked (its first month, or a day it was
  down and the history did not rebuild) the trades keep the estimate and say why
  (``month_only``); two days of fills must never be given a whole month of charges.
* **A trade** (``apply_trips``) has an actual only when every one of its orders has one: an
  order's share is split among the trades that hold its fills by quantity, and the trade's net
  is then gross less the actual. Until then, and whenever an order of it loses its share, the
  trade keeps the estimate. A part Upstox left empty marks the trade's actual ``partial``.
* **After a replay** (``after_replay``): a day whose fills changed is shared again from the rows
  already kept (a late fill is an order with no share yet), and every trade the replay wrote is
  brought to what its orders say now; the replay itself only ever writes the estimate.

Only the live journal has actual charges: the sandbox has no broker to charge it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .core import charges as charges_core
from .core import spread as spread_core
from .core.money import u_to_paise, value_u
from .core.pairing import CASH_EXCHANGES, product_class
from .migrations import BASIS_DAY, BASIS_MONTH

LIVE = "live"
#: Upstox's report segment of an OpenAlgo exchange.
SEGMENT_OF = {"NSE": "EQ", "BSE": "EQ", "NFO": "FO", "BFO": "FO", "MCX": "COM", "CDS": "CD",
              "BCD": "CD"}  # fmt: skip
#: The nights a day is asked of Upstox before the month's row answers for it.
GIVE_UP = 7
#: On the trade sheet, for a trade that keeps the estimate because of it.
MONTH_ONLY = (
    "Upstox gave only the month's total, and the journal does not cover the whole month, so "
    "these charges stay an estimate."
)
#: A part of the spread and the column of ``upstox_charges`` that holds it.
COLUMN_OF = {
    "brokerage": "brokerage_paise",
    "gst": "gst_paise",
    "stt": "stt_paise",
    "stamp": "stamp_paise",
    "transaction": "transaction_paise",
    "clearing": "clearing_paise",
    "ipft": "ipft_paise",
    "sebi": "sebi_paise",
    "dp": "dp_paise",
    "others": "others_paise",
}
PART_LABELS = {
    "brokerage": "Brokerage",
    "gst": "GST",
    "stt": "STT",
    "stamp": "Stamp duty",
    "transaction": "Exchange transaction charge",
    "clearing": "Clearing charge",
    "ipft": "Investor protection fund",
    "sebi": "SEBI fee",
    "dp": "Depository charge",
    "others": "Other charges",
    "unlisted": "Not itemised by Upstox",
}


def ready(row: Mapping[str, Any] | None) -> bool:
    """Whether a charges row says anything: a row that is all zero or empty while the journal
    holds fills of its period is Upstox not having the figures yet, never "nothing charged"."""
    if row is None:
        return False
    return any(row.get(c) for c in ("total_paise", *COLUMN_OF.values()))


def parts_of(row: Mapping[str, Any]) -> dict[str, int | None]:
    """A charges row as the spread reads it: each part in paise or None, and ``unlisted``, what
    the row's total holds beyond the parts it names (absent when there is no total or no
    difference)."""
    out: dict[str, int | None] = {part: row.get(column) for part, column in COLUMN_OF.items()}
    total = row.get("total_paise")
    if total is not None:
        rest = int(total) - sum(int(v) for v in out.values() if v is not None)
        if rest:
            out["unlisted"] = rest
    return out


def month_of(day: int) -> tuple[int, int]:
    """The first and the last day of ``day``'s calendar month."""
    import calendar

    year, month = day // 10000, day // 100 % 100
    first = year * 10000 + month * 100 + 1
    return first, first - 1 + calendar.monthrange(year, month)[1]


def trading_days_of(plan: Any, first: int, last: int) -> list[int]:
    """The exchange's trading days of ``first`` to ``last``, from the service's calendar
    (``schedule_for``)."""
    from datetime import timedelta

    from .core.money import to_date, to_day

    out, cursor, end = [], to_date(first), to_date(last)
    while cursor <= end:
        if plan.trading(cursor):
            out.append(to_day(cursor))
        cursor += timedelta(days=1)
    return out


def covers(tx: Any, trading_days: Iterable[int]) -> bool:
    """Whether the live journal covers a period whole: every trading day of it, from the first,
    with no gap, was checked against Upstox's own list of the day's trades (the close check) or
    rebuilt from Upstox's history. A day with neither is a day the owner may have traded and the
    journal holds nothing of."""
    days = sorted(set(trading_days))
    return bool(days) and all(
        (tx.day_close_get(LIVE, d) or {}).get("reconciled_at") is not None for d in days
    )


def month_only(tx: Any, days: Iterable[int], segments: Iterable[str],
               whole: Any) -> bool:  # fmt: skip
    """Whether an order day of a trade is one Upstox answered for by its month only, in a month
    the journal does not cover whole: its own row was given up, the month's row is there, and
    ``whole(first, last)`` says no. Such a trade keeps the estimate (``MONTH_ONLY``)."""
    for day in sorted(set(days)):
        first, last = month_of(day)
        for segment in sorted(set(segments)):
            row = tx.upstox_charges_get(segment, day, day)
            if row is None or ready(row) or int(row.get("asked") or 0) < GIVE_UP:
                continue
            if ready(tx.upstox_charges_get(segment, first, last)) and not whole(first, last):
                return True
    return False


def orders_of(tx: Any, days: Iterable[int], segment: str, plan: str) -> list[dict[str, Any]]:
    """The live orders of ``days`` in a segment as the spread reads them: key (day, order id),
    the journal's estimate of the order, its turnover in paise, and what it sold out of the
    demat account (a delivery sale of a share), if anything."""
    out = []
    for day in sorted(set(days)):
        orders: dict[str, list[dict[str, Any]]] = {}
        for fill in tx.fills_of(LIVE, day=day):
            if fill["source"] != "withdrawn" and SEGMENT_OF.get(fill["exchange"]) == segment:
                orders.setdefault(str(fill["order_id"]), []).append(fill)
        refs = tx.order_refs(LIVE, set(orders))
        for order_id in sorted(orders):
            fills = orders[order_id]
            surface = (refs.get(order_id) or {}).get("surface")
            parts, _ = charges_core.estimate_order([{**f, "surface": surface} for f in fills], plan)
            first = fills[0]
            sold = (
                first["exchange"] in CASH_EXCHANGES
                and first["action"] == "SELL"
                and product_class(first["product"]) == "carry"
            )
            out.append({
                "key": (day, order_id),
                "estimate": parts,
                "turnover": u_to_paise(sum(value_u(int(f["qty"]), int(f["price_u"]))
                                           for f in fills)),
                "dp_group": (day, first["exchange"], first["symbol"]) if sold else None,
            })  # fmt: skip
    return out


def _keep(tx: Any, segment: str, shared: Mapping[str, Any], basis: str, now: int) -> set[str]:
    """Write a spread's answer as ``charges_order`` rows, a day at a time; the order ids."""
    by_day: dict[int, list[dict[str, Any]]] = {}
    extra = {"missing": shared["missing"], "partial": shared["partial"]}
    for (day, order_id), found in shared["orders"].items():
        by_day.setdefault(day, []).append({
            "order_id": order_id, "parts": {"parts": found["parts"], **extra},
            "total_paise": found["total"], "basis": basis,
        })  # fmt: skip
    for day, rows in by_day.items():
        tx.charges_orders_put(LIVE, day, segment, rows, now)
    return {order_id for _, order_id in shared["orders"]}


def spread_day(tx: Any, day: int, segment: str, plan: str, now: int) -> set[str]:
    """Share the day's kept row of a segment among the day's orders; the order ids written
    (none when the row is not ready or the day has no order of the segment)."""
    row = tx.upstox_charges_get(segment, day, day)
    if not ready(row):
        return set()
    orders = orders_of(tx, [day], segment, plan)
    if not orders:
        tx.charges_orders_put(LIVE, day, segment, [], now)
        return set()
    return _keep(tx, segment, spread_core.spread(parts_of(row), orders), BASIS_DAY, now)


def spread_month(tx: Any, day: int, segment: str, plan: str, now: int,
                 whole: bool | None = None) -> set[str]:  # fmt: skip
    """The month fallback for the month holding ``day`` (see the module's words); the order ids
    written, none while the month's row is not ready or a day of it is still being asked.

    ``whole`` is whether the journal covers the month whole (``covers``); when it does not,
    nothing is shared and the trades keep the estimate. None is for a caller without the
    calendar (a replay): the month answers again only where it has answered before, which it
    did only as a whole month."""
    first, last = month_of(day)
    month = tx.upstox_charges_get(segment, first, last)
    if not ready(month):
        return set()
    if whole is None:
        whole = BASIS_MONTH in tx.charges_order_days(LIVE, segment, first, last).values()
    if not whole:
        return set()
    traded = sorted({d for d, exchange in tx.fill_days(LIVE)
                     if first <= d <= last and SEGMENT_OF.get(exchange) == segment})  # fmt: skip
    rows = {d: tx.upstox_charges_get(segment, d, d) for d in traded}
    answered = [d for d in traded if ready(rows[d])]
    rest = [d for d in traded if not ready(rows[d])]
    if not rest or any(int((rows[d] or {}).get("asked") or 0) < GIVE_UP for d in rest):
        return set()
    # Each part the month names, less what the answered days took of it; a part the month left
    # empty stays empty. What the month's total then still holds goes in as ``unlisted``, so the
    # days left add up to the month's total less the answered days' totals, to the paisa.
    left: dict[str, int | None] = {}
    taken = [parts_of(rows[d]) for d in answered]
    for part, column in COLUMN_OF.items():
        if month.get(column) is None:
            left[part] = None
        else:
            left[part] = int(month[column]) - sum(int(t.get(part) or 0) for t in taken)
    if month.get("total_paise") is not None:
        spent = sum(
            int(rows[d]["total_paise"]) if rows[d].get("total_paise") is not None
            else sum(int(v) for v in t.values() if v is not None)
            for d, t in zip(answered, taken, strict=True)
        )  # fmt: skip
        rest_total = int(month["total_paise"]) - spent
        beyond = rest_total - sum(int(v) for v in left.values() if v is not None)
        if beyond:
            left["unlisted"] = beyond
    orders = orders_of(tx, rest, segment, plan)
    if not orders:
        return set()
    return _keep(tx, segment, spread_core.spread(left, orders), BASIS_MONTH, now)


def apply_trips(tx: Any, trip_ids: Iterable[int], now: int) -> int:
    """Bring each live trade's actual charges to what its orders' shares say now; returns how
    many trades have an actual."""
    count = 0
    for trip_id in sorted(set(trip_ids)):
        trip = tx.get("round_trip", trip_id)
        if trip is None or trip["mode"] != LIVE:
            continue
        legs = tx.legs_of(trip_id)
        order_ids = {str(leg["order_id"]) for leg in legs}
        shares = tx.charges_orders_of(LIVE, order_ids) if order_ids else {}
        mine: dict[tuple[str, int], int] = {}
        for leg in legs:
            key = (str(leg["order_id"]), int(leg["day"]))
            mine[key] = mine.get(key, 0) + int(leg["leg_qty"])
        values: dict[str, Any]
        if not mine or any(key not in shares for key in mine):
            if trip.get("charges_actual_paise") is None:
                continue  # an estimate still, as the replay left it
            values = {"charges_actual_paise": None, "charges_actual_basis": None,
                      "charges_actual_parts": None}  # fmt: skip
            est = int(trip.get("charges_est_paise") or 0)
            if trip["status"] == "closed" and trip.get("gross_paise") is not None:
                values["net_paise"] = int(trip["gross_paise"]) - est
        else:
            holders: dict[tuple[str, int], dict[int, int]] = {}
            for leg in tx.order_legs(LIVE, order_ids):
                key = (str(leg["order_id"]), int(leg["day"]))
                held = holders.setdefault(key, {})
                held[int(leg["trip_id"])] = held.get(int(leg["trip_id"]), 0) + int(leg["qty"])
            parts: dict[str, int] = {}
            missing: list[str] = []
            partial, bases = False, set()
            for key in sorted(mine):
                share = shares[key]
                kept = share["parts"] or {}
                took = spread_core.to_trips(kept.get("parts") or {}, holders[key])[trip_id]
                for part, paise in took.items():
                    parts[part] = parts.get(part, 0) + paise
                missing += [m for m in kept.get("missing") or [] if m not in missing]
                partial = partial or bool(kept.get("partial"))
                bases.add(share["basis"])
            total = sum(parts.values())
            values = {
                "charges_actual_paise": total,
                "charges_actual_basis": BASIS_MONTH if BASIS_MONTH in bases else BASIS_DAY,
                "charges_actual_parts": {"parts": parts, "missing": missing, "partial": partial},
            }
            if trip["status"] == "closed" and trip.get("gross_paise") is not None:
                values["net_paise"] = int(trip["gross_paise"]) - total
            count += 1
        if any(trip.get(k) != v for k, v in values.items()):
            tx.touch("round_trip", trip_id, values, now)
    return count


def trips_of_orders(tx: Any, order_ids: Iterable[str]) -> set[int]:
    return {int(leg["trip_id"]) for leg in tx.order_legs(LIVE, order_ids)}


def respread(tx: Any, day: int, plan: str, now: int) -> set[str]:
    """Share a day's kept rows again, every segment the day has fills of: from its own row,
    else through its month's once that answers for it. The order ids written."""
    written: set[str] = set()
    for segment in sorted({SEGMENT_OF[e] for d, e in tx.fill_days(LIVE)
                           if d == day and e in SEGMENT_OF}):  # fmt: skip
        if ready(tx.upstox_charges_get(segment, day, day)):
            written |= spread_day(tx, day, segment, plan, now)
        else:
            written |= spread_month(tx, day, segment, plan, now)
    return written


def after_replay(tx: Any, mode: str, days: Iterable[int], trip_ids: Iterable[int], plan: str,
                 now: int) -> None:  # fmt: skip
    """What a replay of the live journal leaves to do (see the module's words). A day with no
    kept row costs one read; a trade with no share and no actual costs two."""
    if mode != LIVE:
        return
    touched = set(trip_ids)
    for day in sorted(set(days)):
        # A day asked of Upstox has a row, answered or not (the month's answers for it later).
        if tx.rows("upstox_charges", order="segment", period_from=day, period_to=day):
            touched |= trips_of_orders(tx, respread(tx, day, plan, now))
    apply_trips(tx, touched, now)
