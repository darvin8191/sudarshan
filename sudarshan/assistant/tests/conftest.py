"""Fixtures for the assistant's tests: a throwaway risk database and no operator .env.

OpenAlgo's modules read their settings when they are imported, so the environment is made safe
here first (the guard tests' own guards).
"""

from __future__ import annotations

import os
import tempfile

import dotenv
import pytest

_TMP = tempfile.mkdtemp(prefix="sudarshan-assistant-tests-")
_TEMP_ROOT = tempfile.gettempdir()


def _isolate(name: str, value: str, isolated_prefix: str) -> None:
    if not os.environ.get(name, "").startswith(isolated_prefix):
        os.environ[name] = value


os.environ.setdefault("API_KEY_PEPPER", "a" * 64)  # fake, test only
os.environ.setdefault("APP_KEY", "test-only-app-key-" + "5" * 32)  # fake, test only
for _name in ("DATABASE_URL", "SANDBOX_DATABASE_URL", "LOGS_DATABASE_URL", "LATENCY_DATABASE_URL"):
    _isolate(_name, f"sqlite:///{_TMP}/{_name.lower()}.db", f"sqlite:///{_TEMP_ROOT}")
_isolate("LOG_DIR", f"{_TMP}/log", _TEMP_ROOT)
if not os.environ.get("SUDARSHAN_RISK_DB", "").startswith(_TEMP_ROOT):
    os.environ["SUDARSHAN_RISK_DB"] = f"{_TMP}/sudarshan_risk_default.db"

dotenv.load_dotenv = lambda *args, **kwargs: False
dotenv.main.load_dotenv = dotenv.load_dotenv


@pytest.fixture
def risk_store(tmp_path, monkeypatch):
    """A risk store of this test's own, in place of the app's, and an empty cutoff cache."""
    from sudarshan.assistant import cutoff
    from sudarshan.guard import store

    fresh = store.RiskStore(tmp_path / "risk.db")
    monkeypatch.setattr(store, "STORE", fresh)
    cutoff._cache.clear()
    yield fresh
    fresh.dispose()
    cutoff._cache.clear()
