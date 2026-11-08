"""One run of one screen (M9.14, M9.15, design 3.7): evaluate in slices, then record it whole.

``evaluate`` judges every member of the screen's universe at the boundary with the pure engine,
25 instruments a slice with a yield to the loop between slices, so the feed's ticks keep flowing
while a run is made (tick lag under 50 ms). It reads candles and context through a ``Source`` the
runtime provides (SV5); a test provides a fake.

``record`` writes the run in one IMMEDIATE transaction on the store thread, only while the screen
is still at the version the run was evaluated on: the ``run`` row, its changes (``in``, ``out``,
``not_checked`` per instrument), the values of its matches, the membership transitions
(membership.apply_run), the alert marks and the outbox rows for the list sync, the digest and the
push. A screen edited meanwhile gets a ``superseded`` row and nothing else changes. A screen with
no target list keeps its membership under list 0 (never synced): transitions and alerts work the
same either way.

Rewind (3.8): the session's first run of a screen records every match as ``in``; later runs record
differences, so the page replays them.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, NamedTuple, Protocol

from . import alerting as al
from . import catalog as cat
from . import digests, words
from . import membership as mem
from .engine import ENGINE_VERSION, Context, Subject, judge
from .scheduler import Due
from .store import OWNER, Tx, VersionConflict
from .types import HIT, MISS, NO_FEED, NOT_CHECKED, Outcome, Plan, Why

SLICE = 25
#: Membership rows of a screen without a target watchlist live under this list id.
NO_LIST = 0


class Member(NamedTuple):
    """One instrument of a universe, as the store's ``universe_member`` row names it."""

    key: str
    symbol: str
    exchange: str
    segment: str
    cas: bool = False


class Source(Protocol):
    """What a run reads: the runtime's live bars and stores (SV5), or a test's fake."""

    def members(self, universe: Mapping[str, Any]) -> Sequence[Member]: ...

    def subject(self, member: Member, plan: Plan, boundary: int) -> Subject | None:
        """The instrument's candles and facts at the boundary; None when no price has come yet."""
        ...

    def context(self, boundary: int, session: int) -> Context: ...

    def prices(self, member: Member) -> tuple[int | None, int | None]:
        """(last price, previous close) in paise, for the entry price and the digest."""
        ...


@dataclass(frozen=True, slots=True)
class Judged:
    member: Member
    outcome: Outcome
    #: (last price, previous close) in paise at the run.
    prices: tuple[int | None, int | None] = (None, None)


@dataclass(frozen=True, slots=True)
class Report:
    """What a recorded run came to, for the log, the push and the tests."""

    run_id: int
    run_key: str
    status: str
    matched: int = 0
    entered: int = 0
    exited: int = 0
    missing: int = 0
    not_checked: int = 0
    told: bool = False
    why: str = ""
    list_id: int = NO_LIST
    changes: tuple[mem.Change, ...] = ()
    waiting: tuple[str, ...] = ()
    marks: Mapping[str, Any] = field(default_factory=dict)


async def evaluate(
    plan: Plan,
    members: Sequence[Member],
    source: Source,
    ctx: Context,
    *,
    slice_size: int = SLICE,
    yielder: Callable[[], Any] | None = None,
) -> list[Judged]:
    """Judge every member at ``ctx.boundary``; a yield to the loop after each slice."""
    out: list[Judged] = []
    memo: dict = {}
    for start in range(0, len(members), slice_size):
        for member in members[start : start + slice_size]:
            subject = source.subject(member, plan, ctx.boundary)
            if subject is None:
                outcome = Outcome(NOT_CHECKED, (Why(NO_FEED),))
            else:
                outcome = judge(plan, subject, ctx, memo)
            out.append(Judged(member, outcome, source.prices(member)))
        if start + slice_size < len(members):
            if yielder is not None:
                yielder()
            await asyncio.sleep(0)
    return out


def _row_of(found: Mapping[str, Any]) -> mem.Row:
    fields = {name: found[name] for name in mem.Row.__dataclass_fields__ if name in found}
    return mem.Row(**fields)


def _row_dict(row: mem.Row) -> dict[str, Any]:
    return {name: getattr(row, name) for name in mem.Row.__dataclass_fields__}


