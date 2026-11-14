"""What the owner shares, and the lists a member sees (M8.15, M20.6; design 2.4, 4.2, 6.2, 6.5; the
mid-point review's M3).

**Shares** (the owner's admin ops ``share_list``, ``share_set``, ``share_remove``): a row of
family.db's ``share`` is a list (``kind`` watchlist, ``ref`` OpenAlgo's list id as written by
OpenAlgo, digits only) or a part of the books (``kind`` book, ``ref`` one of the parts), shared
with one member or with a role, at a level (view or restricted), until an optional end. A change
names the version it was decided on, and every change is audited. A share with neither a member
nor a role is a share with every member (the owner's Family page offers it so). OpenAlgo's owner bridge notes
each watchlist share as it passes (review M3), and its internal route serves no list that note
does not name for the member: a share made here alone shows nothing.

**Shared lists**: a member sees the lists shared with them or with their role, unexpired, read
through OpenAlgo's internal route (``internal_client``), never from OpenAlgo's database, and kept
in memory only. They are read when a viewer opens the watchlist screen, every minute while one is
open, when a page asks for lists older than a minute, and at once after a share changes (a list's
membership is not market data: rule 7 allows the re-read). What is kept is always cut to what is
still shared, so an unshared or expired list leaves the screen even while OpenAlgo cannot be read.
Rows go to the symbol map so the socket can follow them; the phone sees each row's symbol,
exchange and name only, never an instrument key, a tick size or a lot size. A list's prices reach
the phone over the socket: a ``quotes`` message for what it follows, then its ticks.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sudarshan.tasks import cancel_and_wait

from . import audit
from .contract import BOOK_PARTS, LIMITS, ROLES, SHARE_KINDS, SHARE_LEVELS
from .errors import FamilyError, Invalid, NotFound, VersionConflict
from .listener import Response
from .portfolio_client import Refusal
from .symbol_map import listing_of

log = logging.getLogger("sudarshan.family")

#: Lists older than this are read again when a page asks, and while a watchlist screen is open.
FRESH_MS = 60_000
#: A watchlist screen that opens reads them unless they are newer than this.
OPENED_MS = 10_000
#: How often the open watchlist screens are looked at.
LOOK_S = 5.0
#: A member with no socket open, not looked at for this long, is forgotten.
FORGET_MS = 10 * FRESH_MS
MAX_ID = 2**31 - 1
MAX_MS = 2**53 - 1


# ---- the owner's share ops ---------------------------------------------------------------------
def share_of(row: Mapping[str, Any]) -> dict[str, Any]:
    """A share row as the owner's page reads it (the contract's ``Share``)."""
    return {key: row[key] for key in ("id", "kind", "ref", "member_id", "role", "level",
                                      "expires_at", "created_at", "version")}  # fmt: skip


def _whole(value: Any, low: int = 1, high: int = MAX_ID) -> bool:
    return type(value) is int and low <= value <= high


def list_ref(ref: Any) -> int | None:
    """OpenAlgo's list id a watchlist share's ``ref`` names: digits as OpenAlgo writes them (no
    sign, no leading zero, no space), within OpenAlgo's ids; None for anything else."""
    if not isinstance(ref, str) or not 0 < len(ref) <= 10 or not ref.isascii() or not ref.isdigit():
        return None
    found = int(ref)
    return found if str(found) == ref and _whole(found) else None


def _target(tx: Any, args: dict[str, Any]) -> tuple[int | None, str | None]:
    """The share's one member, one role, or (neither given) every member."""
    member_id, role = args.get("member_id"), args.get("role")
    if member_id is not None and role is not None:
        raise Invalid(field="member_id")
    if member_id is None and role is None:
        return None, None
    if member_id is not None:
        if not _whole(member_id):
            raise Invalid(field="member_id")
        found = tx.member(member_id)
        if found is None or found["status"] == "removed":
            raise NotFound("member_gone")
        return member_id, None
    if role not in ROLES:
        raise Invalid(field="role")
    return None, role


