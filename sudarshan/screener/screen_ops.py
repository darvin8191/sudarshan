"""The ops of the sealed local API (M9.1, design 6.3): what the /screener page asks the service.

Each op is an ``async def (ctx, args)`` registered in ``ops.REGISTRY``; ``ctx`` is the service
(``settings``, ``store``, ``clock``, ``parts``). Every store call goes through ``store.read`` or
``store.write`` and so runs on the store thread; an op never writes SQLite from the loop (S13).
A refused request is an ``ApiError`` whose message is the owner's sentence and whose ``field``
names the line of the screen it is about (``SpecError``).

Live facts (whether the market trades, the feed's condition, loading counts) come from the
runtime's parts when they are registered (SV5: ``ctx.parts["engine"]`` answering
``engine_facts(now)``); without them the engine line is built from the sessions and the store
alone, which is what a service without a feed link can honestly say.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from datetime import datetime
from typing import Any

from sudarshan.alerts.market.sessions import day_key, sessions_of
from sudarshan.autologin.schedule import IST

from . import alerting as al
from . import catalog as cat
from . import membership as mem
from . import presets, scheduler, spec, words
from .contract import MEMBERSHIP_ACTIONS, SCREEN_ACTIONS, WORDS
from .operand import SpecError
from .ops import ApiError, op
from .store import OWNER, NotFound, StoreError, Tx, VersionConflict
from .types import DAY

SEGMENTS_OF = {
    "fno_stocks": frozenset({"NSE_EQ", "NSE_EQ+auction"}),
    "index_list": frozenset({"NSE_EQ", "NSE_EQ+auction"}),
    "fno_futures": frozenset({"NSE_FO"}),
    "index_futures": frozenset({"NSE_FO"}),
    "options": frozenset({"NSE_FO"}),
    "indices": frozenset({"NSE_INDEX"}),
    "watchlist": frozenset({"NSE_EQ", "NSE_EQ+auction", "NSE_FO", "NSE_INDEX"}),
}
VALUES_GONE = "Values are kept for 7 days; the list for 100 days."
RUNS_PAGE = 50
RUNS_PAGE_MAX = 200


def _store(ctx):
    if ctx.store is None:
        raise ApiError.of("unavailable", WORDS["not_answering"])
    return ctx.store


def _int(args: Mapping[str, Any], name: str, *, required: bool = True) -> int | None:
    value = args.get(name)
    if value is None:
        if required:
            raise ApiError.of("bad_request", WORDS["unreadable"])
        return None
    if value.__class__ is not int or value < 0:
        raise ApiError.of("bad_request", WORDS["unreadable"])
    return value


def _spec(raw) -> tuple[dict, Any]:
    try:
        return spec.build(raw)
    except SpecError as exc:
        raise ApiError.of("refused", exc.message, exc.field) from None


def _translate(call):
    """Run a store call; the store's own refusals become the contract's codes."""
    try:
        return call()
    except NotFound as exc:
        raise ApiError.of("not_found", str(exc)) from None
    except VersionConflict as exc:
        raise ApiError.of("version_conflict", str(exc)) from None
    except StoreError as exc:
        raise ApiError.of("unavailable", str(exc)) from None


def sched_screen(row: Mapping[str, Any]) -> scheduler.Screen:
    stored = row["spec"]
    run = stored["run"]
    universe = stored["universe"]["kind"]
    tf = run.get("tf", DAY)
    return scheduler.Screen(
        row["id"],
        row["version"],
        run["on"],
        tf if isinstance(tf, int) else 0,
        SEGMENTS_OF.get(universe, frozenset({"NSE_EQ"})),
    )


def _sessions(ctx, now: int):
    """Today's sessions from the calendar (an empty mapping when today does not trade)."""
    today = datetime.fromtimestamp(now / 1000, IST).date()
    calendar = ctx.ensure_calendar()
    windows = calendar.windows() if hasattr(calendar, "windows") else {}
    return sessions_of(today, trading_day=calendar.is_trading_day(today), window=windows.get(today))


