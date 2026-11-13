"""Send slots for the lanes of one Telegram bot: one gap, one minute budget, an order of service.

A bot has several kinds of message that must share what Telegram and OpenAlgo allow. Each kind is
a lane. The Pacer does not send anything: a lane asks ``allowed_at(lane)`` when it may start,
sleeps until then, and says ``took(lane)`` when it starts. Everything is a parameter, so the owner
bot and the family bot build their own from this one class:

  lanes     the lanes in their order of service. A lane may name the lanes it ``yields_to``: it takes
            no slot while one of those has messages waiting (the ``waiting(lane)`` callback each
            lane sets).
  gap_s     the least time between the starts of two messages, across every lane.
  per_minute  the most messages a minute across every lane; each lane may have a smaller cap of
            its own (``Lane.per_minute``).
  reserved  lane name -> a number of slots a minute kept for that lane: the other lanes together
            get what is left of ``per_minute``, so a burst of them can never take the slots of
            the reserved lane.

A limit is a number or a function returning one, read each time it is asked, so a test can shrink
it and an operator's setting can change it. The clock is monotonic seconds.

The owner bot's lanes (``sudarshan/alerts/outbox.py``): orders, market, notices, reports. The
family bot has one lane and Telegram's own limit, one message a second to a chat (see
``test_common_pacer.py``).

No I/O: the Pacer reads a clock and keeps a minute of start times in memory. Used from one event
loop; it takes no lock.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

Limit = int | float | Callable[[], int | float]


def _read(limit: Limit | None) -> float | None:
    return limit() if callable(limit) else limit


@dataclass(frozen=True)
class Lane:
    """One kind of message: its name, its own cap a minute (None: only the shared budgets) and the
    lanes whose waiting messages it gives way to."""

    name: str
    per_minute: Limit | None = None
    yields_to: tuple[str, ...] = ()


class Pacer:
    """The send slots of every lane of one bot. See the module words."""

    def __init__(
        self,
        lanes: Sequence[Lane],
        *,
        gap_s: Limit,
        per_minute: Limit | None = None,
        minute_s: Limit = 60.0,
        reserved: Mapping[str, Limit] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        names = [lane.name for lane in lanes]
        if not names or len(set(names)) != len(names):
            raise ValueError("a pacer needs lanes with distinct names")
        for lane in lanes:
            unknown = set(lane.yields_to) - set(names)
            if unknown:
                raise ValueError(f"lane {lane.name} gives way to unknown lanes {sorted(unknown)}")
        for name in reserved or {}:
            if name not in names:
                raise ValueError(f"a slot is reserved for the unknown lane {name}")
        self.lanes: dict[str, Lane] = {lane.name: lane for lane in lanes}
        self.gap_s, self.per_minute, self.minute_s = gap_s, per_minute, minute_s
        self.reserved: dict[str, Limit] = dict(reserved or {})
        self.clock = clock
        self._starts: deque[tuple[float, str]] = deque()  # (start, lane), the last minute
        self._waiting: dict[str, Callable[[], bool]] = {}
        #: While the reserved lanes are flushed at a stop, what the other lanes sent in the last
        #: minute no longer counts against them (review L10).
        self.flushing_reserved = False

    # -- the lanes' waiting messages --------------------------------------------------------------

    def set_waiting(self, lane: str, waiting: Callable[[], bool]) -> None:
        """How the pacer asks whether ``lane`` has messages waiting."""
        self._check(lane)
        self._waiting[lane] = waiting

    def waiting(self, lane: str) -> bool:
        """True when ``lane`` has messages waiting (False when it never said)."""
        self._check(lane)
        ask = self._waiting.get(lane)
        return bool(ask()) if ask is not None else False

    def blocked(self, lane: str) -> bool:
        """True while a lane that ``lane`` gives way to has messages waiting: take no slot."""
        self._check(lane)
        return any(self.waiting(other) for other in self.lanes[lane].yields_to)

    # -- the slots ---------------------------------------------------------------------------------

    def allowed_at(self, lane: str) -> float:
        """The earliest time (the pacer's clock) a message of ``lane`` may start.

        This is the gap and the minute budgets only: whether the lane must give way to another is
        ``blocked(lane)``, asked by the lane."""
        self._check(lane)
        minute = float(_read(self.minute_s) or 60.0)
        starts = self._recent(minute)
        gap = float(_read(self.gap_s) or 0.0)
        allowed = starts[-1][0] + gap if starts else 0.0
        total = _read(self.per_minute)
        held_back = set(self.reserved) if self.flushing_reserved else set()
        counted = [at for at, which in starts if which in self.reserved or not held_back]
        if total is not None and len(counted) >= max(1, int(total)):
            allowed = max(allowed, counted[-max(1, int(total))] + minute)
        if lane not in self.reserved and self.reserved and total is not None:
            share = max(1, int(total) - sum(int(_read(v) or 0) for v in self.reserved.values()))
            others = [at for at, which in starts if which not in self.reserved]
            if len(others) >= share:
                allowed = max(allowed, others[-share] + minute)
        own = _read(self.lanes[lane].per_minute)
        if own is not None:
            mine = [at for at, which in starts if which == lane]
            if len(mine) >= max(1, int(own)):
                allowed = max(allowed, mine[-max(1, int(own))] + minute)
        return allowed

    def took(self, lane: str, slots: int = 1) -> None:
        """A message of ``lane`` starts now. ``slots`` is how many it takes: a report is two
        messages (its text, then its file), so it takes two."""
        self._check(lane)
        now = self.clock()
        for _ in range(max(1, slots)):
            self._starts.append((now, lane))

    def _recent(self, minute: float) -> deque[tuple[float, str]]:
        cutoff = self.clock() - minute
        while self._starts and self._starts[0][0] <= cutoff:
            self._starts.popleft()
        return self._starts

    def _check(self, lane: str) -> None:
        if lane not in self.lanes:
            raise KeyError(f"unknown lane {lane!r}")