def share_set(tx: Any, args: dict[str, Any], now: int) -> dict[str, Any]:
    """Share a list or a part with one member or a role: a new share (no ``version``), or the
    share of that target changed at its ``version``."""
    kind, ref, level = args.get("kind"), args.get("ref"), args.get("level")
    until, version = args.get("expires_at"), args.get("version")
    if kind not in SHARE_KINDS:
        raise Invalid(field="kind")
    if (list_ref(ref) is None) if kind == "watchlist" else (ref not in BOOK_PARTS):
        raise Invalid(field="ref")
    if level not in SHARE_LEVELS:
        raise Invalid(field="level")
    if until is not None and not (_whole(until, 0, MAX_MS) and until > now):
        raise Invalid(field="expires_at")
    if version is not None and not _whole(version, 1, MAX_MS):
        raise VersionConflict()
    member_id, role = _target(tx, args)
    found = next((r for r in tx.rows("share", kind=kind, ref=ref)
                  if r["member_id"] == member_id and r["role"] == role), None)  # fmt: skip
    if found is None:
        if version is not None:
            raise VersionConflict()
        made = tx.insert("share", {"kind": kind, "ref": ref, "member_id": member_id,
                                   "role": role, "level": level, "expires_at": until,
                                   "created_at": now, "updated_at": now})  # fmt: skip
    else:
        if version != found["version"]:
            raise VersionConflict()
        made = found["id"]
        tx.update("share", {"id": made}, {"level": level, "expires_at": until,
                                          "updated_at": now, "version": version + 1})  # fmt: skip
    audit.record(tx, at=now, actor=audit.OWNER, kind="share_set", member_id=member_id,
                 detail={"kind": kind, "ref": ref, "role": role, "level": level,
                         "expires_at": until})  # fmt: skip
    return share_of(tx.row("share", id=made))


def share_remove(tx: Any, args: dict[str, Any], now: int) -> dict[str, Any]:
    share_id, version = args.get("id"), args.get("version")
    row = tx.row("share", id=share_id) if _whole(share_id) else None
    if row is None:
        raise NotFound()
    if not _whole(version, 1, MAX_MS) or version != row["version"]:
        raise VersionConflict()
    tx.delete("share", id=row["id"])
    audit.record(tx, at=now, actor=audit.OWNER, kind="share_removed", member_id=row["member_id"],
                 detail={"kind": row["kind"], "ref": row["ref"], "role": row["role"]})  # fmt: skip
    return {"ok": True, "words": None}


def share_list(tx: Any) -> list[dict[str, Any]]:
    """Every share, oldest first; a removed member's are left out (they serve no one)."""
    gone = {row["id"] for row in tx.rows("member", status="removed")}
    return [share_of(r) for r in tx.rows("share", "id") if r["member_id"] not in gone]


def shares_with(row: Mapping[str, Any], member: Mapping[str, Any]) -> bool:
    """Whether a share is the member's: theirs, their role's, or every member's."""
    if row["member_id"] is not None:
        return row["member_id"] == member["id"]
    return row["role"] is None or row["role"] == member["role"]


def shared_ids(tx: Any, member: Mapping[str, Any], now: int) -> list[int]:
    """The list ids shared with the member, their role or every member, unexpired, oldest share
    first."""
    ids = []
    for row in tx.rows("share", "id", kind="watchlist"):
        mine = shares_with(row, member)
        live = row["expires_at"] is None or row["expires_at"] > now
        found = list_ref(row["ref"])
        if mine and live and found is not None:
            ids.append(found)
    return list(dict.fromkeys(ids))[: LIMITS["internal_lists"]]


# ---- the shared lists ----------------------------------------------------------------------------
@dataclass
class Seen:
    """One member's lists as last read (memory only): when, the lists, the ids shared at the last
    try (read or not), and ``stale`` when a share changed since."""

    at: int
    lists: list[dict[str, Any]]
    wanted: list[int]
    stale: bool = False


