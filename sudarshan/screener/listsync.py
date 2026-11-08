"""The target watchlist, service side (M9.17, M8.13, design 4.2): keep OpenAlgo's list in step with
the membership rows, through the bridge's internal route.

One sync of one list, driven by an outbox ``list_sync`` row after a run (runs.py coalesces them
per list, since only the newest desired set matters):

1. Read the list as OpenAlgo holds it (``watchlist_read``). A list the screen asked to create
   (``create_name``, no id yet) is made first, and the screen's rows kept under list 0 move to it.
2. Reconcile (membership.reconcile): an item the screener believed in the list and now absent was
   removed by the owner (the row is dismissed); an item present that no row explains is the owner's
   and is never touched; a dismissed instrument the owner put back by hand may enter again.
3. Plan (membership.plan_sync): add every wanted row that is not in the list, remove every item
   the screener added that no row wants, within the list's room (250).
4. Send the adds and the removes (by OpenAlgo's item ids) in sealed ``watchlist_apply`` parts,
   each under the internal routes' 16 KiB seal (``contract.INTERNAL_MAX_BYTES``); after each part,
   record what the list holds (``list_item``) and each row's ``in_list``.

A retried sync never repeats what an earlier try did (release review HIGH-5):

- The list is made once. Once a list exists for the screen (``screen_state.list_id``), every later
  sync uses it, whatever its queued row said; before that, the create carries a key of its own
  (``create_ref``), and the bridge answers the list it already made for that key instead of a new
  "Screener: X (2)". A screen deleted meanwhile gets no list.
- Every add is recorded as the screener's before it is sent, so an add that reached OpenAlgo
  without its answer is never mistaken for the owner's item on the next try (and so never left
  in the list for good). An add that exists and a remove of an item already gone are no-ops there.

The link to OpenAlgo is a protocol: the runtime's sealed HTTP client (SV5) or a test's fake. The
owner's items are known by symbol and exchange only; they count toward the room under a key of
their own and are never added, removed or recorded as the screener's.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

from . import membership as mem
from .store import OWNER, ScreenerStore, Tx

log = logging.getLogger("sudarshan.screener")

NO_LIST = 0
#: What a sync came to, for the outbox and the journal.
DONE, CREATED, LIST_GONE, NOTHING = "done", "created", "list_gone", "nothing"
#: The most one part may carry, as the JSON of its adds and removes. A sealed request to OpenAlgo's
#: internal routes is at most 16 KiB and the seal's base64 grows its JSON by a third: 8 KiB of
#: changes plus the envelope seals to about 11.5 KiB.
PART_BYTES = 8 * 1024
#: And at most this many changes a part (the bridge takes up to 300 a request).
PART_CHANGES = 120


class Link(Protocol):
    """The bridge's internal watchlist route, sealed (6.3)."""

    async def watchlist_read(self, list_id: int) -> Mapping[str, Any] | None:
        """``{"list_id", "name", "items": [{"id", "symbol", "exchange"}]}``; None when gone."""
        ...

    async def watchlist_apply(
        self,
        *,
        list_id: int | None,
        create_name: str | None,
        add: Sequence[Mapping[str, str]],
        remove: Sequence[int],
        ref: str,
    ) -> Mapping[str, Any]:
        """Make or change the list; answers it as ``watchlist_read`` does."""
        ...


@dataclass(frozen=True, slots=True)
class Synced:
    status: str
    list_id: int
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    dismissed: tuple[str, ...] = ()
    held: tuple[str, ...] = ()


def _row(found: Mapping[str, Any]) -> mem.Row:
    return mem.Row(**{n: found[n] for n in mem.Row.__dataclass_fields__ if n in found})


def owner_key(symbol: str, exchange: str) -> str:
    """The key an item of the owner's counts under while no row of ours names it."""
    return f"owner:{exchange}:{symbol}"


def create_ref(screen_id: int) -> str:
    """The key a screen's list is made under: the same for every try of that create, so the
    bridge answers a list it already made for it instead of making another."""
    return f"screen:{screen_id}:list"


def items_of(
    listing: Mapping[str, Any],
    rows: Mapping[tuple[int, str], mem.Row],
    known: Mapping[str, Mapping[str, Any]],
) -> tuple[dict[str, bool], dict[str, int]]:
    """(key -> the screener put it there, key -> OpenAlgo's item id) from the list as read.
    ``rows`` are the list's rows by (screen id, key): several screens may feed one list."""
    by_name = {(row.symbol, row.exchange): key for (_, key), row in rows.items()}
    items: dict[str, bool] = {}
    ids: dict[str, int] = {}
    for item in listing.get("items", ()):
        name = (str(item["symbol"]).upper(), str(item["exchange"]).upper())
        key = by_name.get(name) or owner_key(*name)
        ours = key in known and bool(known[key].get("added_by_screener"))
        items[key] = ours
        ids[key] = int(item["id"])
    return items, ids


def parts(
    add: Sequence[Mapping[str, str]], remove: Sequence[int]
) -> list[tuple[list[Mapping[str, str]], list[int]]]:
    """The adds and removes as parts that each seal under the internal routes' limit: removes
    first (they make room), then adds, in order."""
    out: list[tuple[list, list]] = []
    adds: list = []
    removes: list = []
    size = 0
    for kind, item in [("remove", r) for r in remove] + [("add", a) for a in add]:
        cost = len(json.dumps(item, separators=(",", ":"))) + 1
        if adds or removes:
            if size + cost > PART_BYTES or len(adds) + len(removes) >= PART_CHANGES:
                out.append((adds, removes))
                adds, removes, size = [], [], 0
        (removes if kind == "remove" else adds).append(item)
        size += cost
    if adds or removes:
        out.append((adds, removes))
    return out


