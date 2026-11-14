"""upgrade/migrate_sudarshan_risk.py on a missing, a partial and an existing file (M5.13).

Each case runs the script as migrate_all.py does, in a process of its own, against a copy of a
file forced into the state being tested. --status changes nothing, not even by creating the file.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "upgrade" / "migrate_sudarshan_risk.py"
TABLES = {"risk_settings", "risk_state", "daily_locks", "risk_audit"}


def _run(path: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "SUDARSHAN_RISK_DB"}
    env["SUDARSHAN_RISK_DB"] = str(path)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _tables(path: Path) -> set[str]:
    with closing(sqlite3.connect(path)) as db, db:
        return {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _row(path: Path, table: str) -> dict:
    with closing(sqlite3.connect(path)) as db, db:
        cursor = db.execute(f"SELECT * FROM {table} WHERE id = 1")
        values = cursor.fetchone()
        return (
            None
            if values is None
            else dict(zip([c[0] for c in cursor.description], values, strict=True))
        )


def test_status_on_a_missing_file_changes_nothing(tmp_path):
    path = tmp_path / "db" / "sudarshan_risk.db"
    done = _run(path, "--status")
    assert done.returncode == 0, done.stderr
    assert "MISSING" in done.stdout and "Migration needed." in done.stdout
    assert not path.exists() and not path.parent.exists()


def test_a_missing_file_is_created_with_every_guard_off(tmp_path):
    path = tmp_path / "db" / "sudarshan_risk.db"
    done = _run(path)
    assert done.returncode == 0, done.stdout + done.stderr
    assert TABLES <= _tables(path)
    settings = _row(path, "risk_settings")
    assert settings["daily_loss_limit"] is None
    assert (settings["band_on"], settings["size_on"], settings["burst_on"]) == (0, 0, 0)
    assert settings["confirm_phrase"] == "STOP"
    assert _row(path, "risk_state")["ks_on"] == 0
    again = _run(path, "--status")
    assert "Up to date. Nothing to do." in again.stdout, again.stdout


def test_a_partial_file_is_completed_and_keeps_what_the_owner_chose(tmp_path):
    """Interrupted, or from an earlier build: one table with a row, one without, a column short."""
    path = tmp_path / "sudarshan_risk.db"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "CREATE TABLE risk_settings (id INTEGER PRIMARY KEY, daily_loss_limit FLOAT,"
            " band_on BOOLEAN NOT NULL DEFAULT 0, confirm_phrase VARCHAR(40) NOT NULL"
            " DEFAULT 'STOP', updated_at DATETIME, updated_by VARCHAR(80))"
        )
        db.execute(
            "INSERT INTO risk_settings (id, daily_loss_limit, band_on, confirm_phrase)"
            " VALUES (1, 4000, 1, 'HALT')"
        )
        db.execute("CREATE TABLE risk_state (id INTEGER PRIMARY KEY, ks_on BOOLEAN NOT NULL)")
    before = _run(path, "--status")
    assert "MISSING" in before.stdout and "risk_audit" in before.stdout
    assert _tables(path) == {"risk_settings", "risk_state"}  # --status changed nothing

    done = _run(path)
    assert done.returncode == 0, done.stdout + done.stderr
    assert TABLES <= _tables(path)
    settings = _row(path, "risk_settings")
    assert (settings["daily_loss_limit"], settings["band_on"]) == (4000, 1)
    assert settings["confirm_phrase"] == "HALT"
    assert settings["size_on"] == 0 and settings["max_lots_fo"] == 2  # added, at their defaults
    state = _row(path, "risk_state")
    assert state["ks_on"] == 0 and "ks_report" in state  # the row and the columns it lacked
    assert "Up to date. Nothing to do." in _run(path, "--status").stdout


def test_an_existing_file_is_left_as_it_is(tmp_path):
    path = tmp_path / "sudarshan_risk.db"
    assert _run(path).returncode == 0
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE risk_settings SET size_on = 1, max_lots_fo = 4 WHERE id = 1")
        db.execute("UPDATE risk_state SET ks_on = 1, ks_actor = 'owner' WHERE id = 1")
        db.execute(
            "INSERT INTO risk_audit (at, last_at, kind, summary) VALUES"
            " ('2026-10-27 05:00:00', '2026-10-27 05:00:00', 'ks_on', 'kept')"
        )
    status = _run(path, "--status")
    assert "Up to date. Nothing to do." in status.stdout, status.stdout
    done = _run(path)
    assert done.returncode == 0
    assert "[OK]" not in done.stdout  # nothing created, added or inserted
    assert _row(path, "risk_settings")["max_lots_fo"] == 4
    assert _row(path, "risk_state")["ks_actor"] == "owner"
    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute("SELECT summary FROM risk_audit").fetchall() == [("kept",)]


def test_the_app_reads_a_file_the_migration_made(tmp_path):
    from sudarshan.guard.store import RiskSettings, RiskStore

    path = tmp_path / "sudarshan_risk.db"
    assert _run(path).returncode == 0
    reader = RiskStore(path)
    assert reader.settings() == RiskSettings()
    reader.dispose()


def test_it_is_registered_once_and_required():
    with patch.object(sys, "platform", "test"):
        from upgrade import migrate_all

    names = [script for script, _description in migrate_all.MIGRATIONS]
    assert names.count("migrate_sudarshan_risk.py") == 1
    assert "migrate_sudarshan_risk.py" in migrate_all.REQUIRED_MIGRATIONS
    assert SCRIPT.exists()


@pytest.mark.parametrize("broken", ["not a database"])
def test_a_file_it_cannot_read_fails_the_migration(tmp_path, broken):
    """A failure must reach migrate_all.py, which then rolls the deploy back."""
    path = tmp_path / "sudarshan_risk.db"
    path.write_text(broken * 100)
    done = _run(path)
    assert done.returncode == 1
    assert "Migration failed" in done.stdout


@pytest.mark.parametrize("table", ["risk_settings", "risk_state"])
def test_status_does_not_say_up_to_date_when_a_default_row_is_missing(tmp_path, table):
    path = tmp_path / "sudarshan_risk.db"
    assert _run(path).returncode == 0
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(f"DELETE FROM {table} WHERE id = 1")
    status = _run(path, "--status")
    assert "Up to date. Nothing to do." not in status.stdout, status.stdout
    assert _row(path, table) is None  # --status changed nothing
    assert _run(path).returncode == 0
    assert _row(path, table) is not None  # and the migration puts it back


def _older_audit_file(path: Path) -> None:
    """A risk file from a build whose audit table had no merge_key column (nor its index)."""
    from sudarshan.guard import store

    fresh = store.RiskStore(path)
    fresh.settings()  # made whole
    fresh.dispose()
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DROP INDEX ix_risk_audit_merge")
        db.execute("ALTER TABLE risk_audit DROP COLUMN merge_key")


def _audit_columns(path: Path) -> set[str]:
    with closing(sqlite3.connect(path)) as db, db:
        return {row[1] for row in db.execute("PRAGMA table_info(risk_audit)")}


def test_the_migration_adds_a_missing_column_before_the_index_that_covers_it(tmp_path):
    """Release review A, L1: the indexes were created first and failed on the absent column."""
    path = tmp_path / "sudarshan_risk.db"
    _older_audit_file(path)
    done = _run(path)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "merge_key" in _audit_columns(path)
    with closing(sqlite3.connect(path)) as db, db:
        names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "ix_risk_audit_merge" in names
    assert "up to date" in _run(path, "--status").stdout.lower()


def test_the_app_s_first_use_adds_a_missing_column_before_the_index(tmp_path):
    from sudarshan.guard import store

    path = tmp_path / "sudarshan_risk.db"
    _older_audit_file(path)
    fresh = store.RiskStore(path)
    try:
        fresh.settings()  # the first use prepares the file
    finally:
        fresh.dispose()
    assert "merge_key" in _audit_columns(path)


def test_a_file_from_before_the_assistants_cutoff_gains_the_column_and_keeps_its_rows(tmp_path):
    """M17.9: a real file forced back to the schema before ``risk_state.mcp_valid_after``: a
    populated one, with the kill switch on. The column arrives empty (access was never ended by
    this release), nothing else moves, and a second run changes nothing."""
    path = tmp_path / "sudarshan_risk.db"
    assert _run(path).returncode == 0
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE risk_state SET ks_on = 1, ks_actor = 'owner', ks_surface = 'app'")
        db.execute("UPDATE risk_settings SET confirm_phrase = 'MINE', max_lots_fo = 7")
        db.execute("ALTER TABLE risk_state DROP COLUMN mcp_valid_after")
    before = _row(path, "risk_state")
    assert "mcp_valid_after" not in before
    status = _run(path, "--status")
    assert status.returncode == 0 and "risk_state.mcp_valid_after" in status.stdout
    assert "MISSING" in status.stdout and "Migration needed." in status.stdout
    assert _row(path, "risk_state") == before, "--status changes nothing"
    done = _run(path)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "Added risk_state.mcp_valid_after" in done.stdout
    after = _row(path, "risk_state")
    assert after.pop("mcp_valid_after") is None
    assert after == before
    assert _row(path, "risk_settings")["confirm_phrase"] == "MINE"
    again = _run(path)
    assert again.returncode == 0 and "Added" not in again.stdout
    assert "Up to date" in _run(path, "--status").stdout


def test_the_app_s_first_use_adds_the_cutoff_column_to_an_older_file(tmp_path):
    """A dev checkout that never ran the migration: the store adds the column when it opens the
    file, and the cutoff can be written and read."""
    from sudarshan.guard.store import RiskStore

    path = tmp_path / "sudarshan_risk.db"
    assert _run(path).returncode == 0
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("ALTER TABLE risk_state DROP COLUMN mcp_valid_after")
    store = RiskStore(path)
    try:
        assert store.mcp_valid_after() is None
        assert store.set_mcp_valid_after(5) == 5 == store.mcp_valid_after()
    finally:
        store.dispose()
