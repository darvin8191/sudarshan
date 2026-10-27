import json
import os
import re
import threading
import time

import httpx

from broker.upstox.api.rate_limiter import (
    MAX_RETRIES,
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
from sudarshan.books import exit_guard  # SUDARSHAN M6.9: an Exit all's exits, noted
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


def get_api_response(
    endpoint, auth, method="GET", payload="", base_url=UPSTOX_BASE_URL, retry_count=0
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
            response = client.get(url, headers=headers)
        elif method == "POST":
            response = client.post(url, headers=headers, content=payload)
        elif method == "PUT":
            response = client.put(url, headers=headers, content=payload)
        elif method == "DELETE":
            response = client.delete(url, headers=headers)
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
            if category == "standard" and retry_count < MAX_RETRIES:
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
                    endpoint, auth, method, payload, base_url, retry_count + 1
                )
            if category == "standard" and retry_count < MAX_RETRIES:
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
        with _get_symbol_lock(symbol, exchange, product) as symbol_lock:
            if not symbol_lock:
                return SymbolLocks.busy(symbol)
            position_size = int(data.get("position_size", "0"))

            current_position = int(get_open_position(symbol, exchange, map_product_type(product), auth))
            logger.debug(
                f"Desired position size: {position_size}, Current position: {current_position}"
            )

            if position_size == 0 and current_position == 0 and int(data.get("quantity", 0)) != 0:
                logger.debug("No existing position and quantity is specified. Placing a new order.")
                sent = True
                res, response, orderid = place_order_api(data, auth)
                # Like every other placement here: the next smart order for this
                # symbol must not read the book from before this one.
                _invalidate_position_cache(auth)
                # SUDARSHAN M6.9: a fresh order, as the request asked.
                response = _with_sent(response, data.get("action"), data.get("quantity"), False)
                return res, response, orderid

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

            if position_size != 0:
                sent = True
                res, response, orderid = place_order_api(order_data, auth)
                _invalidate_position_cache(auth)
                return res, _with_sent(response, action, quantity, False), orderid
            # SUDARSHAN M6.9: a close, sized from a book that may not show an exit placed just
            # before: not sent while its position is busy (sudarshan/books/exit_guard.py), and
            # noted, before the claim is let go, whenever it may have gone.
            with exit_guard.one_exit("live", exchange, symbol, product) as busy:
                if busy:
                    logger.warning(f"Exit for {symbol} not sent: an exit for it is on its way")
                    return _ErrorResponse(409), {"status": "error", "message": busy}, None
                sent = True
                answered = None
                try:
                    answered = place_order_api(order_data, auth)
                finally:
                    if answered is None or _exit_answer(answered[0], answered[2]) != "refused":
                        exit_guard.exit_placed("live", exchange, symbol, product)
                _invalidate_position_cache(auth)
            res, response, orderid = answered
            return res, _with_sent(response, action, quantity, True), orderid

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


def _counted(count, one, many):
    return f"{count} {one if count == 1 else many}"


# SUDARSHAN M6.9: Exit all reads the order book once, for orders still working to close a
# position; when that read fails it says so and goes on with the other checks.
_ORDERS_NOT_READ = (
    "The open orders could not be read from Upstox, so an order already working to close a "
    "position was not looked for. Check Orders."
)
_FINAL_ORDER_STATUSES = {"complete", "rejected", "cancelled"}
_CLOCK = re.compile(r"(\d{2}:\d{2}:\d{2})$")


def _working_exits(auth):
    """SUDARSHAN M6.9: the orders still working at Upstox, by what they would close.

    Returns:
        ``({(instrument_token, product, side): "HH:MM:SS" placed, or ""}, True)``, or
        ``({}, False)`` when the order book could not be read.
    """
    try:
        book = get_order_book(auth)
    except Exception:  # BrokerBusyError included: the read was refused, which is not "none"
        logger.exception("Exit all could not read the order book")
        return {}, False
    if not isinstance(book, dict) or book.get("status") != "success":
        logger.error("Exit all could not read the order book")
        return {}, False
    working = {}
    for order in book.get("data") or []:
        if not isinstance(order, dict):
            continue
        if normalize_order_status(order.get("status")) in _FINAL_ORDER_STATUSES:
            continue
        key = (
            str(order.get("instrument_token") or ""),
            str(order.get("product") or "").upper(),
            str(order.get("transaction_type") or "").upper(),
        )
        placed = _CLOCK.search(str(order.get("order_timestamp") or "").strip())
        working.setdefault(key, placed.group(1) if placed else "")
    return working, True


def _in_time(started):
    """SUDARSHAN M6.9: whether Exit all may still start an Upstox call."""
    return time.monotonic() - started < exit_guard.EXIT_ALL_SENDS_FOR_S


def _listed(names):
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


def _close_all_summary(counts, unmatched, busy, reason, skipped=(), late=(), orders_read=True):
    """SUDARSHAN M6.9: Exit all's answer when not every exit was placed with an order id.

    ``skipped`` holds a sentence for each busy position it sent nothing for, ``late`` the
    positions it had no time left for, and ``orders_read`` is False when the order book could not
    be read.
    """
    sent, refused, unknown = counts["sent"], counts["refused"], counts["unknown"]
    unread = "" if orders_read is not False else f" {_ORDERS_NOT_READ}"
    if busy and busy == refused and not unknown and not unmatched and not skipped and not late:
        # Only the order window refused (gthread only): its own sentence, as before.
        body = {
            "status": "error",
            "message": (
                f"{refused} of {sent + refused} open positions were not squared off, because "
                "Upstox allows only a limited number of orders per minute and their "
                "turn was too far away. Check your positions and square off the rest "
                f"again.{unread}"
            ),
        }
        if sent:
            body["not_confirmed"] = True
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
        *skipped,
    ]
    if late:
        names = _listed([exit_guard.trader_name(position) for position in late])
        words.append(
            f"Upstox was slow to answer, so Exit all stopped after "
            f"{exit_guard.EXIT_ALL_SENDS_FOR_S:g} s and sent nothing for {names}. Check "
            "Positions, then send Exit all again."
        )
    if refused or unmatched or unknown:
        words.append("Check Positions and Orders before sending again.")
    body = {"status": "error", "message": " ".join(words) + unread}
    if sent or unknown:
        body["not_confirmed"] = True
    if unknown:
        return body, 502
    if refused or unmatched:
        return body, 429 if busy and busy == refused else 400
    return body, 409  # only busy positions and late ones: nothing refused, nothing unknown


