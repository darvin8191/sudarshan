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

import pytest

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
    _sql(path, "INSERT INTO wl_favourite VALUES ('owner', 1, 'list:3', 8, '2026-01-01 00:00:00')")
    _sql(path, "INSERT INTO wl_pref (principal, ref, sort, descending, basis, held, updated_at)"
         " VALUES ('owner', 'list:3', 'change', 1, 'open', 1, 9)")  # fmt: skip
    _sql(path, "INSERT INTO wl_meta VALUES ('flags_version', '5')")


def test_status_on_a_missing_file_changes_nothing(tmp_path):
    path = tmp_path / "db" / "sudarshan_watchlists.db"
    done = _run(path, "--status")
    assert done.returncode == 0, done.stderr
    assert "MISSING" in done.stdout and "Migration needed." in done.stdout
    assert not path.exists() and not path.parent.exists()


def test_a_missing_file_is_created_with_the_current_schema_and_nothing_seeded(tmp_path):
    path = tmp_path / "db" / "sudarshan_watchlists.db"
    done = _run(path)
    assert done.returncode == 0, done.stdout + done.stderr
    names = {r[0] for r in _sql(path, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert names == TABLES
    assert _sql(path, "SELECT name, value FROM wl_meta") == [("schema", "2")]
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
    assert _dump(path)["wl_symbol"] == [("owner", "NSE", "INFY", 2, 5, "results soon", 3, 6, 7)]


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
    assert after["wl_meta"] == sorted([("flags_version", "5"), ("schema", "2")])
    # Added in place at the column's default; the value the owner chose stays.
    assert _sql(copy, "SELECT ref, sort, descending, basis, held FROM wl_pref") == [
        ("list:3", "change", 0, "close", 0)
    ]
    assert after["wl_favourite"] == []
    assert "Up to date. Nothing to do." in _run(copy, "--status").stdout


def _without_figures(path: Path) -> None:
    """``wl_pref`` as schema 1 shipped it before M8.3's figures: no ``cols`` column."""
    _sql(path, "ALTER TABLE wl_pref DROP COLUMN cols")


def test_a_populated_copy_without_the_figures_column_gets_it_and_keeps_every_value(tmp_path):
    path = tmp_path / "w.db"
    assert _run(path).returncode == 0
    _populate(path)
    _without_figures(path)
    owners = _dump(path)
    for sibling in path.parent.glob("w.db-*"):
        sibling.unlink()
    before = path.read_bytes()

    status = _run(path, "--status")
    assert "column wl_pref.cols MISSING" in status.stdout and "Migration needed." in status.stdout
    assert "schema record" not in status.stdout  # a column alone moves no schema
    assert path.read_bytes() == before  # --status changed not a byte

    done = _run(path)
    assert done.returncode == 0, done.stdout + done.stderr
    after = _dump(path)
    assert {t: v for t, v in after.items() if t != "wl_pref"} == {
        t: v for t, v in owners.items() if t != "wl_pref"
    }
    # Every value the owner chose stays; the new column is NULL, which is what each row says.
    assert _sql(path, "SELECT ref, sort, descending, basis, held, cols FROM wl_pref") == [
        ("list:3", "change", 1, "open", 1, None)
    ]
    assert _sql(path, "SELECT value FROM wl_meta WHERE name = 'schema'") == [("2",)]
    again = _run(path)
    assert again.returncode == 0 and "[OK]" not in again.stdout, again.stdout
    assert "Up to date. Nothing to do." in _run(path, "--status").stdout


def test_the_apps_first_use_adds_the_figures_column_too(tmp_path, monkeypatch):
    from sudarshan.watchlists import engine, schema

    path = tmp_path / "w.db"
    assert _run(path).returncode == 0
    _populate(path)
    _without_figures(path)
    monkeypatch.setenv(schema.PATH_ENV, str(path))
    engine.dispose()
    try:
        with engine.open_session() as s:
            got = s.execute(schema.text("SELECT ref, cols FROM wl_pref")).all()
    finally:
        engine.dispose()
    assert [tuple(r) for r in got] == [("list:3", None)]
    assert "Up to date. Nothing to do." in _run(path, "--status").stdout


#: wl_row as schema 1 shipped it (M8.10): four expiry rules, no expiry_on.
ROW_SCHEMA_1 = """CREATE TABLE wl_row (
    id INTEGER NOT NULL, principal VARCHAR(16) DEFAULT 'owner' NOT NULL, list_id INTEGER NOT NULL,
    section_id INTEGER, position INTEGER NOT NULL, kind VARCHAR(16) NOT NULL,
    underlying VARCHAR(80) NOT NULL, underlying_exchange VARCHAR(16) NOT NULL,
    expiry_rule VARCHAR(16) NOT NULL, strike_rule VARCHAR(8) DEFAULT 'atm' NOT NULL,
    "offset" INTEGER DEFAULT '0' NOT NULL, strike_paise BIGINT, opt_right VARCHAR(2),
    created_at BIGINT NOT NULL, updated_at BIGINT NOT NULL, PRIMARY KEY (id),
    CONSTRAINT ck_wl_row_kind CHECK (kind IN ('atm_option', 'month_future', 'strike_pair')),
    CONSTRAINT ck_wl_row_exch CHECK (underlying_exchange IN ('NSE_INDEX', 'NSE', 'BSE_INDEX', 'BSE')),
    CONSTRAINT ck_wl_row_expiry CHECK (expiry_rule IN ('nearest', 'next', 'month', 'next_month')),
    CONSTRAINT ck_wl_row_strike CHECK (strike_rule IN ('atm', 'fixed')),
    CONSTRAINT ck_wl_row_offset CHECK ("offset" BETWEEN -10 AND 10),
    CONSTRAINT ck_wl_row_right CHECK (opt_right IS NULL OR opt_right IN ('CE', 'PE'))
)"""
ROW_COLUMNS = (
    "id, principal, list_id, section_id, position, kind, underlying, underlying_exchange,"
    ' expiry_rule, strike_rule, "offset", strike_paise, opt_right, created_at, updated_at'
)
#: What an owner of schema 1 had: ids that are not 1, 2, 3, a section, a fixed strike, a pair.
ROWS_SCHEMA_1 = [
    (5, "owner", 3, None, 1, "atm_option", "NIFTY", "NSE_INDEX", "nearest", "atm", 2, None, "CE",
     11, 12),
    (9, "owner", 3, 1, 2, "strike_pair", "NIFTY", "NSE_INDEX", "next", "fixed", 0, 2590000, None,
     13, 14),
    (17, "owner", 3, None, 3, "month_future", "SBIN", "NSE", "next_month", "atm", 0, None, None,
     15, 16),
]  # fmt: skip


def _schema_one(path: Path) -> None:
    """The file forced back to schema 1: wl_row as it shipped, with the rows above."""
    _sql(path, "DROP TABLE wl_row")
    _sql(path, ROW_SCHEMA_1)
    _sql(path, "CREATE INDEX ix_wl_row_list ON wl_row (principal, list_id)")
    for row in ROWS_SCHEMA_1:
        _sql(path, f"INSERT INTO wl_row ({ROW_COLUMNS}) VALUES ({', '.join('?' * 15)})", *row)
    _sql(path, "UPDATE wl_meta SET value = '1' WHERE name = 'schema'")


def _rows(path: Path):
    return _sql(path, f"SELECT {ROW_COLUMNS}, expiry_on FROM wl_row ORDER BY id")


def test_a_populated_schema_one_file_is_rebuilt_keeping_every_row_and_its_id(tmp_path):
    path = tmp_path / "w.db"
    assert _run(path).returncode == 0
    _populate(path)
    _schema_one(path)
    others = {t: rows for t, rows in _dump(path).items() if t not in ("wl_row", "wl_meta")}
    for sibling in path.parent.glob("w.db-*"):
        sibling.unlink()
    before = path.read_bytes()

    status = _run(path, "--status")
    assert "table wl_row checks from schema 1 (to 2): rebuild" in status.stdout, status.stdout
    assert "schema record 1 (to 2)" in status.stdout and "Migration needed." in status.stdout
    assert "wl_row.expiry_on" not in status.stdout  # the rebuild brings it
    assert path.read_bytes() == before  # --status changed not a byte

    done = _run(path)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "[OK] table wl_row checks from schema 1 (to 2): rebuild" in done.stdout
    assert _rows(path) == [(*row, None) for row in ROWS_SCHEMA_1]  # every id and value
    assert {t: r for t, r in _dump(path).items() if t not in ("wl_row", "wl_meta")} == others
    assert _sql(path, "SELECT value FROM wl_meta WHERE name = 'schema'") == [("2",)]
    made = _sql(path, "SELECT sql FROM sqlite_master WHERE name = 'wl_row'")[0][0]
    assert "'date'" in made and "ck_wl_row_date" in made
    assert _sql(path, "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'wl_row'"
                " AND name = 'ix_wl_row_list'") == [("ix_wl_row_list",)]  # fmt: skip
    assert _sql(path, "SELECT name FROM sqlite_master WHERE name LIKE 'wl_row__%'") == []

    # The new checks hold: a date needs its day, and only the date rule has one.
    insert = (
        "INSERT INTO wl_row (list_id, position, kind, underlying, underlying_exchange,"
        " expiry_rule, expiry_on, strike_rule, strike_paise, created_at, updated_at)"
        " VALUES (3, 9, 'strike_pair', 'NIFTY', 'NSE_INDEX', ?, ?, 'fixed', 2590000, 1, 1)"
    )
    for rule, day in (("date", None), ("nearest", "2026-12-01")):
        with pytest.raises(sqlite3.IntegrityError):
            _sql(path, insert, rule, day)
    _sql(path, insert, "date", "2026-12-01")
    assert _sql(path, "SELECT max(id) FROM wl_row") == [(18,)]  # after the ids kept

    again = _run(path)
    assert again.returncode == 0 and "[OK]" not in again.stdout, again.stdout
    assert "Up to date. Nothing to do." in _run(path, "--status").stdout


def test_schema_one_code_refuses_a_schema_two_file_untouched(tmp_path, monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    from sudarshan.watchlists import engine, schema

    path = tmp_path / "w.db"
    assert _run(path).returncode == 0  # schema 2
    before = path.read_bytes()
    monkeypatch.setattr(schema, "SCHEMA_VERSION", 1)  # the release before this one
    eng = create_engine(f"sqlite:///{path}", poolclass=NullPool)
    try:
        with pytest.raises(engine.RecordTooNew):
            engine.prepare(eng)
    finally:
        eng.dispose()
    assert path.read_bytes() == before


def test_the_apps_first_use_rebuilds_a_schema_one_file_the_same_way(tmp_path, monkeypatch):
    from sudarshan.watchlists import engine, schema

    path = tmp_path / "w.db"
    assert _run(path).returncode == 0
    _populate(path)
    _schema_one(path)
    monkeypatch.setenv(schema.PATH_ENV, str(path))
    engine.dispose()
    try:
        with engine.open_session() as s:
            s.execute(schema.text("SELECT 1"))
    finally:
        engine.dispose()
    assert _rows(path) == [(*row, None) for row in ROWS_SCHEMA_1]
    assert "'date'" in _sql(path, "SELECT sql FROM sqlite_master WHERE name = 'wl_row'")[0][0]
    assert _sql(path, "SELECT value FROM wl_meta WHERE name = 'schema'") == [("2",)]
    assert "Up to date. Nothing to do." in _run(path, "--status").stdout


def test_a_schema_record_the_owner_cannot_have_set_is_never_lowered_or_written_over(tmp_path):
    path = tmp_path / "w.db"
    assert _run(path).returncode == 0
    _sql(path, "UPDATE wl_meta SET value = '3' WHERE name = 'schema'")
    _sql(path, "CREATE TABLE wl_future (x INTEGER)")
    before = path.read_bytes()
    done = _run(path)
    assert done.returncode == 1
    assert "newer SUDARSHAN release" in done.stdout and "[FAIL]" in done.stdout
    status = _run(path, "--status")
    assert "newer SUDARSHAN release" in status.stdout
    assert _sql(path, "SELECT value FROM wl_meta WHERE name = 'schema'") == [("3",)]
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
