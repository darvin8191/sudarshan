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
  order book shows this one ended. An answer that may have reached the broker and named no order
  (a timeout, say) is noted with the side the close closes on: the surface sizes its next close
  from Upstox's positions, which can show this one's fill a minute late, and nothing corrects
  that, so the order book settles the hold, not the 15 s note (``exit_guard``,
  ``ExitsInFlight._close_verdict``). The scalping terminal says such a close is not confirmed
  (``not_confirmed``), never in the failure's own words.

The surface asks for the quantity its own book holds, and that book can be behind: the scalping
terminal's is Upstox's positions, which can show a fill a minute late, and the strategy module's
is its own legs, which know nothing of an exit sent from another surface. A hold ends when the
order book shows the earlier exit complete, which can be before Upstox's positions show its fill;
a close sent then, at the size the surface still sees, closed the position a second time and
reversed it. So in Live a close is also sized (``read_book``, ``Book``), the way the app's own
exits are: from Upstox's positions rows corrected by the order book's fills
(``sudarshan.books.fills``), the positions read first and the order book after. It then

- sends nothing, with the sentence, when what is held cannot be worked out, when a market order
  still works on the side the close closes on, or when nothing is left to close on that side;
- sends only what is still held when that is less than the surface asked for (``resize``);
- never sends more than the surface asked for, and never an order the surface did not ask for.

A refusal for one of those reasons is kept for ``SIZING_REFUSAL_KEPT_S``, so a close tried again
on every tick costs Upstox one read of each book in that time, not one per tick.

The surface keeps its own claims (the strategy module's ``claim_leg_exit`` is untouched: a
refusal here is a refused dispatch, which releases that claim as any other refusal does, and the
leg stays open and managed). The sandbox is left alone: its book shows an exit the moment it is
placed.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sudarshan.books import exit_guard
from sudarshan.books.single_exit import EXIT_NOT_CONFIRMED, placed_order_ids
from utils import real_threading
from utils.logging import get_logger

logger = get_logger(__name__)

#: How long a close refused for its size (nothing left to close, a size that cannot be worked
#: out, a market order working) answers the same without another read of Upstox's books. The
#: strategy module tries a refused exit again on every tick, and Upstox allows 500 reads a minute.
SIZING_REFUSAL_KEPT_S = 2.0

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


#: Marks an answer of ``exit_once`` that sent nothing because the position is busy or held.
HELD = "exit_held"


def held(response: Any, status: int) -> bool:
    """Whether an answer is ``exit_once``'s own refusal: the exit was held, not rejected, because
    another exit for the position is being placed, an earlier one is not yet seen ended, or
    Upstox's books do not show (409), or cannot say (424), that the quantity is still held."""
    return status in (409, 424) and isinstance(response, dict) and response.get(HELD) is True


#: Why ``exit_once`` sent nothing, as ``why`` in its answer (``sudarshan.books.held_exits`` reads
#: it to decide what the trader is told): another exit is being placed; an earlier one is not
#: seen ended; a market order works on the closing side; the size could not be confirmed; and
#: what the books say when they do answer: nothing held, held the other way, held in another
#: product.
BUSY, EARLIER, WORKING = "busy", "earlier", "working"
UNCONFIRMED, NO_POSITION, OTHER_WAY, ELSEWHERE = (
    "unconfirmed",
    "no_position",
    "other_way",
    "elsewhere",
)


def _held_answer(said: str, status: int = 409, why: str = "") -> Answer:
    body = {"status": "error", "message": said, "nothing_sent": True, HELD: True}
    if why:
        body["why"] = why
    return False, body, status


@dataclass(frozen=True)
class Book:
    """Upstox's two books as one close needs them: the positions rows read first, the order book
    after, and what they say is held in the close's product.

    ``sized`` False: the broker is not one whose rows can be corrected (``sudarshan.books.fills``
    reads Upstox's), so the close goes at the surface's own size, as before.
    """

    statuses: dict[str, str] | None = None  # the order book for the hold check; None: not read
    sized: bool = True
    positions_read: bool = True
    orders_read: bool = True
    held: int | None = None  # signed, in the close's product; None: it cannot be worked out
    elsewhere: dict[str, int] = field(default_factory=dict)  # other products (the app's words)
    working: tuple = ()  # the MARKET orders working on the side the close closes on


