"""screener.db (SV1): migrations, the store's transactions, versions, the outbox bound, retention,
the read-only opener and the principal structure every owner-data method must keep."""

from __future__ import annotations

import ast
import inspect
import os
import re
import stat
import threading
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError

from sudarshan.screener import contract, migrate, migrations, store
from sudarshan.screener.store import (
    OWNER,
    NotFound,
    ScreenerStore,
    StoreError,
    Tx,
    UnknownPrincipal,
    VersionConflict,
)

NOW = 1_761_600_000_000  # 2025-10-28 in epoch ms, a Tuesday
DAY = store.DAY_MS
SPEC = {"schema": 1, "name": "Breakout", "universe": {"kind": "fno_stocks"}, "where": []}


@pytest.fixture
def opened(tmp_path):
    made = ScreenerStore(tmp_path / "screener.db")
    yield made
    made.close()
    store.stop()


def _screen(tx: Tx, name="Breakout", now=NOW, **extra):
    return tx.screen_insert(name, SPEC, "active", now, **extra)


# ---- migrations ----------------------------------------------------------------------------
def test_a_fresh_file_is_at_the_latest_version_with_every_table_and_private_modes(tmp_path):
    path = tmp_path / "state" / "screener.db"
    made = ScreenerStore(path)
    try:
        assert made.applied == [1]
        with made.engine.connect() as conn:
            assert migrations.version(conn) == migrations.LATEST
            names = set(store.tables_of(conn))
        expected = {
            "screen", "screen_state", "run", "run_change", "run_value", "membership",
            "list_item", "alert_state", "outbox", "universe", "universe_member", "bar_today",
            "hist_coverage", "oi_daily", "iv_daily", "pause", "meta", "settings",
        }  # fmt: skip
        assert expected <= names
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
    finally:
        made.close()
        store.stop()


def test_opening_twice_runs_no_step_the_second_time(tmp_path):
    path = tmp_path / "screener.db"
    first = ScreenerStore(path)
    first.close()
    second = ScreenerStore(path)
    try:
        assert second.applied == []
    finally:
        second.close()
        store.stop()


def test_a_populated_file_forced_back_to_version_zero_migrates_and_keeps_its_rows(tmp_path):
    path = tmp_path / "screener.db"
    first = ScreenerStore(path)
    first.write(OWNER, lambda tx: _screen(tx))
    with first.writer.begin() as conn:
        conn.exec_driver_sql("PRAGMA user_version = 0")
    first.close()
    again = ScreenerStore(path)
    try:
        assert again.applied == [1]
        assert [row["name"] for row in again.read(OWNER, Tx.screens)] == ["Breakout"]
    finally:
        again.close()
        store.stop()


def test_a_file_from_a_newer_release_is_refused_in_the_owners_words_and_left_alone(tmp_path):
    path = tmp_path / "screener.db"
    first = ScreenerStore(path)
    with first.writer.begin() as conn:
        conn.exec_driver_sql(f"PRAGMA user_version = {migrations.LATEST + 7}")
    first.close()
    with pytest.raises(migrations.SchemaTooNew) as caught:
        ScreenerStore(path)
    assert "newer SUDARSHAN release" in str(caught.value)
    assert not re.search(r"[A-Z][a-z]+Error|sqlite|version \d", str(caught.value))
    assert caught.value.found == migrations.LATEST + 7
    report = migrate.status(path)
    assert report["newer"] and report["pending"] == []
    store.stop()


def test_every_v1_statement_is_idempotent_ddl():
    for statement in migrations.V1:
        head = " ".join(statement.split()[:6])
        assert "IF NOT EXISTS" in head, head
    assert migrations.STEPS[-1][0] == migrations.LATEST == 1


def test_migrate_status_is_read_only_and_reports_pending_steps(tmp_path, capsys):
    path = tmp_path / "screener.db"
    assert migrate.main(["--status", "--path", str(path)]) == 1
    assert not path.exists(), "--status made a file"
    out = capsys.readouterr().out
    assert "no file" in out and "pending steps: 1" in out
    assert migrate.main(["--path", str(path), "--port", "1"]) == 0
    assert migrate.main(["--status", "--path", str(path)]) == 0
    assert "up to date" in capsys.readouterr().out
    store.stop()


