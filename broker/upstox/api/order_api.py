import contextvars  # SUDARSHAN M6.9
import json
import os
import re
import threading
import time
from contextlib import ExitStack  # SUDARSHAN M6.9

import httpx

from broker.upstox.api.rate_limiter import (
    MAX_RETRIES,
    SEND_DEADLINE,  # SUDARSHAN M6.9
    SendWithdrawn,  # SUDARSHAN M5.13
    apply_rate_limit,
    is_rate_limited,
    retry_delay_from_headers,
)
from broker.upstox.mapping.order_data import normalize_order_status  # SUDARSHAN M6.9
from broker.upstox.mapping.transform_data import (
    map_product_type,
    reverse_map_product_type,
    transform_data,
    transform_modify_order_data,
)
from database.auth_db import get_auth_token
from database.token_db import get_br_symbol, get_symbol, get_token
from sudarshan.books import exit_guard, fills  # SUDARSHAN M6.9: an Exit all's exits, noted
from sudarshan.books.single_exit import EXIT_NOT_CONFIRMED, placed_order_ids  # SUDARSHAN M6.9
from sudarshan.guard import gate as risk_gate  # SUDARSHAN M5.13: the risk gate
from utils.broker_backpressure import (
    BrokerBusyError,
    BusyResponse,
    busy_response,
    cap_server_delay,
)
from utils.httpx_client import get_httpx_client
from utils.logging import get_logger
from utils.position_read import (
    PositionReadError,
    read_position_book,
    refuse_smart_order_on_read_failure,
)
from utils.smart_order_guard import PositionBookCache, SymbolLocks

logger = get_logger(__name__)

UPSTOX_BASE_URL = "https://api.upstox.com"
# Order place/modify/cancel use the v3 API, which Upstox serves only from the
# low-latency api-hft host. Every other endpoint (order book, trade book,
# positions, holdings) has no v3 equivalent and stays on UPSTOX_BASE_URL.
UPSTOX_HFT_BASE_URL = "https://api-hft.upstox.com"

# Order-mutating path fragments. Upstox's rate-limit table (04-rate-limits.md)
# scopes the tighter order budget to Place, Modify, Cancel and Multi Order; the
# order book, trade book, positions and holdings reads below are Standard APIs
# even though they live in this file, and must not be charged to the order
# bucket or a squareoff would be queued behind the polling the UI does anyway.
_ORDER_PATH_FRAGMENTS = (
    "/order/place",
    "/order/modify",
    "/order/cancel",
    "/order/multi",
    "/order/exit",
    "/order/gtt",
)


def _rate_limit_category(endpoint, base_url):
    """Which of Upstox's two published budgets this request draws on.

    Order place/modify/cancel are the only endpoints served from the api-hft
    host, so the host alone answers it today; the path check is kept so a v2
    order endpoint added later is categorised correctly rather than silently
    charged to the standard budget.
    """
    if base_url == UPSTOX_HFT_BASE_URL:
        return "order"
    lowered = (endpoint or "").lower()
    if any(fragment in lowered for fragment in _ORDER_PATH_FRAGMENTS):
        return "order"
    return "standard"


# SUDARSHAN M6.9: a limit of its own for the reads made while it is set (Exit all's order book,
# sudarshan/books/exit_guard.EXIT_ALL_ORDERS_READ_S): cut off there and never retried.
_READ_LIMIT = contextvars.ContextVar("upstox_read_limit", default=None)


def read_timeout() -> dict:
    """SUDARSHAN M5.13: ``{"timeout": seconds}`` for a GET made while a read limit is set (the
    risk gate's reads, Exit all's order book), else nothing: the shared client's own."""
    limit = _READ_LIMIT.get()
    return {"timeout": limit} if limit else {}


