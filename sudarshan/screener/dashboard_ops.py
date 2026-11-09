"""The dashboard's reads (M9.16, dashboards design 3.4 to 3.8): what the Dashboard view asks the service.

``dashboard`` answers the whole layout with a card for each widget; ``widget`` answers one card,
which is what the page reads after a screen runs, so a row moves once, from its own card. A widget
is addressed by its screen and its kind, never by an id, so a suggested widget (the owner has not
changed anything yet) is addressed exactly as a stored one.

The card itself is ``dashboard.py``'s (pure): this module reads the store, once, in one read
transaction on the store thread, and hands the facts over. The ``dashboard`` read works cards out
in position order against ``CARD_BUDGET_S`` (1.5 s, on an injectable clock: ``ctx.monotonic``);
a card past the budget answers ``rows_pending`` with its identity, its screen's name and its
state and no rows, and the page reads it with ``widget``. The bridge waits 3 s for a read.
"""

from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from . import dashboard as dash
from . import presets, scheduler, spec, words
from .contract import WORDS
from .operand import SpecError
from .ops import ApiError, op
from .screen_ops import (
    _clauses,
    _int,
    _moved,
    _session_key,
    _sessions,
    _spec,
    _store,
    _summary,
    _within_limit,
    engine_of,
    next_check,
    result_rows,
    run_row,
    sched_screen,
)
from .store import (
    OWNER,
    WIDGET_KINDS,
    WIDGET_LIMIT,
    NotFound,
    StoreError,
    Tx,
    VersionConflict,
    WidgetRefused,
)


def translate(call, gone: str = words.DASH_GONE):
    """A store call; its refusals become the contract's codes, in the dashboard's words."""
    try:
        return call()
    except NotFound:
        raise ApiError.of("not_found", gone) from None
    except VersionConflict:
        raise ApiError.of("version_conflict", words.DASH_CONFLICT) from None
    except WidgetRefused as exc:
        raise ApiError.of("refused", str(exc)) from None
    except StoreError as exc:
        raise ApiError.of("unavailable", str(exc)) from None


def _address(args: Mapping[str, Any]) -> tuple[int, str]:
    screen_id, kind = _int(args, "id"), args.get("kind")  # the route's id is the screen
    if kind not in WIDGET_KINDS:
        raise ApiError.of("bad_request", WORDS["unreadable"])
    return screen_id, kind  # type: ignore[return-value]


def _facts(screen: Mapping[str, Any]) -> tuple[dict, dict, list[str]]:
    """(stored screen, the candles its plan needs, its clause ids)."""
    try:
        stored, plan = spec.build(screen["spec"])
    except SpecError:
        stored, plan = screen["spec"], None
    return stored, dict(plan.need) if plan else {}, [c["id"] for c in _clauses(stored.get("where"))]


def _list_of(tx: Tx, screen: Mapping[str, Any], stored: Mapping[str, Any]):
    state = tx.state(screen["id"]) or {}
    target = stored.get("target") or {}
    list_id = state.get("list_id") or target.get("list_id") or 0
    name = state.get("list_name") or (target.get("create_name") if not list_id else None)
    return list_id, name, tx.memberships(screen_id=screen["id"], list_id=list_id)


