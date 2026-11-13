"""``trips``, ``trip_get`` and ``trip_update`` (JB4, M14.1 and M14.4; design 6.2 and 7.2)."""

from __future__ import annotations

from typing import Any

from ..book import update_trip
from ..core import kinds
from ..core.metrics import strategy_of
from ..errors import ApiError
from ..views import done, leg_out, note_out, ref_out, trip_out
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
    filtered = strategy is not None or tag is not None

    def read(tx):
        rows = tx.trips_page(
            mode, status=status, before_ms=before, from_day=start, to_day=end,
            setup_id=playbook, limit=limit * OVERFETCH if filtered else limit,
        )  # fmt: skip
        if strategy is not None:
            rows = [r for r in rows if strategy_of(r).casefold() == strategy.casefold()]
        if tag is not None:
            notes = tx.notes_of(r["id"] for r in rows)
            rows = [r for r in rows if tag in (notes.get(r["id"]) or {}).get("mistakes", [])]
        return [trip_out(r) for r in rows[:limit]]

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
        return {
            **trip_out(row),
            "legs": [leg_out(leg) for leg in legs],
            "note": note_out(tx.note_of(trip_id)),
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