def test_open_read_only_never_writes_and_refuses_a_missing_file(tmp_path, opened):
    opened.write(OWNER, lambda tx: _screen(tx))
    ro = store.open_read_only(opened.path)
    try:
        with ro.connect() as conn:
            assert conn.exec_driver_sql("SELECT count(*) FROM screen").scalar() == 1
            with pytest.raises(OperationalError):
                conn.exec_driver_sql(
                    "INSERT INTO meta (name, value, updated_at) VALUES ('a', 'b', 1)"
                )
    finally:
        ro.dispose()
    with pytest.raises(StoreError):
        store.open_read_only(tmp_path / "none.db")


# ---- the principal structure ---------------------------------------------------------------
def _calls(node: ast.AST, name: str) -> bool:
    return (
        any(isinstance(sub, ast.Attribute) and sub.attr == name for sub in ast.walk(node))
        or any(isinstance(sub, ast.keyword) and sub.arg == "principal" for sub in ast.walk(node))
        or any(
            isinstance(sub, ast.Dict)
            and any(isinstance(k, ast.Constant) and k.value == "principal" for k in sub.keys)
            for sub in ast.walk(node)
        )
    )


def test_every_tx_method_on_an_owner_table_filters_or_stamps_the_principal():
    tree = ast.parse(inspect.getsource(Tx))
    owner = set(migrations.OWNER_TABLES)
    checked = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef) or node.name.startswith("_"):
            continue
        tables = {
            sub.args[0].value
            for sub in ast.walk(node)
            if isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr == "_t"
            and sub.args
            and isinstance(sub.args[0], ast.Constant)
            and isinstance(sub.args[0].value, str)
        }
        if tables & owner:
            checked += 1
            assert _calls(node, "_mine"), f"Tx.{node.name} touches {tables & owner} unfiltered"
    assert checked >= 25


def test_an_unknown_principal_is_a_programming_error_and_rows_are_kept_apart(tmp_path):
    made = ScreenerStore(tmp_path / "screener.db", principals=("owner", "viewer"))
    try:
        made.write("owner", lambda tx: _screen(tx, "Mine"))
        made.write("viewer", lambda tx: _screen(tx, "Theirs"))
        assert [r["name"] for r in made.read("owner", Tx.screens)] == ["Mine"]
        assert [r["name"] for r in made.read("viewer", Tx.screens)] == ["Theirs"]
        mine = made.read("owner", Tx.screens)[0]
        assert made.read("viewer", lambda tx: tx.screen(mine["id"])) is None
        assert "principal" not in mine and "request_id" not in mine
        with pytest.raises(UnknownPrincipal):
            made.read("nobody", Tx.screens)
    finally:
        made.close()
        store.stop()


# ---- screens and versions ------------------------------------------------------------------
def test_a_screen_is_created_with_its_state_and_a_retried_create_returns_the_same_row(opened):
    made = opened.write(OWNER, lambda tx: _screen(tx, request_id="r1"))
    assert made["version"] == 1 and made["status"] == "active" and made["spec"] == SPEC
    assert opened.read(OWNER, lambda tx: tx.state(made["id"]))["alerts_paused"] is False
    again = opened.read(OWNER, lambda tx: tx.screen_by_request("r1"))
    assert again == made
    with pytest.raises(StoreError):  # the unique index: the second attempt never makes a twin
        opened.write(OWNER, lambda tx: _screen(tx, request_id="r1"))
    assert opened.read(OWNER, lambda tx: tx.screen_count()) == 1


def test_an_edit_moves_the_version_and_a_stale_edit_writes_nothing(opened):
    made = opened.write(OWNER, lambda tx: _screen(tx))
    changed = opened.write(
        OWNER, lambda tx: tx.screen_change(made["id"], 1, {"name": "Breakout 2"}, NOW + 1)
    )
    assert changed["version"] == 2 and changed["name"] == "Breakout 2"
    with pytest.raises(VersionConflict) as caught:
        opened.write(OWNER, lambda tx: tx.screen_change(made["id"], 1, {"name": "x"}, NOW + 2))
    assert str(caught.value) == store.WORDS["version_conflict"]
    assert opened.read(OWNER, lambda tx: tx.screen(made["id"]))["name"] == "Breakout 2"
    with pytest.raises(NotFound):
        opened.write(OWNER, lambda tx: tx.screen_change(999, 1, {"name": "x"}, NOW))
    with pytest.raises(ValueError):
        opened.write(OWNER, lambda tx: tx.screen_change(made["id"], 2, {"status": "armed"}, NOW))


