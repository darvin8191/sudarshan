"""What leaves the service, in the contract's shapes (JB4; ``core/kinds.py`` RECORDS).

A row of the store carries more than its record (exact sums, stamps); a view picks exactly the
record's fields, so the phone's types and the answers never drift. The scope's mask is applied
after this, in one place (``serialize.py``).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .core import kinds, money


def fields_of(record: str) -> tuple[str, ...]:
    return tuple(f.name for f in kinds.record(record))


TRIP = fields_of("Trip")
FILL = fields_of("Fill")
NOTE = fields_of("TripNote")
TAG = fields_of("Tag")
PLAYBOOK = fields_of("Playbook")
ORDER_REF = fields_of("OrderRef")


def pick(row: Mapping[str, Any], names: Iterable[str]) -> dict[str, Any]:
    return {name: row.get(name) for name in names}


def trip_out(row: Mapping[str, Any]) -> dict[str, Any]:
    return pick(row, TRIP)


def row_out(row: Mapping[str, Any], note: Mapping[str, Any] | None) -> dict[str, Any]:
    """A row of the trades list (``TripRow``): the trip and its mistake tags."""
    return {**pick(row, TRIP), "mistakes": list((note or {}).get("mistakes") or [])}


def fill_out(row: Mapping[str, Any], ref: Mapping[str, Any] | None) -> dict[str, Any]:
    """A fill as executed (``Fill``), with the surface its order was placed from."""
    return {**pick(row, FILL), "surface": (ref or {}).get("surface")}


def excursion_out(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """The trip's excursion (``Excursion``); None while it is open. Capture is the net over the
    MFE, only where the MFE is above 0 (design 5.4)."""
    if row["status"] != "closed":
        return None
    mfe, net = row.get("mfe_paise"), row.get("net_paise")
    capture = (
        money.ratio_str(int(net) * 100, int(mfe), 2) if mfe and mfe > 0 and net is not None
        else None
    )  # fmt: skip
    return {"source": row["excursion_source"], "mae_paise": row.get("mae_paise"),
            "mfe_paise": mfe, "mae_ms": row.get("mae_ms"), "mfe_ms": row.get("mfe_ms"),
            "capture_pct": capture, "note": row.get("excursion_note")}  # fmt: skip


def actual_out(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """A trip's actual charges (``ChargeActual``), or None while it carries the estimate."""
    from .actual import PART_LABELS
    from .core.spread import ACTUAL_PARTS

    kept = row.get("charges_actual_parts")
    if row.get("charges_actual_paise") is None or not isinstance(kept, Mapping):
        return None
    parts = kept.get("parts") or {}
    return {
        "parts": [{"part": p, "label": PART_LABELS[p], "paise": int(parts[p])}
                  for p in ACTUAL_PARTS if p in parts],
        "total_paise": int(row["charges_actual_paise"]),
        "basis": row.get("charges_actual_basis") or "day",
        "partial": bool(kept.get("partial")),
        "missing": [PART_LABELS.get(m, m) for m in kept.get("missing") or []],
    }  # fmt: skip


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
