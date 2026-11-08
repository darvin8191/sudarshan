"""Exits a surface sizes itself, under the one-at-a-time rule (M6.9).

The scalping terminal's closes (blueprints/scalping.py, ``_reducing_exit``) and the strategy
module's exits (services/strategy_module/order_dispatch.py) send a plain order for the quantity
they hold on their own books, not a smart order sized by the broker module. Until this module
they stood outside ``sudarshan.books.exit_guard``: an Exit all sized from a positions row that did
not show their order yet sent an exit beside it, and one of their closes went while an exit for
the same position from the app, TradingView or Exit all was being placed or still working.

``exit_once`` puts such a send under the same rule, in the Live book only:

- a claim on the position while the order is placed (``exit_guard.one_exit``): refused with the
  busy sentence while another exit for it is being placed, or an Exit all holds the book;
- the hold check (``exit_guard.hold_verdict``) when an earlier exit for the position is held: the
  order book is read then, and only then, so a close with no hold open costs no extra call;
- the hold noted after (``exit_guard.exit_placed``) with the orders the broker named, unless the
  answer was a refusal, so the next exit for the position from any surface is held until the
  order book shows this one ended.

The surface keeps its own sizing and its own claims (the strategy module's ``claim_leg_exit``
is untouched: a refusal here is a refused dispatch, which releases that claim as any other
refusal does, and the leg stays open and managed). The sandbox is left alone: its book shows an
exit the moment it is placed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sudarshan.books import exit_guard
from sudarshan.books.single_exit import placed_order_ids
from utils.logging import get_logger

logger = get_logger(__name__)

#: ``(success, response, status)``: the shape every placement service answers with.
Answer = tuple[bool, Any, int]


def refused(answer: Answer | None) -> bool:
    """Whether an answer says nothing reached the broker: a 4xx with no order named. Anything
    else (an order id, a 5xx, a failure after the send, no answer at all) may have placed one."""
    if answer is None:
        return False
    ok, response, status = answer
    if ok:
        return False
    if isinstance(response, dict) and response.get("orderid"):
        return False
    return isinstance(status, int) and 400 <= status < 500


def order_ids(answer: Answer | None) -> tuple[str, ...]:
    """The orders the broker named in an answer, empty when it named none."""
    if answer is None or refused(answer):
        return ()
    _ok, response, _status = answer
    if not isinstance(response, dict):
        return ()
    named = []
    if response.get("orderid"):
        named.extend(placed_order_ids(response, response["orderid"]))
    for result in response.get("results") or []:  # a split order, one answer per slice
        if isinstance(result, dict) and result.get("orderid"):
            named.append(str(result["orderid"]).strip())
    return tuple(each for each in named if each)


def upstox_statuses(auth_token: str, broker: str) -> dict[str, str] | None:
    """``{order id: status}`` from the broker's order book, read with the single exit's limit
    (Upstox); None when it could not be read, or the broker is another."""
    if str(broker or "").lower() != "upstox":
        return None
    from broker.upstox.api import order_api as upstox

    rows = upstox._read_order_rows(auth_token, exit_guard.EXIT_ORDERS_READ_S)
    return upstox._order_statuses(rows)


def statuses_reader(
    api_key: str | None, auth_token: str | None = None, broker: str | None = None
) -> Callable[[], dict[str, str] | None]:
    """The order book reader for a surface that places its order by api key alone.

    The scalping terminal's close (blueprints/scalping.py) passes no broker session on purpose:
    the service layer resolves one from the api key and routes by mode. The hold check needs
    the same session to read the order book, so it is resolved here the way the service layer
    resolves it for the order (``database.auth_db.get_auth_token_broker``), and only when the
    reader is called, which is only while an earlier exit of the position is held. A session
    that cannot be resolved reads as "not read": the hold then lasts its unconfirmed time.
    """

    def read() -> dict[str, str] | None:
        token, name = auth_token, broker
        if not (token and name):
            try:
                from database.auth_db import get_auth_token_broker

                token, name = get_auth_token_broker(api_key) if api_key else (None, None)
            except Exception:
                logger.exception("The broker session for an exit's order book read was not found")
                return None
        if not (token and name):
            return None
        return upstox_statuses(token, name)

    return read


def exit_once(
    book: str,
    exchange: object,
    symbol: object,
    product: object,
    send: Callable[[], Answer],
    read_statuses: Callable[[], dict[str, str] | None],
) -> Answer:
    """Send one exit of a position that its surface sized itself, one at a time per position.

    Args:
        book: "live" or "sandbox"; only the live book is guarded.
        exchange, symbol, product: The position, however the surface spells it.
        send: Places the order and answers ``(success, response, status)``.
        read_statuses: The order book as ``{order id: status}`` (OpenAlgo's words), or None when
            it could not be read; called only while an earlier exit of the position is held.

    Returns:
        ``send``'s own answer; or ``(False, {"status": "error", "message": <why>}, 409)`` without
        calling it while the position is busy or held.
    """
    if book != "live":
        return send()
    position = exit_guard.position_of(exchange, symbol, product)
    with exit_guard.EXITS.one(book, position) as busy:
        if busy:
            logger.warning(f"Exit for {position[1]} not sent: an exit for it is on its way")
            return False, {"status": "error", "message": busy, "nothing_sent": True}, 409
        if position in exit_guard.EXITS.placed_lately(book):
            said = exit_guard.EXITS.hold_verdict(book, position, read_statuses())
            if said:
                logger.warning(f"Exit for {position[1]} not sent: an earlier exit is not ended")
                return False, {"status": "error", "message": said, "nothing_sent": True}, 409
        answer = None
        try:
            answer = send()
        finally:
            if answer is None or not refused(answer):
                exit_guard.EXITS.placed(book, position, order_ids(answer))
        return answer
