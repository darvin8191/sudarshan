"""market.db: migrations by user_version, once-only fires, deliveries, settings, retention."""

from __future__ import annotations

import contextlib
import inspect
import os
import sqlite3
import threading
import time
from datetime import datetime

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

import database
from sudarshan.alerts.market import migrations, spec
from sudarshan.alerts.market.clock import Pause
from sudarshan.alerts.market.engine import Fire, State
from sudarshan.alerts.market.store import (
    DB_FILENAME,
    OFF_REASON,
    WORDS,
    MarketStore,
    NotFound,
    Refused,
    StoreError,
    UnknownPrincipal,
    VersionConflict,
    meta,
    writer,
)
from sudarshan.autologin.schedule import IST

NOW = int(datetime(2026, 10, 21, 10, 0, tzinfo=IST).timestamp() * 1000)
DAY_MS = 86_400_000
SBIN = {
    "instrument_key": "NSE_EQ|INE062A01020",
    "symbol": "SBIN",
    "exchange": "NSE",
    "tick_size": "0.05",
}
FIELDS = spec.check(
    SBIN | {"kind": "price", "params": {"op": "crosses_above", "level": "100"}}, NOW
)
BOTH = {"telegram": True, "app": True}


@pytest.fixture
def path(tmp_path):
    return tmp_path / "state" / DB_FILENAME


@pytest.fixture
def store(path):
    made = MarketStore(path)
    yield made
    made.close()


def fire(alert_id: int, claim: str, at: int = NOW, **more) -> Fire:
    """A fire decided on version 1 unless ``version`` says otherwise."""
    return Fire(alert_id, claim, 20261021, 0, at, at, 10005, 10000, 1, 1, **{"version": 1} | more)


def raw(path, sql: str):
    """One statement on the file itself; sqlite3's ``with`` commits but does not close."""
    with contextlib.closing(sqlite3.connect(path)) as conn, conn:
        return conn.execute(sql).fetchall()


# ---- the file and its schema ---------------------------------------------------------------


def test_a_fresh_file_is_private_and_at_the_latest_version(path, store):
    assert store.applied == [1] and raw(path, "PRAGMA user_version") == [(migrations.LATEST,)]
    assert os.stat(path).st_mode & 0o777 == 0o600
    assert os.stat(path.parent).st_mode & 0o777 == 0o700
    store.create("owner", FIELDS, NOW)
    for side in (path.with_name(DB_FILENAME + "-wal"), path.with_name(DB_FILENAME + "-shm")):
        if side.exists():
            assert os.stat(side).st_mode & 0o777 == 0o600


def test_the_schema_is_what_the_store_reads(path, store):
    for table in meta.sorted_tables:
        on_disk = {row[1] for row in raw(path, f"PRAGMA table_info({table.name})")}
        assert on_disk == {column.name for column in table.columns}, table.name


def test_a_second_start_runs_nothing_and_keeps_everything(path, store):
    row = store.create("owner", FIELDS, NOW)
    store.close()
    again = MarketStore(path)
    assert again.applied == [] and again.alert("owner", row["id"]) == row
    again.close()


def test_a_populated_file_forced_back_to_version_0_migrates_without_loss(path, store):
    row = store.create("owner", FIELDS, NOW)
    store.save_states("owner", {row["id"]: (1, State(20261021, -1, 9995, NOW))}, NOW)
    event_id = store.record_fire("owner", fire(row["id"], "1:v1:once"), State(20261021, 0),
                                 title="t", line="l", value="100.00", level="100.00",
                                 channels=BOTH)  # fmt: skip
    store.close()
    raw(path, "PRAGMA user_version = 0")
    again = MarketStore(path)
    assert again.applied == [1]
    assert again.alert("owner", row["id"]) == row
    assert again.states("owner")[row["id"]] == State(20261021, 0)
    assert [e["id"] for e in again.events("owner")] == [event_id]
    again.close()


