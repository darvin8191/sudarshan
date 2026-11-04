"""Exits that could close a position twice, and what keeps them apart (M6.9).

Upstox's REST position book can say the old quantity for a second or more after
an exit, and every exit is sized from that book: Exit all sends one order per
open position, and a smart order to a position size of 0 (the app's Exit, a
TradingView alert, a script, Flow) sends what takes the position to 0. Sized
again before the book shows the first exit, a second one closes the position a
second time, which reverses it. And the phone gives up after 45 s while the
server goes on placing (one Upstox call may take 120 s), so a second tap can
arrive while the first is still being placed.

So in the Live book every exit decides on its own read of the book, and a
position is busy while

- an exit for it is being placed: claimed here from before the read it is
  sized from until after it is noted, or until it has sent nothing (``one``);
- an exit for it that Upstox answered with an order id is on hold (``Hold``) and the order book
  still shows that order working. The hold is noted here (``exit_placed``) by the app's Exit, by
  any smart order to a position size of 0, and by Exit all for each exit it sent. It ends when
  Upstox's order book shows the order complete, cancelled or rejected, and no clock decides:
  the book is read only when a second exit comes in while a hold is open. If the book cannot be
  read, or does not list the order, the hold lasts up to ``EXIT_HOLD_UNCONFIRMED_S`` from the
  answer, and the refusal says the first exit is not confirmed;
- an exit for it whose answer named no order id may have placed one the book cannot be asked
  about: that note lasts ``EXIT_SHOWS_WITHIN_S``.

A resting stop loss or target placed earlier is not a hold: it does not stop a single Exit (the
same as Upstox's own app). Exit all keeps leaving such a position out, with its own sentence.

Every exit the server sizes (Exit all, the smart order to size 0, the app's Exit, Flow's close
and the kill switch's Exit all) is sized from the positions row corrected by the order book's
fills (``sudarshan.books.fills``), never by a clock; when that cannot be worked out, Exit all
leaves the position out with a sentence and a single exit is refused.

Exit all (``close_all_once``, which every surface passes: the app's, the one at
/api/v1/closeposition that TradingView, scripts and Telegram's /closeall use,
and Flow's close-all node) takes the whole book while it places its orders, so
no other exit starts in it meanwhile (409, at once), and closes every open
position at once except the busy ones, each of which it reports as not sent in
a sentence of its own. It never waits for anything, and it starts no Upstox
call after ``EXIT_ALL_SENDS_FOR_S``.

One position's exit claims its position before the read it is sized from and
holds it across that read, so an Exit all or another exit that starts during a
slow read leaves the position to it: a read can answer older than an exit sent
while it was on its way. It is refused with that sentence (409) if its position
is busy, or an Exit all holds the book, when it claims, and then never placed as given: a smart
order to a position size of 0 that finds the position flat is a late or repeated exit. An entry
(a smart order to a size other than 0) is never refused.

The sandbox's book shows each exit the moment it is placed, so nothing is noted
there; the app's Exit still claims its position in the sandbox's book while it
is placed.

The claims and notes are per process, which is the whole app (gunicorn runs a
single worker). They are kept under a real lock from utils.real_threading that
is held for dict work only, never across I/O or a wait: correct under the
eventlet worker, under the gthread worker's real threads, and on the development
server. A claim is let go in a ``finally``, after its exit has been noted, so no
moment exists in which a position whose exit may have gone is neither claimed
nor noted.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from utils import real_threading
from utils.logging import get_logger

logger = get_logger(__name__)

EXIT_ALL_BEING_PLACED = "Exit all is still being placed. Wait for it, then check Orders."

#: How long an exit whose answer named no order id counts as placed just now: the book has no
#: order to be asked about, so only a clock can end it (frontend/src/sudarshan/books/sent.ts,
#: SENT_HOLD_MS).
EXIT_SHOWS_WITHIN_S = 15.0

#: How long a hold lasts, at most, when Upstox's order book cannot say how its order ended (it
#: could not be read, or does not list it): the phone's no-answer hold
#: (frontend/src/sudarshan/books/sent.ts, NO_ANSWER_HOLD_MS).
EXIT_HOLD_UNCONFIRMED_S = 150.0

#: A single exit reads the order book with the gate's limit (sudarshan/guard/readers.READ_LIMIT_S),
#: cut off there and never retried.
EXIT_ORDERS_READ_S = 4.0

#: Order statuses that end a hold: Upstox's order is done, whatever filled.
FINAL_STATUSES = frozenset({"complete", "rejected", "cancelled"})

#: Exit all reads the order book with a limit of its own and no retry: a read that takes longer
#: counts as not read, and Exit all goes on without it and says so.
EXIT_ALL_ORDERS_READ_S = 5.0

#: Exit all sends no exit after this long from the end of its reads. Its positions read may take
#: 120 s (utils/httpx_client.REQUEST_TIMEOUT_SECONDS) plus 7 s of Upstox's 429 back-off, the order
#: book EXIT_ALL_ORDERS_READ_S, and the last exit 120 s after up to 10 s in the order window under
#: gthread (utils/broker_backpressure): 277 s in all, inside the 300 s nginx gives a request
#: (install/install.sh). A slow read never uses up the time for sending, so a slow Upstox, when
#: Exit all matters most, still closes everything. Overlaps stay safe through the claims and
#: notes here, not through the phone's hold: a tap that comes after the phone stopped holding an
#: unanswered Exit all (frontend/src/sudarshan/books/sent.ts, NO_ANSWER_HOLD_MS) gets the server's
#: 409 and its sentence.
EXIT_ALL_SENDS_FOR_S = 15.0

IST = timezone(timedelta(hours=5, minutes=30), "IST")  # India keeps no daylight saving

Position = tuple[str, str, str]  # (exchange, symbol, product), as the app writes them

_MONTHS = "JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC"
#: OpenAlgo's derivative symbol: base, expiry DDMMMYY, then FUT or a strike and CE/PE.
_CONTRACT = re.compile(rf"^(.+?)\d{{2}}(?:{_MONTHS})\d{{2}}(?:(FUT)|(\d+(?:\.\d+)?)(CE|PE))$")
_DERIVATIVE_EXCHANGES = {"NFO", "BFO", "MCX", "CDS", "BCD", "NCDEX", "NCO"}


def _word(value: object) -> str:
    return str(value or "").strip().upper()


def position_of(exchange: object, symbol: object, product: object) -> Position:
    """A position as the app writes it, however a caller spelled it."""
    return (_word(exchange), _word(symbol), _word(product))


def trader_name(position: Position) -> str:
    """A position as the phone names it: "NIFTY 25900 CE", "BANKNIFTY FUT", "SBIN"."""
    exchange, symbol, _product = position
    parsed = _CONTRACT.match(symbol) if exchange in _DERIVATIVE_EXCHANGES else None
    if parsed is None:
        return symbol
    base, future, strike, right = parsed.groups()
    return f"{base} FUT" if future else f"{base} {strike} {right}"


def clock(at: float) -> str:
    """A moment as the trader's clock shows it (IST), to the second."""
    return datetime.fromtimestamp(at, IST).strftime("%H:%M:%S")


