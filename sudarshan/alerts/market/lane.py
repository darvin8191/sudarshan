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

List alerts (M8.14): a fire that carries a ``Group`` goes at once, as any market alert does, but
only the first of its group. That send opens the group's window (``Group.window_s``, 5 minutes by
default). Fires of the group inside the window wait in the group's own buffer (at most
MAX_BUFFER; past that only a count is kept) and go together as one digest when the window ends.
A window that ends with nothing waiting closes, so the next fire is first again; a window that
ends with a digest sent opens the next window from that send. The digest enters the queue once,
composed, so a gap-up morning on a long list never fills MAX_PENDING; it is under the age limit
from its composition, never from the first fire.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
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
#: Fires one list alert's window keeps; past this only a count is kept (M8.14).
MAX_BUFFER = 200
MAX_AGE = timedelta(minutes=5)
RETRY_S = (2.0, 5.0, 10.0, 30.0, 60.0)
UNCONFIRMED_TRIES = 3
#: How often a message ready to go looks again while order alerts hold the slot.
POLL_S = 0.05
FLUSH_S = 5.0

SENT_STATUS, NOT_SENT = "sent", "not_sent"
TOO_OLD = "OpenAlgo could not send it within 5 minutes; it is in the alert history"
TOO_MANY = "Too many market alerts were waiting at once; it is in the alert history"
TOO_MANY_IN_WINDOW = (
    "More alerts of this list fired in one window than can be kept; it is in the alert history"
)
UNLISTED = "It was one of more than 15 in a digest message; it is in the alert history"
NOT_CONFIRMED = "Telegram did not confirm it after 3 tries"


class Group(NamedTuple):
    """A list alert a fire belongs to: its digest's title and window (0 or less: each at once)."""

    id: int
    title: str
    window_s: float = 300.0


class Line(NamedTuple):
    event_id: int
    text: str
    #: When it fired (aware), for the message's time and the age limit.
    at: datetime
    group: Group | None = None
    #: A composed digest stands for these events; its ``text`` is the whole message.
    ids: tuple[int, ...] = ()


