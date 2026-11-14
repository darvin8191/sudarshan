"""Pairing fills into round trips (JA1, M14.1; design 5.1, review B1).

Fills of one mode are replayed per key (mode, exchange, symbol, product class) in time order
with a running net quantity ``q``. A fill that moves ``q`` away from zero is an entry leg, one that
moves it toward zero an exit leg, and one that crosses zero is split into an exit leg of ``|q|``
and an entry leg of the rest: a new round trip opens at that same fill. A trip opens at the first
entry leg from ``q = 0`` and closes when ``q`` returns to 0.

The journal pairs only the fills it has seen, so two cases are kept out of every figure:

* **Unmatched.** A position held before the journal's first fill of the instrument: ``openings``
  gives the quantity held then (signed, a short is negative) and ``q`` starts there. The trip
  that runs from it until ``q`` is back at 0 is ``unmatched``: its entry is not known here, so it
  has no average, no gross and no hold, and it is never a LONG or a SHORT on a screen (its
  ``side`` is only the direction of the position it reduces). In the cash market's carry class a
  sale never opens a short (a delivery sale is a sale from holdings): the part of a sale beyond
  what the journal saw bought goes to an unmatched trip, the one just reduced to 0 when that was
  one, else one of its own, ended at once.
* **Expired.** A contract left open past its own expiry day: an expiry has no fill, so the trip
  never closes by itself. ``past_expiry`` says when its status becomes ``expired`` (the replay
  and the close check apply it); its result waits for a settlement value.

* **Counted again.** A share trade left open whose sale the journal never saw (it was off for
  longer than its catch-up reaches) would stay an open LONG for ever (verification N2).
  ``recounts`` give, for a key, what the account held at the start of a day, read again later.
  At that day the replay compares: where the account holds fewer shares than the position the
  fills make, the trip is ended there as ``unmatched`` (its closing was not seen, ``unclosed``)
  and the pairing goes on from the quantity counted, as from an opening. More shares than the
  fills make change nothing (a sale past what was seen bought is already listed apart). Outside
  the cash market's carry class a recount of any other quantity ends the trip the same way.

Rules that each pin a defect:

* The order is (``exch_ts_ms``, ``trade_id``), never the order the fills arrived in; a trade id
  that is a number is compared as one ("9" before "10", review L8).
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

import re
from collections.abc import Iterable, Mapping
from datetime import date
from typing import Any

from sudarshan.journal.core import kinds, money

OPEN, CLOSED, UNMATCHED, EXPIRED = "open", "closed", "unmatched", "expired"
#: The cash market: a sale in its carry class (delivery) is a sale from holdings, never a short.
CASH_EXCHANGES = frozenset({"NSE", "BSE"})
#: Exchanges whose contracts stop trading before the close check (16:05) on their expiry day.
CLOSED_BY_CHECK = frozenset({"NFO", "BFO"})
MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
#: An OpenAlgo derivative symbol ends with its expiry (DDMMMYY), then a strike and CE or PE, or
#: FUT: NIFTY25NOV2625000CE, VEDL25APR24292.5CE, CRUDEOILM20MAY24FUT.
_EXPIRY = re.compile(r"(\d{2})(" + "|".join(MONTHS) + r")(\d{2})(?:\d+(?:\.\d+)?(?:CE|PE)|FUT)$")


def product_class(product: str) -> str:
    """``intraday`` for MIS and I, ``carry`` for NRML, CNC and D; anything else is an error."""
    p = str(product).upper()
    if p in kinds.INTRADAY_PRODUCTS:
        return "intraday"
    if p in kinds.CARRY_PRODUCTS:
        return "carry"
    raise ValueError(f"unknown product: {product!r}")


def cash_carry(exchange: str, pclass: str) -> bool:
    """True for the cash market's carry class (delivery): a sale there never opens a short."""
    return pclass == "carry" and str(exchange).upper() in CASH_EXCHANGES


def symbol_expiry(symbol: str) -> int | None:
    """The expiry day (YYYYMMDD) an OpenAlgo derivative symbol carries, or None (a share, or a
    symbol of another form). The master drops a contract after it expires, so this is what tells
    the day once the master no longer can."""
    found = _EXPIRY.search(str(symbol).upper())
    if not found:
        return None
    dd, mon, yy = int(found.group(1)), MONTHS.index(found.group(2)) + 1, int(found.group(3))
    try:
        return money.to_day(date(2000 + yy, mon, dd))
    except ValueError:
        return None


