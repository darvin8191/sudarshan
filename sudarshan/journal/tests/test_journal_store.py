"""journal.db (JB1): the file, migrations by user_version, --status, one writer, principals,
versions, request ids and the first-start seeds (design 4.1-4.3)."""

from __future__ import annotations

import ast
import contextlib
import inspect
import re
import socket
import sqlite3
import textwrap
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

import database
from sudarshan.journal import migrate, migrations, store
from sudarshan.journal.store import (
    DB_FILENAME,
    JournalStore,
    NotFound,
    Scope,
    StoreError,
    UnknownPrincipal,
    VersionConflict,
)
from sudarshan.tests.fdwatch import FdWatch

NOW = 1_794_000_000_000
OWNER = Scope.owner()
VIEWER = Scope("viewer")
ALL_STEPS = [number for number, _ in migrations.STEPS]


@pytest.fixture(autouse=True)
def _stop_thread():
    yield
    store.stop()


@pytest.fixture
def path(tmp_path):
    return tmp_path / "state" / DB_FILENAME


@pytest.fixture
def made(path):
    opened = JournalStore(path, now=NOW)
    yield opened
    opened.close()


def raw(path, sql: str, params=()):
    with contextlib.closing(sqlite3.connect(path)) as conn, conn:
        return conn.execute(sql, params).fetchall()


def snapshot(directory: Path) -> dict:
    return {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in directory.iterdir()}


def _tag(name: str, kind: str = "mistake") -> dict:
    return {"kind": kind, "name": name, "colour": "neutral"}


def _fill(trade_id: str, **more) -> dict:
    return {
        "mode": "live", "day": 20261109, "trade_id": trade_id, "order_id": "o-" + trade_id,
        "exchange": "NFO", "symbol": "NIFTY25NOV2625000CE", "instrument_key": "NSE_FO|1",
        "product": "MIS", "action": "BUY", "qty": 65, "price_u": 1_000_000,
        "value_paise": 650_000, "exch_ts_ms": NOW, "source": "stream_read", **more,
    }  # fmt: skip


# ---- the file and its schema ---------------------------------------------------------------
def test_a_fresh_file_is_private_at_the_latest_version_and_seeded(path, made):
    assert (path.parent.stat().st_mode & 0o777) == 0o700
    assert (path.stat().st_mode & 0o777) == 0o600
    assert raw(path, "PRAGMA user_version") == [(migrations.LATEST,)]
    assert raw(path, "PRAGMA journal_mode") == [("wal",)]
    assert made.applied == ALL_STEPS
    assert made.seeded == {"owner": ["tags", "checklist", "settings"]}
    tags = made.read(OWNER, lambda tx: tx.rows("tag"))
    assert [t["name"] for t in tags] == list(migrations.MISTAKE_TAGS)
    assert {t["kind"] for t in tags} == {"mistake"} and all(t["system"] for t in tags)
    (general,) = made.read(OWNER, lambda tx: tx.rows("playbook"))
    assert general["name"] == "General" and general["rules"] == list(migrations.DEFAULT_CHECKLIST)
    settings = made.read(OWNER, lambda tx: tx.settings())
    assert settings["version"] == 1 and settings["eod_on"] and settings["charges_plan"] == "plus"
    assert settings["capital_base_paise"] is None


def test_running_twice_changes_nothing(path, made):
    made.close()
    again = JournalStore(path, now=NOW + 1)
    assert again.applied == [] and again.seeded == {"owner": []}
    assert again.read(OWNER, lambda tx: tx.count("tag")) == len(migrations.MISTAKE_TAGS)
    assert again.read(OWNER, lambda tx: tx.count("playbook")) == 1
    again.close()


def test_a_populated_copy_forced_back_a_version_is_migrated_without_losing_a_row(path, made):
    made.write(OWNER, lambda tx: tx.insert("fill", _fill("t1"), NOW))
    made.write(OWNER, lambda tx: tx.insert("tag", _tag("Mine"), NOW))
    made.close()
    store.stop()
    before = {t: raw(path, f"SELECT count(*) FROM {t}")[0][0] for t in ("fill", "tag", "playbook")}
    raw(path, "PRAGMA user_version = 0")
    again = JournalStore(path, now=NOW + 1)
    assert again.applied == ALL_STEPS and again.seeded == {"owner": []}
    after = {t: raw(path, f"SELECT count(*) FROM {t}")[0][0] for t in ("fill", "tag", "playbook")}
    assert after == before
    assert raw(path, "PRAGMA user_version") == [(migrations.LATEST,)]
    again.close()


