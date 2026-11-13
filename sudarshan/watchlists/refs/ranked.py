"""The reader of ``ranked:<source>:<asset>:<category>`` refs (M8.9, W22): one Upstox ranking.

Reads through the ``ranked`` op's own path (``ops/ranked.py``), so the view, the op and the sealed
members route share its 60 s cache and its single flight and keep nothing of their own: a ranking
is never held here past the op's cache. Locked, no sections, no follow rows, version "0". With no
sign-in or no answer the view has no rows and its one line says why, in words (the ``ranked`` op
carries the state for the page); the members route is refused with the same words, since a
consumer must never take an empty ranking for an empty list.
"""

from __future__ import annotations

from sudarshan.watchlists import ranked, versions
from sudarshan.watchlists import view as merge_view
from sudarshan.watchlists.ops import OpError
from sudarshan.watchlists.ops import ranked as op
from sudarshan.watchlists.words import ranked as words

KIND = "ranked"


def parts_of(ident: str) -> tuple[str, str, str]:
    """``futures:INDEX:OI_GAINERS`` to its three parts; anything not offered is ``invalid``."""
    found = ranked.parse_ref(f"{KIND}:{ident}")
    if found is None:
        raise OpError("invalid", field="ref")
    return found


def view(ident: str, user: str) -> dict:
    """The ``View`` record of ``ranked:<ident>``: the rows now, 20 of them (the page asks the
    ``ranked`` op for 50)."""
    src, asset, cat = parts_of(ident)
    got, _ = op.answer(src, asset, cat, ranked.DEFAULT_SIZE, user)
    return {
        "ref": got["ref"], "kind": KIND, "name": words.label(src, asset, cat), "version": "0",
        "locked": True, "as_of": None, "lines": [got["words"]], "total": len(got["rows"]),
        "missing": [], "sections": [], "rows": got["rows"], "follow": [],
        "pref": merge_view._pref(None),
    }  # fmt: skip


def members(ident: str, user: str) -> dict:
    """The contracts the ranking holds now (the sealed route, W23): the page of 50, never a flag
    or a note. A ranking that cannot be read is ``signin`` or ``failed`` in words, never empty."""
    src, asset, cat = parts_of(ident)
    got, missing = op.answer(src, asset, cat, ranked.MAX_SIZE, user)
    if got["state"] == "signin":
        raise OpError("signin", got["words"])
    if not got["rows"] and got["state"] in ("unavailable", "wait"):
        raise OpError("failed", got["words"])
    return {
        "ref": got["ref"],
        "version": versions.read(),
        "members": [r["contract"] for r in got["rows"]],
        "valid_until": None,
        "left_out": [{"token": k, "reason": words.NOT_IN_MASTER} for k in missing],
    }  # fmt: skip
