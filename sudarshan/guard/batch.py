"""All or nothing for orders sent together (M20.7): a basket, a multi-leg options order, a split.

Each part goes through the gate as it is sent, like any order; that alone could leave a hedged
basket half placed, one leg sent and the next refused by the price band or the size limit. So
before any part is sent, every part that adds risk is held to those two limits, and when one is
over, nothing is sent and the sentence names the part. A split is held to the size limit as the
one order it was asked as, so splitting an order never gets round the cap.

The kill switch and the daily loss limit stay with the gate, part by part: under them every part
adding risk is refused and an exit still goes, so neither can leave a batch half placed with
more risk than before.

Each service carries one marked call to this module, which answers with the service's own error
answer (its analyzer event in Analyze mode, its failure event in Live) or None when the batch may
go. With the guard not covering the pipe it returns None after one environment lookup.
"""

from __future__ import annotations

from typing import Any

from utils.logging import get_logger

from . import flag, gate, words

logger = get_logger(__name__)

Answer = tuple[bool, dict[str, Any], int]


def basket(basket_data: dict, auth_token: str, original_data: dict) -> Answer | None:
    """services/basket_order_service.process_basket_order_with_auth, before any order is sent."""
    pipe = _pipe()
    if pipe is None:
        return None
    try:
        orders = basket_data.get("orders") or []
        api_key = basket_data.get("apikey") or original_data.get("apikey")
        owner = _owner(pipe, auth_token, api_key)
        if owner is None:
            return None
        described = [_described(pipe, order, owner) for order in orders]
        found = gate.GATE.check_together(described)
    except Exception:
        logger.exception(
            "A basket could not be checked as a whole; its orders are checked one by one"
        )
        return None
    if found is None:
        return None
    index, decision = found
    part = None if index is None else index + 1  # None: the basket as a whole (the burst brake)
    message = words.together_refused("basket", part, decision.words)
    return _refused(pipe, "basket_order_service", "basketorder", original_data, message)


def split(split_data: dict, auth_token: str, original_data: dict) -> Answer | None:
    """services/split_order_service.split_order_with_auth, after its own checks of the sizes and
    before any part is sent: the whole quantity, as the one order it was asked as."""
    pipe = _pipe()
    if pipe is None:
        return None
    try:
        owner = _owner(pipe, auth_token, original_data.get("apikey"))
        if owner is None:
            return None
        whole = _described(pipe, split_data, owner)
        found = gate.GATE.check_together([whole])
    except Exception:
        logger.exception(
            "A split order could not be checked as a whole; its parts are checked one by one"
        )
        return None
    if found is None:
        return None
    _index, decision = found
    message = words.together_refused("split", None, decision.words)
    return _refused(pipe, "split_order_service", "splitorder", original_data, message)


def multi_leg(
    multiorder_data: dict,
    common_data: dict,
    auth_token: str,
    api_key: str,
    underlying_ltp: Any,
    original_data: dict,
) -> Answer | None:
    """services/options_multiorder_service.process_multiorder_with_auth, once the underlying's
    price is known and before any leg is sent: each leg resolved to its option as the service
    resolves it, at its whole quantity (a leg's own split included). A leg that cannot be resolved
    is left to the service, which reports it."""
    pipe = _pipe()
    if pipe is None:
        return None
    try:
        from services import options_multiorder_service as service

        owner = _owner(pipe, auth_token, api_key)
        if owner is None:
            return None
        described, numbers = [], []
        for number, leg in enumerate(multiorder_data.get("legs") or [], start=1):
            ok, resolved, _status = service.resolve_leg_symbol(
                leg, common_data, api_key, underlying_ltp
            )
            if not ok or not isinstance(resolved, dict) or not resolved.get("symbol"):
                continue
            order = {
                "symbol": resolved.get("symbol"),
                "exchange": resolved.get("exchange"),
                "action": leg.get("action"),
                "quantity": leg.get("quantity"),
                "pricetype": leg.get("pricetype", "MARKET"),
                "product": leg.get("product", "NRML"),
                "price": leg.get("price", 0.0),
                "trigger_price": leg.get("trigger_price", 0.0),
            }
            described.append(_described(pipe, order, owner))
            numbers.append(number)
        found = gate.GATE.check_together(described)
    except Exception:
        logger.exception(
            "A multi-leg order could not be checked as a whole; its legs are checked one by one"
        )
        return None
    if found is None:
        return None
    index, decision = found
    part = None if index is None else numbers[index]
    message = words.together_refused("multi_leg", part, decision.words)
    return _refused(pipe, "options_multiorder_service", "optionsmultiorder", original_data, message)


# -- the parts -------------------------------------------------------------------------------------


def _pipe() -> str | None:
    """Where the service will send the batch (the sandbox in Analyze mode, else Live), or None
    when the guard does not check orders there. With the guard off, one environment lookup."""
    if flag.stage() == flag.OFF:
        return None
    from database.settings_db import get_analyze_mode

    pipe = gate.SANDBOX if get_analyze_mode() else gate.LIVE
    return pipe if flag.covers(pipe) else None


def _owner(pipe: str, auth_token: str, api_key: str | None) -> str | None:
    """Whose position book the parts are judged against: the broker session, or the sandbox's
    user (as services/sandbox_service names it from the API key)."""
    if pipe == gate.LIVE:
        return auth_token or None
    if not api_key:
        return None
    from database.auth_db import verify_api_key

    user = verify_api_key(api_key)
    return str(user) if user else None


def _described(pipe: str, data: dict, owner: str) -> gate.Order:
    """One part, as the gate would describe it at the broker's order call."""
    if pipe == gate.LIVE:
        return gate._upstox_place(data, owner)
    return gate._sandbox_place(_SandboxUser(owner), data)


class _SandboxUser:
    """What the sandbox's description reads of its order manager: the user."""

    def __init__(self, user_id: str):
        self.user_id = user_id


def _refused(
    pipe: str, service_name: str, api_type: str, original_data: dict, message: str
) -> Answer:
    """The service's own error answer for a batch refused whole: 403, nothing sent."""
    import importlib

    if pipe == gate.SANDBOX:
        service = importlib.import_module(f"services.{service_name}")
        return False, service.emit_analyzer_error(original_data, message), 403
    from events import OrderFailedEvent
    from utils.event_bus import bus

    response = {"status": "error", "message": message}
    request = {key: value for key, value in original_data.items() if key != "apikey"}
    bus.publish(
        OrderFailedEvent(
            mode="live",
            api_type=api_type,
            request_data=request,
            response_data=response,
            error_message=message,
            api_key=original_data.get("apikey", ""),
        )
    )
    return False, response, 403
