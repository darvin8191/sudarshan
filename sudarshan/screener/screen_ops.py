"""The ops of the sealed local API (M9.1, design 6.3): what the /screener page asks the service.

Each op is an ``async def (ctx, args)`` registered in ``ops.REGISTRY``; ``ctx`` is the service
(``settings``, ``store``, ``clock``, ``parts``). Every store call goes through ``store.read`` or
``store.write`` and so runs on the store thread; an op never writes SQLite from the loop (S13).
A refused request is an ``ApiError`` whose message is the owner's sentence and whose ``field``
names the line of the screen it is about (``SpecError``).

The answers take the shapes the page's ``frontend/src/sudarshan/screener/types.ts`` reads
(``ScreenerState``, ``ScreenRow``, ``Results``, ``ResultRow``, ``RunRow``, ``Preview``,
``PresetCard``, ``UniverseOption``, ``Today``, ``MemberRow``); every sentence in them is built
by ``words.py`` and shown as it came.

Live facts (whether the market trades, the feed's condition, loading counts) come from the
runtime's parts when they are registered (SV5: ``ctx.parts["engine"]`` answering
``engine_facts(now)``, ``ctx.parts["source"]`` answering a preview); without them the engine line
is built from the sessions and the store alone, which is what a service without a feed link can
honestly say.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

from sudarshan.alerts.market.sessions import day_key, epoch_ms, key_day, sessions_of
from sudarshan.autologin.schedule import IST

from . import alerting as al
from . import catalog as cat
from . import membership as mem
from . import presets, scheduler, spec, words
from .contract import EVENT_STATE, MEMBERSHIP_ACTIONS, SCREEN_ACTIONS, WORDS
from .operand import SpecError
from .ops import ApiError, op
from .store import OWNER, NotFound, StoreError, Tx, VersionConflict
from .types import DAY

log = logging.getLogger("sudarshan.screener")

SEGMENTS_OF = {
    "fno_stocks": frozenset({"NSE_EQ", "NSE_EQ+auction"}),
    "index_list": frozenset({"NSE_EQ", "NSE_EQ+auction"}),
    "fno_futures": frozenset({"NSE_FO"}),
    "index_futures": frozenset({"NSE_FO"}),
    "options": frozenset({"NSE_FO"}),
    "indices": frozenset({"NSE_INDEX"}),
    "watchlist": frozenset({"NSE_EQ", "NSE_EQ+auction", "NSE_FO", "NSE_INDEX"}),
}
UNIVERSE_LABELS = {
    "fno_stocks": "F&O stocks",
    "fno_futures": "Stock futures",
    "index_futures": "Index futures",
    "indices": "Indices",
    "options": "Option strikes",
    "index_list": "NIFTY list",
    "watchlist": "A watchlist",
}
NOT_FOLLOWED = "Checked after the close only: this list is not followed tick by tick."
VALUES_GONE = "Values are kept for 7 days; the list for 100 days."
PREVIEW_WAITS = "Preview needs the screener's price feed, which is not connected yet."
MEMBER_MOVED = (
    "This instrument changed with the latest check. Nothing was saved; look at it again, then "
    "repeat the change."
)
RUNS_PAGE = 30
RUNS_PAGE_MAX = 200
VALUE_COLUMNS = 2


# ---- helpers ---------------------------------------------------------------------------------------
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


def _moved(ctx) -> None:
    """The active screens or what they look at changed: the feed job rebuilds the universes and
    the claims at its next tick, not at the next midnight (release review HIGH-2)."""
    changed = getattr(ctx, "screens_changed", None)
    if callable(changed):
        changed()


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
    tf = run.get("tf", DAY)
    return scheduler.Screen(
        row["id"],
        row["version"],
        run["on"],
        tf if isinstance(tf, int) else 0,
        SEGMENTS_OF.get(stored["universe"]["kind"], frozenset({"NSE_EQ"})),
        row.get("updated_at") or 0,  # every change moves the version (store.screen_change)
    )


def _sessions(ctx, now: int):
    """Today's sessions from the calendar (an empty mapping when today does not trade)."""
    today = datetime.fromtimestamp(now / 1000, IST).date()
    calendar = ctx.ensure_calendar()
    windows = calendar.windows() if hasattr(calendar, "windows") else {}
    return sessions_of(today, trading_day=calendar.is_trading_day(today), window=windows.get(today))


