"""Membership: who a screen holds in a watchlist, who is leaving, and what the list should get.

Pure, like the rest of the engine: no database, no OpenAlgo, no clock, no logging. Every input is an
argument and every decision a return value, so the service (``runs``, ``listsync``), the replay check
and the golden vectors run the same code. The runtime translates; it never decides.

One row per screen, list and instrument. Its status says where the instrument stands:

    active     matched at the last check that judged it
    missing    stopped matching, still inside the grace
    exited     the grace ran out (or it was never kept); the screen let go of it
    dismissed  the owner took it out of the list by hand while it was still a member

A run gives each instrument one outcome. ``HIT`` and ``MISS`` decide something; ``NOT_CHECKED`` and
``NOT_DUE`` change nothing at all, so a gap in the data can never empty a watchlist.

The rules, each one a decision the owner trades on:

* A match that is not a member enters (``ENTERED``) and is added to the list. At most
  ``max_adds`` new instruments enter a run, the best ranked first, ties by symbol; the rest wait for
  the next run. A full list admits nothing and says so. An instrument already in the list (the owner
  put it there) costs the list nothing, so neither limit stops it.
* A miss counts. ``runs``: the row leaves once its misses reach ``n``. ``day_end``: it leaves at the
  screen's last run of the session, or at its first judged miss on a later day. ``never``: a miss
  never takes it out; the owner does. A hit puts a missing row back (``BACK``) without an alert.
* A pinned row never leaves: it stays ``missing`` however long it does not match.
* An item the owner removed by hand is ``dismissed``. While it keeps matching it stays out of the
  list; once it has missed for the whole grace it is forgotten (``FORGOTTEN``), and the next match
  enters it again and adds it again.
* Only items the screener added are ever removed. An item the owner added by hand stays after the
  screen lets go of it. Several screens may feed one list: an item stays while any row wants it.

Prices are paise, instants epoch milliseconds and a trading day its session key (cross-design X3).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from fractions import Fraction
from typing import NamedTuple

from . import catalog as cat
from .types import HIT, MISS

ACTIVE, MISSING, EXITED, DISMISSED = "active", "missing", "exited", "dismissed"
STATUSES = (ACTIVE, MISSING, EXITED, DISMISSED)

# What happened to one instrument at one run.
EV_ENTERED = "ENTERED"  # became a member: told, added to the list
EV_STILL = "STILL"  # matched again while a member: nothing to say
EV_MISSING = "MISSING"  # stopped matching, still inside the grace
EV_BACK = "BACK"  # matched again inside the grace: not an entry, no alert
EV_EXITED = "EXITED"  # the grace ran out: left, and removed from the list if the screener added it
EV_FORGOTTEN = "FORGOTTEN"  # a dismissed instrument stopped matching: the next match is an entry

#: Items one watchlist holds (OpenAlgo's limit).
LIST_MAX = 250
#: Why matches wait: "limit" (the screen's most to add in one run) or "full" (the list has no room).
LIMIT, FULL = "limit", "full"

RUNS, DAY_END, NEVER = cat.GRACE_MODES


@dataclass(frozen=True, slots=True)
class Grace:
    """How long a miss is forgiven: ``n`` judged misses, the day, or always."""

    mode: str = RUNS
    n: int = cat.DEFAULT_GRACE_RUNS

    def __post_init__(self) -> None:
        if self.mode not in cat.GRACE_MODES or self.mode == RUNS and self.n < 1:
            raise ValueError(f"not a grace: {self.mode!r} {self.n!r}")

    @classmethod
    def of(cls, raw: Mapping | None) -> Grace:
        """From the screen's ``target.grace`` (``{"mode": "runs", "n": 2}``); None is the default."""
        if raw is None:
            return cls()
        return cls(raw["mode"], int(raw.get("n", cat.DEFAULT_GRACE_RUNS)))