def _step2(conn):
    added = migrations.add_column(conn, "alert", "colour", "TEXT NOT NULL DEFAULT ''")
    if added:  # backfill from the data: an alert that is stopped is grey
        conn.exec_driver_sql("UPDATE alert SET colour = 'grey' WHERE status = 'stopped'")
    conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS alert_colour ON alert (colour)")


def test_a_later_step_on_a_populated_old_copy_then_again_forced_back(path, store, monkeypatch):
    active = store.create("owner", FIELDS, NOW)
    stopped = store.create("owner", FIELDS, NOW)
    store.set_status("owner", stopped["id"], "stopped", "Fired once at 10:00", NOW, version=1)
    store.close()
    monkeypatch.setattr(migrations, "STEPS", (*migrations.STEPS, (2, _step2)))
    first = MarketStore(path)
    assert first.applied == [2] and raw(path, "PRAGMA user_version") == [(2,)]
    first.close()
    assert raw(path, "SELECT id, colour FROM alert ORDER BY id") == [
        (active["id"], ""),
        (stopped["id"], "grey"),
    ]
    raw(path, f"UPDATE alert SET colour = 'gold' WHERE id = {active['id']}")  # the owner's own
    raw(path, "PRAGMA user_version = 1")  # forced back, the column already there
    again = MarketStore(path)
    assert again.applied == [2]
    assert raw(path, "SELECT colour FROM alert ORDER BY id") == [("gold",), ("grey",)]
    again.close()
    third = MarketStore(path)
    assert third.applied == []
    third.close()


def test_a_step_that_fails_leaves_the_file_as_it_was(path, store, monkeypatch):
    store.create("owner", FIELDS, NOW)
    store.close()

    def broken(conn):
        conn.exec_driver_sql("CREATE TABLE half_done (x INTEGER)")
        migrations.add_column(conn, "alert", "colour", "TEXT")
        raise RuntimeError("the disk went away")

    monkeypatch.setattr(migrations, "STEPS", (*migrations.STEPS, (2, broken)))
    with pytest.raises(RuntimeError):
        MarketStore(path)
    assert raw(path, "PRAGMA user_version") == [(1,)]
    assert raw(path, "SELECT name FROM sqlite_master WHERE name = 'half_done'") == []
    assert "colour" not in {row[1] for row in raw(path, "PRAGMA table_info(alert)")}


def test_a_step_another_process_finished_first_is_not_run_again(path, store, monkeypatch):
    """The version is read again under the write lock: pending() saw the step missing, but
    another process ran it before this one held the lock. This step fails if it runs twice."""
    store.close()
    runs = []

    def once(conn):
        runs.append(conn)
        conn.exec_driver_sql("CREATE TABLE once_only (x INTEGER)")

    monkeypatch.setattr(migrations, "STEPS", (*migrations.STEPS, (2, once)))
    MarketStore(path).close()
    monkeypatch.setattr(migrations, "pending", lambda engine, steps=None: [2])  # a stale look
    again = MarketStore(path)
    assert again.applied == [] and len(runs) == 1
    again.close()


def _hold_first(statement_start: str, names: set[str], gate: threading.Barrier):
    """A cursor hook: each named thread waits at ``gate`` before its first statement starting
    ``statement_start``, so both have read before either writes (if the store lets them)."""
    held = set()

    def hold(conn, cursor, statement, parameters, context, executemany):
        name = threading.current_thread().name
        if name in names and name not in held and statement.startswith(statement_start):
            held.add(name)
            with contextlib.suppress(threading.BrokenBarrierError):
                gate.wait(timeout=0.5)

    return hold


def _race(*calls) -> list:
    """Run each call on its own thread at once; their results, or the StoreError raised."""
    results: list = [None] * len(calls)

    def run(n, call):
        try:
            results[n] = call()
        except StoreError as exc:
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