def on_its_way(position: Position, at: float) -> str:
    """Why no exit was sent for a position whose earlier exit went at ``at``, or is going."""
    return (
        f"An exit for {trader_name(position)} is already on its way (sent {clock(at)}). Check "
        "Orders, and exit it again only if it is still open."
    )


def first_exit_unconfirmed(position: Position, at: float) -> str:
    """Why no exit was sent for a position whose first exit (sent at ``at``) cannot be checked."""
    return (
        f"The earlier exit for {trader_name(position)} (sent {clock(at)}) is not confirmed: its "
        "order could not be checked at Upstox. Check Orders, and exit it again only if it is "
        "not there."
    )


def exit_not_sized(position: Position) -> str:
    """Why no exit was sent for a position whose quantity could not be confirmed."""
    return (
        f"No exit was sent for {trader_name(position)}: the quantity held could not be confirmed "
        "from Upstox's orders. Check Positions and Orders, then exit again."
    )


@dataclass(frozen=True)
class Hold:
    """An exit Upstox answered with order ids, held until its orders end in the order book."""

    order_ids: tuple[str, ...]  # empty: the answer named none
    started: float  # monotonic, for how long it has been held
    at: float  # epoch, for the sentence


@dataclass(frozen=True)
class WorkingOrder:
    """An order still working at the broker on a position's closing side, as Exit all read it."""

    side: str  # BUY or SELL
    quantity: int  # still to fill; 0 when the broker did not say
    kind: str = ""  # MARKET, LIMIT, SL or SL-M
    price: float = 0.0
    trigger: float = 0.0
    order_id: str = ""
    placed: str = ""  # HH:MM:SS, India's time, as the broker stamped it


