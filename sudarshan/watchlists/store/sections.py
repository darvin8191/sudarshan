"""Section writes and the per-list version they decide on (M8.4, W7).

Every write of a list's overlay names the version it was decided on (``view`` answered it) and
moves it in the transaction that changes the rows. ``claim`` is one compare-and-set in SQL: an
UPDATE guarded on the old version, checked by its row count, and for a list with no guard row yet
an INSERT the primary key can refuse. It is the first statement of the transaction on purpose: an
UPDATE takes SQLite's write lock at once, so the writer never holds a read snapshot another
writer can invalidate (a read followed by a write fails there as a lock error that no retry
clears), and the writers of one list are serialised from that statement to the commit. Nothing
here reads the version and then writes it back.

Every guard row starts at the epoch milliseconds of the write that makes it, never at 1. SQLite
hands a deleted list's id to the next list made, and the delete hook removes the old guard row; a
row counted from 1 again would let a screen still holding version 1 of the deleted list pass the
claim on the new one (``created`` is read from the live list at the write, so it matches). A guard
row whose ``oa_created_at`` differs from the live list's belongs to such a deleted list whose row
the hook did not remove: the first write prunes the stale overlay in the same transaction and starts
the fresh row the same way. No client of an old overlay can hold the new number. The functions run
inside the caller's ``with open_session() as s, s.begin():``.
"""

from __future__ import annotations

import time

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from sudarshan.watchlists import schema
from sudarshan.watchlists.schema import OWNER
from sudarshan.watchlists.store import prune


class Stale(Exception):
    """The version the write was decided on is not the list's: nothing was changed."""


def now_ms() -> int:
    return int(time.time() * 1000)


def claim(s, list_id: int, created: str, held: int) -> int:
    """Check ``held`` against the list's version and advance it, as one step; the new version.
    ``held`` 0 is a list with no guard row, and the row it makes starts at the epoch milliseconds.
    Raises ``Stale`` (the caller's transaction rolls back, so a refused claim changes nothing, a
    prune included)."""
    guard = schema.ListRow
    at = now_ms()
    done = s.execute(update(guard).where(
        guard.principal == OWNER, guard.list_id == list_id, guard.oa_created_at == created,
        guard.version == held).values(version=guard.version + 1, updated_at=at))  # fmt: skip
    if done.rowcount == 1:
        return held + 1
    if held != 0:
        raise Stale
    row = s.execute(select(guard.oa_created_at).where(
        guard.principal == OWNER, guard.list_id == list_id)).first()  # fmt: skip
    if row is not None:
        if row[0] == created:
            raise Stale  # the row exists, so "0" is not what the list holds
        prune.drop_overlay(s, list_id)  # a deleted list's id, reused
    try:
        s.add(guard(list_id=list_id, oa_created_at=created, version=at, updated_at=at))
        s.flush()
    except IntegrityError:
        raise Stale from None  # another first write made the row between the two statements
    return at


def sections_of(s, list_id: int) -> list:
    sec = schema.SectionRow
    return list(s.execute(select(sec.id, sec.name, sec.position).where(
        sec.principal == OWNER, sec.list_id == list_id).order_by(sec.position, sec.id)).all())  # fmt: skip


def taken(rows: list, name: str, keep: int | None = None) -> bool:
    """Whether another section of the list already has ``name`` (letter case is not a difference)."""
    return any(r.id != keep and r.name.casefold() == name.casefold() for r in rows)


def add(s, list_id: int, name: str, mark: int, rows: list) -> int:
    at = now_ms()
    row = schema.SectionRow(
        list_id=list_id, name=name, mark=mark, position=max((r.position for r in rows), default=0) + 1,
        created_at=at, updated_at=at)  # fmt: skip
    s.add(row)
    s.flush()
    return row.id


def find(s, section_id: int):
    sec = schema.SectionRow
    return s.scalars(select(sec).where(sec.principal == OWNER, sec.id == section_id)).first()


def remove(s, row) -> None:
    """The section goes and its symbols and follow rows fall back to No section."""
    item, fol = schema.SectionItemRow, schema.FollowRow
    s.execute(item.__table__.delete().where(
        item.principal == OWNER, item.list_id == row.list_id, item.section_id == row.id))  # fmt: skip
    s.execute(update(fol).where(fol.principal == OWNER, fol.list_id == row.list_id,
                                fol.section_id == row.id).values(section_id=None))  # fmt: skip
    s.delete(row)