@dataclass(frozen=True, slots=True)
class Run:
    """The run being applied."""

    id: int
    #: The candle end the run judged, epoch ms: what "New 10:45" and "Since 10:45" show.
    at: int
    session: int
    version: int = 1
    #: The screen's last run of the session (the only one of an after-close screen).
    last: bool = False


@dataclass(frozen=True, slots=True)
class Result:
    """What one run said about one instrument."""

    key: str
    symbol: str
    exchange: str
    outcome: str  # HIT, MISS, NOT_CHECKED or NOT_DUE
    #: The rank value of a hit (an exact number or a float), None when it could not be ranked.
    rank: object = None
    #: The price at the run's candle in paise, kept as the entry price when the hit enters.
    price: int | None = None


@dataclass(frozen=True, slots=True)
class Row:
    """One instrument's standing in one screen's list (the ``membership`` table)."""

    key: str
    symbol: str
    exchange: str
    status: str
    pinned: bool = False
    #: The screener put it in the list (or another screen of it did), not the owner.
    added_by_screener: bool = True
    #: The list held it at the last sync. False while an add is on its way.
    in_list: bool = False
    #: This stay's entry: when and in which run (a re-entry starts a new stay).
    first_seen_at: int = 0
    first_seen_run: int = 0
    last_seen_at: int = 0
    last_seen_run: int = 0
    #: Consecutive hits, and consecutive judged misses.
    hits: int = 0
    misses: int = 0
    entries: int = 1
    exited_at: int | None = None
    entry_price: int | None = None
    score: object = None
    screen_version: int = 0
    #: The session of the first miss of this lapse (0 when none): a day-end grace ends with it.
    missing_session: int = 0


@dataclass(frozen=True, slots=True)
class Change:
    """A transition worth recording: the digest, the run's counts and the page read these."""

    key: str
    event: str
    row: Row


@dataclass(frozen=True, slots=True)
class Applied:
    """What a run did to a screen's rows."""

    #: Every row the run created or touched, by key (a member that matched again moves its figures).
    rows: Mapping[str, Row]
    #: ENTERED (best ranked first), then the other transitions in the order the results came.
    changes: tuple[Change, ...]
    #: Keys that matched and could not be added, best ranked first.
    waiting: tuple[str, ...] = ()
    #: Why they wait: LIMIT or FULL, "" when none do.
    blocked: str = ""


class Pick(NamedTuple):
    """An instrument to put in the list, by the names OpenAlgo's list knows it by."""

    key: str
    symbol: str
    exchange: str


@dataclass(frozen=True, slots=True)
class Sync:
    """What to tell OpenAlgo's list: the instruments to add and the keys to remove (removed first)."""

    add: tuple[Pick, ...]
    remove: tuple[str, ...]
    #: Wanted keys left for the next sync because the list had no room.
    held: tuple[str, ...] = ()


def wants(row: Row) -> bool:
    """Whether the list should hold this row's instrument."""
    return row.status in (ACTIVE, MISSING)


# ---- one instrument at one run ------------------------------------------------------------------------
def step(
    row: Row | None,
    result: Result,
    run: Run,
    grace: Grace,
    *,
    item: bool | None = None,
) -> tuple[Row | None, str | None]:
    """(row, event) after ``result``: the row as it now stands (None if there is none) and what happened.

    A match that is not a member enters here, so the caller (``apply_run``) decides who may before
    it calls. ``item`` is the list's entry for the key: None when absent, True when the screener put
    it there, False when the owner did.
    """
    if result.outcome == HIT:
        if row is None or row.status == EXITED:
            return _enter(row, result, run, item)
        return _seen(row, result, run)
    if result.outcome == MISS and row is not None and row.status != EXITED:
        return _missed(row, run, grace)
    return row, None  # not checked, not due, or nothing to lose


