"""Fixtures for the sudarshan-feed tests: no network, no real token, small timings."""

from __future__ import annotations

import os

import pytest
from feed_fakes import TOKEN

from sudarshan.feed import service


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    # httpx and websockets honour proxy variables; the fakes live on 127.0.0.1.
    for name in [k for k in os.environ if k.lower().endswith("_proxy")]:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def feed_env(tmp_path):
    """Environment for a FeedService talking to a FakeUpstox (``fake``)."""

    def make(fake=None, **overrides) -> dict[str, str]:
        (tmp_path / "token").write_text(TOKEN + "\n")
        (tmp_path / "keys.txt").write_text(
            "ltpc NSE_INDEX|Nifty 50\nltpc NSE_INDEX|Nifty Bank\nltpc NSE_INDEX|India VIX\n"
            "full NSE_EQ|INE002A01018  # RELIANCE\n"
        )
        env = {
            "SUDARSHAN_UPSTOX_TOKEN_FILE": str(tmp_path / "token"),
            "SUDARSHAN_FEED_KEYS": str(tmp_path / "keys.txt"),
            "SUDARSHAN_FEED_STATE_DIR": str(tmp_path / "state"),
            "SUDARSHAN_NSE_HOLIDAYS_FILE": str(tmp_path / "nse_holidays.txt"),
            "SUDARSHAN_NSE_EXTRA_DAYS_FILE": str(tmp_path / "nse_extra_trading_days.txt"),
            "SUDARSHAN_UPSTOX_API_BASE": fake.api_base if fake else "https://api.upstox.com",
            "SUDARSHAN_FEED_HTTP_PORT": "0",
            "SUDARSHAN_FEED_ZMQ_PORT": "0",
            "SUDARSHAN_FEED_ALWAYS_ON": "1",
            "SUDARSHAN_DEPLOYED_TAG_FILE": str(tmp_path / "deployed_tag"),  # never the machine's
        }
        return env | overrides

    return make


@pytest.fixture
def fast(monkeypatch):
    """Shrink the service timings so reconnect paths run in milliseconds."""
    timings = {
        "BACKOFF_BASE_S": 0.05, "BACKOFF_CAP_S": 0.2, "REFUSED_FLOOR_S": 0.3,
        "TOKEN_REJECTED_S": (30.0, 30.0, 30.0), "TOKEN_POLL_S": 0.05, "WATCH_EVERY_S": 0.05,
        "SUB_BATCH": 2, "SUB_PAUSE_S": 0.0,
    }  # fmt: skip
    for name, value in timings.items():
        monkeypatch.setattr(service, name, value)
