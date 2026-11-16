"""upgrade/migrate_sudarshan_charts.py on a missing, a populated, a forced-back and a newer file
(M4.18, T7a). Each case runs the script as migrate_all.py does, in a process of its own."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "upgrade" / "migrate_sudarshan_charts.py"
TABLES = {"chart_doc", "chart_meta"}
#: What an owner who has used the store for a month would have, values the code never defaults.
ROWS = [
    ("owner", "drawings", "NSE:INFY", 1794000000123, '{"version":2,"drawings":[{"id":"a"}]}', 11),
    ("owner", "phone_set", "phone", 1794000000456, '{"version":1,"studies":[]}', 22),
    ("owner", "workspace", "oa-trading%3Aowner", 1794000000789,
     '{"version":1,"revision":9,"workspaces":[],"templates":[]}', 33),
]  # fmt: skip


def _run(path: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "SUDARSHAN_CHARTS_DB"}
    env["SUDARSHAN_CHARTS_DB"] = str(path)
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
    for row in ROWS:
        _sql(path, "INSERT INTO chart_doc VALUES (?, ?, ?, ?, ?, ?)", *row)


def test_status_on_a_missing_file_changes_nothing(tmp_path):
    path = tmp_path / "db" / "sudarshan_charts.db"
    done = _run(path, "--status")
    assert done.returncode == 0, done.stderr
    assert "MISSING" in done.stdout and "Migration needed." in done.stdout
    assert not path.exists() and not path.parent.exists()


def test_a_missing_file_is_created_with_schema_one_and_nothing_seeded(tmp_path):
    path = tmp_path / "db" / "sudarshan_charts.db"
    done = _run(path)
    assert done.returncode == 0, done.stdout + done.stderr
    names = {r[0] for r in _sql(path, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert names == TABLES
    assert _sql(path, "SELECT name, value FROM chart_meta") == [("schema", "1")]
    assert _sql(path, "SELECT count(*) FROM chart_doc") == [(0,)]
    assert "Up to date. Nothing to do." in _run(path, "--status").stdout


def test_the_file_the_migration_makes_is_the_one_the_store_makes(tmp_path, monkeypatch):
    from sudarshan.charts import schema, store

    by_script = tmp_path / "script.db"
    assert _run(by_script).returncode == 0
    by_store = tmp_path / "store.db"
    monkeypatch.setenv(schema.PATH_ENV, str(by_store))
    store.dispose()
    try:
        store.ready()
    finally:
        store.dispose()
    ddl = "SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name"
    assert _sql(by_script, ddl) == _sql(by_store, ddl)


def test_a_second_run_changes_nothing_and_says_so(tmp_path):
    path = tmp_path / "c.db"
    assert _run(path).returncode == 0
    _populate(path)
    before = _dump(path)
    done = _run(path)
    assert done.returncode == 0 and "[OK]" not in done.stdout, done.stdout
    assert _dump(path) == before


def test_status_changes_not_a_byte_of_an_existing_file(tmp_path):
    path = tmp_path / "c.db"
    assert _run(path).returncode == 0
    _populate(path)
    for sibling in path.parent.glob("c.db-*"):
        sibling.unlink()  # as the app leaves it at rest
    before = path.read_bytes()
    done = _run(path, "--status")
    assert done.returncode == 0 and "Up to date. Nothing to do." in done.stdout, done.stdout
    assert path.read_bytes() == before
    assert _dump(path)["chart_doc"] == sorted(ROWS)


def test_a_populated_copy_forced_back_keeps_every_row_and_value(tmp_path):
    path = tmp_path / "c.db"
    assert _run(path).returncode == 0
    _populate(path)
    owners = _dump(path)["chart_doc"]
    # The old state: the meta table gone with its schema record, as a file made before it existed.
    _sql(path, "DROP TABLE chart_meta")
    copy = tmp_path / "copy.db"
    shutil.copyfile(path, copy)

    status = _run(copy, "--status")
    assert "Migration needed." in status.stdout
    for line in ("table chart_meta MISSING", "schema record MISSING"):
        assert line in status.stdout, status.stdout
    assert _run(copy, "--status").stdout == status.stdout  # reading changed nothing

    done = _run(copy)
    assert done.returncode == 0, done.stdout + done.stderr
    after = _dump(copy)
    assert after["chart_doc"] == owners
    assert after["chart_meta"] == [("schema", "1")]
    assert "Up to date. Nothing to do." in _run(copy, "--status").stdout


def test_a_schema_record_written_lower_is_raised_and_nothing_else_moves(tmp_path):
    path = tmp_path / "c.db"
    assert _run(path).returncode == 0
    _populate(path)
    _sql(path, "UPDATE chart_meta SET value = '0' WHERE name = 'schema'")
    assert "schema record 0 (to 1)" in _run(path, "--status").stdout
    assert _run(path).returncode == 0
    assert _sql(path, "SELECT value FROM chart_meta WHERE name = 'schema'") == [("1",)]
    assert _dump(path)["chart_doc"] == sorted(ROWS)


def test_a_file_from_a_newer_release_is_refused_untouched(tmp_path):
    path = tmp_path / "c.db"
    assert _run(path).returncode == 0
    _populate(path)
    _sql(path, "UPDATE chart_meta SET value = '2' WHERE name = 'schema'")
    for sibling in path.parent.glob("c.db-*"):
        sibling.unlink()
    before = path.read_bytes()
    status = _run(path, "--status")
    assert "newer release" in status.stdout and "Up to date" not in status.stdout
    done = _run(path)
    assert done.returncode == 1 and "[FAIL]" in done.stdout and "newer release" in done.stdout
    assert path.read_bytes() == before
