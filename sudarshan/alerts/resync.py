"""After every subscription: what happened to the orders while nothing was listening.

OpenAlgo's proxy replays nothing on ``subscribe_orders``, so a fill during a restart of OpenAlgo,
the proxy or this service, or a deploy, would never be told. So after each successful
subscription (the day's first too) the day's order book and trade book are read once from OpenAlgo
on this server, and every order goes through the same tracker as the stream, marked found later.
The store's final rows and the part-fill maximum keep this from repeating anything already told.

  POST /api/v1/orderbook {apikey}  -> data.orders: orderid, symbol, exchange, action, quantity,
                                      pricetype, product, trigger_price, order_status (open,
                                      complete, cancelled, rejected; a stop order waiting for its
                                      trigger reads open)
  POST /api/v1/tradebook {apikey}  -> data: one row per trade: orderid, quantity, average_price

Upstox's order book as OpenAlgo maps it has no filled quantity, average price or rejection
reason, so the fills come from the trade book (summed per order, the price weighted), and a
rejection found this way names no reason. While OpenAlgo is in Analyze mode both answer from the
sandbox and say so (``"mode": "analyze"``): those rows are sandbox orders, and the live book
could not be checked.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from . import loopback

log = logging.getLogger("sudarshan.alerts")

ORDERBOOK_PATH = "/api/v1/orderbook"
TRADEBOOK_PATH = "/api/v1/tradebook"
TIMEOUT_S = 20.0
#: Exchanges a cancelled re-sync left to finish on their own (each ends within TIMEOUT_S).
_LEFT_RUNNING: set[asyncio.Future] = set()


class BookUnavailable(Exception):
    """The order book could not be read; ``str()`` says why, in words."""


class Books:
    """The day's orders as order_update messages, and whether they are the live account's."""

    def __init__(self, messages: list[dict], live: bool):
        self.messages = messages
        self.live = live


async def _post(client: httpx.AsyncClient, base_url: str, path: str, api_key: str) -> dict:
    url = base_url.rstrip("/") + path
    refused = loopback.refusal(url)
    if refused:
        raise BookUnavailable(f"the API key is not sent: {refused}")
    try:
        answer = await _shielded(client.post(url, json={"apikey": api_key}, timeout=TIMEOUT_S))
    except httpx.HTTPError:
        raise BookUnavailable("OpenAlgo did not answer") from None
    try:
        body = answer.json()
    except ValueError:
        body = None
    if answer.status_code != 200 or not isinstance(body, dict) or body.get("status") != "success":
        raise BookUnavailable("OpenAlgo answered without the order book")
    return body


async def _shielded(exchange):
    """Await one HTTP exchange so that cancelling the re-sync does not cut it short.

    A drop of the stream cancels the re-sync. httpx cancelled before it has sent its request can
    keep the socket open until garbage collection (seen with httpx 0.28, httpcore 1.0, anyio 4),
    so the exchange is left to finish or time out by itself, and its outcome is dropped.
    """
    request = asyncio.ensure_future(exchange)
    try:
        return await asyncio.shield(request)
    except asyncio.CancelledError:
        _LEFT_RUNNING.add(request)
        request.add_done_callback(_forget)
        raise


def _forget(request: asyncio.Future) -> None:
    _LEFT_RUNNING.discard(request)
    if not request.cancelled():
        request.exception()  # fetched, so asyncio does not report it as never retrieved


async def read_books(client: httpx.AsyncClient, base_url: str, api_key: str) -> Books:
    """The order book, with fills from the trade book. Raises BookUnavailable."""
    book = await _post(client, base_url, ORDERBOOK_PATH, api_key)
    data = book.get("data")
    rows = data.get("orders") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise BookUnavailable("OpenAlgo's order book came back in an unexpected shape")
    sandbox = book.get("mode") == "analyze"
    fills: dict[str, tuple[int, float]] = {}
    if not sandbox:  # sandbox rows carry their own fills
        try:
            trades = (await _post(client, base_url, TRADEBOOK_PATH, api_key)).get("data")
        except BookUnavailable as exc:
            log.info("The trade book could not be read (%s); fills found later show no price", exc)
            trades = []
        fills = fills_by_order(trades if isinstance(trades, list) else [])
    messages = [as_update(row, fills, sandbox) for row in rows if isinstance(row, dict)]
    return Books([m for m in messages if m["orderid"]], live=not sandbox)


def _number(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def fills_by_order(trades: list) -> dict[str, tuple[int, float]]:
    """{order id: (filled quantity, average price weighted by quantity)} from trade rows."""
    totals: dict[str, tuple[float, float]] = {}
    for trade in trades:
        if not isinstance(trade, dict):
            continue
        order_id = str(trade.get("orderid") or "")
        quantity = _number(trade.get("quantity"))
        price = _number(trade.get("average_price"))
        if not order_id or quantity <= 0:
            continue
        filled, value = totals.get(order_id, (0.0, 0.0))
        totals[order_id] = (filled + quantity, value + quantity * price)
    return {
        order_id: (int(filled), value / filled if filled else 0.0)
        for order_id, (filled, value) in totals.items()
    }


def as_update(row: dict, fills: dict[str, tuple[int, float]], sandbox: bool) -> dict:
    """An order book row in the shape of the proxy's order_update message."""
    order_id = str(row.get("orderid") or "")
    filled, average = fills.get(order_id, (0, 0.0))
    return {
        "type": "order_update",
        "mode": "analyze" if sandbox else "live",
        "orderid": order_id,
        "symbol": row.get("symbol"),
        "exchange": row.get("exchange"),
        "action": row.get("action"),
        "quantity": row.get("quantity"),
        "filled_quantity": row.get("filled_quantity", filled),
        "average_price": row.get("average_price") or average,
        "trigger_price": row.get("trigger_price"),
        "pricetype": row.get("pricetype"),
        "product": row.get("product"),
        "order_status": row.get("order_status"),
        "rejection_reason": row.get("rejection_reason") or "",
    }
