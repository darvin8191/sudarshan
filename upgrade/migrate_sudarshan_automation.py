#!/usr/bin/env python3
"""
Migration: SUDARSHAN automation (TradingView alerts in front of the smart order, M12.1)

Creates db/sudarshan_automation.db and its three tables, from the ORM in
sudarshan/automation/schema.py:

- auto_strategy  one row per strategy the owner set up (its link's token only as a hash)
- auto_state     what the intake last did for each strategy
- auto_alert     one line per alert received

and their indexes. Two of the indexes are claims the order path rests on: one alert is handled
once (auto_alert: strategy_id, claim), and one strategy owns a contract and product in a mode
(auto_strategy: mode, contract_key, product, for strategies that are not Stopped).

A file of its own, so no OpenAlgo table changes. The app also creates what is missing when it
first opens the file; this script is what reaches a server that upgrades. It is a required
migration: if it fails, migrate_all.py fails and the deploy rolls back.

Idempotent and safe to re-run. A table that exists keeps its rows; a column or index a later
release adds is added in place. A unique index that the rows already in the file break is
reported and nothing is deleted to make room for it. The DDL comes from the ORM metadata, so the
migration cannot disagree with the models it creates.

Usage:
    cd upgrade
    uv run migrate_sudarshan_automation.py           # Apply
    uv run migrate_sudarshan_automation.py --status  # Report what would change, change nothing
"""

import argparse
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# Register the app's SQLite pragmas on this process's engines (GitHub issue #1726).
import _pragmas  # noqa: F401,E402
from sqlalchemy import create_engine, inspect  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402
from sqlalchemy.schema import CreateColumn  # noqa: E402


def automation_db_path():
    """The file the app opens: db/sudarshan_automation.db, or SUDARSHAN_AUTOMATION_DB from .env or the
    environment (an environment value wins, as python-dotenv never overrides one)."""
    from dotenv import load_dotenv

    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    from sudarshan.automation.schema import default_path

    return default_path()


def _shown(path):
    """The file as a path in the checkout; never a path outside it (the output may be shared)."""
    path = os.path.abspath(path)
    if path.startswith(PROJECT_ROOT + os.sep):
        return os.path.relpath(path, PROJECT_ROOT)
    return "the file named by SUDARSHAN_AUTOMATION_DB"


def _schema():
    from sudarshan.automation import schema

    return schema


def missing_tables(engine):
    present = set(inspect(engine).get_table_names())
    return [table.name for table in _schema().TABLES if table.name not in present]


def missing_columns(engine):
    """(table, column) pairs a present table lacks."""
    inspector = inspect(engine)
    present = set(inspector.get_table_names())
    missing = []
    for table in _schema().TABLES:
        if table.name not in present:
            continue
        have = {column["name"] for column in inspector.get_columns(table.name)}
        missing.extend((table, column) for column in table.columns if column.name not in have)
    return missing


def missing_indexes(engine):
    inspector = inspect(engine)
    present = set(inspector.get_table_names())
    missing = []
    for table in _schema().TABLES:
        if table.name not in present:
            continue
        have = {index["name"] for index in inspector.get_indexes(table.name)}
        missing.extend(index for index in table.indexes if index.name not in have)
    return missing


def status(path):
    """Report what would change, without changing anything (not even creating the file)."""
    print("\nSUDARSHAN automation database status")
    print("-" * 46)
    print(f"File: {_shown(path)}")
    if not os.path.exists(path):
        print(
            f"  file                       MISSING (would create it and {len(_schema().TABLES)} tables)"
        )
        print("-" * 46)
        print("Migration needed.")
        return False
    engine = create_engine(f"sqlite:///{path}", poolclass=NullPool)
    try:
        tables = missing_tables(engine)
        columns = missing_columns(engine)
        indexes = missing_indexes(engine)
        for table in _schema().TABLES:
            print(f"  {table.name:<26} {'MISSING' if table.name in tables else 'present'}")
        for table, column in columns:
            print(f"  {table.name}.{column.name:<16} MISSING")
        for index in indexes:
            print(f"  index {index.name:<20} MISSING")
        print("-" * 46)
        if tables or columns or indexes:
            print("Migration needed.")
            return False
        print("Up to date. Nothing to do.")
        return True
    finally:
        engine.dispose()


def apply(path):
    """Create whatever is missing. What exists keeps its rows and values."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    engine = create_engine(f"sqlite:///{path}", poolclass=NullPool)
    try:
        absent = missing_tables(engine)
        # Each only when missing: an interrupted run completes cleanly, and the app's own first
        # use at the same moment is no failure.
        _schema().create_tables(engine)  # the columns, then the indexes that cover them
        for name in absent:
            print(f"  [OK] Created {name}")
        for table, column in missing_columns(engine):
            if column.primary_key:
                print(f"  [FAIL] {table.name} has no {column.name} key column; it cannot be added")
                return False
            ddl = CreateColumn(column).compile(dialect=engine.dialect)
            with engine.begin() as connection:
                connection.exec_driver_sql(f"ALTER TABLE {table.name} ADD COLUMN {ddl}")
            print(f"  [OK] Added {table.name}.{column.name}")
        for index in missing_indexes(engine):
            if index.table.name not in absent:
                print(f"  [OK] Added index {index.name}")
        try:
            _schema().create_indexes(engine)  # each only when missing
        except IntegrityError:
            # Rows already in the file break a unique index (two lines for one alert, or two
            # strategies that are not Stopped on one contract). Nothing is deleted to make room.
            for index in missing_indexes(engine):
                print(f"  [FAIL] {index.name} cannot be made: rows in {index.table.name} repeat")
            return False
        left = missing_tables(engine) or missing_columns(engine) or missing_indexes(engine)
        if left:
            print(f"  [FAIL] Still missing after the migration: {left}")
            return False
        return True
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="SUDARSHAN automation database migration")
    parser.add_argument(
        "--status", action="store_true", help="Report what would change without changing anything"
    )
    args = parser.parse_args()
    print("\nSUDARSHAN Automation Migration")
    print("-" * 46)
    try:
        path = str(automation_db_path())
        if args.status:
            status(path)
            return 0
        print(f"File: {_shown(path)}")
        print("\nApplying migration...")
        ok = apply(path)
        print("-" * 46)
        print("Migration complete." if ok else "Migration failed. See the messages above.")
        return 0 if ok else 1
    except Exception as exc:
        print(f"\n[ERROR] Migration failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
