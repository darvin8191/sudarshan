"""The store: schema 1 as the design freezes it, the engine, the newer-file check (M8.4, W1).

The column lists are typed by hand from the design's table (2.2), never read from the models, so
a change to the schema fails here and is a decision.
"""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import threading
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from sudarshan.watchlists import contract, engine, schema
from sudarshan.watchlists.words import store as words

REPO = Path(__file__).resolve().parents[3]

FROZEN = {
    "wl_list": "principal list_id oa_created_at version updated_at",
    "wl_section": "id principal list_id name mark position created_at updated_at",
    "wl_section_item": "principal list_id exchange symbol section_id",
    "wl_row": (
        "id principal list_id section_id position kind underlying underlying_exchange expiry_rule"
        " strike_rule offset strike_paise opt_right created_at updated_at"
    ),
    "wl_symbol": "principal exchange symbol flag flag_at note note_at version updated_at",
    "wl_flag": "principal mark name",
    "wl_favourite": "principal slot ref updated_at oa_created_at",
    "wl_pref": "principal ref sort descending basis held updated_at",
    "wl_meta": "name value",
}
KEYS = {
    "wl_list": "principal list_id",
    "wl_section_item": "principal list_id exchange symbol",
    "wl_symbol": "principal exchange symbol",
    "wl_flag": "principal mark",
    "wl_favourite": "principal slot",
    "wl_pref": "principal ref",
    "wl_meta": "name",
}


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "db" / "sudarshan_watchlists.db"
    monkeypatch.setenv(schema.PATH_ENV, str(path))
    engine.dispose()
    yield path
    engine.dispose()


def _raw(path: Path, sql: str, *args):
    with closing(sqlite3.connect(path)) as db, db:
        return db.execute(sql, args).fetchall()


def test_the_tables_and_columns_are_exactly_the_frozen_ones(store):
    with engine.open_session() as s:
        inspector = inspect(s.get_bind())
        assert set(inspector.get_table_names()) == set(FROZEN)
        for table, columns in FROZEN.items():
            assert [c["name"] for c in inspector.get_columns(table)] == columns.split(), table
        for table, key in KEYS.items():
            assert inspector.get_pk_constraint(table)["constrained_columns"] == key.split()
        assert inspector.get_pk_constraint("wl_section")["constrained_columns"] == ["id"]
        assert inspector.get_pk_constraint("wl_row")["constrained_columns"] == ["id"]


def test_the_file_is_in_db_and_the_variable_is_the_contracts(tmp_path, monkeypatch):
    monkeypatch.delenv(schema.PATH_ENV, raising=False)
    assert schema.default_path() == REPO / "db" / "sudarshan_watchlists.db"
    assert schema.PATH_ENV == contract.DB_ENV
    monkeypatch.setenv(schema.PATH_ENV, "elsewhere/w.db")
    assert schema.default_path() == REPO / "elsewhere" / "w.db"
    monkeypatch.setenv(schema.PATH_ENV, str(tmp_path / "w.db"))
    assert schema.default_path() == tmp_path / "w.db"


def test_schema_one_is_recorded_and_nothing_else_is_seeded(store):
    with engine.open_session():
        pass
    assert _raw(store, "SELECT name, value FROM wl_meta") == [("schema", "1")]
    for table in FROZEN:
        if table != "wl_meta":
            assert _raw(store, f"SELECT count(*) FROM {table}") == [(0,)], table


def test_the_engine_is_the_factorys_and_uses_nullpool(store):
    with engine.open_session() as s:
        assert isinstance(s.get_bind().pool, NullPool)
        assert s.get_bind().url.database == str(store)


def test_creation_is_idempotent_and_keeps_every_row(store):
    with engine.open_session() as s:
        s.execute(text("INSERT INTO wl_list VALUES ('owner', 7, '2026-11-01 10:00:00', 4, 99)"))
        s.execute(text("UPDATE wl_meta SET value = '1'"))
        s.commit()
    engine.dispose()
    with engine.open_session() as s:  # a second process start
        engine.prepare(s.get_bind())  # and the app and migration meeting
        engine.prepare(s.get_bind())
    assert _raw(store, "SELECT * FROM wl_list") == [("owner", 7, "2026-11-01 10:00:00", 4, 99)]
    assert _raw(store, "SELECT * FROM wl_meta") == [("schema", "1")]


