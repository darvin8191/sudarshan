"""The ``lists`` op and what favourites resolve against (M8.5, W10). Reads only.

``lists`` answers every list the All lists sheet offers: the owner's own lists in OpenAlgo's order
with their counts, the built-in catalogue, the ranked catalogue, the flags with their counts and
the favourites resolved. ``state`` carries the favourites resolved too (``favourites_of``).

Until the owner pins anything (the favourites version has never moved and no slot exists) the
favourites are his first seven lists in OpenAlgo's order: a suggestion computed at read and never
stored as his choice. A slot that names a list that was deleted, or a preset or flag that does not
exist, is dropped at read; the delete hook and the next write remove the row itself. A ``list:`` slot also
keeps the list's ``created_at`` and is dropped when the live list's differs: OpenAlgo gives a
deleted list's id to the next list made, and the slot must not follow that other list.

Not read here, on purpose:

* Lists a screen feeds. The screener is a service of its own (``sudarshan-screener``), so its state
  is not readable in this process; the page marks them from the screener's state as it does today.
* Built-in lists are catalogued by name only (``presets()``); their counts and dates are read by
  ``lists`` alone, from the one master generation, and are null while the master is not loaded.
* Whether a ranked list is available today (the Upstox sign-in) is ``state``'s (W22).
"""

from __future__ import annotations

from flask import session
from sqlalchemy import func

from sudarshan.watchlists import contract, engine, ranked, versions
from sudarshan.watchlists import presets as catalogue
from sudarshan.watchlists import view as merge_view
from sudarshan.watchlists.ops import OpError
from sudarshan.watchlists.ops import state as state_ops
from sudarshan.watchlists.store import favourites as store
from sudarshan.watchlists.words import presets as preset_words
from sudarshan.watchlists.words import ranked as ranked_words
from utils.logging import get_logger

logger = get_logger(__name__)

FLAGS = range(1, 6)


def presets() -> dict[str, str]:
    """The built-in lists by id and name, in catalogue order (``presets/``). Names only: the
    catalogue needs no master, so ``state`` and the favourites read it without building one."""
    return {e.id: e.name for e in catalogue.CATALOGUE}


def preset_figures() -> dict[str, tuple[int | None, str | None]]:
    """``{id: (symbols now, "As of 9 Nov 2026")}`` for each built-in list; ``(None, None)`` while
    the master is not loaded (nothing is called empty that is only not known)."""
    from sudarshan.watchlists.refs import preset as reader

    out: dict[str, tuple[int | None, str | None]] = {}
    for e in catalogue.CATALOGUE:
        try:
            built, as_of = reader.built_now(e)
        except OpError:  # no master today: not known
            out[e.id] = (None, None)
            continue
        except Exception:  # the sheet still answers; a count is a convenience
            logger.exception("Watchlists: a built-in list could not be counted for the sheet")
            out[e.id] = (None, None)
            continue
        when = preset_words.date_words(as_of)
        out[e.id] = (len(built.rows), f"As of {when}" if when else None)
    return out


def own_lists(user: str) -> list[tuple[int, str, str, int]]:
    """``(id, name, created_at, symbols)`` of each of the user's OpenAlgo lists, in its order."""
    from database import watchlist_db as oa

    try:
        item = oa.WatchlistItem
        rows = (oa.db_session.query(oa.Watchlist.id, oa.Watchlist.name, oa.Watchlist.created_at,
                                    func.count(item.id))
                .outerjoin(item, item.watchlist_id == oa.Watchlist.id)
                .filter(oa.Watchlist.user_id == user).group_by(oa.Watchlist.id)
                .order_by(oa.Watchlist.position, oa.Watchlist.id).all())  # fmt: skip
        return [(r[0], r[1], merge_view.created_key(r[2]), r[3]) for r in rows]
    finally:
        oa.db_session.remove()  # a read: the connection goes back at once


def exists(ref: str, user: str) -> bool | None:
    """Whether ``ref`` names something that exists now; None when it cannot name anything at all (not
    a ref of the contract, a ranking that is not offered, a flag that is not one of the five)."""
    kind, _, ident = ref.partition(":")
    if not ident or kind not in contract.ENUMS["RefKind"]:
        return None
    if kind == "list":
        from sudarshan.watchlists.refs import list as listref

        if not ident.isascii() or not ident.isdigit() or not 0 < int(ident) <= listref.MAX_ID:
            return None
        return listref.read_live(user, int(ident)) is not None
    if kind == "preset":
        return ident in presets()
    if kind == "ranked":
        return True if ranked.parse_ref(ref) is not None else None  # offered, or not a ranking
    return True if ident in {str(n) for n in FLAGS} else None  # the five flags always exist