def test_two_writers_at_once_queue_instead_of_failing(store, monkeypatch):
    """The 1 s flush and a fire's record write at the same moment from two threads. Each write
    begins IMMEDIATE, so the second waits for the first; with a deferred BEGIN both read first,
    and the loser's write could only fail ("database is locked") after waiting it out."""
    monkeypatch.setattr(database, "LOCK_RETRY_BUDGET_S", 3.0)
    row = store.create("owner", FIELDS, NOW)
    hook = _hold_first("INSERT", {"race-0", "race-1"}, threading.Barrier(2))
    event.listen(store.engine, "before_cursor_execute", hook)
    start = time.monotonic()
    results = _race(
        lambda: store.save_states("owner", {row["id"]: (1, State(20261021, -1, 9995, NOW))}, NOW),
        lambda: store.record_fire("owner", fire(row["id"], "1:v1:20261021:1"), State(20261021, 1),
                                  title="t", line="l", value="v", level="l", channels=BOTH),
    )  # fmt: skip
    event.remove(store.engine, "before_cursor_execute", hook)
    assert not [r for r in results if isinstance(r, StoreError)], results
    assert results[0] == 1 and isinstance(results[1], int)
    assert time.monotonic() - start < 2.5  # one hold of 0.5 s, then a short queue


def test_two_records_of_one_claim_at_once_tell_it_once(store, monkeypatch):
    """The same fire recorded twice at the same moment: one event id, one "already told"
    (None), never a failure, and one row in the log."""
    monkeypatch.setattr(database, "LOCK_RETRY_BUDGET_S", 3.0)
    row = store.create("owner", FIELDS, NOW)
    hook = _hold_first("INSERT INTO alert_event", {"race-0", "race-1"}, threading.Barrier(2))
    event.listen(store.engine, "before_cursor_execute", hook)

    def record():
        return store.record_fire("owner", fire(row["id"], "1:v1:once"), State(20261021, 1),
                                 title="t", line="l", value="v", level="l", channels=BOTH,
                                 status="stopped", status_reason="Fired once")  # fmt: skip

    results = _race(record, record)
    event.remove(store.engine, "before_cursor_execute", hook)
    assert sorted(type(r).__name__ for r in results) == ["NoneType", "int"], results
    assert len(store.events("owner")) == 1


def test_two_processes_opening_a_new_file_at_once_both_start(path, monkeypatch):
    """Both run the migrations at the same moment: the second waits for the first's step and
    then finds it done. (Threads here; a process is the same to SQLite.)"""
    monkeypatch.setattr(database, "LOCK_RETRY_BUDGET_S", 3.0)
    hook = _hold_first("CREATE TABLE", {"race-0", "race-1"}, threading.Barrier(2))
    event.listen(Engine, "before_cursor_execute", hook)
    try:
        results = _race(lambda: MarketStore(path), lambda: MarketStore(path))
    finally:
        event.remove(Engine, "before_cursor_execute", hook)
    assert not [r for r in results if isinstance(r, StoreError)], results
    assert sorted(len(r.applied) for r in results) == [0, 1]
    for opened in results:
        opened.close()
    assert raw(path, "PRAGMA user_version") == [(migrations.LATEST,)]


def test_a_file_newer_than_the_code_is_refused_and_left_alone(path, store):
    store.close()
    raw(path, "PRAGMA user_version = 99")
    with pytest.raises(migrations.SchemaTooNew) as caught:
        MarketStore(path)
    assert str(caught.value) == (
        "The market alert record was written by a newer SUDARSHAN release (version 99; this "
        "one knows up to 1). Market alerts stay off until that release is installed again; "
        "order alerts carry on."
    )
    assert raw(path, "PRAGMA user_version") == [(99,)]


