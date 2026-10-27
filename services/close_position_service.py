import copy
import importlib
from typing import Any, Dict, Optional, Tuple

from database.auth_db import get_auth_token_broker
from database.settings_db import get_analyze_mode
from events import AnalyzerErrorEvent, PositionClosedEvent
from sudarshan.books.exit_guard import close_all_once, one_exit  # SUDARSHAN M6.9
from sudarshan.books.single_exit import EXIT_NOT_CONFIRMED, exit_live, not_confirmed
from utils.broker_backpressure import BrokerBusyError
from utils.event_bus import bus
from utils.logging import get_logger

# Initialize logger
logger = get_logger(__name__)

API_TYPE = "closeposition"


def emit_analyzer_error(request_data: dict[str, Any], error_message: str) -> dict[str, Any]:
    """
    Helper function to emit analyzer error events

    Args:
        request_data: Original request data
        error_message: Error message to emit

    Returns:
        Error response dictionary
    """
    error_response = {"mode": "analyze", "status": "error", "message": error_message}

    # Store complete request data without apikey
    analyzer_request = request_data.copy()
    if "apikey" in analyzer_request:
        del analyzer_request["apikey"]
    analyzer_request["api_type"] = API_TYPE

    bus.publish(AnalyzerErrorEvent(
        mode="analyze", api_type=API_TYPE,
        request_data=analyzer_request, response_data=error_response,
        error_message=error_message,
    ))

    return error_response


def import_broker_module(broker_name: str) -> Any | None:
    """
    Dynamically import the broker-specific order API module.

    Args:
        broker_name: Name of the broker

    Returns:
        The imported module or None if import fails
    """
    try:
        module_path = f"broker.{broker_name}.api.order_api"
        broker_module = importlib.import_module(module_path)
        return broker_module
    except ImportError as error:
        logger.error(f"Error importing broker module '{module_path}': {error}")
        return None


