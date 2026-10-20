"""The API key: read with OpenAlgo's own functions, read-only, in both states of a WAL file."""

from __future__ import annotations

import os
import sqlite3

import dotenv
import pytest
from alerts_fakes import API_KEY, NEW_API_KEY, OWNER, FakeOpenAlgoDb

from sudarshan.alerts import owner as owner_module
from sudarshan.alerts.config import ConfigError
from sudarshan.alerts.owner import (
    Owner,
    OwnerError,
    database_urls,
    load_openalgo_modules,
    prepare_openalgo_env,
    read_owner,
)


@pytest.fixture
def fake_db(tmp_path):
    db = FakeOpenAlgoDb(tmp_path / "db")
    yield db
    db.close()


def _files(path):
    return sorted(p.name for p in path.parent.iterdir())


def test_the_owner_and_his_key(fake_db):
    fake_db.set_key(API_KEY)
    owner = read_owner(fake_db.path)
    assert owner == Owner(OWNER, API_KEY)
    assert API_KEY not in repr(owner)  # never shown by a log line or a traceback


def test_reading_writes_nothing_next_to_the_database(fake_db):
    """No -wal file: the main file is read as it is, so no side file is created (read-only db/)."""
    fake_db.set_key(API_KEY)
    before = _files(fake_db.path)
    assert "openalgo.db-wal" not in before
    assert "immutable=1" in database_urls(fake_db.path)[0]
    assert read_owner(fake_db.path).api_key == API_KEY
    assert _files(fake_db.path) == before


def test_a_key_still_in_the_write_ahead_log_is_seen(fake_db):
    """While OpenAlgo holds the file open, a just-regenerated key may exist only in the -wal."""
    fake_db.set_key(API_KEY)
    from database import auth_db

    writer = sqlite3.connect(fake_db.path)
    try:
        writer.execute("PRAGMA wal_autocheckpoint=0")
        new = auth_db.encrypt_token(NEW_API_KEY)
        writer.execute("UPDATE api_keys SET api_key_encrypted = ?", (new,))
        writer.commit()
        assert "openalgo.db-wal" in _files(fake_db.path)
        assert "mode=ro" in database_urls(fake_db.path)[0]
        assert read_owner(fake_db.path).api_key == NEW_API_KEY
    finally:
        writer.close()


def test_a_regenerated_key_is_read_next_time(fake_db):
    fake_db.set_key(API_KEY)
    assert read_owner(fake_db.path).api_key == API_KEY
    fake_db.set_key(NEW_API_KEY)
    assert read_owner(fake_db.path).api_key == NEW_API_KEY


def test_no_key_and_no_user_are_answers(tmp_path, fake_db):
    with pytest.raises(OwnerError) as no_key:
        read_owner(fake_db.path)
    assert no_key.value.kind == "no_key"
    empty = FakeOpenAlgoDb(tmp_path / "other", with_user=False)
    try:
        with pytest.raises(OwnerError) as no_user:
            read_owner(empty.path)
        assert no_user.value.kind == "no_user"
    finally:
        empty.close()


def test_a_key_that_does_not_decrypt(fake_db):
    fake_db.set_key("", ciphertext="gAAAAA-not-a-fernet-token")
    with pytest.raises(OwnerError) as failed:
        read_owner(fake_db.path)
    assert failed.value.kind == "unreadable_key"


def test_a_missing_database_is_a_read_failure(tmp_path):
    with pytest.raises(OwnerError) as failed:
        read_owner(tmp_path / "nothing" / "openalgo.db")
    assert failed.value.kind == "database"


def test_a_failed_first_read_tries_the_other_way(fake_db, monkeypatch):
    fake_db.set_key(API_KEY)
    tried = []
    real = owner_module._read_with

    def first_fails(url):
        tried.append(url)
        if len(tried) == 1:
            from sqlalchemy.exc import OperationalError

            raise OperationalError("SELECT", {}, sqlite3.OperationalError("readonly database"))
        return real(url)

    monkeypatch.setattr(owner_module, "_read_with", first_fails)
    assert read_owner(fake_db.path).api_key == API_KEY
    assert ["immutable=1" in url for url in tried] == [True, False]


