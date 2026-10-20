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
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/openalgo-test.db"
os.environ["LOG_DIR"] = f"{_TMP}/log"
os.environ["REDIRECT_URL"] = "http://127.0.0.1:5000/upstox/callback"
os.environ["SUDARSHAN_VAULT_DB"] = f"{_TMP}/vault-unused.db"
os.environ["SUDARSHAN_NSE_HOLIDAYS_FILE"] = f"{_TMP}/holidays-unused.txt"
os.environ["SUDARSHAN_AUTOLOGIN_TEST_LOG"] = f"{_TMP}/log/sudarshan_autologin.log"

dotenv.load_dotenv = lambda *args, **kwargs: False
dotenv.main.load_dotenv = dotenv.load_dotenv

import pytest  # noqa: E402
from autologin_fakes import FAKE_API_KEY  # noqa: E402

from sudarshan.vault import Vault  # noqa: E402


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
