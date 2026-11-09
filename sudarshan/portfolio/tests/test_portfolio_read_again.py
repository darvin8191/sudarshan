"""Read again (M7.6, M7.7) on its way from the phone to the service: ``force`` has to survive the
phone's query string, the bridge and the sealed call, or the button is a plain open.

The fix round's tests missed it because each hop was tested alone: the phone's test mocked the
call, and the service's test called the op directly. This file drives the real Flask bridge into the
real service, ReportReader and UpstoxReader against the FakeUpstox on loopback. Synthetic token; no
broker call."""

from __future__ import annotations

import time

import httpx
import pytest
from portfolio_bridge_app import login_session, make_app, reset_users
from portfolio_fakes import FakeUpstox
from test_portfolio_bridge_e2e import PREFIX, RealService
from test_portfolio_reports_cache import WIDE, page, trow

import blueprints.sudarshan_portfolio as bp_module
from sudarshan.portfolio import reports
from sudarshan.portfolio.budget import Budget
from sudarshan.portfolio.core import kinds
from sudarshan.portfolio.upstox import UpstoxReader
from utils.thread_safe_cache import LockedTTLCache

TOKEN = "synthetic-token-0123456789abcdef"  # never a real token
FY = 2025  # FY 2025-26: past and settled today, so a complete read is final


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    reset_users()
    http = httpx.Client(trust_env=False)
    monkeypatch.setattr(bp_module, "get_httpx_client", lambda: http)
    monkeypatch.setattr(bp_module, "_loopback_warned", LockedTTLCache(maxsize=8, ttl=600))
    upstox = FakeUpstox()
    upstox.start()
    token = tmp_path / "token"
    token.write_text(TOKEN + "\n")
    with RealService(tmp_path) as service:
        reader = UpstoxReader(token, upstox.url, clock=lambda: int(time.time() * 1000))
        reader.budget = Budget(limits=WIDE)
        service.svc.upstox = reader
        monkeypatch.setenv("SUDARSHAN_PORTFOLIO_API_URL", service.url)
        client = make_app().test_client()
        login_session(client)
        yield client, upstox, reader
    upstox.stop()
    http.close()


def get(client, kind, **query):
    return client.get(f"{PREFIX}/reports/{kind}", query_string={"fy": FY, "segment": "EQ", **query})


def test_both_report_ops_take_force_as_a_flag():
    for name in ("report_trades", "report_pnl"):
        spec = next(op for op in kinds.OPS if op.name == name)
        assert ("force", "bool") in spec.query, name


def test_read_again_reaches_upstox_through_the_bridge(bridge):
    client, upstox, _ = bridge
    upstox.bodies["trades"] = page([trow("A", "2026-03-30"), trow("B", "2026-03-31")], 1, 2)
    first = get(client, "trades").get_json()
    assert first["data"]["state"] == "ok" and len(first["data"]["rows"]) == 2, first
    upstox.bodies["trades"] = page(
        [trow("A", "2026-03-30"), trow("B", "2026-03-31"), trow("C", "2026-03-31")], 1, 3
    )
    asked = upstox.count("trades")
    plain = get(client, "trades").get_json()  # a plain open is the cache, final
    assert upstox.count("trades") == asked and len(plain["data"]["rows"]) == 2
    again = get(client, "trades", force="true").get_json()
    assert upstox.count("trades") > asked, "force never reached the service"
    assert len(again["data"]["rows"]) == 3
    assert again["data"]["read_at"] != first["data"]["read_at"]


def test_read_again_on_the_pnl_reaches_upstox_through_the_bridge(bridge):
    client, upstox, _ = bridge
    first = get(client, "pnl").get_json()
    assert first["status"] == "success", first
    asked = upstox.count("pnl")
    get(client, "pnl")
    assert upstox.count("pnl") == asked  # final, so the cache
    get(client, "pnl", force="true")
    assert upstox.count("pnl") > asked, "force never reached the service"


def test_force_false_is_a_plain_open(bridge):
    client, upstox, _ = bridge
    upstox.bodies["trades"] = page([trow("A", "2026-03-30")], 1, 1)
    get(client, "trades")
    asked = upstox.count("trades")
    get(client, "trades", force="false")
    assert upstox.count("trades") == asked


@pytest.mark.parametrize("junk", ["yes", "1", "0", "TRUE1", "{}", "null"])
def test_force_that_is_not_a_boolean_is_refused_at_the_bridge(bridge, junk):
    client, upstox, _ = bridge
    answer = get(client, "trades", force=junk)
    assert answer.status_code == 400, junk
    assert upstox.count("trades") == 0


def test_a_forced_read_still_obeys_the_budget_and_keeps_the_old_figures(bridge):
    client, upstox, reader = bridge
    upstox.bodies["trades"] = page([trow("A", "2026-03-30")], 1, 1)
    first = get(client, "trades").get_json()
    assert first["data"]["state"] == "ok"
    spent = Budget(limits=((100, 1.0), (1, 60.0), (200, 1800.0)))
    assert spent.take("trades")  # the minute is used up
    reader.budget = spent
    asked = upstox.count("trades")
    upstox.bodies["trades"] = page([trow("Z", "2026-03-31")], 1, 1)
    again = get(client, "trades", force="true").get_json()
    assert upstox.count("trades") == asked, "a forced read went past the minute budget"
    assert len(again["data"]["rows"]) == 1 and again["data"]["rows"][0]["trade_id"] == "A"
    assert again["data"]["note"] == reports.WORDS["held_back"]
