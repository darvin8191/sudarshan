"""upgrade/migrate_sudarshan_watchlists.py on a missing, a populated, a forced-back and a newer file
(M8.4, W2). Each case runs the script as migrate_all.py does, in a process of its own."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "upgrade" / "migrate_sudarshan_watchlists.py"
TABLES = {
    "wl_list", "wl_section", "wl_section_item", "wl_row", "wl_symbol", "wl_flag", "wl_favourite",
    "wl_pref", "wl_meta",
}  # fmt: skip


def _run(path: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "SUDARSHAN_WATCHLISTS_DB"}
    env["SUDARSHAN_WATCHLISTS_DB"] = str(path)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def _sql(path: Path, sql: str, *args):
    with closing(sqlite3.connect(path)) as db, db:
        return db.execute(sql, args).fetchall()


def _dump(path: Path) -> dict:
    """Every table's rows, to compare before and after."""
    names = [r[0] for r in _sql(path, "SELECT name FROM sqlite_master WHERE type='table'")]
    return {n: sorted(_sql(path, f"SELECT * FROM {n}")) for n in names}


def _populate(path: Path) -> None:
    """What an owner who has used v2 for a month would have, values the code never defaults."""
    _sql(path, "INSERT INTO wl_list VALUES ('owner', 3, '2026-11-01 10:00:00', 9, 1700000000000)")
    _sql(path, "INSERT INTO wl_section (id, list_id, name, mark, position, created_at, updated_at)"
         " VALUES (1, 3, 'Core', 4, 0, 1, 2)")  # fmt: skip
    _sql(path, "INSERT INTO wl_section_item VALUES ('owner', 3, 'NSE', 'INFY', 1)")
    _sql(path, "INSERT INTO wl_row (id, list_id, position, kind, underlying, underlying_exchange,"
         " expiry_rule, \"offset\", created_at, updated_at) VALUES (1, 3, 0, 'atm_option', 'NIFTY',"
         " 'NSE_INDEX', 'nearest', 2, 1, 2)")  # fmt: skip
    _sql(
        path, "INSERT INTO wl_symbol VALUES ('owner', 'NSE', 'INFY', 2, 5, 'results soon', 3, 6, 7)"
    )
    _sql(path, "INSERT INTO wl_flag VALUES ('owner', 2, 'Watch closely')")
    _sql(path, "INSERT INTO wl_favourite VALUES ('owner', 1, 'list:3', 8)")
    _sql(path, "INSERT INTO wl_pref VALUES ('owner', 'list:3', 'change', 1, 'open', 1, 9)")
    _sql(path, "INSERT INTO wl_meta VALUES ('flags_version', '5')")


def test_status_on_a_missing_file_changes_nothing(tmp_path):
    path = tmp_path / "db" / "sudarshan_watchlists.db"
    done = _run(path, "--status")
    assert done.returncode == 0, done.stderr
    assert "MISSING" in done.stdout and "Migration needed." in done.stdout
    assert not path.exists() and not path.parent.exists()


