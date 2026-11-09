"""market.db step 2 and the group store methods (M8.14): list alerts as one definition."""

from __future__ import annotations

import contextlib
import sqlite3
import threading
from datetime import datetime

import pytest

from sudarshan.alerts.market import migrations, spec
from sudarshan.alerts.market.engine import State
from sudarshan.alerts.market.store import (
    DB_FILENAME,
    GroupRefused,
    MarketStore,
    NotFound,
    VersionConflict,
    meta,
)
from sudarshan.autologin.schedule import IST

NOW = int(datetime(2026, 11, 9, 10, 0, tzinfo=IST).timestamp() * 1000)
PRICE = {"op": "crosses_above", "level": "100"}
GROUP = {"ref": "list:7", "name": "Swing", "kind": "pct_move", "params": {"pct": "2"},
         "frequency": "every_time", "cooldown_s": 300, "delivery": "first_then_digest",
         "digest_min": 5, "covered": 3, "left_out": ["ATM and strike rows: 1"],
         "synced_version": "boot:4"}  # fmt: skip


def member(n: int, **more) -> dict:
    """A member as spec.check makes it: a price alert on the n-th instrument."""
    return spec.check(
        {"instrument_key": f"NSE_EQ|INE{n:03d}A01020", "symbol": f"S{n}", "exchange": "NSE",
         "tick_size": "0.05", "kind": "price", "params": PRICE} | more, NOW)  # fmt: skip


def raw(path, sql: str):
    with contextlib.closing(sqlite3.connect(path)) as conn, conn:
        return conn.execute(sql).fetchall()


@pytest.fixture
def path(tmp_path):
    return tmp_path / "state" / DB_FILENAME


@pytest.fixture
def store(path):
    made = MarketStore(path)
    yield made
    made.close()


def keys(store) -> list[str]:
    return [a["instrument_key"] for a in store.alerts("owner")]


# ---- step 2 --------------------------------------------------------------------------------


