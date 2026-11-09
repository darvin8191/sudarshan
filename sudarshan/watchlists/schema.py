"""The watchlist store's tables, schema 1, in ``db/sudarshan_watchlists.db`` (M8.4).

A file of its own, so no upstream table changes and a rebase never meets it, and in ``db/`` so a
deploy's backup and a rollback copy it together with ``openalgo.db``: every row here is keyed to an
OpenAlgo list id, and the two files must come back as one. ``SUDARSHAN_WATCHLISTS_DB`` points
elsewhere (tests, a dev checkout).

Schema 1 is frozen here (design 2.2). Times are epoch milliseconds, prices are paise, and every
table carries ``principal`` ("owner" in v2). Limits that need a count (20 sections or follow rows a
list) are the store's to enforce; the checks below hold the ranges and the enums, so a bug in a
writer cannot store a value no reader understands.

This module imports SQLAlchemy only: ``upgrade/migrate_sudarshan_watchlists.py`` creates the tables
from it and must still run when OpenAlgo's own packages cannot be imported. Nothing happens at
import: no file is opened and no engine is made.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    inspect,
    text,
)
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import declarative_base
from sqlalchemy.schema import CreateColumn, CreateIndex, CreateTable

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_FILENAME = "sudarshan_watchlists.db"
PATH_ENV = "SUDARSHAN_WATCHLISTS_DB"  # the same name as contract.DB_ENV; a test holds them equal
#: The schema this code writes. A file whose ``wl_meta.schema`` is higher is never touched.
SCHEMA_VERSION = 1
OWNER = "owner"

Base = declarative_base()


def default_path() -> Path:
    """``db/sudarshan_watchlists.db`` in the checkout, or ``SUDARSHAN_WATCHLISTS_DB`` when set.

    A relative value is taken from the checkout, as the app runs there, so the migration (run
    from upgrade/) finds the same file.
    """
    override = os.environ.get(PATH_ENV)
    if not override:
        return REPO_ROOT / "db" / DB_FILENAME
    path = Path(override)
    return path if path.is_absolute() else REPO_ROOT / path


def _principal() -> Column:
    return Column(String(16), primary_key=True, server_default=OWNER)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class ListRow(Base):
    """One OpenAlgo list this overlay knows. ``oa_created_at`` is OpenAlgo's own ``created_at``
    text: SQLite may hand a deleted list's id to a new list, so rows whose value differs from the
    live list's are ignored and pruned. ``version`` guards the list's sections and follow rows."""

    __tablename__ = "wl_list"
    principal = _principal()
    list_id = Column(Integer, primary_key=True, autoincrement=False)
    oa_created_at = Column(String(40), nullable=False)
    version = Column(Integer, nullable=False, server_default="1")
    updated_at = Column(BigInteger, nullable=False)


class SectionRow(Base):
    __tablename__ = "wl_section"
    id = Column(Integer, primary_key=True, autoincrement=True)
    principal = Column(String(16), nullable=False, server_default=OWNER)
    list_id = Column(Integer, nullable=False)
    name = Column(String(40), nullable=False)
    mark = Column(Integer, nullable=False, server_default="0")  # 0 none, 1 to 5 a colour
    position = Column(Integer, nullable=False)
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        UniqueConstraint("principal", "list_id", "name", name="uq_wl_section_name"),
        CheckConstraint("length(name) BETWEEN 1 AND 40", name="ck_wl_section_name"),
        CheckConstraint("mark BETWEEN 0 AND 5", name="ck_wl_section_mark"),
    )


class SectionItemRow(Base):
    """A symbol's section. A symbol with no row here is in "no section"."""

    __tablename__ = "wl_section_item"
    principal = _principal()
    list_id = Column(Integer, primary_key=True, autoincrement=False)
    exchange = Column(String(16), primary_key=True)
    symbol = Column(String(80), primary_key=True)
    section_id = Column(Integer, nullable=False)

    __table_args__ = (Index("ix_wl_section_item_section", "section_id"),)


