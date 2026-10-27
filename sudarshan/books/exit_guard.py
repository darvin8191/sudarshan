"""One exit at a time per position, and one Exit all at a time per book (M6.9).

The phone gives up on an exit after 45 s, but the server does not: Flask runs
the view to its end when the phone has stopped waiting, one Upstox call may take
up to 120 s, Exit all places one order per position, and nginx allows 300 s. A
second exit sent while the first is still being placed is sized from a book
that does not show the first fill yet, and reverses the position instead of
closing it. The order the first one is placing does not show in Orders yet
either, so the trader cannot check for it.

So what closes a position claims it for as long as it runs:

- Exit all in the Live book, from every surface, where every close-all passes
  once (``close_all_once``, called by services/close_position_service.py): the
  app's Exit all, /api/v1/closeposition (TradingView, scripts, Telegram's
  /closeall through the SDK) and Flow's close-all node. The sandbox's Exit all
  answers in milliseconds and is not claimed.
- One position's exit from the app's own screens (``one_exit_at_a_time`` on
  blueprints/orders.py's /close_position). Single-position smart orders from
  /api/v1 are not claimed: a TradingView entry must never be refused.

A second exit for the same position, an exit while Exit all runs and a second
Exit all are refused at once with 409 and a sentence saying why, which the phone
reads as a refusal and shows. An Exit all that arrives while one of the app's
exits is being placed takes the book at once, so no new exit starts, then waits
for the exits already in flight to end (``EXIT_ALL_WAITS_S``) and closes what is
left: it is often sent by something that does not resend it (a TradingView
alert, a Flow node, a script), and a refusal would leave every other position
open. Only a wait that runs out is refused, as before.

Exit all sizes its orders from the position book, and Upstox's book can say the
old quantity for a second or more after an exit: an Exit all that read it then
would close that position a second time, which reverses it. So an exit the app
may have placed in the last ``EXIT_SHOWS_WITHIN_S`` (one it waited for, or one
that ended just before it) has to show in the book first: Exit all reads the book
until it does, still holding the book, and is refused if it has not by then.

The claims are per process, which is the whole app (gunicorn runs a single
worker). They are kept under a real lock from utils.real_threading that is held
for set work only, never across I/O or a wait: correct under the eventlet
worker, where the only callers are greenlets that cannot be switched out inside
it, under the gthread worker's real threads, and on the development server.
Exit all's wait is on a real Event through ``real_threading.wait_for``, which
polls with eventlet's sleep (the greenlet yields) and waits natively elsewhere;
between its reads of the book it sleeps the same way, holding no lock.
A claim is let go in a ``finally``, however the view or the close-all ends,
whether or not its caller is still there to read the answer.
"""

from __future__ import annotations

import functools
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from utils import real_threading
from utils.logging import get_logger

logger = get_logger(__name__)

EXIT_BEING_PLACED = (
    "An exit for this position is still being placed. Wait for it, then check Orders."
)
EXIT_ALL_BEING_PLACED = "Exit all is still being placed. Wait for it, then check Orders."
AN_EXIT_BEING_PLACED = "An exit is still being placed. Wait for it, then check Orders."
EXIT_NOT_SHOWN_YET = (
    "An exit placed just now does not show in Positions yet. Check Positions, then try Exit all"
    " again."
)

#: How long an Exit all waits for the exits already being placed in its book. One exit is
#: at most one Upstox call that can take long: the call is bounded at 120 s
#: (utils/httpx_client.REQUEST_TIMEOUT_SECONDS), the order window may queue it for up to
#: 10 s first under gthread (utils/broker_backpressure.BROKER_MAX_ORDER_QUEUE_WAIT_SECONDS),
#: and the position read before it and the logging after it take well under a second;
#: 5 s more is margin. An exit still running after that is not ending soon, and the Exit
#: all is refused as before.
EXIT_ALL_WAITS_S = 135.0

#: How long an Exit all gives the position book to show an exit the app placed just before
#: it, and how long such an exit counts as just placed: the time the phone holds a sent exit
#: for the book to show it (frontend/src/sudarshan/books/sent.ts, SENT_HOLD_MS).
EXIT_SHOWS_WITHIN_S = 15.0
#: How often the book is read meanwhile; each read is one call to the broker.
EXIT_SHOWS_POLL_S = 1.0

Position = tuple[str, str, str]