def close_position_with_auth(
    position_data: dict[str, Any], auth_token: str, broker: str, original_data: dict[str, Any]
) -> tuple[bool, dict[str, Any], int]:
    """
    Close all positions using provided auth token.

    Args:
        position_data: Position data
        auth_token: Authentication token for the broker API
        broker: Name of the broker
        original_data: Original request data for logging

    Returns:
        Tuple containing:
        - Success status (bool)
        - Response data (dict)
        - HTTP status code (int)
    """
    position_request_data = copy.deepcopy(original_data)
    if "apikey" in position_request_data:
        position_request_data.pop("apikey", None)

    # If in analyze mode, route to sandbox for real position closing
    if get_analyze_mode():
        from services.sandbox_service import sandbox_close_position

        api_key = original_data.get("apikey")
        if not api_key:
            return (
                False,
                {
                    "status": "error",
                    "message": "API key required for sandbox mode",
                    "mode": "analyze",
                },
                400,
            )

        # Convert position_data format if needed
        close_data = {
            "symbol": position_data.get("symbol"),
            "exchange": position_data.get("exchange"),
            "product": position_data.get("product_type") or position_data.get("product"),
        }

        if close_data["symbol"]:
            # SUDARSHAN M6.9: one position's close holds it in the sandbox's book, as the
            # app's Exit does there, so the two never close it twice.
            with one_exit(
                "sandbox", close_data["exchange"], close_data["symbol"], close_data["product"]
            ) as busy:
                if busy:
                    success, status_code = False, 409
                    response = {"status": "error", "message": busy, "mode": "analyze"}
                else:
                    success, response, status_code = sandbox_close_position(
                        close_data, api_key, original_data
                    )
        else:
            success, response, status_code = sandbox_close_position(
                close_data, api_key, original_data
            )

        position_request_data["api_type"] = API_TYPE
        bus.publish(PositionClosedEvent(
            mode="analyze", api_type=API_TYPE,
            symbol=position_data.get("symbol", ""),
            exchange=position_data.get("exchange", ""),
            product=position_data.get("product_type", "") or position_data.get("product", ""),
            message=response.get("message", ""),
            request_data=position_request_data,
            response_data=response,
            api_key=api_key,
        ))

        return success, response, status_code

    broker_module = import_broker_module(broker)
    if broker_module is None:
        error_response = {"status": "error", "message": "Broker-specific module not found"}
        bus.publish(PositionClosedEvent(
            mode="live", api_type=API_TYPE,
            symbol=position_data.get("symbol", ""), exchange=position_data.get("exchange", ""),
            product=position_data.get("product_type", "") or position_data.get("product", ""),
            orderid="", message="Broker-specific module not found",
            request_data=position_request_data, response_data=error_response,
            api_key=original_data.get("apikey", ""),
        ))
        return False, error_response, 404

    # SUDARSHAN M6.9: a close that names a position (Flow's "Close Positions" node) closes that
    # position only, as the app's Exit does; upstream closed every open position here.
    if position_data.get("symbol"):
        return _close_one_live(position_data, auth_token, broker_module, original_data)

    try:
        # Use the dynamically imported module's function to close all positions
        api_key = position_data.get("apikey", "")
        # SUDARSHAN M6.9: one Exit all at a time, whichever surface sent it (the app, the API,
        # TradingView, Telegram, Flow, scripts): a second answers 409 while the first is placed,
        # and the close-all sends nothing for a position whose exit is already on its way.
        response_code, status_code = close_all_once(
            "live", broker_module.close_all_positions, api_key, auth_token
        )
    except BrokerBusyError as e:
        # Refused before it was sent: the broker's request queue was longer
        # than a caller may wait under the gthread worker. Never raised under
        # eventlet or the development server.
        logger.warning(f"Close positions not sent, broker busy: {e}")
        error_response = {"status": "error", "message": str(e)}
        bus.publish(
            PositionClosedEvent(
                mode="live",
                api_type=API_TYPE,
                symbol=position_data.get("symbol", ""),
                exchange=position_data.get("exchange", ""),
                product=position_data.get("product_type", "") or position_data.get("product", ""),
                orderid="",
                message=str(e),
                request_data=position_request_data,
                response_data=error_response,
                api_key=original_data.get("apikey", ""),
            )
        )
        return False, error_response, 429
    except Exception as e:
        logger.exception(f"Error in broker_module.close_all_positions: {e}")
        error_response = {
            "status": "error",
            "message": "Failed to close positions due to internal error",
        }
        bus.publish(PositionClosedEvent(
            mode="live", api_type=API_TYPE,
            symbol=position_data.get("symbol", ""), exchange=position_data.get("exchange", ""),
            product=position_data.get("product_type", "") or position_data.get("product", ""),
            orderid="", message="Failed to close positions due to internal error",
            request_data=position_request_data, response_data=error_response,
            api_key=original_data.get("apikey", ""),
        ))
        return False, error_response, 500

    if status_code == 200:
        response_data = {"status": "success", "message": "All Open Positions Squared Off"}
        # SUDARSHAN M6.9: every exit went; what the close-all could not check is said too.
        note = response_code.get("note") if isinstance(response_code, dict) else None
        if note:
            message = f"All Open Positions Squared Off. {note}"
            response_data = {**response_data, "message": message, "note": note}
        bus.publish(PositionClosedEvent(
            mode="live", api_type=API_TYPE,
            symbol=position_data.get("symbol", ""), exchange=position_data.get("exchange", ""),
            product=position_data.get("product_type", "") or position_data.get("product", ""),
            orderid="", message=response_data["message"],
            request_data=position_request_data, response_data=response_data,
            api_key=original_data.get("apikey", ""),
        ))
        return True, response_data, 200
    else:
        message = (
            response_code.get("message", "Failed to close positions")
            if isinstance(response_code, dict)
            else "Failed to close positions"
        )
        error_response = {"status": "error", "message": message}
        # SUDARSHAN M6.9: a close-all that sent some of its exits keeps saying so.
        if isinstance(response_code, dict) and response_code.get("not_confirmed"):
            error_response["not_confirmed"] = True
        bus.publish(PositionClosedEvent(
            mode="live", api_type=API_TYPE,
            symbol=position_data.get("symbol", ""), exchange=position_data.get("exchange", ""),
            product=position_data.get("product_type", "") or position_data.get("product", ""),
            orderid="", message=message,
            request_data=position_request_data, response_data=error_response,
            api_key=original_data.get("apikey", ""),
        ))
        return False, error_response, status_code