def order_working(position: Position, orders: list[WorkingOrder], held: int) -> str:
    """Why Exit all sent no exit for a position with an order on its closing side still working.

    A stop loss or a target left working after the position is closed can fill later and turn it
    the other way, and with the broker's book lagging, sizing only the part not covered cannot be
    trusted: so nothing is sent for the position, and the trader is told which order to cancel.
    One that covers only part of the position stops the whole exit, and the sentence says so.

    Args:
        position: The position, as the app writes it.
        orders: Every order working on its closing side; the first is named.
        held: The position's quantity, unsigned.
    """
    first = orders[0]
    what = " ".join(part for part in (first.side, str(first.quantity or ""), first.kind) if part)
    details = []
    if first.trigger > 0:
        details.append(f"trigger {first.trigger:.2f}")
    if first.price > 0 and first.kind not in ("MARKET", "SL-M"):
        details.append(f"price {first.price:.2f}")
    if first.order_id:
        details.append(f"order ending {first.order_id[-4:]}")
    if first.placed:
        details.append(f"placed {first.placed}")
    said = f"a {what} order" + (f" ({', '.join(details)})" if details else "")
    more = len(orders) - 1
    if more:
        said += f" and {more} more {'order' if more == 1 else 'orders'}"
    covered = sum(order.quantity for order in orders)
    if 0 < covered < held:
        working = f"working to close {covered} of the {held} held"
        working += ", so no exit was sent for any of it"
    else:
        working = "working to close it, so no exit was sent for it"
    them = "them" if more else "it"
    return f"{trader_name(position)} has {said} {working}. Cancel {them} in Orders, then exit."