def created_of(lid: int, user: str) -> str | None:
    """The live list's ``created_at`` as the guards store it; None when there is no such list."""
    from sudarshan.watchlists.refs import list as listref

    live = listref.read_live(user, lid)
    return None if live is None else live["created_at"]


def named(ref: str, found: dict) -> tuple[str, str, int | None] | None:
    """``(kind, name, count)`` of a ref that exists now, else None. ``found`` is ``gather``'s."""
    kind, _, ident = ref.partition(":")
    if kind == "list":
        own = found["own"].get(int(ident)) if ident.isascii() and ident.isdigit() else None
        return None if own is None else ("list", own[0], own[1])
    if kind == "preset":
        return ("preset", found["presets"][ident], None) if ident in found["presets"] else None
    if kind == "ranked":
        parts = ranked.parse_ref(ref)
        return None if parts is None else ("ranked", ranked_words.label(*parts), None)
    if kind == "flag" and ident in {str(n) for n in FLAGS}:
        return ("flag", found["flags"][int(ident)], found["flag_counts"].get(int(ident), 0))
    return None


def resolve(slots: list[tuple[int, str, str | None]], version: int, found: dict) -> list[dict]:
    """The ``Favourite`` records: each slot that still names something, else the suggestion when
    the owner has never pinned anything."""
    if not slots and version == 0:  # a suggestion, never stored
        slots = [(n, f"list:{lid}", found["created"][lid]) for n, lid in enumerate(found["own"], 1)
                 if n <= contract.LIMITS["favourite_slots"]]  # fmt: skip
    out = []
    for slot, ref, made in slots:
        if ref.startswith("list:") and found["created"].get(int(ref[5:])) != made:
            continue  # a deleted list's id handed to another list (or no identity stored)
        got = named(ref, found)
        if got is not None:
            out.append({"slot": slot, "ref": ref, "kind": got[0], "name": got[1], "count": got[2]})
    return out


def gather(user: str) -> dict:
    """Everything a list or a favourite is named from, read once: own lists in OpenAlgo's order
    (``{id: (name, count)}``, a dict keeps the order), presets, flag names and counts, the slots
    and the favourites version."""
    live = own_lists(user)
    with engine.open_session() as s:
        follow = store.follow_counts(s)
        names = store.flag_names(s)
        found = {
            "slots": store.slots(s), "version": store.version(s),
            "flag_counts": store.flag_counts(s), "presets": presets(),
            "flags": {n: names.get(n, state_ops.DEFAULT_FLAG_NAMES[n - 1]) for n in FLAGS},
        }  # fmt: skip
    found["own"] = {
        lid: (name, count + (follow[lid][1] if lid in follow and follow[lid][0] == made else 0))
        for lid, name, made, count in live
    }
    found["symbols"] = {lid: count for lid, _, _, count in live}
    found["created"] = {lid: made for lid, _, made, _ in live}
    return found


def favourites_of(user: str) -> list[dict]:
    found = gather(user)
    return resolve(found["slots"], found["version"], found)


def lists(args: dict) -> dict:
    found = gather(session.get("user") or "")
    room = contract.LIMITS["list_items"]
    figures = preset_figures()
    own = [
        {"ref": f"list:{lid}", "kind": "list", "name": name, "count": count,
         "room": max(0, room - found["symbols"][lid]), "locked": False, "note": None}
        for lid, (name, count) in found["own"].items()
    ]  # fmt: skip
    return {
        "version": versions.read(),
        "own": own,
        "presets": [
            {
                "ref": f"preset:{i}",
                "kind": "preset",
                "name": name,
                "count": figures[i][0],
                "room": None,
                "locked": True,
                "note": figures[i][1],
            }
            for i, name in found["presets"].items()
        ],  # fmt: skip
        "ranked": [
            {
                "ref": e.ref,
                "kind": "ranked",
                "name": e.label,
                "count": None,
                "room": None,
                "locked": True,
                "note": None,
            }
            for e in ranked.catalogue()
        ],  # fmt: skip
        "flags": [
            {
                "ref": f"flag:{n}",
                "kind": "flag",
                "name": name,
                "count": found["flag_counts"].get(n, 0),
                "room": None,
                "locked": True,
                "note": None,
            }
            for n, name in found["flags"].items()
        ],  # fmt: skip
        "favourites": resolve(found["slots"], found["version"], found),
    }


HANDLERS = {"lists": lists}