def engine_line(ctx, now: int, active: list) -> dict[str, Any]:
    """The line under the header: the runtime's facts when it has them, else sessions and store."""
    facts = ctx.parts.get("engine")
    base: dict[str, Any] = {"now": now, "enabled": ctx.enabled}
    if facts is not None and hasattr(facts, "engine_facts"):
        base.update(facts.engine_facts(now))
    else:
        sessions = _sessions(ctx, now)
        base["trading"] = any(s.start <= now < s.end for s in sessions.values())
        base["next_at"] = scheduler.next_at(active, sessions, now) if active else None
    engine = words.Engine(
        **{k: v for k, v in base.items() if k in words.Engine.__dataclass_fields__}
    )
    return {
        "state": words.engine_state(engine),
        "text": words.engine_words(engine),
        **asdict(engine),
    }


def _summary(tx: Tx, row: Mapping[str, Any]) -> dict[str, Any]:
    state = tx.state(row["id"]) or {}
    latest = tx.latest_run(row["id"])
    return {
        **row,
        "where": presets.where(row["spec"]),
        "alerts_paused": bool(state.get("alerts_paused")),
        "list_id": state.get("list_id") or (row["spec"].get("target") or {}).get("list_id"),
        "last_run": latest,
        "alerts": words.alerts_words(
            al.Policy.of(row["spec"].get("alerts")), paused=bool(state.get("alerts_paused"))
        ),
    }


@op("state")
async def state(ctx, args: Mapping[str, Any]) -> dict[str, Any]:
    now = ctx.clock()
    store = _store(ctx)
    screens = _translate(
        lambda: store.read(OWNER, lambda tx: [_summary(tx, r) for r in tx.screens()])
    )
    active = [sched_screen(r) for r in screens if r["status"] == "active"]
    return {
        "enabled": True,
        "screens": screens,
        "engine": engine_line(ctx, now, active),
        "budget": {
            "active": len(active),
            "max_active": ctx.settings.max_active,
            "max_keys": ctx.settings.max_keys,
        },
    }


@op("create")
async def create(ctx, args: Mapping[str, Any]) -> dict[str, Any]:
    stored, _plan = _spec(args.get("spec"))
    status = args.get("status", "active")
    if status not in ("active", "paused"):
        raise ApiError.of("bad_request", WORDS["unreadable"])
    request_id = args.get("request_id")
    if request_id is not None and (request_id.__class__ is not str or len(request_id) > 64):
        raise ApiError.of("bad_request", WORDS["unreadable"])
    now = ctx.clock()
    store = _store(ctx)

    def make(tx: Tx):
        if request_id is not None:
            have = tx.screen_by_request(request_id)
            if have is not None:
                return _summary(tx, have)
        if status == "active" and stored["run"]["on"] == "close":
            busy = sum(
                1
                for r in tx.screens()
                if r["status"] == "active" and r["spec"]["run"]["on"] == "close"
            )
            if busy >= ctx.settings.max_active:
                raise ApiError.of(
                    "refused",
                    f"Up to {ctx.settings.max_active} screens can check during the day at once. "
                    "Pause one, or make this one run after the close.",
                    "run",
                )
        return _summary(tx, tx.screen_insert(stored["name"], stored, status, now, request_id))

    return _translate(lambda: store.write(OWNER, make))


@op("update")
async def update(ctx, args: Mapping[str, Any]) -> dict[str, Any]:
    screen_id = _int(args, "id")
    version = _int(args, "version")
    action = args.get("action")
    if action not in SCREEN_ACTIONS:
        raise ApiError.of("bad_request", WORDS["unreadable"])
    stored = _spec(args.get("spec"))[0] if action == "edit" else None
    now = ctx.clock()
    store = _store(ctx)

    def change(tx: Tx):
        if action == "edit":
            row = tx.screen_change(
                screen_id, version, {"spec": stored, "name": stored["name"]}, now
            )
        elif action in ("pause", "resume"):
            row = tx.screen_change(
                screen_id, version, {"status": "active" if action == "resume" else "paused"}, now
            )
        else:
            row = tx.screen_change(screen_id, version, {}, now)  # the version moves with the state
            tx.state_put(screen_id, now, alerts_paused=(action == "alerts_off"))
        return _summary(tx, row)

    return _translate(lambda: store.write(OWNER, change))


