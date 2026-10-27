#!/usr/bin/env python3
"""Move the Muhurat session of 8 November 2026 to the hours the exchanges published.

The 2026 calendar shipped the Diwali Muhurat session (Sunday 8 November 2026)
with placeholder hours, 18:00 to 19:15 IST (MCX 18:00 to 00:15 the next
morning), because the exchanges had not yet announced it. It is an afternoon
session:

    Exchange  Placeholder        Published    Source
    NSE       18:00-19:15        14:20-15:30  NSE circular 149/2026 (NSE/CMTR/76941, 19 Oct 2026)
    NFO       18:00-19:15        14:20-15:40  NSE circular 142/2026 (NSE/FAOP/76940, 19 Oct 2026)
    BSE       18:00-19:15        14:20-15:30  BSE schedules of 19 Oct 2026, as reported by
    BFO       18:00-19:15        14:20-15:40  Angel One on 20 Oct 2026 (equity, equity
    BCD       18:00-19:15        14:20-15:30  derivatives, currency derivatives)
    CDS       18:00-19:15        14:20-15:30  NSE currency derivatives, Angel One, 20 Oct 2026
    MCX       18:00-00:15 (+1d)  14:20-15:30  MCX circular MCX/TRD/590/2026 (19 Oct 2026),
                                              as summarised by TeamLease RegTech

Each window runs from the normal market open to the normal market close, as the
calendar encodes a normal day (NSE 09:15-15:30, not the 09:00 pre-open; MCX's
special session from 14:05 is left out the same way).

Anything that decides "market closed" from the calendar would otherwise call
every price closed during the real session and the empty 18:00 market open.

Only a row that still holds the placeholder when it is written is changed, so a
window an admin set for that day, before or during the run, is left alone and
reported. Nothing is inserted: a missing row
stays missing, and an empty or missing calendar is left for the app, which seeds
it at startup from database/market_calendar_db.py with the published hours.

The running app caches the calendar for up to an hour, so the new hours show
after a restart (the update scripts restart it after migrating) or within the
hour.

Idempotent, and safe to re-run.

Usage:
    cd upgrade
    uv run migrate_muhurat_nov_2026.py
    uv run migrate_muhurat_nov_2026.py --status
"""

import argparse
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, PROJECT_ROOT)
# Register the app's SQLite pragmas on this process's engines, so a migration
# waits the same 15s for a write lock the running app does instead of the
# sqlite3 default of 5s (GitHub issue #1726).
import _pragmas  # noqa: F401,E402
from sqlalchemy import create_engine, inspect  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

TABLES = ("market_holidays", "market_holiday_exchanges")
SESSION_DATE = date(2026, 11, 8)
IST = timezone(timedelta(hours=5, minutes=30))


def _at(hour, minute, day=8):
    """Epoch milliseconds of hour:minute IST on ``day`` November 2026."""
    return int(datetime(2026, 11, day, hour, minute, tzinfo=IST).timestamp() * 1000)


#: The windows the 2026 seed shipped: 18:00-19:15, and MCX to 00:15 the next morning.
PLACEHOLDER = (_at(18, 0), _at(19, 15))
MCX_PLACEHOLDER = (_at(18, 0), _at(0, 15, day=9))

#: Exchange -> (window this revision replaces, published window).
REVISION = {
    "NSE": (PLACEHOLDER, (_at(14, 20), _at(15, 30))),
    "BSE": (PLACEHOLDER, (_at(14, 20), _at(15, 30))),
    "NFO": (PLACEHOLDER, (_at(14, 20), _at(15, 40))),
    "BFO": (PLACEHOLDER, (_at(14, 20), _at(15, 40))),
    "CDS": (PLACEHOLDER, (_at(14, 20), _at(15, 30))),
    "BCD": (PLACEHOLDER, (_at(14, 20), _at(15, 30))),
    "MCX": (MCX_PLACEHOLDER, (_at(14, 20), _at(15, 30))),
}

NO_CALENDAR = (
    "No market calendar stored yet. The app creates it at startup",
    "with the published Muhurat hours.",
)
READ_FAILED = (
    "Could not read the market calendar, so nothing was changed.",
    "The details are in log/errors.jsonl.",
)
SAVE_FAILED = (
    "Could not save the published hours, so nothing was changed.",
    "The details are in log/errors.jsonl.",
)