#: ``(exchange, symbol, product, side) -> Book``: what ``exit_once`` sizes a close on.
BookReader = Callable[[object, object, object, object], Book]


class _Refusals:
    """The sizing refusals of the last ``SIZING_REFUSAL_KEPT_S``, by position and side. Kept under
    a real lock held for dict work only: a strategy exit and a scalping close can come from a
    green thread and a real one."""

    def __init__(self) -> None:
        self._lock = real_threading.Lock()
        self._kept: dict[tuple, tuple[float, Answer]] = {}

    def get(self, key: tuple) -> Answer | None:
        now = time.monotonic()
        with self._lock:
            kept = self._kept.get(key)
            if kept is None or now - kept[0] > SIZING_REFUSAL_KEPT_S:
                self._kept.pop(key, None)
                return None
            return kept[1]

    def keep(self, key: tuple, answer: Answer) -> Answer:
        now = time.monotonic()
        with self._lock:
            for each, (at, _answer) in list(self._kept.items()):
                if now - at > SIZING_REFUSAL_KEPT_S:
                    del self._kept[each]
            self._kept[key] = (now, answer)
        return answer

    def clear(self) -> None:
        with self._lock:
            self._kept.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._kept)


REFUSALS = _Refusals()


def kept_refusal(
    book: str, exchange: object, symbol: object, product: object, side: object
) -> Answer | None:
    """The sizing refusal ``exit_once`` would answer from its memory, without a read and without
    a send; None when it would read the books. For a surface that writes a row before each
    dispatch (the strategy module): a close tried again on every tick then writes nothing while
    the answer is only being repeated."""
    if book != "live":
        return None
    position = exit_guard.position_of(exchange, symbol, product)
    return REFUSALS.get((book, position, str(side or "").strip().upper()))


def closable(side: object, held_now: int) -> int:
    """How much of ``held_now`` (signed) an order on ``side`` closes: a SELL closes a long, a BUY
    a short; 0 when the position is flat or held the way the order would add to."""
    side = str(side or "").strip().upper()
    if side == "SELL" and held_now > 0:
        return held_now
    if side == "BUY" and held_now < 0:
        return -held_now
    return 0


def sized(position: tuple[str, str, str], side: object, quantity: object, book: Book):
    """What one close may send, from Upstox's books. Performs no I/O.

    Returns:
        ``(quantity to send, None)``; or ``(0, (sentence, status, why))`` when nothing is sent.
        The quantity is never more than the surface asked for.
    """
    try:
        asked = int(quantity)
    except (TypeError, ValueError):
        asked = 0
    if not book.positions_read:
        return 0, (exit_guard.positions_not_read(position), 424, UNCONFIRMED)
    if book.held is None or asked <= 0:
        return 0, (exit_guard.exit_not_sized(position, book.orders_read), 424, UNCONFIRMED)
    if book.working:
        return 0, (exit_guard.market_exit_working(position, list(book.working)), 409, WORKING)
    left = closable(side, book.held)
    if left:
        return min(asked, left), None
    closing = str(side or "").strip().upper() == "SELL"
    other = {
        product: held_there
        for product, held_there in book.elsewhere.items()
        if held_there and (held_there > 0) == closing
    }
    if other and not book.held:
        return 0, (exit_guard.held_in_another_product(position, other), 409, ELSEWHERE)
    why = OTHER_WAY if book.held else NO_POSITION
    return 0, (exit_guard.nothing_left_to_close(position, book.held), 409, why)