class Watchlists:
    """The members' shared lists, and a ``viewer_ws.Source`` for the socket. One event loop."""

    def __init__(self, store: Any, client: Any, symbols: Any, *, clock: Any):
        self.store, self.client, self.symbols, self.clock = store, client, symbols, clock
        self.seen: dict[int, Seen] = {}
        self.dialect: Any = None  # seated by install: its sockets, and told what changed
        self.counts = {"reads": 0, "failed": 0}
        self._flights: dict[int, asyncio.Task] = {}
        self._tasks: set[asyncio.Task] = set()

    # ---- reads ----
    async def load(self, member: Mapping[str, Any]) -> Seen:
        """The member's lists read now, or the read already on its way (one at a time for each
        member). ``Refusal`` when OpenAlgo did not answer; what was kept stays, cut to what is
        still shared."""
        member_id = int(member["id"])
        task = self._flights.get(member_id)
        if task is None or task.done():
            task = asyncio.ensure_future(self._load(dict(member)))
            self._flights[member_id] = task
            task.add_done_callback(lambda done, m=member_id: self._landed(m, done))
        return await asyncio.shield(task)

    def _landed(self, member_id: int, task: asyncio.Task) -> None:
        if self._flights.get(member_id) is task:
            del self._flights[member_id]
        if not task.cancelled():
            task.exception()  # whoever waited has it; a read nobody waited for is not an error

    async def _load(self, member: dict[str, Any]) -> Seen:
        member_id, now = int(member["id"]), self.clock()
        ids = await self.store.read(lambda tx: shared_ids(tx, member, now))
        kept = self.seen.get(member_id)
        try:
            found = await self.client.lists(ids, member_id=member_id, role=member["role"]) \
                if ids else {"lists": []}  # fmt: skip
        except Refusal:
            self.counts["failed"] += 1
            if kept is not None:
                cut, kept.wanted = [lst for lst in kept.lists if lst["id"] in ids], ids
                if cut != kept.lists:
                    kept.lists = cut
                    self._settle(member)
            raise
        lists = found["lists"]
        self.symbols.put(listing for lst in lists for row in lst["rows"]
                         if (listing := listing_of(row, now)) is not None)  # fmt: skip
        self.seen[member_id] = Seen(now, lists, ids)
        self.counts["reads"] += 1
        if kept is None or kept.lists != lists:
            self._settle(member)
        return self.seen[member_id]

    def _settle(self, member: Mapping[str, Any]) -> None:
        """The member's lists changed: the socket follows what is now shared and lets go of the
        rest (``viewer_ws.Dialect.settle``)."""
        if self.dialect is not None:
            self.dialect.settle(member)

    async def fresh(self, member: Mapping[str, Any]) -> Seen:
        """The member's lists for a page: read again when older than a minute or a share changed;
        the kept ones (cut to what is still shared) when that read fails; FamilyError when none is
        kept while something is shared (an empty answer would say nothing is shared)."""
        kept = self.seen.get(int(member["id"]))
        if kept is not None and not kept.stale and self.clock() - kept.at < FRESH_MS:
            return kept
        try:
            return await self.load(member)
        except Refusal:
            kept = self.seen.get(int(member["id"]))
            if kept is not None and (kept.lists or not kept.wanted):
                return kept
            raise FamilyError("failed") from None

    async def _quietly(self, member: Mapping[str, Any]) -> None:
        try:
            await self.load(member)
        except Refusal:
            pass  # the client said it in the journal; the next look reads again

    def _spawn(self, member: Mapping[str, Any]) -> None:
        task = asyncio.ensure_future(self._quietly(member))
        self._tasks.add(task)
        task.add_done_callback(self._done)

    def _done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("the family's shared lists could not be read", exc_info=task.exception())

    def _seated(self) -> dict[int, Mapping[str, Any]]:
        """Every member with a socket open, as their socket last read them."""
        seats = self.dialect.seats.values() if self.dialect is not None else ()
        return {int(seat.member["id"]): seat.member for seat in seats}

    # ---- what starts a read ----
    def opened(self, seat: Any) -> None:
        """A socket's screen changed: the watchlist screen reads the lists unless they are new."""
        if seat.screen != "watchlist":
            return
        kept = self.seen.get(int(seat.member["id"]))
        if kept is None or kept.stale or self.clock() - kept.at >= OPENED_MS:
            self._spawn(seat.member)

    def changed(self) -> None:
        """A share was set or removed: every member's lists are read again, now for each member
        with a socket open (it may follow a list's symbols), at the next look for the rest."""
        for kept in self.seen.values():
            kept.stale = True
        for member_id, member in self._seated().items():
            if member_id in self.seen:
                self._spawn(member)

    async def run(self) -> None:
        """While a watchlist screen is open, its member's lists are read once a minute."""
        while True:
            await asyncio.sleep(LOOK_S)
            now = self.clock()
            seats = self.dialect.seats.values() if self.dialect is not None else ()
            looking = {int(s.member["id"]): s.member for s in seats if s.screen == "watchlist"}
            for member_id, member in looking.items():
                kept = self.seen.get(member_id)
                if kept is None or kept.stale or now - kept.at >= FRESH_MS:
                    self._spawn(member)
            seated = self._seated()
            for member_id in [m for m, kept in self.seen.items()
                              if m not in seated and now - kept.at >= FORGET_MS]:  # fmt: skip
                del self.seen[member_id]

    async def close(self) -> None:
        tasks = [*self._tasks, *self._flights.values()]
        self._tasks, self._flights = set(), {}
        await cancel_and_wait(*tasks)

    # ---- viewer_ws.Source ----
    def keys(self, member_id: int) -> set[str]:
        kept = self.seen.get(member_id)
        return {row["instrument_key"] for lst in (kept.lists if kept else ()) for row in lst["rows"]
                if isinstance(row.get("instrument_key"), str)}  # fmt: skip

    def known(self, member_id: int) -> bool:
        return member_id in self.seen

    async def ensure(self, member: Mapping[str, Any]) -> None:
        if int(member["id"]) not in self.seen:
            await self._quietly(member)

    def health(self) -> dict[str, int]:
        return {**self.counts, "members": len(self.seen)}

    # ---- the viewer routes ----
    async def route_watchlists(self, request: Any, args: dict[str, Any], session: Any) -> Response:
        seen = await self.fresh(session.member)
        return Response.ok([{"id": lst["id"], "name": lst["name"], "count": len(lst["rows"])}
                            for lst in seen.lists])  # fmt: skip

    async def route_watchlist(self, request: Any, args: dict[str, Any], session: Any) -> Response:
        wanted = list_ref(str(args.get("id", "")))
        if wanted is None:
            raise Invalid(field="id")
        seen = await self.fresh(session.member)
        found = next((lst for lst in seen.lists if lst["id"] == wanted), None)
        if found is None:
            raise FamilyError("not_shared")
        rows = [{"symbol": r["symbol"], "exchange": r["exchange"],
                 "name": r["name"] if isinstance(r.get("name"), str) and r["name"].strip()
                 else r["symbol"]} for r in found["rows"]]  # fmt: skip
        return Response.ok({"id": found["id"], "name": found["name"], "rows": rows,
                            "refreshed_at": seen.at})  # fmt: skip


def install(plane: Any, service: Any, internal_key: Any) -> Watchlists:
    """Seat the shared lists: the viewer routes, the owner's share ops at the admin door, the
    socket's source and screen hook, and the plane's piece ``watchlists``."""
    from .internal_client import InternalClient

    client = InternalClient(service.settings.openalgo_url, internal_key, plane.http)
    lists = Watchlists(service.store, client, plane.symbols, clock=service.clock)
    lists.dialect = plane.dialect
    plane.dialect.sources.append(lists)
    plane.dialect.on_screen.append(lists.opened)
    service.viewer._watchlists = lists.route_watchlists
    service.viewer._watchlist = lists.route_watchlist

    def changing(decide: Any) -> Any:
        async def call(args: dict[str, Any]) -> Any:
            done = await service.store.write(lambda tx: decide(tx, args, service.clock()))
            lists.changed()
            return done

        return call

    async def listed(args: dict[str, Any]) -> Any:
        return await service.store.read(share_list)

    service.doors.ops.update(share_list=listed, share_set=changing(share_set),
                             share_remove=changing(share_remove))  # fmt: skip
    plane.parts["watchlists"] = lists.run
    plane.closers.append(lists.close)
    plane.reports["watchlists"] = lists.health
    return lists
