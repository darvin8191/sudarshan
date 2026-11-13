"""The reader of ``flag:<n>`` refs (M8.6, W9): every symbol carrying flag ``n`` across every list.

Newest flag first, read-only (``locked``), no sections and no follow rows. The rows come from
``wl_symbol`` alone, so a symbol keeps its place here when it has left every list, and a contract
no longer in the master reads as expired (decided at read, as in a list). Only whether a symbol has
a note is told, never the note: notes and flags are the owner's alone, and the sealed members route
(W23) must not return a note either. The merge into the ``View`` record is ``view.merge_flag``.
"""

from __future__ import annotations

from sqlalchemy import select

from sudarshan.watchlists import engine, master, schema
from sudarshan.watchlists import view as merge_view
from sudarshan.watchlists.ops import OpError
from sudarshan.watchlists.schema import OWNER
from sudarshan.watchlists.store import symbols
from sudarshan.watchlists.words import marks as words

KIND = "flag"


def mark_of(ident: str) -> int:
    """``3`` to 3; anything but 1 to 5 is not a flag."""
    if ident not in ("1", "2", "3", "4", "5"):
        raise OpError("invalid", field="ref")
    return int(ident)


def view(ident: str, user: str) -> dict:
    """The ``View`` record of ``flag:<ident>``."""
    mark = mark_of(ident)
    ref = f"flag:{mark}"
    with engine.open_session() as s:
        rows = symbols.flagged(s, mark)
        name = next(f["name"] for f in symbols.flag_names(s) if f["mark"] == mark)
        pref = s.scalars(select(schema.PrefRow).where(
            schema.PrefRow.principal == OWNER, schema.PrefRow.ref == ref)).first()  # fmt: skip
        raw = None if pref is None else {"sort": pref.sort, "descending": pref.descending,
                                         "basis": pref.basis, "held": pref.held}  # fmt: skip
    m = master.Master()
    return merge_view.merge_flag(mark, name, rows, m.row, loaded=m.loaded(), isin=m.isin,
                                 pref=raw, lines=[words.FLAG_LIST])  # fmt: skip


def members(ident: str, user: str) -> dict:
    """The contracts carrying the flag (the sealed route, W23); never a note."""
    raise NotImplementedError("the members of a flag list are W23's")