def test_a_record_that_cannot_be_written_says_so_in_words(store):
    """What still works and what to check; the system's own words only in ``cause``."""
    store.engine = create_engine("sqlite:////nonexistent/dir/market.db")
    store.writer = writer(store.engine)
    with pytest.raises(StoreError) as caught:
        store.create("owner", FIELDS, NOW)
    assert (
        str(caught.value)
        == WORDS["written"]
        == (
            "This could not be saved to the market alert record. Alerts already set are still "
            "checked. Try again in a minute; if it keeps failing, check that the server has free "
            "disk space."
        )
    )
    assert caught.value.cause == "unable to open database file"
    with pytest.raises(StoreError) as caught:
        store.alerts("owner")
    assert str(caught.value) == WORDS["read"] and "(" not in str(caught.value)


def test_a_record_that_cannot_be_opened_says_market_alerts_are_off(tmp_path):
    """A failed open stops market alerts: the words must not say they are still checked."""
    (tmp_path / "state").write_text("a file where the directory should be")
    with pytest.raises(StoreError) as caught:
        MarketStore(tmp_path / "state" / DB_FILENAME)
    assert str(caught.value) == (
        "Market alerts are off: their record could not be opened. Order alerts carry on. Check "
        "that the server has free disk space."
    )
    assert "still checked" not in str(caught.value) and caught.value.cause


# ---- definitions and state -----------------------------------------------------------------


def test_create_read_and_list(store):
    row = store.create("owner", FIELDS | {"note": "book half"}, NOW)
    assert row["status"] == "active" and row["version"] == 1 and row["note"] == "book half"
    assert row["params"] == FIELDS["params"] and row["channels"] is None
    assert spec.Alert.of(row).cond.up == 10000  # what the engine runs, in paise
    second = store.create("owner", FIELDS | {"channels": {"telegram": False, "app": True}}, NOW)
    assert [r["id"] for r in store.alerts("owner")] == [row["id"], second["id"]]
    assert second["channels"] == {"telegram": False, "app": True}
    assert store.alerts("owner", "paused") == [] and store.alert("owner", 999) is None


def test_update_needs_the_current_version(store):
    row = store.create("owner", FIELDS, NOW)
    store.save_states("owner", {row["id"]: (1, State(20261021, -1, 9995, NOW))}, NOW)
    edited = store.update("owner", row["id"], 1, FIELDS | {"note": "new"}, NOW + 1)
    assert edited["version"] == 2 and edited["note"] == "new"
    assert store.states("owner")[row["id"]].side == -1  # a note edit keeps the state
    with pytest.raises(VersionConflict) as caught:
        store.update("owner", row["id"], 1, FIELDS, NOW + 2)
    assert (
        str(caught.value)
        == "This alert was changed on another screen. Open it again, then edit it."
    )
    with pytest.raises(VersionConflict) as caught:
        store.pause("owner", row["id"], 1, NOW + 2)
    assert str(caught.value).endswith("Open it again, then try again.")
    with pytest.raises(NotFound):
        store.update("owner", 999, 1, FIELDS, NOW)
    with pytest.raises(NotFound):
        store.resume("owner", 999, 1, NOW)
    level = FIELDS | {"params": FIELDS["params"] | {"level": "102"}}
    store.update("owner", row["id"], 2, level, NOW + 3, reset_state=True)
    assert row["id"] not in store.states("owner")


