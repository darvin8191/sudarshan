"""The app's Exit of one live position, for the phone and for Flow's close node (M6.9).

blueprints/orders.py's /close_position sends it for the phone, and services/close_position_service.py
for a Flow "Close Positions" node that names a symbol (upstream closed every open position there).
It is a smart order to a position size of 0 through the broker's ``place_smartorder_api``, which
sizes it from the book and, for Upstox, sends nothing while the position is busy, saying why
(broker/upstox/api/order_api.py, sudarshan/books/exit_guard.py).

Its answer says what became of it: the order it placed (every slice of a sliced one); "not
confirmed" (a 5xx carrying ``not_confirmed``) when it may have gone without naming an order; or a
refusal (a 4xx) when nothing was sent. A trader told an exit failed sends it again, and a second
exit sized from a book that has not caught up reverses the position, so nothing that may have
reached the broker is ever called a failure, and no answer carries an exception's words.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from utils.logging import get_logger

logger = get_logger(__name__)

EXIT_NOT_CONFIRMED = (
    "This exit is not confirmed: it may have gone through. Check Orders before sending it again."
)


def not_confirmed(message: str) -> dict[str, Any]:
    """The body of an answer that may have reached the broker."""
    return {"status": "error", "message": message, "not_confirmed": True}


def placed_order_ids(response: Any, orderid: Any) -> list[str]:
    """Every order an exit placed; Upstox names one per slice of a sliced order."""
    data = response.get("data") if isinstance(response, dict) else None
    named = data.get("order_ids") if isinstance(data, dict) else None
    ids = [str(each).strip() for each in named] if isinstance(named, (list, tuple)) else []
    return [each for each in ids if each] or [str(orderid)]


def exit_order(*, symbol: str, exchange: str, product: str, strategy: str) -> dict[str, str]:
    """The smart order that exits one position: to a position size of 0, sized by the broker."""
    return {
        "strategy": strategy,
        "exchange": exchange,
        "symbol": symbol,
        "action": "BUY",  # Will be determined by the smart order API based on current position
        "product": product,
        "pricetype": "MARKET",
        "quantity": "0",
        "price": "0",
        "trigger_price": "0",
        "disclosed_quantity": "0",
        "position_size": "0",  # Setting to 0 to close the position
    }


def exit_live(
    place_smartorder_api: Callable,
    auth_token: str,
    *,
    symbol: str,
    exchange: str,
    product: str,
    strategy: str,
) -> tuple[dict[str, Any], int, dict[str, Any] | None]:
    """Send the Exit of one live position.

    Returns:
        ``(body, status, sent)``: the answer and its HTTP status, and the side and quantity it
        sent when the broker says (for the alert only, never the answer).
    """
    order_data = exit_order(symbol=symbol, exchange=exchange, product=product, strategy=strategy)
    res, response, orderid = place_smartorder_api(order_data, auth_token)
    if orderid:
        body = {
            "status": "success",
            "message": response.get("message")
            if response and "message" in response
            else "Position close order placed successfully.",
            "orderid": orderid,
        }
        # The phone holds the exit until every slice of it has ended.
        placed = placed_order_ids(response, orderid)
        if len(placed) > 1:
            body["orderids"] = placed
        sent = response.get("sent") if isinstance(response, dict) else None
        return body, 200, sent
    # An answer from the broker that names no order (a success without an id, an error carried
    # by a 2xx, a broker 5xx, or a failure after the send) may still have placed one. Only a
    # refusal (a 4xx: the broker's, or the guard's while the position is busy) or no send at
    # all (no response) is a refusal.
    sent_status = getattr(res, "status", None)
    if isinstance(sent_status, int) and not 400 <= sent_status < 500:
        logger.error(f"Close position for {symbol} not confirmed (status {sent_status})")
        return not_confirmed(EXIT_NOT_CONFIRMED), sent_status if sent_status >= 500 else 502, None
    body = {
        "status": "error",
        "message": response.get("message")
        if response and "message" in response
        else "Failed to close position (broker did not return order ID).",
    }
    if isinstance(sent_status, int) and sent_status >= 400:
        return body, sent_status, None  # the broker's own refusal code
    return body, 400, None