def test_removing_a_screen_takes_its_state_and_marks_and_keeps_its_runs_and_membership(opened):
    made = opened.write(OWNER, lambda tx: _screen(tx))
    sid = made["id"]

    def fill(tx: Tx):
        run = tx.run_insert(_run(sid, 1, "k1"))
        tx.membership_put(sid, 7, _row("NSE_EQ|INE1", "SBIN"), NOW)
        tx.mark_told(sid, 20251028, {"NSE_EQ|INE1": (1, 3)})
        return run

    run = opened.write(OWNER, fill)
    assert opened.write(OWNER, lambda tx: tx.screen_remove(sid)) is True
    assert opened.read(OWNER, lambda tx: tx.state(sid)) is None
    kept = opened.read(OWNER, lambda tx: tx.memberships(screen_id=sid))
    assert [r["key"] for r in kept] == ["NSE_EQ|INE1"], "membership is kept (design 4.2)"
    assert opened.read(OWNER, lambda tx: tx.told(sid, 20251028)) == {}
    assert opened.read(OWNER, lambda tx: tx.run(run["id"]))["screen_id"] == sid
    assert opened.write(OWNER, lambda tx: tx.screen_remove(sid)) is False


# ---- runs ----------------------------------------------------------------------------------
def _run(screen_id: int, version: int, key: str, at: int = NOW, status: str = "done") -> dict:
    return {
        "screen_id": screen_id,
        "screen_version": version,
        "run_key": f"{screen_id}:v{version}:20251028:15:{key}",
        "session": 20251028,
        "candle_start": at - 900_000,
        "candle_end": at,
        "ran_at": at + 5_000,
        "matched": 3,
        "entered": 1,
        "exited": 0,
        "missing": 0,
        "not_checked": 2,
        "marks": {"warming": 2},
        "status": status,
        "engine_version": 1,
    }


def test_a_run_applies_only_to_the_screen_version_it_judged(opened):
    made = opened.write(OWNER, lambda tx: _screen(tx))
    sid = made["id"]
    first = opened.write(OWNER, lambda tx: tx.run_insert(_run(sid, 1, "a")))
    assert first["marks"] == {"warming": 2} and first["status"] == "done"
    assert opened.write(OWNER, lambda tx: tx.run_insert(_run(sid, 1, "a"))) == first
    opened.write(OWNER, lambda tx: tx.screen_change(sid, 1, {"name": "v2"}, NOW))
    with pytest.raises(VersionConflict) as caught:
        opened.write(OWNER, lambda tx: tx.run_insert(_run(sid, 1, "b")))
    assert str(caught.value) == store.WORDS["stale_run"]
    stale = opened.write(OWNER, lambda tx: tx.run_insert(_run(sid, 1, "b", status="superseded")))
    assert stale["status"] == "superseded" and stale["screen_version"] == 1
    second = opened.write(OWNER, lambda tx: tx.run_insert(_run(sid, 2, "b", NOW + 900_000)))
    assert opened.read(OWNER, lambda tx: tx.latest_run(sid))["id"] == second["id"]
    opened.write(OWNER, lambda tx: tx.run_mark(second["id"], "superseded"))
    assert opened.read(OWNER, lambda tx: tx.latest_run(sid))["id"] == first["id"]
    with pytest.raises(NotFound):
        opened.write(OWNER, lambda tx: tx.run_insert(_run(999, 1, "c")))


def test_runs_page_newest_first_and_runs_since_oldest_first(opened):
    sid = opened.write(OWNER, lambda tx: _screen(tx))["id"]
    ids = [
        opened.write(OWNER, lambda tx, k=k: tx.run_insert(_run(sid, 1, str(k), NOW + k * 60_000)))[
            "id"
        ]
        for k in range(5)
    ]
    page = opened.read(OWNER, lambda tx: tx.runs_page(sid, None, 2))
    assert [r["id"] for r in page] == ids[4:2:-1]
    rest = opened.read(OWNER, lambda tx: tx.runs_page(sid, page[-1]["id"], 10))
    assert [r["id"] for r in rest] == ids[2::-1]
    since = opened.read(OWNER, lambda tx: tx.runs_since(ids[2], 10))
    assert [r["id"] for r in since] == ids[3:]