def test_the_owners_moves_are_accepted_only_from_their_status(store):
    """Pause only from Active, resume only from Paused, restart only from Stopped (review R3);
    the status is read before the version, so a stale screen hears what the alert is now."""
    row = store.create("owner", FIELDS, NOW)
    assert store.pause("owner", row["id"], 1, NOW + 1)["status"] == "paused"
    with pytest.raises(Refused, match=r"^This alert is already paused\.$"):
        store.pause("owner", row["id"], 2, NOW + 2)
    with pytest.raises(Refused, match=r"^This alert is paused\. Use Resume to check it again\.$"):
        store.restart("owner", row["id"], 2, NOW + 2)
    back = store.resume("owner", row["id"], 2, NOW + 3)
    assert (back["status"], back["status_reason"], back["version"]) == ("active", "", 3)
    for move in (store.resume, store.restart):
        with pytest.raises(Refused, match=r"^This alert is already active\.$"):
            move("owner", row["id"], 3, NOW + 4)
    once = "Fired once at 10:42 on 21 Oct"
    assert store.set_status("owner", row["id"], "stopped", once, NOW + 5, version=3)
    with pytest.raises(Refused) as caught:
        store.pause("owner", row["id"], 1, NOW + 6)  # stale too: the status speaks first
    assert str(caught.value) == f"This alert has stopped ({once}). There is nothing to pause."
    with pytest.raises(Refused) as caught:
        store.resume("owner", row["id"], 3, NOW + 6)
    assert str(caught.value) == f"This alert has stopped ({once}). Use Restart to check it again."
    with pytest.raises(VersionConflict):
        store.restart("owner", row["id"], 2, NOW + 7)
    again = store.restart("owner", row["id"], 3, NOW + 7)
    assert (again["status"], again["status_reason"], again["version"]) == ("active", "", 4)


def test_a_restart_past_the_end_date_or_the_expiry_is_refused(store):
    ends = SBIN | {"kind": "price", "params": {"op": "crosses_above", "level": "100"},
                   "ends": "2026-10-21T11:00:00+05:30"}  # fmt: skip
    row = store.create("owner", spec.check(ends, NOW), NOW)
    store.set_status("owner", row["id"], "stopped", "Ended at 11:00 on 21 Oct", NOW, version=1)
    with pytest.raises(Refused) as caught:
        store.restart("owner", row["id"], 1, NOW + 3_600_000)
    assert str(caught.value) == (
        "This alert's end date has passed. Change the end date, then restart it."
    )
    option = {"instrument_key": "NSE_FO|49229", "symbol": "NIFTY25NOV2625900CE",
              "exchange": "NFO", "tick_size": "0.05", "expiry": "2026-11-25", "kind": "price",
              "params": {"op": "crosses_above", "level": "100"}}  # fmt: skip
    row = store.create("owner", spec.check(option, NOW), NOW)
    store.set_status("owner", row["id"], "stopped", "Contract expired on 25 Nov", NOW, version=1)
    with pytest.raises(Refused) as caught:
        store.restart("owner", row["id"], 1, NOW + 40 * DAY_MS)
    assert str(caught.value) == (
        "This alert's contract expired on 25 Nov, so it cannot be checked again. Set a new "
        "alert on a contract that is still trading."
    )
    assert store.restart("owner", row["id"], 1, NOW + DAY_MS)["status"] == "active"


def test_set_status_and_delete(store):
    row = store.create("owner", FIELDS, NOW)
    assert store.set_status("owner", row["id"], "stopped", "Ended on 20 Dec", NOW + 5, version=1)
    after = store.alert("owner", row["id"])
    assert (after["status"], after["status_reason"], after["version"]) == (
        "stopped",
        "Ended on 20 Dec",
        1,  # the version counts the owner's edits only
    )
    store.record_fire("owner", fire(row["id"], "1:v1:once"), State(20261021, 0),
                      title="t", line="l", value="1", level="1", channels=BOTH)  # fmt: skip
    assert store.delete("owner", row["id"]) and not store.delete("owner", row["id"])
    assert store.alert("owner", row["id"]) is None and store.states("owner") == {}
    assert len(store.events("owner")) == 1  # the history outlives the alert


def test_states_are_saved_only_for_alerts_still_there(store):
    row = store.create("owner", FIELDS, NOW)
    full = State(20261021, 1, 10005, NOW, NOW - 5, 3, 7, 2, 1, forming=None, derived=("x",))
    assert store.save_states("owner", {row["id"]: (1, full), 999: (1, State())}, NOW) == 1
    assert store.states("owner") == {row["id"]: State(*full[:9])}  # memory-only fields drop


