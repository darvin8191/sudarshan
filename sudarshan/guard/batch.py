"""All or nothing for orders sent together (M20.7): a basket, a multi-leg options order, a split
(a split order, or an options order given a split size).

Each part goes through the gate as it is sent, like any order; that alone could leave a hedged
basket half placed, one leg sent and the next refused by the price band or the size limit. So
before any part is sent, every part that adds risk is held to those two limits, and when one is
over, nothing is sent and the sentence names the part. A split is held to the size limit as the
one order it was asked as, so splitting an order never gets round the cap.

The kill switch and the daily loss limit hold a batch whole too (the coordinator's decision c):
a wing roll that closes one wing and buys a further one is either sent whole or not at all, never
half, and its refusal names the exits it carried, so the owner can send them on their own; an
exit sent on its own always goes.

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
    exits = _exits(decision, described, "Order", [n + 1 for n in range(len(described))])
    message = words.together_refused("basket", part, decision.words, exits)
    told = _told(pipe, decision, described, index)
    return _refused(pipe, "basket_order_service", "basketorder", original_data, message, told)


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
        found = gate.GATE.check_together([whole], [_slices(split_data)])
    except Exception:
        logger.exception(
            "A split order could not be checked as a whole; its parts are checked one by one"
        )
        return None
    if found is None:
        return None
    _index, decision = found
    message = words.together_refused("split", None, decision.words)
    told = _told(pipe, decision, [whole], 0)
    return _refused(pipe, "split_order_service", "splitorder", original_data, message, told)


def options_split(
    order_data: dict, auth_token: str | None, api_key: str | None, original_data: dict
) -> Answer | None:
    """services/place_options_order_service.place_options_order with a split size, once the
    option is resolved and before any slice is sent: the resolved option at its whole quantity,
    as the one order it was asked as (the gate review's M1)."""
    pipe = _pipe()
    if pipe is None:
        return None
    try:
        token = auth_token
        if pipe == gate.LIVE and not token and api_key:
            from database.auth_db import get_auth_token_broker

            token, _broker = get_auth_token_broker(api_key)
        owner = _owner(pipe, token, api_key)
        if owner is None:
            return None
        whole = _described(pipe, order_data, owner)
        found = gate.GATE.check_together([whole], [_slices(order_data)])
    except Exception:
        logger.exception(
            "An options order in slices could not be checked as a whole; its slices are "
            "checked one by one"
        )
        return None
    if found is None:
        return None
    _index, decision = found
    message = words.together_refused("split", None, decision.words)
    told = _told(pipe, decision, [whole], 0)
    return _refused(pipe, None, "optionsorder", original_data, message, told)


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
        described, numbers, slices = [], [], []
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
            slices.append(_slices(leg))
        found = gate.GATE.check_together(described, slices)
    except Exception:
        logger.exception(
            "A multi-leg order could not be checked as a whole; its legs are checked one by one"
        )
        return None
    if found is None:
        return None
    index, decision = found
    part = None if index is None else numbers[index]
    exits = _exits(decision, described, "Leg", numbers)
    message = words.together_refused("multi_leg", part, decision.words, exits)
    told = _told(pipe, decision, described, index)
    return _refused(
        pipe, "options_multiorder_service", "optionsmultiorder", original_data, message, told
    )


# -- the parts -------------------------------------------------------------------------------------


def _pipe() -> str | None:
    """Where the service will send the batch (the sandbox in Analyze mode, else Live), or None
    when the guard does not check orders there. With the guard off, one environment lookup."""
    if flag.stage() == flag.OFF:
        return None
    from database.settings_db import get_analyze_mode

    pipe = gate.SANDBOX if get_analyze_mode() else gate.LIVE
    return pipe if flag.covers(pipe) else None


def _exits(decision, described: list, label: str, numbers: list[int]) -> list[str]:
    """The exits a refused batch carried, named for the owner to send on their own."""
    named = []
    for index in decision.detail.get("exits") or []:
        if 0 <= index < len(described):
            order = described[index]
            named.append(
                words.batch_exit(label, numbers[index], order.side, order.quantity, order.name)
            )
    return named


def _slices(data: dict) -> list[int] | None:
    """The quantities a part is sent as: its split size for each full slice, then what is left
    (as services/split_order_service.py sends them); None when it is not split."""
    try:
        quantity, size = int(data.get("quantity") or 0), int(data.get("splitsize") or 0)
    except (TypeError, ValueError):
        return None
    if quantity <= 0 or size <= 0:
        return None
    return [size] * (quantity // size) + ([quantity % size] if quantity % size else [])


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


def _analyzer_error(api_type: str, original_data: dict, message: str) -> dict[str, Any]:
    """An analyzer error event for ``api_type``, as the services' own emitters publish one."""
    from events import AnalyzerErrorEvent
    from utils.event_bus import bus

    response = {"mode": "analyze", "status": "error", "message": message}
    request = {key: value for key, value in original_data.items() if key != "apikey"}
    request["api_type"] = api_type
    bus.publish(
        AnalyzerErrorEvent(
            mode="analyze",
            api_type=api_type,
            request_data=request,
            response_data=response,
            error_message=message,
        )
    )
    return response


class _SandboxUser:
    """What the sandbox's description reads of its order manager: the user."""

    def __init__(self, user_id: str):
        self.user_id = user_id


def _told(pipe: str, decision, described: list, index: int | None) -> int | None:
    """The coordinator's decision b for a batch refused whole: told on Telegram when it did not
    come from the app's own screens, merged per symbol and reason per minute (gate.told_now).
    None: not told now; else how many like it went untold since the last telling."""
    if gate._from_app() or decision.code == gate.BURST_BRAKE:  # the brake tells on its own
        return None
    part = described[index] if index is not None and 0 <= index < len(described) else None
    symbol = part.symbol if part is not None else ""
    return gate.GATE.told_now(pipe, symbol, decision.code)


def _refused(
    pipe: str,
    service_name: str | None,
    api_type: str,
    original_data: dict,
    message: str,
    told: int | None = None,
) -> Answer:
    """The service's own error answer for a batch refused whole: 403, nothing sent. In Analyze
    mode, its analyzer event (the service's own emitter, or one for ``api_type``). When it is
    told (``told``), Live's failure event carries nothing_sent, which Telegram hears; Analyze
    mode's analyzer events are not alerted, so the sandbox's is told here."""
    import importlib

    if told is not None:
        message += words.refused_again(told)
    if pipe == gate.SANDBOX:
        if told is not None:
            gate._tell_owner(words.refusal_told(message, 0, pipe))
        if service_name is not None:
            service = importlib.import_module(f"services.{service_name}")
            return False, service.emit_analyzer_error(original_data, message), 403
        return False, _analyzer_error(api_type, original_data, message), 403
    from events import OrderFailedEvent
    from utils.event_bus import bus

    response = {"status": "error", "message": message}
    if told is not None:
        response["nothing_sent"] = True  # subscribers/telegram_subscriber.on_order_failed
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
