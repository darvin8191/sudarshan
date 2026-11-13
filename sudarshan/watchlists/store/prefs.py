"""View preferences, one row per ref (M8.7, W10).

An absent row reads as the defaults (list order, previous close, holdings line off; see
``view.DEFAULT_PREF``) and is never written as if the owner had chosen it. ``put`` writes only the
fields given, in one statement that takes SQLite's write lock first, so two screens changing
different fields of one ref both land and neither reads and writes back the other's value.
"""

from __future__ import annotations

from sqlalchemy import update

from sudarshan.watchlists import schema
from sudarshan.watchlists.schema import OWNER


def put(s, ref: str, fields: dict, at: int) -> None:
    """Set ``fields`` (any of sort, descending, basis, held) on ``ref``; a ref with no row gets
    one, its other fields at the defaults the table declares."""
    values = {k: int(v) if isinstance(v, bool) else v for k, v in fields.items()}
    values["updated_at"] = at
    pref = schema.PrefRow
    done = s.execute(update(pref).where(pref.principal == OWNER, pref.ref == ref).values(**values))
    if done.rowcount == 0:
        s.add(pref(ref=ref, **values))
        s.flush()
