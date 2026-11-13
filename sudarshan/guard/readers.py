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

import contextlib
import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from utils.thread_safe_cache import LockedTTLCache

from . import rules

FINAL_STATUSES = frozenset({"complete", "rejected", "cancelled"})


class Unreadable(Exception):
    """A book could not be read, so what an order would do is unknown."""


class _Miss(Exception):
    pass


#: Every read the gate makes is cut off after this long (a few seconds, never the shared client's
#: 120): under the kill switch an exit waits for its read, and Exit all's sends are timed.
READ_LIMIT_S = 4.0


@contextlib.contextmanager
def _limited():
    """The Upstox module's reads made inside are cut off after ``READ_LIMIT_S`` and not retried
    (a limit already set by Exit all, if shorter, stays)."""
    order_api = _upstox()
    held = order_api._READ_LIMIT.get()
    token = order_api._READ_LIMIT.set(min(held, READ_LIMIT_S) if held else READ_LIMIT_S)
    try:
        yield
    finally:
        order_api._READ_LIMIT.reset(token)


limited = _limited  # for the callers outside this module (the kill switch's reads)


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
        with _limited():
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


def live_order_rows(auth: str) -> list:
    """Upstox's raw order book rows, read once and cut off after ``READ_LIMIT_S``."""
    try:
        with _limited():
            book = _upstox().get_order_book(auth)
    except Exception as exc:  # a refused read (gthread) or a failed one
        raise Unreadable(f"orders: {type(exc).__name__}") from exc
    if not isinstance(book, dict) or book.get("status") != "success":
        raise Unreadable("the orders answer is not a success")
    return [row for row in book.get("data") or [] if isinstance(row, dict)]


def live_working_order(auth: str, order_id: object) -> Working | None:
    """The order ``order_id`` in Upstox's order book, or None when it is not there."""
    order_api = _upstox()
    try:
        with _limited():
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


def live_order_statuses(auth: str) -> dict[str, str]:
    """Every order in Upstox's order book today and its status as OpenAlgo names it
    (broker/upstox/mapping/order_data.normalize_order_status: complete, rejected, cancelled,
    open, or Upstox's own word lowered): what tells whether a claim has ended (gate.py)."""
    order_api = _upstox()
    try:
        with _limited():
            book = order_api.get_order_book(auth)
    except Exception as exc:
        raise Unreadable(f"orders: {type(exc).__name__}") from exc
    if not isinstance(book, dict) or book.get("status") != "success":
        raise Unreadable("the orders answer is not a success")
    from broker.upstox.mapping.order_data import normalize_order_status

    return {
        str(row.get("order_id")): normalize_order_status(row.get("status"))
        for row in book.get("data") or []
        if isinstance(row, dict) and row.get("order_id")
    }


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
        with _limited():
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


def quote_of(data: dict):
    """The market the band reads from OpenAlgo's quote (Upstox's full quote as
    broker/upstox/api/data.BrokerData maps it): the last price and the best bid and ask."""
    from .band import Quote

    return Quote(_number(data.get("ltp")), _number(data.get("bid")), _number(data.get("ask")))


#: A quote serves the price band this long: two orders in the same second share one read.
QUOTE_KEPT_S = 1.0
_quotes = LockedTTLCache(maxsize=256, ttl=QUOTE_KEPT_S)


def quote(pipe: str, owner: str, symbol: str, exchange: str):
    """The market for one instrument (band.py): Upstox's full quote, its last price and best bid
    and ask, kept a second.

    A Live order's owner is its broker session; a sandbox order's is the user, whose own Upstox
    session prices it (the sandbox fills at Upstox's prices). A standard-budget read through the
    Upstox module's shared client: no new connection, no thread.
    """
    auth = owner if pipe == "live" else _upstox_session_of(owner)

    def load():
        from broker.upstox.api.data import BrokerData

        try:
            with _limited():
                data = BrokerData(auth).get_quotes(symbol, exchange)
        except Exception as exc:
            raise Unreadable(f"quote: {type(exc).__name__}") from exc
        if not isinstance(data, dict):
            raise Unreadable("the quote answer is not one")
        return quote_of(data)

    key = (hashlib.sha256(auth.encode()).hexdigest(), exchange, symbol)  # never the token itself
    return _quotes.get_or_load(key, load)