def _close_one_live(
    position_data: dict[str, Any],
    auth_token: str,
    broker_module: Any,
    original_data: dict[str, Any],
) -> tuple[bool, dict[str, Any], int]:
    """SUDARSHAN M6.9: close the one live position a close names, through the app's Exit.

    sudarshan/books/single_exit.py sends it as the phone's Exit does, and the broker sends
    nothing while the position is busy, saying why (broker/upstox/api/order_api.py).
    """
    position_request_data = copy.deepcopy(original_data)
    position_request_data.pop("apikey", None)
    symbol = position_data.get("symbol")
    exchange = position_data.get("exchange")
    product = position_data.get("product_type") or position_data.get("product")
    sent = None
    if not exchange or not product:
        body = {
            "status": "error",
            "message": "Missing required parameters (symbol, exchange, product)",
        }
        status_code = 400
    else:
        try:
            body, status_code, sent = exit_live(
                broker_module.place_smartorder_api,
                auth_token,
                symbol=symbol,
                exchange=exchange,
                product=product,
                strategy=position_data.get("strategy") or "Squareoff",
            )
        except BrokerBusyError as e:
            logger.warning(f"Close position for {symbol} not sent, broker busy: {e}")
            body, status_code = {"status": "error", "message": str(e)}, 429
        except Exception:
            logger.exception(f"Error closing the position {symbol}")
            body, status_code = not_confirmed(EXIT_NOT_CONFIRMED), 500  # it may have gone
    bus.publish(
        PositionClosedEvent(
            mode="live",
            api_type=API_TYPE,
            symbol=symbol or "",
            exchange=exchange or "",
            product=product or "",
            orderid=str(body.get("orderid") or ""),
            message=str(body.get("message") or ""),
            request_data=position_request_data,
            response_data={**body, "sent": sent} if sent else body,  # what it sent: the alert only
            api_key=original_data.get("apikey", ""),
        )
    )
    return status_code == 200, body, status_code


def close_position(
    position_data: dict[str, Any] = None,
    api_key: str | None = None,
    auth_token: str | None = None,
    broker: str | None = None,
) -> tuple[bool, dict[str, Any], int]:
    """
    Close all open positions.
    Supports both API-based authentication and direct internal calls.

    Args:
        position_data: Position data (optional, may contain additional parameters)
        api_key: OpenAlgo API key (for API-based calls)
        auth_token: Direct broker authentication token (for internal calls)
        broker: Direct broker name (for internal calls)

    Returns:
        Tuple containing:
        - Success status (bool)
        - Response data (dict)
        - HTTP status code (int)
    """
    if position_data is None:
        position_data = {}

    original_data = copy.deepcopy(position_data)
    if api_key:
        original_data["apikey"] = api_key

    # Case 1: API-based authentication
    if api_key and not (auth_token and broker):
        # Check if user is in semi-auto mode (closeposition is blocked in semi-auto)
        # BUT allow execution in analyze/sandbox mode (sandbox trading should always work)
        from database.auth_db import get_order_mode, verify_api_key

        # Check analyze mode first - if in analyze mode, allow execution
        if not get_analyze_mode():
            user_id = verify_api_key(api_key)
            if user_id:
                order_mode = get_order_mode(user_id)
                if order_mode == "semi_auto":
                    error_response = {
                        "status": "error",
                        "message": "Close position operation is not allowed in Semi-Auto mode. Please switch to Auto mode to close positions.",
                    }
                    logger.warning(f"Close position blocked for user {user_id} (semi-auto mode)")
                    position_request_data = copy.deepcopy(original_data)
                    if "apikey" in position_request_data:
                        position_request_data.pop("apikey", None)
                    bus.publish(PositionClosedEvent(
                        mode="live", api_type=API_TYPE,
                        symbol=position_data.get("symbol", ""), exchange=position_data.get("exchange", ""),
                        product=position_data.get("product_type", "") or position_data.get("product", ""),
                        orderid="", message=error_response["message"],
                        request_data=position_request_data, response_data=error_response,
                        api_key=api_key,
                    ))
                    return False, error_response, 403

        # Add API key to position data
        position_data["apikey"] = api_key

        AUTH_TOKEN, broker_name = get_auth_token_broker(api_key)
        if AUTH_TOKEN is None:
            error_response = {"status": "error", "message": "Invalid openalgo apikey"}
            # Skip logging for invalid API keys to prevent database flooding
            return False, error_response, 403

        return close_position_with_auth(position_data, AUTH_TOKEN, broker_name, original_data)

    # Case 2: Direct internal call with auth_token and broker
    elif auth_token and broker:
        return close_position_with_auth(position_data, auth_token, broker, original_data)

    # Case 3: Invalid parameters
    else:
        error_response = {
            "status": "error",
            "message": "Either api_key or both auth_token and broker must be provided",
        }
        return False, error_response, 400