def past_expiry(exchange: str, expiry_day: int | None, today: int, after_close: bool) -> bool:
    """Whether a contract with this expiry day can no longer trade: its day has passed, or (with
    ``after_close``, the close check at 16:05) it is today and its exchange's derivatives closed
    at 15:30. A currency or commodity contract can still trade after 16:05 on its expiry day, so
    it waits for the next day."""
    if expiry_day is None:
        return False
    if expiry_day < today:
        return True
    return after_close and expiry_day == today and str(exchange).upper() in CLOSED_BY_CHECK


def held_before(opening: Mapping[str, Any] | None, fills: Iterable[Mapping[str, Any]], *,
                cash: bool, expiry_day: int | None = None) -> int:  # fmt: skip
    """The quantity held before the first of ``fills`` (a key's fills from its first one on),
    from the opening read at first sight: ``qty`` held at the start of ``day``. Fills of days
    before the read (a day rebuilt from Upstox's history after it) are already in that figure,
    so they are taken back out. A contract that expired before the day of the read is gone from
    it whatever was held, so the read says nothing then: 0. A share is never held short: 0."""
    if opening is None:
        return 0
    qty, day = int(opening["qty"]), int(opening["day"])
    if expiry_day is not None and expiry_day < day:
        return 0
    seen: set[str] = set()
    before = 0
    for fill in fills:
        ident = str(fill["trade_id"])
        if fill.get("source") == "withdrawn" or ident in seen or int(fill["day"]) >= day:
            continue
        seen.add(ident)
        before += int(fill["qty"]) if str(fill["action"]).upper() == "BUY" else -int(fill["qty"])
    start = qty - before
    return max(start, 0) if cash else start


def unclosed(trip: Mapping[str, Any]) -> bool:
    """Whether an unmatched trip is one the journal saw opened and did not see closed (ended by
    a recount): it ended holding more than its fills took off. A position opened before the
    journal ends with at least as much sold as it saw bought."""
    return (
        trip.get("status") == UNMATCHED
        and trip.get("closed_ms") is not None
        and int(trip.get("entry_qty") or 0) > int(trip.get("exit_qty") or 0)
    )


def fill_order(fill: Mapping[str, Any]) -> tuple[int, int, int, str]:
    """A fill's place in time: its exchange time, then its trade id, a number as a number (an
    id of other letters after the numbers, as text)."""
    ident = str(fill["trade_id"])
    if ident.isdigit():
        return int(fill["exch_ts_ms"]), 0, int(ident), ""
    return int(fill["exch_ts_ms"]), 1, 0, ident


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


def _new_trip(fill: dict[str, Any], side: str, status: str = OPEN) -> dict[str, Any]:
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
        "status": status,
        "qty_max": 0,
        "entry_qty": 0,
        "exit_qty": 0,
        "entry_value_u": 0,
        "exit_value_u": 0,
        "legs": [],
    }


def _add_leg(trip: dict[str, Any], fill: dict[str, Any], role: str, qty: int) -> None:
    """A leg of ``qty`` of ``fill``; a second part of the same fill in the same role (a sale that
    goes on past what an unmatched trip held) is added to its leg, so a fill has one leg a trip."""
    last = trip["legs"][-1] if trip["legs"] else None
    if last is not None and last["trade_id"] == fill["trade_id"] and last["role"] == role:
        last["qty"] += qty
    else:
        trip["legs"].append({
            "trade_id": fill["trade_id"],
            "role": role,
            "qty": qty,
            "price_u": fill["price_u"],
            "exch_ts_ms": fill["exch_ts_ms"],
            "day": fill["day"],
        })  # fmt: skip
    value = money.value_u(qty, fill["price_u"])
    if role == "entry":
        trip["entry_qty"] += qty
        trip["entry_value_u"] += value
    else:
        trip["exit_qty"] += qty
        trip["exit_value_u"] += value


def _end(trip: dict[str, Any], fill: dict[str, Any]) -> None:
    """The position is back at 0 at ``fill``: a trip closes; an unmatched one ends, unmatched."""
    if trip["status"] == OPEN:
        trip["status"] = CLOSED
    trip["closed_ms"] = fill["exch_ts_ms"]
    trip["close_day"] = fill["day"]


def _finish(trip: dict[str, Any]) -> dict[str, Any]:
    """Averages, gross and hold, from the exact sums. An unmatched trip's entry is not known:
    its average is 0 (from the entries seen, when there were some), its gross and hold None."""
    if trip["entry_qty"]:
        trip["entry_avg_u"] = money.avg_price_u(trip["entry_value_u"], trip["entry_qty"])
    else:
        trip["entry_avg_u"] = 0
    if trip["exit_qty"]:
        trip["exit_avg_u"] = money.avg_price_u(trip["exit_value_u"], trip["exit_qty"])
    else:
        trip["exit_avg_u"] = None
    if trip["status"] == CLOSED:
        diff = trip["exit_value_u"] - trip["entry_value_u"]
        if trip["side"] == "short":
            diff = -diff
        trip["gross_paise"] = money.u_to_paise(diff)
        trip["hold_s"] = (trip["closed_ms"] - trip["opened_ms"]) // 1000
    else:
        trip["gross_paise"] = None
        trip["hold_s"] = None
    if trip["status"] == UNMATCHED:
        trip["qty_max"] = max(trip["qty_max"], trip["exit_qty"])
    return trip