def test_a_missing_column_is_added_in_place(store):
    with engine.open_session():
        pass
    engine.dispose()
    with closing(sqlite3.connect(store)) as db, db:
        db.execute("DROP TABLE wl_pref")
        db.execute(
            "CREATE TABLE wl_pref (principal VARCHAR(16) NOT NULL DEFAULT 'owner', ref VARCHAR(80)"
            " NOT NULL, sort VARCHAR(12) NOT NULL DEFAULT 'list', updated_at BIGINT NOT NULL,"
            " PRIMARY KEY (principal, ref))"
        )
        db.execute("INSERT INTO wl_pref VALUES ('owner', 'list:3', 'price', 5)")
    with engine.open_session() as s:
        columns = [c["name"] for c in inspect(s.get_bind()).get_columns("wl_pref")]
    assert {"descending", "basis", "held"} <= set(columns)
    assert _raw(store, "SELECT ref, sort, descending, basis, held FROM wl_pref") == [
        ("list:3", "price", 0, "close", 0)
    ]


def test_a_file_from_a_newer_release_is_refused_in_words_and_left_alone(store):
    with engine.open_session():
        pass
    engine.dispose()
    _raw(store, "UPDATE wl_meta SET value = '2' WHERE name = 'schema'")
    _raw(store, "CREATE TABLE wl_future (x INTEGER)")
    before = store.read_bytes()
    for _ in range(2):  # refused each time, never cached as ready
        with pytest.raises(engine.RecordTooNew) as refused:
            engine.open_session()
        assert str(refused.value) == words.RECORD_NEWER
    assert "newer SUDARSHAN release" in words.RECORD_NEWER
    assert store.read_bytes() == before  # not a byte changed, no column or index added
    assert [r[0] for r in _raw(store, "SELECT name FROM sqlite_master WHERE name='wl_future'")]


def test_an_unreadable_schema_value_is_refused_like_a_newer_file(store):
    with engine.open_session():
        pass
    engine.dispose()
    _raw(store, "UPDATE wl_meta SET value = 'banana' WHERE name = 'schema'")
    with pytest.raises(engine.RecordTooNew):
        engine.open_session()


def test_a_file_that_is_not_a_database_is_unreadable_in_words(store):
    store.parent.mkdir(parents=True)
    store.write_bytes(b"this is not a database" * 50)
    with pytest.raises(engine.RecordUnreadable) as refused:
        engine.open_session()
    assert str(refused.value) == words.RECORD_UNREADABLE


def test_a_file_with_tables_but_no_schema_row_is_adopted(store):
    store.parent.mkdir(parents=True)
    _raw(store, "CREATE TABLE wl_meta (name VARCHAR(40) PRIMARY KEY, value VARCHAR(80) NOT NULL)")
    with engine.open_session():
        pass
    assert _raw(store, "SELECT value FROM wl_meta WHERE name = 'schema'") == [("1",)]


