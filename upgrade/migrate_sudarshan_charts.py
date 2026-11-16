#!/usr/bin/env python3
"""
Migration: SUDARSHAN chart documents across devices (the store, M4.18)

Creates db/sudarshan_charts.db and its two tables (schema 1), from the ORM in
sudarshan/charts/schema.py: chart_doc (the owner's drawings, the phone chart's indicator set and
the terminal's saved layouts, one row per document) and chart_meta, which records the schema. A
file of its own, so no OpenAlgo table changes.

Listed in migrate_all.py's MIGRATIONS but not in REQUIRED_MIGRATIONS, on purpose: with the file
missing or older, the app's store makes or completes it on first use (sudarshan/charts/store.py
creates whatever is missing, as this script does), so a run that did not happen costs nothing; only
a file the store cannot open, or one from a newer release, is answered in the owner's words, and
every device keeps its own copy meanwhile. The feature is off by default. A failure here is still
reported by migrate_all's summary.

Idempotent and safe to re-run. A table that exists keeps its rows; a column a later release adds is
added in place; the schema row is written only when absent or lower. Nothing is seeded. A file
written by a newer release is refused untouched.

Usage:
    cd upgrade
    uv run migrate_sudarshan_charts.py           # Apply
    uv run migrate_sudarshan_charts.py --status  # Report what would change, change nothing
"""

import argparse
import os
import sqlite3
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

# Register the app's SQLite pragmas on this process's engines (GitHub issue #1726).
import _pragmas  # noqa: F401,E402
from sqlalchemy import create_engine, inspect  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

LINE = "-" * 46


def _newer():
    """The owner's sentence for a file from a newer release, the one the app says."""
    from sudarshan.charts.words import RECORD_NEWER

    return RECORD_NEWER


def charts_db_path():
    """The file the app opens: db/sudarshan_charts.db, or SUDARSHAN_CHARTS_DB from .env or the
    environment (an environment value wins, as python-dotenv never overrides one)."""
    from dotenv import load_dotenv

    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    from sudarshan.charts.schema import default_path

    return default_path()


def _shown(path):
    """The file as a path in the checkout; never a path outside it (the output may be shared)."""
    path = os.path.abspath(path)
    if path.startswith(PROJECT_ROOT + os.sep):
        return os.path.relpath(path, PROJECT_ROOT)
    return "the file named by SUDARSHAN_CHARTS_DB"


def _schema():
    from sudarshan.charts import schema

    return schema


def _read_only_engine(path):
    """An engine that cannot write: --status must leave the file's bytes as they were."""

    def connect():
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=15)

    return create_engine("sqlite://", creator=connect, poolclass=NullPool)


def missing_tables(engine):
    present = set(inspect(engine).get_table_names())
    return [table.name for table in _schema().TABLES if table.name not in present]


def missing_columns(engine):
    """(table, column) pairs a present table lacks."""
    inspector = inspect(engine)
    present = set(inspector.get_table_names())
    missing = []
    for table in _schema().TABLES:
        if table.name in present:
            have = {column["name"] for column in inspector.get_columns(table.name)}
            missing.extend((table, c) for c in table.columns if c.name not in have)
    return missing


def pending(engine):
    """What would change: the lines --status prints, in the order apply does them."""
    code = _schema().SCHEMA_VERSION
    steps = [f"table {name} MISSING" for name in missing_tables(engine)]
    steps += [f"column {t.name}.{c.name} MISSING" for t, c in missing_columns(engine)]
    stored = _schema().stored_schema(engine)
    if stored is None or stored < code:
        steps.append(f"schema record {'MISSING' if stored is None else stored} (to {code})")
    return steps


def status(path):
    """Report what would change, without changing anything (not even creating the file)."""
    code = _schema().SCHEMA_VERSION
    print("\nSUDARSHAN chart store status")
    print(LINE)
    print(f"File: {_shown(path)}")
    print(f"Schema in this release: {code}")
    if not os.path.exists(path):
        print("  file                       MISSING (would create it and its tables)")
        print(LINE)
        print("Migration needed.")
        return False
    engine = _read_only_engine(path)
    try:
        stored = _schema().stored_schema(engine)
        print(f"Schema in the file: {'none' if stored is None else stored}")
        if stored is not None and stored > code:
            print(LINE)
            print(_newer())
            return False
        steps = pending(engine)
        for step in steps:
            print(f"  {step}")
        print(LINE)
        if steps:
            print("Migration needed.")
            return False
        print("Up to date. Nothing to do.")
        return True
    finally:
        engine.dispose()


def apply(path):
    """Create whatever is missing. What exists keeps its rows and values."""
    schema = _schema()
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    engine = create_engine(f"sqlite:///{path}", poolclass=NullPool)
    try:
        stored = schema.stored_schema(engine)
        if stored is not None and stored > schema.SCHEMA_VERSION:
            print(
                f"  [FAIL] The file's schema is {stored}; this release writes up to "
                f"{schema.SCHEMA_VERSION}. Nothing was changed."
            )
            print(f"  {_newer()}")
            return False
        for line in pending(engine):
            print(f"  [OK] {line.replace(' MISSING', ' created or completed')}")
        schema.create_tables(engine)
        for table, column in missing_columns(engine):
            if column.primary_key:
                print(f"  [FAIL] {table.name} has no {column.name} key column; it cannot be added")
                return False
        schema.add_missing_columns(engine)
        schema.record_schema(engine)  # written when absent or lower, never over a higher one
        left = pending(engine)
        if left:
            print(f"  [FAIL] Still missing after the migration: {left}")
            return False
        return True
    finally:
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description="SUDARSHAN chart store migration")
    parser.add_argument(
        "--status", action="store_true", help="Report what would change without changing anything"
    )
    args = parser.parse_args()
    print("\nSUDARSHAN Chart Documents Migration")
    print(LINE)
    try:
        path = str(charts_db_path())
        if args.status:
            status(path)
            return 0
        print(f"File: {_shown(path)}")
        print("\nApplying migration...")
        ok = apply(path)
        print(LINE)
        print("Migration complete." if ok else "Migration failed. See the messages above.")
        return 0 if ok else 1
    except Exception as exc:
        print(f"\n[ERROR] Migration failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
