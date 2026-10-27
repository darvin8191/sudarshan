"""market.db opened only to read (cross-design X7, A7p) and ``migrate --status`` (A9)."""

from __future__ import annotations

import contextlib
import hashlib
import sqlite3
from datetime import datetime

import pytest

from sudarshan.alerts.market import migrate, migrations, spec
from sudarshan.alerts.market.store import WORDS, MarketStore, StoreError
from sudarshan.autologin.schedule import IST

NOW = int(datetime(2026, 10, 21, 10, 0, tzinfo=IST).timestamp() * 1000)
FIELDS = spec.check(
    {"instrument_key": "NSE_EQ|INE062A01020", "symbol": "SBIN", "exchange": "NSE",
     "tick_size": "0.05", "kind": "price", "params": {"op": "crosses_above", "level": "100"}},
    NOW,
)  # fmt: skip


def digest(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def raw(path, sql: str):
    with contextlib.closing(sqlite3.connect(path)) as conn, conn:
        return conn.execute(sql).fetchall()


@pytest.fixture
def path(tmp_path):
    made = MarketStore(tmp_path / "market.db")
    made.create("owner", FIELDS, NOW)
    made.close()
    return tmp_path / "market.db"


def test_a_reader_sees_the_record_and_writes_nothing(path):
    before = digest(path)
    reader = MarketStore(path, read_only=True)
    try:
        assert [row["symbol"] for row in reader.alerts("owner")] == ["SBIN"]
        assert reader.applied == [] and reader.read_only
        for write in (
            lambda: reader.create("owner", FIELDS, NOW),
            lambda: reader.set_prefs("owner", telegram=False, app=True, now=NOW),
            lambda: reader.pause("owner", 1, 1, NOW),
            lambda: reader.prune(NOW),
        ):
            with pytest.raises(StoreError) as caught:
                write()
            assert str(caught.value) == WORDS["read_only"]
    finally:
        reader.close()
    assert digest(path) == before


def test_a_reader_sees_what_a_running_writer_committed(path):
    writer = MarketStore(path)
    try:
        writer.create("owner", FIELDS | {"note": "second"}, NOW + 1)
        reader = MarketStore(path, read_only=True)
        try:
            assert [row["note"] for row in reader.alerts("owner")] == ["", "second"]
        finally:
            reader.close()
    finally:
        writer.close()


def test_a_missing_file_is_not_created(tmp_path):
    with pytest.raises(StoreError) as caught:
        MarketStore(tmp_path / "market.db", read_only=True)
    assert str(caught.value) == WORDS["missing"] and not (tmp_path / "market.db").exists()


def test_an_older_file_is_refused_and_left_as_it_is(path):
    raw(path, "PRAGMA user_version = 0")
    with pytest.raises(StoreError) as caught:
        MarketStore(path, read_only=True)
    assert "older release (version 0; this one reads version 1)" in str(caught.value)
    assert raw(path, "PRAGMA user_version") == [(0,)]  # never migrated by a reader


def test_a_newer_file_is_refused(path):
    raw(path, "PRAGMA user_version = 9")
    with pytest.raises(migrations.SchemaTooNew):
        MarketStore(path, read_only=True)


def test_migrate_status_reports_without_touching(path, tmp_path, monkeypatch, capsys):
    before = digest(path)
    assert migrate.main(["--status", "--db", str(path)]) == 0
    assert capsys.readouterr().out.splitlines() == [
        f"market.db: {path}", "file version: 1", "code version: 1", "pending steps: none",
    ]  # fmt: skip
    assert digest(path) == before
    raw(path, "PRAGMA user_version = 0")
    code, lines = migrate.status(path)
    assert code == 0 and lines[1:] == ["file version: 0", "code version: 1", "pending steps: 1"]
    raw(path, "PRAGMA user_version = 4")
    code, lines = migrate.status(path)
    assert code == 0 and lines[-1].startswith("pending steps: none; the file is from a newer")
    missing = tmp_path / "elsewhere" / "market.db"
    code, lines = migrate.status(missing)
    assert code == 0 and lines[1].startswith("file version: none") and not missing.exists()
    monkeypatch.setenv("SUDARSHAN_ALERTS_STATE_DIR", str(tmp_path))
    assert migrate.default_path() == path
    assert migrate.main([]) == 2  # only --status is offered