def test_ranges_and_enums_are_held_by_the_file(store):
    bad = [
        "INSERT INTO wl_section (list_id, name, mark, position, created_at, updated_at)"
        " VALUES (1, '', 0, 0, 1, 1)",
        "INSERT INTO wl_section (list_id, name, mark, position, created_at, updated_at)"
        " VALUES (1, 'x', 6, 0, 1, 1)",
        "INSERT INTO wl_symbol (exchange, symbol, flag, updated_at) VALUES ('NSE', 'A', 6, 1)",
        "INSERT INTO wl_flag (mark, name) VALUES (0, 'x')",
        "INSERT INTO wl_favourite (slot, ref, updated_at) VALUES (8, 'list:1', 1)",
        "INSERT INTO wl_pref (ref, sort, updated_at) VALUES ('list:1', 'random', 1)",
        "INSERT INTO wl_pref (ref, basis, updated_at) VALUES ('list:1', 'high', 1)",
        "INSERT INTO wl_row (list_id, position, kind, underlying, underlying_exchange,"
        " expiry_rule, created_at, updated_at) VALUES (1, 0, 'spread', 'NIFTY', 'NSE_INDEX',"
        " 'nearest', 1, 1)",
        "INSERT INTO wl_row (list_id, position, kind, underlying, underlying_exchange,"
        " expiry_rule, \"offset\", created_at, updated_at) VALUES (1, 0, 'atm_option', 'NIFTY',"
        " 'NSE_INDEX', 'nearest', 11, 1, 1)",
        "INSERT INTO wl_row (list_id, position, kind, underlying, underlying_exchange,"
        " expiry_rule, opt_right, created_at, updated_at) VALUES (1, 0, 'atm_option', 'NIFTY',"
        " 'NSE_INDEX', 'nearest', 'XX', 1, 1)",
    ]
    with engine.open_session() as s:
        for sql in bad:
            with pytest.raises(IntegrityError):
                s.execute(text(sql))
            s.rollback()
        s.execute(
            text(
                "INSERT INTO wl_section (list_id, name, position, created_at, updated_at)"
                " VALUES (1, 'Core', 0, 1, 1)"
            )
        )
        with pytest.raises(IntegrityError):  # a name once a list
            s.execute(
                text(
                    "INSERT INTO wl_section (list_id, name, position, created_at, updated_at)"
                    " VALUES (1, 'Core', 1, 1, 1)"
                )
            )
        s.rollback()
        s.execute(
            text(
                "INSERT INTO wl_symbol (exchange, symbol, note, updated_at)"
                " VALUES ('NSE', 'A', :n, 1)"
            ),
            {"n": "x" * 500},
        )
        with pytest.raises(IntegrityError):
            s.execute(
                text(
                    "INSERT INTO wl_symbol (exchange, symbol, note, updated_at) VALUES (:e, 'B', :n, 1)"
                ),
                {"e": "NSE", "n": "x" * 501},
            )


def test_import_does_nothing_and_no_thread_is_started(tmp_path):
    path = tmp_path / "never.db"
    code = (
        "import threading, os;"
        "before = threading.enumerate();"
        "import sudarshan.watchlists.schema, sudarshan.watchlists.engine as e;"
        "assert threading.enumerate() == before;"
        "assert e._engine.peek() is None and not e._ready;"
        f"assert not os.path.exists({str(path)!r});"
        "e.open_session().close();"
        "assert threading.enumerate() == before;"
        "e.dispose()"
    )
    env = {"SUDARSHAN_WATCHLISTS_DB": str(path), "PATH": "", "PYTHONPATH": str(REPO)}
    done = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO, env=env, capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0, done.stderr
    assert path.exists()


def test_schema_alone_imports_sqlalchemy_only():
    code = (
        "import sys; import sudarshan.watchlists.schema;"
        "bad = [m for m in ('flask','database','utils','extensions') if m in sys.modules];"
        "assert not bad, bad"
    )
    done = subprocess.run(
        [sys.executable, "-c", code],
        cwd=REPO,
        env={"PYTHONPATH": str(REPO), "PATH": ""},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr


def test_concurrent_first_uses_make_one_file_and_agree(store):
    errors = []

    def first_use():
        try:
            with engine.open_session():
                pass
        except Exception as exc:  # pragma: no cover - the assertion below reports it
            errors.append(exc)

    threads = [threading.Thread(target=first_use) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert not errors
    assert _raw(store, "SELECT value FROM wl_meta WHERE name = 'schema'") == [("1",)]


@pytest.mark.skipif(not Path("/proc/self/fd").exists(), reason="counts descriptors through /proc")
def test_repeated_use_leaks_no_descriptor_and_keeps_one_engine(store):
    def fds() -> int:
        return len(list(Path("/proc/self/fd").iterdir()))

    with engine.open_session():  # first use: the file, the engine
        pass
    held = engine._engine.get()
    before = fds()
    for _ in range(300):
        with engine.open_session() as s:
            s.execute(text("SELECT count(*) FROM wl_list")).scalar()
        with engine.open_session() as s:
            s.execute(text("INSERT INTO wl_meta VALUES ('x', '1') ON CONFLICT DO NOTHING"))
            s.commit()
    assert fds() <= before + 1
    assert engine._engine.get() is held  # one engine for the process's life
