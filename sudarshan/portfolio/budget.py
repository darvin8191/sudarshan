"""The service's own cap on its Upstox reads (M7.1, design 3.2, cross-design P17).

Upstox limits each API per user to 50 a second, 500 a minute and 2,000 in 30 minutes, shared with
OpenAlgo's own reads. This service asks for about 2 a second, 30 a minute and 200 in 30 minutes per
API (a tenth of Upstox's figures, rounded to what a page needs), with three token buckets on a
monotonic clock. A token bucket admits up to twice its size in a sliding window of its own length,
so a burst can reach double; that is still far inside Upstox's figures. A read that finds a bucket
empty is not sent: the caller shows its last figures. A report read may wait out the per-second
bucket (``wait_s``, under a second); the minute and 30-minute buckets are stops.

A 429 (or Upstox's own rate code) pauses that API for 60 seconds.

**Reserve for the owner** (cross-design Y5). A principal other than ``owner`` (a family viewer, the
journal) takes a token only while the API's 30-minute bucket holds at least ``RESERVE_30MIN`` and
its minute bucket at least ``RESERVE_MINUTE``: a burst of order activity read for the family can
never empty the budget of the owner's own Upstox tab.

The surface is small on purpose, ``take`` and ``pause``, so that this can become the client of the
platform's one governor later (M18.9) without a change in the reader.
"""

from __future__ import annotations

import time
from collections.abc import Callable

#: (tokens, seconds in which they refill): per second, per minute, per 30 minutes.
LIMITS = ((2, 1.0), (30, 60.0), (200, 1800.0))
#: The owner's principal, and what a different principal leaves in the minute bucket and the
#: 30-minute bucket (by their position in ``LIMITS``) for the owner.
OWNER = "owner"
RESERVE = {1: 10, 2: 100}
#: How long an API rests after Upstox said to slow down.
PAUSE_S = 60.0
#: The longest a read waits for a bucket to refill before it stops: the per-second bucket only.
MAX_WAIT_S = 0.6


class Bucket:
    """``size`` tokens that come back evenly over ``window`` seconds."""

    def __init__(self, size: int, window: float, now: float):
        self.size, self.rate = size, size / window
        self.tokens, self.at = float(size), now

    def _fill(self, now: float) -> None:
        self.tokens = min(float(self.size), self.tokens + max(0.0, now - self.at) * self.rate)
        self.at = now

    def ready(self, now: float) -> bool:
        self._fill(now)
        return self.tokens >= 1.0

    def wait(self, now: float) -> float:
        """Seconds until this bucket holds a token (0 when it does)."""
        self._fill(now)
        if self.tokens >= 1.0:
            return 0.0
        return (1.0 - self.tokens) / self.rate if self.rate > 0 else float("inf")

    def spend(self) -> None:
        self.tokens -= 1.0


class Budget:
    """Per-API buckets and pauses. Not thread-safe by design: the reader calls it on one loop."""

    def __init__(
        self,
        limits: tuple[tuple[int, float], ...] = LIMITS,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._limits, self._clock = limits, clock
        self._buckets: dict[str, list[Bucket]] = {}
        self._paused: dict[str, float] = {}

    def _of(self, api: str) -> list[Bucket]:
        found = self._buckets.get(api)
        if found is None:
            now = self._clock()
            found = self._buckets[api] = [
                Bucket(size, window, now) for size, window in self._limits
            ]
        return found

    def paused_for(self, api: str) -> float:
        """Seconds left of the pause of ``api``; 0 when it is not paused."""
        left = self._paused.get(api, 0.0) - self._clock()
        if left <= 0:
            self._paused.pop(api, None)
            return 0.0
        return left

    def pause(self, api: str, seconds: float = PAUSE_S) -> None:
        self._paused[api] = self._clock() + seconds

    def wait_s(self, api: str, longest: float = MAX_WAIT_S) -> float | None:
        """How long until ``take`` would succeed, when that is no more than ``longest`` seconds
        (the per-second bucket alone); ``None`` when a longer bucket is short or the API is
        paused, which are stops, not waits."""
        if self.paused_for(api):
            return None
        now = self._clock()
        worst = max(bucket.wait(now) for bucket in self._of(api))
        return worst + 0.01 if worst <= longest else None

    def can_take(self, api: str, principal: str = OWNER) -> bool:
        """Whether ``take`` would succeed now, spending nothing."""
        if self.paused_for(api):
            return False
        now = self._clock()
        for index, bucket in enumerate(self._of(api)):
            if not bucket.ready(now):
                return False
            if principal != OWNER and bucket.tokens < RESERVE.get(index, 0):
                return False
        return True

    def take(self, api: str, principal: str = OWNER) -> bool:
        """Spend one read of ``api`` if every bucket has one and the API is not paused. All or
        nothing: a refusal spends nothing. A principal other than the owner also leaves the
        reserve (``RESERVE``) in the minute and 30-minute buckets."""
        if not self.can_take(api, principal):
            return False
        buckets = self._of(api)
        for bucket in buckets:
            bucket.spend()
        return True