async def sync(
    store: ScreenerStore,
    link: Link,
    *,
    screen_id: int,
    list_id: int,
    create_name: str | None,
    ref: str,
    now: int,
) -> Synced:
    """One sync of the list a screen targets. Reads and writes the store on its thread; one call
    to OpenAlgo to read the list and one a part to change it (a list made first is one more).
    Raises what the link raises (the outbox retries)."""
    status = DONE
    if list_id == NO_LIST:
        if not create_name:
            return Synced(NOTHING, NO_LIST)

        def known_list(tx: Tx) -> tuple[bool, int]:
            state = tx.state(screen_id) or {}
            return tx.screen(screen_id) is not None, int(state.get("list_id") or NO_LIST)

        exists, made_id = store.read(OWNER, known_list)
        if made_id != NO_LIST:  # an earlier try of this create made it: never made twice
            list_id = made_id
        elif not exists:  # the screen was deleted meanwhile: no list for it
            return Synced(NOTHING, NO_LIST)
    if list_id == NO_LIST:
        made = await link.watchlist_apply(
            list_id=None, create_name=create_name, add=(), remove=(), ref=create_ref(screen_id)
        )
        list_id = int(made["list_id"])

        def relist(tx: Tx) -> None:
            tx.membership_relist(screen_id, NO_LIST, list_id, now)
            tx.state_put(screen_id, now, list_id=list_id, list_name=made.get("name"))

        store.write(OWNER, relist)
        log.info("the screener made the watchlist %s for screen %s", list_id, screen_id)
        status = CREATED

    listing = await link.watchlist_read(list_id)
    if listing is None:
        log.warning("the watchlist %s a screen feeds is gone; its rows are kept", list_id)
        return Synced(LIST_GONE, list_id)

    def plan(tx: Tx):
        rows = {(int(r["screen_id"]), r["key"]): _row(r) for r in tx.memberships(list_id=list_id)}
        known = tx.list_items(list_id)
        items, ids = items_of(listing, rows, known)
        moved: dict[tuple[int, str], mem.Row] = {}
        for (sid, key), row in list(rows.items()):
            changed = mem.reconcile([row], items).get(key)
            if changed is not None:
                rows[(sid, key)] = moved[(sid, key)] = changed
                tx.membership_put(sid, list_id, _dict(changed), now)
        wanted = mem.plan_sync(rows.values(), items)
        # written ahead: each add is the screener's before it is sent (HIGH-5)
        tx.list_items_mark(list_id, [p.key for p in wanted.add], now)
        return rows, items, ids, moved, wanted

    rows, items, ids, moved, wanted = store.write(OWNER, plan)
    add = [{"key": p.key, "symbol": p.symbol, "exchange": p.exchange} for p in wanted.add]
    remove_ids = [ids[key] for key in wanted.remove if key in ids]
    known = {**_known(items, ids), **{p.key: {"added_by_screener": True} for p in wanted.add}}

    def settle(answer: Mapping[str, Any], pending: Sequence[str]):
        def write(tx: Tx) -> None:
            tx.state_put(screen_id, now, list_name=answer.get("name"))
            after, after_ids = items_of(answer, rows, known)
            entries = [
                {"key": key, "added_by_screener": ours, "openalgo_item_id": after_ids.get(key)}
                for key, ours in after.items()
            ] + [
                {"key": key, "added_by_screener": True, "openalgo_item_id": None}
                for key in pending
                if key not in after
            ]
            tx.list_items_replace(list_id, entries, now)
            for (sid, key), row in rows.items():
                present = key in after
                if row.in_list != present:
                    rows[(sid, key)] = replace(row, in_list=present)
                    tx.membership_put(sid, list_id, _dict(rows[(sid, key)]), now)

        store.write(OWNER, write)

    chunks = parts(add, remove_ids)
    if not chunks:
        settle(listing, ())
    for n, (part_add, part_remove) in enumerate(chunks):
        listing = await link.watchlist_apply(
            list_id=list_id, create_name=None, add=part_add, remove=part_remove, ref=ref
        )
        settle(listing, [a["key"] for later in chunks[n + 1 :] for a in later[0]])
    dismissed = tuple(sorted({k for (_, k), r in moved.items() if r.status == mem.DISMISSED}))
    return Synced(
        status, list_id, tuple(p.key for p in wanted.add), wanted.remove, dismissed, wanted.held
    )


def _known(items: Mapping[str, bool], ids: Mapping[str, int]) -> dict[str, dict[str, Any]]:
    return {
        key: {"added_by_screener": ours, "openalgo_item_id": ids.get(key)}
        for key, ours in items.items()
    }


def _dict(row: mem.Row) -> dict[str, Any]:
    return {n: getattr(row, n) for n in mem.Row.__dataclass_fields__}


__all__ = [
    "CREATED",
    "DONE",
    "LIST_GONE",
    "NOTHING",
    "NO_LIST",
    "PART_BYTES",
    "Link",
    "Synced",
    "create_ref",
    "items_of",
    "owner_key",
    "parts",
    "sync",
]
