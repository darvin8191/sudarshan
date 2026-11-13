"""The seven favourite slots, and what a favourite resolves against (M8.5, W10).

``claim`` is the favourites version's compare-and-set, the first statement of the write's
transaction: ``versions.advance_favourites(s, held)``, an UPDATE guarded on the held number and
checked by its row count, which takes SQLite's write lock at once (a read first and a write after
fails under WAL as a lock error no retry clears). The slots are replaced as a whole. The
reads here are one read-only session each and never write: a slot naming something that is gone is
dropped by the caller, and the delete hook (``store/prune.py``) empties the slots of a deleted list.
"""

from __future__ import annotations

from sqlalchemy import delete, func, select

from sudarshan.watchlists import schema, versions
from sudarshan.watchlists.schema import OWNER

META = "favourites_version"


class Stale(Exception):
    """The version the write was decided on is not the favourites': nothing was changed."""


def claim(s, held: int) -> int:
    """Check ``held`` against the favourites version and advance it, as one step; the new version.
    ``held`` 0 is a store that has never held one. Raises ``Stale`` (the caller's transaction rolls
    back, so a refused claim changes nothing)."""
    try:
        return versions.advance_favourites(s, held)  # an UPDATE first: the write lock at once
    except versions.Stale:
        raise Stale from None


def replace(s, slots: list[tuple[int, str, str | None]], at: int) -> None:
    """The seven slots become exactly ``slots``, each ``(slot, ref, oa_created_at)`` (the created
    text of a ``list:`` slot's list, else None), inside the claimed transaction."""
    fav = schema.FavouriteRow
    s.execute(delete(fav).where(fav.principal == OWNER))
    s.add_all(fav(slot=n, ref=ref, oa_created_at=made, updated_at=at) for n, ref, made in slots)
    s.flush()


def slots(s) -> list[tuple[int, str, str | None]]:
    fav = schema.FavouriteRow
    got = s.execute(select(fav.slot, fav.ref, fav.oa_created_at).where(
        fav.principal == OWNER).order_by(fav.slot)).all()  # fmt: skip
    return [(r[0], r[1], r[2]) for r in got]


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
