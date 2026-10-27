"""The risk database: defaults, whole versions, invalidation after commit, locks, audit (M5.13)."""

from __future__ import annotations

import dataclasses
import os
import sqlite3
import threading
from datetime import UTC, datetime, timedelta

import pytest

from sudarshan.guard import schema, store
from sudarshan.guard.store import KillSwitchState, RiskSettings, RiskStore

WAIT = 10


def test_a_new_file_has_every_guard_off_and_the_switch_off(risk_store):
    settings = risk_store.settings()
    assert settings == RiskSettings()
    assert not settings.guards_on
    assert settings.daily_loss_limit is None  # never a figure SUDARSHAN picked
    assert settings.confirm_phrase == "STOP"
    assert risk_store.state() == KillSwitchState()
    assert risk_store.path.exists()


def test_the_columns_default_to_the_values_the_code_uses(tmp_path):
    """The file's own defaults (what the migration writes) equal the dataclass defaults."""
    path = tmp_path / "r.db"
    RiskStore(path).settings()
    db = sqlite3.connect(path)
    try:
        row = db.execute("SELECT * FROM risk_settings WHERE id = 1")
        names = [column[0] for column in row.description]
        values = dict(zip(names, row.fetchone(), strict=True))
    finally:
        db.close()
    for name, default in schema.DEFAULTS.items():
        assert values[name] == default, name
        assert getattr(RiskSettings(), name) == default, name


def test_settings_are_one_immutable_version(risk_store):
    settings = risk_store.settings()
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.band_on = True  # type: ignore[misc]
    saved = risk_store.save_settings("owner", band_on=True, band_opt_pct=25.0)
    assert (saved.band_on, saved.band_opt_pct, saved.updated_by) == (True, 25.0, "owner")
    assert saved.updated_at is not None and saved.updated_at.tzinfo is not None
    assert settings.band_on is False  # the version read before is unchanged
    assert risk_store.settings() == saved


def test_a_name_that_is_not_a_setting_is_refused(risk_store):
    with pytest.raises(ValueError):
        risk_store.save_settings("owner", order_mode="auto")
    with pytest.raises(ValueError):
        risk_store.save_state(analyze_mode=True)


def test_the_kill_switch_round_trips(risk_store):
    since = datetime(2026, 10, 27, 5, 12, 30, tzinfo=UTC)
    saved = risk_store.save_state(
        on=True,
        since=since,
        surface="telegram",
        actor="owner",
        activation_id="ks-1",
        exit_all=True,
        running=True,
        report_json='{"steps": [{"step": "drain", "ok": true}]}',
    )
    assert saved.on and saved.since == since and saved.running and saved.exit_all
    assert saved.report == {"steps": [{"step": "drain", "ok": True}]}
    assert RiskStore(risk_store.path).state() == saved  # as another process reads it


def test_a_read_that_raced_a_write_is_returned_but_never_cached(risk_store):
    """Barrier-synchronised: the read loads the old row, the write commits, then the read ends."""
    risk_store.settings()
    risk_store._cache.clear()
    loaded, resume = threading.Event(), threading.Event()
    original = risk_store._load_settings

    def slow_load():
        version = original()
        loaded.set()
        assert resume.wait(WAIT)
        return version

    risk_store._load_settings = slow_load
    seen = {}
    reader = threading.Thread(target=lambda: seen.setdefault("read", risk_store.settings()))
    reader.start()
    assert loaded.wait(WAIT)
    risk_store._load_settings = original
    risk_store.save_settings("owner", size_on=True)  # commits, then invalidates
    resume.set()
    reader.join(WAIT)
    assert seen["read"].size_on is False  # its own caller gets what it read
    assert risk_store.settings().size_on is True  # but that was never cached


def test_a_day_is_locked_once_and_keeps_its_first_figure(risk_store):
    assert risk_store.day_lock("live", "2026-10-27") is None
    first = risk_store.lock_day("live", "2026-10-27", -4100.0, 4000.0)
    again = risk_store.lock_day("live", "2026-10-27", -5200.0, 4000.0)
    assert again == first and again.figure == -4100.0
    assert risk_store.day_lock("sandbox", "2026-10-27") is None
    assert risk_store.day_lock("live", "2026-10-28") is None


def test_refusals_within_a_minute_share_one_row(risk_store, monkeypatch):
    now = [datetime(2026, 10, 27, 5, 0, tzinfo=UTC)]
    monkeypatch.setattr(store, "utcnow", lambda: now[0])
    for _ in range(3):
        risk_store.audit("order_refused", "first words", merge_key="ks|api|NIFTY")
        now[0] += timedelta(seconds=20)
    risk_store.audit("order_refused", "other symbol", merge_key="ks|api|BANKNIFTY")
    now[0] += timedelta(seconds=61)
    risk_store.audit("order_refused", "a minute on", merge_key="ks|api|NIFTY")
    rows = risk_store.history()
    assert [(row.summary, row.count) for row in rows] == [
        ("a minute on", 1),
        ("other symbol", 1),
        ("first words", 3),
    ]


def test_old_rows_are_pruned_at_most_once_a_day(risk_store, monkeypatch):
    now = [datetime(2026, 7, 1, 5, 0, tzinfo=UTC)]
    monkeypatch.setattr(store, "utcnow", lambda: now[0])
    risk_store.audit("ks_on", "old")
    now[0] = datetime(2026, 10, 27, 5, 0, tzinfo=UTC)
    deletes = []
    original = risk_store._session

    def counting():
        session = original()
        execute = session.execute

        def spy(statement, *args, **kwargs):
            if statement.__class__.__name__ == "Delete":
                deletes.append(statement)
            return execute(statement, *args, **kwargs)

        session.execute = spy
        return session

    monkeypatch.setattr(risk_store, "_session", counting)
    risk_store.audit("ks_off", "new")
    risk_store.audit("ks_on", "newer")
    assert len(deletes) == 1
    assert [row.summary for row in risk_store.history()] == ["newer", "new"]


def test_history_is_newest_first_and_bounded(risk_store):
    for n in range(5):
        risk_store.audit("settings_changed", f"change {n}", detail={"n": n})
    rows = risk_store.history(limit=2)
    assert [(row.summary, row.detail) for row in rows] == [
        ("change 4", {"n": 4}),
        ("change 3", {"n": 3}),
    ]
    assert len(risk_store.history(limit=0)) == 1


def test_first_use_from_two_threads_at_once_builds_one_engine(tmp_path):
    fresh = RiskStore(tmp_path / "r.db")
    start = threading.Barrier(4)
    seen, errors = [], []

    def first_use():
        start.wait(WAIT)
        try:
            seen.append(fresh.settings())
        except Exception as exc:  # pragma: no cover - the failure being guarded
            errors.append(exc)

    threads = [threading.Thread(target=first_use) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(WAIT)
    assert not errors and len(seen) == 4
    assert fresh._engine is not None
    fresh.dispose()


def test_many_operations_hold_no_file_open(risk_store):
    """NullPool: each operation opens and closes its own connection (FD hygiene)."""
    risk_store.settings()
    fd_dir = "/proc/self/fd"
    if not os.path.isdir(fd_dir):
        pytest.skip("needs /proc")
    before = len(os.listdir(fd_dir))
    for n in range(100):
        risk_store.save_settings("owner", burst_n=10 + n)
        risk_store.state()
        risk_store.audit("settings_changed", "x", merge_key="k")
    assert len(os.listdir(fd_dir)) <= before + 2  # what the interpreter opened meanwhile