def _card(
    ctx,
    tx: Tx,
    widget: Mapping[str, Any],
    screen: Mapping[str, Any],
    *,
    now: int,
    sessions,
    session_runs: Mapping[int, list],
    pending: bool,
) -> dict[str, Any]:
    """One card without its line: the widget, its screen's name and state, and (unless pending)
    the latest check's rows or the day's counts."""
    stored, need, clause_ids = _facts(screen)
    only_day = dash.day_only(need)
    mode, mode_words = dash.mode_of(stored["run"], only_day)
    sort_by = dash.usable_sort(widget["sort_by"], clause_ids)
    chosen = dash.usable_clauses(widget["value_clauses"], clause_ids)
    active = screen["status"] == "active"
    card: dict[str, Any] = {
        "screen_id": screen["id"],
        "kind": widget["kind"],
        "position": widget["position"],
        "rows": widget["rows"],
        "sort_by": sort_by,
        "value_clauses": chosen,
        "name": screen["name"],
        "status": screen["status"],
        "status_reason": screen.get("status_reason"),
        "where": presets.where(stored),
        "mode": mode,
        "mode_words": mode_words,
        "day_only": only_day,
        "waits": dash.waits_of(need),
        # today's next check, else the next trading day's, as the engine line words it
        "next_at": next_check(ctx, now, [sched_screen(screen)], sessions) if active else None,
        "rows_pending": pending,
        "run": None,
        "matches": None,
        "rows_shown": [],
        "more": 0,
        "not_checked": None,
        "list": None,
    }
    if pending:
        return card
    latest = tx.latest_run(screen["id"])
    list_id, list_name, members = _list_of(tx, screen, stored)
    card["run"] = run_row(latest)
    card["matches"] = latest["matched"] if latest else 0
    card["list"] = {
        "id": list_id or None,
        "name": list_name,
        "count": sum(1 for m in members if m["in_list"]),
    }
    if widget["kind"] == "count":
        done = [r for r in session_runs.get(screen["id"], ()) if r["status"] == "done"]
        series = dash.thin_series(dash.newest_per_boundary(done))
        card["not_checked"] = {"count": latest["not_checked"] if latest else 0, "reasons": []}
        card["counts"] = {
            "entered_today": sum(r["entered"] for r in done),
            "exited_today": sum(r["exited"] for r in done),
            "series": [{"at": at, "matches": matches} for at, matches in series],
            "series_words": words.series_words(series),
        }
        return card
    values = tx.run_values(latest["id"]) if latest else {}
    changes = tx.run_changes(latest["id"]) if latest else []
    rows, card["not_checked"] = result_rows(
        stored, screen["name"], latest, changes, values, members, now, chosen
    )
    entered = {m["key"]: m["first_seen_at"] for m in members}
    rows = dash.sort_rows(rows, sort_by, entered=entered, found=values)
    card["rows_shown"] = rows[: widget["rows"]]
    card["more"] = max(0, len(rows) - widget["rows"])
    return card


def _with_line(card: dict[str, Any], engine: words.Engine, now: int) -> dict[str, Any]:
    """The card's line. The engine's facts are the whole screener's; the next check in its sentence
    is this card's own screen's (``next_at``), not the earliest of every Active screen."""
    run, state = card["run"], words.engine_state(engine)
    own = words.engine_words(replace(engine, next_at=card["next_at"]))
    card["line"] = dash.line(
        status=card["status"],
        reason=card["status_reason"],
        mode=card["mode"],
        waits=card["waits"],
        engine_state=state,
        engine_line=own,
        run_at=run["at"] if run else None,
        now=now,
        next_at=card["next_at"],
        count=card["kind"] == "count",
    )
    # LOW-B8: the engine line's own sentence (token refused, no token, feed stopped, not answering)
    # whenever the engine is not real-time, so a card that is not live says why
    card["not_live_words"] = None if state == words.REAL_TIME else own
    return card


def _read(ctx, pick):
    """One read transaction: ``pick(tx, layout, screens, make)`` chooses the widgets and makes
    their cards; the engine line is worked out after, outside the store thread."""
    now, store = ctx.clock(), _store(ctx)
    sessions, session = _sessions(ctx, now), _session_key(now)

    def read(tx: Tx):
        layout = tx.layout()
        screens = {r["id"]: r for r in tx.screens()}
        day: dict[int, list] = defaultdict(list)
        if any(w["kind"] == "count" for w in layout["widgets"]):
            for run in tx.runs_session(session):
                day[run["screen_id"]].append(run)

        def make(widget, *, pending=False):
            screen = screens.get(widget["screen_id"])
            if screen is None:
                raise NotFound()
            return _card(
                ctx,
                tx,
                widget,
                screen,
                now=now,
                sessions=sessions,
                session_runs=day,
                pending=pending,
            )

        out = pick(tx, layout, make)
        active = [sched_screen(r) for r in screens.values() if r["status"] == "active"]
        newest = tx.runs_page_all(1)
        return layout, out, active, (newest[0]["id"] if newest else 0)

    layout, out, active, last_run_id = translate(lambda: store.read(OWNER, read))
    return now, layout, out, engine_of(ctx, now, active, sessions), last_run_id