def test_a_later_step_runs_on_a_populated_file_forced_back_to_the_step_before(path, made):
    """A synthetic second step (a column added only when missing) on a file with rows: the rows
    stay and get the column's default; run again, the step finds the column and does nothing."""
    made.write(OWNER, lambda tx: tx.insert("fill", _fill("t1"), NOW))
    made.close()
    store.stop()

    def v2(conn):
        migrations.add_column(conn, "fill", "note", "TEXT NOT NULL DEFAULT ''")

    steps = (*migrations.STEPS, (migrations.LATEST + 1, v2))
    made_engine = store.engine(path)
    try:
        assert migrations.migrate(made_engine, steps) == [migrations.LATEST + 1]
        with made_engine.begin() as conn:
            conn.exec_driver_sql(f"PRAGMA user_version = {migrations.LATEST}")
        assert migrations.migrate(made_engine, steps) == [migrations.LATEST + 1]
    finally:
        made_engine.dispose()
    assert raw(path, "SELECT trade_id, note FROM fill") == [("t1", "")]


def test_a_seed_never_touches_what_the_owner_changed(path, made):
    fomo = made.read(OWNER, lambda tx: tx.rows("tag", name="FOMO"))[0]
    made.write(
        OWNER, lambda tx: tx.change("tag", fomo["id"], 1, {"name": "Fear of missing out"}, NOW)
    )
    last = made.read(OWNER, lambda tx: tx.rows("tag"))[-1]
    made.write(OWNER, lambda tx: tx.remove("tag", last["id"]))
    made.close()
    again = JournalStore(path, now=NOW + 1)
    names = [t["name"] for t in again.read(OWNER, lambda tx: tx.rows("tag"))]
    assert "Fear of missing out" in names and "FOMO" not in names
    assert last["name"] not in names and again.seeded == {"owner": []}
    again.close()


def test_a_file_newer_than_the_code_is_refused_and_left_alone(path, made):
    made.close()
    store.stop()
    raw(path, f"PRAGMA user_version = {migrations.LATEST + 1}")
    before = raw(path, "SELECT count(*) FROM tag")
    with pytest.raises(migrations.SchemaTooNew) as caught:
        JournalStore(path, now=NOW)
    assert str(caught.value) == (
        "Your trade journal was written by a newer release. Install that release again; "
        "nothing was changed."
    )
    assert caught.value.found == migrations.LATEST + 1
    assert raw(path, "PRAGMA user_version") == [(migrations.LATEST + 1,)]
    assert raw(path, "SELECT count(*) FROM tag") == before


def _hold_first(prefix: str, names: set, barrier: threading.Barrier):
    """A cursor hook that holds each named thread at its first statement starting ``prefix``
    for up to half a second, or until all of them have reached theirs: the one holding the write
    lock waits there while the other asks for the lock, which is the race being tested."""
    seen: set = set()

    def hook(conn, cursor, statement, parameters, context, executemany):
        name = threading.current_thread().name
        if name in names and name not in seen and statement.lstrip().startswith(prefix):
            seen.add(name)
            with contextlib.suppress(threading.BrokenBarrierError):
                barrier.wait(timeout=0.5)

    return hook


def _race(*calls):
    results: list = [None] * len(calls)

    def run(n, call):
        try:
            results[n] = call()
        except BaseException as exc:  # noqa: BLE001 - the test reads it
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


def _open(path):
    opened = JournalStore(path, now=NOW)
    try:
        return opened.applied, opened.counts(OWNER)["tag"]
    finally:
        opened.close()


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
    assert sorted(n for r in results for n in r[0]) == ALL_STEPS
    assert raw(path, "PRAGMA user_version") == [(migrations.LATEST,)]
    assert raw(path, "SELECT count(*) FROM tag") == [(len(migrations.MISTAKE_TAGS),)]
    assert raw(path, "SELECT count(*) FROM playbook") == [(1,)]