def test_a_runs_changes_and_values_are_kept_by_key(opened):
    sid = opened.write(OWNER, lambda tx: _screen(tx))["id"]
    run = opened.write(OWNER, lambda tx: tx.run_insert(_run(sid, 1, "a")))
    changes = [
        {"key": "NSE_EQ|B", "symbol": "B", "exchange": "NSE", "change": "in", "rank": 2},
        {"key": "NSE_EQ|A", "symbol": "A", "exchange": "NSE", "change": "in", "rank": 1},
        {"key": "NSE_EQ|C", "symbol": "C", "exchange": "NSE", "change": "out", "rank": None},
    ]
    values = {"NSE_EQ|A": {"c1": {"l": "10", "r": "9", "forming": False}}}
    opened.write(
        OWNER,
        lambda tx: (tx.run_changes_put(run["id"], changes), tx.run_values_put(run["id"], values)),
    )
    got = opened.read(OWNER, lambda tx: tx.run_changes(run["id"]))
    assert [c["key"] for c in got] == ["NSE_EQ|C", "NSE_EQ|A", "NSE_EQ|B"]  # None ranks first
    assert opened.read(OWNER, lambda tx: tx.run_values(run["id"])) == values


# ---- membership, lists, marks ---------------------------------------------------------------
def _row(key: str, symbol: str, **extra) -> dict:
    return {
        "key": key,
        "symbol": symbol,
        "exchange": "NSE",
        "status": "active",
        "pinned": False,
        "added_by_screener": True,
        "in_list": False,
        "first_seen_at": NOW,
        "first_seen_run": 1,
        "last_seen_at": NOW,
        "last_seen_run": 1,
        "hits": 1,
        "misses": 0,
        "entries": 1,
        "exited_at": None,
        "entry_price": 81240,
        "score": None,
        "screen_version": 1,
        "missing_session": 0,
        **extra,
    }


def test_membership_is_one_row_per_screen_list_and_key_and_owner_moves_are_versioned(opened):
    sid = opened.write(OWNER, lambda tx: _screen(tx))["id"]
    first = opened.write(OWNER, lambda tx: tx.membership_put(sid, 7, _row("NSE_EQ|A", "A"), NOW))
    assert first["version"] == 1 and first["pinned"] is False and first["entry_price"] == 81240
    again = opened.write(
        OWNER,
        lambda tx: tx.membership_put(sid, 7, _row("NSE_EQ|A", "A", hits=2, score=1.5), NOW + 1),
    )
    assert again["id"] == first["id"] and again["version"] == 2 and again["hits"] == 2
    assert again["score"] == "1.5"
    pinned = opened.write(
        OWNER, lambda tx: tx.membership_change(first["id"], 2, {"pinned": True}, NOW + 2)
    )
    assert pinned["pinned"] is True and pinned["version"] == 3
    with pytest.raises(VersionConflict):
        opened.write(OWNER, lambda tx: tx.membership_change(first["id"], 2, {"pinned": False}, NOW))
    with pytest.raises(NotFound):
        opened.write(OWNER, lambda tx: tx.membership_change(999, 1, {"pinned": False}, NOW))
    rows = opened.read(OWNER, lambda tx: tx.memberships(list_id=7))
    assert len(rows) == 1 and opened.read(OWNER, lambda tx: tx.memberships(list_id=8)) == []
    assert opened.write(OWNER, lambda tx: tx.membership_remove(first["id"])) is True


