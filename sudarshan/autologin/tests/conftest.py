"""Fixtures for the auto-login tests: no network, no real secrets, throwaway databases.

OpenAlgo's modules read their settings when they are imported, so the environment is set here,
before any of them can be, and python-dotenv is disabled so an operator's .env is never read into
a test (the same guard as ``test/conftest.py``). Every value below is a fake.
"""

from __future__ import annotations

import os
import tempfile

import dotenv

_TMP = tempfile.mkdtemp(prefix="sudarshan-autologin-tests-")
os.environ.setdefault("API_KEY_PEPPER", "f" * 64)  # fake, test only
os.environ.setdefault("APP_KEY", "test-only-app-key-" + "0" * 32)
# Assigned, never defaulted: a test must not run against a real database.
DATABASE_URL = f"sqlite:///{_TMP}/openalgo-test.db"
os.environ["DATABASE_URL"] = DATABASE_URL
os.environ["LOG_DIR"] = f"{_TMP}/log"
os.environ["REDIRECT_URL"] = "http://127.0.0.1:5000/upstox/callback"
os.environ["SUDARSHAN_VAULT_DB"] = f"{_TMP}/vault-unused.db"
os.environ["SUDARSHAN_NSE_HOLIDAYS_FILE"] = f"{_TMP}/holidays-unused.txt"
os.environ["SUDARSHAN_NSE_EXTRA_DAYS_FILE"] = f"{_TMP}/extra-days-unused.txt"
os.environ["SUDARSHAN_AUTOLOGIN_TEST_LOG"] = f"{_TMP}/log/sudarshan_autologin.log"
# Never the server's /etc/sudarshan/autologin.env: a test that wants one writes its own.
os.environ["SUDARSHAN_AUTOLOGIN_ENV_FILE"] = f"{_TMP}/autologin-unused.env"

dotenv.load_dotenv = lambda *args, **kwargs: False
dotenv.main.load_dotenv = dotenv.load_dotenv

import pytest  # noqa: E402
from autologin_fakes import FAKE_API_KEY  # noqa: E402

from sudarshan.vault import Vault  # noqa: E402

_OWN_ENGINE = None


@pytest.fixture(autouse=True)
def _openalgo_tables_in_our_own_database():
    """Every test here on this directory's own database file, whatever the import order.

    OpenAlgo's database modules bind their engine when first imported. A run that also takes a
    path under test/ loads test/conftest.py first, which imports them on the repository suite's
    shared db/openalgo-test.db; that suite, and the threads and processes it leaves running,
    write there too. A store in the end-to-end tests then queued behind those writers past the
    worker's 20 s hand-off (it reported handoff_failed, not store_failed). So the tables are
    bound here for each test and put back after it; in a run of this directory alone they
    already are, and nothing changes.
    """
    global _OWN_ENGINE
    from database import auth_db, user_db
    from database.engine_factory import create_db_engine

    moved = []
    for module in (auth_db, user_db):
        if str(module.engine.url) == DATABASE_URL:
            continue
        if _OWN_ENGINE is None:
            _OWN_ENGINE = create_db_engine(DATABASE_URL)
        moved.append((module, module.engine))
        module.db_session.remove()
        module.db_session.configure(bind=_OWN_ENGINE)
        module.engine = _OWN_ENGINE
    try:
        yield
    finally:
        for module, engine in moved:
            module.db_session.remove()
            module.db_session.configure(bind=engine)
            module.engine = engine


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    # httpx honours proxy variables; every fake server lives on 127.0.0.1.
    for name in [key for key in os.environ if key.lower().endswith("_proxy")]:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def pepper() -> str:
    """The process's (fake) pepper: OpenAlgo-side code reads it from the environment."""
    return os.environ["API_KEY_PEPPER"]


@pytest.fixture
def vault_path(tmp_path, monkeypatch):
    path = tmp_path / "db" / "sudarshan_vault.db"
    monkeypatch.setenv("SUDARSHAN_VAULT_DB", str(path))
    return path


@pytest.fixture
def vault(vault_path, pepper) -> Vault:
    return Vault(vault_path, pepper=pepper)


@pytest.fixture
def holidays_file(tmp_path, monkeypatch):
    path = tmp_path / "nse_holidays.txt"
    path.write_text("# fake holiday list\n2026-11-09\n")
    monkeypatch.setenv("SUDARSHAN_NSE_HOLIDAYS_FILE", str(path))
    return path


@pytest.fixture
def openalgo_env_file(tmp_path, pepper):
    """A fake OpenAlgo .env in its own spaced, quoted style."""
    path = tmp_path / ".env"
    path.write_text(
        f"API_KEY_PEPPER = '{pepper}'\nBROKER_API_KEY = '{FAKE_API_KEY}'\n"
        "REDIRECT_URL = 'http://127.0.0.1:5000/upstox/callback'\n"
    )
    return path