def test_a_decision_applies_only_to_the_definition_it_was_judged_at(store):
    """A state or a stop judged at version 1 does not reach version 2 (the owner's edit), and
    an automatic stop does not reach an alert the owner paused."""
    row = store.create("owner", FIELDS, NOW)
    level = FIELDS | {"params": FIELDS["params"] | {"level": "110"}}
    store.update("owner", row["id"], 1, level, NOW + 1, reset_state=True)
    old = State(20261021, -1, 9995, NOW)
    assert store.save_states("owner", {row["id"]: (1, old)}, NOW + 2) == 0
    assert not store.set_status("owner", row["id"], "stopped", "Ended", NOW + 3, version=1)
    assert store.states("owner") == {} and store.alert("owner", row["id"])["status"] == "active"
    assert store.save_states("owner", {row["id"]: (2, State(20261021, -1))}, NOW + 4) == 1
    store.pause("owner", row["id"], 2, NOW + 5)
    assert not store.set_status("owner", row["id"], "stopped", "Ended", NOW + 6, version=3)
    assert store.alert("owner", row["id"])["status"] == "paused"
    late = store.record_fire("owner", fire(row["id"], "1:v3:once", version=3), State(20261021, 1),
                             title="t", line="l", value="v", level="l", channels=BOTH,
                             status="stopped", status_reason="Fired once")  # fmt: skip
    assert late is not None  # told: it was decided while the alert was Active
    assert store.alert("owner", row["id"])["status"] == "paused"  # the owner's pause stands
    assert store.states("owner")[row["id"]] == State(20261021, -1)


def test_an_alert_that_stopped_itself_takes_the_owners_next_edit_without_a_conflict(store):
    row = store.create("owner", FIELDS, NOW)
    assert store.set_status("owner", row["id"], "stopped", "Ended on 20 Dec", NOW + 1, version=1)
    note = store.update("owner", row["id"], 1, FIELDS | {"note": "later"}, NOW + 2)
    assert (note["status"], note["status_reason"], note["version"]) == (
        "stopped",
        "Ended on 20 Dec",
        2,
    )
    level = FIELDS | {"params": FIELDS["params"] | {"level": "105"}}
    again = store.update("owner", row["id"], 2, level, NOW + 3, reset_state=True)
    assert (again["status"], again["status_reason"], again["version"]) == ("active", "", 3)


# ---- the log -------------------------------------------------------------------------------


def test_a_fire_is_recorded_once_with_its_state_and_stop(store):
    row = store.create("owner", FIELDS, NOW)
    state = State(20261021, 0, 10005, NOW, NOW, 0, 1, 1)
    channels = {"telegram": True, "app": False}
    first = store.record_fire("owner", fire(row["id"], "1:v1:once", found_later=(NOW - 60_000, NOW)),
                              state, title="SBIN crossed above ₹100.00", line="Alert: ...",
                              value="100.05", level="100.00", channels=channels,
                              status="stopped", status_reason="Fired once at 10:00 on 21 Oct")  # fmt: skip
    again = store.record_fire("owner", fire(row["id"], "1:v1:once"), state, title="t", line="l",
                              value="v", level="l", channels=channels)  # fmt: skip
    assert first is not None and again is None
    [event] = store.events("owner")
    assert event["found_later"] and not event["opening"] and not event["test"]
    assert event["title"] == "SBIN crossed above ₹100.00" and event["value"] == "100.05"
    assert event["deliveries"] == {
        "telegram": {"status": "pending", "reason": "", "attempts": 0, "updated_at": NOW},
        "app": {"status": "off", "reason": OFF_REASON, "attempts": 0, "updated_at": NOW},
    }
    assert store.states("owner")[row["id"]] == state
    assert store.alert("owner", row["id"])["status"] == "stopped"
    with pytest.raises(NotFound):
        store.record_fire("owner", fire(999, "999:v1:once"), state, title="t", line="l",
                          value="v", level="l", channels=channels)  # fmt: skip


