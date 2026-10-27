"""market.db: migrations by user_version, once-only fires, deliveries, settings, retention."""

from __future__ import annotations

import contextlib
import inspect
import os
import sqlite3
from datetime import datetime

import pytest
from sqlalchemy import create_engine

from sudarshan.alerts.market import migrations, spec
from sudarshan.alerts.market.clock import Pause
from sudarshan.alerts.market.engine import Fire, State
from sudarshan.alerts.market.store import (
    DB_FILENAME,
    OFF_REASON,
    MarketStore,
    NotFound,
    StoreError,
    UnknownPrincipal,
    VersionConflict,
    meta,
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
    return Fire(alert_id, claim, 20261021, 0, at, at, 10005, 10000, 1, 1, **more)


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
    store.save_states("owner", {row["id"]: State(20261021, -1, 9995, NOW)}, NOW)
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
    store.set_status("owner", stopped["id"], "stopped", "Fired once at 10:00 on 21 Oct", NOW)
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
    store.engine = create_engine("sqlite:////nonexistent/dir/market.db")
    with pytest.raises(StoreError) as caught:
        store.create("owner", FIELDS, NOW)
    assert str(caught.value).startswith("The market alert record could not be written (")
    assert str(caught.value).endswith("changes cannot be saved until this is fixed.")
    with pytest.raises(StoreError, match="could not be read"):
        store.alerts("owner")


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
    store.save_states("owner", {row["id"]: State(20261021, -1, 9995, NOW)}, NOW)
    edited = store.update("owner", row["id"], 1, FIELDS | {"note": "new"}, NOW + 1, status="active")
    assert edited["version"] == 2 and edited["note"] == "new"
    assert store.states("owner")[row["id"]].side == -1  # a note edit keeps the state
    with pytest.raises(VersionConflict) as caught:
        store.update("owner", row["id"], 1, FIELDS, NOW + 2, status="paused")
    assert (
        str(caught.value)
        == "This alert was changed on another screen. Open it again, then edit it."
    )
    with pytest.raises(NotFound):
        store.update("owner", 999, 1, FIELDS, NOW, status="active")
    level = FIELDS | {"params": FIELDS["params"] | {"level": "102"}}
    store.update("owner", row["id"], 2, level, NOW + 3, status="active", reset_state=True)
    assert row["id"] not in store.states("owner")
    with pytest.raises(ValueError):
        store.update("owner", row["id"], 3, FIELDS, NOW, status="armed")


def test_set_status_and_delete(store):
    row = store.create("owner", FIELDS, NOW)
    assert store.set_status("owner", row["id"], "stopped", "Ended on 20 Dec", NOW + 5)
    after = store.alert("owner", row["id"])
    assert (after["status"], after["status_reason"], after["version"]) == (
        "stopped",
        "Ended on 20 Dec",
        2,
    )
    store.record_fire("owner", fire(row["id"], "1:v1:once"), State(20261021, 0),
                      title="t", line="l", value="1", level="1", channels=BOTH)  # fmt: skip
    assert store.delete("owner", row["id"]) and not store.delete("owner", row["id"])
    assert store.alert("owner", row["id"]) is None and store.states("owner") == {}
    assert len(store.events("owner")) == 1  # the history outlives the alert


def test_states_are_saved_only_for_alerts_still_there(store):
    row = store.create("owner", FIELDS, NOW)
    full = State(20261021, 1, 10005, NOW, NOW - 5, 3, 7, 2, 1, forming=None, derived=("x",))
    assert store.save_states("owner", {row["id"]: full, 999: State()}, NOW) == 1
    assert store.states("owner") == {row["id"]: State(*full[:9])}  # memory-only fields drop


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
    store.save_states("owner", {mine["id"]: State(1), theirs["id"]: State(2)}, NOW)
    store.save_states("family", {theirs["id"]: State(3)}, NOW)
    store.record_fire("family", fire(theirs["id"], "2:v1:once"), State(3), title="t", line="l",
                      value="v", level="l", channels=BOTH)  # fmt: skip
    store.set_prefs("family", telegram=False, app=False, now=NOW)
    assert [r["id"] for r in store.alerts("owner")] == [mine["id"]]
    assert store.alert("owner", theirs["id"]) is None
    assert store.states("owner") == {mine["id"]: State(1)}
    assert store.events("owner") == [] and store.events_since("owner", 0) == []
    assert store.pending("owner", "telegram") == []
    assert store.prefs("owner") == {"telegram": True, "app": True}
    assert not store.delete("owner", theirs["id"]) and store.alert("family", theirs["id"])
    with pytest.raises(NotFound):
        store.update("owner", theirs["id"], 1, FIELDS, NOW, status="paused")
    with pytest.raises(NotFound):
        store.record_fire("owner", fire(theirs["id"], "x"), State(), title="t", line="l",
                          value="v", level="l", channels=BOTH)  # fmt: skip
    store.close()
