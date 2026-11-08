"""A slow Upstox must not cost the dashboard card its own tabs (M3.11, review MEDIUM-1).

The real Flask bridge (real httpx client and timeouts) in front of the real service, its ops and
store, and a real Upstox reader against a fake Upstox on loopback that answers after a delay. The
summary is a read the bridge waits 3 seconds for; the Upstox holdings read has 10 of its own and
its own op (12 seconds at the bridge). Synthetic token, no broker call.
"""

from __future__ import annotations

import time

import httpx
import pytest
from portfolio_bridge_app import csrf_headers, login_session, make_app, reset_users
from portfolio_fakes import FakeUpstox
from test_portfolio_bridge_e2e import PREFIX, RealService

import blueprints.sudarshan_portfolio as bp_module
from sudarshan.portfolio.core import kinds
from sudarshan.portfolio.upstox import UpstoxReader
from utils.thread_safe_cache import LockedTTLCache

TOKEN = "synthetic-token-0123456789abcdef"  # never a real token


@pytest.fixture
def rig(tmp_path, monkeypatch):
    reset_users()
    http = httpx.Client(trust_env=False)
    monkeypatch.setattr(bp_module, "get_httpx_client", lambda: http)
    monkeypatch.setattr(bp_module, "_loopback_warned", LockedTTLCache(maxsize=8, ttl=600))
    fake = FakeUpstox()
    fake.start()
    token = tmp_path / "token"
    token.write_text(TOKEN + "\n")
    with RealService(tmp_path) as service:
        service.svc.upstox = UpstoxReader(token, fake.url, clock=lambda: int(time.time() * 1000))
        monkeypatch.setenv("SUDARSHAN_PORTFOLIO_API_URL", service.url)
        client = make_app().test_client()
        login_session(client)
        made = client.post(
            PREFIX + "/tabs",
            headers=csrf_headers(client),
            json={"request_id": "t-1", "name": "Gold", "kind": "gold", "pinned": True},
        ).get_json()
        assert made["status"] == "success", made
        yield client, fake
        fake.delay = 0.0
        time.sleep(0.5)  # let a read that is still out finish before the loop stops
    fake.stop()
    http.close()


def test_control_a_prompt_upstox_gives_the_card_its_rows(rig):
    client, fake = rig
    body = client.get(PREFIX + "/summary").get_json()
    assert body["status"] == "success", body
    assert [e["tab"]["name"] for e in body["data"]["entries"]] == ["Upstox", "Gold"]
    upstox = body["data"]["entries"][0]["upstox"]
    assert upstox["state"] == "ok" and upstox["holdings"]


def test_a_slow_upstox_costs_the_card_one_line_and_never_its_tabs(rig):
    """Upstox's holdings API answering after 3.6 s: past the bridge's 3 s for a read, well inside
    the reader's own 10 s. The summary answers with the tabs and a row that is still reading; the
    phone then asks the Upstox op, which waits for the read, and gets the holdings."""
    client, fake = rig
    fake.delay = 3.6
    started = time.monotonic()
    body = client.get(PREFIX + "/summary").get_json()
    took = time.monotonic() - started
    assert body["status"] == "success", body
    assert took < kinds.TIMEOUTS_S["read"], took
    assert [e["tab"]["name"] for e in body["data"]["entries"]] == ["Upstox", "Gold"]
    upstox = body["data"]["entries"][0]["upstox"]
    assert upstox["state"] == "reading" and upstox["holdings"] == []
    assert "not answering" not in upstox["note"]
    filled = client.get(PREFIX + "/upstox?parts=holdings").get_json()
    assert filled["status"] == "success", filled
    assert filled["data"]["holdings"]["state"] == "ok" and filled["data"]["holdings"]["data"]
    assert fake.count("holdings") == 1  # one read served the summary's wait and the fill-in