class ExitsInFlight:
    """The exits this process is placing, by book."""

    def __init__(self) -> None:
        self._lock = real_threading.Lock()
        self._positions: set[tuple[str, str, str, str]] = set()
        self._every: set[str] = set()
        # Per book, set whenever one of its exits ends: what a waiting Exit all wakes on.
        self._ended: dict[str, real_threading.Event] = {}
        # Per book, the positions whose exit may have placed an order, and when it ended;
        # kept for EXIT_SHOWS_WITHIN_S only.
        self._placed: dict[str, dict[Position, float]] = {}

    def _placing(self, book: str) -> bool:
        """Whether one of the app's exits is being placed in ``book``; call under the lock."""
        return any(key[0] == book for key in self._positions)

    @contextmanager
    def one(self, book: str, position: Position) -> Iterator[str | None]:
        """Claim one position's exit in ``book`` ("live" or "sandbox").

        Yields:
            None while the claim is held; otherwise the sentence saying why the
            exit cannot be sent now (the claim is then not taken).
        """
        key = (book, *position)
        with self._lock:
            if book in self._every:
                busy = EXIT_ALL_BEING_PLACED
            elif key in self._positions:
                busy = EXIT_BEING_PLACED
            else:
                self._positions.add(key)
                busy = None
        try:
            yield busy
        finally:
            if busy is None:
                with self._lock:
                    self._positions.discard(key)
                    ended = self._ended.get(book)
                if ended is not None:
                    ended.set()

    @contextmanager
    def every(self, book: str, wait_s: float = 0.0) -> Iterator[str | None]:
        """Claim Exit all in ``book``.

        A second Exit all is refused at once. Otherwise the book is taken at once, so no new
        exit starts in it, and the exits already being placed there are waited for, up to
        ``wait_s``; when they have not ended by then the book is let go again and the Exit all
        is refused. The wait holds no lock.

        Yields:
            None while the claim is held and nothing else is being placed in the book;
            otherwise the sentence saying why the Exit all cannot be sent now.
        """
        with self._lock:
            if book in self._every:
                busy = EXIT_ALL_BEING_PLACED
            elif wait_s <= 0 and self._placing(book):
                busy = AN_EXIT_BEING_PLACED
            else:
                self._every.add(book)
                busy = None
        if busy is not None:
            yield busy
            return
        try:
            if not self._settled(book, wait_s):
                busy = AN_EXIT_BEING_PLACED
            yield busy
        finally:
            with self._lock:
                self._every.discard(book)

    def _settled(self, book: str, wait_s: float) -> bool:
        """Wait up to ``wait_s`` until no exit is being placed in ``book``; True once none is."""
        deadline = time.monotonic() + wait_s
        waited = False
        while True:
            with self._lock:
                if not self._placing(book):
                    break
                ended = self._ended.setdefault(book, real_threading.Event())
                ended.clear()
            left = deadline - time.monotonic()
            if left <= 0:
                logger.warning(f"Exit all gave up waiting for an exit being placed ({book})")
                return False
            if not waited:
                waited = True
                logger.info(f"Exit all waiting for an exit being placed to end ({book})")
            real_threading.wait_for(ended, left)
        return True

    def placed(self, book: str, position: Position) -> None:
        """Note that an exit for ``position`` in ``book`` may have placed an order just now."""
        now = time.monotonic()
        with self._lock:
            recent = self._placed.setdefault(book, {})
            self._forget_old(recent, now)
            recent[position] = now

    def placed_lately(self, book: str) -> set[Position]:
        """The positions whose exit may have placed an order in ``book`` in the last
        ``EXIT_SHOWS_WITHIN_S``."""
        now = time.monotonic()
        with self._lock:
            recent = self._placed.get(book)
            if recent is None:
                return set()
            self._forget_old(recent, now)
            if not recent:
                del self._placed[book]
            return set(recent)

    @staticmethod
    def _forget_old(recent: dict[Position, float], now: float) -> None:
        """Drop what is older than ``EXIT_SHOWS_WITHIN_S``; call under the lock."""
        for position, at in list(recent.items()):
            if now - at > EXIT_SHOWS_WITHIN_S:
                del recent[position]

    def __len__(self) -> int:
        """How many claims are held right now."""
        with self._lock:
            return len(self._positions) + len(self._every)


EXITS = ExitsInFlight()


def _word(value: object) -> str:
    return str(value or "").strip().upper()


def _book() -> str:
    """Which book an exit sent now goes to; Live when that cannot be read."""
    try:
        from database import settings_db

        return "sandbox" if settings_db.get_analyze_mode() else "live"
    except Exception:
        logger.exception("Could not read the analyze mode for the exit guard; keyed as Live")
        return "live"


