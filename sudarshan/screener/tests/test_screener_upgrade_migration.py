"""Schema step 5 (alerts not looked at) and upgrade/migrate_sudarshan_screener.py (GA7.6).

The step is tried on a populated file forced back to schema 4, not only on a new one: the runs an
earlier release recorded are filled in from what each says of itself, nothing else changes, and a
second run changes nothing. The upgrade script is run as migrate_all.py runs it, in a process of
its own: it reports with ``--status`` without touching the file, applies the step, and leaves a
missing file, a newer file and a running service alone, each in words.
"""

from __future__ import annotations

import os
import shutil
import socket
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
from test_screener_results import judged, make, record
from test_screener_results import service as service  # noqa: F401  (the fixture)

from sudarshan.screener import migrations, words
from sudarshan.screener import store as store_module
from sudarshan.screener.store import OWNER, ScreenerStore

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "upgrade" / "migrate_sudarshan_screener.py"


def _sql(path: Path, sql: str, *args):
    with closing(sqlite3.connect(path)) as db, db:
        return db.execute(sql, args).fetchall()


def _dump(path: Path, without: tuple[str, ...] = ()) -> dict:
    """Every table's rows; ``without`` leaves columns of ``run`` out."""
    names = [r[0] for r in _sql(path, "SELECT name FROM sqlite_master WHERE type='table'")]
    out = {}
    for name in names:
        cols = [c[1] for c in _sql(path, f"PRAGMA table_info({name})")]
        cols = [c for c in cols if name != "run" or c not in without]
        listed = ", ".join(f'"{c}"' for c in cols)
        out[name] = sorted(_sql(path, f"SELECT {listed} FROM {name}"), key=repr)
    return out


def _free_port() -> int:
    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _run(path: Path, *args: str, port: int | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.endswith("STATE_DIRECTORY")}
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--path", str(path), "--port", str(port or _free_port()),
         *args],
        cwd=REPO, env=env, capture_output=True, text=True, timeout=120,
    )  # fmt: skip


@pytest.fixture
def old_file(service, tmp_path):  # noqa: F811
    """A file an owner has used, forced back to schema 4: three runs of two screens, one that
    raised an alert, one that raised none and one whose alert was never sent."""
    one, two = make(service), make(service, name="Other", target=None)
    raised = record(service, one, 1, judged({"AAA": 100, "BBB": 200}))
    quiet = record(service, one, 2, judged({"AAA": 100, "BBB": 200}))
    dropped = record(service, two, 1, judged({"CCC": 100}))
    service.store.write(
        OWNER, lambda tx: tx.run_alert_words(dropped.run_key, words.untold_words(409))
    )
    path = Path(service.store.path)
    service.store.close()
    store_module.stop()
    copy = tmp_path / "copy" / "screener.db"
    copy.parent.mkdir()
    shutil.copy(path, copy)
    _sql(copy, "DROP INDEX run_alert_new")
    _sql(copy, "ALTER TABLE run DROP COLUMN alerted")
    _sql(copy, "ALTER TABLE run DROP COLUMN alert_seen_at")
    _sql(copy, "PRAGMA user_version = 4")
    assert "alerted" not in {c[1] for c in _sql(copy, "PRAGMA table_info(run)")}
    # the fixture's teardown closes the store again; give it one to close
    service.store = ScreenerStore(path)
    return copy, {"raised": raised.run_id, "quiet": quiet.run_id, "dropped": dropped.run_id}


def marks_of(path: Path) -> dict[int, tuple]:
    return {
        r[0]: (r[1], r[2], r[3])
        for r in _sql(path, "SELECT id, alerted, alert_seen_at, ran_at FROM run")
    }


def test_the_step_is_the_latest_and_adds_two_columns(tmp_path):
    assert migrations.LATEST == 5
    made = ScreenerStore(tmp_path / "screener.db")
    try:
        assert made.applied == [1, 2, 3, 4, 5]
    finally:
        made.close()
        store_module.stop()
    cols = {c[1]: c for c in _sql(tmp_path / "screener.db", "PRAGMA table_info(run)")}
    assert cols["alerted"][3] == 1 and cols["alerted"][4] == "0" and cols["alert_seen_at"][3] == 0
    indexes = {r[1] for r in _sql(tmp_path / "screener.db", "PRAGMA index_list(run)")}
    assert "run_alert_new" in indexes