FOLLOW_KINDS = ("atm_option", "month_future", "strike_pair")
UNDERLYING_EXCHANGES = ("NSE_INDEX", "NSE", "BSE_INDEX", "BSE")
EXPIRY_RULES = ("nearest", "next", "month", "next_month")
STRIKE_RULES = ("atm", "fixed")


class FollowRow(Base):
    """A row that follows the money: an ATM option, the month's future or a CE and PE pair."""

    __tablename__ = "wl_row"
    id = Column(Integer, primary_key=True, autoincrement=True)
    principal = Column(String(16), nullable=False, server_default=OWNER)
    list_id = Column(Integer, nullable=False)
    section_id = Column(Integer)
    position = Column(Integer, nullable=False)
    kind = Column(String(16), nullable=False)
    underlying = Column(String(80), nullable=False)
    underlying_exchange = Column(String(16), nullable=False)
    expiry_rule = Column(String(16), nullable=False)
    strike_rule = Column(String(8), nullable=False, server_default="atm")
    offset = Column("offset", Integer, nullable=False, server_default="0")
    strike_paise = Column(BigInteger)  # a fixed strike only
    opt_right = Column(String(2))  # CE, PE, or NULL for a future or a pair
    created_at = Column(BigInteger, nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        Index("ix_wl_row_list", "principal", "list_id"),
        CheckConstraint(_in("kind", FOLLOW_KINDS), name="ck_wl_row_kind"),
        CheckConstraint(_in("underlying_exchange", UNDERLYING_EXCHANGES), name="ck_wl_row_exch"),
        CheckConstraint(_in("expiry_rule", EXPIRY_RULES), name="ck_wl_row_expiry"),
        CheckConstraint(_in("strike_rule", STRIKE_RULES), name="ck_wl_row_strike"),
        CheckConstraint('"offset" BETWEEN -10 AND 10', name="ck_wl_row_offset"),
        CheckConstraint("opt_right IS NULL OR opt_right IN ('CE', 'PE')", name="ck_wl_row_right"),
    )


class SymbolRow(Base):
    """The owner's flag and note on one symbol, one row across every list. A row with neither is
    deleted. A contract that has left the master keeps its row: it is the owner's words."""

    __tablename__ = "wl_symbol"
    principal = _principal()
    exchange = Column(String(16), primary_key=True)
    symbol = Column(String(80), primary_key=True)
    flag = Column(Integer)
    flag_at = Column(BigInteger)
    note = Column(Text)
    note_at = Column(BigInteger)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        CheckConstraint("flag IS NULL OR flag BETWEEN 1 AND 5", name="ck_wl_symbol_flag"),
        CheckConstraint("note IS NULL OR length(note) <= 500", name="ck_wl_symbol_note"),
    )


class FlagNameRow(Base):
    """A flag the owner renamed. An absent row reads as the default name and is never written as
    if chosen."""

    __tablename__ = "wl_flag"
    principal = _principal()
    mark = Column(Integer, primary_key=True, autoincrement=False)
    name = Column(String(24), nullable=False)

    __table_args__ = (
        CheckConstraint("mark BETWEEN 1 AND 5", name="ck_wl_flag_mark"),
        CheckConstraint("length(name) BETWEEN 1 AND 24", name="ck_wl_flag_name"),
    )


class FavouriteRow(Base):
    """One of the seven favourite slots. ``ref`` is ``list:<id>``, ``preset:<id>``,
    ``ranked:<source>:<asset>:<category>`` or ``flag:<n>``."""

    __tablename__ = "wl_favourite"
    principal = _principal()
    slot = Column(Integer, primary_key=True, autoincrement=False)
    ref = Column(String(80), nullable=False)
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (CheckConstraint("slot BETWEEN 1 AND 7", name="ck_wl_favourite_slot"),)


SORTS = ("list", "change", "price", "name", "exchange")
BASES = ("close", "open")


