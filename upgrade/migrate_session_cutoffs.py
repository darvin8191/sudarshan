#!/usr/bin/env python3
"""
Migration: SUDARSHAN session cutoffs (review HIGH-2, "sessions valid after")

Adds the table session_cutoffs to OpenAlgo's main database (DATABASE_URL): per user, when every
session was last ended at once (a logout, a password change or a reset). A cookie that logged in
before it is refused on its next request, on every device. Nothing else changes. It starts empty,
the only backfill the data allows: no such ending was recorded before, so every session valid now
stays valid until its daily expiry, as before. The app's start creates it too (auth_db.init_db).

Idempotent and safe to re-run.

Usage:
    cd upgrade
    uv run migrate_session_cutoffs.py           # Apply
    uv run migrate_session_cutoffs.py --status  # Report what would change, change nothing
"""

import argparse
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Register the app's SQLite pragmas on this process's engines (GitHub issue #1726).
import _pragmas  # noqa: F401,E402
from sqlalchemy import (  # noqa: E402
    Column,
    DateTime,
    MetaData,
    String,
    Table,
    create_engine,
    inspect,
)
from sqlalchemy.pool import NullPool  # noqa: E402

#: As database.auth_db.SessionCutoff declares it, written out so this runs without importing the
#: app (which needs API_KEY_PEPPER); a test holds the two to the same columns.
SESSION_CUTOFFS = Table(
    "session_cutoffs",
    MetaData(),
    Column("username", String(255), primary_key=True),
    Column("valid_after", DateTime(timezone=True), nullable=False),
)


def main():
    parser = argparse.ArgumentParser(description="SUDARSHAN session cutoffs migration")
    parser.add_argument(
        "--status", action="store_true", help="Report what would change without changing anything"
    )
    args = parser.parse_args()
    print("\nSUDARSHAN Session Cutoffs Migration\n" + "-" * 46)
    from dotenv import load_dotenv

    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))  # never overrides the environment
    url = os.getenv("DATABASE_URL", "sqlite:///db/openalgo.db")
    path = url[len("sqlite:///") :] if url.startswith("sqlite:///") else None
    if path and path != ":memory:" and not os.path.isabs(path):
        # Run from upgrade/ as documented, a relative path still means the app's own file.
        path = os.path.join(PROJECT_ROOT, path)
        url = "sqlite:///" + path
    engine = create_engine(url, poolclass=NullPool) if path else create_engine(url)
    try:
        if path and path != ":memory:" and not os.path.exists(path):
            print("  No database yet: the app's first start creates it, this table included.")
            return 0  # and not even an empty file is made here
        present = SESSION_CUTOFFS.name in inspect(engine).get_table_names()
        if args.status or present:
            print(f"  {SESSION_CUTOFFS.name:<26} {'present' if present else 'MISSING'}")
            print("-" * 46)
            print("Up to date. Nothing to do." if present else "Migration needed.")
            return 0
        SESSION_CUTOFFS.create(engine, checkfirst=True)  # the app's own start may race this
        print(f"  [OK] Created {SESSION_CUTOFFS.name}")
        return 0
    except Exception as exc:
        print(f"\n[ERROR] Migration failed: {exc}")
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