def test_a_populated_file_forced_back_is_filled_in_from_its_own_runs(old_file):
    path, runs = old_file
    before = _dump(path)
    status = _run(path, "--status")
    assert status.returncode == 0 and "step 5 MISSING" in status.stdout
    assert "Migration needed." in status.stdout
    assert _dump(path) == before, "--status changed the file"
    assert _sql(path, "PRAGMA user_version") == [(4,)]

    applied = _run(path)
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert "[OK] step 5" in applied.stdout and "Migration complete." in applied.stdout
    assert _sql(path, "PRAGMA user_version") == [(5,)]
    after = marks_of(path)
    told, seen, ran = after[runs["raised"]]
    assert (told, seen) == (1, ran), "a run that raised an alert: marked, and as looked at"
    assert after[runs["quiet"]][:2] == (0, None)
    assert after[runs["dropped"]][:2] == (0, None), "an alert never sent was counted as one"
    # nothing else moved: every table as it was, the two new columns aside
    assert _dump(path, without=("alerted", "alert_seen_at")) == before
    # the count starts at nothing: the history is not the owner's first sight of it
    opened = ScreenerStore(path)
    try:
        assert opened.read(OWNER, lambda tx: tx.alerts_unseen()) == {}
    finally:
        opened.close()
        store_module.stop()

    again = _run(path)
    assert again.returncode == 0 and "up to date" in again.stdout
    assert marks_of(path) == after
    assert "Up to date. Nothing to do." in _run(path, "--status").stdout


def test_the_step_run_again_keeps_what_the_owner_has_since_looked_at(old_file):
    path, runs = old_file
    assert _run(path).returncode == 0
    # an alert raised after the upgrade and not looked at, and the file forced to run the step again
    _sql(path, "UPDATE run SET alerted = 1, alert_seen_at = NULL WHERE id = ?", runs["quiet"])
    _sql(path, "PRAGMA user_version = 4")
    assert _run(path).returncode == 0
    after = marks_of(path)
    assert after[runs["quiet"]][:2] == (1, None), "the step marked a new alert as looked at"
    assert after[runs["raised"]][0] == 1


def test_no_file_a_newer_file_and_a_running_service_are_left_alone(old_file, tmp_path):
    path, _runs = old_file
    missing = tmp_path / "nowhere" / "screener.db"
    for args in ((), ("--status",)):
        got = _run(missing, *args)
        assert got.returncode == 0 and "has not been switched on" in got.stdout
    assert not missing.exists() and not missing.parent.exists()

    # the service is running: it holds the file and migrates it itself
    before = _dump(path)
    with closing(socket.socket()) as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        got = _run(path, port=held.getsockname()[1])
    assert got.returncode == 0 and "sudarshan-screener is running" in got.stdout
    assert _dump(path) == before and _sql(path, "PRAGMA user_version") == [(4,)]

    newer = tmp_path / "newer.db"
    shutil.copy(path, newer)
    _sql(newer, "PRAGMA user_version = 99")
    before = _dump(newer)
    got = _run(newer)
    assert got.returncode == 1 and "newer SUDARSHAN release" in got.stdout
    assert "newer SUDARSHAN release" in _run(newer, "--status").stdout
    assert _dump(newer) == before and _sql(newer, "PRAGMA user_version") == [(99,)]


def test_the_output_names_no_folder_of_the_server(old_file):
    path, _runs = old_file
    out = _run(path, "--status").stdout + _run(path).stdout
    assert str(path.parent) not in out and "File: screener.db" in out


def test_migrate_all_lists_it_and_does_not_require_it():
    sys.path.insert(0, str(REPO / "upgrade"))
    try:
        import migrate_all
    finally:
        sys.path.pop(0)
    names = [script for script, _description in migrate_all.MIGRATIONS]
    assert "migrate_sudarshan_screener.py" in names
    assert "migrate_sudarshan_screener.py" not in migrate_all.REQUIRED_MIGRATIONS