def test_list_items_are_replaced_whole_and_told_marks_are_per_session(opened):
    sid = opened.write(OWNER, lambda tx: _screen(tx))["id"]
    items = [{"key": "NSE_EQ|A", "added_by_screener": True, "openalgo_item_id": 11}]
    assert opened.write(OWNER, lambda tx: tx.list_items_replace(7, items, NOW)) == 1
    got = opened.read(OWNER, lambda tx: tx.list_items(7))
    assert got["NSE_EQ|A"]["added_by_screener"] is True and got["NSE_EQ|A"]["seen_at"] == NOW
    assert opened.write(OWNER, lambda tx: tx.list_items_replace(7, [], NOW)) == 0
    assert opened.read(OWNER, lambda tx: tx.list_items(7)) == {}
    opened.write(
        OWNER, lambda tx: tx.mark_told(sid, 20251028, {"NSE_EQ|A": (1, 5), "NSE_EQ|B": (1, 5)})
    )
    opened.write(OWNER, lambda tx: tx.mark_told(sid, 20251028, {"NSE_EQ|A": (2, 6)}))
    told = opened.read(OWNER, lambda tx: tx.told(sid, 20251028))
    assert told["NSE_EQ|A"] == {
        "screen_id": sid,
        "session": 20251028,
        "key": "NSE_EQ|A",
        "told": 2,
        "last_run": 6,
    }
    assert told["NSE_EQ|B"]["told"] == 1
    assert opened.read(OWNER, lambda tx: tx.told(sid, 20251029)) == {}


# ---- the outbox ----------------------------------------------------------------------------
def test_the_outbox_coalesces_list_syncs_and_drops_only_old_pushes_past_the_bound(opened):
    opened.write(
        OWNER, lambda tx: tx.outbox_add("list_sync", "7", {"add": ["A"]}, NOW, coalesce=True)
    )
    opened.write(
        OWNER, lambda tx: tx.outbox_add("list_sync", "7", {"add": ["B"]}, NOW + 1, coalesce=True)
    )
    due = opened.read(OWNER, lambda tx: tx.outbox_due(NOW + 1))
    assert len(due) == 1 and due[0]["payload"] == {"add": ["B"]}
    with pytest.raises(ValueError):
        opened.write(OWNER, lambda tx: tx.outbox_add("email", "x", {}, NOW))

    def flood(tx: Tx):
        for k in range(store.OUTBOX_MAX_PENDING):
            tx.outbox_add("push", str(k), {"k": k}, NOW + 2 + k)
        tx.outbox_add("digest", "d", {"text": "x"}, NOW + 5_000)

    opened.write(OWNER, flood)
    pending = opened.read(OWNER, lambda tx: tx.outbox_count("pending"))
    assert pending == store.OUTBOX_MAX_PENDING
    kinds = opened.read(OWNER, lambda tx: [r["kind"] for r in tx.outbox_due(NOW + 10_000, 2000)])
    assert kinds.count("list_sync") == 1 and kinds.count("digest") == 1
    assert kinds.count("push") == store.OUTBOX_MAX_PENDING - 2
    first_push = opened.read(
        OWNER, lambda tx: [r for r in tx.outbox_due(NOW + 10_000, 2000) if r["kind"] == "push"][0]
    )
    assert first_push["payload"]["k"] == 2, "the oldest pushes went, not the newest"


def test_an_outbox_row_is_retried_at_its_next_time_then_settled(opened):
    row = opened.write(OWNER, lambda tx: tx.outbox_add("digest", "d", {"text": "x"}, NOW))
    opened.write(
        OWNER, lambda tx: tx.outbox_settle(row["id"], "pending", NOW + 1, next_at=NOW + 60_000)
    )
    assert opened.read(OWNER, lambda tx: tx.outbox_due(NOW + 2)) == []
    later = opened.read(OWNER, lambda tx: tx.outbox_due(NOW + 60_000))
    assert later[0]["attempts"] == 1
    opened.write(OWNER, lambda tx: tx.outbox_settle(row["id"], "done", NOW + 60_001))
    assert opened.read(OWNER, lambda tx: tx.outbox_count("pending")) == 0