def test_deliveries_pages_and_catching_up(store):
    row = store.create("owner", FIELDS, NOW)
    ids = [
        store.record_fire("owner", fire(row["id"], f"1:v1:20261021:{n}", NOW + n), State(),
                          title=f"t{n}", line="l", value="v", level="l", channels=BOTH)
        for n in range(1, 6)
    ]  # fmt: skip
    test_id = store.record_test(
        "owner", "Test", "Test: market alerts reach this chat", NOW + 9, BOTH
    )
    assert store.set_delivery("owner", ids[0], "telegram", "sent", "", NOW + 10, attempts=1)
    assert not store.set_delivery("owner", 12345, "telegram", "sent", "", NOW)
    with pytest.raises(ValueError):
        store.set_delivery("owner", ids[0], "telegram", "delivered", "", NOW)
    settled = store.deliveries("owner", [ids[0], ids[1], 12345])  # as the push reads them back
    assert settled[ids[0]]["telegram"] == {"status": "sent", "reason": "", "attempts": 1,
                                           "updated_at": NOW + 10}  # fmt: skip
    assert settled[ids[1]]["telegram"]["status"] == "pending" and settled[12345] == {}
    page = store.events("owner", limit=2)
    assert [e["id"] for e in page] == [test_id, ids[4]] and page[0]["test"]
    older = store.events("owner", before=page[-1]["id"], limit=2)
    assert [e["id"] for e in older] == [ids[3], ids[2]]
    assert [e["id"] for e in store.events("owner", alert_id=row["id"])] == ids[::-1]
    assert [e["id"] for e in store.events_since("owner", ids[2])] == [ids[3], ids[4], test_id]
    assert store.events("owner", limit=10)[-1]["deliveries"]["telegram"]["status"] == "sent"
    assert [e["id"] for e in store.pending("owner", "telegram")] == [*ids[1:], test_id]


# ---- settings, market facts, upkeep --------------------------------------------------------


def test_prefs_default_on_and_are_kept(store):
    assert store.prefs("owner") == {"telegram": True, "app": True}
    assert store.set_prefs("owner", telegram=False, app=True, now=NOW) == {
        "telegram": False,
        "app": True,
    }
    assert store.prefs("owner") == {"telegram": False, "app": True}


def test_baselines_pauses_and_the_last_moment_seen(store):
    store.put_baseline("owner", "NSE_EQ|X", 20261021, 20, 1_234_567.0, NOW)
    store.put_baseline("owner", "NSE_EQ|X", 20261021, 20, 1_300_000.0, NOW + 1)
    assert store.baseline("owner", "NSE_EQ|X", 20261021) == (20, 1_300_000.0)
    assert store.baseline("owner", "NSE_EQ|X", 20261022) is None
    old = store.open_pause("owner", NOW - DAY_MS, "feed")
    store.close_pause("owner", old, NOW - DAY_MS + 60_000)
    pause = store.open_pause("owner", NOW, "feed")
    assert store.pauses("owner", NOW - 3_600_000) == (Pause(NOW, None, "feed"),)
    store.close_pause("owner", pause, NOW + 39_000)
    assert store.pauses("owner", NOW - 3_600_000) == (Pause(NOW, NOW + 39_000, "feed"),)
    assert store.seen("owner") is None
    store.set_seen("owner", NOW)
    store.set_seen("owner", NOW + 10_000)
    assert store.seen("owner") == NOW + 10_000