def _results(judged: Iterable[Judged], rows: Mapping[str, mem.Row]) -> list[mem.Result]:
    results = []
    named = set()
    for item in judged:
        kind = item.outcome.kind
        named.add(item.member.key)
        results.append(
            mem.Result(
                item.member.key,
                item.member.symbol,
                item.member.exchange,
                kind,
                item.outcome.rank,
                item.prices[0],
            )
        )
    for key, row in rows.items():  # left the universe: a miss (membership.apply_run's rule)
        if key not in named and row.status != mem.EXITED:
            results.append(mem.Result(key, row.symbol, row.exchange, MISS))
    return results


def _changes(
    judged: Sequence[Judged], applied: mem.Applied, rows: Mapping[str, mem.Row], first_run: bool
) -> list[dict[str, Any]]:
    """The run_change rows: in, out and not_checked per instrument, ranked for the page."""
    out: dict[str, dict[str, Any]] = {}
    ranked = {
        item.member.key: index
        for index, item in enumerate(
            sorted(
                (j for j in judged if j.outcome.kind == HIT),
                key=lambda j: (j.outcome.rank is None, j.member.symbol),
            )
        )
    }
    for item in judged:
        key, kind = item.member.key, item.outcome.kind
        if kind == NOT_CHECKED:
            out[key] = _change(item.member, "not_checked", None)
        elif kind == HIT and first_run:
            out[key] = _change(item.member, "in", ranked.get(key))
    members = {item.member.key: item.member for item in judged}
    for change in applied.changes:
        row = change.row
        member = members.get(change.key) or Member(row.key, row.symbol, row.exchange, "")
        if change.event in (mem.EV_ENTERED, mem.EV_BACK):
            out[change.key] = _change(member, "in", ranked.get(change.key))
        elif change.event == mem.EV_MISSING:
            out[change.key] = _change(member, "out", None)
        elif change.event == mem.EV_EXITED:
            before = rows.get(change.key)
            if before is not None and before.status == mem.ACTIVE:
                out[change.key] = _change(member, "out", None)
    return list(out.values())


def _change(member: Member, change: str, rank: int | None) -> dict[str, Any]:
    return {
        "key": member.key,
        "symbol": member.symbol,
        "exchange": member.exchange,
        "change": change,
        "rank": rank,
    }


