"""upgrade/migrate_sudarshan_automation.py on a missing, a partial and a populated file (A1).

Each case runs the script as migrate_all.py does, in a process of its own, against a file forced
into the state being tested. --status changes nothing, not even by creating the file.
"""

from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "upgrade" / "migrate_sudarshan_automation.py"
TABLES = {"auto_strategy", "auto_state", "auto_alert"}
CLAIMS = {"ux_auto_alert_claim", "ux_auto_strategy_contract", "ux_auto_strategy_token"}


def _run(path: Path, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "SUDARSHAN_AUTOMATION_DB": str(path)}
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _names(path: Path, kind: str) -> set[str]:
    with closing(sqlite3.connect(path)) as db, db:
        rows = db.execute("SELECT name FROM sqlite_master WHERE type = ?", (kind,))
        return {row[0] for row in rows}


def _populated(path: Path) -> None:
    """A file as the app makes it, with a strategy, its state and two alert lines."""
    assert _run(path).returncode == 0
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "INSERT INTO auto_strategy (id, name, token_sha256, state, mode, contract_key, product)"
            " VALUES (1, 'ORB', 'h1', 'active', 'sandbox', 'NFO:NIFTY:FUT', 'MIS')"
        )
        db.execute(
            "INSERT INTO auto_state (strategy_id, last_target, alerts_today) VALUES (1, 1, 7)"
        )
        for claim in ("100|-|long:", "160|-|flat:"):
            db.execute(
                "INSERT INTO auto_alert (strategy_id, received_ms, claim, verdict)"
                " VALUES (1, 5, ?, 'sent')",
                (claim,),
            )


def _back_to_an_older_schema(path: Path) -> None:
    """Forced back: no ``no_entry_before_stop_min`` column, no claim index, no state table."""
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DROP INDEX ux_auto_alert_claim")
        db.execute("DROP INDEX ux_auto_strategy_contract")
        db.execute("ALTER TABLE auto_strategy DROP COLUMN no_entry_before_stop_min")
        db.execute("ALTER TABLE auto_alert DROP COLUMN tv_delay_ms")


def test_status_on_a_missing_file_changes_nothing(tmp_path):
    path = tmp_path / "db" / "sudarshan_automation.db"
    done = _run(path, "--status")
    assert done.returncode == 0, done.stderr
    assert "MISSING" in done.stdout and "Migration needed." in done.stdout
    assert not path.exists() and not path.parent.exists()


def test_a_missing_file_is_created_whole_and_a_second_run_changes_nothing(tmp_path):
    path = tmp_path / "db" / "sudarshan_automation.db"
    done = _run(path)
    assert done.returncode == 0, done.stdout + done.stderr
    assert TABLES <= _names(path, "table") and CLAIMS <= _names(path, "index")
    before = path.read_bytes()
    assert "Up to date. Nothing to do." in _run(path, "--status").stdout
    again = _run(path)
    assert again.returncode == 0 and "[OK]" not in again.stdout
    assert path.read_bytes() == before


def test_a_populated_file_forced_back_is_completed_and_keeps_its_rows(tmp_path):
    path = tmp_path / "sudarshan_automation.db"
    _populated(path)
    _back_to_an_older_schema(path)
    told = _run(path, "--status")
    assert "auto_strategy.no_entry_before_stop_min" in told.stdout.replace(" ", "")
    assert "ux_auto_alert_claim" in told.stdout and "Migration needed." in told.stdout
    assert "ux_auto_alert_claim" not in _names(path, "index")  # --status changed nothing
    done = _run(path)
    assert done.returncode == 0, done.stdout + done.stderr
    assert CLAIMS <= _names(path, "index")
    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute(
            "SELECT name, state, no_entry_before_stop_min FROM auto_strategy"
        ).fetchall() == [("ORB", "active", 15)]  # the new column takes its default
        assert db.execute("SELECT last_target, alerts_today FROM auto_state").fetchall() == [(1, 7)]
        assert db.execute("SELECT count(*), max(tv_delay_ms) FROM auto_alert").fetchone() == (
            2,
            None,
        )
        # and the claim holds again
        try:
            db.execute(
                "INSERT INTO auto_alert (strategy_id, received_ms, claim, verdict)"
                " VALUES (1, 6, '100|-|long:', 'claimed')"
            )
            raise AssertionError("a second line for one alert was accepted")
        except sqlite3.IntegrityError:
            pass
    assert "Up to date. Nothing to do." in _run(path, "--status").stdout


def test_a_partial_file_from_an_interrupted_run_is_completed(tmp_path):
    path = tmp_path / "sudarshan_automation.db"
    _populated(path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DROP TABLE auto_state")
    done = _run(path)
    assert done.returncode == 0 and "Created auto_state" in done.stdout
    assert TABLES <= _names(path, "table")
    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute("SELECT count(*) FROM auto_alert").fetchone() == (2,)


def test_rows_that_break_a_claim_are_reported_and_never_deleted(tmp_path):
    path = tmp_path / "sudarshan_automation.db"
    _populated(path)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DROP INDEX ux_auto_alert_claim")
        db.execute(
            "INSERT INTO auto_alert (strategy_id, received_ms, claim, verdict)"
            " VALUES (1, 6, '100|-|long:', 'sent')"
        )
    done = _run(path)
    assert done.returncode == 1
    assert "[FAIL] ux_auto_alert_claim cannot be made" in done.stdout
    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute("SELECT count(*) FROM auto_alert").fetchone() == (3,)


def test_it_is_registered_and_required():
    text = (REPO / "upgrade" / "migrate_all.py").read_text()
    assert re.search(r'\("migrate_sudarshan_automation\.py", "[^"]+"\)', text)
    required = text.split("REQUIRED_MIGRATIONS = frozenset(")[1].split(")")[0]
    assert '"migrate_sudarshan_automation.py"' in required


def test_the_output_names_no_path_outside_the_checkout(tmp_path):
    done = _run(tmp_path / "x.db")
    assert str(tmp_path) not in done.stdout and "SUDARSHAN_AUTOMATION_DB" in done.stdout
