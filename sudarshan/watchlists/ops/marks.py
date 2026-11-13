"""The flag and note ops (M8.6, W9): ``mark``, ``flags``, ``symbol`` and ``expired_clear``.

A symbol has one flag (1 to 5) and one note (up to 500 characters) across every list; ``mark``
names the symbol's ``version`` it was decided on and ``flags`` and ``expired_clear`` name the
flags version, each checked as one compare-and-set in the transaction's first statement
(``store/symbols.py``), so a stale one is ``conflict`` and changes nothing. Nothing is checked
against the master except the expired reading and ``expired_clear``; a flag on a contract that
left it is kept. The bridge bumps the lists version once after a handler returns (``ops.bumps``),
never after a refusal: ``mark`` names ``flag:<old>`` and ``flag:<new>`` only when the flag changed
(a note alone changes no flag list's members), ``flags`` and ``expired_clear`` as the answers say.
Notes and flags are the owner's alone.
"""

from __future__ import annotations

from flask import session

from sudarshan.watchlists import contract, engine, versions
from sudarshan.watchlists.ops import OpError, bumps
from sudarshan.watchlists.store import symbols as store
from sudarshan.watchlists.words import marks as words

MAX_VERSION = 2**63 - 1


class Done(dict):
    """The ``Done`` record, with the flag lists it changed for ``ops.bumps``."""

    refs: tuple[str, ...] = ()


def _key(raw: str) -> tuple[str, str]:
    exchange, _, symbol = raw.partition(":")
    if not exchange or not symbol or len(exchange) > 16 or len(symbol) > 80:
        raise OpError("invalid", words.KEY_BAD, "k")
    return exchange, symbol


def _held(raw) -> int | None:
    if raw is None:
        return None
    if not 0 <= raw <= MAX_VERSION:
        raise OpError("conflict", words.CONFLICT)  # no symbol can hold it, so it is stale
    return raw


def _note(raw: str | None) -> str | None:
    note = (raw or "").strip()
    if len(note) > contract.LIMITS["note"]:
        raise OpError("invalid", words.NOTE_LONG, "note")
    return note or None


def _refs(args: dict, data: Done) -> list[str]:
    return list(data.refs)


@bumps("flags", _refs)
def mark(args: dict) -> dict:
    exchange, symbol = _key(args["k"])
    if "flag" not in args and "note" not in args:
        raise OpError("invalid", words.NOTHING, "flag")
    flag = args.get("flag")
    if flag is not None and not 1 <= flag <= 5:
        raise OpError("invalid", words.FLAG_BAD, "flag")
    note = _note(args.get("note"))
    held = _held(args.get("version"))
    with engine.open_session() as s, s.begin():
        try:
            old = store.claim(s, exchange, symbol, held)
        except store.Stale:
            raise OpError("conflict", words.CONFLICT) from None
        before = None if old is None else old.flag
        if "flag" not in args:
            flag = before
        if "note" not in args:
            note = None if old is None else old.note
        version = store.write(s, exchange, symbol, old, held, flag, note)
    done = Done(version=None if version is None else str(version), id=None, words=None)
    if before != flag:
        done.refs = tuple(f"flag:{n}" for n in (before, flag) if n is not None)
    return done


def _names(raw: list[dict]) -> list[tuple[int, str]]:
    if not raw:
        raise OpError("invalid", words.NAMES_NONE, "names")
    out: list[tuple[int, str]] = []
    for item in raw:
        if not 1 <= item["mark"] <= 5:
            raise OpError("invalid", words.FLAG_BAD, "names")
        if item["mark"] in [m for m, _ in out]:
            raise OpError("invalid", words.NAME_TWICE, "names")
        name = item["name"].strip()
        if not name:
            raise OpError("invalid", words.NAME_EMPTY, "names")
        if len(name) > contract.LIMITS["flag_name"]:
            raise OpError("invalid", words.NAME_LONG, "names")
        out.append((item["mark"], name))
    return out


class _Nothing(Exception):
    """A clear that found nothing: the transaction is rolled back, the flags version stays."""


def _flags_write(held: int, change) -> int:
    """One transaction: the flags version's compare-and-set first (``versions.advance_flags``),
    then ``change(s)``; the new version. ``_Nothing`` from ``change`` rolls it all back."""
    if not 0 <= held <= MAX_VERSION:
        raise OpError("conflict", words.CONFLICT)
    with engine.open_session() as s, s.begin():
        try:
            version = versions.advance_flags(s, held)
        except versions.Stale:
            raise OpError("conflict", words.CONFLICT) from None
        change(s)
    versions.committed("flags", version)
    return version


@bumps("flags")
def flags(args: dict) -> dict:
    pairs = _names(args["names"])
    version = _flags_write(args["version"], lambda s: store.rename(s, pairs))
    return Done(version=str(version), id=None, words=None)


@bumps("flags", _refs)
def expired_clear(args: dict) -> dict:
    """Remove the flag and note of every symbol the master no longer lists, and only those."""
    from sudarshan.watchlists import master

    m = master.shared()  # the process's one master: a request never builds its own
    if not m.loaded():
        raise OpError("failed", words.NOT_LOADED)
    gone: list = []

    def change(s):
        gone[:] = [(e, y, f) for e, y, f in store.everything(s) if m.row(e, y) is None]
        if not gone:
            raise _Nothing
        store.remove(s, [(e, y) for e, y, _ in gone])

    try:
        version = _flags_write(args["version"], change)
    except _Nothing:
        return Done(version=str(args["version"]), id=None, words=words.NONE_EXPIRED)
    done = Done(version=str(version), id=None, words=words.cleared(len(gone)))
    done.refs = tuple(sorted({f"flag:{f}" for _, _, f in gone if f}))
    return done


def symbol(args: dict) -> dict:
    from sudarshan.watchlists import master
    from sudarshan.watchlists.refs import list as listref

    exchange, name = _key(args["k"])
    with engine.open_session() as s:
        row = store.find(s, exchange, name)
        names = {f["mark"]: f["name"] for f in store.flag_names(s)}
        found = None if row is None else (row.flag, row.note, row.note_at, row.version)
    flag, note, note_at, version = found or (None, None, None, None)
    m = master.shared()  # the process's one master: a request never builds its own
    # Own lists only. Built-in lists (W12) join this answer as `Held` records after them.
    held = [{"ref": f"list:{i}", "name": n}
            for i, n in listref.holding(session.get("user") or "", exchange, name)]  # fmt: skip
    return {
        "key": f"{exchange}:{name}",
        "flag": flag,
        "flag_name": names.get(flag),
        "note": note,
        "note_at": note_at,
        "expired": m.loaded() and m.row(exchange, name) is None,
        "version": version,
        "lists": held,
    }


HANDLERS = {"mark": mark, "flags": flags, "symbol": symbol, "expired_clear": expired_clear}
