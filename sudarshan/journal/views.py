"""What leaves the service, in the contract's shapes (JB4; ``core/kinds.py`` RECORDS).

A row of the store carries more than its record (exact sums, stamps); a view picks exactly the
record's fields, so the phone's types and the answers never drift. The scope's mask is applied
after this, in one place (``serialize.py``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .core import kinds


def fields_of(record: str) -> tuple[str, ...]:
    return tuple(f.name for f in kinds.record(record))


TRIP = fields_of("Trip")
NOTE = fields_of("TripNote")
TAG = fields_of("Tag")
PLAYBOOK = fields_of("Playbook")
ORDER_REF = fields_of("OrderRef")


def pick(row: Mapping[str, Any], names: Iterable[str]) -> dict[str, Any]:
    return {name: row.get(name) for name in names}


def trip_out(row: Mapping[str, Any]) -> dict[str, Any]:
    return pick(row, TRIP)


def note_out(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    return None if row is None else pick(row, NOTE)


def tag_out(row: Mapping[str, Any]) -> dict[str, Any]:
    return pick(row, TAG)


def playbook_out(row: Mapping[str, Any]) -> dict[str, Any]:
    return pick(row, PLAYBOOK)


def leg_out(row: Mapping[str, Any]) -> dict[str, Any]:
    """One leg of ``Tx.legs_of`` as ``TripLeg``: the leg's own role and quantity."""
    return {
        "trade_id": row["trade_id"],
        "order_id": row["order_id"],
        "role": row["leg_role"],
        "action": row["action"],
        "qty": row["leg_qty"],
        "price_u": row["price_u"],
        "exch_ts_ms": row["exch_ts_ms"],
        "day": row["day"],
        "source": row["source"],
    }


def ref_out(row: Mapping[str, Any]) -> dict[str, Any]:
    return pick(row, ORDER_REF)


def done(row_id: int | None = None, version: int | None = None, words: str | None = None):
    """The ``Done`` record a write answers with."""
    return {"id": row_id, "version": version, "words": words}
