"""Section membership and order, written in bulk (M8.4, W8).

The functions run inside the caller's ``with open_session() as s, s.begin():``, after
``sections.claim``. Nothing here yields to the hub: the transaction holds SQLite's write lock, and
another greenlet's write would wait for it inside C, where the holder can never be resumed to
commit. The rows go in as one batch instead.
"""

from __future__ import annotations

from sqlalchemy import delete, select, update

from sudarshan.watchlists import schema
from sudarshan.watchlists.schema import OWNER
from sudarshan.watchlists.store import sections


def current(s, list_id: int, created: str, held: int) -> bool:
    """Whether ``held`` is the list's version now, without changing it (the early refusal of a
    write that makes its change in two steps; the claim stays the authority)."""
    guard = schema.ListRow
    row = s.execute(select(guard.oa_created_at, guard.version).where(
        guard.principal == OWNER, guard.list_id == list_id)).first()  # fmt: skip
    if row is None or (held == 0 and row[0] != created):
        return held == 0
    return row[0] == created and row[1] == held


def placed(s, list_id: int) -> dict:
    """``{(exchange, symbol): section_id}`` as stored."""
    item = schema.SectionItemRow
    return {
        (r.exchange, r.symbol): r.section_id
        for r in s.scalars(select(item).where(item.principal == OWNER, item.list_id == list_id))
    }


def put(s, list_id: int, keys: dict, *, replace: bool) -> None:
    """Set the section of each ``(exchange, symbol): section_id``. ``replace`` drops every row of
    the list first (arrange); otherwise only the rows of ``keys`` are replaced."""
    item = schema.SectionItemRow
    where = [item.principal == OWNER, item.list_id == list_id]
    if replace:
        s.execute(delete(item).where(*where))
    else:
        for exchange, symbol in keys:
            s.execute(delete(item).where(*where, item.exchange == exchange, item.symbol == symbol))
    s.add_all(
        item(list_id=list_id, exchange=e, symbol=y, section_id=sid)
        for (e, y), sid in keys.items()
        if sid is not None
    )


def drop(s, list_id: int, keys: list) -> None:
    """The section rows of symbols that left the list."""
    put(s, list_id, dict.fromkeys(keys), replace=False)


def order_sections(s, ids: list) -> None:
    """Positions 1, 2, 3 ... in the order given."""
    sec, at = schema.SectionRow, sections.now_ms()
    for position, ident in enumerate(ids, 1):
        s.execute(update(sec).where(sec.principal == OWNER, sec.id == ident).values(
            position=position, updated_at=at))  # fmt: skip
