"""Fixtures for the automation's tests (sudarshan/automation).

OpenAlgo's modules read their settings when they are imported, so the environment is made safe
here, before any of them can be, and python-dotenv is disabled so an operator's .env is never
read into a test. Every test gets a throwaway automation database, and automation starts off, as
it ships.
"""

from __future__ import annotations

import os
import tempfile

import dotenv
import pytest

_TMP = tempfile.mkdtemp(prefix="sudarshan-automation-tests-")
_TEMP_ROOT = tempfile.gettempdir()


def _isolate(name: str, value: str, isolated_prefix: str) -> None:
    """Point `name` at a throwaway location unless another test package already has."""
    if not os.environ.get(name, "").startswith(isolated_prefix):
        os.environ[name] = value


os.environ.setdefault("API_KEY_PEPPER", "e" * 64)  # fake, test only
os.environ.setdefault("APP_KEY", "test-only-app-key-" + "3" * 32)
for _name in ("DATABASE_URL", "SANDBOX_DATABASE_URL", "LOGS_DATABASE_URL", "LATENCY_DATABASE_URL"):
    _isolate(_name, f"sqlite:///{_TMP}/{_name.lower()}.db", f"sqlite:///{_TEMP_ROOT}")
_isolate("LOG_DIR", f"{_TMP}/log", _TEMP_ROOT)
os.environ["SUDARSHAN_AUTOMATION_DB"] = f"{_TMP}/sudarshan_automation_default.db"
_isolate("SUDARSHAN_RISK_DB", f"{_TMP}/sudarshan_risk_default.db", _TEMP_ROOT)

dotenv.load_dotenv = lambda *args, **kwargs: False
dotenv.main.load_dotenv = dotenv.load_dotenv


@pytest.fixture(autouse=True)
def _automation_off_unless_a_test_says(monkeypatch):
    """Automation ships off; a test that needs it on says so."""
    monkeypatch.delenv("SUDARSHAN_AUTOMATION", raising=False)


@pytest.fixture
def store(tmp_path):
    from sudarshan.automation.store import AutomationStore

    made = AutomationStore(tmp_path / "automation.db")
    yield made
    made.dispose()


FUTURE_FORM = {"name": "ORB", "kind": "future", "underlying": "NIFTY", "product": "MIS", "lots": 1}


@pytest.fixture
def form():
    return dict(FUTURE_FORM)
