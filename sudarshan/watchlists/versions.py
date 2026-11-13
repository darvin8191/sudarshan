"""The three versions of Watchlists v2 and the push that tells the phone one moved (M8.4, W5).

The lists version is an in-memory counter, read as ``"<boot>:<n>"``. ``<boot>`` is new at every
process start, so a reader only ever asks whether two values are equal, never which is larger: a
restart looks like a change, which is the safe answer. The favourites and flags versions are the
store's integers (``wl_meta.favourites_version`` and ``flags_version``; an absent row reads as 0),
because a write of favourites or flags names the version it was decided on and a stale one is a
``conflict``, across restarts. This module keeps an in-memory mirror of those two, loaded once from
the store on first use (a store that cannot be read leaves them out of ``snapshot`` and the push,
and never raises) and updated by a writer right after its commit: ``advance_favourites`` and
``advance_flags`` add one inside the writer's own transaction, ``committed`` publishes the number
once it has committed. The push and ``state`` read the mirror, so a request opens a store session
only for its own write (and for that first load). Every lock's critical section is bookkeeping and
nothing else, so it is correct under eventlet, gthread and the dev server. The push is emitted
after the bump, outside the lock, through ``extensions.emit_from_any_thread``; a push that cannot
be sent is logged and never raised, because the write it follows has already committed.

Every write that changes what a list, a section or a flag list holds bumps the lists version once
and pushes once, from one of two places and never both: the hook (``bridge/push.py``) for requests
to OpenAlgo's own watchlist routes, or the op itself (``ops.bumps``) for the bridge's writes.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable

from sqlalchemy import Integer, String, cast, select, update
from sqlalchemy.exc import IntegrityError

from extensions import emit_from_any_thread
from sudarshan.watchlists import contract, engine
from sudarshan.watchlists.schema import MetaRow
from utils.logging import get_logger
from utils.real_threading import Lock

logger = get_logger(__name__)

BOOT = secrets.token_hex(4)
#: The two versions the store holds, by name, and the ``wl_meta`` row of each.
STORED = {"favourites": "favourites_version", "flags": "flags_version"}
_lock = Lock()
_lists = 0
_mirror: dict[str, int | None] = dict.fromkeys(STORED)


def read(kind: str = "lists") -> str:
    """The lists version now (one whole-integer read, so it needs no lock)."""
    if kind != "lists":
        raise ValueError("favourites and flags are integers: use favourites() and flags()")
    return f"{BOOT}:{_lists}"


def _load() -> None:
    """Fill the mirror from the store, once. A store that is missing, newer or unreadable leaves it
    empty (the next call looks again) and is not an error here: the bridge answers for it."""
    if all(v is not None for v in _mirror.values()):
        return
    try:
        if not engine.store_path().exists():
            return
        engine.ready()
        with engine.open_session() as s:
            rows = dict(s.execute(select(MetaRow.name, MetaRow.value).where(
                MetaRow.name.in_(STORED.values()))).all())  # fmt: skip
    except (engine.RecordTooNew, engine.RecordUnreadable):
        return
    except Exception:
        logger.exception("Watchlists: the favourites and flags versions could not be read")
        return
    for kind, name in STORED.items():
        try:
            found = int(rows.get(name, 0))
        except (TypeError, ValueError):
            continue  # not a number: left out rather than guessed
        committed(kind, found, loaded=True)


def committed(kind: str, version: int, loaded: bool = False) -> None:
    """Publish a stored version a writer has just committed (or the first load has read). It only
    ever moves forward, so a load that read before a commit cannot undo it."""
    with _lock:  # one comparison and one assignment
        have = _mirror[kind]
        if have is None or (version > have and not loaded):
            _mirror[kind] = version


def favourites() -> int | None:
    """The favourites version, or None when the store cannot be read."""
    _load()
    return _mirror["favourites"]


def flags() -> int | None:
    """The flags version, or None when the store cannot be read."""
    _load()
    return _mirror["flags"]


def snapshot() -> dict:
    """``{lists, favourites, flags}``; an integer the store cannot give is left out."""
    out: dict = {"lists": read()}
    for kind, got in (("favourites", favourites()), ("flags", flags())):
        if got is not None:
            out[kind] = got
    return out


def _advance(s, kind: str) -> int:
    """Add one to the stored version inside the caller's open transaction; the new value. One
    atomic update, so two writers never read the same number; a row that is absent is made."""
    name = STORED[kind]
    done = s.execute(update(MetaRow).where(MetaRow.name == name).values(
        value=cast(cast(MetaRow.value, Integer) + 1, String)))  # fmt: skip
    if done.rowcount == 0:
        s.add(MetaRow(name=name, value="1"))
        s.flush()
    return int(s.scalar(select(MetaRow.value).where(MetaRow.name == name)))


def advance_favourites(s) -> int:
    """For the favourites writer (W10) and the delete prune: inside the write's transaction, then
    ``committed("favourites", n)`` after it commits."""
    return _advance(s, "favourites")


class Stale(Exception):
    """The version a write was decided on is not the stored one: nothing was changed."""


def advance_flags(s, held: int) -> int:
    """For the flags writer (W9): inside the write's transaction, then ``committed("flags", n)``
    after it commits. One compare-and-set: the UPDATE only matches when the stored version is
    ``held`` (absent reads as 0), and a row count of 0 raises ``Stale`` so the caller's
    transaction rolls back. It is the transaction's first statement, so it takes the write lock."""
    name = STORED["flags"]
    done = s.execute(update(MetaRow).where(
        MetaRow.name == name, cast(MetaRow.value, Integer) == held).values(
        value=cast(cast(MetaRow.value, Integer) + 1, String)))  # fmt: skip
    if done.rowcount == 1:
        return held + 1
    if held != 0 or s.scalar(select(MetaRow.value).where(MetaRow.name == name)) is not None:
        raise Stale
    try:
        s.add(MetaRow(name=name, value="1"))
        s.flush()
    except IntegrityError:
        raise Stale from None  # another first write made the row between the two statements
    return 1


def bump(why: str, refs: Iterable[str] = ()) -> str:
    """Bump the lists version once and push ``{v, ref, why}`` (with the other two versions when
    the store gives them) to the owner's room. ``ref`` is the one ref changed, or ``lists`` when
    none or several were."""
    global _lists
    with _lock:  # the increment and nothing else
        _lists += 1
        version = f"{BOOT}:{_lists}"
    refs = list(refs)
    push = {"v": version, "ref": refs[0] if len(refs) == 1 else "lists", "why": why}
    for kind, got in (("favourites", favourites()), ("flags", flags())):
        if got is not None:
            push[kind] = got
    try:
        emit_from_any_thread(contract.EVENT_PUSH, push, to=contract.ROOM, namespace="/")
    except Exception:
        logger.exception("Watchlists: the change push could not be sent")
    return version


def forget() -> None:
    """Drop the mirror so the next use loads again (tests, and a store that was replaced)."""
    with _lock:
        for kind in _mirror:
            _mirror[kind] = None