def _error_log():
    """This script's logger, writing to log/errors.jsonl and not to the screen.

    utils.logging also gives the root logger a console handler, which would print
    the traceback under the plain line this script shows for a failure. The
    detail belongs in the error log, the first place anyone debugging looks.
    """
    from utils.logging import JSONErrorFormatter, get_logger

    log = get_logger(__name__)
    error_log = [
        handler
        for handler in logging.getLogger().handlers
        if isinstance(handler.formatter, JSONErrorFormatter)
    ]
    if error_log:
        log.handlers = error_log
        log.propagate = False
    return log


def _fail(lines, what):
    """Say in plain words what failed, and keep the detail in the error log.

    Called from an ``except`` block, so the traceback goes with the record.
    """
    try:
        _error_log().exception(f"Muhurat 2026 migration: {what}")
    except Exception:
        pass  # the lines below still say what happened
    for line in ("[FAIL] " + lines[0], *lines[1:]):
        print(f"  {line}")


def resolve_sqlite_path(db_url):
    """Make a relative sqlite:/// path absolute against the project root.

    The documented invocation runs from upgrade/, and DATABASE_URL is relative
    by default ("sqlite:///db/openalgo.db"). Left relative it would point at
    upgrade/db/openalgo.db, which SQLAlchemy creates empty on connect, and the
    migration would report success against a database the app never opens.

    Args:
        db_url: The database URL.

    Returns:
        str: The URL with any relative SQLite path made absolute.
    """
    prefix = "sqlite:///"
    if not db_url.startswith(prefix):
        return db_url
    path = db_url[len(prefix) :]
    if not path or path == ":memory:" or os.path.isabs(path):
        return db_url
    return prefix + os.path.join(PROJECT_ROOT, path)


def get_database_url():
    """The database this instance uses.

    Returns:
        str: The SQLAlchemy URL.
    """
    from dotenv import load_dotenv

    load_dotenv(os.path.join(PROJECT_ROOT, ".env"))
    return resolve_sqlite_path(os.getenv("DATABASE_URL", "sqlite:///db/openalgo.db"))


def sqlite_file(db_url):
    """The file a sqlite URL points at, or None for any other backend.

    Args:
        db_url: The database URL.

    Returns:
        str | None: The file path.
    """
    prefix = "sqlite:///"
    if not db_url.startswith(prefix):
        return None
    path = db_url[len(prefix) :]
    return path if path and path != ":memory:" else None


def _window(row):
    """A calendar row's (start, end) in epoch ms, or None when it is closed."""
    if not row.is_open or row.start_time is None or row.end_time is None:
        return None
    return int(row.start_time), int(row.end_time)


def plan(engine):
    """What this migration would do to each exchange's window on 8 November 2026.

    Args:
        engine: An engine bound to the target database.

    Returns:
        list[tuple[str, int | None, tuple | None, tuple, str]] | None: One
        (exchange, row id, current window, published window, action) per
        exchange row, where action is "update", "current" (already on the
        published hours), "custom" (set by an admin, left alone) or "absent"
        (no row, left alone). None when the calendar tables are missing or
        empty, which the app creates and seeds itself.
    """
    if not set(TABLES) <= set(inspect(engine).get_table_names()):
        return None

    from database.market_calendar_db import Holiday, HolidayExchange

    with Session(engine) as session:
        if session.query(Holiday).first() is None:
            return None
        special = [
            holiday.id
            for holiday in session.query(Holiday).filter(
                Holiday.holiday_date == SESSION_DATE,
                Holiday.holiday_type == "SPECIAL_SESSION",
            )
        ]
        rows = []
        if special:
            rows = (
                session.query(HolidayExchange)
                .filter(
                    HolidayExchange.holiday_id.in_(special),
                    HolidayExchange.exchange_code.in_(list(REVISION)),
                )
                .order_by(HolidayExchange.id)
                .all()
            )
        found = [(row.exchange_code, row.id, _window(row)) for row in rows]

    steps = []
    for exchange, (old, new) in REVISION.items():
        held = [(row_id, window) for code, row_id, window in found if code == exchange]
        if not held:
            steps.append((exchange, None, None, new, "absent"))
        for row_id, window in held:
            if window == new:
                action = "current"
            elif window == old:
                action = "update"
            else:
                action = "custom"
            steps.append((exchange, row_id, window, new, action))
    return steps


def _clock(ms):
    """Epoch ms as IST wall-clock time, with the day when it is not the 8th."""
    moment = datetime.fromtimestamp(ms / 1000, IST)
    return f"{moment:%H:%M}" if moment.date() == SESSION_DATE else f"{moment:%d %b %H:%M}"


def _span(window):
    return "closed" if window is None else f"{_clock(window[0])}-{_clock(window[1])}"