def _quantity(value: object) -> float | None:
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def open_in_book(auth_token: str, broker: str) -> Callable[[set[Position]], set[Position] | None]:
    """A reader of the live position book: which of some positions it still shows open.

    The reader answers None when the book could not be read, and counts a quantity it
    cannot read as open.
    """

    def still_open(positions: set[Position]) -> set[Position] | None:
        from services.positionbook_service import get_positionbook_with_auth

        ok, answer, _ = get_positionbook_with_auth(auth_token, broker)
        if not ok:
            return None
        shown = set()
        for row in answer.get("data") or []:
            position = (
                _word(row.get("exchange")),
                _word(row.get("symbol")),
                _word(row.get("product")),
            )
            if position in positions and _quantity(row.get("quantity")) != 0:
                shown.add(position)
        return shown

    return still_open


def _shown_in_book(positions: set[Position], still_open: Callable) -> bool:
    """Read the book until it shows none of ``positions`` open, up to ``EXIT_SHOWS_WITHIN_S``.

    Returns:
        True once it shows them closed (at once when there are none); False when it still
        shows one of them open, or could not be read, at the end.
    """
    if not positions:
        return True
    deadline = time.monotonic() + EXIT_SHOWS_WITHIN_S
    while True:
        try:
            open_now = still_open(positions)
        except Exception:
            logger.exception("Exit all could not read the position book for the exits it waits on")
            open_now = None
        if open_now is not None and not open_now:
            return True
        left = deadline - time.monotonic()
        if left <= 0:
            return False
        time.sleep(min(EXIT_SHOWS_POLL_S, left))  # eventlet's sleep under eventlet: it yields


def close_all_once(book: str, close_all: Callable, *args, still_open: Callable | None = None):
    """Run one Exit all in ``book`` while holding its claim, whichever surface it came from.

    Args:
        book: "live" or "sandbox".
        close_all: The broker's ``close_all_positions``, answering ``(body, status)``.
        *args: What ``close_all`` takes.
        still_open: Reads which of some positions the book still shows open (``open_in_book``).
            Without it the book is not checked for exits placed just before.

    Returns:
        ``close_all``'s own answer, once the app's exits already being placed in that book have
        ended (waited for up to ``EXIT_ALL_WAITS_S``, no new one starting meanwhile) and the
        book shows the ones that may have placed an order lately (``EXIT_SHOWS_WITHIN_S``); or
        ``({"status": "error", "message": <why>}, 409)`` without calling it, at once while
        another Exit all runs in that book, or when either wait runs out.
    """
    with EXITS.every(book, wait_s=EXIT_ALL_WAITS_S) as busy:
        if busy:
            logger.warning(f"Exit all refused while an exit is still being placed ({book})")
            return {"status": "error", "message": busy}, 409
        if still_open is not None and not _shown_in_book(EXITS.placed_lately(book), still_open):
            logger.warning(
                f"Exit all refused: an exit placed just now is not in the book yet ({book})"
            )
            return {"status": "error", "message": EXIT_NOT_SHOWN_YET}, 409
        return close_all(*args)


def _may_have_placed(answer: object) -> bool:
    """Whether a close view's answer leaves open that an order went: all but a refusal (4xx)."""
    if isinstance(answer, tuple) and len(answer) > 1:
        status = answer[1]
    else:
        status = getattr(answer, "status_code", None)
    return not (isinstance(status, int) and 400 <= status < 500)


def one_exit_at_a_time() -> Callable:
    """Decorate the app's close route so it refuses with 409 while that exit is being placed.

    An exit whose answer is not a refusal (4xx) may have placed an order, and is noted for
    ``EXIT_SHOWS_WITHIN_S`` so an Exit all does not size its orders from a book that does not
    show it yet. The app's Exit all route is not decorated: its claim is taken where every
    close-all passes, by ``close_all_once``, and a second claim would make it refuse itself.
    """

    def wrap(view: Callable) -> Callable:
        @functools.wraps(view)
        def guarded(*args, **kwargs):
            from flask import jsonify, request

            book = _book()
            data = request.get_json(silent=True) or {}
            position = (
                _word(data.get("exchange")),
                _word(data.get("symbol")),
                _word(data.get("product")),
            )
            with EXITS.one(book, position) as busy:
                if busy:
                    logger.warning(f"Exit refused while one is still being placed ({book})")
                    return jsonify({"status": "error", "message": busy}), 409
                try:
                    answer = view(*args, **kwargs)
                except BaseException:
                    EXITS.placed(book, position)  # it may have broken after the order went
                    raise
                if _may_have_placed(answer):
                    EXITS.placed(book, position)
                return answer

        return guarded

    return wrap
