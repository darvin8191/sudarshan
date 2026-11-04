"""What Upstox's positions book does not show yet, worked out from its order book (M6.9).

Upstox's positions book can show a fill a minute late. Its order book shows the fill at once, as
``filled_quantity``, and each positions row says how much it has taken in so far today:
``day_buy_quantity`` and ``day_sell_quantity``. For one instrument and product, today's filled
quantity in the order book less the row's own figure is exactly what the row does not show yet:

    unseen = (filled buys - day_buy_quantity) - (filled sells - day_sell_quantity)

and the position held is the row's ``quantity`` plus ``unseen``. No clock is involved, so it holds
however long the positions book lags.

A position converted between products in Upstox's app (MIS to NRML, to carry it overnight) has no
order behind its new row, so the correction is made for the whole instrument across its products
(``held_by_product``): while every product is consistent each is corrected as above; when one is
not, the instrument's net is compared instead.

The positions book must be read first and the order book after it. A fill that lands between the
two reads then shows in the order book and not in the row, and the correction still comes out
right; read the other way round, the row would show a fill the order book did not, and the
difference would come out negative.

This module reads Upstox's raw rows and performs no I/O. It adds no field to anything
/api/v1/orderbook, /orderstatus or /positionbook return (.claude/rules/sudarshan.md).
"""

from __future__ import annotations

from collections.abc import Iterable
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


def _product(row: dict) -> str:
    return str(row.get("product") or "").strip().upper()


def held_by_product(
    positions: Iterable[dict], orders: Iterable[dict] | None, token: str
) -> dict[str, int] | None:
    """The signed quantity held in each product of one instrument, from Upstox's raw rows.

    Args:
        positions: Upstox's short-term positions rows (any instrument; the instrument's own are
            taken), read before ``orders``.
        orders: Every raw row of Upstox's order book for today (any status: a cancelled order
            may have part-filled), read after the positions; None when it was not read.
        token: The instrument's token.

    Returns:
        ``{product: quantity}`` for every product the instrument has a row or an order in (a
        product with an order and no row stands for a row that shows nothing yet); None when it
        cannot be worked out: a field is missing or not a number, an order of the instrument
        names no side, product or filled quantity, or the figures do not add up.

    A row lags the fills, by a second or by a minute. When every product is consistent (no row
    shows more bought or sold today than the order book lists), each product's quantity is its
    row's plus what the order book's fills added since: for one product,

        held = quantity + (filled buys - day_buy_quantity) - (filled sells - day_sell_quantity)

    A conversion between products (Upstox's app, MIS to NRML) moves quantity from one row to the
    other without any order, so the rows' day figures stop matching the orders product by product.
    Then, and only then, the instrument's net is compared across its products: when the rows'
    day figures and the order book's fills add up to the same net, the rows are up to date and
    every row's quantity is used as read; when they do not, a fill the rows do not show yet cannot
    be placed in a product, and the answer is None.
    """
    if orders is None or not token:
        return None
    rows: dict[str, dict] = {}
    for row in positions:
        if not isinstance(row, dict) or str(row.get("instrument_token") or "").strip() != token:
            continue
        product = _product(row)
        if not product or product in rows:
            return None  # no product, or two rows for one product: which is which?
        rows[product] = row
    bought: dict[str, int] = {}
    sold: dict[str, int] = {}
    for order in orders:
        if not isinstance(order, dict) or str(order.get("instrument_token") or "").strip() != token:
            continue
        filled = _whole(order.get("filled_quantity"))
        side = str(order.get("transaction_type") or "").strip().upper()
        product = _product(order)
        if filled is None or side not in ("BUY", "SELL") or not product:
            return None
        book = bought if side == "BUY" else sold
        book[product] = book.get(product, 0) + filled
    figures: dict[str, tuple[int, int, int, int, int]] = {}
    for product in sorted({*rows, *bought, *sold}):
        row = rows.get(product)
        if row is None:
            quantity = day_buy = day_sell = 0
        else:
            quantity = _signed(row.get("quantity"))
            day_buy = _whole(row.get("day_buy_quantity"))
            day_sell = _whole(row.get("day_sell_quantity"))
            if quantity is None or day_buy is None or day_sell is None:
                return None
        figures[product] = (
            quantity,
            day_buy,
            day_sell,
            bought.get(product, 0),
            sold.get(product, 0),
        )
    if all(
        buys >= day_buy and sells >= day_sell
        for _q, day_buy, day_sell, buys, sells in figures.values()
    ):
        return {
            product: quantity + (buys - day_buy) - (sells - day_sell)
            for product, (quantity, day_buy, day_sell, buys, sells) in figures.items()
        }
    if len(figures) < 2:
        return None  # nothing was converted: the row shows more than the order book does
    rows_net = sum(day_buy - day_sell for _q, day_buy, day_sell, _b, _s in figures.values())
    book_net = sum(buys - sells for _q, _db, _ds, buys, sells in figures.values())
    if rows_net != book_net:
        return None
    return {product: quantity for product, (quantity, *_rest) in figures.items()}


def held_for(
    positions: Iterable[dict], orders: Iterable[dict] | None, token: str, product: str
) -> int | None:
    """``held_by_product`` for one product: 0 for one the instrument has no row or order in;
    None when it cannot be worked out."""
    held = held_by_product(positions, orders, token)
    if held is None:
        return None
    return held.get(str(product or "").strip().upper(), 0)


def held_each(positions: list[dict], orders: Iterable[dict] | None) -> list[int | None]:
    """The signed quantity held for each raw positions row, in order, from the rows of all its
    instrument's products (``held_by_product``); None for a row that cannot be worked out."""
    orders = None if orders is None else list(orders)
    by_token: dict[str, dict[str, int] | None] = {}
    out: list[int | None] = []
    for row in positions:
        if not isinstance(row, dict):
            out.append(None)
            continue
        token = str(row.get("instrument_token") or "").strip()
        if token not in by_token:
            by_token[token] = held_by_product(positions, orders, token)
        held = by_token[token]
        out.append(None if held is None else held.get(_product(row)))
    return out
