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

The claims are per process, which is the whole app (gunicorn runs a single
worker). They are kept under a real lock from utils.real_threading that is held
for set work only, never across I/O or a wait: correct under the eventlet
worker, where the only callers are greenlets that cannot be switched out inside
it, under the gthread worker's real threads, and on the development server.
Exit all's wait is on a real Event through ``real_threading.wait_for``, which
polls with eventlet's sleep (the greenlet yields) and waits natively elsewhere.
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

#: How long an Exit all waits for the exits already being placed in its book. One exit is
#: at most one Upstox call that can take long: the call is bounded at 120 s
#: (utils/httpx_client.REQUEST_TIMEOUT_SECONDS), the order window may queue it for up to
#: 10 s first under gthread (utils/broker_backpressure.BROKER_MAX_ORDER_QUEUE_WAIT_SECONDS),
#: and the position read before it and the logging after it take well under a second;
#: 5 s more is margin. An exit still running after that is not ending soon, and the Exit
#: all is refused as before.
EXIT_ALL_WAITS_S = 135.0

Position = tuple[str, str, str]


class ExitsInFlight:
    """The exits this process is placing, by book."""

    def __init__(self) -> None:
        self._lock = real_threading.Lock()
        self._positions: set[tuple[str, str, str, str]] = set()
        self._every: set[str] = set()
        # Per book, set whenever one of its exits ends: what a waiting Exit all wakes on.
        self._ended: dict[str, real_threading.Event] = {}

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


def close_all_once(book: str, close_all: Callable, *args):
    """Run one Exit all in ``book`` while holding its claim, whichever surface it came from.

    Args:
        book: "live" or "sandbox".
        close_all: The broker's ``close_all_positions``, answering ``(body, status)``.
        *args: What ``close_all`` takes.

    Returns:
        ``close_all``'s own answer, once the app's exits already being placed in that book have
        ended (waited for up to ``EXIT_ALL_WAITS_S``, no new one starting meanwhile); or
        ``({"status": "error", "message": <why>}, 409)`` without calling it, at once while
        another Exit all runs in that book, or when the wait runs out.
    """
    with EXITS.every(book, wait_s=EXIT_ALL_WAITS_S) as busy:
        if busy:
            logger.warning(f"Exit all refused while an exit is still being placed ({book})")
            return {"status": "error", "message": busy}, 409
        return close_all(*args)


def one_exit_at_a_time() -> Callable:
    """Decorate the app's close route so it refuses with 409 while that exit is being placed.

    The app's Exit all route is not decorated: its claim is taken where every close-all passes,
    by ``close_all_once``, and a second claim would make it refuse itself.
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
                return view(*args, **kwargs)

        return guarded

    return wrap