def get_api_response(
    endpoint,
    auth,
    method="GET",
    payload="",
    base_url=UPSTOX_BASE_URL,
    retry_count=0,
    timeout=None,
):
    """
    A wrapper to send requests to the Upstox API and handle responses.
    Args:
        endpoint (str): The API endpoint to call.
        auth (str): The authentication token.
        method (str): The HTTP method (GET, POST, PUT, DELETE).
        payload (str): The JSON payload for POST and PUT requests.
        base_url (str): The API host, only overridden for the v3 api-hft endpoints.
        retry_count (int): Internal; how many 429 retries have already been spent.
        timeout (float): SUDARSHAN M5.13: a limit in seconds for a DELETE that must not wait for
            the shared client's own 120 s (the kill switch's cancel of every open order).
    Returns:
        dict: The JSON response from the API, or an error dictionary.
    """
    logger.debug(f"Requesting {method} on endpoint: {endpoint}")
    category = _rate_limit_category(endpoint, base_url)
    apply_rate_limit(category)
    try:
        api_key = os.getenv("BROKER_API_KEY")
        if not api_key:
            logger.error("BROKER_API_KEY environment variable not set.")
            return {"status": "error", "message": "BROKER_API_KEY not set"}

        client = get_httpx_client()
        headers = {
            "Authorization": f"Bearer {auth}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        url = f"{base_url}{endpoint}"

        if method == "GET":
            response = client.get(url, headers=headers, **read_timeout())  # SUDARSHAN M6.9
        elif method == "POST":
            response = client.post(url, headers=headers, content=payload)
        elif method == "PUT":
            response = client.put(url, headers=headers, content=payload)
        elif method == "DELETE":
            response = client.delete(
                url, headers=headers, **({"timeout": timeout} if timeout else {})
            )
        else:
            logger.error(f"Unsupported HTTP method: {method}")
            return {"status": "error", "message": f"Unsupported HTTP method: {method}"}

        response.raise_for_status()
        response_data = response.json()
        logger.debug(f"API response for {endpoint}: {response_data}")
        return response_data

    except httpx.HTTPStatusError as e:
        error_response = e.response.text
        # Reactive rate-limit handling. Only the read endpoints retry: this
        # function also carries order modify and cancel, and a 429 cannot be
        # distinguished from a response lost after Upstox accepted the request,
        # so a mutation surfaces the error and lets the caller decide. The
        # proactive pacer is what is meant to keep mutations out of this branch.
        if is_rate_limited(e.response.status_code):
            delay = None
            # SUDARSHAN M6.9: a read with a limit of its own is answered as it is.
            retries = category == "standard" and retry_count < MAX_RETRIES and not _READ_LIMIT.get()
            if retries:
                # Under gthread a delay past the data ceiling is not slept out.
                delay = cap_server_delay(
                    retry_delay_from_headers(e.response.headers, retry_count), "data"
                )
            if delay is not None:
                logger.warning(
                    f"Upstox rate limit hit on {endpoint}; retrying in {delay:.2f}s "
                    f"(attempt {retry_count + 1}/{MAX_RETRIES})"
                )
                time.sleep(delay)
                return get_api_response(
                    endpoint, auth, method, payload, base_url, retry_count + 1, timeout
                )
            if retries:
                # gthread only (cap_server_delay never answers None elsewhere):
                # Upstox asked for a wait past the ceiling. Handing its throttle
                # body back as data would read as an empty book, and an empty
                # position book sizes a smart order as if flat.
                raise BrokerBusyError(
                    retry_after=retry_delay_from_headers(e.response.headers, retry_count)
                ) from e
            logger.warning(
                f"Upstox rate limit hit on {endpoint} (category={category}); not retrying"
            )

        logger.exception(f"HTTP error on {endpoint}: {error_response}")
        try:
            body = e.response.json()
        except json.JSONDecodeError:
            body = {"status": "error", "message": f"HTTP error: {error_response}"}
        # SUDARSHAN M6.9: an Upstox 5xx does not say a cancel or a modify was not acted on.
        if e.response.status_code >= 500 and isinstance(body, dict):
            body["not_confirmed"] = True
        return body
    except Exception as e:
        logger.exception(f"Unexpected error on {endpoint}")
        # SUDARSHAN M6.9: a timeout or a dropped connection may come after Upstox acted.
        return {"status": "error", "message": str(e), "not_confirmed": True}


def get_order_book(auth):
    """Fetches the order book."""
    return get_api_response("/v2/order/retrieve-all", auth)


def get_trade_book(auth):
    """Fetches the trade book."""
    return get_api_response("/v2/order/trades/get-trades-for-day", auth)


def get_positions(auth):
    """Fetches short-term positions."""
    return get_api_response("/v2/portfolio/short-term-positions", auth)


def get_holdings(auth):
    """Fetches long-term holdings."""
    return get_api_response("/v2/portfolio/long-term-holdings", auth)


# --- Per-Symbol Smart Order Lock ---
# Ensures only one smart order per symbol executes at a time.
# Others queue and execute sequentially, each getting a fresh position book.
# The registry forgets a symbol once nobody holds or waits on it, and under the
# gthread worker a wait is bounded (utils/smart_order_guard.py).
_SMART_ORDER_LOCKS = SymbolLocks()

# --- Position Book Cache ---
# Caches get_positions() for 1 second. Invalidated after each smart order placement.
# A fetch still in flight when an order invalidates the cache is returned to its
# own caller but never cached, so the next order cannot read the book from
# before the previous fill (utils/smart_order_guard.py).
_POSITION_BOOK = PositionBookCache()


def _get_symbol_lock(symbol, exchange, product):
    """Hold the smart order lock for one symbol, as a context manager.

    Yields True while holding it. Yields False when the wait ran out, which
    happens only under the gthread worker; the caller must then return
    ``SymbolLocks.busy(symbol)`` without placing an order.
    """
    return _SMART_ORDER_LOCKS.hold(symbol, exchange, product)


def _position_book_ok(positions_data):
    """Upstox wraps a position book it read as {"status": "success", ...}."""
    return isinstance(positions_data, dict) and positions_data.get("status") == "success"


def _get_cached_positions(auth):
    """Get positions from cache if fresh, otherwise fetch from broker API."""
    return _POSITION_BOOK.get(
        auth,
        lambda: read_position_book("upstox", lambda: get_positions(auth), _position_book_ok),
    )


def _invalidate_position_cache(auth):
    """Invalidate the position cache so the next queued order fetches fresh data.

    Also stops a fetch that started before this order from caching the book
    it read.
    """
    _POSITION_BOOK.invalidate(auth)


def get_open_position(tradingsymbol, exchange, product, auth):
    """
    Gets the net quantity of an open position for a given symbol.
    """
    logger.debug(f"Getting open position for {tradingsymbol} on {exchange} with product {product}")
    try:
        br_symbol = get_br_symbol(tradingsymbol, exchange)
        positions_data = _get_cached_positions(auth)
        net_qty = "0"

        if (
            positions_data
            and positions_data.get("status") == "success"
            and positions_data.get("data")
        ):
            for position in positions_data["data"]:
                if (
                    position.get("tradingsymbol") == br_symbol
                    and position.get("exchange") == exchange
                    and position.get("product") == product
                ):
                    net_qty = position.get("quantity", "0")
                    logger.debug(f"Found open position for {tradingsymbol}: {net_qty}")
                    break
        elif positions_data.get("status") == "error":
            logger.error(f"Failed to get positions: {positions_data.get('message')}")

        return net_qty
    except (BrokerBusyError, PositionReadError):
        # A refused or failed read says nothing about the position. Reading it
        # as flat would send an order sized against a position that may be open.
        raise
    except Exception:
        logger.exception(f"Error getting open position for {tradingsymbol}")
        return "0"


def _position_rows(tradingsymbol, exchange, auth):
    """SUDARSHAN M6.9: Upstox's raw positions rows for one instrument, one per product (empty when
    it lists none).

    The same rows ``get_open_position`` reads its quantity from, through the same cache: they also
    carry the day's buy and sell quantities that ``sudarshan.books.fills`` corrects them by. A
    position converted to another product (MIS to NRML in Upstox's app) has a row in each, and an
    exit of either is sized from both. A refused or failed read raises, as there.
    """
    br_symbol = get_br_symbol(tradingsymbol, exchange)
    positions_data = _get_cached_positions(auth)
    if (
        isinstance(positions_data, dict)
        and positions_data.get("status") == "success"
        and positions_data.get("data")
    ):
        return [
            position
            for position in positions_data["data"]
            if position.get("tradingsymbol") == br_symbol and position.get("exchange") == exchange
        ]
    return []


def _read_order_rows(auth, limit):
    """SUDARSHAN M6.9: Upstox's raw order book rows, cut off after ``limit`` seconds and never
    retried, or None when it could not be read (a refused read, a failure, a cut-off)."""
    token = _READ_LIMIT.set(limit)
    try:
        book = get_order_book(auth)
    except Exception:  # BrokerBusyError included: the read was refused, which is not "none"
        logger.exception("The order book could not be read for an exit")
        return None
    finally:
        _READ_LIMIT.reset(token)
    if not isinstance(book, dict) or book.get("status") != "success":
        logger.error("The order book could not be read for an exit")
        return None
    return [row for row in book.get("data") or [] if isinstance(row, dict)]


def _order_statuses(rows):
    """SUDARSHAN M6.9: ``{order id: status}`` (OpenAlgo's words) from the order book's rows, or
    None when the book was not read."""
    if rows is None:
        return None
    return {
        str(row.get("order_id")): normalize_order_status(row.get("status"))
        for row in rows
        if row.get("order_id")
    }


def _reduces_or_crosses(current, target):
    """SUDARSHAN M6.9 (X7): whether an order to ``target`` from ``current``, as the positions row
    says, reduces the position or crosses it. One that only adds (from flat, or further the same
    way) does neither. ``target == current`` needs no order."""
    if target == current or current == 0:
        return False
    return (target > 0) != (current > 0) or abs(target) < abs(current)


def _market_exit_working(
    rows, orders, symbol, exchange, product, request_product, current_position
):
    """SUDARSHAN M6.9 (L2, Y2): the refusal (409, nothing sent) for an order that closes or
    reduces a position while the order book shows a MARKET order working on the closing side
    (SELL when long, BUY when short): one that rests at its protected price, or whose answer was
    lost to a restart, can still fill, and a second beside it would reverse the position. A
    resting stop or target (SL, SL-M, LIMIT) is not a market order and does not block. None when
    nothing blocks."""
    token = rows[0].get("instrument_token") if rows else get_token(symbol, exchange)
    closing = (
        str(token or ""),
        request_product,
        "SELL" if current_position > 0 else "BUY",
    )
    market = [
        order for order in _working_exits(orders)[0].get(closing, []) if order.kind == "MARKET"
    ]
    if not market:
        return None
    said = exit_guard.market_exit_working(exit_guard.position_of(exchange, symbol, product), market)
    return _ErrorResponse(409), {"status": "error", "message": said, "nothing_sent": True}, None


def _held_products(rows, symbol, exchange, orders):
    """SUDARSHAN M6.9: the signed quantity held in each product of one instrument, for an exit to
    be sized from: its positions rows corrected by the order book's fills across the products
    (``sudarshan.books.fills``), or None when that cannot be worked out. No row stands for a
    position the book does not list yet, so its fills alone count; an instrument Upstox has no
    token for holds nothing."""
    if rows:
        # SUDARSHAN M6.9 (Y4): rows are the instrument's own; one with no token is a row this
        # cannot match to the order book, and when none has one what is held cannot be worked
        # out (not "flat").
        tokens = (str(r.get("instrument_token") or "").strip() for r in rows)
        token = next((each for each in tokens if each), None)
        if not token:
            return None
    else:
        token = get_token(symbol, exchange)
        if not token:
            return {}
    return fills.held_by_product(rows, orders, str(token).strip())


class _ErrorResponse:
    """Minimal stand-in so callers can rely on the .status contract."""

    def __init__(self, status):
        self.status = status
        self.status_code = status


# SUDARSHAN M6.9: what an order or a cancel that may have reached Upstox says, and what one
# that broke before anything was sent says. Written for a trader, never an exception's words.
_SMART_NOT_CONFIRMED = {
    "status": "error",
    "message": "This order is not confirmed: it may have gone through. Check Orders before "
    "sending it again.",
}
_SMART_NOT_SENT = {
    "status": "error",
    "message": "The order was not sent: something went wrong before it could be. Try again.",
}
_CANCEL_NOT_CONFIRMED = (
    "This cancel is not confirmed: it may have gone through. Check the order's status before "
    "cancelling it again."
)


def _extract_error(response):
    """Flatten an Upstox error body into OpenAlgo's {status, message} shape.

    Upstox returns errors as {"status": "error", "errors": [{"errorCode": ..,
    "message": ..}]} with no top-level message, which the service layer expects.
    """
    try:
        body = response.json()
    except Exception:
        return {"status": "error", "message": response.text or "Failed to place order."}

    errors = body.get("errors") or []
    if errors:
        parts = []
        for err in errors:
            code = err.get("errorCode") or err.get("error_code")
            msg = err.get("message", "Unknown error")
            parts.append(f"{code}: {msg}" if code else msg)
        body["message"] = " | ".join(parts)
    elif "message" not in body:
        body["message"] = "Failed to place order."
    return body


def _extract_order_id(data):
    """Reads the order id out of a v3 order response body.

    v3 place returns a list under "order_ids" (a sliced order yields one id per
    slice) while modify and cancel kept v2's singular "order_id", so both shapes
    are accepted rather than silently returning None if the other one arrives.
    """
    if not isinstance(data, dict):
        return None

    order_ids = data.get("order_ids")
    if isinstance(order_ids, (list, tuple)):
        return order_ids[0] if order_ids else None
    if order_ids:
        return order_ids

    return data.get("order_id")


@risk_gate.upstox_place  # SUDARSHAN M5.13: new risk refused while the kill switch is on
def place_order_api(data, auth):
    """
    Places an order using the Upstox API.
    Returns a tuple: (httpx.Response, response_data_dict, order_id_str)
    """
    logger.debug(f"Placing order with data: {data}")
    try:
        api_key = os.getenv("BROKER_API_KEY")
        if not api_key:
            logger.error("BROKER_API_KEY not set. Cannot place order.")
            return None, {"status": "error", "message": "BROKER_API_KEY not set"}, None

        token = get_token(data["symbol"], data["exchange"])
        if not token:
            logger.error(f"Instrument token not found for {data['symbol']}. Cannot place order.")
            return None, {"status": "error", "message": "Instrument token not found"}, None

        newdata = transform_data(data, token)
        order_payload = {
            "quantity": newdata["quantity"],
            "product": newdata.get("product", "I"),
            "validity": newdata.get("validity", "DAY"),
            "price": newdata.get("price", "0"),
            "tag": newdata.get("tag", "string"),
            "instrument_token": newdata["instrument_token"],
            "order_type": newdata.get("order_type", "MARKET"),
            "transaction_type": newdata["transaction_type"],
            "disclosed_quantity": newdata.get("disclosed_quantity", "0"),
            "trigger_price": newdata.get("trigger_price", "0"),
            "is_amo": newdata.get("is_amo", False),
        }

        # transform_data only sets market_protection when the caller supplied a
        # usable value; leaving the key out is what selects Upstox's own -1
        # default, so existing orders keep behaving exactly as before.
        if "market_protection" in newdata:
            order_payload["market_protection"] = newdata["market_protection"]

        payload = json.dumps(order_payload)
        logger.debug(f"Placing order with payload: {payload}")

        client = get_httpx_client()
        headers = {
            "Authorization": f"Bearer {auth}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        # Order budget: 10/sec, 500/min, 2000/30min, five times tighter than
        # the standard one. close_all_positions() loops straight through here,
        # one call per open position, so this is the pacer that matters most.
        # No 429 retry: a rejected place is indistinguishable from a response
        # lost after Upstox accepted it, and a duplicate order is far worse
        # than a failed one.
        apply_rate_limit("order")
        response = client.post(
            f"{UPSTOX_HFT_BASE_URL}/v3/order/place", headers=headers, content=payload
        )
        response.raise_for_status()

        # Add status attribute to make response compatible with place_order_service.py
        # as the rest of the codebase expects .status instead of .status_code
        response.status = response.status_code

        response_data = response.json()
        logger.debug(f"Place order API response: {response_data}")

        if response_data.get("status") == "success":
            order_id = _extract_order_id(response_data.get("data"))
            if not order_id:
                logger.error(f"Order placed but no order id in response: {response_data}")
            logger.debug(f"Successfully placed order. Order ID: {order_id}")
            return response, response_data, order_id
        else:
            error_msg = response_data.get("message", "Failed to place order.")
            logger.error(f"Failed to place order: {error_msg} | Response: {response_data}")
            return response, response_data, None

    except SendWithdrawn as exc:
        # SUDARSHAN M5.13: the kill switch went on while this waited for its turn.
        return risk_gate.withdrawn_response(exc.text)
    except BrokerBusyError as exc:
        # Refused by the pacer before anything was sent (gthread only).
        return busy_response(str(exc))
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error placing order: {e.response.text}")
        # Preserve the .status contract expected by place_order_service.py
        e.response.status = e.response.status_code
        return e.response, _extract_error(e.response), None
    except Exception as e:
        logger.exception("Unexpected error in place_order_api")
        return _ErrorResponse(500), {"status": "error", "message": str(e)}, None


def _with_sent(response, action, quantity, closes):
    """SUDARSHAN M6.9: the side and quantity this smart order sent, beside the broker's answer.

    The request's own side and quantity are placeholders for an exit (position size 0); the alert
    that reports the order says what went (sudarshan/books/telegram_words.py). Callers that pass
    the answer on take it out first, so no API answer changes.
    """
    if not isinstance(response, dict):
        return response
    # SUDARSHAN M5.13: a reversal the risk gate cut to its exit sent only that.
    quantity = response.get("sent_quantity", quantity)
    try:  # never a failure of its own after the order went
        quantity = int(quantity or 0)
    except (TypeError, ValueError):
        return response
    sent = {"action": str(action or "").upper(), "quantity": quantity, "closes": closes}
    return {**response, "sent": sent}


@refuse_smart_order_on_read_failure
def place_smartorder_api(data, auth):
    """
    Places a smart order by comparing the desired position size with the current open position.
    """
    logger.debug(f"Placing smart order with data: {data}")
    sent = False  # SUDARSHAN M6.9: whether a failure below came after the order went
    try:
        symbol = data.get("symbol")
        exchange = data.get("exchange")
        product = data.get("product")
        # Per-symbol lock: serialize smart orders per symbol
        with _get_symbol_lock(symbol, exchange, product) as symbol_lock, ExitStack() as held:
            if not symbol_lock:
                return SymbolLocks.busy(symbol)
            position_size = int(data.get("position_size", "0"))
            # SUDARSHAN M6.9: a position size of 0 claims its position before the read it is
            # sized from (sudarshan/books/exit_guard.py) and holds it until its exit is sent and
            # noted, or nothing is sent: an Exit all or another exit that starts meanwhile then
            # leaves the position to it, however long the read takes. When the claim is refused,
            # ``busy`` says why, and nothing is sent: not a close, and not the request as given either.
            busy = None
            request_product = map_product_type(product)
            if position_size == 0:
                busy = held.enter_context(exit_guard.one_exit("live", exchange, symbol, product))

            unsized = None
            statuses = None
            by_product = None
            reducing = False  # SUDARSHAN M6.9 (Y2): a size other than 0 that reduces or crosses
            if position_size == 0:
                # SUDARSHAN M6.9: an exit is sized from the positions row corrected by what the
                # order book says was filled since (sudarshan/books/fills.py), read after the
                # row, and no clock is involved. When that cannot be worked out nothing is sent.
                rows = _position_rows(symbol, exchange, auth)
                orders = _read_order_rows(auth, exit_guard.EXIT_ORDERS_READ_S)
                statuses = _order_statuses(orders)
                by_product = _held_products(rows, symbol, exchange, orders)
                held_now = None if by_product is None else by_product.get(request_product, 0)
                if held_now is None:
                    unsized = exit_guard.exit_not_sized(
                        exit_guard.position_of(exchange, symbol, product), orders is not None
                    )
                    held_now = 0  # nothing is sent below
                current_position = held_now
            else:
                row_position = int(get_open_position(symbol, exchange, request_product, auth))
                current_position = row_position
                if _reduces_or_crosses(row_position, position_size):
                    # SUDARSHAN M6.9 (Y2): an order that reduces or crosses the position, as the
                    # row says, claims the position before the read it is sized from and holds
                    # it until its order is sent and noted, as a size-0 exit does: an Exit all
                    # that starts meanwhile leaves the position to it, and one that holds the
                    # book refuses it. The claim is let go whatever happens below, a refused
                    # dispatch included. An order that only adds never claims.
                    busy = held.enter_context(
                        exit_guard.one_exit("live", exchange, symbol, product)
                    )
                    if busy:
                        logger.warning(f"Order for {symbol} not sent: an exit for it is on its way")
                        refusal = {"status": "error", "message": busy, "nothing_sent": True}
                        return _ErrorResponse(409), refusal, None
                    # SUDARSHAN M6.9 (X7): sized as an exit is, from the row corrected by the
                    # order book's fills, read after it: the row alone lags a fill, and a
                    # reversal sized from it misses its target. An order that only adds goes as
                    # the row says, with no order book read.
                    rows = _position_rows(symbol, exchange, auth)
                    orders = _read_order_rows(auth, exit_guard.EXIT_ORDERS_READ_S)
                    statuses = _order_statuses(orders)
                    by_product = _held_products(rows, symbol, exchange, orders)
                    if by_product is None:
                        logger.warning(f"Order for {symbol} not sent: the quantity held is unknown")
                        said = exit_guard.exit_not_sized(
                            exit_guard.position_of(exchange, symbol, product), orders is not None
                        )
                        refusal = {"status": "error", "message": said, "nothing_sent": True}
                        return _ErrorResponse(424), refusal, None
                    current_position = by_product.get(request_product, 0)
                    by_product = None  # only an exit's own guards read it
                    # SUDARSHAN M6.9 (Y2, R1): a reversal or part exit repeated while the first
                    # is held (its order sent, and not yet seen ended in the order book) or still
                    # rests at Upstox would go beside it. The claim was taken on the row's word,
                    # and these checks go with it whatever the corrected figure says: after the
                    # first reversal's market order filled the position's size and rests for the
                    # other side, the corrected figure is flat or past it, and the repeat read as
                    # an add went beside the resting order and doubled the short. The working
                    # order is looked for on the side that closes what the row shows.
                    reducing = True
                    refused = _market_exit_working(
                        rows, orders, symbol, exchange, product, request_product, row_position
                    )
                    if refused:
                        logger.warning(f"Order for {symbol} not sent: a market exit is working")
                        return refused
                    held_by = exit_guard.hold_verdict("live", exchange, symbol, product, statuses)
                    if held_by:
                        logger.warning(f"Order for {symbol} not sent: an earlier one is held")
                        refusal = {"status": "error", "message": held_by, "nothing_sent": True}
                        return _ErrorResponse(409), refusal, None
            logger.debug(
                f"Desired position size: {position_size}, Current position: {current_position}"
            )

            held_by = None
            if position_size == 0 and not busy:
                # An earlier exit Upstox answered with an order id holds the position while the
                # order book shows that order working, whatever its age; a resting stop or
                # target placed earlier does not (the same as Upstox's own app).
                held_by = exit_guard.hold_verdict("live", exchange, symbol, product, statuses)
            if unsized and (busy or held_by or position_size == 0):
                # Nothing was sent: what is held cannot be confirmed (the order book that
                # corrects the positions row could not be read, or does not add up). An exit
                # for a position that is busy or held says that first.
                logger.warning(f"Exit for {symbol} not sent: the quantity held is not confirmed")
                said = busy or held_by or unsized
                refusal = {"status": "error", "message": said, "nothing_sent": True}
                return _ErrorResponse(409 if said is not unsized else 424), refusal, None

            if busy:
                # SUDARSHAN M6.9: the claim was refused (another exit of this position is being
                # placed, or Exit all holds the book): this exit is not sent, and never placed
                # as given either. A size-0 alert that finds the position flat is a late or
                # repeated exit, and sent as an order it opens the opposite position.
                logger.warning(f"Exit for {symbol} not sent: an exit for it is on its way")
                # nothing_sent: the services tell Telegram, which hears no other refusal.
                refusal = {"status": "error", "message": busy, "nothing_sent": True}
                return _ErrorResponse(409), refusal, None

            entry = (
                position_size == 0 and current_position == 0 and int(data.get("quantity", 0)) != 0
            )
            if held_by:
                logger.warning(f"Exit for {symbol} not sent: an earlier exit is not ended")
                refusal = {"status": "error", "message": held_by, "nothing_sent": True}
                return _ErrorResponse(409), refusal, None

            if position_size == 0 and current_position != 0 and not held_by:
                # SUDARSHAN M6.9 (L2): a market order still working on the closing side holds a
                # single exit, whether or not this process remembers placing it (a restart, an
                # answer that named no order). A resting stop or target is not a market order.
                refused = _market_exit_working(
                    rows, orders, symbol, exchange, product, request_product, current_position
                )
                if refused:
                    logger.warning(f"Exit for {symbol} not sent: a market exit is still working")
                    return refused

            elsewhere = {}
            if entry and by_product:
                # SUDARSHAN M6.9 (X2): a size-0 order on the side that closes what the instrument
                # holds in another product (it was converted in Upstox's app) is an exit set up
                # for the old product. Sent as given it opens the opposite position beside it.
                closing = data.get("action", "").upper() == "SELL"
                elsewhere = {
                    reverse_map_product_type(exchange, each): quantity
                    for each, quantity in by_product.items()
                    if each != request_product and quantity != 0 and (quantity > 0) == closing
                }
            if elsewhere:
                logger.warning(f"Exit for {symbol} not sent: it is held in another product")
                said = exit_guard.held_in_another_product(
                    exit_guard.position_of(exchange, symbol, product), elsewhere
                )
                return (
                    _ErrorResponse(409),
                    {"status": "error", "message": said, "nothing_sent": True},
                    None,
                )

            if entry:
                # SUDARSHAN M6.9 (Y1): a smart order to a position size of 0 never opens a
                # position. TradingView's strategy alert carries the size after the order, so its
                # only size-0 alert is an exit; from flat such an order is a late or repeated
                # exit, or the exit of an entry that never filled, and sent it would open a
                # position nobody asked for. A quantity of 0 (upstream's own exit form) is below.
                logger.warning(f"Exit for {symbol} not sent: the position is flat already")
                return (
                    None,
                    {
                        "status": "success",
                        "message": "No open position found. Not placing exit order.",
                        "nothing_sent": True,
                    },
                    None,
                )

            if position_size == current_position:
                msg = "No action needed. Position size matches current position."
                if int(data.get("quantity", 0)) == 0:
                    msg = "No open position found. Not placing exit order."
                logger.debug(msg)
                return None, {"status": "success", "message": msg}, None

            action, quantity = None, 0
            if position_size > current_position:
                action, quantity = "BUY", position_size - current_position
            else:
                action, quantity = "SELL", current_position - position_size

            logger.debug(f"Determined action: {action}, Quantity: {quantity}")

            order_data = data.copy()
            order_data["action"] = action
            order_data["quantity"] = str(quantity)

            if position_size != 0 and not reducing:
                sent = True
                res, response, orderid = place_order_api(order_data, auth)
                _invalidate_position_cache(auth)
                return res, _with_sent(response, action, quantity, False), orderid
            # SUDARSHAN M6.9: a close (or an order that reduces or crosses the position), sized
            # from a book that may not show an exit placed just before, and noted, before the
            # claim is let go, whenever it may have gone: the next exit or reducing order for the
            # position is held until the order book shows this one ended.
            sent = True
            answered = None
            try:
                answered = place_order_api(order_data, auth)
            finally:
                if answered is None or _exit_answer(answered[0], answered[2]) != "refused":
                    exit_guard.exit_placed(
                        "live",
                        exchange,
                        symbol,
                        product,
                        _named_orders(answered),
                    )
            _invalidate_position_cache(auth)
            res, response, orderid = answered
            return res, _with_sent(response, action, quantity, position_size == 0), orderid

    except BrokerBusyError as exc:
        # The position read or the order was refused by the pacer (gthread
        # only), so nothing was sent.
        return busy_response(str(exc))
    except PositionReadError:
        raise
    except Exception:
        logger.exception("Unexpected error in place_smartorder_api")
        # SUDARSHAN M6.9: after the send it is not confirmed (the answer's .status is what
        # the close-position route reads); before it, a refusal. Never an exception's words.
        if sent:
            return _ErrorResponse(500), {**_SMART_NOT_CONFIRMED, "not_confirmed": True}, None
        return None, dict(_SMART_NOT_SENT), None


# SUDARSHAN M6.9: what Exit all answers. "All Open Positions SquaredOff" only when every exit
# was placed with an order id; a positions read that failed sent nothing; anything else says how
# many exits were sent, refused and not confirmed, and is marked not_confirmed when any went or
# may have gone. A trader who believes he is flat when he is not is the worst outcome here.
_CLOSE_ALL_EMPTY_NOT_CHECKED = (
    "Upstox's positions show nothing open, but its order list did not come in time, so a "
    "position opened in the last minute may not show yet. Check Positions in a minute."
)
_CLOSE_ALL_NOT_READ = (
    "Nothing was sent: the positions could not be read from Upstox. Check Positions and try again."
)


def _exit_answer(res, orderid):
    """SUDARSHAN M6.9: "sent", "refused" or "unknown", from one exit's place_order_api answer."""
    if res is None or isinstance(res, BusyResponse):
        return "refused"  # refused before anything was sent
    status = getattr(res, "status", None)
    if isinstance(status, int) and 400 <= status < 500:
        return "refused"
    if isinstance(status, int) and status < 400 and orderid:
        return "sent"
    return "unknown"  # a 5xx, a failure after the send, or an answer naming no order


def _named_orders(answered):
    """SUDARSHAN M6.9: the order ids Upstox named in one exit's place_order_api answer, empty when
    it named none (the order book has nothing to be asked about then)."""
    if answered is None:
        return ()
    res, response, orderid = answered
    if not orderid or _exit_answer(res, orderid) != "sent":
        return ()
    return tuple(placed_order_ids(response, orderid))


def _counted(count, one, many):
    return f"{count} {one if count == 1 else many}"


# SUDARSHAN M6.9: Exit all reads the order book once, after the positions: it corrects what each
# position holds and shows the orders still working to close one. When that read fails nothing can
# be sized and nothing is sent (each position says so, exit_guard.exit_not_sized).
_FINAL_ORDER_STATUSES = {"complete", "rejected", "cancelled"}
_CLOCK = re.compile(r"(\d{2}:\d{2}:\d{2})$")


def _count(value):
    try:
        return max(int(float(value)), 0)
    except (TypeError, ValueError):
        return 0


def _amount(value):
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        return 0.0


def _shown(value):
    """SUDARSHAN M6.9 (R2): a positions row's signed quantity as shown, 0 when it says none."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _working_exits(rows):
    """SUDARSHAN M6.9: the orders still working at Upstox, by what they would close.

    Args:
        rows: Upstox's raw order book rows, or None when the book could not be read.

    Returns:
        ``({(instrument_token, product, side): [exit_guard.WorkingOrder, ...]}, True)``, or
        ``({}, False)`` when the order book could not be read.
    """
    if rows is None:
        return {}, False
    working = {}
    for order in rows:
        if normalize_order_status(order.get("status")) in _FINAL_ORDER_STATUSES:
            continue
        side = str(order.get("transaction_type") or "").upper()
        token = str(order.get("instrument_token") or "")
        key = (token, str(order.get("product") or "").upper(), side)
        placed = _CLOCK.search(str(order.get("order_timestamp") or "").strip())
        working.setdefault(key, []).append(
            exit_guard.WorkingOrder(
                side=side,
                quantity=_count(order.get("pending_quantity")) or _count(order.get("quantity")),
                kind=str(order.get("order_type") or "").upper(),
                price=_amount(order.get("price")),
                trigger=_amount(order.get("trigger_price")),
                order_id=str(order.get("order_id") or "").strip(),
                placed=placed.group(1) if placed else "",
            )
        )
    return working, True


def _in_time(started):
    """SUDARSHAN M6.9: whether Exit all may still send an exit (its reads are done)."""
    return time.monotonic() - started < exit_guard.EXIT_ALL_SENDS_FOR_S


def _listed(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


# SUDARSHAN M6.9: how many positions an Exit all's answer names one by one; the rest are counted,
# so a Telegram message stays well under its 4096 characters. The "positions" list has them all.
_SAID_AT_MOST = 10


def _named(names):
    """Up to _SAID_AT_MOST names, then how many more."""
    if len(names) <= _SAID_AT_MOST:
        return _listed(names)
    return _listed([*names[:_SAID_AT_MOST], f"{len(names) - _SAID_AT_MOST} more"])


def _outcome(held, outcome, reason=None, orderids=None):
    """SUDARSHAN M6.9: what became of one position in an Exit all: "sent", "not_confirmed" (it
    may have gone) or "not_sent" (nothing went for it), with its own reason or orders."""
    exchange, symbol, product = held
    entry = {"exchange": exchange, "symbol": symbol, "product": product, "outcome": outcome}
    if reason:
        entry["reason"] = reason
    if orderids:
        entry["orderids"] = list(orderids)
    return entry


_ERROR_CODE = re.compile(r"\b[A-Z]{3,}\d{3,}\s*:\s*")


def _refusal_words(response):
    """SUDARSHAN M6.9: Upstox's own words for a refusal, without its error code ("UDAPI100050: ")
    that a trader cannot act on; empty when it said none."""
    said = str((response or {}).get("message") or "")
    return " ".join(_ERROR_CODE.sub("", said).split())


def _exit_outcome(held, answer, res, response, orderid):
    """SUDARSHAN M6.9: one sent exit's outcome, from its own answer (``_exit_answer``)."""
    if answer == "sent":
        return _outcome(held, "sent", orderids=placed_order_ids(response, orderid))
    if answer == "unknown":
        return _outcome(held, "not_confirmed", EXIT_NOT_CONFIRMED)
    if isinstance(res, BusyResponse):
        why = (
            "Upstox allows only a limited number of orders per minute and its turn was too far "
            "away, so its exit was not sent. Exit it again."
        )
    else:
        said = _refusal_words(response)
        why = f"Upstox refused its exit: {said}" if said else "Upstox refused its exit."
    return _outcome(held, "not_sent", why)


def _late_words(names):
    """SUDARSHAN M6.9 (N7): the sentence for the exits Exit all had no time left to send. It
    names no cause: the wait may be Upstox's, or SUDARSHAN's own order pacer behind other orders."""
    them = "it" if len(names) == 1 else "them"
    return (
        f"Exit all ran out of its {exit_guard.EXIT_ALL_SENDS_FOR_S:g} seconds before it could send "
        f"an exit for {_named(names)}, so nothing was sent for {them}. Check Positions, then send "
        "Exit all again."
    )


def _close_all_summary(counts, unmatched, busy, reason, skipped=(), late=(), positions=()):
    """SUDARSHAN M6.9: Exit all's answer when not every exit was placed with an order id.

    ``skipped`` holds a sentence for each busy position it sent nothing for, ``late`` the
    positions it had no time left for, and ``positions`` is what became of each position
    (``_outcome``), carried whole while the message names at most _SAID_AT_MOST.
    """
    sent, refused, unknown = counts["sent"], counts["refused"], counts["unknown"]
    if busy and busy == refused and not unknown and not unmatched and not skipped and not late:
        # Only the order window refused (gthread only): its own sentence, as before.
        body = {
            "status": "error",
            "message": (
                f"{refused} of {sent + refused} open positions were not squared off, because "
                "Upstox allows only a limited number of orders per minute and their "
                "turn was too far away. Check your positions and square off the rest "
                "again."
            ),
        }
        if sent:
            body["not_confirmed"] = True
        if positions:
            body["positions"] = list(positions)
        return body, 429
    parts = []
    if sent:
        parts.append(_counted(sent, "was sent", "were sent"))
    if refused:
        parts.append(
            _counted(refused, "was refused", "were refused") + (f" ({reason})" if reason else "")
        )
    if unmatched:
        parts.append(
            _counted(
                unmatched,
                "could not be sent, its contract not found",
                "could not be sent, their contracts not found",
            )
        )
    if unknown:
        parts.append(
            _counted(
                unknown,
                "is not confirmed and may have gone through",
                "are not confirmed and may have gone through",
            )
        )
    if skipped:
        parts.append(_counted(len(skipped), "was not sent", "were not sent"))
    if late:
        parts.append(_counted(len(late), "was not sent in time", "were not sent in time"))
    total = sent + refused + unknown + unmatched + len(skipped) + len(late)
    words = [
        f"Exit all did not close every position. Of {_counted(total, 'exit', 'exits')}, "
        f"{_listed(parts)}.",
        *skipped[:_SAID_AT_MOST],
    ]
    if len(skipped) > _SAID_AT_MOST:
        more = _counted(len(skipped) - _SAID_AT_MOST, "more position was", "more positions were")
        words.append(f"{more} not sent either. Check Positions.")
    if late:
        words.append(_late_words([exit_guard.trader_name(position) for position in late]))
    if refused or unmatched or unknown:
        words.append("Check Positions and Orders before sending again.")
    body = {"status": "error", "message": " ".join(words)}
    if sent or unknown:
        body["not_confirmed"] = True
    if positions:
        body["positions"] = list(positions)
    if unknown:
        return body, 502
    if refused or unmatched:
        return body, 429 if busy and busy == refused else 400
    return body, 409  # only busy positions and late ones: nothing refused, nothing unknown


#: SUDARSHAN M6.9 (Y3): marks a positions row Exit all made up for an instrument and product the
#: order book shows held and Upstox's positions do not list yet. Never leaves this module.
_UNLISTED = "sudarshan_unlisted"


def _unlisted_positions(rows, orders):
    """SUDARSHAN M6.9 (Y3): the positions the order book shows traded today and held, which
    Upstox's positions do not list yet (bought a second ago), each as a row of the shape Exit all
    exits from, with the quantity held, so they are exited as every listed position is: the
    product from the order rows, the size from what the fills leave held in it
    (``sudarshan.books.fills``), and the symbol looked up from the token in the loop. One whose
    size cannot be worked out is left out with its sentence, never counted as closed. A CNC net
    sale is a sale from holdings, not an open position. An unlisted product of an instrument that
    has a row in another product is named and not sent (V1): a conversion in Upstox's app whose
    new row is not shown yet cannot be told from a fresh position, and an exit sent in the old
    product would open the opposite position."""
    candidates, skipped, outcomes = [], [], []
    listed_in = {}  # token -> the products (as the app writes them) Upstox's positions list
    for row in rows or ():
        if isinstance(row, dict) and str(row.get("instrument_token") or "").strip():
            token = str(row["instrument_token"]).strip()
            letter = str(row.get("product") or "").strip().upper()
            product = reverse_map_product_type(str(row.get("exchange") or ""), letter) or letter
            listed_in.setdefault(token, []).append(product)
    for exchange, raw_name, token, letter, held_now in fills.unlisted_nets(rows, orders):
        product = reverse_map_product_type(exchange, letter)
        if held_now is not None and held_now < 0 and product == "CNC":
            continue
        row = {
            "instrument_token": token,
            "exchange": exchange,
            "product": letter,
            "tradingsymbol": raw_name,
            "quantity": 0,
            _UNLISTED: True,
        }
        if held_now is None or not product or token in listed_in:
            # SUDARSHAN M6.9 (Z3): named as every other exit sentence names it, from the
            # OpenAlgo symbol; Upstox's own trading symbol only when the lookup finds nothing.
            name = get_symbol(token, exchange) or raw_name
            held = exit_guard.position_of(exchange, name, product or letter)
            if held_now is not None and product and token in listed_in:
                why = exit_guard.unlisted_in_another_product(held, listed_in[token])
                logger.warning(f"Exit all sent no exit for {name}: unlisted as {product}, listed")
            else:
                why = exit_guard.unlisted_not_sized(held)
                logger.warning(f"Exit all sent no exit for {name}: unlisted, and not sized")
            skipped.append(why)
            outcomes.append(_outcome(held, "not_sent", why))
            continue
        candidates.append((row, held_now))
    return candidates, skipped, outcomes


def close_all_positions(current_api_key, auth):
    """
    Closes all open positions.
    """
    logger.debug("Attempting to close all open positions.")
    # SUDARSHAN M6.9: every exit is decided on this Exit all's own reads. A busy position (an
    # exit for it being placed or placed lately, sudarshan/books/exit_guard.py, or an order on
    # its closing side still working) is not sent, and says why; the rest are sent at once, and
    # no exit is sent after exit_guard.EXIT_ALL_SENDS_FOR_S from the end of the reads.
    busy = exit_guard.busy_positions("live")  # nothing new starts while the book is held
    counts = {"sent": 0, "refused": 0, "unknown": 0}  # SUDARSHAN M6.9
    sending = None  # SUDARSHAN M6.9: the position whose exit is being sent, until answered
    try:
        positions_response = get_positions(auth)
        # SUDARSHAN M6.9: a read that failed is not an empty book, and nothing was sent.
        if positions_response.get("status") != "success":
            logger.error(f"Close all: positions could not be read: {positions_response}")
            return {"status": "error", "message": _CLOSE_ALL_NOT_READ}, 424
        listed_rows = positions_response.get("data") or []

        refused = 0
        attempted = 0
        unmatched = 0  # SUDARSHAN M6.9
        reason = ""  # SUDARSHAN M6.9: the first refusal's own words
        skipped = []  # SUDARSHAN M6.9: why each busy position was not sent, in its own sentence
        late = []  # SUDARSHAN M6.9: the positions no time was left for
        noted_here = set()  # SUDARSHAN M6.9: positions whose exit this call has noted
        outcomes = []  # SUDARSHAN M6.9: what became of each position, for the phone and the API
        # SUDARSHAN M6.9: the order book, read once and after the positions, tells what each
        # position really holds (sudarshan/books/fills.py) and which orders are still working.
        orders = _read_order_rows(auth, exit_guard.EXIT_ALL_ORDERS_READ_S)
        if orders is None and not any(_shown(row.get("quantity")) for row in listed_rows):
            # SUDARSHAN M6.9 (Y3, R2): a book that shows nothing open (empty, or only closed
            # positions at 0) may be one that has not caught up with a position opened or
            # re-entered a moment ago, and only the order book could say. With it unread, every
            # row would read as nothing held and Exit all would answer success over a position
            # it never saw: the one answer a trader must never get.
            return {"status": "error", "message": _CLOSE_ALL_EMPTY_NOT_CHECKED}, 424
        working, _read = _working_exits(orders)
        statuses = _order_statuses(orders)
        # SUDARSHAN M6.9: the time for sending starts now, so a slow read never uses it up.
        started = time.monotonic()
        # SUDARSHAN M6.9: each row corrected with the other products of its instrument.
        held_rows = fills.held_each(listed_rows, orders)
        candidates = list(zip(listed_rows, held_rows, strict=True))
        # SUDARSHAN M6.9 (Y3): a position the order book shows traded today and held, which
        # Upstox's positions do not list yet, is exited after the listed ones, from its fills,
        # through the same claim, hold, working-order check and time limit as every other.
        unlisted, left_out, left_outcomes = _unlisted_positions(listed_rows, orders)
        candidates.extend(unlisted)
        skipped.extend(left_out)
        outcomes.extend(left_outcomes)
        if not candidates and not skipped:
            logger.debug("No open positions found to close.")
            return {"message": "No Open Positions Found"}, 200
        for position, held_now in candidates:
            shown = int(position.get("quantity", 0))
            if shown == 0 and held_now in (None, 0):
                continue  # nothing held, as far as the books say
            symbol = (
                get_symbol(position["instrument_token"], position["exchange"])
                if position.get("instrument_token")
                else None
            )
            product = reverse_map_product_type(position["exchange"], position["product"])

            if position.get(_UNLISTED) and (not symbol or not product):
                # SUDARSHAN M6.9 (Y3): with no row, the contract or the product could not be
                # worked out from the order rows: left out, with its sentence, never "closed".
                held = exit_guard.position_of(
                    position["exchange"], symbol or position["tradingsymbol"], product or ""
                )
                why = exit_guard.unlisted_not_sized(held)
                logger.warning(f"Exit all sent no exit for {position['tradingsymbol']}: unlisted")
                skipped.append(why)
                outcomes.append(_outcome(held, "not_sent", why))
                continue
            if not symbol:
                logger.warning(
                    f"Could not find symbol for instrument token {position.get('instrument_token')}. Skipping position."
                )
                unmatched += 1  # SUDARSHAN M6.9: not sent, so not squared off
                continue

            held = exit_guard.position_of(position["exchange"], symbol, product)
            if held_now == 0:
                logger.debug(f"Exit all: {symbol} is closed already, as the order book shows")
                continue
            # SUDARSHAN M6.9: a busy position is not sent, and neither is any once time is up.
            # A hold this very call made does not count against a later row of the same name.
            # Busy is said before not-sized: the position is on its way out whatever it holds.
            why = busy.get(held) or (
                None
                if held in noted_here
                else exit_guard.hold_verdict(
                    "live", position["exchange"], symbol, product, statuses
                )
            )
            if why is None and held_now is None:
                # The order book could not be read, or does not add up with the rows: what is
                # held cannot be confirmed, so nothing is sent for it.
                why = exit_guard.exit_not_sized(held, orders is not None)
                logger.warning(f"Exit all sent no exit for {symbol}: the quantity is not confirmed")
            if why is not None and held_now is None:
                skipped.append(why)
                outcomes.append(_outcome(held, "not_sent", why))
                continue
            action = "SELL" if held_now > 0 else "BUY"
            quantity = abs(held_now)
            place_order_payload = {
                "apikey": current_api_key,
                "strategy": "Squareoff",
                "symbol": symbol,
                "action": action,
                "exchange": position["exchange"],
                "pricetype": "MARKET",
                "product": product,
                "quantity": str(quantity),
            }
            logger.debug(f"Closing position with payload: {place_order_payload}")
            closing = (
                str(position.get("instrument_token") or ""),
                str(position.get("product") or "").upper(),
                action,
            )
            if why is None and closing in working:
                why = exit_guard.order_working(held, working[closing], quantity)
            if why is not None:
                logger.warning(f"Exit all sent no exit for {symbol}: it is busy")
                skipped.append(why)
                outcomes.append(_outcome(held, "not_sent", why))
                continue
            if not _in_time(started):
                late.append(held)
                outcomes.append(
                    _outcome(held, "not_sent", _late_words([exit_guard.trader_name(held)]))
                )
                continue
            sending = [position["exchange"], symbol, product]
            # SUDARSHAN M6.9: the pacer's wait is part of the time, so the budget is asked again
            # when this exit's turn comes: past it, nothing is sent (it never left for Upstox).
            ran_out = []
            deadline = SEND_DEADLINE.set(
                (
                    started + exit_guard.EXIT_ALL_SENDS_FOR_S,
                    _late_words([exit_guard.trader_name(held)]),
                    ran_out,
                )
            )
            try:
                res, api_response, orderid = place_order_api(place_order_payload, auth)
            finally:
                SEND_DEADLINE.reset(deadline)
            if ran_out:
                sending = None
                late.append(held)
                outcomes.append(
                    _outcome(held, "not_sent", _late_words([exit_guard.trader_name(held)]))
                )
                continue
            attempted += 1
            if isinstance(res, BusyResponse):
                refused += 1
            logger.debug(f"Close position response for {symbol}: {api_response}")
            # SUDARSHAN M6.9: count what this exit's own answer says.
            answer = _exit_answer(res, orderid)
            counts[answer] += 1
            if answer != "refused":
                # SUDARSHAN M6.9: busy for every exit that follows, until the book shows it.
                exit_guard.exit_placed(
                    "live", *sending, _named_orders((res, api_response, orderid))
                )
                noted_here.add(held)
            sending = None
            if answer == "refused" and not reason and not isinstance(res, BusyResponse):
                reason = _refusal_words(api_response)
            outcomes.append(_exit_outcome(held, answer, res, api_response, orderid))

        # Only under the gthread worker does the order window refuse a square-off
        # whose turn is too far away instead of sending it late (BusyResponse).
        # SUDARSHAN M6.9: reporting success when not every exit was placed would leave
        # those positions unwatched, whatever refused them.
        if counts["sent"] < attempted or unmatched or skipped or late:
            return _close_all_summary(counts, unmatched, refused, reason, skipped, late, outcomes)

        logger.debug("Successfully initiated closing of all open positions.")
        done = {"status": "success", "message": "All Open Positions SquaredOff"}
        if outcomes:  # SUDARSHAN M6.9: each position's own orders, for the phone's hold
            done["positions"] = outcomes
        return done, 200

    except BrokerBusyError:
        # The positions read was refused (gthread only). The service answers
        # with the busy sentence; a generic failure would hide why.
        raise
    except Exception:
        logger.exception("An error occurred while closing all positions.")
        if sending is not None:  # SUDARSHAN M6.9: it broke while that exit was being sent
            exit_guard.exit_placed("live", *sending)
        # SUDARSHAN M6.9: it broke after some exits went: they may have gone through.
        if sum(counts.values()):
            return {
                "status": "error",
                "message": (
                    "Exit all broke off before every position was closed: some exits may have "
                    "gone through. Check Positions and Orders before sending again."
                ),
                "not_confirmed": True,
            }, 500
        return {"status": "error", "message": "Failed to close all positions"}, 500


def cancel_order(orderid, auth):
    """
    Cancels a specific order by its ID.
    """
    logger.debug(f"Attempting to cancel order ID: {orderid}")
    try:
        response_data = get_api_response(
            f"/v3/order/cancel?order_id={orderid}",
            auth,
            method="DELETE",
            base_url=UPSTOX_HFT_BASE_URL,
        )

        if response_data.get("status") == "success":
            canceled_id = _extract_order_id(response_data.get("data"))
            logger.debug(f"Successfully canceled order ID: {canceled_id}")
            risk_gate.cancelled(orderid)  # SUDARSHAN M5.13: its claims end with it
            return {"status": "success", "orderid": canceled_id}, 200
        elif response_data.get("not_confirmed"):
            # SUDARSHAN M6.9: no usable answer (a timeout, a dropped connection, an Upstox
            # 5xx) may still have cancelled it, so it is not a refusal.
            logger.error(f"Cancel of order {orderid} not confirmed: {response_data}")
            return {"status": "error", "message": _CANCEL_NOT_CONFIRMED, "not_confirmed": True}, 502
        else:
            error_msg = response_data.get("message", "Failed to cancel order")
            logger.error(
                f"Failed to cancel order {orderid}: {error_msg} | Response: {response_data}"
            )
            return {"status": "error", "message": error_msg}, 400

    except BrokerBusyError as exc:
        return {"status": "error", "message": str(exc)}, 429
    except Exception:
        logger.exception(f"Unexpected error canceling order {orderid}")
        # SUDARSHAN M6.9: it broke after the cancel went.
        return {"status": "error", "message": _CANCEL_NOT_CONFIRMED, "not_confirmed": True}, 500


@risk_gate.upstox_modify  # SUDARSHAN M5.13: new risk refused while the kill switch is on
def modify_order(data, auth):
    """
    Modifies an existing order.
    """
    logger.debug(f"Attempting to modify order with data: {data}")
    try:
        transformed_order_data = transform_modify_order_data(data)
        payload = json.dumps(transformed_order_data)
        logger.debug(f"Modify order payload: {payload}")

        response_data = get_api_response(
            "/v3/order/modify",
            auth,
            method="PUT",
            payload=payload,
            base_url=UPSTOX_HFT_BASE_URL,
        )

        if response_data.get("status") == "success":
            modified_id = _extract_order_id(response_data.get("data"))
            logger.debug(f"Successfully modified order. New Order ID: {modified_id}")
            return {"status": "success", "orderid": modified_id}, 200
        else:
            error_msg = response_data.get("message", "Failed to modify order")
            logger.error(f"Failed to modify order: {error_msg} | Response: {response_data}")
            return {"status": "error", "message": error_msg}, 400

    except SendWithdrawn as exc:  # SUDARSHAN M5.13
        return risk_gate.withdrawn_body(exc.text)
    except BrokerBusyError as exc:
        return {"status": "error", "message": str(exc)}, 429
    except Exception as e:
        logger.exception("Unexpected error modifying order")
        return {"status": "error", "message": str(e)}, 500


#: Upstox's cancel of every open order is cut off after this long: it runs when Upstox is
#: struggling (its order book could not be read), and a cut counts as not confirmed.
MULTI_CANCEL_LIMIT_S = 10.0


def _summary_count(summary, name):
    """One whole number of Upstox's cancel summary, or None when it is missing or not one."""
    if not isinstance(summary, dict) or isinstance(summary.get(name), bool):
        return None
    try:
        value = float(summary.get(name))
    except (TypeError, ValueError):
        return None
    return int(value) if value == int(value) and value >= 0 else None


def cancel_every_open_order_detail(auth):
    """SUDARSHAN M5.13: Upstox's own cancel of every open order, which needs no read of the
    order book (the kill switch's way when the book cannot be read).

    Upstox cancels at most 10 orders per request (UDAPI1110 above that, which is not done), and
    answers a mix as 207 ``partial_success``. It counts as done only when its summary proves
    every order went: ``error`` is 0 and ``success`` equals ``total``.

    Returns:
        ``{"cancelled": bool, "not_confirmed": bool, "ids": [...], "done": n, "total": n}``:
        ``cancelled`` only when every order went; ``not_confirmed`` when the answer cannot say
        (a timeout, an Upstox 5xx, a success that proves nothing), for the cancel may still have
        gone; ``ids`` are the orders Upstox says it cancelled, ``done`` and ``total`` its
        summary's counts when it gave them (else None).
    """
    result = {"cancelled": False, "not_confirmed": False, "ids": [], "done": None, "total": None}
    try:
        answer = get_api_response(
            "/v2/order/multi/cancel", auth, method="DELETE", timeout=MULTI_CANCEL_LIMIT_S
        )
    except BrokerBusyError:
        return result  # refused by the pacer before anything was sent
    except Exception:
        logger.exception("Unexpected error cancelling every open order")
        return {**result, "not_confirmed": True}
    if not isinstance(answer, dict):
        return {**result, "not_confirmed": True}
    data = answer.get("data")
    named = data.get("order_ids") if isinstance(data, dict) else None
    result["ids"] = [str(each) for each in named] if isinstance(named, (list, tuple)) else []
    summary = answer.get("summary")
    total, done, failed = (_summary_count(summary, key) for key in ("total", "success", "error"))
    result["done"], result["total"] = done, total
    status = answer.get("status")
    if status == "success":
        proven = failed == 0 and total is not None and done == total
        result["cancelled"] = proven
        result["not_confirmed"] = not proven  # a success that proves nothing may still have gone
    elif status == "partial_success":
        pass  # some went and some did not: known, not "not confirmed"
    else:
        result["ids"] = []
        result["not_confirmed"] = bool(answer.get("not_confirmed"))
    return result


def cancel_every_open_order_api(auth):
    """SUDARSHAN M5.13: ``(cancelled, not_confirmed)`` of ``cancel_every_open_order_detail``."""
    result = cancel_every_open_order_detail(auth)
    return result["cancelled"], result["not_confirmed"]


def cancel_all_orders_api(data, auth):
    """
    Cancels all open and trigger-pending orders.
    """
    logger.debug("Attempting to cancel all open orders.")
    try:
        order_book_response = get_order_book(auth)
        if order_book_response.get("status") != "success":
            logger.error(
                f"Failed to retrieve order book to cancel orders: {order_book_response.get('message')}"
            )
            # SUDARSHAN M6.9: None says the orders could not be read, which is not "no open
            # orders" (services/cancel_all_order_service.py answers that nothing was cancelled).
            return [], [], None

        orders_to_cancel = [
            order
            for order in order_book_response.get("data", [])
            if order.get("status") in ["open", "trigger pending"]
        ]

        if not orders_to_cancel:
            logger.debug("No open orders to cancel.")
            return [], []

        logger.debug(
            f"Found {len(orders_to_cancel)} orders to cancel: {[o['order_id'] for o in orders_to_cancel]}"
        )
        canceled_orders, failed_cancellations = [], []
        not_confirmed = []  # SUDARSHAN M6.9

        for order in orders_to_cancel:
            orderid = order["order_id"]
            cancel_response, status_code = cancel_order(orderid, auth)
            if status_code == 200:
                canceled_orders.append(orderid)
            else:
                failed_cancellations.append(orderid)
                # SUDARSHAN M6.9: a cancel with no usable answer may still have gone.
                if isinstance(cancel_response, dict) and cancel_response.get("not_confirmed"):
                    not_confirmed.append(orderid)

        logger.debug(
            f"Canceled {len(canceled_orders)} orders. Failed to cancel {len(failed_cancellations)} orders."
        )
        # SUDARSHAN M6.9: which of the failed ones are not confirmed rather than refused
        # (services/cancel_all_order_service.py reads a third list when a broker gives one).
        return canceled_orders, failed_cancellations, not_confirmed

    except BrokerBusyError:
        # The order book read was refused (gthread only), so nothing was
        # cancelled. Returning two empty lists would report a successful
        # cancel-all while every open order is still working.
        raise
    except Exception:
        logger.exception("An error occurred while canceling all orders.")
        return [], []
