"""The ``copy`` op, with ``move`` as its flag (M8.4, W8): symbols or one whole section of an own
list, into another own list or a new one; a move then removes them from the source.

Only own lists (``list:<id>``) can be the source for now: a built-in (W12) or ranked (W22) source
is ``invalid`` with field ``from``. Every refusal that can be known is made, with its numbers,
before anything is written: the version (when a section is to be set), the 250 symbols a list
holds, the 50 lists there can be, a name taken.

Two files, never one transaction. OpenAlgo's own committed functions add the symbols (a new list
in one call), one commit each with a yield to the hub between them; then the target's section rows
are written in one overlay transaction that claims the target's version; then, for a move, OpenAlgo
removes the symbols from the source and the overlay drops their section rows. A failure between
the steps leaves a valid state (symbols OpenAlgo has and the overlay does not know read as No
section; a section row for a symbol the source no longer has is ignored) and the op says what was
done. A symbol already in the target is not added twice and is not an error; a move still takes it
out of the source. The target's version is needed only to set a section; copying into No section
or a new list writes no overlay row and claims nothing.
"""

from __future__ import annotations

import time

from flask import session

from sudarshan.watchlists import contract, engine, refs
from sudarshan.watchlists.ops import OpError, bumps
from sudarshan.watchlists.ops.sections import _held
from sudarshan.watchlists.store import items as store_items
from sudarshan.watchlists.store import sections as store
from sudarshan.watchlists.words import arrange as words
from sudarshan.watchlists.words import sections as section_words
from sudarshan.watchlists.words import view as view_words
from utils.logging import get_logger

logger = get_logger(__name__)
NAME_MAX = 64  # the width of OpenAlgo's list name


class Done(dict):
    """The ``CopyDone`` record, with the refs it changed for ``ops.bumps``."""

    refs: tuple = ()


def _user() -> str:
    return session.get("user") or ""


def add_all(user: str, list_id: int, keys: list) -> int:
    """Add each ``(exchange, symbol)`` through OpenAlgo's own function, one commit each, yielding
    to the hub between them (SQLite's file I/O does not). Stops at the first that fails; the count
    added so far."""
    from database import watchlist_db as oa

    done = 0
    try:
        for exchange, symbol in keys:
            if oa.add_item(user, list_id, symbol, exchange) is None:
                break
            done += 1
            time.sleep(0)
    finally:
        oa.db_session.remove()
    return done


def remove_all(user: str, list_id: int, item_ids: list) -> int:
    """Remove each item through OpenAlgo's own function; the count removed."""
    from database import watchlist_db as oa

    done = 0
    try:
        for item_id in item_ids:
            if oa.remove_item(user, list_id, item_id):
                done += 1
            time.sleep(0)
    finally:
        oa.db_session.remove()
    return done


def _source(spec: dict) -> tuple[int, dict, list, bool]:
    """``(list id, live list, picked keys (a pick in the order given, a section in the list's order), picked by section)``."""
    from sudarshan.watchlists.refs import list as listref

    parts = refs.split(spec["ref"])
    if parts is None or parts[0] != listref.KIND:
        raise OpError("invalid", field="from")
    try:
        lid = listref.list_id(parts[1])
    except OpError:
        raise OpError("invalid", field="from") from None
    live = listref.read_live(_user(), lid)
    if live is None:
        raise OpError("gone", view_words.LIST_GONE)
    flat = sorted(live["items"], key=lambda i: (i[3], i[0]))
    has = {(e, y) for _, e, y, _ in flat}
    if ("keys" in spec) == ("section_id" in spec):
        raise OpError(
            "invalid", words.PICK_ONE_WAY if "keys" in spec else words.PICK_NOTHING, "from"
        )
    if "keys" in spec:
        want = []
        for text in spec["keys"]:
            exchange, _, symbol = text.partition(":")
            if (exchange, symbol) not in has:
                raise OpError("invalid", field="from")
            want.append((exchange, symbol))
        return lid, live, list(dict.fromkeys(want)), False
    overlay = listref.read_overlay(lid, live["items"])
    trusted = overlay.get("oa_created_at") in (None, live["created_at"])
    known = {s["id"] for s in overlay.get("sections", ())} if trusted else set()
    if spec["section_id"] not in known:
        raise OpError("gone", section_words.SECTION_GONE)
    placed = overlay["items"]
    return (
        lid,
        live,
        [(e, y) for _, e, y, _ in flat if placed.get((e, y)) == spec["section_id"]],
        True,
    )


