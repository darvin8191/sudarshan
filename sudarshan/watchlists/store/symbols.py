"""The owner's flag and note on a symbol, and the flag names (M8.6, W9).

One ``wl_symbol`` row per symbol across every list; a row with neither a flag nor a note is deleted,
and a contract that has left the master keeps its row (it reads as expired at read, and only
``clear`` removes it). A write names the version it was decided on: ``wl_symbol.version`` (a counter) for a
symbol (None for a symbol with no row), ``wl_meta.flags_version`` for the flag names. Each is
checked by a compare-and-set in SQL as the transaction's first statement, a guarded UPDATE that
changes nothing but takes SQLite's write lock at once (the reason is in ``store/sections.py``), so
what is read after it cannot have moved. Nothing reads a version and writes it back. The functions
run inside the caller's ``with open_session() as s, s.begin():``.
"""

from __future__ import annotations

from sqlalchemy import delete, select, update

from sudarshan.watchlists import schema
from sudarshan.watchlists.ops.state import DEFAULT_FLAG_NAMES
from sudarshan.watchlists.schema import OWNER
from sudarshan.watchlists.store.sections import Stale, now_ms

SYM = schema.SymbolRow


def _key(exchange: str, symbol: str) -> tuple:
    return (SYM.principal == OWNER, SYM.exchange == exchange, SYM.symbol == symbol)


def find(s, exchange: str, symbol: str):
    """The row of a symbol, or None."""
    return s.scalars(select(SYM).where(*_key(exchange, symbol))).first()


def claim(s, exchange: str, symbol: str, held: int | None):
    """Check ``held`` against the symbol's ``version`` (None: it has no row); the row now, or
    None. Raises ``Stale`` and the caller's transaction rolls back."""
    cond = _key(exchange, symbol) + (() if held is None else (SYM.version == held,))
    done = s.execute(update(SYM).where(*cond).values(version=SYM.version))
    if (done.rowcount == 1) != (held is not None):
        raise Stale
    return None if held is None else find(s, exchange, symbol)


def write(s, exchange: str, symbol: str, old, held: int | None, flag, note) -> int | None:
    """Make the symbol's row what ``flag`` and ``note`` say, from ``old`` (the claimed row); the
    new version, or None when neither is left and the row is gone."""
    if flag is None and note is None:
        if old is not None:
            s.execute(delete(SYM).where(*_key(exchange, symbol)))
        return None
    at = now_ms()
    version = (held or 0) + 1
    flag_at = old.flag_at if old is not None and old.flag == flag else (at if flag else None)
    note_at = old.note_at if old is not None and old.note == note else (at if note else None)
    if old is None:
        s.add(SYM(exchange=exchange, symbol=symbol, flag=flag, flag_at=flag_at, note=note,
                  note_at=note_at, version=version, updated_at=at))  # fmt: skip
    else:
        s.execute(update(SYM).where(*_key(exchange, symbol)).values(
            flag=flag, flag_at=flag_at, note=note, note_at=note_at, version=version, updated_at=at))  # fmt: skip
    return version


def flagged(s, mark: int) -> list:
    """The rows carrying a flag, newest flag first."""
    return list(s.execute(select(SYM.exchange, SYM.symbol, SYM.note).where(
        SYM.principal == OWNER, SYM.flag == mark).order_by(
        SYM.flag_at.desc(), SYM.exchange, SYM.symbol)).all())  # fmt: skip


def everything(s) -> list:
    """Every symbol row: ``(exchange, symbol, flag)``."""
    return list(s.execute(select(SYM.exchange, SYM.symbol, SYM.flag).where(
        SYM.principal == OWNER)).all())  # fmt: skip


def remove(s, keys: list[tuple[str, str]]) -> None:
    for exchange, symbol in keys:
        s.execute(delete(SYM).where(*_key(exchange, symbol)))


def flag_names(s) -> list[dict]:
    """The five flags with their names: a renamed one from the store, the rest by default."""
    row = schema.FlagNameRow
    own = dict(s.execute(select(row.mark, row.name).where(row.principal == OWNER)).all())
    return [{"mark": n, "name": own.get(n, d)} for n, d in enumerate(DEFAULT_FLAG_NAMES, 1)]


def rename(s, pairs: list[tuple[int, str]]) -> None:
    """Store each name given; a name that is the flag's default is not stored (absent reads as the
    default, and is never written as if chosen)."""
    row = schema.FlagNameRow
    for mark, name in pairs:
        s.execute(delete(row).where(row.principal == OWNER, row.mark == mark))
        if name != DEFAULT_FLAG_NAMES[mark - 1]:
            s.add(row(mark=mark, name=name))
