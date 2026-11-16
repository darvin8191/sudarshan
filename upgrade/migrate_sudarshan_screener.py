#!/usr/bin/env python3
"""
Migration: SUDARSHAN Screener (screener.db, M9)

Brings the screener's own file, screener.db, to the schema of this release. The steps are the
numbered ones in sudarshan/screener/migrations.py, the same the sudarshan-screener service runs
on the file when it starts; this script is the way to run or report them from
upgrade/migrate_all.py, as CLAUDE.md asks of every schema change. Step 5 (this release) adds two
columns to `run`: whether a run raised an alert, and when the owner looked at it (the count of
alerts not looked at on the Dashboard card).

Listed in migrate_all.py's MIGRATIONS but not in REQUIRED_MIGRATIONS, on purpose: the screener
ships switched off, its file lives in the service's own state directory (not under db/), and the
service brings the file up to date itself at every start. So this script changes nothing, and
says why, when:

- there is no screener.db (the screener was never switched on: the service makes the file);
- the file cannot be opened by the user running the upgrade (it belongs to the service);
- the sudarshan-screener service is running (it holds the file and has migrated it already; two
  writers of one schema step are one too many).

Idempotent and safe to re-run: each step checks what is there before it changes anything, runs in
one transaction, and records itself in the file. A file written by a newer release is refused
untouched. The backfill of step 5 is from each row's own data: a run recorded as having raised an
alert is marked so, and as looked at (the count never showed it); no other row is touched.

Usage:
    cd upgrade
    uv run migrate_sudarshan_screener.py                  # Apply
    uv run migrate_sudarshan_screener.py --status         # Report what would change, change nothing
    uv run migrate_sudarshan_screener.py --path FILE      # A file elsewhere (a copy, say)
"""

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

RULE = "-" * 46
STEP_WORDS = {
    1: "the screener's tables",
    2: "bar_today.cum, bar_today.last_trade",
    3: "pause.key",
    4: "the dashboard and its widgets",
    5: "run.alerted, run.alert_seen_at (alerts not looked at), backfilled from each run",
}


def _modules():
    from sudarshan.screener import migrate, migrations, store
    from sudarshan.screener.contract import DEFAULT_HTTP_PORT

    return migrate, migrations, store, DEFAULT_HTTP_PORT


def _shown(path):
    """The file by its name only: the output may be shared, and the folder is the server's."""
    return os.path.basename(str(path))


def report(path):
    """(the file's version or None, this release's version, the pending steps, newer?)."""
    migrate, _migrations, _store, _port = _modules()
    found = migrate.status(path)
    return found["file"], found["code"], found["pending"], found["newer"]


def status(path):
    """Report what would change, without changing anything. True when nothing is pending."""
    _migrate, migrations, store, _port = _modules()
    print(f"File: {_shown(path)}")
    if not os.path.exists(path):
        print("  no file: the screener has not been switched on, and its service makes the file")
        print(RULE)
        print("Nothing to do.")
        return True
    try:
        found, code, steps, newer = report(path)
    except (store.StoreError, OSError) as exc:
        print(f"  the file could not be opened by this user ({type(exc).__name__})")
        print("  the sudarshan-screener service brings it up to date itself when it starts")
        print(RULE)
        print("Nothing to do here.")
        return True
    print(f"Schema in the file: {found}")
    print(f"Schema in this release: {code}")
    if newer:
        print(RULE)
        print(migrations.SchemaTooNew.WORDS)
        return False
    for step in steps:
        print(f"  step {step} MISSING: {STEP_WORDS.get(step, 'a later step')}")
    print(RULE)
    print("Migration needed." if steps else "Up to date. Nothing to do.")
    return not steps


def apply(path, port):
    """Run the steps the file lacks. True unless the file is newer or a step failed."""
    migrate, migrations, store, _default = _modules()
    print(f"File: {_shown(path)}")
    if not os.path.exists(path):
        print("  [SKIP] no file: the screener has not been switched on; its service makes the file")
        return True
    try:
        found, code, steps, newer = report(path)
    except (store.StoreError, OSError) as exc:
        print(f"  [SKIP] the file could not be opened by this user ({type(exc).__name__});")
        print("         the sudarshan-screener service brings it up to date when it starts")
        return True
    if newer:
        print(f"  [FAIL] The file's schema is {found}; this release writes up to {code}.")
        print(f"  {migrations.SchemaTooNew.WORDS}")
        return False
    if not steps:
        print(f"  [SKIP] schema {found}: up to date")
        return True
    if migrate.service_is_up(port):
        print("  [SKIP] sudarshan-screener is running: it migrates the file itself at its start")
        return True
    if not os.access(path, os.W_OK):
        print("  [SKIP] the file cannot be written by this user; the service migrates it")
        return True
    try:
        opened = store.ScreenerStore(path)  # opening it runs the steps, as the service's start does
    except (store.StoreError, migrations.SchemaTooNew) as exc:
        print(f"  [FAIL] {exc}")
        return False
    try:
        for step in opened.applied:
            print(f"  [OK] step {step}: {STEP_WORDS.get(step, 'applied')}")
    finally:
        opened.close()
        store.stop()
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description="SUDARSHAN screener store migration")
    parser.add_argument(
        "--status", action="store_true", help="Report what would change without changing anything"
    )
    parser.add_argument("--path", default=None, help="The database (default: the service's)")
    parser.add_argument("--port", type=int, default=None, help="The service's port")
    args = parser.parse_args(argv)
    print("\nSUDARSHAN Screener Migration")
    print(RULE)
    try:
        _migrate, _migrations, store, default_port = _modules()
        path = Path(args.path or store.default_path())
        if args.status:
            status(path)
            return 0
        print("\nApplying migration...")
        ok = apply(path, args.port or default_port)
        print(RULE)
        print("Migration complete." if ok else "Migration failed. See the messages above.")
        return 0 if ok else 1
    except Exception as exc:
        print(f"\n[ERROR] Migration failed: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
