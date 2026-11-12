"""The three versions of Watchlists v2 and the push that tells the phone one moved (M8.4, W5).

Lists, favourites and flags each have an in-memory counter, read as ``"<boot>:<n>"``. ``<boot>``
is new at every process start, so a reader only ever asks whether two values are equal, never
which is larger: a restart looks like a change, which is the safe answer. Each bump is under a real
lock whose critical section is the increment and nothing else, so it is correct under eventlet,
gthread and the dev server. The push is emitted after the bump, outside the lock, through
``extensions.emit_from_any_thread``; a push that cannot be sent is logged and never raised, because
the write it follows has already committed.

Every write that changes what a list, a section or a flag list holds bumps the lists version once
and pushes once, from one of two places and never both: the hook (``bridge/push.py``) for requests
to OpenAlgo's own watchlist routes, or the op itself (``ops.bumps``) for the bridge's writes.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterable

from extensions import emit_from_any_thread
from sudarshan.watchlists import contract
from utils.logging import get_logger
from utils.real_threading import Lock

logger = get_logger(__name__)

BOOT = secrets.token_hex(4)
KINDS = ("lists", "favourites", "flags")
_lock = Lock()
_count = dict.fromkeys(KINDS, 0)


def read(kind: str) -> str:
    """The version of ``kind`` now (one whole-integer read, so it needs no lock)."""
    return f"{BOOT}:{_count[kind]}"


def snapshot() -> dict[str, str]:
    return {kind: read(kind) for kind in KINDS}


def _bump(kind: str) -> str:
    with _lock:  # the increment and nothing else
        _count[kind] += 1
        n = _count[kind]
    return f"{BOOT}:{n}"


def bump_favourites() -> str:
    return _bump("favourites")


def bump_flags() -> str:
    return _bump("flags")


def bump(why: str, refs: Iterable[str] = ()) -> str:
    """Bump the lists version once and push ``{v, ref, why}`` (with the other two versions) to the
    owner's room. ``ref`` is the one ref changed, or ``lists`` when none or several were."""
    version = _bump("lists")
    refs = list(refs)
    push = {
        "v": version,
        "ref": refs[0] if len(refs) == 1 else "lists",
        "why": why,
        "favourites": read("favourites"),
        "flags": read("flags"),
    }
    try:
        emit_from_any_thread(contract.EVENT_PUSH, push, to=contract.ROOM, namespace="/")
    except Exception:
        logger.exception("Watchlists: the change push could not be sent")
    return version
