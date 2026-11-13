"""``trips``, ``trip_get`` and ``trip_update`` (JB4, M14.1 and M14.4; design 6.2 and 7.2)."""

from __future__ import annotations

from typing import Any

from ..book import charges_of, update_trip
from ..core import kinds
from ..core.metrics import strategy_of
from ..errors import ApiError
from ..views import done, excursion_out, fill_out, leg_out, note_out, ref_out, row_out, trip_out
from . import op
from .common import OWNER, choice, need, text, whole, work

DAY_LOW, DAY_HIGH = 19_700_101, 29_991_231
LIMIT_DEFAULT, LIMIT_MAX = 100, 500
#: Fetched before the filters that are not columns (strategy, tag), so a page still fills.
OVERFETCH = 4
TRIP_FIELDS = ("strategy_override", "setup_id", "risk_paise", "notes", "mistakes", "checklist",
               "mood")  # fmt: skip


@op("trips")
async def trips(ctx, args):
    store = need(ctx)
    mode = choice(args, "mode", kinds.MODES, "live")
    status = choice(args, "status", kinds.TRIP_STATUSES, None)
    before = whole(args, "before", low=0, default=None)
    limit = whole(args, "limit", low=1, high=LIMIT_MAX, default=LIMIT_DEFAULT)
    strategy = text(args, "strategy", limit=kinds.LIMITS["strategy_name"], default=None)
    tag = whole(args, "tag", low=1, default=None)
    playbook = whole(args, "playbook", low=1, default=None)
    start = whole(args, "from", low=DAY_LOW, high=DAY_HIGH, default=None)
    end = whole(args, "to", low=DAY_LOW, high=DAY_HIGH, default=None)
    # ``before`` is the last row's close: a later page holds closed trips only (the open ones
    # led the first page). An orphan is listed only when asked for by its status.
    if before is not None and status is None:
        status = "closed"
    hidden = () if status is not None else ("orphan",)
    filtered = strategy is not None or tag is not None or bool(hidden)

    def read(tx):
        rows = tx.trips_page(
            mode, status=status, before_ms=before, from_day=start, to_day=end,
            setup_id=playbook, limit=limit * OVERFETCH if filtered else limit,
        )  # fmt: skip
        rows = [r for r in rows if r["status"] not in hidden]
        if strategy is not None:
            rows = [r for r in rows if strategy_of(r).casefold() == strategy.casefold()]
        notes = tx.notes_of(r["id"] for r in rows)
        if tag is not None:
            rows = [r for r in rows if tag in (notes.get(r["id"]) or {}).get("mistakes", [])]
        return [row_out(r, notes.get(r["id"])) for r in rows[:limit]]

    return await work(lambda: store.read(OWNER, read))


@op("trip_get")
async def trip_get(ctx, args):
    store = need(ctx)
    trip_id = whole(args, "id", low=1)

    def read(tx):
        row = tx.get("round_trip", trip_id)
        if row is None:
            return None
        legs = tx.legs_of(trip_id)
        refs = tx.order_refs(row["mode"], {leg["order_id"] for leg in legs})
        fills = tx.fills_by_trade(row["mode"], [leg["trade_id"] for leg in legs])
        charges = None
        if legs and all(leg["trade_id"] in fills for leg in legs):
            charges = charges_of(
                [{"trade_id": leg["trade_id"], "qty": leg["leg_qty"]} for leg in legs],
                fills, refs, row["mode"], tx.settings()["charges_plan"],
            )  # fmt: skip
        entry = next((leg for leg in legs if leg["leg_role"] == "entry"), None)
        first_ref = refs.get(entry["order_id"]) if entry else None
        seen: set[str] = set()
        executed = []
        for leg in legs:
            if leg["trade_id"] in fills and leg["trade_id"] not in seen:
                seen.add(leg["trade_id"])
                fill = fills[leg["trade_id"]]
                executed.append(fill_out(fill, refs.get(fill["order_id"])))
        return {
            **trip_out(row),
            "legs": [leg_out(leg) for leg in legs],
            "fills": executed,
            "note": note_out(tx.note_of(trip_id)),
            "excursion": excursion_out(row),
            "charges": charges,
            "latency_ms": (first_ref or {}).get("rtt_ms"),
            "orders": [ref_out(refs[o]) for o in sorted(refs)],
        }

    found = await work(lambda: store.read(OWNER, read))
    if found is None:
        raise ApiError(
            404, "not_found", "This trade is not in the journal any more. Open the list again."
        )
    return found


@op("trip_update")
async def trip_update(ctx, args):
    store = need(ctx)
    trip_id = whole(args, "id", low=1)
    version = whole(args, "version", low=1)
    changes: dict[str, Any] = {k: args[k] for k in TRIP_FIELDS if k in args}
    now = ctx.clock()
    new_version = await work(
        lambda: store.write(OWNER, lambda tx: update_trip(tx, trip_id, version, changes, now))
    )
    return done(trip_id, new_version)