def test_pauses_reach_past_the_start_even_when_they_began_before_it(store):
    """A pause still open from before ``since`` (the session's start) is today's too; one that
    ended before it is not."""
    since = NOW
    ended = store.open_pause("owner", since - 7_200_000, "feed")
    store.close_pause("owner", ended, since - 3_600_000)
    across = store.open_pause("owner", since - 600_000, "service")
    store.close_pause("owner", across, since + 60_000)
    store.open_pause("owner", since - 300_000, "feed")  # still open
    assert store.pauses("owner", since) == (
        Pause(since - 600_000, since + 60_000, "service"),
        Pause(since - 300_000, None, "feed"),
    )


def test_prune_keeps_100_days_never_fewer_than_90_and_every_definition(path):
    store = MarketStore(path, keep_days=30)
    assert store.keep_days == 90
    store.close()
    store = MarketStore(path)
    row = store.create("owner", FIELDS, NOW - 200 * DAY_MS)
    for n, age in enumerate((101, 99)):
        at = NOW - age * DAY_MS
        store.record_fire("owner", fire(row["id"], f"1:v1:{n}", at), State(),
                          title="t", line="l", value="v", level="l", channels=BOTH)  # fmt: skip
        store.open_pause("owner", at, "feed")
    store.put_baseline("owner", "K", 1, 20, 1.0, NOW - 11 * DAY_MS)
    store.put_baseline("owner", "K", 2, 20, 1.0, NOW - 9 * DAY_MS)
    assert store.prune(NOW) == 5  # the 101-day event, its 2 deliveries, a pause, a baseline
    assert [e["fired_at"] for e in store.events("owner")] == [NOW - 99 * DAY_MS]
    assert store.alert("owner", row["id"]) is not None
    assert store.baseline("owner", "K", 1) is None and store.baseline("owner", "K", 2) == (20, 1.0)
    store.close()


# ---- principal -----------------------------------------------------------------------------

#: Upkeep, not readers: they touch no one's rows by principal.
UPKEEP = {"prune", "close"}


def test_every_public_method_takes_the_principal_first():
    for name, method in inspect.getmembers(MarketStore, inspect.isfunction):
        if name.startswith("_") or name in UPKEEP:
            continue
        params = list(inspect.signature(method).parameters)
        assert params[:2] == ["self", "principal"], name


def test_the_owners_store_refuses_anyone_else(store):
    with pytest.raises(UnknownPrincipal):
        store.alerts("family")
    with pytest.raises(UnknownPrincipal):
        store.create("viewer", FIELDS, NOW)


def test_one_principal_never_reads_anothers_rows(path):
    store = MarketStore(path, principals=("owner", "family"))
    mine = store.create("owner", FIELDS, NOW)
    theirs = store.create("family", FIELDS, NOW)
    store.save_states("owner", {mine["id"]: (1, State(1)), theirs["id"]: (1, State(2))}, NOW)
    store.save_states("family", {theirs["id"]: (1, State(3))}, NOW)
    their_event = store.record_fire("family", fire(theirs["id"], "2:v1:once"), State(3),
                                    title="t", line="l", value="v", level="l", channels=BOTH)  # fmt: skip
    store.set_prefs("family", telegram=False, app=False, now=NOW)
    assert [r["id"] for r in store.alerts("owner")] == [mine["id"]]
    assert store.alert("owner", theirs["id"]) is None
    assert store.states("owner") == {mine["id"]: State(1)}
    assert store.events("owner") == [] and store.events_since("owner", 0) == []
    assert store.pending("owner", "telegram") == []
    assert store.deliveries("owner", [their_event]) == {their_event: {}}
    assert store.prefs("owner") == {"telegram": True, "app": True}
    assert not store.delete("owner", theirs["id"]) and store.alert("family", theirs["id"])
    with pytest.raises(NotFound):
        store.pause("owner", theirs["id"], 1, NOW)
    with pytest.raises(NotFound):
        store.record_fire("owner", fire(theirs["id"], "x"), State(), title="t", line="l",
                          value="v", level="l", channels=BOTH)  # fmt: skip
    store.close()
