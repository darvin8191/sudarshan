"""What the gate reads to tell an exit from a new order: the broker's own books (M5.13).

Live reads go through the Upstox module's own readers and never through one that turns a failure
into a flat position (``order_api.get_open_position`` answers "0" on an exception):

- positions: ``_get_cached_positions``, the 1 s cache the smart orders share, whose read raises
  ``PositionReadError`` when Upstox's answer is not a success (utils/position_read.py);
- working orders: ``get_order_book``; GTTs: ``gtt_api.get_gtt_book``.

Any failure raises ``Unreadable``. The broker module is imported when it is first read, because it
imports the gate.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import rules

FINAL_STATUSES = frozenset({"complete", "rejected", "cancelled"})


class Unreadable(Exception):
    """A book could not be read, so what an order would do is unknown."""


class _Miss(Exception):
    pass


def _miss():
    raise _Miss


def _upstox():
    from broker.upstox.api import order_api

    return order_api


def _rows(book: object) -> list:
    if not isinstance(book, dict) or book.get("status") != "success":
        raise Unreadable("the positions answer is not a success")
    data = book.get("data")
    if data is None:
        return []
    if not isinstance(data, list):
        raise Unreadable("the positions answer carries no list")
    return data


def live_cached_rows(auth: str) -> list | None:
    """The positions the Upstox module read within its cache's second, or None; never reads."""
    try:
        book = _upstox()._POSITION_BOOK.get(auth, _miss)
    except _Miss:
        return None
    return _rows(book)


def live_rows(auth: str) -> list:
    """Upstox's raw short-term positions, through the module's own cache."""
    try:
        book = _upstox()._get_cached_positions(auth)
    except Exception as exc:  # PositionReadError, BrokerBusyError (gthread), a network failure
        raise Unreadable(f"positions: {type(exc).__name__}") from exc
    return _rows(book)


def instrument_token(symbol: object, exchange: object) -> str | None:
    """The instrument key an order for ``symbol`` is sent with, as place_order_api finds it."""
    try:
        token = _upstox().get_token(symbol, exchange)
    except Exception as exc:
        raise Unreadable(f"instrument: {type(exc).__name__}") from exc
    return str(token) if token else None


@dataclass(frozen=True)
class Working:
    """An order as the broker has it, for judging a modify of it."""

    order: rules.WorkingOrder
    exchange: str
    symbol: str  # OpenAlgo's, so it keys like the orders this process sent
    product: str  # as the position is kept: Upstox's I or D, or the sandbox's product
    instrument: str  # Upstox's instrument key; "" in the sandbox
    final: bool


def _number(value: object) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def live_working_order(auth: str, order_id: object) -> Working | None:
    """The order ``order_id`` in Upstox's order book, or None when it is not there."""
    order_api = _upstox()
    try:
        book = order_api.get_order_book(auth)
    except Exception as exc:
        raise Unreadable(f"orders: {type(exc).__name__}") from exc
    if not isinstance(book, dict) or book.get("status") != "success":
        raise Unreadable("the orders answer is not a success")
    from broker.upstox.mapping.order_data import normalize_order_status

    for row in book.get("data") or []:
        if not isinstance(row, dict) or str(row.get("order_id")) != str(order_id):
            continue
        token = str(row.get("instrument_token") or "")
        exchange = str(row.get("exchange") or "").upper()
        try:
            symbol = order_api.get_symbol(token, exchange) or row.get("tradingsymbol") or ""
        except Exception:
            symbol = row.get("tradingsymbol") or ""
        terms = rules.OrderTerms(
            quantity=int(_number(row.get("quantity"))),
            price_type=str(row.get("order_type") or "").upper(),
            price=_number(row.get("price")),
            trigger=_number(row.get("trigger_price")),
        )
        return Working(
            order=rules.WorkingOrder(
                rules.side_of(row.get("transaction_type")),
                int(_number(row.get("filled_quantity"))),
                terms,
            ),
            exchange=exchange,
            symbol=str(symbol).strip().upper(),
            product=str(row.get("product") or "").strip().upper(),
            instrument=token,
            final=normalize_order_status(row.get("status")) in FINAL_STATUSES,
        )
    return None


@dataclass(frozen=True)
class Gtt:
    """A GTT as the broker has it: what a modify cannot change."""

    trigger_type: str  # SINGLE or OCO
    side: int | None
    product: str  # OpenAlgo's
    symbol: str
    exchange: str


def live_gtt(auth: str, trigger_id: object) -> Gtt | None:
    """The active GTT ``trigger_id`` in Upstox's GTT book, or None when it is not there."""
    from broker.upstox.api import gtt_api

    try:
        answer, status = gtt_api.get_gtt_book(auth)
    except Exception as exc:  # BrokerBusyError under gthread: the read was not sent
        raise Unreadable(f"GTTs: {type(exc).__name__}") from exc
    if status != 200 or not isinstance(answer, dict) or answer.get("status") != "success":
        raise Unreadable("the GTT answer is not a success")
    for item in answer.get("data") or []:
        if not isinstance(item, dict) or str(item.get("trigger_id")) != str(trigger_id):
            continue
        legs = [leg for leg in item.get("legs") or [] if isinstance(leg, dict)]
        return Gtt(
            trigger_type="OCO" if item.get("trigger_type") == "two-leg" else "SINGLE",
            side=rules.side_of(legs[0].get("action")) if legs else None,
            product=str(legs[0].get("product") or "") if legs else "",
            symbol=str(item.get("symbol") or ""),
            exchange=str(item.get("exchange") or ""),
        )
    return None