def not_confirmed(answer: Answer) -> Answer:
    """``answer`` with a close that may have reached the broker (not placed, and not a refusal:
    a timeout, a 5xx, an answer that named no order) said as not confirmed, never in the
    failure's own words ("The read operation timed out"); any other answer as it came. Marked
    ``not_confirmed``, with a 5xx status (502 when it had none)."""
    ok, response, status = answer
    if ok or refused(answer):
        return answer
    body = dict(response) if isinstance(response, dict) else {}
    body.update(status="error", message=EXIT_NOT_CONFIRMED, not_confirmed=True)
    return False, body, status if isinstance(status, int) and status >= 500 else 502


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
    (Upstox), as an ``exit_guard.OrderBook``; None when it could not be read, or the broker is
    another."""
    if str(broker or "").lower() != "upstox":
        return None
    from broker.upstox.api import order_api as upstox

    rows = upstox._read_order_rows(auth_token, exit_guard.EXIT_ORDERS_READ_S)
    return upstox._order_statuses(rows)


def upstox_book(
    auth_token: str, broker: str, exchange: object, symbol: object, product: object, side: object
) -> Book:
    """Upstox's positions rows for one instrument, then its order book, as a ``Book`` for one
    close; read through the Upstox module's own readers, the ones a smart order is sized from
    (the positions through its one-second cache, the order book with the single exit's limit)."""
    if str(broker or "").lower() != "upstox":
        return Book(sized=False)
    from broker.upstox.api import order_api as upstox

    try:
        rows = upstox._position_rows(symbol, exchange, auth_token)
    except Exception:  # a refused or failed read says nothing about the position
        logger.exception("Upstox's positions could not be read to size a close")
        return Book(positions_read=False, orders_read=False)
    orders = upstox._read_order_rows(auth_token, exit_guard.EXIT_ORDERS_READ_S)
    if orders is None:
        return Book(orders_read=False)
    statuses = upstox._order_statuses(orders)
    if not rows and not upstox.get_token(symbol, exchange):
        return Book(statuses=statuses)  # a contract the books cannot be matched to: not "flat"
    by_product = upstox._held_products(rows, symbol, exchange, orders)
    if by_product is None:
        return Book(statuses=statuses)
    letter = upstox.map_product_type(product)
    position = exit_guard.position_of(exchange, symbol, product)
    working = statuses.market_working(position, str(side or "").strip().upper())
    if working is None:
        return Book(statuses=statuses)  # it cannot say what is working: not sized
    elsewhere = {
        upstox.reverse_map_product_type(str(exchange or ""), each) or each: quantity
        for each, quantity in by_product.items()
        if each != letter and quantity
    }
    return Book(
        statuses=statuses,
        held=by_product.get(letter, 0),
        elsewhere=elsewhere,
        working=tuple(working),
    )


def _session(
    api_key: str | None, auth_token: str | None, broker: str | None
) -> tuple[str | None, str | None]:
    """The broker session a surface's order is placed with: the one it passed, else the one the
    service layer resolves from its api key (``database.auth_db.get_auth_token_broker``)."""
    if auth_token and broker:
        return auth_token, broker
    try:
        from database.auth_db import get_auth_token_broker

        token, name = get_auth_token_broker(api_key) if api_key else (None, None)
    except Exception:
        logger.exception("The broker session for an exit's order book read was not found")
        return None, None
    return (token, name) if token and name else (None, None)


def book_reader(
    api_key: str | None, auth_token: str | None = None, broker: str | None = None
) -> BookReader:
    """The reader of both books for a surface's close, with the session the order itself is
    placed with (resolved from the api key as ``statuses_reader`` does, when the surface passes
    none, and only when a close is read for). A session that cannot be resolved reads as
    positions not read: nothing is sent."""

    def read(exchange: object, symbol: object, product: object, side: object) -> Book:
        token, name = _session(api_key, auth_token, broker)
        if not (token and name):
            return Book(positions_read=False, orders_read=False)
        return upstox_book(token, name, exchange, symbol, product, side)

    return read


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
        token, name = _session(api_key, auth_token, broker)
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
    read_statuses: Callable[[], dict[str, str] | None] | None = None,
    side: object = "",
    *,
    quantity: object = None,
    read_book: BookReader | None = None,
    resize: Callable[[int], None] | None = None,
) -> Answer:
    """Send one exit of a position that its surface sized itself, one at a time per position.

    Args:
        book: "live" or "sandbox"; only the live book is guarded.
        exchange, symbol, product: The position, however the surface spells it.
        send: Places the order and answers ``(success, response, status)``.
        read_statuses: The order book as ``{order id: status}`` (OpenAlgo's words), or None when
            it could not be read; called only while an earlier exit of the position is held, and
            only when ``read_book`` is not given.
            The broker's reader (``upstox_statuses``) answers an ``exit_guard.OrderBook``, which
            can also say which market orders are working on one side of the position.
        side: The side the exit closes on (BUY or SELL). When the answer may have reached the
            broker and named no order, the hold is noted with it, and the order book settles
            it (``exit_guard.ExitsInFlight.hold_verdict``); without it, the 15 s note.
        quantity: What the surface asks to close, from its own book.
        read_book: Both of Upstox's books for this close (``book_reader``), read under the claim,
            the positions first. With it the close is sized as the module's words say: nothing is
            sent when the quantity cannot be confirmed held, and never more than is held.
        resize: Sets the order ``send`` places to a smaller quantity; called before ``send`` when
            less is held than the surface asked for. Without it such a close sends nothing.

    Returns:
        ``send``'s own answer (with ``sent_quantity`` when it was made smaller); or
        ``(False, {"status": "error", "message": <why>, ...}, 409 or 424)`` without calling it
        while the position is busy or held, or its size is not confirmed (``held``).
    """
    if book != "live":
        return send()
    position = exit_guard.position_of(exchange, symbol, product)
    with exit_guard.EXITS.one(book, position) as busy:
        if busy:
            logger.warning(f"Exit for {position[1]} not sent: an exit for it is on its way")
            return _held_answer(busy, why=BUSY)
        send_quantity = None
        if read_book is None:
            if position in exit_guard.EXITS.placed_lately(book):
                read = read_statuses() if read_statuses is not None else None
                said = exit_guard.EXITS.hold_verdict(book, position, read)
                if said:
                    logger.warning(f"Exit for {position[1]} not sent: an earlier exit is not ended")
                    return _held_answer(said, why=EARLIER)
        else:
            key = (book, position, str(side or "").strip().upper())
            kept = REFUSALS.get(key)
            if kept is not None:
                return kept
            try:
                read = read_book(exchange, symbol, product, side)
            except Exception:
                logger.exception("Upstox's books could not be read to size a close")
                read = Book(positions_read=False, orders_read=False)
            if position in exit_guard.EXITS.placed_lately(book):
                said = exit_guard.EXITS.hold_verdict(book, position, read.statuses)
                if said:
                    logger.warning(f"Exit for {position[1]} not sent: an earlier exit is not ended")
                    return _held_answer(said, why=EARLIER)
            if read.sized:
                send_quantity, refusal = sized(position, side, quantity, read)
                if refusal is None and send_quantity != int(quantity) and resize is None:
                    refusal = (exit_guard.exit_not_sized(position), 424, UNCONFIRMED)
                if refusal is not None:
                    logger.warning(f"Exit for {position[1]} not sent: its size is not confirmed")
                    return REFUSALS.keep(key, _held_answer(*refusal))
                if send_quantity != int(quantity):
                    logger.warning(
                        f"Exit for {position[1]} made smaller: {send_quantity} is held, "
                        f"{quantity} was asked"
                    )
                    resize(send_quantity)
                else:
                    send_quantity = None
        answer = None
        try:
            answer = send()
        finally:
            if answer is None or not refused(answer):
                exit_guard.EXITS.placed(book, position, order_ids(answer), str(side or ""))
        if send_quantity is not None and isinstance(answer[1], dict):
            answer = (answer[0], {**answer[1], "sent_quantity": send_quantity}, answer[2])
        return answer