class PrefRow(Base):
    """How the owner views one ref: sort, direction, change basis and the holdings line."""

    __tablename__ = "wl_pref"
    principal = _principal()
    ref = Column(String(80), primary_key=True)
    sort = Column(String(12), nullable=False, server_default="list")
    descending = Column(Integer, nullable=False, server_default="0")
    basis = Column(String(8), nullable=False, server_default="close")
    held = Column(Integer, nullable=False, server_default="0")
    updated_at = Column(BigInteger, nullable=False)

    __table_args__ = (
        CheckConstraint(_in("sort", SORTS), name="ck_wl_pref_sort"),
        CheckConstraint(_in("basis", BASES), name="ck_wl_pref_basis"),
        CheckConstraint("descending IN (0, 1) AND held IN (0, 1)", name="ck_wl_pref_bool"),
    )


class MetaRow(Base):
    """``schema`` (this file's schema), ``favourites_version`` and ``flags_version``. Only
    ``schema`` is written by creation; a missing version reads as 0."""

    __tablename__ = "wl_meta"
    name = Column(String(40), primary_key=True)
    value = Column(String(80), nullable=False)


TABLES = tuple(Base.metadata.sorted_tables)


def create_tables(engine) -> None:
    """Every table, each made only when it is missing, one statement apiece."""
    with engine.begin() as connection:
        for table in TABLES:
            connection.execute(CreateTable(table, if_not_exists=True))


def add_missing_columns(engine) -> list[tuple[str, str]]:
    """Add each column a table already in the file lacks, returning the (table, column) pairs.
    A key column cannot be added and is left. A column another caller added first is no error."""
    added = []
    inspector = inspect(engine)
    for table in TABLES:
        if table.name not in inspector.get_table_names():
            continue
        have = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in have or column.primary_key:
                continue
            ddl = CreateColumn(column).compile(dialect=engine.dialect)
            try:
                with engine.begin() as connection:
                    connection.exec_driver_sql(f"ALTER TABLE {table.name} ADD COLUMN {ddl}")
            except OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
            added.append((table.name, column.name))
    return added


def create_indexes(engine) -> None:
    """Every index, each only when missing; run after the columns an index covers exist."""
    with engine.begin() as connection:
        for table in TABLES:
            for index in sorted(table.indexes, key=lambda index: index.name):
                connection.execute(CreateIndex(index, if_not_exists=True))


def stored_schema(engine) -> int | None:
    """The file's ``wl_meta.schema``: None when the file has no such row (a file this code has not
    made yet); a value that is not a whole number reads as a very high one, so it is refused as a
    newer file rather than overwritten. Reads only."""
    if "wl_meta" not in inspect(engine).get_table_names():
        return None
    with engine.connect() as connection:
        found = connection.execute(text("SELECT value FROM wl_meta WHERE name = 'schema'")).first()
    if found is None:
        return None
    try:
        return int(found[0])
    except (TypeError, ValueError):
        return 10**9


def record_schema(engine) -> None:
    """Write ``schema`` when it is absent, and raise it when it is lower; never lower it, never
    touch a value above this code's (a caller refuses a newer file first)."""
    with engine.begin() as connection:
        connection.execute(
            text("INSERT OR IGNORE INTO wl_meta (name, value) VALUES ('schema', :v)"),
            {"v": str(SCHEMA_VERSION)},
        )
        connection.execute(
            text(
                "UPDATE wl_meta SET value = :v WHERE name = 'schema'"
                " AND CAST(value AS INTEGER) < :n AND value GLOB '[0-9]*'"
            ),
            {"v": str(SCHEMA_VERSION), "n": SCHEMA_VERSION},
        )


def create_missing(engine) -> None:
    """Every table, column and index, each only when missing, then the ``schema`` row. Two first
    uses at once (the app and the migration) never trip over each other. A caller checks
    ``stored_schema`` first, so a newer file is never reached here."""
    create_tables(engine)
    add_missing_columns(engine)
    create_indexes(engine)
    record_schema(engine)