def test_prepare_takes_three_values_from_openalgos_env(tmp_path, monkeypatch):
    for name in ("API_KEY_PEPPER", "FERNET_SALT", "DATABASE_URL", "LOG_DIR", "LOG_TO_FILE"):
        monkeypatch.setenv(name, "placeholder")  # restored after the test
        monkeypatch.delenv(name)
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"API_KEY_PEPPER = '{'e' * 64}'\nFERNET_SALT = '{'ab' * 16}'\n"
        "DATABASE_URL = 'sqlite:///db/openalgo.db'\nBROKER_API_SECRET = 'fake-secret'\n"  # pragma: allowlist secret
    )
    db_path = prepare_openalgo_env(env_file, tmp_path)
    assert db_path == tmp_path / "db" / "openalgo.db"
    assert os.environ["API_KEY_PEPPER"] == "e" * 64 and os.environ["FERNET_SALT"] == "ab" * 16
    assert os.environ["DATABASE_URL"].endswith("openalgo.db?immutable=1&uri=true")
    assert not os.environ["LOG_DIR"].startswith(str(tmp_path))  # never OpenAlgo's log/
    assert os.environ.get("BROKER_API_SECRET") != "fake-secret"  # pragma: allowlist secret


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("DATABASE_URL = 'sqlite:///db/openalgo.db'\n", "API_KEY_PEPPER is missing"),
        (f"API_KEY_PEPPER = '{'e' * 64}'\nDATABASE_URL = 'postgresql://x/y'\n", "not SQLite"),
    ],
)
def test_prepare_refuses_what_cannot_work(tmp_path, monkeypatch, text, problem):
    monkeypatch.delenv("API_KEY_PEPPER", raising=False)
    monkeypatch.setenv("DATABASE_URL", "placeholder")
    monkeypatch.setenv("LOG_DIR", "placeholder")
    monkeypatch.setenv("LOG_TO_FILE", "placeholder")
    (tmp_path / ".env").write_text(text)
    with pytest.raises(ConfigError, match=problem):
        prepare_openalgo_env(tmp_path / ".env", tmp_path)
    with pytest.raises(ConfigError, match="cannot read"):
        prepare_openalgo_env(tmp_path / "missing.env", tmp_path)


def test_loading_openalgos_modules_leaves_dotenv_as_it_was():
    before = dotenv.load_dotenv
    load_openalgo_modules()
    assert dotenv.load_dotenv is before


def test_openalgos_sessions_are_left_as_they_were(fake_db):
    """The read borrows OpenAlgo's module sessions; anything else using them keeps its engine."""
    from database import auth_db, user_db

    fake_db.set_key(API_KEY)
    binds = [s.session_factory.kw.get("bind") for s in (auth_db.db_session, user_db.db_session)]
    read_owner(fake_db.path)
    with pytest.raises(OwnerError):
        read_owner(fake_db.path.parent / "missing.db")
    after = [s.session_factory.kw.get("bind") for s in (auth_db.db_session, user_db.db_session)]
    assert after == binds and binds[0] is auth_db.engine


def test_a_failed_read_leaves_no_openalgo_traceback(fake_db, monkeypatch, caplog):
    """A read cut across a write-back is tried again; OpenAlgo's ERROR traceback would alarm."""
    import logging

    from sqlalchemy.exc import OperationalError

    caplog.set_level(logging.DEBUG)
    fake_db.set_key("", ciphertext="gAAAAA-not-a-fernet-token")  # OpenAlgo logs a decrypt error
    with pytest.raises(OwnerError):
        read_owner(fake_db.path)
    real = owner_module._read_with
    calls = []

    def torn_then_fine(url):
        calls.append(url)
        if len(calls) == 1:
            raise OperationalError("SELECT", {}, sqlite3.DatabaseError("malformed"))
        return real(url)

    fake_db.set_key(API_KEY)
    monkeypatch.setattr(owner_module, "_read_with", torn_then_fine)
    assert read_owner(fake_db.path).api_key == API_KEY
    loud = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert loud == []
    assert "a read of openalgo.db did not complete" in caplog.text


def test_a_lock_free_read_during_a_write_is_done_again(fake_db, monkeypatch):
    fake_db.set_key(API_KEY)
    real = owner_module._read_with
    calls = []

    def write_meanwhile(url):
        calls.append(url)
        result = real(url)
        if len(calls) == 1:
            fake_db.set_key(NEW_API_KEY)  # OpenAlgo regenerates the key during the first read
        return result

    monkeypatch.setattr(owner_module, "_read_with", write_meanwhile)
    assert read_owner(fake_db.path).api_key == NEW_API_KEY
    assert len(calls) == 2 and "immutable=1" in calls[0]


_DOTENV_PROBE = """
import os, sys
import dotenv, dotenv.main
calls = []
def record(*args, **kwargs):
    calls.append(1)
    return False
dotenv.load_dotenv = dotenv.main.load_dotenv = record
real_exists = os.path.exists
# As on the server, where OpenAlgo's .env exists next to its code.
os.path.exists = lambda p: True if str(p).endswith(".env") else real_exists(p)
from sudarshan.alerts.owner import load_openalgo_modules
{call}
print("loads:", len(calls))
"""


@pytest.mark.parametrize(
    ("call", "loads"),
    [
        ("load_openalgo_modules()", 0),
        ("from database import auth_db, user_db", 1),  # the control: the probe can see a load
    ],
)
def test_openalgos_env_file_is_not_loaded_into_this_process(tmp_path, call, loads):
    import subprocess
    import sys
    from pathlib import Path

    repo = Path(__file__).resolve().parents[3]
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": str(repo),
        "API_KEY_PEPPER": "d" * 64,
        "DATABASE_URL": f"sqlite:///{tmp_path}/unused.db",
        "LOG_DIR": str(tmp_path / "log"),
    }
    done = subprocess.run(
        [sys.executable, "-c", _DOTENV_PROBE.format(call=call)], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=120,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    assert f"loads: {loads}" in done.stdout
