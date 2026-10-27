#!/usr/bin/env python3
"""
Migration: SUDARSHAN risk layer (kill switch and order guards, M5.13)

Creates db/sudarshan_risk.db and its four tables, from the ORM in sudarshan/guard/schema.py:

- risk_settings  the owner's risk settings, one row; every guard off
- risk_state     the kill switch, one row; off
- daily_locks    a daily loss limit reached, per pipe and day
- risk_audit     what the risk layer did and refused

A file of its own, so no OpenAlgo table changes. The app also creates what is missing when it
first opens the file; this script is what reaches a server that upgrades, and it is a required
migration: if it fails, migrate_all.py fails and the deploy rolls back.

Idempotent and safe to re-run. A table that exists keeps its rows; a column or index a later
release adds is added in place; a one-row table gets its default row only when the row is
missing, so a setting the owner chose is never changed. The DDL comes from the ORM metadata, so
the migration cannot disagree with the models it creates.

Usage:
    cd upgrade
    uv run migrate_sudarshan_risk.py           # Apply
    uv run migrate_sudarshan_risk.py --status  # Report what would change, change nothing
"""

import argparse
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# Register the app's SQLite pragmas on this process's engines (GitHub issue #1726).
import _pragmas  # noqa: F401,E402
from sqlalchemy import create_engine, inspect, text  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402
from sqlalchemy.schema import CreateColumn  # noqa: E402


def risk_db_path():
    """The file the app opens: db/sudarshan_risk.db, or SUDARSHAN_RISK_DB from .env or the
    environment (an environment value wins, as python-dotenv never overrides one)."""
    from dotenv import load_dotenv

    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    from sudarshan.guard.schema import default_path

    return default_path()


def _shown(path):
    """The file as a path in the checkout; never a path outside it (the output may be shared)."""
    path = os.path.abspath(path)
    if path.startswith(PROJECT_ROOT + os.sep):
        return os.path.relpath(path, PROJECT_ROOT)
    return "the file named by SUDARSHAN_RISK_DB"


def _schema():
    from sudarshan.guard import schema

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


def missing_rows(engine):
    """The one-row tables that exist without their row (id 1)."""
    present = set(inspect(engine).get_table_names())
    missing = []
    with engine.connect() as connection:
        for name in _schema().SINGLE_ROW_TABLES:
            if name in present:
                found = connection.execute(text(f"SELECT 1 FROM {name} WHERE id = 1")).first()
                if found is None:
                    missing.append(name)
    return missing


def status(path):
    """Report what would change, without changing anything (not even creating the file)."""
    print("\nSUDARSHAN risk database status")
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
        rows = missing_rows(engine)
        for table in _schema().TABLES:
            print(f"  {table.name:<26} {'MISSING' if table.name in tables else 'present'}")
        for table, column in columns:
            print(f"  {table.name}.{column.name:<16} MISSING")
        for index in indexes:
            print(f"  index {index.name:<20} MISSING")
        for name in rows:
            print(f"  {name} default row      MISSING")
        print("-" * 46)
        if tables or columns or indexes or rows:
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
        _schema().create_missing(engine)
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
            with engine.begin() as connection:
                index.create(bind=connection)
            print(f"  [OK] Added index {index.name}")
        for name in missing_rows(engine):
            # Every value the model defaults, written out: a column an earlier build created
            # without a default would otherwise refuse the row (every guard off).
            table = _schema().Base.metadata.tables[name]
            have = {column["name"] for column in inspect(engine).get_columns(name)}
            values = {"id": 1}
            for column in table.columns:
                if column.name in have and column.server_default is not None:
                    values[column.name] = column.server_default.arg
            names = ", ".join(values)
            marks = ", ".join(f":{key}" for key in values)
            with engine.begin() as connection:
                connection.execute(text(f"INSERT INTO {name} ({names}) VALUES ({marks})"), values)
            print(f"  [OK] Added the default row of {name}")
        left = missing_tables(engine) or missing_columns(engine) or missing_rows(engine)
        if left:
            print(f"  [FAIL] Still missing after the migration: {left}")
            return False
        return True
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="SUDARSHAN risk database migration")
    parser.add_argument(
        "--status", action="store_true", help="Report what would change without changing anything"
    )
    args = parser.parse_args()
    print("\nSUDARSHAN Risk Layer Migration")
    print("-" * 46)
    try:
        path = str(risk_db_path())
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
