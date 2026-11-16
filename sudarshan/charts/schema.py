"""The chart documents' table, schema 1, in ``db/sudarshan_charts.db`` (M4.18, T7a).

One row per document a device keeps for the owner and, with ``SUDARSHAN_CHARTS_SYNC`` on, keeps
here as the copy of record: ``chart_doc(user, kind, key, revision, body, updated_ms)``.

- ``kind`` is one of ``KINDS``: ``drawings`` (one instrument's drawings, ``key`` the engine's
  instrument key, ``NSE:INFY``), ``phone_set`` (the phone chart's indicator set), ``workspace``
  (the terminal's catalogue of saved layouts and indicator templates, ``key`` its namespace) and
  ``template``, which no screen writes yet: the terminal's indicator templates travel inside its
  workspace catalogue, which the engine stores as one revision-checked document.
- ``body`` is the engine's own validated JSON as compact text, at most ``MAX_BODY_BYTES`` (the
  engine's limit on a document); the check below holds it in the file too.
- ``revision`` is what a write names to replace: it moves on every write, never back, and is
  never below the write's epoch milliseconds, so a file put back by a rollback cannot hand out a
  number a device already holds for other content.

A file of its own, so no upstream table changes and a rebase never meets it, and in ``db/`` so a
deploy's backup and a rollback copy it with ``openalgo.db``. ``SUDARSHAN_CHARTS_DB`` points
elsewhere (tests, a dev checkout). This module imports SQLAlchemy only, so
``upgrade/migrate_sudarshan_charts.py`` can run where OpenAlgo's own packages cannot be imported.
Nothing happens at import: no file is opened and no engine is made.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import BigInteger, CheckConstraint, Column, Integer, String, Text, inspect, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import declarative_base
from sqlalchemy.schema import CreateColumn, CreateTable

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_FILENAME = "sudarshan_charts.db"
PATH_ENV = "SUDARSHAN_CHARTS_DB"
#: The schema this code writes. A file whose ``chart_meta.schema`` is higher is never touched.
SCHEMA_VERSION = 1
KINDS = ("drawings", "phone_set", "workspace", "template")
#: The engine's limit on one document (openalgo-charts' workspace tier refuses a larger one).
MAX_BODY_BYTES = 5 * 1024 * 1024
MAX_KEY_CHARS = 200

Base = declarative_base()


def default_path() -> Path:
    """``db/sudarshan_charts.db`` in the checkout, or ``SUDARSHAN_CHARTS_DB`` when set (a relative
    value is taken from the checkout, so the migration, run from upgrade/, finds the same file)."""
    override = os.environ.get(PATH_ENV)
    if not override:
        return REPO_ROOT / "db" / DB_FILENAME
    path = Path(override)
    return path if path.is_absolute() else REPO_ROOT / path


class ChartDoc(Base):
    """One document of one kind under one key, for one OpenAlgo user (only the owner writes)."""

    __tablename__ = "chart_doc"
    user = Column(String(80), primary_key=True)
    kind = Column(String(16), primary_key=True)
    key = Column(String(MAX_KEY_CHARS), primary_key=True)
    revision = Column(BigInteger, nullable=False)
    body = Column(Text, nullable=False)
    updated_ms = Column(BigInteger, nullable=False)

    __table_args__ = (
        CheckConstraint(f"kind IN ({', '.join(repr(k) for k in KINDS)})", name="ck_chart_doc_kind"),
        CheckConstraint("revision >= 1", name="ck_chart_doc_revision"),
        CheckConstraint(f"length(key) BETWEEN 1 AND {MAX_KEY_CHARS}", name="ck_chart_doc_key"),
        CheckConstraint(
            f"length(CAST(body AS BLOB)) <= {MAX_BODY_BYTES}", name="ck_chart_doc_body"
        ),
    )


class MetaRow(Base):
    """``schema``: the schema this file was made or completed to."""

    __tablename__ = "chart_meta"
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


def stored_schema(engine) -> int | None:
    """The file's ``chart_meta.schema``: None when it has no such row (a file this code has not
    made yet); a value that is not a whole number reads as a very high one, so it is refused as a
    newer file rather than overwritten. Reads only."""
    if "chart_meta" not in inspect(engine).get_table_names():
        return None
    with engine.connect() as connection:
        found = connection.execute(
            text("SELECT value FROM chart_meta WHERE name = 'schema'")
        ).first()
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
            text("INSERT OR IGNORE INTO chart_meta (name, value) VALUES ('schema', :v)"),
            {"v": str(SCHEMA_VERSION)},
        )
        connection.execute(
            text(
                "UPDATE chart_meta SET value = :v WHERE name = 'schema'"
                " AND CAST(value AS INTEGER) < :n AND value GLOB '[0-9]*'"
            ),
            {"v": str(SCHEMA_VERSION), "n": SCHEMA_VERSION},
        )


def create_missing(engine) -> None:
    """Every table and column, each only when missing, then the ``schema`` row. Two first uses at
    once (the app and the migration) never trip over each other. A caller checks
    ``stored_schema`` first, so a newer file is never reached here."""
    create_tables(engine)
    add_missing_columns(engine)
    record_schema(engine)