def _enter(row: Row | None, result: Result, run: Run, item: bool | None) -> tuple[Row, str]:
    entered = Row(
        result.key, result.symbol, result.exchange, ACTIVE,
        added_by_screener=item is None or item,
        in_list=item is not None,
        first_seen_at=run.at, first_seen_run=run.id, last_seen_at=run.at, last_seen_run=run.id,
        hits=1, entries=1 if row is None else row.entries + 1,
        entry_price=result.price, score=result.rank, screen_version=run.version,
    )  # fmt: skip
    return entered, EV_ENTERED


def _seen(row: Row, result: Result, run: Run) -> tuple[Row, str]:
    """A hit on a member, or on one the owner removed."""
    moved = replace(
        row, hits=row.hits + 1, misses=0, missing_session=0,
        last_seen_at=run.at, last_seen_run=run.id, score=result.rank, screen_version=run.version,
    )  # fmt: skip
    if row.status == MISSING:
        return replace(moved, status=ACTIVE), EV_BACK
    return moved, EV_STILL


def _missed(row: Row, run: Run, grace: Grace) -> tuple[Row, str]:
    """A judged miss on a member, on one in its grace, or on one the owner removed."""
    misses = row.misses + 1
    since = row.missing_session or run.session
    base = replace(row, hits=0, misses=misses, missing_session=since, screen_version=run.version)
    if row.status == DISMISSED:
        if _spent(misses, since, grace, run):
            return replace(base, status=EXITED, exited_at=run.at), EV_FORGOTTEN
        return base, None  # out of the list already: nothing to show
    if not row.pinned and _spent(misses, since, grace, run):
        return replace(base, status=EXITED, exited_at=run.at), EV_EXITED
    return replace(base, status=MISSING), EV_MISSING


def _spent(misses: int, since: int, grace: Grace, run: Run) -> bool:
    """Whether the grace is used up by a miss at ``run``: ``since`` is the session it first missed."""
    if grace.mode == RUNS:
        return misses >= grace.n
    if grace.mode == DAY_END:
        return run.last or since < run.session
    return False


# ---- a whole run ---------------------------------------------------------------------------------------
def apply_run(
    rows: Mapping[str, Row],
    results: Iterable[Result],
    run: Run,
    grace: Grace,
    *,
    items: Mapping[str, bool],
    max_adds: int,
    order: str,
    free: int,
) -> Applied:
    """Apply a run's outcomes to a screen's rows.

    ``rows`` are the screen's rows for the list by key; ``items`` the list's content (key to whether
    the screener put it there); ``free`` the room left for new instruments (``free_slots``); ``order``
    the rank direction ("desc" or "asc") the screen ranks by. An instrument the results do not name
    keeps its row: the caller reports an instrument that left the screen's universe as a MISS.
    """
    touched: dict[str, Row] = {}
    changes: list[Change] = []
    fresh: list[Result] = []
    for result in results:
        row = rows.get(result.key)
        if result.outcome == HIT and (row is None or row.status == EXITED):
            fresh.append(result)
            continue
        moved, event = step(row, result, run, grace)
        if moved is not row:
            touched[result.key] = moved
        if event not in (None, EV_STILL):
            changes.append(Change(result.key, event, moved))
    entered, waiting = [], []
    room = max(0, min(max_adds, free))
    for result in sorted(fresh, key=lambda found: _by_rank(found, order)):
        item = items.get(result.key)
        if item is None and room == 0:
            waiting.append(result.key)
            continue
        if item is None:
            room -= 1
        moved, event = step(rows.get(result.key), result, run, grace, item=item)
        touched[result.key] = moved
        entered.append(Change(result.key, event, moved))
    blocked = "" if not waiting else FULL if free < max_adds else LIMIT
    return Applied(touched, (*entered, *changes), tuple(waiting), blocked)