def test_two_writers_held_at_their_first_write_queue_instead_of_failing(made, monkeypatch):
    monkeypatch.setattr(database, "LOCK_RETRY_BUDGET_S", 3.0)
    hook = _hold_first("INSERT", {"race-0", "race-1"}, threading.Barrier(2))
    event.listen(made.engine, "before_cursor_execute", hook)
    start = time.monotonic()

    def add(name):
        return lambda: made._write_here(OWNER, lambda tx: tx.insert("tag", _tag(name), NOW)["id"])

    try:
        results = _race(add("a"), add("b"))
    finally:
        event.remove(made.engine, "before_cursor_execute", hook)
    assert all(isinstance(r, int) for r in results), results
    assert time.monotonic() - start < 2.5
    assert made.read(OWNER, lambda tx: tx.count("tag")) == len(migrations.MISTAKE_TAGS) + 2


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
        tx.insert("tag", _tag("half"), NOW)
        raise RuntimeError("mid-write")

    with pytest.raises(RuntimeError):
        made.write(OWNER, broken)
    assert made.read(OWNER, lambda tx: tx.rows("tag", name="half")) == []


def test_a_record_that_cannot_be_opened_says_so_in_words(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(StoreError) as caught:
        JournalStore(blocker / DB_FILENAME, now=NOW)
    assert str(caught.value) == store.WORDS["opened"]
    assert caught.value.cause and "Error" not in str(caught.value)


def test_the_default_path_follows_the_state_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("SUDARSHAN_JOURNAL_STATE_DIR", raising=False)
    monkeypatch.setenv("STATE_DIRECTORY", f"{tmp_path}:/elsewhere")
    assert store.default_path() == tmp_path / DB_FILENAME
    monkeypatch.setenv("SUDARSHAN_JOURNAL_STATE_DIR", str(tmp_path / "own"))
    assert store.default_path() == tmp_path / "own" / DB_FILENAME
    monkeypatch.delenv("SUDARSHAN_JOURNAL_STATE_DIR")
    monkeypatch.delenv("STATE_DIRECTORY")
    assert store.default_path() == Path("/var/lib/sudarshan-journal") / DB_FILENAME


# ---- migrate --status ------------------------------------------------------------------------
def test_status_of_a_missing_file_makes_nothing(tmp_path, capsys):
    target = tmp_path / "none" / DB_FILENAME
    assert migrate.main(["--status", "--path", str(target)]) == 0
    assert not target.parent.exists()
    out = capsys.readouterr().out
    assert f"version: file none, code {migrations.LATEST}" in out
    assert f"pending: {ALL_STEPS}" in out


def test_status_changes_nothing_in_the_directory(path, made, capsys):
    made.close()
    store.stop()
    before = snapshot(path.parent)
    assert migrate.main(["--status", "--path", str(path)]) == 0
    assert snapshot(path.parent) == before
    assert "pending: none" in capsys.readouterr().out


def test_status_of_an_old_file_lists_the_pending_step_and_leaves_it(path, made, capsys):
    made.close()
    store.stop()
    raw(path, "PRAGMA user_version = 0")
    assert migrate.main(["--status", "--path", str(path)]) == 0
    assert f"pending: {ALL_STEPS}" in capsys.readouterr().out
    assert raw(path, "PRAGMA user_version") == [(0,)]


def test_step_5_adds_the_recounts_to_a_file_that_holds_trades_and_keeps_them(tmp_path, capsys):
    """A populated file at step 4 (what a v0.11.0 install without this fix would hold): the
    status names step 5 and changes nothing; opening applies it once; what was kept stays."""
    import sqlite3

    where = tmp_path / "old" / store.DB_FILENAME
    where.parent.mkdir()
    engine = store.engine(where)
    try:
        migrations.migrate(engine, migrations.STEPS[:4])
    finally:
        engine.dispose()
    with sqlite3.connect(where) as conn:
        conn.execute(
            "INSERT INTO key_opening (principal, mode, exchange, symbol, product_class, qty, "
            "day, source, read_ms, created_at, updated_at) "
            "VALUES ('owner', 'live', 'NSE', 'ACME', 'carry', 60, 20261109, 'holdings', 1, 1, 1)"
        )
    assert migrate.main(["--status", "--path", str(where)]) == 0
    assert "pending: [5]" in capsys.readouterr().out
    assert raw(where, "PRAGMA user_version") == [(4,)]
    assert not raw(where, "SELECT name FROM sqlite_master WHERE name = 'key_recount'")
    opened = JournalStore(where, now=NOW)
    try:
        assert opened.applied == [5]
        key = ("NSE", "ACME", "carry")
        assert opened.read(OWNER, lambda tx: tx.openings_of("live"))[key]["qty"] == 60
        assert opened.read(OWNER, lambda tx: tx.recounts_of("live")) == {}
        assert opened.write(OWNER, lambda tx: tx.recount_put(
            "live", key, 20261120, 0, "holdings", NOW, NOW)) is True  # fmt: skip
    finally:
        opened.close()
    again = JournalStore(where, now=NOW)  # a second opening runs nothing and loses nothing
    try:
        assert again.applied == []
        assert again.read(OWNER, lambda tx: tx.recounts_of("live")) == {
            ("NSE", "ACME", "carry"): [(20261120, 0)]}  # fmt: skip
    finally:
        again.close()
        store.stop()


def test_status_of_a_newer_file_says_so_in_the_owners_words(path, made, capsys):
    made.close()
    store.stop()
    raw(path, f"PRAGMA user_version = {migrations.LATEST + 3}")
    assert migrate.main(["--status", "--path", str(path)]) == 2
    assert migrations.SchemaTooNew.WORDS in capsys.readouterr().out


def test_the_read_only_engine_cannot_write(path, made):
    made.close()
    store.stop()
    engine = store.open_read_only(path)
    try:
        with pytest.raises(Exception), engine.begin() as conn:  # noqa: B017 - any refusal
            conn.exec_driver_sql("INSERT INTO job_run VALUES ('x', 1, 'ok', NULL, 1)")
    finally:
        engine.dispose()
    assert raw(path, "SELECT count(*) FROM job_run") == [(0,)]


def test_applying_migrates_and_refuses_while_the_service_holds_its_port(path, capsys):
    with socket.socket() as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        port = held.getsockname()[1]
        assert migrate.main(["--path", str(path), "--port", str(port)]) == 2
        assert not path.exists()
    assert "stop it first" in capsys.readouterr().out
    assert migrate.main(["--path", str(path), "--port", "1"]) == 0
    assert raw(path, "PRAGMA user_version") == [(migrations.LATEST,)]


# ---- principal -------------------------------------------------------------------------------
#: Upkeep, not readers: they touch no one's rows.
UPKEEP = {"close"}


def test_every_public_method_takes_the_scope_first():
    for name, method in inspect.getmembers(JournalStore, inspect.isfunction):
        if name.startswith("_") or name in UPKEEP:
            continue
        assert list(inspect.signature(method).parameters)[:2] == ["self", "scope"], name


def unfiltered(source: str) -> tuple[list[str], int]:
    """The methods in ``source`` that query an owner table without the principal filter, and
    how many methods were checked."""
    bad, checked = [], 0
    for node in ast.walk(ast.parse(source)):
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
            if "principal" in text or "_mine(" in text:
                bad.append(node.name)  # the job log belongs to no one
            continue
        checked += 1
        if "_mine(" not in text and "self.who" not in text:
            bad.append(node.name)
    return bad, checked


def test_every_query_of_a_transaction_filters_by_principal():
    """Each Tx method that reads or writes a table does it through ``_mine`` (or names the
    principal itself when it inserts); the job log alone belongs to no one. A new method that
    forgets fails here (mutation target 25)."""
    bad, checked = unfiltered(textwrap.dedent(inspect.getsource(store.Tx)))
    assert bad == [] and checked >= 8


def test_the_principal_check_is_real():
    """Mutation target 25: a Tx method that forgets the filter is caught."""
    leaky = (
        "class Leaky(Tx):\n"
        "    def everyone(self):\n"
        "        t = self._t('fill')\n"
        "        return self.conn.execute(select(t)).all()\n"
    )
    assert unfiltered(leaky) == (["everyone"], 1)


def test_the_store_refuses_anyone_it_was_not_opened_for(made):
    with pytest.raises(UnknownPrincipal):
        made.counts(VIEWER)
    with pytest.raises(UnknownPrincipal):
        made.write(VIEWER, lambda tx: tx.insert("tag", _tag("x"), NOW))


def test_one_principal_never_reads_or_changes_anothers_rows(path):
    both = JournalStore(path, principals=("owner", "viewer"), now=NOW)
    mine = both.write(OWNER, lambda tx: tx.insert("tag", _tag("Mine"), NOW))
    theirs = both.write(VIEWER, lambda tx: tx.insert("tag", _tag("Theirs"), NOW))
    assert both.seeded["viewer"] == ["tags", "checklist", "settings"]  # each has their own
    names = lambda scope: [r["name"] for r in both.read(scope, lambda tx: tx.rows("tag"))]  # noqa: E731
    assert "Mine" in names(OWNER) and "Theirs" not in names(OWNER)
    assert "Theirs" in names(VIEWER) and "Mine" not in names(VIEWER)
    assert both.read(OWNER, lambda tx: tx.get("tag", theirs["id"])) is None
    with pytest.raises(NotFound):
        both.write(OWNER, lambda tx: tx.change("tag", theirs["id"], 1, {"name": "taken"}, NOW))
    assert not both.write(OWNER, lambda tx: tx.remove("tag", theirs["id"]))
    assert not both.write(OWNER, lambda tx: tx.touch("tag", theirs["id"], {"name": "x"}, NOW))
    assert both.read(VIEWER, lambda tx: tx.get("tag", theirs["id"]))["name"] == "Theirs"
    assert both.read(OWNER, lambda tx: tx.get("tag", mine["id"]))["name"] == "Mine"
    both.write(VIEWER, lambda tx: tx.settings_save({"eod_on": False}, 1, NOW))
    assert both.read(OWNER, lambda tx: tx.settings())["eod_on"] is True
    both.close()


def test_every_owner_table_has_a_principal_and_stamps():
    sql = " ".join(migrations.V1)
    tables = re.findall(r"CREATE TABLE IF NOT EXISTS (\w+) \((.*?)\n    \)", sql, re.S)
    assert len(tables) == 14
    for table, body in tables:
        if table in migrations.SHARED_TABLES:
            assert "principal" not in body, table
            continue
        assert "principal TEXT NOT NULL" in body, table
        assert "created_at INTEGER NOT NULL" in body and "updated_at" in body, table
    versioned = {t for t, body in tables if "version INTEGER NOT NULL DEFAULT 1" in body}
    assert versioned == store.VERSIONED


def test_no_broker_latency_and_prices_are_price_u():
    """Y29: latency.db keeps no broker answer, so the column is not made. Y31: every price is
    price_u (rupees x 10^4); a column named for a price in paise would lose currency ticks."""
    sql = " ".join(migrations.V1)
    assert "broker_latency" not in sql
    assert not re.search(r"\b\w*(price|avg)\w*_paise\b", sql)
    assert "price_u INTEGER" in sql and "entry_avg_u" in sql


# ---- versions and retried creates ----------------------------------------------------------
def test_a_change_from_a_stale_version_writes_nothing(made):
    row = made.write(OWNER, lambda tx: tx.insert("tag", _tag("T"), NOW))
    made.write(OWNER, lambda tx: tx.change("tag", row["id"], 1, {"name": "T2"}, NOW))
    with pytest.raises(VersionConflict) as caught:
        made.write(OWNER, lambda tx: tx.change("tag", row["id"], 1, {"name": "T3"}, NOW))
    assert str(caught.value) == store.WORDS["version_conflict"]
    after = made.read(OWNER, lambda tx: tx.get("tag", row["id"]))
    assert after["name"] == "T2" and after["version"] == 2


def test_the_service_touch_moves_no_version(made):
    row = made.write(OWNER, lambda tx: tx.insert("tag", _tag("T"), NOW))
    assert made.write(OWNER, lambda tx: tx.touch("tag", row["id"], {"colour": "info"}, NOW))
    after = made.read(OWNER, lambda tx: tx.get("tag", row["id"]))
    assert after["colour"] == "info" and after["version"] == 1


def test_a_retried_create_is_found_by_its_request_id(made):
    row = made.write(OWNER, lambda tx: tx.insert("tag", _tag("T"), NOW, request_id="r-1"))
    assert made.read(OWNER, lambda tx: tx.by_request("tag", "r-1")) == row
    assert "request_id" not in row and "principal" not in row
    with pytest.raises(StoreError):  # the unique index is the net under the check
        made.write(OWNER, lambda tx: tx.insert("tag", _tag("T9"), NOW, request_id="r-1"))


def test_a_trade_id_seen_twice_is_one_fill(made):
    """Mutation target 22: the unique index refuses a second row for the same trade."""
    made.write(OWNER, lambda tx: tx.insert("fill", _fill("t1"), NOW))
    with pytest.raises(StoreError):
        made.write(OWNER, lambda tx: tx.insert("fill", _fill("t1", source="close_reconcile"), NOW))
    made.write(OWNER, lambda tx: tx.insert("fill", _fill("t1", mode="sandbox"), NOW))
    assert made.read(OWNER, lambda tx: tx.count("fill")) == 2


def test_a_tag_name_is_unique_whatever_its_case(made):
    made.write(OWNER, lambda tx: tx.insert("tag", _tag("Mine"), NOW))
    with pytest.raises(StoreError):
        made.write(OWNER, lambda tx: tx.insert("tag", _tag("MINE"), NOW))
    made.write(OWNER, lambda tx: tx.insert("tag", _tag("Mine", kind="label"), NOW))


def test_a_reports_file_never_leaves_in_a_row(made):
    def put(tx):
        return tx.insert(
            "report",
            {"kind": "eod", "period_key": "20261109", "built_ms": NOW, "text": "x",
             "file_bytes": b"%PDF-1.4"},
            NOW,
        )  # fmt: skip

    row = made.write(OWNER, put)
    assert "file_bytes" not in row and row["rows"] == {} and row["delivery"] == []


# ---- the settings document -----------------------------------------------------------------
def test_settings_save_from_their_version_only(made):
    first = made.read(OWNER, lambda tx: tx.settings())
    saved = made.write(
        OWNER, lambda tx: tx.settings_save({"capital_base_paise": 50_000_000}, 1, NOW)
    )
    assert saved["capital_base_paise"] == 50_000_000 and saved["version"] == 2
    assert saved["brief_on"] == first["brief_on"]
    with pytest.raises(VersionConflict):
        made.write(OWNER, lambda tx: tx.settings_save({"eod_on": False}, 1, NOW))
    assert made.read(OWNER, lambda tx: tx.settings())["eod_on"] is True


# ---- the job log -----------------------------------------------------------------------------
def test_the_job_log_keeps_one_row_per_job_and_day(made):
    made.write(OWNER, lambda tx: tx.job_put("eod", 20261109, "failed", "not read", NOW))
    made.write(OWNER, lambda tx: tx.job_put("eod", 20261109, "ok", None, NOW + 1))
    made.write(OWNER, lambda tx: tx.job_put("eod", 20251001, "ok", None, NOW))
    row = made.read(OWNER, lambda tx: tx.job_get("eod", 20261109))
    assert (row["status"], row["at"]) == ("ok", NOW + 1)
    assert made.write(OWNER, lambda tx: tx.job_forget(20260101)) == 1
    assert [r["day"] for r in made.read(OWNER, lambda tx: tx.job_runs())] == [20261109]


# ---- descriptors -----------------------------------------------------------------------------
def test_opening_using_and_closing_leaks_no_descriptors(path):
    JournalStore(path, now=NOW).close()  # warm: the thread, imports, the file itself
    watch = FdWatch()
    for _ in range(15):
        opened = JournalStore(path, now=NOW)
        opened.write(OWNER, lambda tx: tx.rows("tag"))
        opened.read(OWNER, lambda tx: tx.count("fill"))
        opened.close()
        migrate.file_version(path)
    watch.assert_clean()
    store.stop()
    assert not [t for t in threading.enumerate() if t.name.startswith(store.THREAD_PREFIX)]
