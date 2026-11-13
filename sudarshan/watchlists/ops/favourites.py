"""The ``favourites`` op (M8.5, W10): the owner's seven numbered tabs.

The request is the slots as the owner now wants them (at most seven, each a ``slot`` 1 to 7 and a
``ref``) on the favourites version ``state`` or ``lists`` answered; the slots are replaced as a
whole in one transaction in the overlay's file, whose first statement claims that version
(``store.favourites.claim``): a stale version is ``conflict`` and changes nothing. Every ref is
checked first (a list that is gone is refused; a preset or flag that does not exist, too).

It changes no list's membership, so the lists version does not move; the push carries the new
favourites version (``versions.committed`` then ``versions.push``, after the commit, never for a
refusal). The answer's ``version`` is the new favourites version.
"""

from __future__ import annotations

from flask import session

from sudarshan.watchlists import contract, engine, versions
from sudarshan.watchlists.ops import OpError
from sudarshan.watchlists.ops import lists as known
from sudarshan.watchlists.store import favourites as store
from sudarshan.watchlists.store import sections
from sudarshan.watchlists.words import favourites as words

MAX_VERSION = 2**63 - 1


def _slots(raw: list[dict]) -> list[tuple[int, str]]:
    if len(raw) > contract.LIMITS["favourite_slots"] or any(
        s["slot"] > contract.LIMITS["favourite_slots"] for s in raw
    ):
        raise OpError("limit", words.SEVEN_AT_MOST)
    if any(s["slot"] < 1 for s in raw):
        raise OpError("invalid", words.SLOT_BAD, "slots")
    if len({s["slot"] for s in raw}) != len(raw):
        raise OpError("invalid", words.SLOT_TWICE, "slots")
    if len({s["ref"] for s in raw}) != len(raw):
        raise OpError("invalid", words.REF_TWICE, "slots")
    user = session.get("user") or ""
    for s in raw:
        found = known.exists(s["ref"], user)
        if found is None:
            raise OpError("invalid", words.REF_BAD, "slots")
        if not found:
            raise OpError("gone", words.GONE)
    return sorted((s["slot"], s["ref"]) for s in raw)


def favourites(args: dict) -> dict:
    slots = _slots(args["slots"])
    held = args["version"]
    if not 0 <= held <= MAX_VERSION:
        raise OpError("conflict", words.CONFLICT)  # no version is that, so it is stale
    with engine.open_session() as s, s.begin():
        try:
            version = store.claim(s, held)
        except store.Stale:
            raise OpError("conflict", words.CONFLICT) from None
        store.replace(s, slots, sections.now_ms())
    versions.committed("favourites", version)
    versions.push("favourites")
    return {"version": str(version), "id": None, "words": None}


HANDLERS = {"favourites": favourites}