def close_all_positions(current_api_key, auth):
    """
    Closes all open positions.
    """
    logger.debug("Attempting to close all open positions.")
    # SUDARSHAN M6.9: every exit is decided on this Exit all's own reads. A busy position (an
    # exit for it being placed or placed lately, sudarshan/books/exit_guard.py, or an order on
    # its closing side still working) is not sent, and says why; the rest are sent at once, and
    # no Upstox call starts after exit_guard.EXIT_ALL_SENDS_FOR_S.
    started = time.monotonic()
    busy = exit_guard.busy_positions("live")  # nothing new starts while the book is held
    counts = {"sent": 0, "refused": 0, "unknown": 0}  # SUDARSHAN M6.9
    sending = None  # SUDARSHAN M6.9: the position whose exit is being sent, until answered
    try:
        positions_response = get_positions(auth)
        # SUDARSHAN M6.9: a read that failed is not an empty book, and nothing was sent.
        if positions_response.get("status") != "success":
            logger.error(f"Close all: positions could not be read: {positions_response}")
            return {"status": "error", "message": _CLOSE_ALL_NOT_READ}, 424
        if not positions_response.get("data"):
            logger.debug("No open positions found to close.")
            return {"message": "No Open Positions Found"}, 200

        refused = 0
        attempted = 0
        unmatched = 0  # SUDARSHAN M6.9
        reason = ""  # SUDARSHAN M6.9: the first refusal's own words
        skipped = []  # SUDARSHAN M6.9: why each busy position was not sent, in its own sentence
        late = []  # SUDARSHAN M6.9: the positions no time was left for
        working, orders_read = {}, None  # SUDARSHAN M6.9: the order book, read once
        if _in_time(started) and any(
            int(position.get("quantity", 0)) != 0 for position in positions_response["data"]
        ):
            working, orders_read = _working_exits(auth)
        for position in positions_response["data"]:
            if int(position.get("quantity", 0)) == 0:
                continue

            action = "SELL" if int(position["quantity"]) > 0 else "BUY"
            quantity = abs(int(position["quantity"]))
            symbol = get_symbol(position["instrument_token"], position["exchange"])

            if not symbol:
                logger.warning(
                    f"Could not find symbol for instrument token {position['instrument_token']}. Skipping position."
                )
                unmatched += 1  # SUDARSHAN M6.9: not sent, so not squared off
                continue

            place_order_payload = {
                "apikey": current_api_key,
                "strategy": "Squareoff",
                "symbol": symbol,
                "action": action,
                "exchange": position["exchange"],
                "pricetype": "MARKET",
                "product": reverse_map_product_type(position["exchange"], position["product"]),
                "quantity": str(quantity),
            }
            logger.debug(f"Closing position with payload: {place_order_payload}")
            # SUDARSHAN M6.9: a busy position is not sent, and neither is any once time is up.
            held = exit_guard.position_of(
                position["exchange"], symbol, place_order_payload["product"]
            )
            why = busy.get(held)
            closing = (
                str(position.get("instrument_token") or ""),
                str(position.get("product") or "").upper(),
                action,
            )
            if why is None and closing in working:
                why = exit_guard.order_working(held, working[closing])
            if why is not None:
                logger.warning(f"Exit all sent no exit for {symbol}: it is busy")
                skipped.append(why)
                continue
            if not _in_time(started):
                late.append(held)
                continue
            sending = [position["exchange"], symbol, place_order_payload["product"]]
            res, api_response, orderid = place_order_api(place_order_payload, auth)
            attempted += 1
            if isinstance(res, BusyResponse):
                refused += 1
            logger.debug(f"Close position response for {symbol}: {api_response}")
            # SUDARSHAN M6.9: count what this exit's own answer says.
            answer = _exit_answer(res, orderid)
            counts[answer] += 1
            if answer != "refused":
                # SUDARSHAN M6.9: busy for every exit that follows, until the book shows it.
                exit_guard.exit_placed("live", *sending)
            sending = None
            if answer == "refused" and not reason and not isinstance(res, BusyResponse):
                reason = str((api_response or {}).get("message") or "").strip()

        # Only under the gthread worker does the order window refuse a square-off
        # whose turn is too far away instead of sending it late (BusyResponse).
        # SUDARSHAN M6.9: reporting success when not every exit was placed would leave
        # those positions unwatched, whatever refused them.
        if counts["sent"] < attempted or unmatched or skipped or late:
            return _close_all_summary(
                counts, unmatched, refused, reason, skipped, late, orders_read
            )

        logger.debug("Successfully initiated closing of all open positions.")
        if orders_read is False:  # SUDARSHAN M6.9: every exit went; the orders were not looked at
            return {
                "status": "success",
                "message": "All Open Positions SquaredOff",
                "note": _ORDERS_NOT_READ,
            }, 200
        return {"status": "success", "message": "All Open Positions SquaredOff"}, 200

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

    except BrokerBusyError as exc:
        return {"status": "error", "message": str(exc)}, 429
    except Exception as e:
        logger.exception("Unexpected error modifying order")
        return {"status": "error", "message": str(e)}, 500


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
