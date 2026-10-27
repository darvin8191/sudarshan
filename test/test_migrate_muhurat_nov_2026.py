"""The published Muhurat hours of 8 November 2026 reach new and existing installs.

The 2026 calendar shipped the session at placeholder hours, 18:00 to 19:15 IST
(MCX 18:00 to 00:15 the next morning). The startup hook seeds the holiday
tables only when they are empty, so a revised
seed reaches a new install and nothing else. Without the migration an existing
install kept calling every price closed during the real afternoon session and
the empty 18:00 market open.
"""

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import inspect
from sqlalchemy.orm import Session, scoped_session, sessionmaker

import database.market_calendar_db as mc
from database.engine_factory import create_db_engine

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "upgrade" / "migrate_muhurat_nov_2026.py"
sys.path.insert(0, str(ROOT / "upgrade"))

import migrate_muhurat_nov_2026 as migration  # noqa: E402

PUBLISHED = {exchange: new for exchange, (_old, new) in migration.REVISION.items()}
SHIPPED = {exchange: old for exchange, (old, _new) in migration.REVISION.items()}


@pytest.fixture
def install(tmp_path, monkeypatch):
    """A main database whose calendar the app's own startup path has seeded."""
    engine = create_db_engine(f"sqlite:///{(tmp_path / 'openalgo.db').as_posix()}")
    held = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))
    monkeypatch.setattr(mc, "engine", engine)
    monkeypatch.setattr(mc, "db_session", held)
    # Swapped by hand: monkeypatch reads the old value with getattr, and a
    # query property read on the declarative base itself raises.
    previous_query = mc.Base.__dict__["query"]
    mc.Base.query = held.query_property()
    try:
        mc.clear_market_calendar_cache()
        mc.ensure_market_calendar_tables_exists()
        yield engine
    finally:
        mc.clear_market_calendar_cache()
        mc.Base.query = previous_query
        held.remove()
        engine.dispose()


def _muhurat(session):
    return (
        session.query(mc.Holiday)
        .filter(mc.Holiday.holiday_date == migration.SESSION_DATE)
        .filter(mc.Holiday.holiday_type == "SPECIAL_SESSION")
        .one()
    )


def _rows(engine):
    """Every exchange row of every holiday: id -> (exchange, open, start, end)."""
    with Session(engine) as session:
        return {
            row.id: (row.exchange_code, row.is_open, row.start_time, row.end_time)
            for row in session.query(mc.HolidayExchange)
        }


def _day(engine):
    """8 November's rows: exchange -> (open, start, end)."""
    with Session(engine) as session:
        rows = session.query(mc.HolidayExchange).filter(
            mc.HolidayExchange.holiday_id == _muhurat(session).id
        )
        return {row.exchange_code: (row.is_open, row.start_time, row.end_time) for row in rows}


def _windows(engine):
    """8 November's windows: exchange -> (start, end), None when closed."""
    return {
        exchange: (start, end) if is_open else None
        for exchange, (is_open, start, end) in _day(engine).items()
    }


def _close(engine, *exchanges):
    """Close ``exchanges`` on 8 November, keeping the times their rows hold."""
    with Session(engine) as session:
        session.query(mc.HolidayExchange).filter(
            mc.HolidayExchange.holiday_id == _muhurat(session).id,
            mc.HolidayExchange.exchange_code.in_(exchanges),
        ).update({mc.HolidayExchange.is_open: False}, synchronize_session=False)
        session.commit()


def _delete(engine, exchange):
    """Delete ``exchange``'s row for 8 November."""
    with Session(engine) as session:
        session.query(mc.HolidayExchange).filter(
            mc.HolidayExchange.holiday_id == _muhurat(session).id,
            mc.HolidayExchange.exchange_code == exchange,
        ).delete(synchronize_session=False)
        session.commit()


def _set(engine, windows):
    """Write ``windows`` (exchange -> (start, end) or None for closed) to 8 November."""
    with Session(engine) as session:
        holiday_id = _muhurat(session).id
        for exchange, window in windows.items():
            start, end = window if window else (None, None)
            session.query(mc.HolidayExchange).filter(
                mc.HolidayExchange.holiday_id == holiday_id,
                mc.HolidayExchange.exchange_code == exchange,
            ).update(
                {
                    mc.HolidayExchange.is_open: window is not None,
                    mc.HolidayExchange.start_time: start,
                    mc.HolidayExchange.end_time: end,
                }
            )
        session.commit()


def _placeholder(engine):
    """Force 8 November back to the hours earlier releases seeded."""
    _set(engine, SHIPPED)


