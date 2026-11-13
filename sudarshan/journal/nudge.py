"""The "orders changed" nudge and the reads it starts (JB3', cross-design Y17).

The journal does not follow OpenAlgo's order stream itself: the alerts service, which holds the
owner's key loopback-only, posts one sealed nudge after each order update (``alerts/nudge.py``),
coalesced to one a second: ``{"kind": "orders" | "holdings", "sandbox": bool, "at": ms}``, no order
data. The journal answers at once and reads what changed itself:

* a nudge with ``sandbox`` false starts a debounced read of the day's trades through the
  portfolio: one read 3 s after the first nudge, then at most one every 10 s while nudges keep
  coming, never one per nudge (mutation target 21). A ``holdings`` nudge is a CNC fill, so it
  reads the trades the same way;
* a nudge with ``sandbox`` true starts the same debounce for the sandbox's book, read from
  ``db/sandbox.db`` (never from OpenAlgo's API, which would answer with live data if the owner
  left analyzer mode in between).

A nudge is a hint that may not arrive: the close check reads the day in any case. While an
order's label waits for its placement (or for its 60 s), a light loop labels it again every 30 s;
it reads only OpenAlgo's local files.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from .capture import capture_for

log = logging.getLogger("sudarshan.journal.nudge")

#: The first read after a quiet spell waits this long, so the fill is in Upstox's day list.
FIRST_READ_S = 3.0
#: Then reads are at least this far apart while nudges keep coming.
READ_GAP_S = 10.0
#: How often orders still waiting for their label are looked at again.
RELABEL_EVERY_S = 30.0


class Debounce:
    """When the next read is due: ``note`` a nudge; ``next`` waits until a read may start."""

    def __init__(
        self,
        first_s: float = FIRST_READ_S,
        gap_s: float = READ_GAP_S,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.first_s, self.gap_s, self.clock = first_s, gap_s, clock
        self.due: float | None = None
        self.last: float | None = None
        self.noted = 0
        self._wake = asyncio.Event()

    def note(self) -> None:
        """A nudge arrived. Never waits; one read covers every nudge until it starts."""
        self.noted += 1
        if self.due is not None:
            return
        due = self.clock() + self.first_s
        if self.last is not None:
            due = max(due, self.last + self.gap_s)
        self.due = due
        self._wake.set()

    async def next(self) -> None:
        """Return when a read is due, marking it started."""
        while True:
            if self.due is None:
                self._wake.clear()
                await self._wake.wait()
                continue
            wait = self.due - self.clock()
            if wait > 0:
                await asyncio.sleep(wait)
                continue
            self.due, self.last = None, self.clock()
            return


class Nudges:
    """The two debounces, live and sandbox (a service part: ``nudges_for``)."""

    def __init__(self, first_s: float = FIRST_READ_S, gap_s: float = READ_GAP_S):
        self.live = Debounce(first_s, gap_s)
        self.sandbox = Debounce(first_s, gap_s)

    def note(self, sandbox: bool) -> None:
        (self.sandbox if sandbox else self.live).note()

    async def run(self, service: Any) -> None:
        """Read after each due nudge, the live trades and the sandbox's book each in their own
        loop, until cancelled. A failed read is logged and the loop goes on."""
        capture = capture_for(service)

        async def loop(debounce: Debounce, read) -> None:
            while True:
                await debounce.next()
                try:
                    await read()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("a read after a nudge failed")

        await asyncio.gather(
            loop(self.live, lambda: capture.read_live("stream_read")),
            loop(self.sandbox, lambda: capture.read_sandbox("sandbox_book")),
        )


def nudges_for(service: Any) -> Nudges:
    """The service's debounces, built on first use."""
    return service.part("nudges", lambda svc: Nudges())


async def relabel_forever(service: Any, every: float = RELABEL_EVERY_S) -> None:
    """Label waiting orders of both modes every ``every`` seconds while any wait."""
    capture = capture_for(service)
    while True:
        try:
            for mode in ("live", "sandbox"):
                if await capture.pending(mode):
                    await capture.relabel(mode)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("labelling the waiting orders failed this time")
        await asyncio.sleep(every)


def register_jobs() -> None:
    """The jobs "nudges" (the reads a nudge starts) and "labels" (orders waiting for theirs)."""
    from .service import job

    @job("nudges")
    async def nudges_job(service) -> None:
        await nudges_for(service).run(service)

    @job("labels")
    async def labels_job(service) -> None:
        await relabel_forever(service)