def _session_key(now: int) -> int:
    return day_key(datetime.fromtimestamp(now / 1000, IST).date())


def _sessions_on(ctx, day):
    calendar = ctx.ensure_calendar()
    windows = calendar.windows() if hasattr(calendar, "windows") else {}
    return sessions_of(day, trading_day=calendar.is_trading_day(day), window=windows.get(day))


def next_check(ctx, now: int, active: Sequence[scheduler.Screen], sessions) -> int | None:
    """When the active screens check next: today, or the next trading day's first check
    ("Market closed · next check Mon 09:30"); None with no active screen."""
    if not active:
        return None
    found = scheduler.next_at(active, sessions, now)
    if found is None:
        found = scheduler.next_trading_boundary(
            active, now, sessions_of=lambda day: _sessions_on(ctx, day)
        )
    return found


def engine_of(ctx, now: int, active: Sequence[scheduler.Screen], sessions=None) -> words.Engine:
    """The runtime's facts when it has them, else sessions and store; the next check either way
    (MEDIUM-3)."""
    facts = ctx.parts.get("engine")
    sessions = _sessions(ctx, now) if sessions is None else sessions
    base: dict[str, Any] = {"now": now, "enabled": ctx.enabled}
    if facts is not None and hasattr(facts, "engine_facts"):
        base.update(facts.engine_facts(now))
    else:
        base["trading"] = any(s.start <= now < s.end for s in sessions.values())
        base["feed"] = "not_linked"  # no feed link registered: never claim real time (rule 7)
    base["next_at"] = next_check(ctx, now, active, sessions)
    return words.Engine(**{k: v for k, v in base.items() if k in words.Engine.__dataclass_fields__})


def engine_line(ctx, now: int, active: Sequence[scheduler.Screen], sessions=None) -> dict[str, Any]:
    """The line under the header, e.g. 'Real-time · next check 10:45'."""
    engine = engine_of(ctx, now, active, sessions)
    state = words.engine_state(engine)
    return {
        "state": state,
        "line": words.engine_words(engine),
        "real_time": state == words.REAL_TIME,
        "next_at": engine.next_at,
    }


def _change_pct(ltp, cp) -> float | None:
    if not isinstance(ltp, int) or not isinstance(cp, int) or not cp:
        return None
    value = (Decimal(ltp) - Decimal(cp)) * 100 / Decimal(cp)
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN))


def _label(operand: Mapping[str, Any]) -> str:
    if "field" in operand:
        return (
            cat.FIELDS[operand["field"]].words
            if operand["field"] in cat.FIELDS
            else operand["field"]
        )
    if "ind" in operand:
        return str(operand["ind"]).upper()
    if "oi" in operand:
        return "OI change" if operand["oi"] == "oi_change_pct" else "OI"
    if "opt" in operand:
        return str(operand["opt"]).replace("_", " ")
    if "math" in operand:
        return "value"
    return ""


def _clauses(node) -> list[dict]:
    if isinstance(node, dict) and "id" in node and "left" in node:
        return [node]
    out = []
    if isinstance(node, dict):
        for key in ("all", "any", "none"):
            for child in node.get(key, ()):
                out.extend(_clauses(child))
        if "not" in node:
            out.extend(_clauses(node["not"]))
    return out


def values_text(
    stored: Mapping[str, Any], found: Mapping[str, Any] | None, only: Sequence[str] | None = None
) -> str:
    """'RSI 61.2 · OI +4.1%': the first two clause values of a match, labelled (``only``: the
    clauses a dashboard widget chose, in its order)."""
    if not found:
        return ""
    parts = []
    clauses = _clauses(stored.get("where"))
    if only:
        by_id = {c["id"]: c for c in clauses}
        clauses = [by_id[i] for i in only if i in by_id]
    for clause in clauses:
        value = found.get(clause["id"])
        if isinstance(value, dict) and value.get("l") is not None:
            label = _label(clause["left"])
            parts.append(f"{label} {value['l']}".strip())
        if len(parts) == VALUE_COLUMNS:
            break
    return " · ".join(parts)