def test_the_seed_carries_the_published_hours(monkeypatch):
    """A new install and a migrated one end up with the same windows."""
    seeded = {}
    monkeypatch.setattr(mc, "_seed_holidays", lambda data, year: seeded.update(data=data))
    mc.seed_holidays_2026()
    muhurat = next(day for day in seeded["data"] if day["date"] == "2026-11-08")
    windows = {row["exchange"]: (row["start_time"], row["end_time"]) for row in muhurat["open"]}

    assert muhurat["holiday_type"] == "SPECIAL_SESSION"
    assert muhurat["closed"] == []
    assert windows == PUBLISHED

    def clock(ms):
        return datetime.fromtimestamp(ms / 1000, mc.IST).strftime("%d %H:%M")

    assert {exchange: tuple(map(clock, window)) for exchange, window in windows.items()} == {
        "NSE": ("08 14:20", "08 15:30"),
        "BSE": ("08 14:20", "08 15:30"),
        "NFO": ("08 14:20", "08 15:40"),
        "BFO": ("08 14:20", "08 15:40"),
        "CDS": ("08 14:20", "08 15:30"),
        "BCD": ("08 14:20", "08 15:30"),
        "MCX": ("08 14:20", "08 15:30"),
    }
    assert tuple(map(clock, migration.PLACEHOLDER)) == ("08 18:00", "08 19:15")
    assert tuple(map(clock, migration.MCX_PLACEHOLDER)) == ("08 18:00", "09 00:15")


def test_a_new_install_has_nothing_to_change(install, capsys):
    assert all(step[4] == "current" for step in migration.plan(install))
    assert migration.status(install)
    assert "Nothing to change." in capsys.readouterr().out


def test_an_existing_install_moves_to_the_published_hours(install):
    _placeholder(install)
    before = _rows(install)
    assert migration.apply(install)
    after = _rows(install)
    windows = _windows(install)

    assert windows == PUBLISHED
    # Seven rows moved; every other day is as it was.
    moved = {row_id for row_id in before if before[row_id] != after[row_id]}
    assert len(moved) == len(PUBLISHED)
    assert {after[row_id][0] for row_id in moved} == set(PUBLISHED)
    assert after.keys() == before.keys()


def test_a_window_an_admin_set_is_left_alone(install, capsys):
    _placeholder(install)
    own = (migration._at(14, 0), migration._at(16, 0))
    _set(install, {"NFO": own})  # an admin's NFO hours
    _close(install, "BCD")  # BCD closed, its row still holding the placeholder times
    _delete(install, "CDS")  # and the CDS row deleted

    actions = {step[0]: step[4] for step in migration.plan(install)}
    assert actions == {
        "NSE": "update",
        "BSE": "update",
        "NFO": "custom",
        "BFO": "update",
        "CDS": "absent",
        "BCD": "custom",
        "MCX": "update",
    }
    assert migration.apply(install)
    windows = _windows(install)

    assert windows["NFO"] == own
    assert _day(install)["BCD"] == (False, *migration.PLACEHOLDER)
    assert "CDS" not in windows  # nothing is inserted
    assert windows["NSE"] == PUBLISHED["NSE"]
    assert windows["MCX"] == PUBLISHED["MCX"]
    out = capsys.readouterr().out
    assert "NFO  14:00-16:00    set by an admin, left alone (published 14:20-15:40)" in out
    assert "BCD  closed    set by an admin, left alone (published 14:20-15:30)" in out
    assert "Updated 4 row(s)" in out


def test_a_row_changed_while_the_migration_runs_is_left_alone(install, monkeypatch, capsys):
    """A change landing after plan() read the rows and before apply() writes them wins.

    Each change leaves one clause of the write's guard to notice it: a new start
    only, a new end only, a row closed with its times kept, a row deleted.
    """
    _placeholder(install)
    start, end = migration.PLACEHOLDER
    read_rows = migration.plan

    def read_then_an_admin_edits(engine):
        steps = read_rows(engine)
        _set(install, {"NSE": (migration._at(14, 15), end), "BSE": (start, migration._at(19, 30))})
        _close(install, "NFO")
        _delete(install, "CDS")
        return steps

    monkeypatch.setattr(migration, "plan", read_then_an_admin_edits)
    assert migration.apply(install)
    day = _day(install)

    assert day["NSE"] == (True, migration._at(14, 15), end)
    assert day["BSE"] == (True, start, migration._at(19, 30))
    assert day["NFO"] == (False, start, end)
    assert "CDS" not in day
    for exchange in ("BFO", "BCD", "MCX"):
        assert day[exchange] == (True, *PUBLISHED[exchange]), exchange
    # Each line says what happened, not what plan() expected.
    out = capsys.readouterr().out
    assert "NSE  changed while this ran, left alone (now 14:15-19:15, published 14:20-15:30)" in out
    assert "BSE  changed while this ran, left alone (now 18:00-19:30, published 14:20-15:30)" in out
    assert "NFO  changed while this ran, left alone (now closed, published 14:20-15:40)" in out
    assert "CDS  changed while this ran, left alone (removed, published 14:20-15:30)" in out
    assert "NSE  18:00-19:15 -> 14:20-15:30" not in out
    assert "Updated 3 row(s)" in out