class ExitsInFlight:
    """The exits this process is placing and has placed just now, by book."""

    def __init__(self) -> None:
        self._lock = real_threading.Lock()
        # (book, *position) -> when its exit began (epoch seconds), while it is being placed.
        self._positions: dict[tuple[str, str, str, str], float] = {}
        self._every: set[str] = set()
        # Per book, the exits Upstox answered (or may have answered), until their orders end.
        self._placed: dict[str, dict[Position, Hold]] = {}

    def _since(self, book: str, position: Position, now: float) -> float | None:
        """When the position's latest exit began, or its latest exit with no order id was noted,
        if it is busy for a reason no order book can settle; under the lock."""
        self._forget_old(book, now)
        times = []
        began = self._positions.get((book, *position))
        if began is not None:
            times.append(began)
        noted = self._placed.get(book, {}).get(position)
        if noted is not None and not noted.order_ids:
            times.append(noted.at)
        return max(times) if times else None

    @contextmanager
    def one(self, book: str, position: Position) -> Iterator[str | None]:
        """Claim one position's exit in ``book`` ("live" or "sandbox").

        Yields:
            None while the claim is held; otherwise the sentence saying why no exit is sent
            for it now (the claim is then not taken): an Exit all holds the book, or the
            position is busy.
        """
        key = (book, *position)
        with self._lock:
            if book in self._every:
                busy, since = EXIT_ALL_BEING_PLACED, None
            else:
                busy, since = None, self._since(book, position, time.monotonic())
                if since is None:
                    self._positions[key] = time.time()
        if since is not None:
            busy = on_its_way(position, since)  # words built outside the lock
        try:
            yield busy
        finally:
            if busy is None:
                with self._lock:
                    self._positions.pop(key, None)

    @contextmanager
    def every(self, book: str) -> Iterator[str | None]:
        """Claim Exit all in ``book``: no other exit starts in it while the claim is held.

        Yields:
            None while the claim is held; otherwise the sentence saying another Exit all is
            being placed (the claim is then not taken). It never waits.
        """
        with self._lock:
            if book in self._every:
                busy = EXIT_ALL_BEING_PLACED
            else:
                self._every.add(book)
                busy = None
        try:
            yield busy
        finally:
            if busy is None:
                with self._lock:
                    self._every.discard(book)

    def why_busy(self, book: str, position: Position) -> str | None:
        """Why an exit for ``position`` would be refused right now, without claiming anything."""
        with self._lock:
            if book in self._every:
                return EXIT_ALL_BEING_PLACED
            since = self._since(book, position, time.monotonic())
        return None if since is None else on_its_way(position, since)

    def busy(self, book: str) -> dict[Position, str]:
        """Every position busy in ``book`` now for a reason no order book can settle, each with
        the sentence that says why (an exit being placed, or noted with no order id)."""
        now = time.monotonic()
        with self._lock:
            self._forget_old(book, now)
            since = {
                position: hold.at
                for position, hold in self._placed.get(book, {}).items()
                if not hold.order_ids
            }
            for (claimed_in, *position), began in self._positions.items():
                if claimed_in == book:
                    key = tuple(position)
                    since[key] = max(began, since.get(key, began))
        return {position: on_its_way(position, at) for position, at in since.items()}

    def placed(
        self, book: str, position: Position, order_ids: tuple[str, ...] = ()
    ) -> None:
        """Note that an exit for ``position`` in ``book`` may have placed an order just now, and
        which orders Upstox named for it."""
        now = time.monotonic()
        with self._lock:
            self._forget_old(book, now)
            self._placed.setdefault(book, {})[position] = Hold(order_ids, now, time.time())

    def holds(self, book: str) -> dict[Position, Hold]:
        """Every position with a hold on Upstox's orders in ``book`` now."""
        with self._lock:
            return {
                position: hold
                for position, hold in self._placed.get(book, {}).items()
                if hold.order_ids
            }

    def hold_verdict(
        self, book: str, position: Position, statuses: dict[str, str] | None
    ) -> str | None:
        """Why an exit for ``position`` is refused because of an earlier exit's orders, or None.

        Args:
            book: "live" or "sandbox".
            position: The position, as the app writes it.
            statuses: Upstox's order book as ``{order id: status}`` (OpenAlgo's words), read for
                this exit; None when it could not be read.

        A hold ends here, for good, when every order of it shows complete, cancelled or rejected;
        a hold whose orders cannot be seen ends only ``EXIT_HOLD_UNCONFIRMED_S`` after its answer.
        """
        with self._lock:
            hold = self._placed.get(book, {}).get(position)
        if hold is None or not hold.order_ids:
            return None
        seen = None if statuses is None else [statuses.get(each) for each in hold.order_ids]
        if seen is not None and all(each in FINAL_STATUSES for each in seen):
            ended = True
            said = None
        elif seen is not None and any(each is not None and each not in FINAL_STATUSES for each in seen):
            ended = False
            said = on_its_way(position, hold.at)
        else:  # not read, or an order the book does not list
            ended = time.monotonic() - hold.started > EXIT_HOLD_UNCONFIRMED_S
            said = None if ended else first_exit_unconfirmed(position, hold.at)
        if ended:
            with self._lock:
                recent = self._placed.get(book)
                if recent is not None and recent.get(position) is hold:
                    del recent[position]
                    if not recent:
                        del self._placed[book]
        return said

    def unplaced(self, book: str, position: Position) -> None:
        """Forget the note that an exit for ``position`` placed an order: that order was
        cancelled (M5.13: the kill switch cancels a resting exit), so none is on its way."""
        with self._lock:
            recent = self._placed.get(book)
            if recent is not None:
                recent.pop(position, None)
                if not recent:
                    del self._placed[book]

    def placed_lately(self, book: str) -> set[Position]:
        """The positions with a note or a hold of an exit in ``book``."""
        with self._lock:
            self._forget_old(book, time.monotonic())
            return set(self._placed.get(book, {}))

    def _forget_old(self, book: str, now: float) -> None:
        """Drop the notes that name no order and are older than ``EXIT_SHOWS_WITHIN_S``, in
        ``book``; call under the lock. A hold on Upstox's orders is never dropped here: only the
        order book ends it, or ``EXIT_HOLD_UNCONFIRMED_S`` when the book cannot say, at the next
        exit for the position (``hold_verdict``), so no timer is needed."""
        recent = self._placed.get(book)
        if recent is None:
            return
        for position, hold in list(recent.items()):
            if not hold.order_ids and now - hold.started > EXIT_SHOWS_WITHIN_S:
                del recent[position]
        if not recent:
            del self._placed[book]

    def every_held(self, book: str) -> bool:
        """Whether an Exit all holds ``book`` now (M5.13: the kill switch's resume waits)."""
        with self._lock:
            return book in self._every

    def __len__(self) -> int:
        """How many claims are held right now."""
        with self._lock:
            return len(self._positions) + len(self._every)


