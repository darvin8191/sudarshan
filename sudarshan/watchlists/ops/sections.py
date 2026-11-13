"""The section ops (M8.4, W7): ``section_add``, ``section_edit`` and ``section_remove``.

They act on own lists only (``list:<id>``); a list that does not exist, or a section that does not,
is ``gone``. Each write is one transaction in the overlay's file (``openalgo.db`` is never touched)
that first claims the list's version (``store/sections.py``), so a stale version is ``conflict``
and changes nothing, then changes the rows. The bridge bumps the lists version once and pushes
after the handler returns (``ops.bumps``), never after a refusal.

``section_edit`` and ``section_remove`` name only the section, so the answer carries the ref of
its list (``Done.ref``, not a field of the record the phone reads) for the push.
"""

from __future__ import annotations

from flask import session

from sudarshan.watchlists import contract, engine
from sudarshan.watchlists.ops import OpError, bumps
from sudarshan.watchlists.store import sections as store
from sudarshan.watchlists.words import sections as words
from sudarshan.watchlists.words import view as view_words

MAX_VERSION = 2**63 - 1
MAX_ID = 2**31 - 1  # the reader of lists is imported when used, so a module import stays light


class Done(dict):
    """The ``Done`` record, with the ref of the list it changed for ``ops.bumps``."""

    ref: str = ""


def _held(text: str) -> int:
    if not text.isascii() or not text.isdigit() or int(text) > MAX_VERSION:
        raise OpError("conflict", words.CONFLICT)  # no list can hold it, so it is stale
    return int(text)


def _name(raw: str) -> str:
    name = raw.strip()
    if not name:
        raise OpError("invalid", words.NAME_EMPTY, "name")
    if len(name) > contract.LIMITS["section_name"]:
        raise OpError("invalid", words.NAME_LONG, "name")
    return name


def _mark(raw: int) -> int:
    if not 0 <= raw <= 5:
        raise OpError("invalid", words.MARK_BAD, "mark")
    return raw


def _created(list_id: int) -> str:
    """The live list's ``created_at`` as the guard stores it; ``gone`` when there is no such list."""
    from sudarshan.watchlists.refs import list as listref

    live = listref.read_live(session.get("user") or "", list_id) if 0 < list_id <= MAX_ID else None
    if live is None:
        raise OpError("gone", view_words.LIST_GONE)
    return live["created_at"]


def _done(version: int, ident: int, list_id: int) -> Done:
    done = Done(version=str(version), id=ident, words=None)
    done.ref = f"list:{list_id}"
    return done


def _list_of(section_id: int) -> int:
    if not 0 < section_id <= MAX_ID:
        raise OpError("gone", words.SECTION_GONE)
    with engine.open_session() as s:
        row = store.find(s, section_id)
        if row is None:
            raise OpError("gone", words.SECTION_GONE)
        return row.list_id


def _ref(args: dict, data: Done) -> list[str]:
    return [data.ref]


@bumps("sections", _ref)
def section_add(args: dict) -> dict:
    name, mark = _name(args["name"]), _mark(args.get("mark", 0))
    held, list_id = _held(args["version"]), args["list"]
    created = _created(list_id)
    with engine.open_session() as s, s.begin():
        try:
            version = store.claim(s, list_id, created, held)
        except store.Stale:
            raise OpError("conflict", words.CONFLICT) from None
        rows = store.sections_of(s, list_id)
        if len(rows) >= contract.LIMITS["sections_per_list"]:
            raise OpError("limit", words.SECTIONS_FULL)
        if store.taken(rows, name):
            raise OpError("invalid", words.NAME_TAKEN, "name")
        ident = store.add(s, list_id, name, mark, rows)
    return _done(version, ident, list_id)


@bumps("sections", _ref)
def section_edit(args: dict) -> dict:
    if "name" not in args and "mark" not in args:
        raise OpError("invalid", words.NOTHING, "name")
    name = _name(args["name"]) if "name" in args else None
    mark = _mark(args["mark"]) if "mark" in args else None
    held, ident = _held(args["version"]), args["id"]
    list_id = _list_of(ident)
    created = _created(list_id)
    with engine.open_session() as s, s.begin():
        try:
            version = store.claim(s, list_id, created, held)
        except store.Stale:
            raise OpError("conflict", words.CONFLICT) from None
        row = store.find(s, ident)
        if row is None or row.list_id != list_id:
            raise OpError("gone", words.SECTION_GONE)
        if name is not None:
            if store.taken(store.sections_of(s, list_id), name, keep=ident):
                raise OpError("invalid", words.NAME_TAKEN, "name")
            row.name = name
        if mark is not None:
            row.mark = mark
        row.updated_at = store.now_ms()
    return _done(version, ident, list_id)


@bumps("sections", _ref)
def section_remove(args: dict) -> dict:
    held, ident = _held(args["version"]), args["id"]
    list_id = _list_of(ident)
    created = _created(list_id)
    with engine.open_session() as s, s.begin():
        try:
            version = store.claim(s, list_id, created, held)
        except store.Stale:
            raise OpError("conflict", words.CONFLICT) from None
        row = store.find(s, ident)
        if row is None or row.list_id != list_id:
            raise OpError("gone", words.SECTION_GONE)
        store.remove(s, row)
    return _done(version, ident, list_id)


HANDLERS = {
    "section_add": section_add,
    "section_edit": section_edit,
    "section_remove": section_remove,
}