def describe(step):
    """One report line for a planned step.

    Args:
        step: An (exchange, row id, current, published, action) tuple from plan().

    Returns:
        str: The line.
    """
    exchange, _row_id, window, new, action = step
    if action == "update":
        return f"  {exchange:<4} {_span(window)} -> {_span(new)}"
    if action == "current":
        return f"  {exchange:<4} {_span(window)}    already on the published hours"
    if action == "absent":
        return f"  {exchange:<4} no row, left alone (published {_span(new)})"
    return (
        f"  {exchange:<4} {_span(window)}    set by an admin, left alone (published {_span(new)})"
    )


def status(engine):
    """Report what would change, without changing anything.

    Args:
        engine: An engine bound to the target database, or None when the
            database file does not exist yet.

    Returns:
        bool: True when the report was produced.
    """
    print("Muhurat trading hours, 8 November 2026: status")
    print("-" * 62)
    try:
        steps = plan(engine) if engine is not None else None
    except Exception:
        _fail(READ_FAILED, "the market calendar could not be read")
        return False
    if steps is None:
        print("\n".join(NO_CALENDAR))
        return True
    for step in steps:
        print(describe(step))
    pending = sum(1 for step in steps if step[4] == "update")
    print(f"Would change {pending} row(s)." if pending else "Nothing to change.")
    return True


def apply(engine):
    """Write the published window to every row that still holds the placeholder.

    Each write is guarded on the placeholder again, so a row changed after
    plan() read it is left alone, and its line says what it holds now.

    Args:
        engine: An engine bound to the target database.

    Returns:
        bool: True on success.
    """
    try:
        steps = plan(engine)
    except Exception:
        _fail(READ_FAILED, "the market calendar could not be read")
        return False

    if steps is None:
        print("\n".join(f"  {line}" for line in NO_CALENDAR))
        return True

    moved, changed_meanwhile = set(), {}
    try:
        from database.market_calendar_db import HolidayExchange

        with Session(engine) as session:
            for exchange, row_id, old, new, action in steps:
                if action != "update":
                    continue
                written = (
                    session.query(HolidayExchange)
                    .filter(
                        HolidayExchange.id == row_id,
                        HolidayExchange.exchange_code == exchange,
                        HolidayExchange.is_open.is_(True),
                        HolidayExchange.start_time == old[0],
                        HolidayExchange.end_time == old[1],
                    )
                    .update(
                        {HolidayExchange.start_time: new[0], HolidayExchange.end_time: new[1]},
                        synchronize_session=False,
                    )
                )
                if written:
                    moved.add(row_id)
                    continue
                row = session.get(HolidayExchange, row_id)
                changed_meanwhile[row_id] = (
                    "removed" if row is None else f"now {_span(_window(row))}"
                )
            session.commit()
    except Exception:
        _fail(SAVE_FAILED, "the published hours could not be saved")
        return False

    for step in steps:
        exchange, row_id, _then, new, _action = step
        if row_id in changed_meanwhile:
            print(
                f"  {exchange:<4} changed while this ran, left alone "
                f"({changed_meanwhile[row_id]}, published {_span(new)})"
            )
        else:
            print(describe(step))
    if moved:
        print(f"  [OK] Updated {len(moved)} row(s). OpenAlgo shows them after a restart,")
        print("  or within the hour.")
    else:
        print("  [OK] Nothing to change.")
    return True


def main():
    """Entry point.

    Returns:
        int: 0 on success, 1 on failure.
    """
    parser = argparse.ArgumentParser(
        description="Move the 8 November 2026 Muhurat session to the published hours"
    )
    parser.add_argument(
        "--status", action="store_true", help="Report what would change without changing it"
    )
    args = parser.parse_args()

    db_url = get_database_url()
    # database.market_calendar_db builds its own engine from DATABASE_URL at
    # import; unset, that import fails. Nothing is written through that engine.
    os.environ.setdefault("DATABASE_URL", db_url)
    path = sqlite_file(db_url)
    if path is not None and not os.path.exists(path):
        # Nothing to migrate, and connecting would create an empty file.
        return 0 if status(None) else 1

    engine = create_engine(db_url, poolclass=NullPool)
    try:
        if args.status:
            return 0 if status(engine) else 1
        print("=" * 62)
        print("Migration: Muhurat trading hours, 8 November 2026")
        print("=" * 62)
        ok = apply(engine)
        print("-" * 62)
        print("Migration complete." if ok else "Migration failed. See the messages above.")
        return 0 if ok else 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    sys.exit(main())
