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

import hashlib
from dataclasses import dataclass

from utils.thread_safe_cache import LockedTTLCache

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


#: Upstox's own kill switch is shown on the Risk page only, so a read is kept a minute.
UPSTOX_KILL_SWITCH_KEPT_S = 60
_upstox_kill_switch = LockedTTLCache(maxsize=2, ttl=UPSTOX_KILL_SWITCH_KEPT_S)


def upstox_kill_switch(auth: str) -> list[dict]:
    """Upstox's own kill switch per segment, for the owner to see; SUDARSHAN never changes it.

    Turning it on at Upstox needs every position in the segment flat and then locks the segment
    for 12 hours (design section 3.10), so it is only ever read here. A standard-budget read.
    """

    def load() -> list[dict]:
        try:
            answer = _upstox().get_api_response("/v2/user/kill-switch", auth)
        except Exception as exc:
            raise Unreadable(f"Upstox kill switch: {type(exc).__name__}") from exc
        if not isinstance(answer, dict) or answer.get("status") != "success":
            raise Unreadable("the Upstox kill switch answer is not a success")
        return [
            {
                "segment": str(row.get("segment") or ""),
                "on": row.get("kill_switch_enabled") is True,
                "active": str(row.get("segment_status") or "").upper() == "ACTIVE",
            }
            for row in answer.get("data") or []
            if isinstance(row, dict)
        ]

    key = hashlib.sha256(auth.encode()).hexdigest()  # never the token itself
    return _upstox_kill_switch.get_or_load(key, load)


# -- the sandbox: its own tables, read in the caller's session ------------------------------------


def _sandbox():
    from database import sandbox_db

    return sandbox_db


def sandbox_position(user_id: str, symbol: str, exchange: str, product: str) -> int:
    """p for one sandbox position. The place decorator runs inside the position's lock
    (sandbox/position_locks.py), so this read is the one the order itself decides on."""
    try:
        row = (
            _sandbox()
            .SandboxPositions.query.filter_by(
                user_id=user_id, symbol=symbol, exchange=exchange, product=product
            )
            .populate_existing()
            .first()
        )
    except Exception as exc:
        raise Unreadable(f"sandbox positions: {type(exc).__name__}") from exc
    return 0 if row is None else int(row.quantity or 0)


def sandbox_day(user_id: str):
    """Today's P&L in the sandbox (M5.14): its funds' realised P&L for today, and its open
    positions' P&L as the sandbox marks them. A position carried in from an earlier day counts
    from its entry, the sandbox keeping no day's figure for it."""
    from .daily import DayFigure

    try:
        db = _sandbox()
        funds = db.SandboxFunds.query.filter_by(user_id=user_id).populate_existing().first()
        open_rows = (
            db.SandboxPositions.query.filter(
                db.SandboxPositions.user_id == user_id, db.SandboxPositions.quantity != 0
            )
            .populate_existing()
            .all()
        )
    except Exception as exc:
        raise Unreadable(f"sandbox day: {type(exc).__name__}") from exc
    realised = _number(funds.today_realized_pnl) if funds is not None else 0.0
    return DayFigure(realised, sum(_number(row.pnl) for row in open_rows))


def sandbox_working_order(user_id: str, order_id: object) -> Working | None:
    """The sandbox order ``order_id``, or None when it is not there."""
    try:
        row = (
            _sandbox()
            .SandboxOrders.query.filter_by(orderid=str(order_id), user_id=user_id)
            .populate_existing()
            .first()
        )
    except Exception as exc:
        raise Unreadable(f"sandbox orders: {type(exc).__name__}") from exc
    if row is None:
        return None
    terms = rules.OrderTerms(
        quantity=int(row.quantity or 0),
        price_type=str(row.price_type or "").upper(),
        price=_number(row.price),
        trigger=_number(row.trigger_price),
    )
    return Working(
        order=rules.WorkingOrder(rules.side_of(row.action), int(row.filled_quantity or 0), terms),
        exchange=str(row.exchange or "").upper(),
        symbol=str(row.symbol or ""),
        product=str(row.product or "").upper(),
        instrument="",
        final=str(row.order_status or "") not in ("open", "trigger pending"),
    )


def sandbox_gtt(user_id: str, trigger_id: object) -> Gtt | None:
    """The active sandbox GTT ``trigger_id``, or None when it is not there."""
    try:
        gtt = (
            _sandbox()
            .SandboxGTT.query.filter_by(gtt_id=str(trigger_id), user_id=user_id)
            .populate_existing()
            .first()
        )
        legs = list(gtt.legs) if gtt is not None else []
    except Exception as exc:
        raise Unreadable(f"sandbox GTTs: {type(exc).__name__}") from exc
    if gtt is None or gtt.gtt_status != "active":
        return None
    return Gtt(
        trigger_type="OCO" if gtt.trigger_type == "two-leg" else "SINGLE",
        side=rules.side_of(legs[0].action) if legs else None,
        product=str(legs[0].product or "").upper() if legs else "",
        symbol=str(gtt.symbol or ""),
        exchange=str(gtt.exchange or "").upper(),
    )
