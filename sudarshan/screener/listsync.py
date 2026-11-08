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
4. Send one sealed ``watchlist_apply`` with the adds, the removes (by OpenAlgo's item ids) and the
   run key; an add that exists and a remove of an item already gone are no-ops there, so a retry
   is harmless. Then record what the list holds (``list_item``) and each row's ``in_list``.

The link to OpenAlgo is a protocol: the runtime's sealed HTTP client (SV5) or a test's fake. The
owner's items are known by symbol and exchange only; they count toward the room under a key of
their own and are never added, removed or recorded as the screener's.
"""

from __future__ import annotations

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
    """One sync of the list a screen targets. Reads and writes the store on its thread; one or
    two calls to OpenAlgo. Raises what the link raises (the outbox retries)."""
    if list_id == NO_LIST:
        if not create_name:
            return Synced(NOTHING, NO_LIST)
        made = await link.watchlist_apply(
            list_id=None, create_name=create_name, add=(), remove=(), ref=ref
        )
        list_id = int(made["list_id"])

        def relist(tx: Tx) -> None:
            tx.membership_relist(screen_id, NO_LIST, list_id, now)
            tx.state_put(screen_id, now, list_id=list_id, list_name=made.get("name"))

        store.write(OWNER, relist)
        log.info("the screener made the watchlist %s for screen %s", list_id, screen_id)
        status = CREATED
    else:
        status = DONE

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
        return rows, items, ids, moved, wanted

    rows, items, ids, moved, wanted = store.write(OWNER, plan)
    add = [{"key": p.key, "symbol": p.symbol, "exchange": p.exchange} for p in wanted.add]
    remove_ids = [ids[key] for key in wanted.remove if key in ids]
    if add or remove_ids:
        listing = await link.watchlist_apply(
            list_id=list_id, create_name=None, add=add, remove=remove_ids, ref=ref
        )

    def settle(tx: Tx) -> None:
        tx.state_put(screen_id, now, list_name=listing.get("name"))
        after, after_ids = items_of(
            listing,
            rows,
            {**_known(items, ids), **{p.key: {"added_by_screener": True} for p in wanted.add}},
        )
        tx.list_items_replace(
            list_id,
            [
                {"key": key, "added_by_screener": ours, "openalgo_item_id": after_ids.get(key)}
                for key, ours in after.items()
            ],
            now,
        )
        for (sid, key), row in rows.items():
            present = key in after
            if row.in_list != present:
                tx.membership_put(sid, list_id, _dict(replace(row, in_list=present)), now)

    store.write(OWNER, settle)
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
    "Link",
    "Synced",
    "items_of",
    "owner_key",
    "sync",
]
