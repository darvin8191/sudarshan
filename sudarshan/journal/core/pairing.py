"""Pairing fills into round trips (JA1, M14.1; design 5.1).

Fills of one mode are replayed per key (mode, exchange, symbol, product class) in time order
with a running net quantity ``q``. A fill that moves ``q`` away from zero is an entry leg, one that
moves it toward zero an exit leg, and one that crosses zero is split into an exit leg of ``|q|``
and an entry leg of the rest: a new round trip opens at that same fill. A trip opens at the first
entry leg from ``q = 0`` and closes when ``q`` returns to 0.

Rules that each pin a defect:

* The order is (``exch_ts_ms``, ``trade_id``), never the order the fills arrived in.
* The key uses the product *class*: MIS and I are intraday; NRML, CNC and D are one carry class.
* A ``trade_id`` seen twice is one fill (the first seen wins); a fill whose source is
  ``withdrawn`` is left out; live and sandbox fills are never mixed.
* The average is half-up to ``price_u``; the gross is summed from exact fill values, never from an
  average.

Fills are plain dicts: ``trade_id``, ``mode`` (default ``live``), ``exchange``, ``symbol``,
``product``, ``action`` (BUY or SELL), ``qty``, ``price_u``, ``exch_ts_ms``, and ``day``
(default: the IST day of ``exch_ts_ms``). A trip still open carries over days: to pair a later day,
pass the fills from the open trip's first entry onward.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from sudarshan.journal.core import kinds, money


def product_class(product: str) -> str:
    """``intraday`` for MIS and I, ``carry`` for NRML, CNC and D; anything else is an error."""
    p = str(product).upper()
    if p in kinds.INTRADAY_PRODUCTS:
        return "intraday"
    if p in kinds.CARRY_PRODUCTS:
        return "carry"
    raise ValueError(f"unknown product: {product!r}")


def _clean(fill: Mapping[str, Any]) -> dict[str, Any]:
    action = str(fill["action"]).upper()
    if action not in kinds.ACTIONS:
        raise ValueError(f"unknown action: {fill['action']!r}")
    qty = int(fill["qty"])
    if qty <= 0:
        raise ValueError("a fill has a positive quantity")
    ms = int(fill["exch_ts_ms"])
    return {
        "trade_id": str(fill["trade_id"]),
        "mode": fill.get("mode") or "live",
        "exchange": fill["exchange"],
        "symbol": fill["symbol"],
        "product": str(fill["product"]).upper(),
        "pclass": product_class(fill["product"]),
        "action": action,
        "qty": qty,
        "price_u": int(fill["price_u"]),
        "exch_ts_ms": ms,
        "day": int(fill["day"]) if fill.get("day") else money.ist_day(ms),
    }


def _usable(fills: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Cleaned fills, without withdrawn ones and without a second copy of a trade id."""
    seen: set[tuple[str, str]] = set()
    out = []
    for raw in fills:
        if raw.get("source") == "withdrawn":
            continue
        fill = _clean(raw)
        ident = (fill["mode"], fill["trade_id"])
        if ident in seen:
            continue
        seen.add(ident)
        out.append(fill)
    return out


def _new_trip(fill: dict[str, Any], side: str) -> dict[str, Any]:
    return {
        "mode": fill["mode"],
        "exchange": fill["exchange"],
        "symbol": fill["symbol"],
        "product": fill["product"],
        "product_class": fill["pclass"],
        "side": side,
        "opened_ms": fill["exch_ts_ms"],
        "open_day": fill["day"],
        "closed_ms": None,
        "close_day": None,
        "status": "open",
        "qty_max": 0,
        "entry_qty": 0,
        "exit_qty": 0,
        "entry_value_u": 0,
        "exit_value_u": 0,
        "legs": [],
    }


def _leg(fill: dict[str, Any], role: str, qty: int) -> dict[str, Any]:
    return {
        "trade_id": fill["trade_id"],
        "role": role,
        "qty": qty,
        "price_u": fill["price_u"],
        "exch_ts_ms": fill["exch_ts_ms"],
        "day": fill["day"],
    }


def _add_leg(trip: dict[str, Any], fill: dict[str, Any], role: str, qty: int) -> None:
    trip["legs"].append(_leg(fill, role, qty))
    value = money.value_u(qty, fill["price_u"])
    if role == "entry":
        trip["entry_qty"] += qty
        trip["entry_value_u"] += value
        trip["qty_max"] = max(trip["qty_max"], trip["entry_qty"] - trip["exit_qty"])
    else:
        trip["exit_qty"] += qty
        trip["exit_value_u"] += value


def _finish(trip: dict[str, Any]) -> dict[str, Any]:
    """Averages, gross and hold, from the exact sums."""
    trip["entry_avg_u"] = money.avg_price_u(trip["entry_value_u"], trip["entry_qty"])
    if trip["exit_qty"]:
        trip["exit_avg_u"] = money.avg_price_u(trip["exit_value_u"], trip["exit_qty"])
    else:
        trip["exit_avg_u"] = None
    if trip["status"] == "closed":
        diff = trip["exit_value_u"] - trip["entry_value_u"]
        if trip["side"] == "short":
            diff = -diff
        trip["gross_paise"] = money.u_to_paise(diff)
        trip["hold_s"] = (trip["closed_ms"] - trip["opened_ms"]) // 1000
    else:
        trip["gross_paise"] = None
        trip["hold_s"] = None
    return trip


def pair_fills(fills: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Round trips from fills, closed and open, ordered by (opened_ms, key).

    Each trip is a dict with the key fields, ``side``, quantities, ``entry_avg_u``,
    ``exit_avg_u`` (None until a fill exits), the exact ``entry_value_u`` and ``exit_value_u``,
    ``gross_paise`` and ``hold_s`` (None while open), ``status`` and its ``legs``.
    """
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for fill in _usable(fills):
        key = (fill["mode"], fill["exchange"], fill["symbol"], fill["pclass"])
        groups.setdefault(key, []).append(fill)
    trips: list[dict[str, Any]] = []
    for group in groups.values():
        group.sort(key=lambda f: (f["exch_ts_ms"], f["trade_id"]))
        q = 0
        trip: dict[str, Any] | None = None
        for fill in group:
            signed = fill["qty"] if fill["action"] == "BUY" else -fill["qty"]
            remaining = fill["qty"]
            if trip is not None and (q > 0) != (signed > 0):
                exit_qty = min(remaining, abs(q))
                _add_leg(trip, fill, "exit", exit_qty)
                q += signed // abs(signed) * exit_qty
                remaining -= exit_qty
                if q == 0:
                    trip["status"] = "closed"
                    trip["closed_ms"] = fill["exch_ts_ms"]
                    trip["close_day"] = fill["day"]
                    trips.append(_finish(trip))
                    trip = None
            if remaining:
                if trip is None:
                    trip = _new_trip(fill, "long" if signed > 0 else "short")
                _add_leg(trip, fill, "entry", remaining)
                q += (1 if signed > 0 else -1) * remaining
        if trip is not None:
            trips.append(_finish(trip))
    trips.sort(
        key=lambda t: (
            t["opened_ms"],
            t["mode"],
            t["exchange"],
            t["symbol"],
            t["product_class"],
            t["legs"][0]["trade_id"],
        )
    )
    return trips