def lot_size(symbol: str, exchange: str) -> float | None:
    """The instrument's lot size from the master contract (database/token_db_enhanced, its
    in-memory cache first), or None when it is not there."""
    from database.token_db_enhanced import get_symbol_info

    try:
        info = get_symbol_info(symbol, exchange)
    except Exception as exc:
        raise Unreadable(f"lot size: {type(exc).__name__}") from exc
    lot = _number(getattr(info, "lotsize", 0)) if info is not None else 0.0
    return lot if lot > 0 else None


def _upstox_session_of(user: str) -> str:
    """The user's broker session, when it is Upstox's."""
    from .killswitch import owner_for

    try:
        owner = owner_for(user, surface="app", actor=user)
    except Exception as exc:
        raise Unreadable(f"session: {type(exc).__name__}") from exc
    if owner.broker != "upstox" or not owner.auth_token:
        raise Unreadable("no Upstox session to price the order with")
    return owner.auth_token


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


def _sandbox_delivery(row) -> bool:
    """A sandbox CNC position in cash: delivery, left out of the day's figure (daily.py)."""
    return (
        str(row.product or "").strip().upper() == "CNC"
        and str(row.exchange or "").strip().upper() in rules.EQUITY_CASH
    )


#: The sandbox stores a position's P&L only when something marks it (its positions page; its
#: background loop never runs inside the app), and a fill leaves the stored figure as it was, for
#: the old quantity. So the day's figure is worked out here from the rows as they stand and the
#: sandbox's own prices, and nothing is written. Only the prices Upstox answered are shared, this
#: long, so two orders in one second ask Upstox once; the rows are read afresh every time, so a
#: fill counts in the very next figure (review of v0.6.1, HIGH-1).
SANDBOX_PRICE_KEPT_S = 1.0
_sandbox_prices = LockedTTLCache(maxsize=256, ttl=SANDBOX_PRICE_KEPT_S)


@contextlib.contextmanager
def _between_fills():
    """Held while the sandbox's rows are read for the day's figure. A fill that reduces a
    position books its realised P&L to the funds in one commit and the new quantity in the next,
    both under the execution engine's position lock, so a read between them counts the closed
    part twice. Waits at most ``READ_LIMIT_S``; a fill still being booked then makes the figure
    unreadable."""
    from sandbox.execution_engine import ExecutionEngine

    lock = getattr(ExecutionEngine, "_position_lock", None)
    if lock is None:  # an upstream sandbox without it: nothing to wait for
        yield
        return
    if not lock.acquire(timeout=READ_LIMIT_S):
        raise Unreadable("sandbox day: a fill is still being booked")
    try:
        yield
    finally:
        lock.release()


def _sandbox_book(user_id: str) -> tuple[object, list]:
    """Today's realised P&L in the user's sandbox funds and their position rows, as committed.
    Column reads: the caller's session objects are neither refreshed nor changed."""
    from sqlalchemy import select

    db = _sandbox()
    funds, positions = db.SandboxFunds, db.SandboxPositions
    with _between_fills():
        realised = db.db_session.execute(
            select(funds.today_realized_pnl).where(funds.user_id == user_id)
        ).first()
        rows = db.db_session.execute(
            select(
                positions.symbol,
                positions.exchange,
                positions.product,
                positions.quantity,
                positions.average_price,
                positions.ltp,
                positions.pnl,
                positions.today_realized_pnl,
            ).where(positions.user_id == user_id)
        ).all()
    return (realised[0] if realised is not None else None), rows


def _price_of(quote: object):
    """The last price in a sandbox quote, above 0, as the sandbox marks with it; else None."""
    from decimal import Decimal, InvalidOperation

    if not isinstance(quote, dict):
        return None
    try:
        price = Decimal(str(quote.get("ltp", 0)))
    except (InvalidOperation, ValueError):
        return None
    return price if price > 0 else None


