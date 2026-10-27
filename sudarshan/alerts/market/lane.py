"""The market alerts lane to Telegram (M10.5): merged bursts, behind the order lane, results kept.

Each fire comes with its event id in market.db and its line (words.line). A burst is merged: a
message waits COALESCE_S after the first fire for the rest, then lists up to MAX_LINES lines (and
MAX_CHARS characters) under the सुदर्शन / SUDARSHAN header, with the time or range (words.message).

Slots come from the process's Pacer (outbox.py): at least one gap between any two messages, at
most PER_MINUTE a minute in all and MARKET_PER_MINUTE for this lane, and none while order alerts
wait, so a fill is never held back by a market storm. This lane sends from its own task.

What happened to each message goes back through ``on_result(event_ids, status, reason)``:
"sent", or "not_sent" with the reason in the owner's words. A fire that cannot go out within
MAX_AGE (5 minutes) is not sent late: a market alert is useless once the moment has passed, and
the Alerts page's history has it. Beyond MAX_PENDING waiting fires, the oldest are dropped and the
next message says how many could not be listed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import NamedTuple

from sudarshan.alerts.market import words
from sudarshan.alerts.outbox import (
    DROP,
    MARKET,
    REFUSED,
    SENT,
    UNCONFIRMED,
    Pacer,
    Throttle,
)

log = logging.getLogger("sudarshan.alerts.market")

COALESCE_S = 0.3
MAX_LINES = 15
MAX_CHARS = 3500
MAX_PENDING = 200
MAX_AGE = timedelta(minutes=5)
RETRY_S = (2.0, 5.0, 10.0, 30.0, 60.0)
UNCONFIRMED_TRIES = 3
#: How often a message ready to go looks again while order alerts hold the slot.
POLL_S = 0.05
FLUSH_S = 5.0

SENT_STATUS, NOT_SENT = "sent", "not_sent"
TOO_OLD = "OpenAlgo could not send it within 5 minutes; it is in the alert history"
TOO_MANY = "Too many market alerts were waiting at once; it is in the alert history"
NOT_CONFIRMED = "Telegram did not confirm it after 3 tries"


class Line(NamedTuple):
    event_id: int
    text: str
    #: When it fired (aware), for the message's time and the age limit.
    at: datetime


Result = Callable[[list[int], str, str], None]


class MarketLane:
    """Fires waiting for Telegram, sent in merged, paced messages.

    Args:
        send: Sends one text; returns (SENT | RETRY | UNCONFIRMED | DROP, reason for DROP).
        clock: The service clock (aware datetimes), for the age limit.
        pacer: The process's shared send slots (outbox.Pacer).
        on_result: Told what became of each message's fires.
    """

    def __init__(
        self,
        send: Callable[[str], Awaitable[tuple[str, str]]],
        clock: Callable[[], datetime],
        pacer: Pacer,
        on_result: Result,
    ):
        self.send, self.clock, self.pacer, self.on_result = send, clock, pacer, on_result
        self._pending: OrderedDict[int, Line] = OrderedDict()
        self._since: float | None = None  # monotonic time the oldest waiting fire arrived
        self._dropped = 0
        self._wake = asyncio.Event()
        self._hurry = False
        self._in_flight = 0
        self.throttle = Throttle()
        self.stats = {"messages": 0, "lines": 0, "dropped": 0, "failed": 0}

    def __len__(self) -> int:
        return len(self._pending)

    def add(self, lines: list[Line]) -> None:
        for line in lines:
            if line.event_id in self._pending:
                continue
            if len(self._pending) >= MAX_PENDING:
                _, oldest = self._pending.popitem(last=False)
                self._dropped += 1
                self.stats["dropped"] += 1
                self._result([oldest.event_id], NOT_SENT, TOO_MANY)
            self._pending[line.event_id] = line
        if self._pending:
            if self._since is None:
                self._since = time.monotonic()
            self._wake.set()

    async def run(self) -> None:
        """Send until cancelled."""
        failures = unconfirmed = 0
        while True:
            if not self._pending:
                self._wake.clear()
                await self._wake.wait()
                continue
            if not self._hurry:
                await _sleep_until(self._since + COALESCE_S)
            await self._slot()
            if not self._pending:  # given away while waiting (too old)
                continue
            batch, dropped = self._take()
            self.pacer.took(MARKET)  # no await since _slot(): the slot is still ours
            self._in_flight = len(batch)
            try:
                outcome, why = await self.send(_compose(batch, dropped))
            finally:
                self._in_flight = 0
            ids = [line.event_id for line in batch]
            if outcome == SENT:
                failures = unconfirmed = 0
                self.stats["messages"] += 1
                self.stats["lines"] += len(batch)
                self._result(ids, SENT_STATUS, "")
                continue
            if outcome == DROP:
                self.stats["failed"] += len(batch)
                self._result(ids, NOT_SENT, why or REFUSED)
                continue
            if outcome == UNCONFIRMED:
                unconfirmed += 1
                if unconfirmed >= UNCONFIRMED_TRIES:
                    unconfirmed = 0
                    self.stats["failed"] += len(batch)
                    self._result(ids, NOT_SENT, NOT_CONFIRMED)
                    continue
            self._put_back(batch, dropped)
            delay = RETRY_S[min(failures, len(RETRY_S) - 1)]
            failures += 1
            await asyncio.sleep(delay)

    async def flush(self, timeout: float = FLUSH_S) -> int:
        """Send what waits without the burst pause, for at most ``timeout``; returns what is left
        (a message on its way counts until its answer)."""
        self._hurry = True
        self._wake.set()
        deadline = time.monotonic() + timeout
        while (self._pending or self._in_flight) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        return len(self._pending) + self._in_flight

    # -- internals ------------------------------------------------------------------------------

    async def _slot(self) -> None:
        """Wait for a market slot: never while order alerts wait, never over the lane's budget."""
        while True:
            if self.pacer.orders_waiting():
                await asyncio.sleep(POLL_S)
                continue
            wait = self.pacer.allowed_at(MARKET) - time.monotonic()
            if wait <= 0:
                return
            await asyncio.sleep(min(wait, 4 * POLL_S))  # an order may arrive meanwhile

    def _take(self) -> tuple[list[Line], int]:
        batch: list[Line] = []
        dropped, self._dropped = self._dropped, 0
        for event_id in list(self._pending):
            line = self._pending[event_id]
            full = len(batch) >= MAX_LINES
            if batch and (full or len(_compose([*batch, line], dropped)) > MAX_CHARS):
                break
            batch.append(line)
            del self._pending[event_id]
        self._since = time.monotonic() if self._pending else None
        return batch, dropped

    def _put_back(self, batch: list[Line], dropped: int) -> None:
        """Back to the front, unless it is older than MAX_AGE: then it is not sent late."""
        oldest_kept = self.clock() - MAX_AGE
        old = []
        for line in reversed(batch):
            if line.at < oldest_kept:
                old.append(line.event_id)
                continue
            self._pending[line.event_id] = line
            self._pending.move_to_end(line.event_id, last=False)
        if old:
            self.stats["failed"] += len(old)
            self.throttle(logging.ERROR, "too-old", "Market alerts could not be sent for 5 minutes")
            self._result(old[::-1], NOT_SENT, TOO_OLD)
        self._dropped += dropped
        if self._pending and self._since is None:
            self._since = time.monotonic()

    def _result(self, ids: list[int], status: str, reason: str) -> None:
        try:
            self.on_result(ids, status, reason)
        except Exception:  # the record is the runtime's; a failure there must not stop sending
            log.exception("recording what became of %d market alerts failed", len(ids))


def _compose(batch: list[Line], dropped: int) -> str:
    first = min(line.at for line in batch)
    last = max(line.at for line in batch)
    return words.message([line.text for line in batch], first, last, dropped)


async def _sleep_until(when: float) -> None:
    delay = when - time.monotonic()
    if delay > 0:
        await asyncio.sleep(delay)
