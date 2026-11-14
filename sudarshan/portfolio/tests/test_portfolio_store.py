"""portfolio.db: the file, migrations by user_version, --status, one writer, principals (PB1)."""

from __future__ import annotations

import ast
import contextlib
import inspect
import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

import database
from sudarshan.portfolio import migrate, migrations, store
from sudarshan.portfolio.store import (
    DB_FILENAME,
    WORDS,
    NotFound,
    PortfolioStore,
    Scope,
    StoreError,
    UnknownPrincipal,
    VersionConflict,
)
from sudarshan.tests.fdwatch import FdWatch

NOW = 1_793_000_000_000
OWNER = Scope.owner()
ALL_STEPS = [number for number, _ in migrations.STEPS]
VIEWER = Scope("viewer")


@pytest.fixture(autouse=True)
def _stop_thread():
    yield
    store.stop()


@pytest.fixture
def path(tmp_path):
    return tmp_path / "state" / DB_FILENAME


@pytest.fixture
def made(path):
    opened = PortfolioStore(path, now=NOW)
    yield opened
    opened.close()


def raw(path, sql: str):
    with contextlib.closing(sqlite3.connect(path)) as conn, conn:
        return conn.execute(sql).fetchall()


def snapshot(directory: Path) -> dict:
    return {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in directory.iterdir()}


# ---- the file and its schema ---------------------------------------------------------------
def test_a_fresh_file_is_private_at_the_latest_version_and_seeded(path, made):
    assert made.applied == ALL_STEPS and raw(path, "PRAGMA user_version") == [(migrations.LATEST,)]
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert os.stat(path.parent).st_mode & 0o777 == 0o700
    assert raw(path, "PRAGMA journal_mode") == [("wal",)]
    assert made.seeded == {"owner": ["member", "account", "tab"]}
    assert raw(path, "SELECT name FROM member") == [("Me",)]
    assert raw(path, "SELECT name, kind, system FROM account") == [("Upstox", "upstox", 1)]
    assert raw(path, "SELECT name, kind, system, position FROM tab") == [("Upstox", "upstox", 1, 0)]


def test_running_twice_changes_nothing(path, made):
    made.close()
    again = PortfolioStore(path, now=NOW + 1)
    assert again.applied == [] and again.seeded == {"owner": []}
    assert raw(path, "SELECT count(*) FROM tab") == [(1,)]
    again.close()


def test_a_populated_copy_forced_back_a_version_is_migrated_without_losing_a_row(path, made):
    made.write(OWNER, lambda tx: tx.insert("tab", _tab("Long term"), NOW))
    made.close()
    raw(path, "PRAGMA user_version = 0")
    before = raw(path, "SELECT id, name, version FROM tab ORDER BY id")
    again = PortfolioStore(path, now=NOW + 5)
    assert again.applied == ALL_STEPS
    assert raw(path, "SELECT id, name, version FROM tab ORDER BY id") == before
    assert raw(path, "SELECT count(*) FROM member") == [(1,)]  # the seeds did not repeat
    again.close()


def test_a_seed_never_touches_what_the_owner_changed(path, made):
    made.write(OWNER, lambda tx: tx.change("member", 1, 1, {"name": "Account owner"}, NOW + 1))
    made.close()
    again = PortfolioStore(path, now=NOW + 9)
    assert raw(path, "SELECT name, version FROM member") == [("Account owner", 2)]
    again.close()


def test_a_file_newer_than_the_code_is_refused_and_left_alone(path, made):
    made.close()
    raw(path, "PRAGMA user_version = 99")
    stamp = snapshot(path.parent)
    with pytest.raises(migrations.SchemaTooNew) as caught:
        PortfolioStore(path)
    assert (
        str(caught.value)
        == WORDS["newer"]
        == (
            "Your portfolio data was written by a newer release. Install that release again; "
            "nothing was changed."
        )
    )
    assert caught.value.cause == f"file version 99, this release knows up to {migrations.LATEST}"
    assert raw(path, "PRAGMA user_version") == [(99,)]
    assert {k: v for k, v in snapshot(path.parent).items() if not k.endswith("-shm")} == {
        k: v for k, v in stamp.items() if not k.endswith("-shm")
    }


def _hold_first(statement_start: str, names: set[str], gate: threading.Barrier):
    held = set()

    def hold(conn, cursor, statement, parameters, context, executemany):
        name = threading.current_thread().name
        if name in names and name not in held and statement.startswith(statement_start):
            held.add(name)
            with contextlib.suppress(threading.BrokenBarrierError):
                gate.wait(timeout=0.5)

    return hold