def record(
    tx: Tx,
    screen: Mapping[str, Any],
    state: Mapping[str, Any] | None,
    due: Due,
    judged: Sequence[Judged],
    now: int,
    *,
    repaired: bool = False,
    push: bool = True,
) -> Report:
    """Write one evaluated run inside ``tx`` (a write transaction on the store thread).

    ``screen`` and ``state`` are the rows as read in this transaction; ``due`` names the boundary
    and the version the run judged. A screen that moved on gets a superseded row only.
    """
    spec = screen["spec"]
    run_key = str(due.key)
    base = {
        "screen_id": screen["id"],
        "screen_version": due.key.version,
        "run_key": run_key,
        "session": due.key.session,
        "candle_start": due.boundary.start,
        "candle_end": due.boundary.end,
        "ran_at": now,
        "status": "done",
        "engine_version": ENGINE_VERSION,
    }
    try:
        run = tx.run_insert(base)
    except VersionConflict:
        run = tx.run_insert({**base, "status": "superseded"})
        return Report(run["id"], run_key, "superseded")
    if run["status"] != "done" or run["ran_at"] != now:  # recorded before this attempt
        return Report(run["id"], run_key, run["status"])

    target = spec.get("target")
    state = state or {}
    list_id = (state.get("list_id") if target else None) or (target or {}).get("list_id") or NO_LIST
    grace = mem.Grace.of((target or {}).get("grace"))
    max_adds = (target or {}).get("max_adds_per_run", cat.DEFAULT_MAX_ADDS)
    order = (spec.get("rank") or {}).get("order", "desc")
    rows = {r["key"]: _row_of(r) for r in tx.memberships(screen_id=screen["id"], list_id=list_id)}
    items = (
        {k: bool(v["added_by_screener"]) for k, v in tx.list_items(list_id).items()}
        if list_id != NO_LIST
        else {}
    )
    first_run = _first_of_session(tx, screen["id"], run["id"], due.key.session)
    last = due.boundary.tf == "after_close" or due.last
    applied = mem.apply_run(
        rows,
        _results(judged, rows),
        mem.Run(run["id"], due.boundary.end, due.key.session, due.key.version, last=last),
        grace,
        items=items,
        max_adds=max_adds,
        order=order,
        free=mem.free_slots(rows.values(), items) if list_id != NO_LIST else mem.LIST_MAX,
    )
    for row in applied.rows.values():
        tx.membership_put(screen["id"], list_id, _row_dict(row), now)

    changes = _changes(judged, applied, rows, first_run)
    tx.run_changes_put(run["id"], changes)
    values: dict[str, dict[str, Any]] = {}
    for j in judged:
        if j.outcome.kind == HIT:
            values[j.member.key] = {
                **dict(j.outcome.values or {}),
                "_ltp": j.prices[0],
                "_cp": j.prices[1],
                "_forming": j.outcome.forming,
            }
        elif j.outcome.kind == NOT_CHECKED:
            values[j.member.key] = {"_not_checked": words.not_checked_words(j.outcome.why)}
    tx.run_values_put(run["id"], values)

    counts = {
        "matched": sum(1 for j in judged if j.outcome.kind == HIT),
        "entered": sum(1 for c in applied.changes if c.event == mem.EV_ENTERED),
        "exited": sum(1 for c in applied.changes if c.event == mem.EV_EXITED),
        "missing": sum(1 for c in applied.changes if c.event == mem.EV_MISSING),
        "not_checked": sum(1 for j in judged if j.outcome.kind == NOT_CHECKED),
    }

    # alerts (M9.18): whether this run is worth telling
    policy = al.Policy.of(spec.get("alerts"))
    entered, exited = al.named(applied.changes)
    late_at = now if due.late else None
    facts = al.Facts(due.key.session, due.key.k, counts["matched"], entered, exited, late_at)
    told = {
        key: al.Told(row["session"], row["told"], row["last_run"])
        for key, row in tx.told(screen["id"], due.key.session).items()
    }
    decision = al.decide(policy, facts, told, paused=bool(state.get("alerts_paused")))
    marks = words.run_marks(repaired=repaired, late_at=late_at)
    marks["alerts"] = words.told_words(decision, policy)
    tx.run_finish(run["id"], marks, **counts)
    if decision.tell:
        tx.mark_told(
            screen["id"],
            due.key.session,
            {key: (item.count, item.last) for key, item in decision.told.items()},
        )
        prices = {j.member.key: j.prices for j in judged}
        payload = digests.build(
            screen_id=screen["id"],
            screen_name=screen["name"],
            run_key=run_key,
            at=due.boundary.end,
            session=due.key.session,
            decision=decision,
            prices=prices,
        )
        tx.outbox_add("digest", run_key, payload, now)
    state_fields: dict[str, Any] = {"last_run_key": run_key, "last_run_at": now}
    if decision.pause:
        state_fields["alerts_paused"] = True
    tx.state_put(screen["id"], now, **state_fields)

    # side effects for the list and the page
    if target and (applied.changes or target.get("create_name")):
        tx.outbox_add(
            "list_sync",
            f"{screen['id']}:{list_id}",
            {
                "screen_id": screen["id"],
                "list_id": list_id,
                "create_name": target.get("create_name"),
            },
            now,
            coalesce=True,
        )
    if push:
        tx.outbox_add(
            "push",
            run_key,
            {
                "screen_id": screen["id"],
                "run_id": run["id"],
                "run_key": run_key,
                "at": due.boundary.end,
                "candle": f"{words.hm(due.boundary.start)} to {words.hm(due.boundary.end)}",
                "counts": {
                    "matches": counts["matched"],
                    **{k: v for k, v in counts.items() if k != "matched"},
                },
                "entered": [
                    {"symbol": c.row.symbol, "exchange": c.row.exchange, "member_id": None}
                    for c in applied.changes
                    if c.event == mem.EV_ENTERED
                ],
                "exited": [
                    {"symbol": c.row.symbol, "exchange": c.row.exchange}
                    for c in applied.changes
                    if c.event == mem.EV_EXITED
                ],
                "marks": list(words.mark_words(marks)),
            },
            now,
        )
    return Report(
        run["id"],
        run_key,
        "done",
        **counts,
        told=decision.tell,
        why=decision.why,
        list_id=list_id,
        changes=applied.changes,
        waiting=applied.waiting,
        marks=marks,
    )


def _first_of_session(tx: Tx, screen_id: int, run_id: int, session: int) -> bool:
    """No other done run of the screen in this session (the rewind's whole-set run)."""
    for row in tx.runs_page(screen_id, run_id, 50):
        if row["session"] == session and row["status"] == "done":
            return False
        if row["session"] < session:
            break
    return True


__all__ = [
    "NO_LIST",
    "SLICE",
    "Judged",
    "Member",
    "Report",
    "Source",
    "evaluate",
    "record",
]