# ---- shared stores -------------------------------------------------------------------------
def test_universes_bars_coverage_daily_pauses_meta_and_settings_round_trip(opened):
    members = [
        {"key": "NSE_EQ|A", "symbol": "A", "exchange": "NSE", "segment": "NSE_EQ", "tick_size": 5,
         "lot": 1, "expiry": None, "strike": None, "opt_type": None, "cas": True},
    ]  # fmt: skip
    assert (
        opened.write(OWNER, lambda tx: tx.universe_replace("fno_stocks", "fno", members, NOW)) == 1
    )
    assert opened.read(OWNER, lambda tx: tx.universe_members("fno_stocks"))[0]["cas"] is True
    assert (
        opened.write(OWNER, lambda tx: tx.universe_replace("fno_stocks", "fno", [], NOW + 1)) == 0
    )
    assert opened.read(OWNER, Tx.universes)[0]["built_at"] == NOW + 1

    bar = {"key": "NSE_EQ|A", "start": NOW, "o": 1, "h": 2, "l": 1, "c": 2, "v": 10, "oi": None,
           "atp": None, "src": "ticks", "flags": 0}  # fmt: skip
    opened.write(OWNER, lambda tx: tx.bars_put([bar, {**bar, "c": 3, "src": "rest"}]))
    bars = opened.read(OWNER, lambda tx: tx.bars("NSE_EQ|A"))
    assert len(bars) == 1 and bars[0]["c"] == 3 and bars[0]["src"] == "rest"
    assert opened.write(OWNER, Tx.bars_clear) == 1

    cov = {"key": "NSE_EQ|A", "tf": "1", "first_start": 1, "last_start": 2, "file": "a.parquet",
           "fetched_at": NOW}  # fmt: skip
    opened.write(
        OWNER, lambda tx: (tx.coverage_put(cov), tx.coverage_put({**cov, "last_start": 9}))
    )
    assert opened.read(OWNER, lambda tx: tx.coverage("NSE_EQ|A"))[0]["last_start"] == 9

    opened.write(OWNER, lambda tx: tx.daily_put("oi_daily", [
        {"key": "NSE_FO|1", "session": 20251027, "oi": 100, "close": 5, "volume": 1},
        {"key": "NSE_FO|1", "session": 20251028, "oi": 120, "close": 6, "volume": 2},
    ]))  # fmt: skip
    assert [d["oi"] for d in opened.read(OWNER, lambda tx: tx.daily("oi_daily", "NSE_FO|1"))] == [
        120,
        100,
    ]
    opened.write(OWNER, lambda tx: tx.daily_put("iv_daily", [
        {"underlying": "NIFTY", "session": 20251028, "atm_iv": "12.5", "straddle": 25000,
         "pcr": "0.9", "max_pain": 2500000, "vix": "13.2"},
    ]))  # fmt: skip
    assert opened.read(OWNER, lambda tx: tx.daily("iv_daily", "NIFTY"))[0]["atm_iv"] == "12.5"
    with pytest.raises(ValueError):
        opened.write(OWNER, lambda tx: tx.daily_put("screen", []))

    pid = opened.write(OWNER, lambda tx: tx.pause_open(20251028, NOW, "feed"))
    opened.write(OWNER, lambda tx: tx.pause_close(pid, NOW + 12_000))
    assert opened.read(OWNER, lambda tx: tx.pauses(20251028))[0]["end"] == NOW + 12_000

    opened.write(
        OWNER, lambda tx: (tx.meta_set("seen", "1", NOW), tx.meta_set("seen", "2", NOW + 1))
    )
    assert opened.read(OWNER, lambda tx: tx.meta_get("seen")) == "2"
    assert opened.read(OWNER, lambda tx: tx.meta_get("none")) is None
    opened.write(
        OWNER, lambda tx: (tx.setting_set("grace", "2", NOW), tx.setting_set("grace", "3", NOW))
    )
    assert opened.read(OWNER, lambda tx: tx.setting_get("grace")) == "3"


