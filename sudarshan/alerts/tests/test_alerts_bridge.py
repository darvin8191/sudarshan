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
GROUP = {"ref": "list:7", "name": "Swing", "kind": "move",
         "params": {"direction": "up", "percent": "2", "base": "close"}}
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
    ("post", "/sudarshan/alerts/api/groups", {"group": GROUP}),
    ("patch", "/sudarshan/alerts/api/groups/1", {"version": 1, "action": "pause"}),
    ("delete", "/sudarshan/alerts/api/groups/1", None),
    ("post", "/sudarshan/alerts/api/groups/sync", {"ref": "list:7"}),
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
    monkeypatch.setattr(bp_module, "_loopback", lambda: http)
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


def test_the_list_alert_ops_reach_the_service_as_sent_and_the_page_sets_nothing_else(
    bridge, service, desk
):
    headers = as_owner(bridge)
    draft = GROUP | {"delivery": "each", "digest_min": 5, "members": ["FORGED"],
                     "instrument_key": "NSE_EQ|FORGED"}  # fmt: skip
    call(bridge, "post", "/sudarshan/alerts/api/groups", {"group": draft}, headers)
    call(bridge, "patch", "/sudarshan/alerts/api/groups/4",
         {"version": 2, "action": "edit", "group": GROUP | {"name": "Swing 3"}}, headers)
    call(bridge, "patch", "/sudarshan/alerts/api/groups/4", {"version": 3, "action": "pause"}, headers)
    call(bridge, "patch", "/sudarshan/alerts/api/groups/4", {"version": 4, "action": "resume"},
         headers)  # fmt: skip
    call(bridge, "delete", "/sudarshan/alerts/api/groups/4", None, headers)
    call(bridge, "post", "/sudarshan/alerts/api/groups/sync", {"ref": "list:7"}, headers)
    seen = [(op, {k: v for k, v in message.items() if k not in ("op", "nonce", "principal")})
            for op, message in desk.calls]  # fmt: skip
    assert seen == [
        ("group_add", {"group": GROUP | {"delivery": "each", "digest_min": 5}}),
        ("group_edit", {"id": 4, "version": 2, "group": GROUP | {"name": "Swing 3"}}),
        ("group_pause", {"id": 4, "version": 3}),
        ("group_resume", {"id": 4, "version": 4}),
        ("group_delete", {"id": 4}),
        ("group_sync", {"ref": "list:7", "id": None}),
    ]


def test_an_unreadable_list_alert_is_the_draft_refusal_and_never_forwarded(bridge, service, desk):
    headers = as_owner(bridge)
    for method, path, body in (
        ("post", "/sudarshan/alerts/api/groups", {"group": "Swing"}),
        ("patch", "/sudarshan/alerts/api/groups/1", {"version": 1, "action": "edit"}),
        ("patch", "/sudarshan/alerts/api/groups/1", {"version": 1, "action": "restart"}),
    ):
        answer = call(bridge, method, path, body, headers)
        assert answer.status_code == 400 and answer.get_json()["code"] == "draft"
    big = {"group": GROUP | {"note": "x" * 70_000}}
    assert call(bridge, "post", "/sudarshan/alerts/api/groups", big, headers).status_code == 400
    assert desk.calls == []


def test_the_service_s_list_alert_refusals_pass_through_in_its_words(bridge, service, desk):
    words = "It does not fit: it needs 180 alerts and 140 of the 500 are free. Nothing was made."
    desk.answers["group_add"] = ApiError(409, "limit", words)
    answer = call(bridge, "post", "/sudarshan/alerts/api/groups", {"group": GROUP},
                  as_owner(bridge))  # fmt: skip
    assert answer.status_code == 409
    assert answer.get_json() == {"status": "error", "code": "limit", "message": words}


