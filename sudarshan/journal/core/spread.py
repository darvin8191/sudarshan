"""Upstox's actual charges of a day, spread over that day's orders and then over the trades
(P7 J2, M14.2; design p7 4.6).

Upstox reports its charges for a segment and a period, part by part, never per order. This module
shares each part among the orders of the day and each order's share among the trades its fills
sit in, so that every sum made of the shares is Upstox's own figure to the paisa.

* **Weights.** A part is shared in proportion to what the journal's estimate says of the matching
  part for each order (``WEIGHT_OF``): brokerage on brokerage, GST on GST, STT on STT, stamp on
  stamp, SEBI's fee on SEBI's fee; the exchange's transaction charge, the clearing charge and the
  investor-protection fund all on the estimate's exchange charge; ``others`` (and ``unlisted``,
  what Upstox's total holds beyond the parts it names) on the estimate's total. The depository's
  charge (``dp``) is charged once for each share sold out of the demat account on a day, so it
  weighs equally on each such share and, within one, equally on its delivery sell orders
  (``dp_group``). Where every weight of a part is 0 (a segment the estimate does not model, a
  part the card has no row for) the orders' turnover is the weight; where even that is 0, the
  orders weigh the same.
* **Rounding.** Each share is floored and the paise left over go one each to the largest
  fractional parts, ties to the lower key (``largest_remainder``), so nothing is lost or made.
  A part below zero (a reversal) is shared the same way and keeps its sign.
* **A part Upstox left empty** (None) is skipped, never read as 0, and is named in ``missing``;
  the answer is ``partial`` when it is one of the parts every trade pays (``CORE_PARTS``), or the
  depository's charge on a day that has a delivery sale.
* **Order to trade** (``to_trips``): by the quantity of the order's fills in each trade, the same
  rounding. A reversal's order (SELL 130 against a long of 65) gives half to each.

No I/O, no clock: every input is an argument.
"""

from __future__ import annotations

from collections.abc import Hashable, Mapping, Sequence
from fractions import Fraction
from typing import Any

#: The parts of Upstox's charges, as the journal names them.
ACTUAL_PARTS = (
    "brokerage",
    "gst",
    "stt",
    "stamp",
    "transaction",
    "clearing",
    "ipft",
    "sebi",
    "dp",
    "others",
    "unlisted",
)
#: The part of the journal's estimate each actual part is weighed on (``dp`` has its own rule).
WEIGHT_OF = {
    "brokerage": "brokerage",
    "gst": "gst",
    "stt": "stt",
    "stamp": "stamp",
    "transaction": "exchange",
    "clearing": "exchange",
    "ipft": "exchange",
    "sebi": "sebi",
    "others": "total",
    "unlisted": "total",
}
#: The parts whose absence makes a day's actual incomplete.
CORE_PARTS = ("brokerage", "gst", "stt", "stamp", "transaction", "sebi")


def largest_remainder(amount: int, weights: Sequence[tuple[Hashable, Any]]) -> dict[Any, int]:
    """``amount`` (paise, may be below zero) shared by ``weights`` (key, weight at or above 0):
    floors, then one paisa each to the largest fractions, ties to the lower key. The shares add
    up to ``amount`` exactly; with no weight above 0 the keys weigh the same."""
    keys = [k for k, _ in weights]
    if not keys:
        if amount:
            raise ValueError("nothing to share the amount among")
        return {}
    given = [Fraction(w) for _, w in weights]
    if any(w < 0 for w in given):
        raise ValueError("a weight below zero")
    whole = sum(given)
    if whole == 0:
        given, whole = [Fraction(1)] * len(keys), Fraction(len(keys))
    sign, size = (-1, -amount) if amount < 0 else (1, amount)
    exact = [size * w / whole for w in given]
    shares = [int(e) for e in exact]  # floor: every exact share is at or above 0
    left = size - sum(shares)
    order = sorted(range(len(keys)), key=lambda i: (-(exact[i] - shares[i]), keys[i]))
    for i in order[:left]:
        shares[i] += 1
    return {k: sign * s for k, s in zip(keys, shares, strict=True)}


def _dp_weights(orders: Sequence[Mapping[str, Any]]) -> list[tuple[Any, Fraction]]:
    groups: dict[Any, int] = {}
    for o in orders:
        if o.get("dp_group") is not None:
            groups[o["dp_group"]] = groups.get(o["dp_group"], 0) + 1
    return [
        (o["key"], Fraction(1, groups[o["dp_group"]]) if o.get("dp_group") is not None else 0)
        for o in orders
    ]


def weights_of(part: str, orders: Sequence[Mapping[str, Any]]) -> list[tuple[Any, Any]]:
    """The weight of each order for one part (see the module's words)."""
    if part == "dp":
        found = _dp_weights(orders)
    else:
        name = WEIGHT_OF[part]
        found = [(o["key"], int((o.get("estimate") or {}).get(name) or 0)) for o in orders]
    if any(w for _, w in found):
        return found
    return [(o["key"], int(o.get("turnover") or 0)) for o in orders]


def spread(actual: Mapping[str, int | None], orders: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Share Upstox's parts of one day and segment among that day's orders.

    ``actual`` maps a name of ``ACTUAL_PARTS`` to paise, or None where Upstox gave none.
    Each order carries ``key`` (sortable, unique), ``estimate`` (the journal's parts of it in
    paise: brokerage, stt, exchange, sebi, stamp, gst, total), ``turnover`` and ``dp_group``
    (what was sold out of the demat account, for a delivery sell order; else None).

    Returns ``{"orders": {key: {"parts": {name: paise}, "total": paise}}, "missing": [names],
    "partial": bool}``; every order is in the answer, a part only when Upstox gave it.
    """
    keys = [o["key"] for o in orders]
    if len(set(keys)) != len(keys):
        raise ValueError("an order is listed twice")
    unknown = set(actual) - set(ACTUAL_PARTS)
    if unknown:
        raise ValueError(f"not a part of Upstox's charges: {sorted(unknown)}")
    out: dict[Any, dict[str, Any]] = {k: {"parts": {}, "total": 0} for k in keys}
    missing = []
    for part in ACTUAL_PARTS:
        if part not in actual:
            continue
        amount = actual[part]
        if amount is None:
            missing.append(part)
            continue
        for key, share in largest_remainder(int(amount), weights_of(part, orders)).items():
            out[key]["parts"][part] = share
            out[key]["total"] += share
    sold = any(o.get("dp_group") is not None for o in orders)
    partial = any(p in CORE_PARTS or (p == "dp" and sold) for p in missing)
    return {"orders": out, "missing": missing, "partial": partial}


def to_trips(parts: Mapping[str, int], holders: Mapping[Any, int]) -> dict[Any, dict[str, int]]:
    """One order's parts shared among the trades that hold its fills: ``holders`` maps a trade to
    the quantity of the order in it. Each trade gets every part of ``parts``."""
    weights = sorted(holders.items())
    if any(q <= 0 for _, q in weights):
        raise ValueError("a trade holds none of the order")
    out: dict[Any, dict[str, int]] = {k: {} for k, _ in weights}
    for part, amount in parts.items():
        for key, share in largest_remainder(int(amount), weights).items():
            out[key][part] = share
    return out
