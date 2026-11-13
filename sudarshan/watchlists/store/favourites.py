"""The seven favourite slots, and what a favourite resolves against (M8.5, W10).

``claim`` is the favourites version's compare-and-set, the first statement of the write's
transaction: an UPDATE guarded on the held number, which takes SQLite's write lock at once (a read
first and a write after fails under WAL as a lock error no retry clears), then
``versions.advance_favourites`` in the same transaction. The slots are replaced as a whole. The
reads here are one read-only session each and never write: a slot naming something that is gone is
dropped by the caller, and the delete hook (``store/prune.py``) empties the slots of a deleted list.
"""

from __future__ import annotations

from sqlalchemy import Integer, cast, delete, func, select, update

from sudarshan.watchlists import schema, versions
from sudarshan.watchlists.schema import OWNER

META = "favourites_version"


class Stale(Exception):
    """The version the write was decided on is not the favourites': nothing was changed."""


def claim(s, held: int) -> int:
    """Check ``held`` against the favourites version and advance it, as one step; the new version.
    ``held`` 0 is a store that has never held one. Raises ``Stale`` (the caller's transaction rolls
    back, so a refused claim changes nothing)."""
    meta = schema.MetaRow
    done = s.execute(update(meta).where(
        meta.name == META, cast(meta.value, Integer) == held).values(value=meta.value))  # fmt: skip
    if done.rowcount == 0 and (
        held != 0 or s.scalar(select(meta.value).where(meta.name == META)) is not None
    ):
        raise Stale
    return versions.advance_favourites(s)


def replace(s, slots: list[tuple[int, str]], at: int) -> None:
    """The seven slots become exactly ``slots`` (inside the claimed transaction)."""
    fav = schema.FavouriteRow
    s.execute(delete(fav).where(fav.principal == OWNER))
    s.add_all(fav(slot=n, ref=ref, updated_at=at) for n, ref in slots)
    s.flush()


def slots(s) -> list[tuple[int, str]]:
    fav = schema.FavouriteRow
    return [(r[0], r[1]) for r in s.execute(select(fav.slot, fav.ref).where(
        fav.principal == OWNER).order_by(fav.slot)).all()]  # fmt: skip


def version(s) -> int:
    """The stored favourites version; 0 when the owner has never written one."""
    raw = s.scalar(select(schema.MetaRow.value).where(schema.MetaRow.name == META))
    try:
        return int(raw or 0)
    except ValueError:
        return 0


def flag_names(s) -> dict[int, str]:
    """The names the owner gave flags; a flag he has not renamed is absent."""
    flag = schema.FlagNameRow
    return dict(s.execute(select(flag.mark, flag.name).where(flag.principal == OWNER)).all())


def flag_counts(s) -> dict[int, int]:
    """How many symbols carry each flag, across every list (W9 builds the flag lists; this reads
    ``wl_symbol`` directly so this change needs nothing of it)."""
    sym = schema.SymbolRow
    return dict(s.execute(select(sym.flag, func.count()).where(
        sym.principal == OWNER, sym.flag.is_not(None)).group_by(sym.flag)).all())  # fmt: skip


def follow_counts(s) -> dict[int, tuple[str, int]]:
    """``{list_id: (oa_created_at, follow rows)}`` for the lists the overlay has a guard row for;
    the caller counts a list's rows only when its guard matches the live list (a reused id's
    overlay is ignored, as ``view`` ignores it)."""
    guard, fol = schema.ListRow, schema.FollowRow
    made = dict(s.execute(select(guard.list_id, guard.oa_created_at).where(
        guard.principal == OWNER)).all())  # fmt: skip
    rows = s.execute(select(fol.list_id, func.count()).where(
        fol.principal == OWNER).group_by(fol.list_id)).all()  # fmt: skip
    return {lid: (made[lid], n) for lid, n in rows if lid in made}