@op("dashboard")
async def dashboard(ctx, args: Mapping[str, Any]) -> dict[str, Any]:
    clock = getattr(ctx, "monotonic", None) or time.monotonic

    def pick(tx, layout, make):
        started, cards = clock(), []
        for widget in layout["widgets"]:
            late = bool(cards) and clock() - started >= dash.CARD_BUDGET_S
            try:
                cards.append(make(widget, pending=late))
            except NotFound:  # a widget whose screen has gone: the next write drops it
                continue
        return cards

    now, layout, cards, engine, last_run_id = _read(ctx, pick)
    return {
        "version": layout["version"],
        "name": layout["name"],
        "suggested": layout["suggested"],
        "widgets": [_with_line(card, engine, now) for card in cards],
        "limits": {"widgets": WIDGET_LIMIT},
        "last_run_id": last_run_id,
        "server_time": now,
    }


@op("widget")
async def widget(ctx, args: Mapping[str, Any]) -> dict[str, Any]:
    screen_id, kind = _address(args)

    def pick(tx, layout, make):
        for found in layout["widgets"]:
            if (found["screen_id"], found["kind"]) == (screen_id, kind):
                return make(found)
        # only the widget is gone while its screen is still there: the layout moved, read it again
        raise VersionConflict() if tx.screen(screen_id) else NotFound()

    now, _layout, card, engine, _last = _read(ctx, pick)
    return _with_line(card, engine, now)