def _race(*calls) -> list:
    results: list = [None] * len(calls)

    def run(n, call):
        try:
            results[n] = call()
        except Exception as exc:  # noqa: BLE001 - the result is what the test inspects
            results[n] = exc

    threads = [
        threading.Thread(target=run, args=(n, call), name=f"race-{n}")
        for n, call in enumerate(calls)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return results


def test_two_openers_of_a_new_file_at_once_both_start(path, monkeypatch):
    """Both reach the first step together: the second waits for the first, then finds it done."""
    monkeypatch.setattr(database, "LOCK_RETRY_BUDGET_S", 3.0)
    hook = _hold_first("CREATE TABLE", {"race-0", "race-1"}, threading.Barrier(2))
    event.listen(Engine, "before_cursor_execute", hook)
    try:
        results = _race(lambda: _open(path), lambda: _open(path))
    finally:
        event.remove(Engine, "before_cursor_execute", hook)
    assert not [r for r in results if isinstance(r, BaseException)], results
    # Each step is its own transaction, so who takes the next one after a commit is up to the
    # lock: what is guaranteed is that every step ran once in all, never which opener ran it.
    assert sorted(n for r in results for n in r[0]) == ALL_STEPS
    assert raw(path, "PRAGMA user_version") == [(migrations.LATEST,)]
    assert raw(path, "SELECT count(*) FROM tab") == [(1,)]  # seeded once


def test_the_steps_run_once_in_all_even_when_the_second_opener_takes_the_later_ones(
    path, monkeypatch
):
    """The split the test above used to demand ([0, all]) is not promised: held here by hand, the
    first opener runs step 1, the second wins the lock after that commit and runs the rest."""
    if len(ALL_STEPS) < 3:
        pytest.skip("the split needs a first step, a second and a later one; this file has fewer")
    monkeypatch.setattr(database, "LOCK_RETRY_BUDGET_S", 10.0)
    first_done, second_done = threading.Event(), threading.Event()
    begins = {"race-0": 0, "race-1": 0}

    def hold(conn, cursor, statement, parameters, context, executemany):
        name = threading.current_thread().name
        if name not in begins:
            return
        if statement.startswith("BEGIN IMMEDIATE"):
            begins[name] += 1
            if name == "race-1" and begins[name] == 1:
                first_done.wait(timeout=10)  # the first opener has step 1 in hand
            if name == "race-0" and begins[name] == 2:
                second_done.wait(timeout=10)  # ... and lets the second take the rest
        elif statement.startswith("PRAGMA user_version = ") and name == "race-0":
            first_done.set()
        elif statement == f"PRAGMA user_version = {ALL_STEPS[-1]}" and name == "race-1":
            second_done.set()

    event.listen(Engine, "before_cursor_execute", hold)
    try:
        results = _race(lambda: _open(path), lambda: _open(path))
    finally:
        event.remove(Engine, "before_cursor_execute", hold)
    assert not [r for r in results if isinstance(r, BaseException)], results
    assert [r[0] for r in results] == [ALL_STEPS[:1], ALL_STEPS[1:]]
    assert raw(path, "PRAGMA user_version") == [(migrations.LATEST,)]
    assert raw(path, "SELECT count(*) FROM tab") == [(1,)]


def _open(path):
    opened = PortfolioStore(path, now=NOW)
    try:
        return opened.applied, opened.counts(OWNER)["tab"]
    finally:
        opened.close()


def test_two_writers_held_at_their_first_write_queue_instead_of_failing(made, monkeypatch):
    monkeypatch.setattr(database, "LOCK_RETRY_BUDGET_S", 3.0)
    hook = _hold_first("INSERT", {"race-0", "race-1"}, threading.Barrier(2))
    event.listen(made.engine, "before_cursor_execute", hook)
    start = time.monotonic()

    def add(name):
        return lambda: made._write_here(OWNER, lambda tx: tx.insert("tab", _tab(name), NOW)["id"])

    results = _race(add("a"), add("b"))
    event.remove(made.engine, "before_cursor_execute", hook)
    assert all(isinstance(r, int) for r in results), results
    assert time.monotonic() - start < 2.5
    assert made.counts(OWNER)["tab"] == 3


def test_every_call_runs_on_the_one_store_thread_and_stop_shuts_it_down(made):
    names = {
        made.read(OWNER, lambda tx: threading.current_thread().name),
        made.write(OWNER, lambda tx: threading.current_thread().name),
    }
    assert len(names) == 1 and names.pop().startswith(store.THREAD_PREFIX)
    pool = store._pool
    store.stop()
    assert store._pool is None and pool._shutdown  # noqa: SLF001


def test_a_failed_write_leaves_nothing_behind(made):
    def broken(tx):
        tx.insert("tab", _tab("half"), NOW)
        raise RuntimeError("mid-write")

    with pytest.raises(RuntimeError):
        made.write(OWNER, broken)
    assert made.read(OWNER, lambda tx: tx.rows("tab", name="half")) == []


def test_a_record_that_cannot_be_opened_says_so_in_words(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(StoreError) as caught:
        PortfolioStore(blocker / "inside" / DB_FILENAME)
    assert (
        str(caught.value)
        == WORDS["opened"]
        == (
            "Your portfolio data could not be opened. Nothing was changed; check the service on "
            "the server."
        )
    )
    assert caught.value.cause and "inside" not in str(caught.value)


def test_a_write_that_the_disk_refuses_says_so_in_words(made, monkeypatch):
    from sqlalchemy import create_engine

    made.writer = store.writer(create_engine("sqlite:////nonexistent/dir/portfolio.db"))
    with pytest.raises(StoreError) as caught:
        made.write(OWNER, lambda tx: tx.insert("tab", _tab("x"), NOW))
    assert str(caught.value) == WORDS["written"]
    assert "unable to open" in caught.value.cause


def test_the_default_path_follows_the_state_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("SUDARSHAN_PORTFOLIO_STATE_DIR", raising=False)
    monkeypatch.setenv("STATE_DIRECTORY", str(tmp_path))
    assert store.default_path() == tmp_path / DB_FILENAME


# ---- --status ------------------------------------------------------------------------------
def test_status_of_a_missing_file_makes_nothing(tmp_path, capsys):
    target = tmp_path / "state" / DB_FILENAME
    assert migrate.main(["--status", "--path", str(target)]) == 0
    out = capsys.readouterr().out
    assert f"file none, code {migrations.LATEST}" in out and f"pending: {ALL_STEPS}" in out
    assert not target.parent.exists()


def test_status_changes_nothing_in_the_directory(path, made, capsys):
    made.close()
    before = snapshot(path.parent)
    assert migrate.main(["--status", "--path", str(path)]) == 0
    out = capsys.readouterr().out
    assert f"file {migrations.LATEST}, code {migrations.LATEST}" in out and "pending: none" in out
    assert snapshot(path.parent) == before


def test_status_of_an_old_file_lists_the_pending_step_and_leaves_it(path, made, capsys):
    made.close()
    raw(path, "PRAGMA user_version = 0")
    before = snapshot(path.parent)
    assert migrate.main(["--status", "--path", str(path)]) == 0
    assert f"pending: {ALL_STEPS}" in capsys.readouterr().out
    assert snapshot(path.parent) == before and raw(path, "PRAGMA user_version") == [(0,)]


def test_status_of_a_newer_file_says_so(path, made, capsys):
    made.close()
    raw(path, "PRAGMA user_version = 7")
    assert migrate.main(["--status", "--path", str(path)]) == 2
    assert WORDS["newer"] in capsys.readouterr().out


def test_the_read_only_engine_cannot_write(path, made):
    made.close()
    ro = store.open_read_only(path)
    with ro.connect() as conn, pytest.raises(Exception, match="readonly|read-only"):
        conn.exec_driver_sql("DELETE FROM member")
    ro.dispose()


def test_applying_migrates_and_refuses_while_the_service_holds_its_port(path, capsys):
    import socket

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        assert migrate.main(["--path", str(path), "--port", str(port)]) == 2
        assert not path.exists()
    assert "stop it first" in capsys.readouterr().out
    assert migrate.main(["--path", str(path), "--port", "1"]) == 0
    assert raw(path, "PRAGMA user_version") == [(migrations.LATEST,)]


# ---- principal -----------------------------------------------------------------------------
def _tab(name, **more):
    return {"name": name, "kind": "stocks", "icon": "briefcase", "colour": "brand",
            "position": 5, **more}  # fmt: skip


#: Upkeep, not readers: they touch no one's rows.
UPKEEP = {"close"}


def test_every_public_method_takes_the_scope_first():
    for name, method in inspect.getmembers(PortfolioStore, inspect.isfunction):
        if name.startswith("_") or name in UPKEEP:
            continue
        assert list(inspect.signature(method).parameters)[:2] == ["self", "scope"], name


def test_every_query_of_a_transaction_filters_by_principal():
    """Each Tx method that reads or writes a table does it through ``_mine`` (or names the
    principal itself when it inserts); a new method that forgets fails here."""
    tree = ast.parse(inspect.getsource(store.Tx))
    checked = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name.startswith("__"):
            continue
        calls = {
            n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "")
            for n in ast.walk(node)
            if isinstance(n, ast.Call)
        }
        if not calls & {"select", "update", "delete", "insert"}:
            continue
        text = ast.unparse(node)
        if any(f"_t('{table}')" in text for table in migrations.SHARED_TABLES):
            # market data and the job log belong to no one (migrations.SHARED_TABLES)
            assert "principal" not in text, node.name
            continue
        assert "_mine(" in text or "self.who" in text, node.name
        checked += 1
    assert checked >= 12


def test_the_store_refuses_anyone_it_was_not_opened_for(made):
    with pytest.raises(UnknownPrincipal):
        made.counts(VIEWER)
    with pytest.raises(UnknownPrincipal):
        made.write(VIEWER, lambda tx: tx.insert("tab", _tab("x"), NOW))


def test_one_principal_never_reads_or_changes_anothers_rows(path):
    both = PortfolioStore(path, principals=("owner", "viewer"), now=NOW)
    mine = both.write(OWNER, lambda tx: tx.insert("tab", _tab("Mine"), NOW))
    theirs = both.write(VIEWER, lambda tx: tx.insert("tab", _tab("Theirs"), NOW))
    assert both.seeded["viewer"] == ["member", "account", "tab"]  # each has their own Me
    names = lambda scope: [r["name"] for r in both.read(scope, lambda tx: tx.rows("tab"))]  # noqa: E731
    assert names(OWNER) == ["Upstox", "Mine"] and names(VIEWER) == ["Upstox", "Theirs"]
    assert both.read(OWNER, lambda tx: tx.get("tab", theirs["id"])) is None
    assert both.read(OWNER, lambda tx: tx.count("tab")) == 2
    assert both.read(VIEWER, lambda tx: tx.by_request("tab", "none")) is None
    with pytest.raises(NotFound):
        both.write(OWNER, lambda tx: tx.change("tab", theirs["id"], 1, {"name": "taken"}, NOW))
    assert not both.write(OWNER, lambda tx: tx.remove("tab", theirs["id"]))
    assert both.read(VIEWER, lambda tx: tx.get("tab", theirs["id"]))["name"] == "Theirs"
    assert both.read(OWNER, lambda tx: tx.get("tab", mine["id"]))["name"] == "Mine"
    both.close()


def test_a_scope_with_tabs_sees_only_those_tabs_and_their_entries(path):
    opened = PortfolioStore(path, now=NOW)
    one = opened.write(OWNER, lambda tx: tx.insert("tab", _tab("One"), NOW))
    opened.write(OWNER, lambda tx: tx.insert("tab", _tab("Two"), NOW))
    narrow = Scope("owner", frozenset({one["id"]}))
    assert [r["name"] for r in opened.read(narrow, lambda tx: tx.rows("tab"))] == ["One"]
    opened.close()


def test_every_owner_table_has_a_principal_and_stamps():
    sql = " ".join(migrations.V1)
    import re

    names = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n    \)", sql, re.S)
    assert len(names) == 16
    for table, body in names:
        if table in migrations.SHARED_TABLES:
            assert "principal" not in body, table
        else:
            assert "principal TEXT NOT NULL" in body, table
            assert "created_at INTEGER NOT NULL" in body and "updated_at" in body, table


