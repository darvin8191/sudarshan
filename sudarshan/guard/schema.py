"""The risk database's tables, in ``db/sudarshan_risk.db`` (M5.13, M5.14, M20.7).

A file of its own, so no upstream table changes and a rebase never meets it, and in ``db/`` so the
deploy's backup loop copies it with OpenAlgo's databases. ``SUDARSHAN_RISK_DB`` points elsewhere
(tests, a dev checkout).

This module imports SQLAlchemy only: upgrade/migrate_sudarshan_risk.py creates these tables from
it, and a migration must still run when OpenAlgo's own packages cannot be imported
(upgrade/_pragmas.py). Every column that cannot be NULL carries a server default, so a column
added in a later release can be added to an existing file in place.
"""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import Boolean, Column, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import declarative_base
from sqlalchemy.schema import CreateIndex, CreateTable

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_FILENAME = "sudarshan_risk.db"
PATH_ENV = "SUDARSHAN_RISK_DB"

#: Every guard ships off, and these are what a guard's fields hold until the owner changes them:
#: the values the Risk page prefills when he turns one on (amendment R4). The daily loss limit
#: has no default: SUDARSHAN never picks that figure for him.
DEFAULTS = {
    "daily_loss_limit": None,
    "band_on": False,
    "band_opt_pct": 20.0,
    "band_opt_min_rs": 2.0,
    "band_fut_pct": 1.0,
    "band_cash_pct": 5.0,
    "band_both_sides": False,
    "size_on": False,
    "max_lots_fo": 2,
    "max_qty_cash": 1000,
    "burst_on": False,
    "burst_n": 10,
    "burst_window_s": 60.0,
    "confirm_phrase": "STOP",
    "revoke_mcp": True,
}

Base = declarative_base()


def default_path() -> Path:
    """``db/sudarshan_risk.db`` in the checkout, or ``SUDARSHAN_RISK_DB`` when it is set.

    A relative ``SUDARSHAN_RISK_DB`` is taken from the checkout, as the app runs there, so the
    migration (run from upgrade/) finds the same file.
    """
    override = os.environ.get(PATH_ENV)
    if not override:
        return REPO_ROOT / "db" / DB_FILENAME
    path = Path(override)
    return path if path.is_absolute() else REPO_ROOT / path


def _flag(name: str) -> Column:
    return Column(name, Boolean, nullable=False, server_default="1" if DEFAULTS[name] else "0")


def _number(name: str, kind=Float) -> Column:
    return Column(name, kind, nullable=False, server_default=str(DEFAULTS[name]))


class SettingsRow(Base):
    """The owner's risk settings: one row, id 1."""

    __tablename__ = "risk_settings"
    id = Column(Integer, primary_key=True)
    daily_loss_limit = Column(Float)  # rupees; NULL: the daily loss guard is off
    band_on = _flag("band_on")
    band_opt_pct = _number("band_opt_pct")
    band_opt_min_rs = _number("band_opt_min_rs")
    band_fut_pct = _number("band_fut_pct")
    band_cash_pct = _number("band_cash_pct")
    band_both_sides = _flag("band_both_sides")
    size_on = _flag("size_on")
    max_lots_fo = _number("max_lots_fo", Integer)
    max_qty_cash = _number("max_qty_cash", Integer)
    burst_on = _flag("burst_on")
    burst_n = _number("burst_n", Integer)
    burst_window_s = _number("burst_window_s")
    confirm_phrase = Column(
        String(40), nullable=False, server_default=str(DEFAULTS["confirm_phrase"])
    )
    revoke_mcp = _flag("revoke_mcp")
    updated_at = Column(DateTime)
    updated_by = Column(String(80))


class StateRow(Base):
    """The kill switch: one row, id 1. Written before any broker call it leads to."""

    __tablename__ = "risk_state"
    id = Column(Integer, primary_key=True)
    ks_on = Column(Boolean, nullable=False, server_default="0")
    ks_since = Column(DateTime)
    ks_surface = Column(String(24))  # app, telegram or api: where it was turned on
    ks_actor = Column(String(80))
    ks_activation_id = Column(String(40))
    ks_exit_all = Column(Boolean, nullable=False, server_default="0")
    ks_running = Column(Boolean, nullable=False, server_default="0")  # its steps are running
    ks_report = Column(Text)  # JSON: what each step did
    agent_engaged_by_us = Column(Boolean, nullable=False, server_default="0")
    updated_at = Column(DateTime)


class DailyLockRow(Base):
    """A daily loss limit reached: new risk stays refused in that pipe for the rest of the day."""

    __tablename__ = "daily_locks"
    pipe = Column(String(8), primary_key=True)
    ist_date = Column(String(10), primary_key=True)  # YYYY-MM-DD in India's time
    at = Column(DateTime, nullable=False)
    figure = Column(Float, nullable=False)  # the day's P&L when it was reached
    loss_limit = Column(Float, nullable=False)


class AuditRow(Base):
    """What the risk layer did and refused, newest last; refusals merged per minute."""

    __tablename__ = "risk_audit"
    id = Column(Integer, primary_key=True, autoincrement=True)
    at = Column(DateTime, nullable=False)
    last_at = Column(DateTime, nullable=False)
    kind = Column(String(24), nullable=False)
    pipe = Column(String(8))
    surface = Column(String(120))
    actor = Column(String(80))
    activation_id = Column(String(40))
    count = Column(Integer, nullable=False, server_default="1")
    merge_key = Column(String(200))
    summary = Column(Text, nullable=False, server_default="")
    detail = Column(Text)  # JSON

    __table_args__ = (
        Index("ix_risk_audit_at", "at"),
        Index("ix_risk_audit_merge", "merge_key", "last_at"),
    )


TABLES = tuple(Base.metadata.sorted_tables)


def create_missing(engine) -> None:
    """Every table and index in this file, each made only when it is missing, one statement
    apiece. Two first uses at once (two threads, or the app and the migration) never trip over
    each other: a check followed by a CREATE lets another creator in between, on any table."""
    with engine.begin() as connection:
        for table in TABLES:
            connection.execute(CreateTable(table, if_not_exists=True))
            for index in sorted(table.indexes, key=lambda index: index.name):
                connection.execute(CreateIndex(index, if_not_exists=True))


#: The tables that hold one row, id 1, which must exist for the app to read them.
SINGLE_ROW_TABLES = (SettingsRow.__tablename__, StateRow.__tablename__)
