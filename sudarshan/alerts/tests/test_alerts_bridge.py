"""The Alerts page's bridge in OpenAlgo (M10.1): owner routes, CSRF, the master contract, and the
sealed forward to the service's real local API on 127.0.0.1."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import httpx
import pytest
from bridge_app import (
    VIEWER,
    Desk,
    Service,
    csrf_headers,
    login_session,
    make_app,
    reset_users,
)

import blueprints.sudarshan_alerts as bp_module
from database.token_db_enhanced import SymbolData
from sudarshan.alerts.market.api import ApiError
from utils.thread_safe_cache import LockedTTLCache

REPO = Path(__file__).resolve().parents[3]
JSON = {"Accept": "application/json"}
SBIN = SymbolData(symbol="SBIN", brsymbol="SBIN-EQ", name="STATE BANK OF INDIA", exchange="NSE",
                  brexchange="NSE_EQ", token="NSE_EQ|INE062A01020", expiry="", strike=-1.0,
                  lotsize=1, instrumenttype="EQ", tick_size=0.05)  # fmt: skip
OPTION = SymbolData(symbol="NIFTY25NOV2625900CE", brsymbol="NIFTY 25900 CE 25 NOV 26",
                    name="NIFTY", exchange="NFO", brexchange="NSE_FO", token="NSE_FO|45450",
                    expiry="25-NOV-26", strike=25900.0, lotsize=65, instrumenttype="CE",
                    tick_size=0.05)  # fmt: skip
MASTER = {("SBIN", "NSE"): SBIN, ("NIFTY25NOV2625900CE", "NFO"): OPTION}
DRAFT = {"symbol": "SBIN", "exchange": "NSE", "kind": "price",
         "params": {"op": "crosses_above", "level": "100"}}  # fmt: skip
ROUTES = [
    ("get", "/sudarshan/alerts/api/state", None),
    ("post", "/sudarshan/alerts/api/alerts", {"alert": DRAFT}),
    ("patch", "/sudarshan/alerts/api/alerts/1", {"version": 1, "action": "pause"}),
    ("delete", "/sudarshan/alerts/api/alerts/1", None),
    ("get", "/sudarshan/alerts/api/events", None),
    ("get", "/sudarshan/alerts/api/events/since?after=0", None),
    ("get", "/sudarshan/alerts/api/prefs", None),
    ("put", "/sudarshan/alerts/api/prefs", {"telegram": True, "app": False}),
    ("post", "/sudarshan/alerts/api/test", None),
]
WRITES = [route for route in ROUTES if route[0] != "get"]


@pytest.fixture
def desk():
    return Desk()


@pytest.fixture
def service(desk, monkeypatch):
    with Service(desk, os.environ["API_KEY_PEPPER"]) as running:
        monkeypatch.setenv("SUDARSHAN_ALERTS_API_URL", running.url)
        yield running


@pytest.fixture
def bridge(monkeypatch):
    reset_users()
    monkeypatch.setenv("REDIRECT_URL", "http://127.0.0.1:5000/upstox/callback")
    http = httpx.Client(trust_env=False)
    monkeypatch.setattr(bp_module, "get_httpx_client", lambda: http)
    monkeypatch.setattr(bp_module, "get_symbol_info", lambda s, e: MASTER.get((s, e)))
    monkeypatch.setattr(bp_module, "_warned", LockedTTLCache(maxsize=8, ttl=600))
    app = make_app()
    yield app.test_client()
    http.close()


def call(client, method, path, body=None, headers=None):
    return getattr(client, method)(path, json=body, headers={**JSON, **(headers or {})})


def as_owner(client) -> dict:
    login_session(client)
    return csrf_headers(client)


# -- who may call ---------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_every_route_needs_a_session(bridge, service, desk, method, path, body):
    answer = call(bridge, method, path, body, csrf_headers(bridge))
    assert answer.status_code == 401
    assert answer.get_json()["error"] == "session_expired"
    assert desk.calls == []


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_a_viewer_session_is_refused_on_every_route_reading_included(
    bridge, service, desk, method, path, body
):
    """The family layer rule: a viewer gets 403 on every write, and here on every read too."""
    login_session(bridge, VIEWER)
    answer = call(bridge, method, path, body, csrf_headers(bridge))
    assert answer.status_code == 403
    assert answer.get_json() == {
        "status": "error",
        "code": "owner_only",
        "message": "Only the account owner can manage market alerts.",
    }
    assert desk.calls == []


@pytest.mark.parametrize(("method", "path", "body"), WRITES)
def test_every_change_needs_its_csrf_token(bridge, service, desk, method, path, body):
    login_session(bridge)
    assert call(bridge, method, path, body).status_code == 400
    assert desk.calls == []


# -- what is forwarded ----------------------------------------------------------------------


def test_a_new_alert_takes_its_instrument_from_the_master_never_from_the_page(
    bridge, service, desk
):
    desk.answers["create"] = {"id": 7}
    headers = as_owner(bridge)
    draft = DRAFT | {"instrument_key": "NSE_EQ|FORGED", "tick_size": "0.01", "expiry": "2030-01-01",
                     "note": "book half", "unknown": "dropped"}  # fmt: skip
    answer = call(bridge, "post", "/sudarshan/alerts/api/alerts", {"alert": draft}, headers)
    assert answer.status_code == 200 and answer.get_json() == {"status": "success",
                                                               "data": {"id": 7}}  # fmt: skip
    [(op, message)] = desk.calls
    assert op == "create" and message["principal"] == "owner"
    assert message["alert"] == {
        "symbol": "SBIN", "exchange": "NSE", "kind": "price",
        "params": {"op": "crosses_above", "level": "100"}, "note": "book half",
        "instrument_key": "NSE_EQ|INE062A01020", "tick_size": "0.05", "expiry": None,
    }  # fmt: skip
    option = {"symbol": "NIFTY25NOV2625900CE", "exchange": "NFO", "kind": "price",
              "params": {"op": "crosses_above", "level": "120"}}  # fmt: skip
    call(bridge, "post", "/sudarshan/alerts/api/alerts", {"alert": option}, headers)
    assert desk.calls[1][1]["alert"]["expiry"] == "2026-11-25"
    assert desk.calls[1][1]["alert"]["instrument_key"] == "NSE_FO|45450"


def test_an_instrument_the_master_does_not_hold_is_refused_here(bridge, service, desk):
    headers = as_owner(bridge)
    for draft in (
        DRAFT | {"symbol": "NOSUCH"},
        DRAFT | {"symbol": 5},
        DRAFT | {"symbol": "x" * 61},
    ):
        answer = call(bridge, "post", "/sudarshan/alerts/api/alerts", {"alert": draft}, headers)
        assert answer.status_code == 400
        assert answer.get_json() == {
            "status": "error",
            "code": "instrument",
            "field": "instrument",
            "message": "Pick the instrument again from the search.",
        }
    junk = call(bridge, "post", "/sudarshan/alerts/api/alerts", {"alert": "SBIN"}, headers)
    assert junk.status_code == 400 and junk.get_json()["code"] == "draft"
    assert desk.calls == []


def test_alerts_are_set_with_an_upstox_login_only(bridge, service, desk, monkeypatch):
    monkeypatch.setenv("REDIRECT_URL", "http://127.0.0.1:5000/zerodha/callback")
    answer = call(
        bridge, "post", "/sudarshan/alerts/api/alerts", {"alert": DRAFT}, as_owner(bridge)
    )
    assert answer.status_code == 409
    assert answer.get_json()["message"] == "Market alerts work with an Upstox login only."
    assert desk.calls == []


def test_changes_queries_settings_and_the_test_reach_the_service_as_sent(bridge, service, desk):
    headers = as_owner(bridge)
    call(bridge, "patch", "/sudarshan/alerts/api/alerts/3", {"version": 2, "action": "pause"},
         headers)  # fmt: skip
    call(bridge, "patch", "/sudarshan/alerts/api/alerts/3",
         {"version": 3, "action": "edit", "alert": DRAFT | {"ends": None}}, headers)  # fmt: skip
    call(bridge, "delete", "/sudarshan/alerts/api/alerts/3", None, headers)
    call(bridge, "get", "/sudarshan/alerts/api/events?before=9&limit=20&alert=3")
    call(bridge, "get", "/sudarshan/alerts/api/events?before=x")
    call(bridge, "get", "/sudarshan/alerts/api/events/since?after=7")
    call(bridge, "get", "/sudarshan/alerts/api/prefs")
    call(bridge, "put", "/sudarshan/alerts/api/prefs", {"telegram": False, "app": True}, headers)
    call(bridge, "post", "/sudarshan/alerts/api/test", None, headers)
    seen = [(op, {k: v for k, v in message.items() if k not in ("op", "nonce", "principal")})
            for op, message in desk.calls]  # fmt: skip
    assert seen[0] == ("update", {"id": 3, "version": 2, "action": "pause"})
    assert seen[1][0] == "update" and seen[1][1]["alert"]["instrument_key"] == SBIN.token
    assert seen[1][1]["alert"]["ends"] is None
    assert seen[2:] == [
        ("delete", {"id": 3}),
        ("events", {"before": 9, "limit": 20, "alert": 3}),
        ("events", {"before": None, "limit": None, "alert": None}),
        ("events_since", {"after": 7, "limit": None}),
        ("prefs_get", {}),
        ("prefs_set", {"telegram": False, "app": True}),
        ("test", {}),
    ]


def test_the_state_learns_on_and_off_from_the_service_s_own_health(bridge, service, desk):
    """Cross-design R3: no second switch here; switched off, no sealed op is even sent."""
    login_session(bridge)
    desk.on = False
    off = call(bridge, "get", "/sudarshan/alerts/api/state").get_json()["data"]
    assert off["enabled"] is False and off["alerts"] == []
    assert off["engine"] == {
        "code": "off",
        "line": "Switched off on the server",
        "real_time": False,
    }
    assert desk.calls == []
    desk.on, desk.answers["state"] = True, {"enabled": True, "alerts": []}
    on = call(bridge, "get", "/sudarshan/alerts/api/state").get_json()
    assert on == {"status": "success", "data": {"enabled": True, "alerts": []}}
    assert [op for op, _ in desk.calls] == ["state"]


def test_the_service_s_refusals_pass_through_in_its_words(bridge, service, desk):
    desk.answers["update"] = ApiError(409, "conflict", "This alert was changed on another screen. "
                                      "Open it again, then try again.")  # fmt: skip
    answer = call(bridge, "patch", "/sudarshan/alerts/api/alerts/1",
                  {"version": 1, "action": "pause"}, as_owner(bridge))  # fmt: skip
    assert answer.status_code == 409
    assert answer.get_json() == {"status": "error", "code": "conflict", "message": (
        "This alert was changed on another screen. Open it again, then try again.")}  # fmt: skip


# -- when the service cannot answer ---------------------------------------------------------


def test_nobody_listening_is_said_at_once(bridge, monkeypatch):
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setenv("SUDARSHAN_ALERTS_API_URL", f"http://127.0.0.1:{port}")
    login_session(bridge)
    started = time.monotonic()
    answer = call(bridge, "get", "/sudarshan/alerts/api/state")
    assert answer.status_code == 503 and time.monotonic() - started < 1
    assert answer.get_json()["message"] == (
        "The alert service is not answering. Your alerts are kept; checking resumes when it is "
        "back."
    )


def test_a_slow_service_is_cut_at_its_timeout_and_said_in_words(bridge, service, desk, monkeypatch):
    import asyncio

    async def slow(message):
        await asyncio.sleep(3)
        return {}

    desk.answers["prefs_get"] = slow
    monkeypatch.setitem(bp_module.TIMEOUTS, "prefs_get", 0.5)
    login_session(bridge)
    started = time.monotonic()
    answer = call(bridge, "get", "/sudarshan/alerts/api/prefs")
    assert answer.status_code == 503 and answer.get_json()["code"] == "not_answering"
    assert time.monotonic() - started < 2


def test_a_seal_the_service_cannot_open_is_said_with_what_to_do(bridge, desk, monkeypatch):
    with Service(desk, "q" * 48) as other:  # another pepper than OpenAlgo's
        monkeypatch.setenv("SUDARSHAN_ALERTS_API_URL", other.url)
        login_session(bridge)
        answer = call(bridge, "get", "/sudarshan/alerts/api/prefs")
    assert answer.status_code == 503
    assert answer.get_json()["message"].endswith("restart sudarshan-alerts.")
    assert desk.calls == []


def test_too_many_requests_are_said_in_words(service, desk, monkeypatch):
    reset_users()
    http = httpx.Client(trust_env=False)
    monkeypatch.setattr(bp_module, "get_httpx_client", lambda: http)
    try:
        client = make_app(rate_limits=True).test_client()
        headers = as_owner(client)
        answers = [call(client, "post", "/sudarshan/alerts/api/test", None, headers)
                   for _ in range(7)]  # fmt: skip
    finally:
        http.close()
    assert [a.status_code for a in answers] == [200] * 6 + [429]
    assert answers[-1].get_json()["message"] == "Too many requests. Wait a minute, then try again."


# -- registration ---------------------------------------------------------------------------


def test_the_blueprint_is_registered_once_with_its_marked_lines():
    source = (REPO / "app.py").read_text(encoding="utf-8")
    assert source.count("app.register_blueprint(sudarshan_alerts_bp)") == 1
    assert "from blueprints.sudarshan_alerts import sudarshan_alerts_bp  # SUDARSHAN M10" in source
    autologin = source.index("app.register_blueprint(sudarshan_autologin_bp)")
    assert source.index("app.register_blueprint(sudarshan_alerts_bp)") > autologin  # X R2


def test_every_route_is_the_contract_s(bridge):
    rules = {(rule.rule, method) for rule in bridge.application.url_map.iter_rules()
             for method in rule.methods - {"HEAD", "OPTIONS"}
             if rule.rule.startswith("/sudarshan/alerts")}  # fmt: skip
    expected = {(re.sub(r"/1$", "/<int:alert_id>", path.split("?")[0]), method.upper())
                for method, path, _ in ROUTES}  # fmt: skip
    assert rules == expected | {("/sudarshan/alerts/internal/push", "POST")}  # the service's


def test_the_bridge_holds_no_loop_thread_or_scheduler():
    """sudarshan.md: nothing of the kind is added inside the OpenAlgo app."""
    source = (REPO / "blueprints" / "sudarshan_alerts.py").read_text(encoding="utf-8")
    for banned in ("asyncio", "import threading", "Thread(", "ThreadPoolExecutor", "APScheduler",
                   "BackgroundScheduler", "submit_to_hub("):  # fmt: skip
        assert banned not in source