# ---- the version guard ---------------------------------------------------------------------
def test_a_change_from_a_stale_version_writes_nothing(made):
    row = made.write(OWNER, lambda tx: tx.insert("tab", _tab("T"), NOW))
    made.write(OWNER, lambda tx: tx.change("tab", row["id"], 1, {"name": "T2"}, NOW))
    with pytest.raises(VersionConflict) as caught:
        made.write(OWNER, lambda tx: tx.change("tab", row["id"], 1, {"name": "T3"}, NOW))
    assert str(caught.value) == (
        "This item was changed on another screen. Nothing was saved; open it again and repeat "
        "the change."
    )
    assert made.read(OWNER, lambda tx: tx.get("tab", row["id"]))["name"] == "T2"
    assert made.read(OWNER, lambda tx: tx.get("tab", row["id"]))["version"] == 2


def test_a_retried_create_is_found_by_its_request_id(made):
    row = made.write(OWNER, lambda tx: tx.insert("tab", _tab("T"), NOW, request_id="r-1"))
    assert made.read(OWNER, lambda tx: tx.by_request("tab", "r-1")) == row
    assert "request_id" not in row and "principal" not in row
    with pytest.raises(StoreError):  # the unique index is the net under the check
        made.write(OWNER, lambda tx: tx.insert("tab", _tab("T"), NOW, request_id="r-1"))


# ---- descriptors ---------------------------------------------------------------------------
def test_opening_using_and_closing_leaks_no_descriptors(path):
    PortfolioStore(path, now=NOW).close()  # warm: the thread, imports, the file itself
    watch = FdWatch()
    for _ in range(15):
        opened = PortfolioStore(path, now=NOW)
        opened.write(OWNER, lambda tx: tx.rows("tab"))
        opened.read(OWNER, lambda tx: tx.count("tab"))
        opened.close()
        migrate.file_version(path)
    watch.assert_clean()
    store.stop()
    assert not [t for t in threading.enumerate() if t.name.startswith(store.THREAD_PREFIX)]
