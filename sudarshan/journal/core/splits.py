"""The expiry split (JA3, M14.3; design 5.2).

``index_expiry_day`` is set on a trip when an index option of the master expires on the day it
opened (the caller reads the master and passes the days); ``own_expiry_day`` when the instrument's
own expiry is that day. The analytics show both splits side by side.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from sudarshan.journal.core import metrics

Trip = Mapping[str, Any]


def expiry_flags(
    open_day: int, own_expiry_day: int | None, index_expiry_days: Iterable[int]
) -> tuple[bool, bool]:
    """``(own_expiry_day, index_expiry_day)`` for a trip that opened on ``open_day``."""
    return (own_expiry_day == open_day, open_day in set(index_expiry_days))


def expiry_split(trips: Iterable[Trip]) -> dict[str, dict[str, Any]]:
    """The metrics block of the trips on and off an index expiry day, and on and off their own."""
    rows = list(trips)
    return {
        "index_expiry": metrics.compute(t for t in rows if t.get("index_expiry_day")),
        "not_index_expiry": metrics.compute(t for t in rows if not t.get("index_expiry_day")),
        "own_expiry": metrics.compute(t for t in rows if t.get("own_expiry_day")),
        "not_own_expiry": metrics.compute(t for t in rows if not t.get("own_expiry_day")),
    }