@op("delete")
async def delete(ctx, args: Mapping[str, Any]) -> dict[str, Any]:
    screen_id = _int(args, "id")
    remove_added = bool(args.get("remove_added"))
    now = ctx.clock()
    store = _store(ctx)

    def remove(tx: Tx):
        row = tx.screen(screen_id)
        if row is None:
            raise NotFound()
        state_row = tx.state(screen_id) or {}
        list_id = state_row.get("list_id") or (row["spec"].get("target") or {}).get("list_id") or 0
        left = 0
        if remove_added and list_id:
            for found in tx.memberships(screen_id=screen_id, list_id=list_id):
                if found["added_by_screener"] and found["status"] in (mem.ACTIVE, mem.MISSING):
                    gone = {**found, "status": mem.EXITED, "pinned": False, "exited_at": now}
                    tx.membership_put(screen_id, list_id, gone, now)
                    left += 1
            tx.outbox_add(
                "list_sync",
                f"{screen_id}:{list_id}",
                {"screen_id": screen_id, "list_id": list_id, "create_name": None},
                now,
                coalesce=True,
            )
        tx.screen_remove(screen_id)
        return {"deleted": screen_id, "removed_from_list": left}

    return _translate(lambda: store.write(OWNER, remove))


@op("preview")
async def preview(ctx, args: Mapping[str, Any]) -> dict[str, Any]:
    stored, plan = _spec(args.get("spec"))
    out: dict[str, Any] = {
        "spec": stored,
        "where": presets.where(stored),
        "needs": {str(tf): count for tf, count in plan.need.items()},
        "instances": len(plan.instances),
        "uses": sorted(plan.uses),
    }
    source = ctx.parts.get("source")
    if source is not None and hasattr(source, "preview"):
        out["results"] = await source.preview(plan, stored, ctx.clock())
    else:
        out["results"] = None
        out["note"] = "Live figures come once the screener's price feed is connected."
    return out


@op("results")
async def results(ctx, args: Mapping[str, Any]) -> dict[str, Any]:
    screen_id = _int(args, "id")
    store = _store(ctx)

    def read(tx: Tx):
        row = tx.screen(screen_id)
        if row is None:
            raise NotFound()
        state_row = tx.state(screen_id) or {}
        latest = tx.latest_run(screen_id)
        list_id = state_row.get("list_id") or (row["spec"].get("target") or {}).get("list_id") or 0
        members = tx.memberships(screen_id=screen_id, list_id=list_id)
        values = tx.run_values(latest["id"]) if latest else {}
        changes = tx.run_changes(latest["id"]) if latest else []
        return {
            "screen": _summary(tx, row),
            "run": latest,
            "marks": words.mark_words(latest["marks"]) if latest else (),
            "members": members,
            "values": values,
            "changes": changes,
        }

    return _translate(lambda: store.read(OWNER, read))


@op("runs")
async def runs(ctx, args: Mapping[str, Any]) -> dict[str, Any]:
    screen_id = _int(args, "id")
    before = _int(args, "before", required=False)
    limit = min(_int(args, "limit", required=False) or RUNS_PAGE, RUNS_PAGE_MAX)
    store = _store(ctx)

    def read(tx: Tx):
        if tx.screen(screen_id) is None:
            raise NotFound()
        page = tx.runs_page(screen_id, before, limit)
        for row in page:
            row["mark_words"] = words.mark_words(row["marks"])
        return {"runs": page, "more": len(page) == limit}

    return _translate(lambda: store.read(OWNER, read))


@op("run")
async def run(ctx, args: Mapping[str, Any]) -> dict[str, Any]:
    run_id = _int(args, "id")
    store = _store(ctx)

    def read(tx: Tx):
        row = tx.run(run_id)
        if row is None:
            raise ApiError.of("not_found", "This check is no longer in the history.")
        values = tx.run_values(run_id)
        return {
            "run": row,
            "changes": tx.run_changes(run_id),
            "values": values,
            "values_note": None if values else VALUES_GONE,
            "mark_words": words.mark_words(row["marks"]),
        }

    return _translate(lambda: store.read(OWNER, read))