def _pair_key(group: list[dict[str, Any]], opening: int, cash: bool,
              recounts: Iterable[tuple[int, int]] = ()) -> list[dict[str, Any]]:  # fmt: skip
    """The trips of one key's fills (in order), starting from ``opening`` held before them;
    ``recounts`` are (day, quantity the account held at the start of that day)."""
    out: list[dict[str, Any]] = []
    q = max(opening, 0) if cash else opening
    trip: dict[str, Any] | None = None
    counted = sorted((int(day), int(qty)) for day, qty in recounts)

    def recount(day: int, qty: int) -> None:
        nonlocal q, trip
        held = max(qty, 0) if cash else qty
        if held == q or (cash and held > q):
            return
        if trip is not None:
            # Ended at the start of the day it was counted on, its closing not seen.
            trip["status"] = UNMATCHED
            trip["closed_ms"] = money.day_start_ms(day)
            trip["close_day"] = day
            out.append(trip)
            trip = None
        q = held

    for fill in group:
        while counted and counted[0][0] <= fill["day"]:
            recount(*counted.pop(0))
        if trip is None and q:
            # Held before this fill (an opening, or a recount): the run-off of it is unmatched.
            trip = _new_trip(fill, "long" if q > 0 else "short", UNMATCHED)
            trip["qty_max"] = abs(q)
        step = 1 if fill["action"] == "BUY" else -1
        remaining = fill["qty"]
        ended: dict[str, Any] | None = None
        if trip is not None and q and (q > 0) != (step > 0):
            exit_qty = min(remaining, abs(q))
            _add_leg(trip, fill, "exit", exit_qty)
            q += step * exit_qty
            remaining -= exit_qty
            if q == 0:
                _end(trip, fill)
                out.append(trip)
                ended, trip = trip, None
        if not remaining:
            continue
        if cash and step < 0:
            # A delivery sale past what the journal saw bought: shares held before it.
            if ended is not None and ended["status"] == UNMATCHED:
                _add_leg(ended, fill, "exit", remaining)
            else:
                lone = _new_trip(fill, "long", UNMATCHED)
                _add_leg(lone, fill, "exit", remaining)
                _end(lone, fill)
                out.append(lone)
            continue
        if trip is None:
            trip = _new_trip(fill, "long" if step > 0 else "short")
        _add_leg(trip, fill, "entry", remaining)
        q += step * remaining
        trip["qty_max"] = max(trip["qty_max"], abs(q))
    for day, qty in counted:  # counted after the last fill: a trip still open may end there
        recount(day, qty)
    if trip is not None:
        out.append(trip)
    return [_finish(t) for t in out]


def pair_fills(
    fills: Iterable[Mapping[str, Any]],
    openings: Mapping[tuple[str, str, str, str], int] | None = None,
    recounts: Mapping[tuple[str, str, str, str], Iterable[tuple[int, int]]] | None = None,
) -> list[dict[str, Any]]:
    """Round trips from fills, closed, open and unmatched, ordered by (opened_ms, key).

    ``openings`` maps a key (mode, exchange, symbol, product class) to the quantity held before
    the first of its fills given here (see the module's words); a key it does not name starts at
    0. ``recounts`` maps a key to (day, quantity held at the start of that day) pairs read
    again later (see the module's words). Each trip is a dict with the key fields, ``side``, quantities, ``entry_avg_u``,
    ``exit_avg_u`` (None until a fill exits), the exact ``entry_value_u`` and ``exit_value_u``,
    ``gross_paise`` and ``hold_s`` (None unless closed), ``status`` and its ``legs``.
    """
    known = openings or {}
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for fill in _usable(fills):
        key = (fill["mode"], fill["exchange"], fill["symbol"], fill["pclass"])
        groups.setdefault(key, []).append(fill)
    trips: list[dict[str, Any]] = []
    for key, group in groups.items():
        group.sort(key=fill_order)
        trips += _pair_key(group, int(known.get(key) or 0), cash_carry(key[1], key[3]),
                           (recounts or {}).get(key) or ())  # fmt: skip
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