def _sandbox_marks(manager, wanted: list[tuple[str, str]]) -> tuple[dict, datetime | None]:
    """A price for each ``(symbol, exchange)`` from the sandbox's own two sources, in its own
    order: the live feed's ticks no older than its 5 s first, then one multiquotes read for the
    rest, cut off after ``READ_LIMIT_S`` like every other read of the gate's (never the shared
    client's 120 s). Returns the prices found and when the oldest of them was had (None for
    none)."""
    prices: dict = {}
    times: list[datetime] = []
    if not wanted:
        return prices, None
    ticks = manager._fetch_quotes_from_websocket(wanted)
    now = datetime.now(UTC)
    for key in wanted:
        price = _price_of(ticks.get(key))
        if price is not None:
            prices[key] = price
            times.append(now)
    for key in [key for key in wanted if key not in prices]:
        kept = _sandbox_prices.get(key)
        if kept is not None:
            prices[key] = kept[0]
            times.append(kept[1])
    missing = [key for key in wanted if key not in prices]
    if missing:
        with _limited():
            answered = manager._fetch_quotes_batch(missing)
        had = datetime.now(UTC)
        for key in missing:
            price = _price_of(answered.get(key))
            if price is not None:
                prices[key] = price
                times.append(had)
                _sandbox_prices[key] = (price, had)
    return prices, (min(times) if times else None)


def _sandbox_unrealised(user_id: str, held: list) -> tuple[float, datetime]:
    """The open positions' P&L at the sandbox's own prices, by its own arithmetic
    (``PositionManager._calculate_position_pnl`` with the contract's value), and when the prices
    were had. A contract past its expiry that the sandbox has not settled yet is valued at the
    price its settlement will use, needing no quote. Raises ``Unreadable`` when any other open
    position has no price now: its stored figure may be any age, and is never served as current
    (review of v0.6.1, MEDIUM-2)."""
    from sandbox.position_manager import (
        PositionManager,
        get_contract_expiry,
        get_expiry_settlement_price,
        is_contract_expired_now,
    )

    if not held:
        return 0.0, datetime.now(UTC)
    manager = PositionManager(user_id)
    settled: dict[int, object] = {}
    for index, row in enumerate(held):
        expiry = get_contract_expiry(row.symbol, row.exchange)
        if expiry is not None and is_contract_expired_now(expiry, row.exchange, symbol=row.symbol):
            settled[index] = get_expiry_settlement_price(row)
    wanted = sorted(
        {(row.symbol, row.exchange) for index, row in enumerate(held) if index not in settled}
    )
    prices, priced_at = _sandbox_marks(manager, wanted)
    total = 0.0
    for index, row in enumerate(held):
        price = settled.get(index, prices.get((row.symbol, row.exchange)))
        if price is None:
            raise Unreadable(f"sandbox prices: none now for {row.symbol} on {row.exchange}")
        value = manager._get_contract_value(row.symbol, row.exchange)
        total += float(
            manager._calculate_position_pnl(
                row.quantity, row.average_price, price, contract_value=value
            )
        )
    return total, priced_at or datetime.now(UTC)


def sandbox_day(user_id: str):
    """Today's P&L in the sandbox (M5.14): its funds' realised P&L for today, and its open
    positions' P&L at the sandbox's own prices, worked out from the rows as they stand (never
    stored), delivery (CNC in cash) left out of both. A position carried in from an earlier day
    counts from its entry, the sandbox keeping no day's figure for it. ``marked_at`` is when its
    prices were had."""
    from .daily import DayFigure

    try:
        booked, rows = _sandbox_book(user_id)
    except Unreadable:
        raise
    except Exception as exc:
        raise Unreadable(f"sandbox day: {type(exc).__name__}") from exc
    realised = _number(booked)
    realised -= sum(_number(row.today_realized_pnl) for row in rows if _sandbox_delivery(row))
    held = [row for row in rows if row.quantity and not _sandbox_delivery(row)]
    try:
        unrealised, marked_at = _sandbox_unrealised(user_id, held)
    except Unreadable:
        raise
    except Exception as exc:
        raise Unreadable(f"sandbox prices: {type(exc).__name__}") from exc
    return DayFigure(realised, unrealised, marked_at=marked_at)


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


def sandbox_order_statuses(user_id: str, order_ids: list[str]) -> dict[str, str]:
    """The sandbox's status for each of ``order_ids`` it has (complete, rejected, cancelled,
    open, trigger pending)."""
    try:
        orders = _sandbox().SandboxOrders
        rows = (
            orders.query.filter(orders.user_id == user_id, orders.orderid.in_(order_ids))
            .populate_existing()
            .all()
        )
    except Exception as exc:
        raise Unreadable(f"sandbox orders: {type(exc).__name__}") from exc
    return {str(row.orderid): str(row.order_status or "") for row in rows}


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