EXITS = ExitsInFlight()


def one_exit(book: str, exchange: object, symbol: object, product: object):
    """``EXITS.one`` for a position however its caller spelled it."""
    return EXITS.one(book, position_of(exchange, symbol, product))


def why_busy(book: str, exchange: object, symbol: object, product: object) -> str | None:
    """Why an exit for this position would be refused right now (``EXITS.why_busy``).

    The phone's Exit asks first, so a second tap is answered at once instead of waiting behind
    the first for the broker's per-symbol lock; the claim itself is taken under that lock,
    before the read the exit is sized from (broker/upstox/api/order_api.py).
    """
    return EXITS.why_busy(book, position_of(exchange, symbol, product))


def exit_placed(
    book: str,
    exchange: object,
    symbol: object,
    product: object,
    order_ids: object = (),
) -> None:
    """Note that an exit for this position may have placed an order just now.

    Called by every exit in the Live book that was not refused, before its claim (or, for Exit
    all, the book's) is let go: a second exit that came before the order book shows this one
    ended would close the position again. ``order_ids`` are the orders Upstox named for it.
    """
    ids = tuple(str(each).strip() for each in (order_ids or ()) if str(each or "").strip())
    EXITS.placed(book, position_of(exchange, symbol, product), ids)


def exit_cancelled(book: str, exchange: object, symbol: object, product: object) -> None:
    """The order an exit for this position placed was cancelled: it no longer counts as on its
    way (``EXITS.unplaced``)."""
    EXITS.unplaced(book, position_of(exchange, symbol, product))


def exit_holds(book: str) -> dict[Position, Hold]:
    """Every position with a hold on Upstox's orders in ``book`` (``EXITS.holds``)."""
    return EXITS.holds(book)


def hold_verdict(
    book: str, exchange: object, symbol: object, product: object, statuses: dict[str, str] | None
) -> str | None:
    """Why an exit for this position is refused because of an earlier exit's orders
    (``EXITS.hold_verdict``), or None."""
    return EXITS.hold_verdict(book, position_of(exchange, symbol, product), statuses)


def exit_all_held(book: str) -> bool:
    """Whether an Exit all is being placed in ``book`` now (``EXITS.every_held``)."""
    return EXITS.every_held(book)


def busy_positions(book: str) -> dict[Position, str]:
    """Every position busy in ``book`` now, each with the sentence that says why (``EXITS.busy``)."""
    return EXITS.busy(book)


def close_all_once(book: str, close_all: Callable, *args):
    """Run one Exit all in ``book`` while holding the whole book, whichever surface it came from.

    Args:
        book: "live" or "sandbox".
        close_all: The broker's ``close_all_positions``, answering ``(body, status)``. It skips
            the busy positions (``busy_positions``) and notes each exit it sends
            (``exit_placed``), while the book is still held.
        *args: What ``close_all`` takes.

    Returns:
        ``close_all``'s own answer; or ``({"status": "error", "message": <why>}, 409)`` at once,
        without calling it, while another Exit all runs in that book.
    """
    with EXITS.every(book) as busy:
        if busy:
            logger.warning(f"Exit all refused while another is being placed ({book})")
            return {"status": "error", "message": busy}, 409
        return close_all(*args)