# ---- the writes: every one names the dashboard version it was decided on --------------------------------
def _checked(raw: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return dash.check_fields(raw)
    except dash.WidgetError as exc:
        raise ApiError.of("refused", str(exc)) from None


def _written(ctx, call, **extra) -> dict[str, Any]:
    """A write in the dashboard's words; the answer is the version the dashboard is at now."""
    try:
        row = translate(call)
    except ValueError:  # the store's own check: a field the owner could not have sent
        raise ApiError.of("bad_request", WORDS["unreadable"]) from None
    return {"version": row["version"], **extra}


def _start(
    ctx, tx: Tx, screen: Mapping[str, Any], now: int
) -> tuple[Mapping[str, Any], str | None]:
    """Start a Paused screen that was just added, unless that would make more screens check through
    the day than the limit: then it stays Paused and the owner is told."""
    if screen["status"] != "paused":
        return screen, None
    if screen["spec"]["run"]["on"] == "close":
        busy = sum(
            1
            for r in tx.screens()
            if r["id"] != screen["id"]
            and r["status"] == "active"
            and r["spec"]["run"]["on"] == "close"
        )
        if busy >= ctx.settings.max_active:
            return screen, words.paused_add_words(busy, ctx.settings.max_active)
    return tx.screen_change(screen["id"], screen["version"], {"status": "active"}, now), None


def _when(ctx, tx: Tx, screen: Mapping[str, Any], choice: str, now: int) -> Mapping[str, Any]:
    """Change how a day-only screen is checked: an edit of the screen, through the checker."""
    stored, need, _ids = _facts(screen)
    if not dash.day_only(need):
        raise ApiError.of("refused", words.DASH_WHEN_DAY_ONLY, "run")
    run = dash.WHEN_RUNS[choice]
    if choice == "during_day" and stored["run"]["on"] == "close":
        return screen  # already checked at its closes
    if stored["run"] == run:
        return screen
    if screen["status"] == "active" and run["on"] == "close":
        _within_limit(ctx, tx, screen["id"])  # it would start checking through the day
    edited = _spec({**stored, "run": run})[0]
    return tx.screen_change(
        screen["id"], screen["version"], {"spec": edited, "name": edited["name"]}, now
    )


@op("widget_add")
async def widget_add(ctx, args: Mapping[str, Any]) -> dict[str, Any]:
    version, store, now = _int(args, "version"), _store(ctx), ctx.clock()
    screen_id, key = args.get("screen_id"), args.get("preset")
    if (screen_id is None) == (key is None):
        raise ApiError.of("bad_request", words.DASH_PICK)
    if screen_id is not None:
        screen_id = _int(args, "screen_id")
    fields = _checked(
        {
            "kind": "table",
            "rows": 10,
            **{k: args[k] for k in ("kind", "rows", "sort_by", "value_clauses") if k in args},
        }
    )
    choice, start = args.get("when"), args.get("start", True)
    if choice is not None and choice not in dash.WHEN_RUNS:
        raise ApiError.of("bad_request", words.DASH_WHEN)
    if start.__class__ is not bool:
        raise ApiError.of("bad_request", WORDS["unreadable"])
    made = None
    if key is not None:
        try:
            item = presets.preset(key)
            made = presets.use(key, futures_followed=ctx.settings.max_keys >= 700)
        except presets.Unavailable as exc:
            raise ApiError.of("refused", exc.message) from None
        if item.waits:
            raise ApiError.of("refused", item.waits)

    def add(tx: Tx):
        screen = (
            tx.screen_insert(made["name"], made["spec"], made["status"], now)
            if made
            else tx.screen(screen_id)
        )
        if screen is None:
            raise NotFound(WORDS["gone"])
        if choice is not None:
            screen = _when(ctx, tx, screen, choice, now)
        # the widget first: at version 0 the layout is worked out from the Active screens, and a
        # screen started before it would already be on it
        row = tx.widget_insert(version, {"screen_id": screen["id"], **fields}, now)
        notice = None
        if start:
            screen, notice = _start(ctx, tx, screen, now)
        return row, _summary(ctx, tx, screen, now), notice

    try:
        row, summary, notice = translate(lambda: store.write(OWNER, add), WORDS["gone"])
    except ValueError:
        raise ApiError.of("bad_request", WORDS["unreadable"]) from None
    _moved(ctx)
    return {"version": row["version"], "kind": fields["kind"], "screen": summary, "notice": notice}


def _kept(tx: Tx, screen_id: int, call):
    """A write on one widget. When only the widget is missing and its screen is still there, the
    layout moved under the page: a VersionConflict, never "that screen is no longer there"."""
    try:
        return call()
    except NotFound:
        if tx.screen(screen_id) is not None:
            raise VersionConflict() from None
        raise


def _widget_args(args: Mapping[str, Any]) -> tuple[int, int, str]:
    screen_id, kind = _address(args)
    return _int(args, "version"), screen_id, kind


@op("widget_update")
async def widget_update(ctx, args: Mapping[str, Any]) -> dict[str, Any]:
    version, screen_id, kind = _widget_args(args)
    named = args.get("set")
    if not isinstance(named, dict) or not named:
        raise ApiError.of("bad_request", words.DASH_NOTHING)
    if set(named) - {"kind", "rows", "sort_by", "value_clauses"}:
        raise ApiError.of("bad_request", WORDS["unreadable"])
    fields, store, now = _checked(named), _store(ctx), ctx.clock()
    return _written(
        ctx,
        lambda: store.write(
            OWNER,
            lambda tx: _kept(
                tx, screen_id, lambda: tx.widget_change(version, screen_id, kind, fields, now)
            ),
        ),
    )


@op("widget_remove")
async def widget_remove(ctx, args: Mapping[str, Any]) -> dict[str, Any]:
    version, screen_id, kind = _widget_args(args)
    store, now = _store(ctx), ctx.clock()
    return _written(
        ctx,
        lambda: store.write(
            OWNER,
            lambda tx: _kept(
                tx, screen_id, lambda: tx.widget_delete(version, screen_id, kind, now)
            ),
        ),
        notice=words.DASH_REMOVED,
    )


@op("widget_order")
async def widget_order(ctx, args: Mapping[str, Any]) -> dict[str, Any]:
    version, store, now = _int(args, "version"), _store(ctx), ctx.clock()
    try:
        order = dash.check_order(args.get("keys"))
    except dash.WidgetError as exc:
        raise ApiError.of("bad_request", str(exc)) from None
    return _written(
        ctx, lambda: store.write(OWNER, lambda tx: tx.widgets_order(version, order, now))
    )
