"""The automation database's tables, in ``db/sudarshan_automation.db`` (M12.1), schema 1.

A file of its own, so no upstream table changes and a rebase never meets it, and in ``db/`` so the
deploy's backup loop copies it with OpenAlgo's databases. ``SUDARSHAN_AUTOMATION_DB`` points
elsewhere (tests, a dev checkout).

This module imports SQLAlchemy only: upgrade/migrate_sudarshan_automation.py creates these tables
from it, and a migration must still run when OpenAlgo's own packages cannot be imported. A column
added in a later release must be nullable or carry a server default, so it can be added to an
existing file in place.

Two indexes are claims, not conveniences:

- ``ux_auto_alert_claim`` on ``(strategy_id, claim)``: one alert is handled once. The row is
  written before the order is sent; a second delivery loses the insert.
- ``ux_auto_strategy_contract`` on ``(mode, contract_key, product)`` for strategies that are not
  Stopped: one strategy owns a contract and product in its mode, because a smart order's target is
  the account's position there.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Float,
    Index,
    Integer,
    String,
    Text,
    inspect,
    text,
)
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import declarative_base
from sqlalchemy.schema import CreateColumn, CreateIndex, CreateTable

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_FILENAME = "sudarshan_automation.db"
PATH_ENV = "SUDARSHAN_AUTOMATION_DB"
SCHEMA = 1

Base = declarative_base()


def default_path() -> Path:
    """``db/sudarshan_automation.db`` in the checkout, or ``SUDARSHAN_AUTOMATION_DB`` when set (a
    relative value is taken from the checkout, so the migration finds the app's file)."""
    override = os.environ.get(PATH_ENV)
    if not override:
        return REPO_ROOT / "db" / DB_FILENAME
    path = Path(override)
    return path if path.is_absolute() else REPO_ROOT / path


class StrategyRow(Base):
    __tablename__ = "auto_strategy"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(40), nullable=False)  # the order's strategy label, and the journal's
    token_sha256 = Column(String(64), nullable=False)  # the link's token is kept only as this
    token_tail = Column(String(4), nullable=False, server_default="")
    state = Column(String(12), nullable=False, server_default="stopped")
    stopped_why = Column(String(200))
    mode = Column(String(8), nullable=False, server_default="sandbox")
    chart = Column(String(64))
    kind = Column(String(8), nullable=False, server_default="future")
    exchange = Column(String(12), nullable=False, server_default="")
    underlying = Column(String(40), nullable=False, server_default="")
    expiry_rule = Column(String(12))
    strike_offset = Column(String(8))
    product = Column(String(4), nullable=False, server_default="MIS")
    direction = Column(String(5), nullable=False, server_default="both")
    size_mode = Column(String(6), nullable=False, server_default="fixed")
    lots = Column(Integer)
    quantity = Column(Integer)
    multiplier = Column(Float)
    max_lots = Column(Integer)
    max_age_s = Column(Integer, nullable=False, server_default="30")
    max_alerts_day = Column(Integer, nullable=False, server_default="20")
    roll_on_expiry_day = Column(Boolean, nullable=False, server_default="0")
    no_entry_before_stop_min = Column(Integer, nullable=False, server_default="15")
    contract_key = Column(String(64), nullable=False, server_default="")
    created_ms = Column(BigInteger, nullable=False, server_default="0")
    updated_ms = Column(BigInteger, nullable=False, server_default="0")
    version = Column(Integer, nullable=False, server_default="1")

    __table_args__ = (
        Index("ux_auto_strategy_name", "name", unique=True),
        Index("ux_auto_strategy_token", "token_sha256", unique=True),
        Index(
            "ux_auto_strategy_contract",
            "mode",
            "contract_key",
            "product",
            unique=True,
            sqlite_where=text("state != 'stopped'"),
        ),
    )


class StateRow(Base):
    __tablename__ = "auto_state"
    strategy_id = Column(Integer, primary_key=True)
    last_target = Column(Integer, nullable=False, server_default="0")
    last_fired_s = Column(BigInteger)
    held_exchange = Column(String(12))
    held_symbol = Column(String(64))
    held_side = Column(String(5))
    held_since_ms = Column(BigInteger)
    day = Column(String(10))
    alerts_today = Column(Integer, nullable=False, server_default="0")
    told_stopped_day = Column(String(10))
    version = Column(Integer, nullable=False, server_default="1")


class AlertRow(Base):
    """One line per alert received. Append-only but for the four columns written once when the
    order's answer is in: ``verdict``, ``sentence``, ``order_id``, ``answer_ms``."""

    __tablename__ = "auto_alert"
    id = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id = Column(Integer, nullable=False)
    received_ms = Column(BigInteger, nullable=False)
    fired_s = Column(BigInteger)
    bar_s = Column(BigInteger)
    claim = Column(String(160), nullable=False)
    verdict = Column(String(16), nullable=False)
    sentence = Column(Text, nullable=False, server_default="")
    exchange = Column(String(12))
    symbol = Column(String(64))
    action = Column(String(4))
    quantity = Column(Integer)
    target = Column(Integer)
    mode = Column(String(8))
    order_id = Column(String(64))
    tv_delay_ms = Column(BigInteger)
    answer_ms = Column(BigInteger)
    ip = Column(String(45))
    payload = Column(Text)  # redacted and capped (intake.py)

    __table_args__ = (
        Index("ux_auto_alert_claim", "strategy_id", "claim", unique=True),
        Index("ix_auto_alert_strategy_id", "strategy_id", "id"),
        Index("ix_auto_alert_received", "received_ms"),
    )


TABLES = tuple(Base.metadata.sorted_tables)


def create_tables(engine) -> None:
    """Every table in this file, each made only when it is missing, one statement apiece."""
    with engine.begin() as connection:
        for table in TABLES:
            connection.execute(CreateTable(table, if_not_exists=True))


def add_missing_columns(engine) -> list[tuple[str, str]]:
    """Add each column a table already in the file lacks, and return the (table, column) pairs
    added. A key column cannot be added and is left. Two callers at once are safe."""
    inspector = inspect(engine)
    added = []
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
    """Every index in this file, each made only when it is missing, after the columns it covers."""
    with engine.begin() as connection:
        for table in TABLES:
            for index in sorted(table.indexes, key=lambda index: index.name):
                connection.execute(CreateIndex(index, if_not_exists=True))


def create_missing(engine) -> None:
    """Every table, column and index, each only when missing: safe beside another first use."""
    create_tables(engine)
    add_missing_columns(engine)
    create_indexes(engine)
