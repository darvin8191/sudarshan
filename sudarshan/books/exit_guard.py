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

A second Exit all, an exit while Exit all runs, or Exit all while one of the
app's exits runs, is refused with 409 and a sentence saying why, which the phone
reads as a refusal and shows; so is a second exit for the same position.

The claims are per process, which is the whole app (gunicorn runs a single
worker). They are kept under a real lock from utils.real_threading that is held
for set work only, never across I/O: correct under the eventlet worker, where
the only callers are greenlets that cannot be switched out inside it, under the
gthread worker's real threads, and on the development server. A claim is let go
in a ``finally``, however the view or the close-all ends.
"""

from __future__ import annotations

import functools
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

Position = tuple[str, str, str]


class ExitsInFlight:
    """The exits this process is placing, by book."""

    def __init__(self) -> None:
        self._lock = real_threading.Lock()
        self._positions: set[tuple[str, str, str, str]] = set()
        self._every: set[str] = set()

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

    @contextmanager
    def every(self, book: str) -> Iterator[str | None]:
        """Claim Exit all in ``book``; yields None while held, or why not."""
        with self._lock:
            if book in self._every:
                busy = EXIT_ALL_BEING_PLACED
            elif any(key[0] == book for key in self._positions):
                busy = AN_EXIT_BEING_PLACED
            else:
                self._every.add(book)
                busy = None
        try:
            yield busy
        finally:
            if busy is None:
                with self._lock:
                    self._every.discard(book)

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
        ``close_all``'s own answer; or, while an exit or an Exit all is still being placed in that
        book, ``({"status": "error", "message": <why>}, 409)`` without calling it.
    """
    with EXITS.every(book) as busy:
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
