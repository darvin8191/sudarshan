"""The three versions of Watchlists v2 and the push that tells the phone one moved (M8.4, W5).

The lists version is an in-memory counter, read as ``"<boot>:<n>"``. ``<boot>`` is new at every
process start, so a reader only ever asks whether two values are equal, never which is larger: a
restart looks like a change, which is the safe answer. The favourites and flags versions are the
store's integers (``wl_meta.favourites_version`` and ``flags_version``; an absent row reads as 0),
because a write of favourites or flags names the version it was decided on and a stale one is a
``conflict``, across restarts. This module keeps an in-memory mirror of those two, loaded once from
the store on first use (a store that cannot be read leaves them out of ``snapshot`` and the push,
and never raises) and updated by a writer right after its commit: ``advance_favourites`` and
``advance_flags`` add one inside the writer's own transaction, as one compare-and-set on the
version the write was decided on (``Stale`` when it has moved; only the delete prune advances the
favourites without one), and ``committed`` publishes the number once it has committed. The push and ``state`` read the mirror, so a request opens a store session
only for its own write (and for that first load). Every lock's critical section is bookkeeping and
nothing else, so it is correct under eventlet, gthread and the dev server. The push is emitted
after the bump, outside the lock, through ``extensions.emit_from_any_thread``, and names every ref
the write changed (``refs``); a push that cannot be sent is logged and never raised, because the
write it follows has already committed.

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
from utils.thread_safe_cache import LockedTTLCache

logger = get_logger(__name__)

BOOT = secrets.token_hex(4)
#: The two versions the store holds, by name, and the ``wl_meta`` row of each.
STORED = {"favourites": "favourites_version", "flags": "flags_version"}
#: How long a store that could not be read is left alone before the mirror's load tries again.
STORE_RETRY_S = 5
_lock = Lock()
_lists = 0
_mirror: dict[str, int | None] = dict.fromkeys(STORED)
#: Set while the store is remembered as unreadable: every OpenAlgo watchlist write pushes, and
#: without this each push would open the broken file again and log the failure again.
_unreadable = LockedTTLCache(maxsize=1, ttl=STORE_RETRY_S)


def read(kind: str = "lists") -> str:
    """The lists version now (one whole-integer read, so it needs no lock)."""
    if kind != "lists":
        raise ValueError("favourites and flags are integers: use favourites() and flags()")
    return f"{BOOT}:{_lists}"


def _load() -> None:
    """Fill the mirror from the store, once. A store that is missing, newer or unreadable leaves it
    empty and is not an error here: the bridge answers for it. A missing file is looked for again
    at the next call (it costs one ``stat``); a newer or unreadable one is left alone for
    ``STORE_RETRY_S`` seconds, so a run of pushes while it is broken opens it and logs it once."""
    if all(v is not None for v in _mirror.values()) or _unreadable.get("store", False):
        return
    try:
        if not engine.store_path().exists():
            return
        engine.ready()
        with engine.open_session() as s:
            rows = dict(s.execute(select(MetaRow.name, MetaRow.value).where(
                MetaRow.name.in_(STORED.values()))).all())  # fmt: skip
    except (engine.RecordTooNew, engine.RecordUnreadable):
        _unreadable["store"] = True  # the cause is in the error log once, from engine.ready()
        return
    except Exception:
        logger.exception("Watchlists: the favourites and flags versions could not be read")
        _unreadable["store"] = True
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


class Stale(Exception):
    """The stored version a write was decided on is not the current one: nothing was advanced, and
    the caller's transaction rolls back, so the write changes nothing."""


def _advance(s, kind: str, held: int | None = None) -> int:
    """Add one to the stored version inside the caller's open transaction; the new value. One
    atomic UPDATE, the writer's first statement, so it takes SQLite's write lock at once and two
    writers never read the same number; an absent row reads as 0 and is made at 1. With ``held`` it
    is a compare-and-set: the UPDATE is guarded on that number and checked by its row count
    (``held`` 0 matches an absent row), else ``Stale``. Without ``held`` (the delete prune, which
    cannot be refused) it always advances."""
    name = STORED[kind]
    where = [MetaRow.name == name]
    if held is not None:
        where.append(cast(MetaRow.value, Integer) == held)
    done = s.execute(update(MetaRow).where(*where).values(
        value=cast(cast(MetaRow.value, Integer) + 1, String)))  # fmt: skip
    if done.rowcount == 1:
        if held is not None:
            return held + 1
        return int(s.scalar(select(MetaRow.value).where(MetaRow.name == name)))
    if held not in (None, 0) or (
        s.scalar(select(MetaRow.value).where(MetaRow.name == name)) is not None
    ):
        raise Stale  # a row is there that is not ``held``, or ``held`` is not 0
    try:
        s.add(MetaRow(name=name, value="1"))
        s.flush()
    except IntegrityError:
        raise Stale from None  # another first write made the row between the two statements
    return 1


def advance_favourites(s, held: int | None = None) -> int:
    """For the favourites writer (W10), which names the version its request was decided on, and the
    delete prune, which names none: inside the write's transaction, then
    ``committed("favourites", n)`` after it commits. A stale version raises ``Stale``."""
    return _advance(s, "favourites", held)


def advance_flags(s, held: int) -> int:
    """For the flags writers (W9's rename and clear), which always name the version they were
    decided on: inside the write's transaction, then ``committed("flags", n)`` after it commits. A
    stale version raises ``Stale``."""
    return _advance(s, "flags", held)


def push(why: str, refs: Iterable[str] = (), version: str | None = None) -> None:
    """Tell the owner's open apps what changed: ``{v, ref, refs, why}`` (with the favourites and
    flags versions when the store gives them) to the owner's room. ``bump`` calls it after moving
    the lists version; a change that alters no list's membership (the favourites) calls it alone,
    and the lists version stays where it is.

    ``refs`` is every ref the change touched (the target and the source of a move, the old and the
    new flag list of a mark), in the order named, each once; it is empty when the change cannot be
    pinned to a ref (OpenAlgo's answer did not name its list), which means "read everything again".
    ``ref`` is the one of them, or ``lists`` when there are none or several: the phone reads it until
    it moves to ``refs``. ``v`` is the lists version, ``version`` when the caller has just bumped it.
    A push that cannot be sent is logged and never raised: the change it follows has committed."""
    named = list(dict.fromkeys(refs))
    payload = {"v": version or read(), "ref": named[0] if len(named) == 1 else "lists",
               "refs": named, "why": why}  # fmt: skip
    for kind, got in (("favourites", favourites()), ("flags", flags())):
        if got is not None:
            payload[kind] = got
    try:
        emit_from_any_thread(contract.EVENT_PUSH, payload, to=contract.ROOM, namespace="/")
    except Exception:
        logger.exception("Watchlists: the change push could not be sent")


def bump(why: str, refs: Iterable[str] = ()) -> str:
    """Bump the lists version once and push it with every ref the change touched (``push``); the
    new version."""
    global _lists
    with _lock:  # the increment and nothing else
        _lists += 1
        version = f"{BOOT}:{_lists}"
    push(why, refs, version)
    return version


def forget() -> None:
    """Drop the mirror so the next use loads again (tests, and a store that was replaced)."""
    with _lock:
        for kind in _mirror:
            _mirror[kind] = None
    _unreadable.clear()