def run_row(run: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if run is None:
        return None
    return {
        "id": run["id"],
        "screen_id": run["screen_id"],
        "at": run["candle_end"],
        "candle": f"{words.hm(run['candle_start'])} to {words.hm(run['candle_end'])}",
        "matches": run["matched"],
        "entered": run["entered"],
        "exited": run["exited"],
        "missing": run["missing"],
        "not_checked": run["not_checked"],
        "status": run["status"],
        "marks": list(words.mark_words(run.get("marks"))),
        "alerts": (run.get("marks") or {}).get("alerts"),
    }


def _chip_of(row: Mapping[str, Any], *, now: int, last_run: int, grace: mem.Grace, screen: str):
    found = mem.Row(**{n: row[n] for n in mem.Row.__dataclass_fields__ if n in row})
    chip = words.chip(found, now=now, last_run=last_run, grace=grace, screen=screen)
    return asdict(chip) if chip is not None else None


def result_rows(
    stored: Mapping[str, Any],
    screen_name: str,
    run: Mapping[str, Any] | None,
    changes: Sequence[Mapping[str, Any]],
    values: Mapping[str, Any],
    members: Sequence[Mapping[str, Any]],
    now: int,
    value_clauses: Sequence[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """(rows, not_checked) of a run: every instrument that matched, best ranked first, with its
    membership chip; the not-checked count and its reasons."""
    grace = mem.Grace.of((stored.get("target") or {}).get("grace"))
    order = (stored.get("rank") or {}).get("order", "desc")
    by_key = {m["key"]: m for m in members}
    entries = {c["key"]: c for c in changes if c["change"] == "in"}
    # members matching now that this run did not list as "in" (a later run of the day): keep them
    for member in members:
        if member["status"] == mem.ACTIVE and member["key"] not in entries:
            entries[member["key"]] = {
                "key": member["key"],
                "symbol": member["symbol"],
                "exchange": member["exchange"],
            }

    def best(entry: Mapping[str, Any]) -> tuple:
        """As the screen ranks (MEDIUM-2): the run's own rank value, else the member's score."""
        found = values.get(entry["key"]) or {}
        rank = found["_rank"] if "_rank" in found else (by_key.get(entry["key"]) or {}).get("score")
        return mem.best_first(rank, entry["symbol"], entry["key"], order)

    ranked = sorted(entries.values(), key=best)
    rows = []
    for position, change in enumerate(ranked, start=1):
        found = values.get(change["key"]) or {}
        member = by_key.get(change["key"])
        rows.append(
            {
                "member_id": member["id"] if member else None,
                "key": change["key"],
                "symbol": change["symbol"],
                "exchange": change["exchange"],
                "rank": position,
                "values": values_text(stored, found, value_clauses),
                "forming": bool(found.get("_forming")),
                "chip": _chip_of(
                    member,
                    now=now,
                    last_run=run["id"] if run else 0,
                    grace=grace,
                    screen=screen_name,
                )
                if member
                else None,
                "ltp": found.get("_ltp"),
                "change_pct": _change_pct(found.get("_ltp"), found.get("_cp")),
            }
        )
    reasons = []
    count = 0
    for change in changes:
        if change["change"] == "not_checked":
            count += 1
            text = (values.get(change["key"]) or {}).get("_not_checked")
            if isinstance(text, str) and text not in reasons:
                reasons.append(text)
    return rows, {"count": count, "reasons": reasons[: words.REASONS_SHOWN]}


def screen_row(
    tx: Tx, row: Mapping[str, Any], now: int, session: int, next_at: int | None
) -> dict[str, Any]:
    state = tx.state(row["id"]) or {}
    latest = tx.latest_run(row["id"])
    entered_today = sum(
        r["entered"]
        for r in tx.runs_page(row["id"], None, 400)
        if r["session"] == session and r["status"] == "done"
    )
    target = row["spec"].get("target") or {}
    list_id = state.get("list_id") or target.get("list_id") or 0
    added = sum(
        1
        for m in tx.memberships(screen_id=row["id"], list_id=list_id)
        if m["added_by_screener"] and m["in_list"]
    )
    return {
        "id": row["id"],
        "version": row["version"],
        "name": row["name"],
        "status": row["status"],
        "status_reason": row.get("status_reason"),
        "where": presets.where(row["spec"]),
        "spec": row["spec"],
        "matches": latest["matched"] if latest else 0,
        "entered_today": entered_today,
        "added_count": added,
        "target_name": state.get("list_name")
        or (target.get("create_name") if target and not list_id else None),
        "list_id": list_id or None,
        "next_check": f"next check {words.when(next_at, now)}"
        if next_at and row["status"] == "active"
        else None,
        "last_run_id": latest["id"] if latest else 0,
        "alerts": words.alerts_words(
            al.Policy.of(row["spec"].get("alerts")), paused=bool(state.get("alerts_paused"))
        ),
        "alerts_paused": bool(state.get("alerts_paused")),
        "updated_at": row["updated_at"],
    }


# ---- the ops -----------------------------------------------------------------------------------------
#: How often the engine line is looked at for a change to push (the page's line follows it).
ENGINE_TICK_S = 1.0


async def run_engine_push(service, *, tick_s: float = ENGINE_TICK_S) -> None:
    """The job: the engine line pushed to an open page whenever it changes
    (``sudarshan_screener_state``, design 4.5), so it never stays "Real-time" after the feed
    stops or the market closes while the page is open (release review MEDIUM-3)."""
    last: dict[str, Any] | None = None
    while True:
        try:
            last = engine_push(service, last)
        except StoreError:
            log.warning("the engine line could not be read for the page")
        except Exception:
            log.exception("the engine line push failed")
        await asyncio.sleep(tick_s)


def engine_push(service, last: dict[str, Any] | None) -> dict[str, Any]:
    """One look at the engine line: a push is queued when it differs from ``last``."""
    now = service.clock()
    sessions = _sessions(service, now)
    rows = service.store.read(
        OWNER, lambda tx: [r for r in tx.screens() if r["status"] == "active"]
    )
    line = engine_line(service, now, [sched_screen(r) for r in rows], sessions)
    if last is None or (line["state"], line["line"]) != (last["state"], last["line"]):
        payload = {"_event": EVENT_STATE, "engine": line, "screens": [], "removed": []}
        service.store.write(
            OWNER, lambda tx: tx.outbox_add("push", "engine", payload, now, coalesce=True)
        )
    return line


@op("state")
async def state(ctx, args: Mapping[str, Any]) -> dict[str, Any]:
    now = ctx.clock()
    store = _store(ctx)
    sessions = _sessions(ctx, now)
    session = _session_key(now)

    def read(tx: Tx):
        screens = tx.screens()
        active = [sched_screen(r) for r in screens if r["status"] == "active"]
        rows = []
        for row in screens:
            next_at = (
                scheduler.next_at([sched_screen(row)], sessions, now)
                if row["status"] == "active"
                else None
            )
            rows.append(screen_row(tx, row, now, session, next_at))
        newest = tx.runs_page_all(1)
        return rows, active, (newest[0]["id"] if newest else 0)

    rows, active, last_run_id = _translate(lambda: store.read(OWNER, read))
    intraday = sum(1 for r in rows if r["status"] == "active" and r["spec"]["run"]["on"] == "close")
    return {
        "enabled": True,
        "engine": engine_line(ctx, now, active, sessions),
        "screens": rows,
        "limits": {
            "active": ctx.settings.max_active,
            "active_used": intraday,
            "max_keys": ctx.settings.max_keys,
        },
        "last_run_id": last_run_id,
        "server_time": now,
    }


def _summary(ctx, tx: Tx, row: Mapping[str, Any], now: int) -> dict[str, Any]:
    sessions = _sessions(ctx, now)
    next_at = (
        scheduler.next_at([sched_screen(row)], sessions, now) if row["status"] == "active" else None
    )
    return screen_row(tx, row, now, _session_key(now), next_at)


def _target_of(stored: Mapping[str, Any]) -> tuple:
    """Which list a screen feeds, as the owner chose it: an existing list, a new one by name,
    or none."""
    target = stored.get("target") or {}
    return target.get("list_id"), target.get("create_name")


def _within_limit(ctx, tx: Tx, screen_id: int | None) -> None:
    """Refuse a screen that would make more than the limit check during the day at once
    (SUDARSHAN_SCREENER_MAX_ACTIVE), for a create, a resume and an edit alike (MEDIUM-8)."""
    busy = sum(
        1
        for r in tx.screens()
        if r["id"] != screen_id and r["status"] == "active" and r["spec"]["run"]["on"] == "close"
    )
    if busy >= ctx.settings.max_active:
        raise ApiError.of(
            "refused",
            f"Up to {ctx.settings.max_active} screens can check during the day at once. "
            "Pause one, or make this one run after the close.",
            "run",
        )


@op("create")
async def create(ctx, args: Mapping[str, Any]) -> dict[str, Any]:
    stored, _plan = _spec(args.get("spec"))
    status = args.get("status", "active")
    if status not in ("active", "paused"):
        raise ApiError.of("bad_request", WORDS["unreadable"])
    request_id = args.get("request_id")
    if request_id is not None and (
        request_id.__class__ is not str or not 0 < len(request_id) <= 64
    ):
        raise ApiError.of("bad_request", WORDS["unreadable"])
    now = ctx.clock()
    store = _store(ctx)

    def make(tx: Tx):
        if request_id is not None:
            have = tx.screen_by_request(request_id)
            if have is not None:
                return _summary(ctx, tx, have, now)
        if status == "active" and stored["run"]["on"] == "close":
            _within_limit(ctx, tx, None)
        return _summary(
            ctx, tx, tx.screen_insert(stored["name"], stored, status, now, request_id), now
        )

    made = _translate(lambda: store.write(OWNER, make))
    _moved(ctx)
    return made


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
        before = tx.screen(screen_id)
        if before is not None and before["version"] == version:
            status = "active" if action == "resume" else before["status"]
            run_on = (stored or before["spec"])["run"]["on"]
            counted = before["status"] == "active" and before["spec"]["run"]["on"] == "close"
            if action in ("edit", "resume") and status == "active" and run_on == "close":
                if (
                    not counted
                ):  # it would start checking during the day: the same limit as a create
                    _within_limit(ctx, tx, screen_id)
        if action == "edit":
            row = tx.screen_change(
                screen_id, version, {"spec": stored, "name": stored["name"]}, now
            )
            if before is not None and _target_of(before["spec"]) != _target_of(stored):
                # a new target list, or none: the list made for the old target is not this
                # screen's any more (MEDIUM-10); its rows and items stay where they are
                tx.state_put(screen_id, now, list_id=None, list_name=None)
        elif action in ("pause", "resume"):
            row = tx.screen_change(
                screen_id, version, {"status": "active" if action == "resume" else "paused"}, now
            )
        else:
            row = tx.screen_change(screen_id, version, {}, now)  # the version moves with the state
            tx.state_put(screen_id, now, alerts_paused=(action == "alerts_off"))
        return _summary(ctx, tx, row, now)

    changed = _translate(lambda: store.write(OWNER, change))
    if action in ("edit", "pause", "resume"):
        _moved(ctx)
    return changed


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
            # the list sync takes out what the screener put in and no other screen still wants;
            # the answer counts exactly those (release review HIGH-6)
            others = {
                m["key"]
                for m in tx.memberships(list_id=list_id)
                if m["screen_id"] != screen_id and m["status"] in (mem.ACTIVE, mem.MISSING)
            }
            for found in tx.memberships(screen_id=screen_id, list_id=list_id):
                if not found["added_by_screener"]:
                    continue
                if found["status"] in (mem.ACTIVE, mem.MISSING):
                    gone = {**found, "status": mem.EXITED, "pinned": False, "exited_at": now}
                    tx.membership_put(screen_id, list_id, gone, now)
                if found["in_list"] and found["key"] not in others:
                    left += 1
            tx.outbox_add(
                "list_sync",
                f"{screen_id}:{list_id}",
                {"screen_id": screen_id, "list_id": list_id, "create_name": None},
                now,
                coalesce=True,
            )
        tx.screen_remove(screen_id, now)
        return {"deleted": screen_id, "removed_from_list": left}

    deleted = _translate(lambda: store.write(OWNER, remove))
    _moved(ctx)
    return deleted


@op("preview")
async def preview(ctx, args: Mapping[str, Any]) -> dict[str, Any]:
    if "spec" in args:
        stored, plan = _spec(args.get("spec"))
    else:
        screen_id = _int(args, "screen_id")
        store = _store(ctx)
        row = _translate(lambda: store.read(OWNER, lambda tx: tx.screen(screen_id)))
        if row is None:
            raise ApiError.of("not_found", WORDS["gone"])
        stored, plan = _spec(row["spec"])
    source = ctx.parts.get("source")
    if source is not None and hasattr(source, "preview"):
        return await source.preview(plan, stored, ctx.clock())
    return {
        "label": PREVIEW_WAITS,
        "count": 0,
        "first": [],
        "not_checked": {"count": 0, "reasons": []},
        "needs": {str(tf): count for tf, count in plan.need.items()},
    }


@op("results")
async def results(ctx, args: Mapping[str, Any]) -> dict[str, Any]:
    screen_id = _int(args, "id")
    now = ctx.clock()
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
        rows, not_checked = result_rows(
            row["spec"], row["name"], latest, changes, values, members, now
        )
        return {
            "screen_id": screen_id,
            "run": run_row(latest),
            "rows": rows,
            "not_checked": not_checked,
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
        return {"runs": [run_row(r) for r in page], "more": len(page) == limit}

    return _translate(lambda: store.read(OWNER, read))


@op("run")
async def run(ctx, args: Mapping[str, Any]) -> dict[str, Any]:
    run_id = _int(args, "id")
    now = ctx.clock()
    store = _store(ctx)

    def read(tx: Tx):
        found = tx.run(run_id)
        if found is None:
            raise ApiError.of("not_found", "This check is no longer in the history.")
        screen = tx.screen(found["screen_id"])
        stored = screen["spec"] if screen else {"where": None}
        values = tx.run_values(run_id)
        own = tx.run_changes(run_id)
        changes = [c for c in own if c["change"] == "not_checked"] + _match_set(tx, found, values)
        rows, not_checked = result_rows(
            stored, screen["name"] if screen else "", found, changes, values, [], now
        )
        return {
            "screen_id": found["screen_id"],
            "run": run_row(found),
            "rows": rows,
            "not_checked": not_checked,
            "values_note": None if values or not rows else VALUES_GONE,
        }

    return _translate(lambda: store.read(OWNER, read))


def _match_set(tx: Tx, run: Mapping[str, Any], values: Mapping[str, Any]) -> list[dict]:
    """A past run's whole match set as "in" rows (MEDIUM-1): from its values while they are
    kept (every match has its own), else replayed from the day's first run, which records every
    match, and the later runs' changes up to this one."""
    hits = {k: v for k, v in values.items() if "_not_checked" not in v}
    if hits and all("_symbol" in v for v in hits.values()):
        return [
            {"key": k, "symbol": v["_symbol"], "exchange": v["_exchange"], "change": "in"}
            for k, v in hits.items()
        ]
    held: dict[str, dict] = {}
    for change in tx.session_changes(run["screen_id"], run["session"], run["id"]):
        if change["change"] == "in":
            held[change["key"]] = change
        elif change["change"] == "out":
            held.pop(change["key"], None)
    return list(held.values())


@op("runs_since")
async def runs_since(ctx, args: Mapping[str, Any]) -> dict[str, Any]:
    after = _int(args, "after", required=False) or 0
    limit = min(_int(args, "limit", required=False) or RUNS_PAGE, RUNS_PAGE_MAX)
    store = _store(ctx)
    page = _translate(lambda: store.read(OWNER, lambda tx: tx.runs_since(after, limit)))
    return {"runs": [run_row(r) for r in page], "more": len(page) == limit}


def member_row(tx: Tx, found: Mapping[str, Any], now: int) -> dict[str, Any]:
    screen = tx.screen(found["screen_id"])
    stored = screen["spec"] if screen else {}
    latest = tx.latest_run(found["screen_id"])
    grace = mem.Grace.of((stored.get("target") or {}).get("grace"))
    return {
        **found,
        "chip": _chip_of(
            found,
            now=now,
            last_run=latest["id"] if latest else 0,
            grace=grace,
            screen=screen["name"] if screen else "",
        ),
    }


@op("membership")
async def membership(ctx, args: Mapping[str, Any]) -> dict[str, Any]:
    list_id = _int(args, "list", required=False)
    screen_id = _int(args, "screen", required=False)
    if list_id is None and screen_id is None:
        raise ApiError.of("bad_request", WORDS["unreadable"])
    now = ctx.clock()
    store = _store(ctx)

    def read(tx: Tx):
        # a deleted screen's rows are kept for the history, never shown as its chips
        live = {s["id"] for s in tx.screens()}
        return [
            member_row(tx, r, now)
            for r in tx.memberships(screen_id, list_id)
            if r["screen_id"] in live
        ]

    rows = _translate(lambda: store.read(OWNER, read))
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
        try:
            out = tx.membership_change(member_id, version, fields, now)
        except VersionConflict:  # a check moved the row since the page read it (LOW-7)
            raise ApiError.of("version_conflict", MEMBER_MOVED) from None
        if found["list_id"]:
            tx.outbox_add(
                "list_sync",
                f"{found['screen_id']}:{found['list_id']}",
                {"screen_id": found["screen_id"], "list_id": found["list_id"], "create_name": None},
                now,
                coalesce=True,
            )
        return member_row(tx, out, now)

    return _translate(lambda: store.write(OWNER, change))


def _refusal(action: str, row: mem.Row) -> str:
    if action == "restore":
        return "Only an instrument you removed can be restored."
    if action == "pin":
        return f"{row.symbol} is not in the list any more, so it cannot be pinned."
    return f"{row.symbol} is not in the list any more."


@op("presets")
async def presets_op(ctx, args: Mapping[str, Any]) -> dict[str, Any]:
    followed = ctx.settings.max_keys >= 700
    return {"presets": list(presets.library(futures_followed=followed))}


@op("universes")
async def universes(ctx, args: Mapping[str, Any]) -> dict[str, Any]:
    options: list[dict[str, Any]] = []
    for kind in cat.UNIVERSES:
        if kind == "options":
            for underlying in cat.UNDERLYINGS:
                options.append(
                    {
                        "kind": kind,
                        "underlying": underlying,
                        "label": f"{underlying} option strikes",
                        "note": None,
                    }
                )
        elif kind == "index_list":
            for list_key, (name, followed) in cat.INDEX_LISTS.items():
                options.append(
                    {
                        "kind": kind,
                        "id": list_key,
                        "label": name,
                        "note": None if followed else NOT_FOLLOWED,
                    }
                )
        elif kind == "watchlist":
            continue  # the page offers the owner's own lists from its watchlist API
        else:
            options.append({"kind": kind, "label": UNIVERSE_LABELS.get(kind, kind), "note": None})
    return {"universes": options}


@op("today")
async def today(ctx, args: Mapping[str, Any]) -> dict[str, Any]:
    """The dashboard's card: each screen's day (entered, exited, matches now) and the latest
    entries and exits. Read in a few queries whatever the number of runs (release review HIGH-4):
    the counts from the runs, the changes from the membership rows, so a member only missing in
    its grace never reads as left and the day's first run never lists carried members as new."""
    now = ctx.clock()
    session = _session_key(now)
    start = epoch_ms(key_day(session), 0)
    store = _store(ctx)

    def read(tx: Tx):
        names = {r["id"]: r["name"] for r in tx.screens()}
        screens = [
            {
                "id": row["screen_id"],
                "name": names[row["screen_id"]],
                "entered": row["entered"],
                "exited": row["exited"],
                "matches": row["matched"],
            }
            for row in tx.session_totals(session)
            if row["screen_id"] in names
        ]
        recent: list[dict[str, Any]] = []
        for row in tx.memberships():
            name = names.get(row["screen_id"])
            if name is None:
                continue
            seen = {"symbol": row["symbol"], "exchange": row["exchange"], "screen": name}
            if row["first_seen_at"] >= start:
                recent.append({**seen, "kind": "entered", "at": row["first_seen_at"]})
            if row["status"] == mem.EXITED and (row["exited_at"] or 0) >= start:
                recent.append({**seen, "kind": "exited", "at": row["exited_at"]})
        recent.sort(key=lambda item: item["at"], reverse=True)
        return {
            "screens": screens,
            "recent": recent[:20],
            # Not counted yet: the screener keeps no "seen" mark per alert, so the card never shows it.
            "unseen_alerts": 0,
            "session": session,
        }

    return _translate(lambda: store.read(OWNER, read))