class _Window:
    """One group's window: when it ends (None until a message of the group is on its way) and
    what fired inside it."""

    __slots__ = ("buffer", "end", "more")

    def __init__(self) -> None:
        self.end: float | None = None
        self.buffer: list[Line] = []
        self.more = 0


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
        self._windows: dict[int, _Window] = {}
        self._digest_keys = itertools.count(-(1 << 40), -1)  # a digest's key in ``_pending``
        self._since: float | None = None  # monotonic time the oldest waiting fire arrived
        self._dropped = 0
        self._wake = asyncio.Event()
        self._hurry = False
        self._in_flight = 0
        self.throttle = Throttle()
        self.stats = {"messages": 0, "lines": 0, "dropped": 0, "failed": 0, "digests": 0}
        # The reports lane takes no slot while this lane has messages waiting (Y18).
        if hasattr(pacer, "set_waiting"):
            pacer.set_waiting(MARKET, lambda: bool(self._pending))

    def __len__(self) -> int:
        return len(self._pending)

    def add(self, lines: list[Line]) -> None:
        dropped = False
        for line in lines:
            if line.event_id in self._pending or self._buffered(line):
                continue
            if len(self._pending) >= MAX_PENDING:
                _, oldest = self._pending.popitem(last=False)
                self._dropped += 1
                self.stats["dropped"] += 1
                self._result(_ids([oldest]), NOT_SENT, TOO_MANY)
                dropped = True
            self._pending[line.event_id] = line
        if dropped:
            self._end_orphaned_windows()
        if self._pending:
            if self._since is None:
                self._since = time.monotonic()
            self._wake.set()

    async def run(self) -> None:
        """Send until cancelled."""
        failures = unconfirmed = 0
        while True:
            self._tick()
            if not self._pending:
                self._wake.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), self._next_window())
                continue
            if not self._hurry:
                await _sleep_until(self._since + COALESCE_S)
            await self._slot()
            self._expire()
            if not self._pending:  # given away while waiting (too old)
                continue
            batch, dropped = self._take()
            self.pacer.took(MARKET)  # no await since _slot(): the slot is still ours
            self._in_flight = len(batch)
            try:
                outcome, why = await self.send(_compose(batch, dropped))
            finally:
                self._in_flight = 0
            ids = _ids(batch)
            if outcome == SENT:
                failures = unconfirmed = 0
                self.stats["messages"] += 1
                self.stats["lines"] += len(ids)
                self.stats["digests"] += sum(1 for line in batch if line.ids)
                self._result(ids, SENT_STATUS, "")
                continue
            if outcome == DROP:
                self.stats["failed"] += len(ids)
                self._result(ids, NOT_SENT, why or REFUSED)
                continue
            if outcome == UNCONFIRMED:
                unconfirmed += 1
                if unconfirmed >= UNCONFIRMED_TRIES:
                    unconfirmed = 0
                    self.stats["failed"] += len(ids)
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
        self._tick(force=True)  # what a window holds goes now: a stop must not strand it
        self._wake.set()
        deadline = time.monotonic() + timeout
        while (self._pending or self._in_flight) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        return len(self._pending) + self._in_flight

    # -- internals ------------------------------------------------------------------------------

    async def _slot(self) -> None:
        """Wait for a market slot: never while order alerts wait, never while a server notice has
        waited its limit for one (review L2), never over the lane's budget. A line that turns
        MAX_AGE old meanwhile is told not sent at once (review M1)."""
        while True:
            self._expire()
            if not self._pending:
                return
            if self.pacer.orders_waiting() or self._gives_way():
                await asyncio.sleep(POLL_S)
                continue
            wait = self.pacer.allowed_at(MARKET) - time.monotonic()
            if wait <= 0:
                return
            await asyncio.sleep(min(wait, 4 * POLL_S))  # an order may arrive meanwhile

    def _gives_way(self) -> bool:
        """The Pacer says this lane takes no slot now (a notice has waited its limit)."""
        blocked = getattr(self.pacer, "blocked", None)
        return bool(blocked(MARKET)) if blocked is not None else False

    def _expire(self) -> None:
        """Lines older than MAX_AGE are never sent late (design 6): each is told not sent, with
        the words History shows, and dropped. Lines waited behind the order lane's retries or
        the minute budget with no check of their age, and went out 12 minutes late (review
        M1)."""
        oldest_kept = self.clock() - MAX_AGE
        old = [key for key, line in self._pending.items() if line.at < oldest_kept]
        if not old:
            return
        gone = [self._pending.pop(key) for key in old]
        if not self._pending:
            self._since = None
        self.stats["failed"] += len(_ids(gone))
        self.throttle(logging.ERROR, "too-old", "Market alerts could not be sent for 5 minutes")
        self._result(_ids(gone), NOT_SENT, TOO_OLD)
        self._end_orphaned_windows()

    def _take(self) -> tuple[list[Line], int]:
        batch: list[Line] = []
        dropped, self._dropped = self._dropped, 0
        for event_id in list(self._pending):
            line = self._pending[event_id]
            full = len(batch) >= MAX_LINES
            if batch and (full or line.ids or batch[0].ids
                          or len(_compose([*batch, line], dropped)) > MAX_CHARS):  # fmt: skip
                break  # a digest is a message of its own
            batch.append(line)
            del self._pending[event_id]
        self._since = time.monotonic() if self._pending else None
        self._opened(batch)
        return batch, dropped

    def _put_back(self, batch: list[Line], dropped: int) -> None:
        """Back to the front, unless it is older than MAX_AGE: then it is not sent late."""
        oldest_kept = self.clock() - MAX_AGE
        old = []
        for line in reversed(batch):
            if line.at < oldest_kept:
                old.append(line)
                continue
            self._pending[line.event_id] = line
            self._pending.move_to_end(line.event_id, last=False)
        if old:
            self.stats["failed"] += len(_ids(old))
            self.throttle(logging.ERROR, "too-old", "Market alerts could not be sent for 5 minutes")
            self._result(_ids(old[::-1]), NOT_SENT, TOO_OLD)
        self._dropped += dropped
        if self._pending and self._since is None:
            self._since = time.monotonic()

    # -- list alerts: the first at once, then a digest a window (M8.14) ------------------------

    def _buffered(self, line: Line) -> bool:
        """True when this fire of a list alert waits for its window's digest; False when it is the
        group's first (or still fits the first message) and goes to the queue now."""
        group = line.group
        if group is None or group.window_s <= 0 or line.ids:
            return False
        window = self._windows.get(group.id)
        waiting = [p for p in self._pending.values() if p.group and p.group.id == group.id]
        busy = (window is not None and window.end is not None) or any(p.ids for p in waiting)
        if not busy and len(waiting) < MAX_LINES:
            return False
        window = self._windows.setdefault(group.id, _Window())
        if len(window.buffer) < MAX_BUFFER:
            window.buffer.append(line)
        else:  # only a count is kept: the history has the fire
            window.more += 1
            self._result([line.event_id], NOT_SENT, TOO_MANY_IN_WINDOW)
        return True

    def _opened(self, batch: list[Line]) -> None:
        """A message of a group is on its way: its window opens (again) from this send."""
        now = time.monotonic()
        for line in batch:
            if line.group is not None and line.group.window_s > 0:
                self._windows.setdefault(line.group.id, _Window()).end = now + line.group.window_s

    def _end_orphaned_windows(self) -> None:
        """A window that never opened (no message of its group went) has nothing to end it once
        every line of its group that waited is gone, dropped as too old or for room. It ends now,
        so its buffer goes as a digest at the next tick rather than after the group's next fire
        plus a window, however old by then (review L5)."""
        waiting = {line.group.id for line in self._pending.values() if line.group is not None}
        now = time.monotonic()
        for group_id, window in self._windows.items():
            if window.end is None and window.buffer and group_id not in waiting:
                window.end = now

    def _next_window(self) -> float | None:
        ends = [w.end for w in self._windows.values() if w.end is not None]
        return max(0.0, min(ends) - time.monotonic()) if ends else None

    def _tick(self, force: bool = False) -> None:
        """Close each window that has ended: nothing waiting closes it, a buffer becomes one
        digest in the queue."""
        now = time.monotonic()
        for group_id, window in list(self._windows.items()):
            ended = force or (window.end is not None and now >= window.end)
            idle = window.end is None and not window.buffer
            if not (ended or idle):
                continue
            if window.buffer:
                self._digest(window)
                window.end = None
            else:
                del self._windows[group_id]

    def _digest(self, window: _Window) -> None:
        listed, rest = (
            window.buffer[: words.MAX_DIGEST_LINES],
            window.buffer[words.MAX_DIGEST_LINES :],
        )
        if rest:  # kept in the history, counted in the message
            self._result(_ids(rest), NOT_SENT, UNLISTED)
        group = listed[0].group
        text = words.digest(group.title, [line.text for line in listed],
                            min(line.at for line in listed), max(line.at for line in listed),
                            len(rest) + window.more)  # fmt: skip
        window.buffer, window.more = [], 0
        digest = Line(next(self._digest_keys), text, self.clock(), group, _ids(listed))
        self._pending[digest.event_id] = digest  # one composed message enters the queue
        self._since = self._since if self._since is not None else time.monotonic()
        self._wake.set()

    def _result(self, ids: list[int], status: str, reason: str) -> None:
        try:
            self.on_result(ids, status, reason)
        except Exception:  # the record is the runtime's; a failure there must not stop sending
            log.exception("recording what became of %d market alerts failed", len(ids))


def _ids(lines: list[Line]) -> list[int]:
    """The events these lines stand for: a digest's own, else the line's."""
    return [event for line in lines for event in (line.ids or (line.event_id,))]


def _compose(batch: list[Line], dropped: int) -> str:
    if batch[0].ids:
        return batch[0].text
    first = min(line.at for line in batch)
    last = max(line.at for line in batch)
    return words.message([line.text for line in batch], first, last, dropped)


async def _sleep_until(when: float) -> None:
    delay = when - time.monotonic()
    if delay > 0:
        await asyncio.sleep(delay)
