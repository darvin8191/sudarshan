"""What changed (T2, M13.7): the current readings and a new snapshot in, the next readings and the
timeline's new rows out. Pure: no I/O, no clock; every input is an argument.

A change is, for one (module, instrument) (README section 4):

    first     no live reading before: none at all, or the one before had lapsed or was missing
              from the last document
    state     another state
    bias      the same state with another bias
    no_data   the quality went to no_data (from ok or degraded)
    lapsed    valid_until passed, once; by a document that brings it lapsed, or by the clock
              between documents (``lapses``)

A move of score or confidence alone, and any other field, updates the current reading (and so the
screens) but is not a change. A reading missing from a document leaves the current readings; when
it comes back it is a first reading again.

TRINETRA's times decide nothing on its own clock: a document's times are moved onto the server's
clock by its offset (``Snapshot.published_ms - Snapshot.at``, 0 when the clocks agree) before a
lapse is judged.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any

from sudarshan.signals import contract as c

Key = tuple[str, str]


@dataclass(frozen=True)
class Current:
    """One current reading as the service keeps it: the contract's ``Reading`` fields a document
    gives (``ReadingIn.as_record``), when its document was published (server clock), that
    document's seq, the offset of its times onto the server's clock, and whether its lapse has
    been recorded."""

    reading: Mapping[str, Any]
    published_ms: int
    doc_seq: int
    offset_ms: int = 0
    lapsed: bool = False

    @property
    def key(self) -> Key:
        return self.reading["module"], self.reading["instrument"]

    @property
    def lapse_ms(self) -> int | None:
        """When it lapses, on the server's clock (None: it does not)."""
        until = self.reading.get("valid_until_ms")
        return None if until is None else until + self.offset_ms


@dataclass(frozen=True)
class ChangeIn:
    """One new row of the timeline, before the store gives it an id. ``at_ms``: when it took
    effect, as shown (the document's published time; the lapse moment for ``lapsed``)."""

    at_ms: int
    kind: str
    reading: Mapping[str, Any]
    before: Mapping[str, Any] | None = None

    def as_row(self) -> dict[str, Any]:
        """The contract's ``Change`` fields but ``id`` and ``recorded_ms``."""
        r, b = self.reading, self.before or {}
        return {
            "at_ms": self.at_ms,
            "module": r["module"],
            "instrument": r["instrument"],
            "kind": self.kind,
            "from_state": b.get("state"),
            "from_label": b.get("label"),
            "from_bias": b.get("bias"),
            "to_state": r["state"],
            "to_label": r["label"],
            "bias": r["bias"],
            "score": r.get("score"),
            "confidence": r.get("confidence"),
            "quality": r["quality"],
            "horizon": r["horizon"],
            "since_ms": r.get("since_ms"),
            "as_of_ms": r.get("as_of_ms"),
            "valid_until_ms": r.get("valid_until_ms"),
            "levels": r.get("levels"),
            "inputs": list(r.get("inputs") or []),
            "why": dict(r.get("why") or dict.fromkeys(c.LANGS)),
        }


@dataclass(frozen=True)
class Outcome:
    """What one document did: the next current readings, the new timeline rows, and whether the
    current readings differ from before at all (a push is due)."""

    current: dict[Key, Current]
    changes: tuple[ChangeIn, ...]
    altered: bool


def _lapsed_at(entry: Current, now_ms: int) -> bool:
    lapse = entry.lapse_ms
    return entry.lapsed or (lapse is not None and lapse <= now_ms)


def apply(prior: Mapping[Key, Current], snap: c.Snapshot, now_ms: int) -> Outcome:
    """The next current readings and the changes a snapshot makes over ``prior``."""
    offset = snap.published_ms - snap.at
    current: dict[Key, Current] = {}
    changes: list[ChangeIn] = []
    for reading in snap.readings:
        record = reading.as_record()
        entry = Current(record, snap.published_ms, snap.seq, offset)
        before = prior.get(reading.key)
        live_before = before is not None and not _lapsed_at(before, now_ms)
        old = before.reading if before is not None else None
        if _lapsed_at(entry, now_ms):
            entry = replace(entry, lapsed=True)
            already = (
                before is not None
                and before.lapsed
                and (old["state"], old["bias"]) == (record["state"], record["bias"])
            )
            if not already:
                changes.append(ChangeIn(entry.lapse_ms or snap.published_ms, "lapsed", record, old))
        elif not live_before:
            changes.append(ChangeIn(snap.published_ms, "first", record, old))
        elif record["state"] != old["state"]:
            changes.append(ChangeIn(snap.published_ms, "state", record, old))
        elif record["bias"] != old["bias"]:
            changes.append(ChangeIn(snap.published_ms, "bias", record, old))
        elif record["quality"] == "no_data" and old["quality"] != "no_data":
            changes.append(ChangeIn(snap.published_ms, "no_data", record, old))
        current[reading.key] = entry
    altered = _differs(prior, current)
    return Outcome(current, tuple(changes), altered)


def _differs(prior: Mapping[Key, Current], current: Mapping[Key, Current]) -> bool:
    if prior.keys() != current.keys():
        return True
    return any(
        prior[key].reading != entry.reading or prior[key].lapsed != entry.lapsed
        for key, entry in current.items()
    )


def lapses(current: Mapping[Key, Current], now_ms: int) -> tuple[dict[Key, Current], list]:
    """The lapses the clock has brought since the last look: the readings with their lapse now
    recorded, and one ``lapsed`` change for each."""
    out = dict(current)
    changes: list[ChangeIn] = []
    for key, entry in current.items():
        if entry.lapsed or entry.lapse_ms is None or entry.lapse_ms > now_ms:
            continue
        out[key] = replace(entry, lapsed=True)
        changes.append(ChangeIn(entry.lapse_ms, "lapsed", entry.reading, entry.reading))
    return out, changes


def next_lapse_ms(current: Iterable[Current]) -> int | None:
    """The earliest lapse still to come (None when none is)."""
    times = [e.lapse_ms for e in current if not e.lapsed and e.lapse_ms is not None]
    return min(times) if times else None


_EPOCH = datetime(1970, 1, 1)


def ist_day(ms: int) -> date:
    """The IST calendar day of an instant (IST keeps no daylight saving)."""
    return (_EPOCH + timedelta(milliseconds=ms + c.IST_OFFSET_S * 1000)).date()


def sessions(
    since_ms: int | None, now_ms: int, is_trading_day: Callable[[date], bool]
) -> int | None:
    """Trading sessions from ``since_ms``'s IST day to today, both counted ("since 11 Nov, 3
    sessions"); at least 1 once it has begun. None when there is no ``since``; at most 400 days
    are looked at (older: 400 days' worth)."""
    if since_ms is None:
        return None
    first, last = ist_day(since_ms), ist_day(now_ms)
    if first > last:
        return 1
    first = max(first, last - timedelta(days=400))
    count = 0
    day = first
    while day <= last:
        if is_trading_day(day):
            count += 1
        day += timedelta(days=1)
    return max(count, 1)