def test_rerunning_changes_nothing(install):
    _placeholder(install)
    assert migration.apply(install)
    first = _rows(install)
    assert all(step[4] == "current" for step in migration.plan(install))
    assert migration.apply(install)
    assert _rows(install) == first


def test_status_changes_nothing(install, capsys):
    _placeholder(install)
    before = _rows(install)
    assert migration.status(install)
    assert _rows(install) == before
    out = capsys.readouterr().out
    assert "NFO  18:00-19:15 -> 14:20-15:40" in out
    assert "MCX  18:00-09 Nov 00:15 -> 14:20-15:30" in out
    assert "Would change 7 row(s)." in out


@pytest.mark.parametrize("make_tables", [True, False])
def test_an_empty_or_missing_calendar_is_left_for_the_startup_seed(tmp_path, make_tables):
    """A row inserted here would stop the seed, which runs only on an empty table."""
    engine = create_db_engine(f"sqlite:///{(tmp_path / 'openalgo.db').as_posix()}")
    tables = [mc.Holiday.__table__, mc.HolidayExchange.__table__]
    try:
        if make_tables:
            mc.Base.metadata.create_all(engine, tables=tables)
        assert migration.plan(engine) is None
        assert migration.apply(engine)
        assert migration.status(engine)
        if make_tables:
            assert _rows(engine) == {}
        else:
            assert not set(migration.TABLES) & set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_the_script_reports_then_migrates_a_database_file(install):
    """The entry point, as migrate_all runs it, on a file forced back to the placeholder."""
    _placeholder(install)
    env = {**os.environ, "DATABASE_URL": install.url.render_as_string(hide_password=False)}

    def run(*args):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )

    status = run("--status")
    assert status.returncode == 0, status.stderr
    assert "Would change 7 row(s)." in status.stdout
    assert _windows(install) == SHIPPED

    applied = run()
    assert applied.returncode == 0, applied.stderr
    assert "Updated 7 row(s)" in applied.stdout
    assert _windows(install) == PUBLISHED

    again = run()
    assert again.returncode == 0, again.stderr
    assert "Nothing to change." in again.stdout


@pytest.mark.parametrize("args", [(), ("--status",)])
def test_a_database_that_cannot_be_read_fails_in_plain_words(tmp_path, args):
    """Exit 1 with a sentence on screen; the traceback goes to log/errors.jsonl only."""
    broken = tmp_path / "openalgo.db"
    broken.write_bytes(b"this file is not a database " * 512)
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{broken.as_posix()}",
        "LOG_DIR": str(tmp_path / "log"),
    }
    run = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert run.returncode == 1, run.stdout + run.stderr
    assert "[FAIL] Could not read the market calendar, so nothing was changed." in run.stdout
    assert "The details are in log/errors.jsonl." in run.stdout
    for detail in ("Traceback", "DatabaseError", "sqlite3.", "SELECT"):
        assert detail not in run.stdout + run.stderr, detail
    records = [
        json.loads(line)
        for line in (tmp_path / "log" / "errors.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [
        record
        for record in records
        if record["message"] == "Muhurat 2026 migration: the market calendar could not be read"
        and "DatabaseError" in "".join(record["exception"])
    ]


@pytest.mark.parametrize("args", [[], ["--status"]])
def test_a_database_file_that_is_not_there_is_reported_and_not_created(
    tmp_path, monkeypatch, capsys, args
):
    missing = tmp_path / "openalgo.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{missing.as_posix()}")
    monkeypatch.setattr(sys, "argv", ["migrate_muhurat_nov_2026.py", *args])

    assert migration.main() == 0
    assert "No market calendar stored yet." in capsys.readouterr().out
    assert not missing.exists()


def test_the_migration_is_registered():
    # On Windows migrate_all rewraps stdout at import, which closes pytest's capture.
    with patch.object(sys, "platform", "test"):
        from upgrade import migrate_all

    names = [script for script, _description in migrate_all.MIGRATIONS]
    assert names.count("migrate_muhurat_nov_2026.py") == 1
