"""The service's own cap on its Upstox reads (M7.1, design 3.2, cross-design P17).

Upstox limits each API per user to 50 a second, 500 a minute and 2,000 in 30 minutes, shared with
OpenAlgo's own reads. This service asks for at most 2 a second, 30 a minute and 200 in 30 minutes
per API (a tenth of Upstox's figures, rounded to what a page needs), with three token buckets on a
monotonic clock. A read that finds a bucket empty is not sent: the caller shows its last figures.

A 429 (or Upstox's own rate code) pauses that API for 60 seconds.

The surface is small on purpose, ``take`` and ``pause``, so that this can become the client of the
platform's one governor later (M18.9) without a change in the reader.
"""

from __future__ import annotations

import time
from collections.abc import Callable

#: (tokens, seconds in which they refill): per second, per minute, per 30 minutes.
LIMITS = ((2, 1.0), (30, 60.0), (200, 1800.0))
#: How long an API rests after Upstox said to slow down.
PAUSE_S = 60.0


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

    def take(self, api: str) -> bool:
        """Spend one read of ``api`` if every bucket has one and the API is not paused. All or
        nothing: a refusal spends nothing."""
        if self.paused_for(api):
            return False
        now = self._clock()
        buckets = self._of(api)
        if not all(bucket.ready(now) for bucket in buckets):
            return False
        for bucket in buckets:
            bucket.spend()
        return True
