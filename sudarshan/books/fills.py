"""What Upstox's positions book does not show yet, worked out from its order book (M6.9).

Upstox's positions book can show a fill a minute late. Its order book shows the fill at once, as
``filled_quantity``, and each positions row says how much it has taken in so far today:
``day_buy_quantity`` and ``day_sell_quantity``. For one instrument and product, today's filled
quantity in the order book less the row's own figure is exactly what the row does not show yet:

    unseen = (filled buys - day_buy_quantity) - (filled sells - day_sell_quantity)

and the position held is the row's ``quantity`` plus ``unseen``. No clock is involved, so it holds
however long the positions book lags.

The positions book must be read first and the order book after it. A fill that lands between the
two reads then shows in the order book and not in the row, and the correction still comes out
right; read the other way round, the row would show a fill the order book did not, and the
difference would come out negative.

This module reads Upstox's raw rows and performs no I/O. It adds no field to anything
/api/v1/orderbook, /orderstatus or /positionbook return (.claude/rules/sudarshan.md).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any


def _whole(value: Any) -> int | None:
    """A non-negative whole number from a row field, or None when it is missing or not one."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number < 0 or number != int(number):  # NaN, negative, fractional
        return None
    return int(number)


def _signed(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number != int(number):
        return None
    return int(number)


def _same_book_key(row: dict, token: str, product: str) -> bool:
    return (
        str(row.get("instrument_token") or "").strip() == token
        and str(row.get("product") or "").strip().upper() == product
    )


def unseen_net(position: dict, orders: Iterable[dict]) -> int | None:
    """The signed quantity today's fills have added that ``position``'s row does not show yet.

    Args:
        position: One raw row of Upstox's short-term positions.
        orders: Every raw row of Upstox's order book for today (any status: a cancelled order
            may have part-filled), read after the positions.

    Returns:
        The signed quantity (buys positive), 0 when the row is up to date; None when it cannot
        be worked out: a field is missing or not a number, an order of this instrument names no
        side or no filled quantity, or the row shows more than the order book does.
    """
    token = str(position.get("instrument_token") or "").strip()
    product = str(position.get("product") or "").strip().upper()
    day_buy = _whole(position.get("day_buy_quantity"))
    day_sell = _whole(position.get("day_sell_quantity"))
    if not token or not product or day_buy is None or day_sell is None:
        return None
    bought = sold = 0
    for order in orders:
        if not isinstance(order, dict) or not _same_book_key(order, token, product):
            continue
        filled = _whole(order.get("filled_quantity"))
        side = str(order.get("transaction_type") or "").strip().upper()
        if filled is None or side not in ("BUY", "SELL"):
            return None
        if side == "BUY":
            bought += filled
        else:
            sold += filled
    if bought < day_buy or sold < day_sell:
        return None
    return (bought - day_buy) - (sold - day_sell)


def held_now(position: dict, orders: Iterable[dict] | None) -> int | None:
    """The signed quantity actually held for a raw positions row: its ``quantity`` corrected by
    ``unseen_net``. None when the order book was not read (``orders`` is None) or the correction
    cannot be worked out."""
    if orders is None:
        return None
    shown = _signed(position.get("quantity"))
    unseen = unseen_net(position, orders)
    if shown is None or unseen is None:
        return None
    return shown + unseen


def _stamp(value: Any) -> datetime | None:
    """A row's ``order_timestamp`` ("2026-10-27 09:20:00", India's time) as a moment, or None."""
    try:
        return datetime.fromisoformat(str(value or "").strip())
    except ValueError:
        return None


def repeats_last_fill(orders: Iterable[dict], token: str, product: str, side: str) -> bool:
    """Whether a flat position's most recent fill today was on ``side``: the order, if it is sent
    as an exit, repeats the one that flattened it, and would open the opposite position.

    Args:
        orders: Every raw row of Upstox's order book for today.
        token: The instrument's token.
        product: Upstox's product letter ("I", "D", "CNC"-style).
        side: The request's own side, BUY or SELL.

    Returns:
        True when the latest order of this instrument and product with a filled quantity was on
        ``side``; also True, for safety, when that cannot be told: a filled order of it has no
        usable timestamp or side, or two latest fills share a second and one is on ``side``.
        False when nothing filled today in this product, or the latest fill was on the other side.
    """
    side = str(side or "").strip().upper()
    latest: list[str] = []
    latest_at: datetime | None = None
    for order in orders:
        if not isinstance(order, dict) or not _same_book_key(order, token, product):
            continue
        filled = _whole(order.get("filled_quantity"))
        if filled is None:
            return True
        if filled == 0:
            continue
        stamp = _stamp(order.get("order_timestamp"))
        order_side = str(order.get("transaction_type") or "").strip().upper()
        if stamp is None or order_side not in ("BUY", "SELL"):
            return True
        if latest_at is None or stamp > latest_at:
            latest_at, latest = stamp, [order_side]
        elif stamp == latest_at:
            latest.append(order_side)
    return side in latest
