"""Fixtures for the order-alert tests: no network, fake keys, throw-away databases.

OpenAlgo's database modules read their settings when they are imported, so the environment is
set here before any of them can be, with setdefault: the auto-login tests' conftest sets the same
names, and one pytest process must give both suites the same values. python-dotenv is disabled so
an operator's .env is never read into a test. Every value below is a fake.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile

import dotenv

_TMP = tempfile.mkdtemp(prefix="sudarshan-alerts-tests-")
atexit.register(shutil.rmtree, _TMP, ignore_errors=True)
os.environ.setdefault("API_KEY_PEPPER", "f" * 64)  # fake, test only
os.environ.setdefault("APP_KEY", "test-only-app-key-" + "0" * 32)
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP}/openalgo-unused.db")
os.environ.setdefault("LOG_DIR", f"{_TMP}/log")

dotenv.load_dotenv = lambda *args, **kwargs: False
dotenv.main.load_dotenv = dotenv.load_dotenv

import pytest  # noqa: E402
from alerts_fakes import API_KEY, OWNER  # noqa: E402

from sudarshan.alerts import outbox, service  # noqa: E402
from sudarshan.alerts.config import Settings  # noqa: E402
from sudarshan.alerts.owner import Owner  # noqa: E402


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    # httpx and websockets honour proxy variables; every fake lives on 127.0.0.1.
    for name in [key for key in os.environ if key.lower().endswith("_proxy")]:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def settings_for(tmp_path):
    """Settings for a service talking to fakes; ``**overrides`` are environment values."""

    def make(proxy=None, notify=None, **overrides) -> Settings:
        env = {
            "SUDARSHAN_ALERTS_STATE_DIR": str(tmp_path / "state"),
            "SUDARSHAN_NSE_HOLIDAYS_FILE": str(tmp_path / "nse_holidays.txt"),
            "SUDARSHAN_NSE_EXTRA_DAYS_FILE": str(tmp_path / "nse_extra_trading_days.txt"),
            "SUDARSHAN_OPENALGO_WS_URL": proxy.url if proxy else "ws://127.0.0.1:9",
            "SUDARSHAN_OPENALGO_URL": notify.url if notify else "http://127.0.0.1:9",
            "SUDARSHAN_ALERTS_ALWAYS_ON": "1",
            "SUDARSHAN_NOTICE_SPOOLS": str(tmp_path / "notice-spool.jsonl"),  # never the machine's
        }
        return Settings.from_env(env | overrides)

    return make


@pytest.fixture
def owner() -> Owner:
    return Owner(OWNER, API_KEY)


@pytest.fixture
def fast(monkeypatch):
    """Shrink the service and outbox timings so reconnects and paces run in milliseconds."""
    for name, value in {
        "BACKOFF_BASE_S": 0.05, "BACKOFF_CAP_S": 0.2, "KEY_REFUSED_S": 0.1,
        "NOT_SUBSCRIBED_S": 0.1, "NO_OWNER_S": 0.1, "CLOSED_POLL_S": 0.1, "WATCH_EVERY_S": 0.05,
        "TICK_S": 0.02, "AUTH_TIMEOUT_S": 2.0, "ACK_TIMEOUT_S": 2.0,
    }.items():  # fmt: skip
        monkeypatch.setattr(service, name, value)
    for name, value in {
        "COALESCE_S": 0.05, "MIN_GAP_S": 0.05, "RETRY_S": (0.05, 0.05, 0.1),
    }.items():  # fmt: skip
        monkeypatch.setattr(outbox, name, value)