@op("runs_since")
async def runs_since(ctx, args: Mapping[str, Any]) -> dict[str, Any]:
    after = _int(args, "after", required=False) or 0
    limit = min(_int(args, "limit", required=False) or RUNS_PAGE, RUNS_PAGE_MAX)
    store = _store(ctx)
    page = _translate(lambda: store.read(OWNER, lambda tx: tx.runs_since(after, limit)))
    return {"runs": page, "more": len(page) == limit}


@op("membership")
async def membership(ctx, args: Mapping[str, Any]) -> dict[str, Any]:
    list_id = _int(args, "list", required=False)
    screen_id = _int(args, "screen", required=False)
    if list_id is None and screen_id is None:
        raise ApiError.of("bad_request", WORDS["unreadable"])
    store = _store(ctx)
    rows = _translate(lambda: store.read(OWNER, lambda tx: tx.memberships(screen_id, list_id)))
    return {"rows": rows}


@op("membership_update")
async def membership_update(ctx, args: Mapping[str, Any]) -> dict[str, Any]:
    member_id = _int(args, "id")
    version = _int(args, "version")
    action = args.get("action")
    if action not in MEMBERSHIP_ACTIONS:
        raise ApiError.of("bad_request", WORDS["unreadable"])
    now = ctx.clock()
    store = _store(ctx)
    move = {"pin": mem.pin, "unpin": mem.unpin, "dismiss": mem.dismiss, "restore": mem.restore}[
        action
    ]

    def change(tx: Tx):
        found = tx.membership(member_id)
        if found is None:
            raise NotFound()
        row = mem.Row(**{n: found[n] for n in mem.Row.__dataclass_fields__ if n in found})
        moved = move(row)
        if moved is None:
            raise ApiError.of("refused", _refusal(action, row))
        fields = {
            n: getattr(moved, n) for n in ("status", "pinned", "misses", "hits", "missing_session")
        }
        out = tx.membership_change(member_id, version, fields, now)
        if found["list_id"]:
            tx.outbox_add(
                "list_sync",
                f"{found['screen_id']}:{found['list_id']}",
                {"screen_id": found["screen_id"], "list_id": found["list_id"], "create_name": None},
                now,
                coalesce=True,
            )
        return out

    return _translate(lambda: store.write(OWNER, change))


def _refusal(action: str, row: mem.Row) -> str:
    if action == "restore":
        return "Only an instrument you removed can be restored."
    return (
        f"{row.symbol} is not in the list any more, so it cannot be {action}ned."
        if action == "pin"
        else (f"{row.symbol} is not in the list any more.")
    )


@op("presets")
async def presets_op(ctx, args: Mapping[str, Any]) -> dict[str, Any]:
    followed = ctx.settings.max_keys >= 700
    return {"presets": list(presets.library(futures_followed=followed))}


@op("universes")
async def universes(ctx, args: Mapping[str, Any]) -> dict[str, Any]:
    store = _store(ctx)
    built = _translate(lambda: store.read(OWNER, Tx.universes))
    return {"kinds": list(cat.UNIVERSES), "built": built}


@op("today")
async def today(ctx, args: Mapping[str, Any]) -> dict[str, Any]:
    now = ctx.clock()
    store = _store(ctx)
    screens = _translate(
        lambda: store.read(OWNER, lambda tx: [r for r in tx.screens() if r["status"] == "active"])
    )
    active = [sched_screen(r) for r in screens]
    sessions = _sessions(ctx, now)
    day = datetime.fromtimestamp(now / 1000, IST).date()
    return {
        "session": day_key(day) if sessions else None,
        "trading": bool(sessions),
        "boundaries": {
            str(s.id): [
                {"k": b.k, "end": b.end, "at": b.at} for b in scheduler.boundaries(s, sessions)
            ]
            for s in active
        },
        "next_at": scheduler.next_at(active, sessions, now) if active else None,
    }
