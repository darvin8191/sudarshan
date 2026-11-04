"""Gate review M7: every read the gate makes is cut off after a few seconds, not the shared
client's 120: under the kill switch an exit waits for its read, and Exit all's sends are timed."""

from __future__ import annotations

import httpx
import pytest

from sudarshan.guard import readers


class _Client:
    def __init__(self):
        self.timeouts = []

    def get(self, url, **kwargs):
        self.timeouts.append(kwargs.get("timeout"))
        raise httpx.ReadTimeout("slow")


@pytest.fixture
def client(monkeypatch):
    seen = _Client()
    for module in ("broker.upstox.api.order_api", "broker.upstox.api.gtt_api",
                   "broker.upstox.api.data"):  # fmt: skip
        monkeypatch.setattr(f"{module}.get_httpx_client", lambda: seen)
    for module in ("broker.upstox.api.order_api", "broker.upstox.api.gtt_api",
                   "broker.upstox.api.data"):  # fmt: skip
        monkeypatch.setattr(f"{module}.apply_rate_limit", lambda *a, **k: None, raising=False)
    monkeypatch.setenv("BROKER_API_KEY", "key")
    from broker.upstox.api.data import BrokerData

    monkeypatch.setattr(
        BrokerData, "_get_instrument_key", lambda self, symbol, exchange: "NSE_FO|1"
    )
    return seen


@pytest.mark.parametrize(
    "read",
    [
        lambda: readers.live_rows("limit-token-rows"),
        lambda: readers.live_working_order("limit-token", "1"),
        lambda: readers.live_order_statuses("limit-token"),
        lambda: readers.live_gtt("limit-token", "1"),
        lambda: readers.quote("live", "limit-token", "NIFTY28OCT2625900CE", "NFO"),
    ],
)
def test_every_read_of_the_gate_is_cut_off_after_a_few_seconds(client, read):
    with pytest.raises(readers.Unreadable):
        read()
    assert client.timeouts, "the read never reached the client"
    assert all(timeout == readers.READ_LIMIT_S for timeout in client.timeouts), client.timeouts
    assert readers.READ_LIMIT_S <= 5


def test_a_shorter_limit_already_set_stays(client):
    from broker.upstox.api import order_api

    token = order_api._READ_LIMIT.set(2.0)
    try:
        with readers._limited():
            assert order_api._READ_LIMIT.get() == 2.0
    finally:
        order_api._READ_LIMIT.reset(token)
    assert order_api._READ_LIMIT.get() is None