# ---- retention -----------------------------------------------------------------------------
def test_prune_keeps_runs_ninety_days_at_least_values_seven_and_never_screens_or_members(opened):
    sid = opened.write(OWNER, lambda tx: _screen(tx))["id"]

    def fill(tx: Tx):
        old = tx.run_insert(_run(sid, 1, "old", NOW - 95 * DAY))
        mid = tx.run_insert(_run(sid, 1, "mid", NOW - 10 * DAY))
        new = tx.run_insert(_run(sid, 1, "new", NOW - 1 * DAY))
        for run in (old, mid, new):
            tx.run_changes_put(
                run["id"],
                [{"key": "k", "symbol": "K", "exchange": "NSE", "change": "in", "rank": 1}],
            )
            tx.run_values_put(run["id"], {"k": {"c1": {}}})
        tx.membership_put(
            sid, 7, _row("NSE_EQ|A", "A", last_seen_at=NOW - 200 * DAY), NOW - 200 * DAY
        )
        done = tx.outbox_add("digest", "d", {}, NOW - 3 * DAY)
        tx.outbox_settle(done["id"], "done", NOW - 3 * DAY)
        tx.outbox_add("digest", "e", {}, NOW - 3 * DAY)  # pending: never pruned
        return old["id"], mid["id"], new["id"]

    old, mid, new = opened.write(OWNER, fill)
    counts = opened.write(OWNER, lambda tx: tx.prune(NOW, keep_run_days=30))  # 30 is raised to 90
    assert counts == {"run_change": 1, "run_value": 2, "run": 1, "outbox": 1}
    assert opened.read(OWNER, lambda tx: tx.run(old)) is None
    assert opened.read(OWNER, lambda tx: tx.run(mid))["matched"] == 3
    assert opened.read(OWNER, lambda tx: tx.run_values(mid)) == {}
    assert opened.read(OWNER, lambda tx: tx.run_values(new)) == {"k": {"c1": {}}}
    assert opened.read(OWNER, lambda tx: tx.run_changes(mid)) != []
    assert len(opened.read(OWNER, lambda tx: tx.memberships(screen_id=sid))) == 1
    assert opened.read(OWNER, lambda tx: tx.screen_count()) == 1
    assert opened.read(OWNER, lambda tx: tx.outbox_count("pending")) == 1


# ---- the writer thread and failures ---------------------------------------------------------
def test_every_read_and_write_runs_on_the_one_store_thread(opened):
    names = set()
    opened.write(OWNER, lambda tx: names.add(threading.current_thread().name))
    opened.read(OWNER, lambda tx: names.add(threading.current_thread().name))
    assert len(names) == 1 and next(iter(names)).startswith(store.THREAD_PREFIX)
    # a nested call from the store thread runs in place (no deadlock on a one-thread pool)
    opened.write(OWNER, lambda tx: opened.read(OWNER, lambda inner: inner.screen_count()))


def test_a_write_that_fails_rolls_back_whole_and_speaks_for_the_owner(opened):
    def half(tx: Tx):
        _screen(tx, "kept?")
        raise RuntimeError("stop here")

    with pytest.raises(RuntimeError):
        opened.write(OWNER, half)
    assert opened.read(OWNER, lambda tx: tx.screen_count()) == 0
    sid = opened.write(OWNER, lambda tx: _screen(tx))["id"]
    with pytest.raises(StoreError) as caught:  # a second row for one (run_id, key): the PK
        opened.write(OWNER, lambda tx: (
            tx.run_insert(_run(sid, 1, "a")),
            tx.run_changes_put(1, [{"key": "k", "symbol": "K", "exchange": "NSE", "change": "in", "rank": 1}] * 2),
        ))  # fmt: skip
    assert str(caught.value) == store.WORDS["written"] and "UNIQUE" in caught.value.cause
    assert opened.read(OWNER, lambda tx: tx.latest_run(sid)) is None


def test_the_contract_names_every_op_once_and_the_port_is_the_screeners(opened):
    assert contract.DEFAULT_HTTP_PORT == 5584
    assert len(set(contract.OP_NAMES)) == len(contract.OPS)
    assert (
        set(contract.LIMITS_PER_MIN) == set(contract.TIMEOUTS_S) == {op.cls for op in contract.OPS}
    )
    for op in contract.OPS:
        assert all(kind in contract.QUERY_TYPES for _, kind in op.query), op.name
        if op.method in ("POST", "PATCH"):
            assert not op.query, op.name
    assert set(contract.ERROR_STATUS) == set(contract.ERROR_CODES)
    for text in contract.WORDS.values():
        assert text.endswith(".") and not re.search(r"\b(HTTP|JSON|API|403|500)\b", text), text


def test_default_path_reads_the_state_directory(monkeypatch):
    monkeypatch.delenv("SUDARSHAN_SCREENER_STATE_DIR", raising=False)
    monkeypatch.setenv("STATE_DIRECTORY", "/var/lib/sudarshan-screener:/var/lib/other")
    assert store.default_path() == Path("/var/lib/sudarshan-screener/screener.db")
    monkeypatch.delenv("STATE_DIRECTORY")
    assert store.default_path() == store.STATE_DIR / store.DB_FILENAME