def test_a_missing_file_is_created_with_schema_one_and_nothing_seeded(tmp_path):
    path = tmp_path / "db" / "sudarshan_watchlists.db"
    done = _run(path)
    assert done.returncode == 0, done.stdout + done.stderr
    names = {r[0] for r in _sql(path, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert names == TABLES
    assert _sql(path, "SELECT name, value FROM wl_meta") == [("schema", "1")]
    for table in TABLES - {"wl_meta"}:
        assert _sql(path, f"SELECT count(*) FROM {table}") == [(0,)]
    assert "Up to date. Nothing to do." in _run(path, "--status").stdout


def test_a_second_run_changes_nothing_and_says_so(tmp_path):
    path = tmp_path / "w.db"
    assert _run(path).returncode == 0
    _populate(path)
    before = _dump(path)
    done = _run(path)
    assert done.returncode == 0 and "[OK]" not in done.stdout, done.stdout
    assert _dump(path) == before


def test_status_changes_not_a_byte_of_an_existing_file(tmp_path):
    path = tmp_path / "w.db"
    assert _run(path).returncode == 0
    _populate(path)
    for sibling in path.parent.glob("w.db-*"):
        sibling.unlink()  # as the app leaves it at rest
    before = path.read_bytes()
    done = _run(path, "--status")
    assert done.returncode == 0 and "Up to date. Nothing to do." in done.stdout, done.stdout
    assert path.read_bytes() == before  # SQLite's own -wal and -shm may appear; the file does not
    assert _dump(path)["wl_symbol"] == [("owner", "NSE", "INFY", 2, 5, "results soon", 6, 7)]


def test_a_populated_copy_forced_back_keeps_every_row_and_value(tmp_path):
    path = tmp_path / "w.db"
    assert _run(path).returncode == 0
    _populate(path)
    owners = _dump(path)
    # The old state: a column short, two indexes gone, a table gone, the schema record gone.
    _sql(path, "DROP INDEX ix_wl_row_list")
    _sql(path, "DROP INDEX ix_wl_section_item_section")
    _sql(path, "DROP TABLE wl_pref")
    _sql(path, "CREATE TABLE wl_pref (principal VARCHAR(16) NOT NULL DEFAULT 'owner', ref"
         " VARCHAR(80) NOT NULL, sort VARCHAR(12) NOT NULL DEFAULT 'list', updated_at BIGINT"
         " NOT NULL, PRIMARY KEY (principal, ref))")  # fmt: skip
    _sql(path, "INSERT INTO wl_pref VALUES ('owner', 'list:3', 'change', 9)")
    _sql(path, "DROP TABLE wl_favourite")
    _sql(path, "DELETE FROM wl_meta WHERE name = 'schema'")
    copy = tmp_path / "copy.db"
    shutil.copyfile(path, copy)

    status = _run(copy, "--status")
    assert "Migration needed." in status.stdout
    for line in ("wl_favourite", "wl_pref.descending", "ix_wl_row_list", "schema record"):
        assert line in status.stdout, status.stdout
    assert _run(copy, "--status").stdout == status.stdout  # reading changed nothing

    done = _run(copy)
    assert done.returncode == 0, done.stdout + done.stderr
    after = _dump(copy)
    for table in ("wl_list", "wl_section", "wl_section_item", "wl_row", "wl_symbol", "wl_flag"):
        assert after[table] == owners[table], table
    assert after["wl_meta"] == sorted([("flags_version", "5"), ("schema", "1")])
    # Added in place at the column's default; the value the owner chose stays.
    assert _sql(copy, "SELECT ref, sort, descending, basis, held FROM wl_pref") == [
        ("list:3", "change", 0, "close", 0)
    ]
    assert after["wl_favourite"] == []
    assert "Up to date. Nothing to do." in _run(copy, "--status").stdout


def test_a_schema_record_the_owner_cannot_have_set_is_never_lowered_or_written_over(tmp_path):
    path = tmp_path / "w.db"
    assert _run(path).returncode == 0
    _sql(path, "UPDATE wl_meta SET value = '2' WHERE name = 'schema'")
    _sql(path, "CREATE TABLE wl_future (x INTEGER)")
    before = path.read_bytes()
    done = _run(path)
    assert done.returncode == 1
    assert "newer SUDARSHAN release" in done.stdout and "[FAIL]" in done.stdout
    status = _run(path, "--status")
    assert "newer SUDARSHAN release" in status.stdout
    assert _sql(path, "SELECT value FROM wl_meta WHERE name = 'schema'") == [("2",)]
    assert path.read_bytes() == before  # refused untouched


def test_the_migration_is_listed_not_required_beside_the_risk_layer():
    sys.path.insert(0, str(REPO))
    from unittest.mock import patch

    with patch.object(sys, "platform", "test"):
        from upgrade import migrate_all
    names = [name for name, _ in migrate_all.MIGRATIONS]
    assert names.count("migrate_sudarshan_watchlists.py") == 1
    assert names.index("migrate_sudarshan_watchlists.py") > names.index("migrate_sudarshan_risk.py")
    assert "migrate_sudarshan_watchlists.py" not in migrate_all.REQUIRED_MIGRATIONS
    assert (REPO / "upgrade" / "migrate_sudarshan_watchlists.py").exists()


def test_the_app_store_accepts_what_the_migration_made(tmp_path, monkeypatch):
    from sudarshan.watchlists import engine, schema

    path = tmp_path / "w.db"
    assert _run(path).returncode == 0
    _populate(path)
    monkeypatch.setenv(schema.PATH_ENV, str(path))
    engine.dispose()
    try:
        with engine.open_session() as s:
            assert s.execute(schema.text("SELECT count(*) FROM wl_symbol")).scalar() == 1
    finally:
        engine.dispose()
