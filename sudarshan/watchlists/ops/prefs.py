"""The ``pref`` op (M8.7, W10): sort, direction, change basis, the holdings line and (M8.3, GA6.1)
the figures the rows show beside the price, for one ref.

Kept on the server so the phone and the tablet agree. Only the fields sent are written, on a row
made if there is none; a ref with no row reads as the defaults and is never written as if chosen.
It changes no list and no version and pushes nothing: a screen reads the preference with ``view``
when it opens the ref. The last write wins (there is no version to hold, and nothing is lost: the
fields are independent).
"""

from __future__ import annotations

from flask import session

from sudarshan.watchlists import contract, engine
from sudarshan.watchlists.ops import OpError
from sudarshan.watchlists.ops import lists as known
from sudarshan.watchlists.store import prefs as store
from sudarshan.watchlists.store import sections
from sudarshan.watchlists.view import MAX_FIGURES
from sudarshan.watchlists.words import favourites as words

FIELDS = ("sort", "descending", "basis", "held", "cols")


def _figures(cols: list) -> None:
    """The figures picked: ids the watchlist shows, each once, six at most. Raises ``OpError``."""
    if any(c not in contract.ENUMS["Figure"] for c in cols):
        raise OpError("invalid", words.FIGURE_BAD, "cols")
    if len(set(cols)) != len(cols):
        raise OpError("invalid", words.FIGURE_TWICE, "cols")
    if len(cols) > MAX_FIGURES:
        raise OpError("invalid", words.FIGURES_SIX, "cols")


def pref(args: dict) -> dict:
    fields = {k: args[k] for k in FIELDS if k in args}
    if not fields:
        raise OpError("invalid", words.PREF_NOTHING, "sort")
    if "cols" in fields:
        _figures(fields["cols"])
    found = known.exists(args["ref"], session.get("user") or "")
    if found is None:
        raise OpError("invalid", field="ref")
    if not found:
        raise OpError("gone", words.PREF_GONE)
    with engine.open_session() as s, s.begin():
        store.put(s, args["ref"], fields, sections.now_ms())
    return {"version": None, "id": None, "words": None}


HANDLERS = {"pref": pref}