def _target(to: dict, picked: list, from_section: bool, src: int) -> tuple[dict | None, list]:
    """``(live target or None for a new list, the keys it does not hold yet)``, refused with the
    numbers when it cannot take them."""
    from database import watchlist_db as oa
    from sudarshan.watchlists.refs import list as listref

    if ("list" in to) == ("name" in to):
        raise OpError("invalid", field="to")
    if "name" in to:
        name = to["name"].strip()
        if not name or len(name) > NAME_MAX:
            raise OpError("invalid", words.NAME_EMPTY if not name else words.NAME_LONG, "name")
        if to.get("section_id") is not None:
            raise OpError("invalid", words.NEW_LIST_SECTION, "to")
        try:
            have = oa.get_watchlists(_user())
        finally:
            oa.db_session.remove()
        if len(have) >= contract.LIMITS["lists"]:
            raise OpError("limit", words.LISTS_FULL)
        if any(w["name"] == name for w in have):
            raise OpError("invalid", words.name_taken(name), "name")
        if len(picked) > contract.LIMITS["list_items"]:
            raise OpError("limit", words.too_many(len(picked)))
        return None, picked
    if to["list"] == src:
        raise OpError("invalid", words.SAME_LIST, "to")
    live = listref.read_live(_user(), to["list"]) if 0 < to["list"] <= listref.MAX_ID else None
    if live is None:
        raise OpError("gone", view_words.LIST_GONE)
    holds = {(e, y) for _, e, y, _ in live["items"]}
    new = [k for k in picked if k not in holds]
    left = contract.LIMITS["list_items"] - len(holds)
    if len(new) > left:
        raise OpError("limit", words.room(live["name"], left, len(picked), from_section))
    return live, new


def _check_section(live: dict, to: dict, args: dict) -> int | None:
    """The held version when a section is to be set; refuses a stale one before any write."""
    if to.get("section_id") is None:
        return None
    if "version" not in args:
        raise OpError("invalid", words.NEED_VERSION, "version")
    held = _held(args["version"])
    with engine.open_session() as s:
        if not store_items.current(s, live["id"], live["created_at"], held):
            raise OpError("conflict", section_words.CONFLICT)
        found = store.find(s, to["section_id"])
        if found is None or found.list_id != live["id"]:
            raise OpError("gone", section_words.SECTION_GONE)
    return held


def _answer(list_id: int, added: int, skipped: int, version, *changed: int) -> Done:
    done = Done(list=list_id, added=added, skipped=skipped, version=version, words=None)
    done.refs = tuple(f"list:{n}" for n in changed)
    return done


@bumps("items", lambda args, data: list(data.refs))
def copy(args: dict) -> dict:
    user, move = _user(), bool(args.get("move", False))
    src, source, picked, from_section = _source(args["from"])
    to = args["to"]
    target, new = _target(to, picked, from_section, src)
    held = _check_section(target, to, args) if target is not None else None
    skipped = len(picked) - len(new)
    if target is None:
        from database import watchlist_db as oa

        try:
            made = oa.create_watchlist(user, to["name"].strip(), [
                {"exchange": e, "symbol": y} for e, y in new])  # fmt: skip
        finally:
            oa.db_session.remove()
        if made is None:
            raise OpError("failed")
        tid, tname, version = made["id"], to["name"].strip(), None
    else:
        tid, tname, version = target["id"], target["name"], None
        added = add_all(user, tid, new)
        if added < len(new):
            raise OpError("failed", words.added_some(added, len(new), tname))
        if held is not None and new:
            try:
                with engine.open_session() as s, s.begin():
                    version = str(store.claim(s, tid, target["created_at"], held))
                    store_items.put(s, tid, dict.fromkeys(new, to["section_id"]), replace=False)
            except store.Stale:
                raise OpError("conflict", section_words.CONFLICT) from None
            except Exception:
                logger.exception("Watchlists: the section of copied symbols could not be set")
                raise OpError("failed", words.section_lost(tname)) from None
    if not move:
        return _answer(tid, len(new), skipped, version, tid)
    gone = {(e, y): n for n, e, y, _ in source["items"]}
    out = remove_all(user, src, [gone[k] for k in picked])
    if out < len(picked):
        raise OpError("failed", words.not_removed(len(picked) - out, tname, source["name"]))
    try:
        with engine.open_session() as s, s.begin():
            store_items.drop(s, src, picked)
    except Exception:
        logger.exception("Watchlists: the section rows of moved symbols could not be dropped")
    return _answer(tid, len(new), skipped, version, tid, src)


HANDLERS = {"copy": copy}
