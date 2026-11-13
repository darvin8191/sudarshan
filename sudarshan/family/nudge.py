"""The alerts' "orders changed" nudge, as the family service takes it, and the book message that
follows every read (M6.7; design 2.4; cross-design Y5, Y11, Y17).

``sudarshan-alerts`` posts ``{"kind": "orders" | "holdings", "sandbox": bool, "at": ms}`` sealed
under the nudge purpose to ``/v1/nudge`` after each order update (``admin_api.SealedDoors.nudge``
opens the seal and calls ``Nudges.nudged``). A nudge carries no order data; the service reads the
books itself:

* ``sandbox: true`` is ignored: the books are the real account (the Analytics Token reads nothing
  else), so an order in the sandbox changes nothing a member sees;
* the parts a kind can change are marked dirty, so a screen that opens later reads at once;
* a part an open screen shows, inside its market session, is read for every member whose screen
  shows it: at once for the first nudge, then at most once in ten seconds for each part while
  nudges keep coming (Y5: a burst of order activity must not spend the portfolio's minute of reads;
  the last nudge of a burst is always followed by a read). Outside the session a nudge only marks.

After every read the service makes, for whatever reason, each socket of that member whose screen
shows the part gets ``{"type": "book", "part": ..., "read_at": ms}`` (Y11, ``announce``), so a fill
on the owner's side reaches the family screen at once: the page reads the part again.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Any, Protocol

from sudarshan.tasks import cancel_and_wait

log = logging.getLogger("sudarshan.family")

#: The parts each kind of nudge can change: a CNC fill moves holdings too.
PARTS_OF_KIND = {
    "orders": ("positions", "orders", "trades", "funds"),
    "holdings": ("positions", "holdings", "orders", "trades", "funds"),
}
#: Y5: a nudge-driven read of one part at most this often.
NUDGE_EVERY_MS = 10_000


class Shelf(Protocol):
    """What the nudges need of the books (``books.Books``)."""

    def mark_dirty(self, part: str, at: int) -> None: ...
    def watching(self, part: str, now: int) -> bool: ...
    def read_shown(self, part: str) -> int: ...
    def sockets_showing(self, member_id: int, part: str) -> list[Any]: ...


class Nudges:
    """``nudged`` is the sealed door's callback; ``close`` ends the reads still waiting."""

    def __init__(
        self,
        books: Shelf,
        *,
        clock: Callable[[], int],
        every_ms: int = NUDGE_EVERY_MS,
    ):
        self.books, self.clock, self.every_ms = books, clock, every_ms
        self.counts = {"taken": 0, "sandbox": 0, "marked": 0, "reads": 0}
        self._last: dict[str, int] = {}  # part -> when its last nudge read started
        self._waiting: dict[str, asyncio.Task] = {}  # part -> the read due at the window's end

    def nudged(self, kind: str, sandbox: bool, at: int) -> bool:
        """Whether this nudge made a read due now or at the end of the part's window."""
        if sandbox:
            self.counts["sandbox"] += 1
            return False
        now, taken = self.clock(), False
        for part in PARTS_OF_KIND.get(kind, ()):
            self.books.mark_dirty(part, now)
            if self.books.watching(part, now):
                self._coalesce(part, now)
                taken = True
        self.counts["taken" if taken else "marked"] += 1
        return taken

    def _coalesce(self, part: str, now: int) -> None:
        if part in self._waiting:
            return  # a read is due at the end of this part's window: it reads what came since
        wait = self._last.get(part, now - self.every_ms) + self.every_ms - now
        if wait <= 0:
            self._go(part)
            return
        self._waiting[part] = asyncio.create_task(self._later(part, wait / 1000),
                                                  name=f"family-nudge-{part}")  # fmt: skip

    def _go(self, part: str) -> None:
        self._last[part] = self.clock()
        self.counts["reads"] += self.books.read_shown(part)

    async def _later(self, part: str, delay_s: float) -> None:
        try:
            await asyncio.sleep(delay_s)
        finally:
            self._waiting.pop(part, None)
        if self.books.watching(part, self.clock()):
            self._go(part)

    async def close(self) -> None:
        waiting, self._waiting = list(self._waiting.values()), {}
        await cancel_and_wait(*waiting)


def book_message(part: str, read_at: int) -> dict[str, Any]:
    """What a socket is told after a read of ``part`` (the contract's ``WsBook``)."""
    return {"type": "book", "part": part, "read_at": int(read_at)}


def announce(books: Shelf, member_id: int, part: str, read_at: int) -> int:
    """Tell every socket of the member whose screen shows ``part`` that it was read again; how
    many were told (a socket that is closing or full is skipped, never waited for)."""
    told = 0
    for sock in books.sockets_showing(member_id, part):
        with contextlib.suppress(Exception):
            told += bool(sock.send(book_message(part, read_at)))
    return told
