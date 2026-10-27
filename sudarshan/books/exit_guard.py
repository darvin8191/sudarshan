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
- an exit for it may have placed an order in the last ``EXIT_SHOWS_WITHIN_S``:
  noted here (``exit_placed``) by the app's Exit, by any smart order to a
  position size of 0, and by Exit all for each exit it sent;
- and, for Exit all, an order on its closing side is still working in Upstox's
  order book (broker/upstox/api/order_api.py reads it once per Exit all).

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
is busy, or an Exit all holds the book, when it claims. An entry (a smart order
that opens or adds to a position) is never refused.

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
from datetime import datetime, timedelta, timezone

from utils import real_threading
from utils.logging import get_logger

logger = get_logger(__name__)

EXIT_ALL_BEING_PLACED = "Exit all is still being placed. Wait for it, then check Orders."

#: How long an exit counts as placed just now: the time the phone holds a sent exit for the
#: book to show it (frontend/src/sudarshan/books/sent.ts, SENT_HOLD_MS).
EXIT_SHOWS_WITHIN_S = 15.0

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


def order_working(position: Position, placed: str) -> str:
    """Why Exit all sent no exit for a position whose closing order still works in the book."""
    when = f" (placed {placed})" if placed else ""
    return (
        f"{trader_name(position)} already has an order working to close it{when}, so no exit "
        "was sent for it. Check Orders, and cancel that order first if you want to exit now."
    )


class ExitsInFlight:
    """The exits this process is placing and has placed just now, by book."""

    def __init__(self) -> None:
        self._lock = real_threading.Lock()
        # (book, *position) -> when its exit began (epoch seconds), while it is being placed.
        self._positions: dict[tuple[str, str, str, str], float] = {}
        self._every: set[str] = set()
        # Per book, the positions whose exit may have placed an order, and when (monotonic for
        # the age, epoch for the sentence); kept for EXIT_SHOWS_WITHIN_S only.
        self._placed: dict[str, dict[Position, tuple[float, float]]] = {}

    def _since(self, book: str, position: Position, now: float) -> float | None:
        """When the position's latest exit began or was noted, if it is busy; under the lock."""
        self._forget_old(book, now)
        times = []
        began = self._positions.get((book, *position))
        if began is not None:
            times.append(began)
        noted = self._placed.get(book, {}).get(position)
        if noted is not None:
            times.append(noted[1])
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
        """Every position busy in ``book`` now, each with the sentence that says why."""
        now = time.monotonic()
        with self._lock:
            self._forget_old(book, now)
            since = {position: at for position, (_age, at) in self._placed.get(book, {}).items()}
            for (claimed_in, *position), began in self._positions.items():
                if claimed_in == book:
                    key = tuple(position)
                    since[key] = max(began, since.get(key, began))
        return {position: on_its_way(position, at) for position, at in since.items()}

    def placed(self, book: str, position: Position) -> None:
        """Note that an exit for ``position`` in ``book`` may have placed an order just now."""
        now = time.monotonic()
        with self._lock:
            self._forget_old(book, now)
            self._placed.setdefault(book, {})[position] = (now, time.time())

    def placed_lately(self, book: str) -> set[Position]:
        """The positions whose exit may have placed an order in ``book`` in the last
        ``EXIT_SHOWS_WITHIN_S``."""
        with self._lock:
            self._forget_old(book, time.monotonic())
            return set(self._placed.get(book, {}))

    def _forget_old(self, book: str, now: float) -> None:
        """Drop what is older than ``EXIT_SHOWS_WITHIN_S`` in ``book``; call under the lock."""
        recent = self._placed.get(book)
        if recent is None:
            return
        for position, (age, _at) in list(recent.items()):
            if now - age > EXIT_SHOWS_WITHIN_S:
                del recent[position]
        if not recent:
            del self._placed[book]

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


def exit_placed(book: str, exchange: object, symbol: object, product: object) -> None:
    """Note that an exit for this position may have placed an order just now.

    Called by every exit in the Live book that was not refused, before its claim (or, for Exit
    all, the book's) is let go: a second exit sized before the book shows this one would close
    the position again.
    """
    EXITS.placed(book, position_of(exchange, symbol, product))


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