def _by_rank(result: Result, order: str) -> tuple:
    """Best first: ranked before unranked, the direction of ``order``, then symbol and key."""
    rank = result.rank
    if rank is None or isinstance(rank, float) and not math.isfinite(rank):
        return (1, 0, result.symbol, result.key)
    exact = Fraction(rank)
    return (0, -exact if order == "desc" else exact, result.symbol, result.key)


# ---- the list ---------------------------------------------------------------------------------------------
def free_slots(rows: Iterable[Row], items: Mapping[str, bool], capacity: int = LIST_MAX) -> int:
    """Room for new instruments: the list as it stands plus the adds already on their way."""
    held = set(items) | {row.key for row in rows if wants(row)}
    return max(0, capacity - len(held))


def reconcile(rows: Iterable[Row], items: Mapping[str, bool]) -> dict[str, Row]:
    """The rows that change when the list is read: what the owner did by hand, and confirmed adds.

    ``items`` is the list as OpenAlgo holds it (key to whether the screener put it there). A row
    the screener believed was in the list and is now gone was removed by the owner (dismissed); an
    item present that no row of ours explains is the owner's and is never touched.
    """
    moved: dict[str, Row] = {}
    for row in rows:
        if row.key in items:
            now = replace(row, in_list=True, added_by_screener=items[row.key])
            if row.status == DISMISSED and not row.in_list:
                now = replace(now, status=EXITED, misses=0)  # the owner put it back by hand
        elif row.in_list and row.status in (ACTIVE, MISSING):
            now = replace(row, in_list=False, status=DISMISSED, pinned=False, misses=0, hits=0,
                          missing_session=0)  # fmt: skip
        elif row.in_list:
            now = replace(row, in_list=False)
        else:
            continue
        if now != row:
            moved[row.key] = now
    return moved


def plan_sync(rows: Iterable[Row], items: Mapping[str, bool], capacity: int = LIST_MAX) -> Sync:
    """What to add and remove so the list holds what the rows want.

    ``rows`` are every row of the list, whatever screen it belongs to: an item stays while any of
    them wants it. Only items the screener added are removed.
    """
    rows = sorted(rows, key=lambda row: (row.first_seen_at, row.key))
    wanted = {row.key for row in rows if wants(row)}
    remove = tuple(sorted(key for key, ours in items.items() if ours and key not in wanted))
    room = max(0, capacity - (len(items) - len(remove)))
    new: dict[str, Pick] = {}
    for row in rows:
        if wants(row) and row.key not in items:
            new[row.key] = Pick(row.key, row.symbol, row.exchange)
    ordered = tuple(new.values())
    return Sync(ordered[:room], remove, tuple(pick.key for pick in ordered[room:]))


# ---- what the owner can do to a row ---------------------------------------------------------------------
def pin(row: Row) -> Row | None:
    """Keep a member in the list however long it does not match. None unless it is a member."""
    return replace(row, pinned=True) if row.status in (ACTIVE, MISSING) else None


def unpin(row: Row) -> Row:
    """The grace applies to the row again, from the misses it has already got."""
    return replace(row, pinned=False)


def dismiss(row: Row) -> Row | None:
    """Take a member out of the list and keep it out while it matches. None unless it is a member."""
    if row.status not in (ACTIVE, MISSING):
        return None
    return replace(row, status=DISMISSED, pinned=False, misses=0, hits=0, missing_session=0)


def restore(row: Row) -> Row | None:
    """Let the screen add a dismissed instrument again at its next match. None unless dismissed."""
    if row.status != DISMISSED:
        return None
    return replace(row, status=EXITED, misses=0, hits=0, missing_session=0)


__all__ = [
    "ACTIVE", "DISMISSED", "EXITED", "FULL", "LIMIT", "LIST_MAX", "MISSING", "STATUSES",
    "Applied", "Change", "Grace", "Pick", "Result", "Row", "Run", "Sync",
    "apply_run", "dismiss", "free_slots", "pin", "plan_sync", "reconcile", "restore", "step",
    "unpin", "wants",
]  # fmt: skip