def test_list_alert_changes_are_limited_to_thirty_a_minute(service, desk, monkeypatch):
    reset_users()
    http = httpx.Client(trust_env=False)
    monkeypatch.setattr(bp_module, "_loopback", lambda: http)
    try:
        client = make_app(rate_limits=True).test_client()
        headers = as_owner(client)
        answers = [call(client, "delete", "/sudarshan/alerts/api/groups/1", None, headers)
                   for _ in range(31)]  # fmt: skip
    finally:
        http.close()
    assert [a.status_code for a in answers] == [200] * 30 + [429]


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
    monkeypatch.setattr(bp_module, "_loopback", lambda: http)
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
    expected = {(re.sub(r"/1$", "/<int:group_id>" if "/groups/" in path else "/<int:alert_id>",
                       path.split("?")[0]), method.upper())
                for method, path, _ in ROUTES}  # fmt: skip
    assert rules == expected | {("/sudarshan/alerts/internal/push", "POST")}  # the service's


def test_the_bridge_holds_no_loop_thread_or_scheduler():
    """sudarshan.md: nothing of the kind is added inside the OpenAlgo app."""
    source = (REPO / "blueprints" / "sudarshan_alerts.py").read_text(encoding="utf-8")
    for banned in ("asyncio", "import threading", "Thread(", "ThreadPoolExecutor", "APScheduler",
                   "BackgroundScheduler", "submit_to_hub("):  # fmt: skip
        assert banned not in source


# -- review of the API surface (M3, L1, L6, L7, the proxy point) -------------------------------


def test_a_transport_refusal_of_the_service_reaches_the_page_as_a_sentence(bridge, service, desk):
    """Review L1: 'size', 'timeout', 'length' and 'malformed' are the service's transport words,
    not the contract's; the page gets the 500 'failed' sentence instead."""
    headers = as_owner(bridge)
    for code in ("size", "timeout", "length", "malformed", "not_found"):
        desk.answers["prefs_get"] = ApiError(413, code, "request too large")
        answer = call(bridge, "get", "/sudarshan/alerts/api/prefs")
        assert answer.status_code == 500
        assert answer.get_json() == {
            "status": "error",
            "code": "failed",
            "message": (
                "Market alerts could not do that. Your alerts are kept. Try again in a minute."
            ),
        }
    desk.answers["delete"] = ApiError(404, "gone", "This alert is gone.")
    kept = call(bridge, "delete", "/sudarshan/alerts/api/alerts/1", None, headers)
    assert kept.status_code == 404 and kept.get_json()["code"] == "gone"  # a contract code stays


def test_a_draft_over_64_kib_is_the_draft_refusal_and_never_parsed_or_forwarded(
    bridge, service, desk
):
    headers = as_owner(bridge)
    big = {"alert": DRAFT | {"note": "x" * 70_000}}
    answer = call(bridge, "post", "/sudarshan/alerts/api/alerts", big, headers)
    assert answer.status_code == 400 and answer.get_json()["code"] == "draft"
    answer = call(bridge, "patch", "/sudarshan/alerts/api/alerts/1", big, headers)
    assert answer.status_code == 400 and answer.get_json()["code"] == "draft"
    assert desk.calls == []


def test_a_failure_inside_the_bridge_is_the_envelope_not_an_error_page(
    bridge, service, desk, monkeypatch
):
    def broken(symbol, exchange):
        raise RuntimeError("the master contract table is gone")

    monkeypatch.setattr(bp_module, "get_symbol_info", broken)
    answer = call(
        bridge, "post", "/sudarshan/alerts/api/alerts", {"alert": DRAFT}, as_owner(bridge)
    )
    assert answer.status_code == 500 and answer.is_json
    assert answer.get_json()["code"] == "failed" and "RuntimeError" not in answer.get_data(True)


def test_a_timeout_raised_inside_the_desk_is_a_500_not_a_408(bridge, service, desk):
    """The service's 408 is for a client that is slow to send; a TimeoutError of the desk is not."""
    desk.answers["prefs_get"] = TimeoutError("the claim")
    login_session(bridge)
    answer = call(bridge, "get", "/sudarshan/alerts/api/prefs")
    assert answer.status_code == 500 and answer.get_json()["code"] == "failed"


def test_each_op_waits_as_long_as_the_contract_says():
    """Review L7, B23: 3 s to read, 6 s for a change (delete and the settings too), 12 s test."""
    assert bp_module.TIMEOUTS == {
        "state": 3.0, "create": 6.0, "update": 6.0, "delete": 6.0, "events": 3.0,
        "events_since": 3.0, "prefs_get": 3.0, "prefs_set": 6.0, "test": 12.0,
        "group_add": 10.0, "group_edit": 12.0, "group_pause": 12.0, "group_resume": 12.0,
        "group_delete": 6.0, "group_sync": 12.0,
    }  # fmt: skip


