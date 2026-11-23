"""View preferences, one row per ref (M8.7, W10; the figures, M8.3 GA6.1).

An absent row reads as the defaults (list order, previous close, holdings line off, no figures;
see ``view.DEFAULT_PREF``) and is never written as if the owner had chosen it. ``put`` writes only
the fields given, in one statement that takes SQLite's write lock first, so two screens changing
different fields of one ref both land and neither reads and writes back the other's value.
"""

from __future__ import annotations

from sqlalchemy import update

from sudarshan.watchlists import schema
from sudarshan.watchlists.schema import OWNER


def put(s, ref: str, fields: dict, at: int) -> None:
    """Set ``fields`` (any of sort, descending, basis, held, cols) on ``ref``; a ref with no row
    gets one, its other fields at the defaults the table declares. ``cols`` is a list of figure
    ids, stored joined by commas; an empty list stores NULL (no figures)."""
    values = {k: int(v) if isinstance(v, bool) else v for k, v in fields.items()}
    if "cols" in values:
        values["cols"] = ",".join(values["cols"]) or None
    values["updated_at"] = at
    pref = schema.PrefRow
    done = s.execute(update(pref).where(pref.principal == OWNER, pref.ref == ref).values(**values))
    if done.rowcount == 0:
        s.add(pref(ref=ref, **values))
        s.flush()


def raw(p) -> dict | None:
    """A stored row as ``view._pref`` reads it; None for no row."""
    if p is None:
        return None
    return {"sort": p.sort, "descending": p.descending, "basis": p.basis, "held": p.held,
            "cols": p.cols}  # fmt: skip
