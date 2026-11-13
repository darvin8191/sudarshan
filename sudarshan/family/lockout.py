"""When a family sign-in locks, and for how long (M2.11, design 2.2). No I/O: numbers in, numbers out.

Failures are counted per credential, in a row, until a good sign-in clears them. The 5th failure
locks the sign-in for 15 minutes, the 10th for 1 hour, the 15th for 24 hours, and every 5th after
that for 24 hours again (``contract.LOCKOUT``). While a sign-in is locked no password is tried at
all, so an attempt during a lock is refused at once and counts for nothing: the next count starts
when the lock ends.
"""

from __future__ import annotations

from .contract import LOCKOUT, LOCKOUT_REPEAT


def lock_seconds(failures: int) -> int | None:
    """How long the ``failures``-th failure in a row locks the sign-in, in seconds; None: no lock."""
    for count, seconds in LOCKOUT:
        if failures == count:
            return seconds
    last_count, last_seconds = LOCKOUT[-1]
    if failures > last_count and (failures - last_count) % LOCKOUT_REPEAT == 0:
        return last_seconds
    return None


def level(failures: int) -> int:
    """How many of the thresholds ``failures`` has reached (0 to 3)."""
    return sum(1 for count, _ in LOCKOUT if failures >= count)


def locked_until(row: dict | None, now: int) -> int | None:
    """The end of the lock on a credential row (epoch ms) while it holds, else None."""
    until = (row or {}).get("locked_until")
    return int(until) if until is not None and int(until) > now else None