def test_the_service_url_follows_the_service_s_port_and_stays_on_this_server(monkeypatch):
    """Review L6, B21."""
    monkeypatch.delenv("SUDARSHAN_ALERTS_API_URL", raising=False)
    monkeypatch.delenv("SUDARSHAN_ALERTS_HTTP_PORT", raising=False)
    assert bp_module._service_url() == "http://127.0.0.1:5582"
    monkeypatch.setenv("SUDARSHAN_ALERTS_HTTP_PORT", "5599")
    assert bp_module._service_url() == "http://127.0.0.1:5599"
    monkeypatch.setenv("SUDARSHAN_ALERTS_HTTP_PORT", "not a port")
    assert bp_module._service_url() == "http://127.0.0.1:5582"
    monkeypatch.setenv("SUDARSHAN_ALERTS_HTTP_PORT", "5599")
    for elsewhere in ("http://203.0.113.9:5582", "https://127.0.0.1:5582", "http://example.org"):
        monkeypatch.setenv("SUDARSHAN_ALERTS_API_URL", elsewhere)
        assert bp_module._service_url() == "http://127.0.0.1:5599"
    monkeypatch.setenv("SUDARSHAN_ALERTS_API_URL", "http://127.0.0.1:6000/")
    assert bp_module._service_url() == "http://127.0.0.1:6000"


def test_the_calls_to_the_service_ignore_a_proxy_in_the_environment(monkeypatch):
    """The suspected point of the review: the shared client trusts HTTP_PROXY; the service is on
    this server and its sealed bodies must never go through one."""
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    made = bp_module._loopback_client._factory()
    try:
        assert made._trust_env is False and made.follow_redirects is False
    finally:
        made.close()


def test_a_key_the_master_gives_that_is_not_an_upstox_key_is_refused(
    bridge, service, desk, monkeypatch
):
    """Review B17."""
    forged = SymbolData(symbol="SBIN", brsymbol="SBIN-EQ", name="X", exchange="NSE",
                        brexchange="NSE_EQ", token="NSE_EQ|two|parts", expiry="", strike=-1.0,
                        lotsize=1, instrumenttype="EQ", tick_size=0.05)  # fmt: skip
    monkeypatch.setattr(bp_module, "get_symbol_info", lambda s, e: forged)
    answer = call(
        bridge, "post", "/sudarshan/alerts/api/alerts", {"alert": DRAFT}, as_owner(bridge)
    )
    assert answer.status_code == 400 and answer.get_json()["code"] == "instrument"
    assert desk.calls == []


def test_a_port_another_program_holds_gets_nothing_sent_and_is_said(
    bridge, service, desk, monkeypatch
):
    """Review B19 ('held')."""
    from sudarshan.alerts import loopback

    monkeypatch.setattr(bp_module.loopback, "holder", lambda url, *rest: loopback.OTHER)
    login_session(bridge)
    answer = call(bridge, "get", "/sudarshan/alerts/api/prefs")
    assert answer.status_code == 503 and answer.get_json()["code"] == "held"
    assert "holds the alert service's port" in answer.get_json()["message"]
    assert desk.calls == []


def test_creating_is_limited_to_thirty_a_minute(service, desk, monkeypatch):
    """Review B20."""
    reset_users()
    monkeypatch.setenv("REDIRECT_URL", "http://127.0.0.1:5000/upstox/callback")
    monkeypatch.setattr(bp_module, "get_symbol_info", lambda s, e: MASTER.get((s, e)))
    http = httpx.Client(trust_env=False)
    monkeypatch.setattr(bp_module, "_loopback", lambda: http)
    try:
        client = make_app(rate_limits=True).test_client()
        headers = as_owner(client)
        codes = [call(client, "post", "/sudarshan/alerts/api/alerts", {"alert": DRAFT},
                      headers).status_code for _ in range(31)]  # fmt: skip
    finally:
        http.close()
    assert codes == [200] * 30 + [429]