def test_step_2_adds_the_group_table_and_the_nullable_column(path, store):
    assert store.applied == [1, 2] and migrations.LATEST == 2
    tables = {r[0] for r in raw(path, "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "alert_group" in tables
    info = {r[1]: r for r in raw(path, "PRAGMA table_info(alert)")}
    assert info["group_id"][2] == "INTEGER" and info["group_id"][3] == 0  # nullable
    for table in meta.sorted_tables:  # what the store reads is what the file has
        assert {r[1] for r in raw(path, f"PRAGMA table_info({table.name})")} == {
            c.name for c in table.columns}, table.name  # fmt: skip
    indexes = {r[0] for r in raw(path, "SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"alert_group_id", "alert_group_principal"} <= indexes


def test_step_2_has_no_check_constraint(path, store):
    sql = "".join(r[0] or "" for r in raw(path, "SELECT sql FROM sqlite_master"))
    assert "CHECK" not in sql.upper()


def test_a_populated_step_1_copy_keeps_every_row_and_gains_the_column(path, store):
    one = store.create("owner", member(1), NOW)
    two = store.create("owner", member(2), NOW)
    store.set_status("owner", two["id"], "stopped", "Fired once", NOW, version=1)
    store.save_states("owner", {one["id"]: (1, State(20261109, 1, 9995, NOW))}, NOW)
    store.close()
    # force the file back to what step 1 left: no group table, no column, version 1
    raw(path, "DROP INDEX alert_group_id")
    raw(path, "ALTER TABLE alert DROP COLUMN group_id")
    raw(path, "DROP TABLE alert_group")
    raw(path, "PRAGMA user_version = 1")
    again = MarketStore(path)
    assert again.applied == [2]
    assert [a["id"] for a in again.alerts("owner")] == [one["id"], two["id"]]
    assert again.alert("owner", two["id"])["status"] == "stopped"
    assert again.alert("owner", one["id"])["group_id"] is None
    assert again.states("owner")[one["id"]].ref == 9995
    again.close()
    raw(path, "PRAGMA user_version = 1")  # forced back with everything already there
    third = MarketStore(path)
    assert third.applied == [2] and len(third.alerts("owner")) == 2
    third.close()
    fourth = MarketStore(path)
    assert fourth.applied == []
    fourth.close()


def test_a_file_from_a_newer_release_is_refused_and_left_alone(path, store):
    store.close()
    raw(path, "PRAGMA user_version = 3")
    with pytest.raises(migrations.SchemaTooNew):
        MarketStore(path)
    assert raw(path, "PRAGMA user_version") == [(3,)]


# ---- the group and its members -------------------------------------------------------------


def test_a_group_is_made_with_its_members_and_read_back(store):
    made = store.create_group("owner", GROUP, [member(1), member(2), member(3)], NOW)
    assert made["status"] == "active" and made["version"] == 1 and made["ref"] == "list:7"
    assert made["params"] == {"pct": "2"} and made["left_out"] == ["ATM and strike rows: 1"]
    rows = store.group_members("owner", made["id"])
    assert [r["symbol"] for r in rows] == ["S1", "S2", "S3"]
    assert {r["group_id"] for r in rows} == {made["id"]} and {r["status"] for r in rows} == {
        "active"
    }
    assert store.groups("owner") == [made] and store.group("owner", made["id"]) == made
    plain = store.create("owner", member(9), NOW)
    assert plain["group_id"] is None and len(store.alerts("owner")) == 4


def test_a_group_that_does_not_fit_makes_nothing(store):
    store.create("owner", member(1), NOW)
    seen = []

    def fit(in_use, active, new):
        seen.append((in_use, active, new))
        return "It does not fit: nothing was made."

    with pytest.raises(GroupRefused) as caught:
        store.create_group("owner", GROUP, [member(1), member(2)], NOW, fit=fit)
    assert str(caught.value) == "It does not fit: nothing was made."
    assert seen == [(frozenset({"NSE_EQ|INE001A01020"}), 1,
                     ["NSE_EQ|INE001A01020", "NSE_EQ|INE002A01020"])]  # fmt: skip
    assert store.groups("owner") == [] and len(store.alerts("owner")) == 1


def test_the_fit_sees_only_active_alerts(store):
    paused = store.create("owner", member(1), NOW)
    store.pause("owner", paused["id"], 1, NOW)
    store.create("owner", member(2), NOW)
    seen = []
    store.create_group("owner", GROUP, [member(3)], NOW,
                       fit=lambda *a: seen.append(a) or None)  # fmt: skip
    assert seen == [(frozenset({"NSE_EQ|INE002A01020"}), 1, ["NSE_EQ|INE003A01020"])]


def test_two_groups_made_at_once_cannot_both_take_the_last_room(store):
    """The fit runs under the write lock: the second reads what the first made."""
    room = 2

    def fit(in_use, active, new):
        return None if active + len(new) <= room else "It does not fit: nothing was made."

    results = [None, None]

    def make(n):
        try:
            results[n] = store.create_group(
                "owner", GROUP | {"name": f"G{n}"}, [member(n * 10 + 1), member(n * 10 + 2)],
                NOW, fit=fit)  # fmt: skip
        except GroupRefused as exc:
            results[n] = exc

    threads = [threading.Thread(target=make, args=(n,)) for n in (0, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(type(r).__name__ for r in results) == ["GroupRefused", "dict"]
    assert len(store.alerts("owner")) == 2 and len(store.groups("owner")) == 1


def test_an_edit_reaches_the_group_and_every_member_in_one_step(store):
    made = store.create_group("owner", GROUP, [member(1), member(2)], NOW)
    one, two = store.group_members("owner", made["id"])
    store.set_status("owner", two["id"], "stopped", "Fired once", NOW, version=1)
    store.save_states("owner", {one["id"]: (1, State(20261109, 1, 9995, NOW))}, NOW)
    with pytest.raises(VersionConflict):
        store.update_group("owner", made["id"], 9, {"cooldown_s": 60}, NOW)
    edited = store.update_group("owner", made["id"], 1, {"cooldown_s": 60, "name": "Swing 2"},
                                NOW + 1, reset_state=True)  # fmt: skip
    assert edited["version"] == 2 and edited["name"] == "Swing 2" and edited["cooldown_s"] == 60
    rows = store.group_members("owner", made["id"])
    assert [r["cooldown_s"] for r in rows] == [60, 60] and [r["version"] for r in rows] == [2, 2]
    assert [r["status"] for r in rows] == ["active", "active"]  # a changed condition restarts
    assert store.states("owner") == {}  # reset in the same step


def test_an_edit_that_restarts_stopped_members_is_held_to_the_limits(store):
    made = store.create_group("owner", GROUP, [member(1)], NOW)
    (one,) = store.group_members("owner", made["id"])
    store.set_status("owner", one["id"], "stopped", "Fired once", NOW, version=1)
    with pytest.raises(GroupRefused):
        store.update_group("owner", made["id"], 1, {"cooldown_s": 60}, NOW, reset_state=True,
                           fit=lambda *a: "It does not fit.")  # fmt: skip
    assert store.group_members("owner", made["id"])[0]["cooldown_s"] == 0
    assert store.group("owner", made["id"])["version"] == 1


def test_a_group_pauses_and_resumes_every_member_together(store):
    made = store.create_group("owner", GROUP, [member(1), member(2)], NOW)
    one, _ = store.group_members("owner", made["id"])
    paused = store.move_group("owner", made["id"], 1, "pause", NOW)
    assert paused["status"] == "paused" and paused["version"] == 2
    assert {r["status"] for r in store.group_members("owner", made["id"])} == {"paused"}
    with pytest.raises(GroupRefused):
        store.move_group("owner", made["id"], 2, "pause", NOW)
    with pytest.raises(GroupRefused):
        store.move_group("owner", made["id"], 2, "resume", NOW, fit=lambda *a: "No room.")
    assert store.group("owner", made["id"])["status"] == "paused"
    resumed = store.move_group("owner", made["id"], 2, "resume", NOW)
    assert resumed["status"] == "active"
    assert {r["status"] for r in store.group_members("owner", made["id"])} == {"active"}
    assert store.alert("owner", one["id"])["version"] == 3  # each member moved twice


def test_a_stale_screen_cannot_move_a_group(store):
    made = store.create_group("owner", GROUP, [member(1)], NOW)
    with pytest.raises(VersionConflict):
        store.move_group("owner", made["id"], 7, "pause", NOW)
    with pytest.raises(NotFound):
        store.move_group("owner", 99, 1, "pause", NOW)


def test_a_group_stopped_by_the_service_keeps_its_version_and_says_why(store):
    made = store.create_group("owner", GROUP, [member(1), member(2)], NOW)
    assert store.stop_group(
        "owner", made["id"], "The list Swing was deleted, so this alert stopped.", NOW
    )
    row = store.group("owner", made["id"])
    assert row["status"] == "stopped" and row["version"] == 1
    assert row["status_reason"].startswith("The list Swing was deleted")
    members = store.group_members("owner", made["id"])
    assert {r["status"] for r in members} == {"stopped"} and {r["version"] for r in members} == {1}
    assert not store.stop_group("owner", made["id"], "again", NOW)


def test_a_sync_adds_removes_and_keeps_the_rest_untouched(store):
    made = store.create_group("owner", GROUP, [member(1), member(2)], NOW)
    one, two = store.group_members("owner", made["id"])
    store.save_states("owner", {one["id"]: (1, State(20261109, 1, 9995, NOW))}, NOW)
    store.save_states("owner", {two["id"]: (1, State(20261109, 1, 9990, NOW))}, NOW)
    synced = store.sync_group("owner", made["id"], NOW + 5, add=[member(3)], remove=[two["id"]],
                              covered=2, left_out=[], synced_version="boot:5")  # fmt: skip
    assert synced["version"] == 1 and synced["covered"] == 2 and synced["left_out"] == []
    assert synced["synced_version"] == "boot:5" and synced["synced_at"] == NOW + 5
    assert [r["symbol"] for r in store.group_members("owner", made["id"])] == ["S1", "S3"]
    assert set(store.states("owner")) == {one["id"]}  # a kept member's state is untouched
    assert store.alert("owner", one["id"])["version"] == 1


def test_a_sync_never_removes_another_groups_alert(store):
    mine = store.create_group("owner", GROUP, [member(1)], NOW)
    other = store.create_group("owner", GROUP | {"ref": "list:8"}, [member(2)], NOW)
    (theirs,) = store.group_members("owner", other["id"])
    plain = store.create("owner", member(5), NOW)
    store.sync_group("owner", mine["id"], NOW, add=[], remove=[theirs["id"], plain["id"]],
                     covered=1, left_out=[], synced_version=None)  # fmt: skip
    assert len(store.group_members("owner", other["id"])) == 1 and store.alert("owner", plain["id"])


def test_a_sync_is_held_to_the_limits_only_while_the_group_is_active(store):
    made = store.create_group("owner", GROUP, [member(1)], NOW)
    with pytest.raises(GroupRefused):
        store.sync_group("owner", made["id"], NOW, add=[member(2)], remove=[], covered=2,
                         left_out=[], synced_version=None, fit=lambda *a: "No room.")  # fmt: skip
    assert len(store.group_members("owner", made["id"])) == 1
    store.move_group("owner", made["id"], 1, "pause", NOW)
    store.sync_group("owner", made["id"], NOW, add=[member(2)], remove=[], covered=2,
                     left_out=[], synced_version=None, fit=lambda *a: "No room.")  # fmt: skip
    assert {r["status"] for r in store.group_members("owner", made["id"])} == {"paused"}


def test_deleting_a_group_takes_its_members_and_state_and_keeps_the_log(store):
    made = store.create_group("owner", GROUP, [member(1), member(2)], NOW)
    keep = store.create("owner", member(9), NOW)
    one, _ = store.group_members("owner", made["id"])
    store.save_states("owner", {one["id"]: (1, State(20261109, 1, 9995, NOW))}, NOW)
    assert store.delete_group("owner", made["id"]) and not store.delete_group("owner", made["id"])
    assert [a["id"] for a in store.alerts("owner")] == [keep["id"]] and store.states("owner") == {}
    assert store.groups("owner") == []


def test_a_group_row_is_the_owners_only(store, path):
    made = store.create_group("owner", GROUP, [member(1)], NOW)
    other = MarketStore(path, principals=("owner", "family"))
    try:
        assert other.group("family", made["id"]) is None and other.groups("family") == []
        assert other.group_members("family", made["id"]) == []
        assert not other.delete_group("family", made["id"])
        with pytest.raises(NotFound):
            other.move_group("family", made["id"], 1, "pause", NOW)
    finally:
        other.close()


def test_an_unknown_delivery_is_refused(store):
    with pytest.raises(ValueError):
        store.create_group("owner", GROUP | {"delivery": "hourly"}, [member(1)], NOW)
