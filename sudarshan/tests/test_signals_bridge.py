"""The TRINETRA page's bridge in OpenAlgo (B1): every route of the contract, who may call it, the
seal, the envelope and the words for every way the service can answer or fail (README section 8).

The service here is the stand-in in signals_bridge_app.py, which opens each request with the real
SIGNALS_API_INFO key; the service's own API is tested in sudarshan/signals/tests."""

from __future__ import annotations

import os
import socket
from pathlib import Path

import httpx
import pytest
from signals_bridge_app import (
    VIEWER,
    FakeSignals,
    csrf_headers,
    login_session,
    make_app,
    reset_users,
)

import blueprints.sudarshan_signals as bp_module
from sudarshan.signals import contract
from sudarshan.vault import SIGNALS_JOURNAL_INFO, fernet_for
from utils.thread_safe_cache import LockedTTLCache

JSON = {"Accept": "application/json"}
PREFIX = contract.API_PREFIX
WORDS = contract.WORDS


def path_of(spec, ident: int = 7) -> str:
    return PREFIX + spec.route.replace("<id>", str(ident))


@pytest.fixture(scope="module", autouse=True)
def _users():
    reset_users()


@pytest.fixture
def signals(monkeypatch):
    with FakeSignals() as running:
        monkeypatch.setenv("SUDARSHAN_SIGNALS_API_URL", running.url)
        yield running


@pytest.fixture
def bridge(monkeypatch):
    http = httpx.Client(trust_env=False)
    monkeypatch.setattr(bp_module, "get_httpx_client", lambda: http)
    monkeypatch.setattr(bp_module, "_warned", LockedTTLCache(maxsize=8, ttl=600))
    yield make_app().test_client()
    http.close()


def send(client, spec, headers=None, **kwargs):
    if spec.method != "GET" and "json" not in kwargs:
        kwargs["json"] = {"version": 1}
    return client.open(path_of(spec), method=spec.method, headers={**JSON, **(headers or {})},
                       **kwargs)  # fmt: skip


def as_owner(client) -> dict:
    login_session(client)
    return csrf_headers(client)


def error_of(response) -> dict:
    body = response.get_json()
    assert body["status"] == "error", body
    return body["error"]


def test_every_op_of_the_contract_has_its_route():
    app = make_app()
    rules = {
        (rule.endpoint.rpartition(".")[2], rule.rule,
         tuple(sorted(rule.methods - {"HEAD", "OPTIONS"})))
        for rule in app.url_map.iter_rules()
        if rule.endpoint.startswith("sudarshan_signals.")
    }  # fmt: skip
    expected = {
        (s.name, PREFIX + s.route.replace("<id>", "<int:id>"), (s.method,)) for s in contract.OPS
    }
    assert rules == expected
    assert {s.name: s.query for s in bp_module.OPS}["timeline"] == (
        ("module", "str"), ("instrument", "str"), ("before", "int"), ("after_ms", "int"),
        ("limit", "int"))  # fmt: skip


def test_app_registers_the_blueprint_with_two_marked_lines():
    text = (Path(__file__).resolve().parents[2] / "app.py").read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if "sudarshan_signals" in line]
    assert len(lines) == 2 and all("SUDARSHAN" in line for line in lines)


def test_the_service_url_is_always_this_server(monkeypatch):
    monkeypatch.setenv("SUDARSHAN_SIGNALS_API_URL", "http://example.invalid:5588")
    assert bp_module._service_url() == "http://127.0.0.1:5588"
    monkeypatch.delenv("SUDARSHAN_SIGNALS_API_URL")
    monkeypatch.setenv("SUDARSHAN_SIGNALS_HTTP_PORT", "6588")
    assert bp_module._service_url() == "http://127.0.0.1:6588"


@pytest.mark.parametrize("spec", bp_module.OPS, ids=lambda s: s.name)
def test_no_session_is_401_and_another_session_is_403_on_every_route(bridge, signals, spec):
    response = send(bridge, spec, headers=csrf_headers(bridge))  # an anonymous page has a token
    assert response.status_code == 401 and error_of(response) == {
        "code": "unauthorized", "message": WORDS["unauthorized"]}  # fmt: skip
    login_session(bridge, VIEWER)  # a family viewer, or any session that is not the owner's
    response = send(bridge, spec, headers=csrf_headers(bridge))
    assert response.status_code == 403 and error_of(response)["code"] == "forbidden"
    assert error_of(response)["message"] == WORDS["forbidden"]
    assert signals.calls == [], "nothing reached the service"


def test_the_owner_reads_with_typed_arguments(bridge, signals):
    as_owner(bridge)
    got = bridge.get(f"{PREFIX}/timeline?module=regime&instrument=NIFTY&before=40&limit=10",
                     headers=JSON).get_json()  # fmt: skip
    assert got == {"status": "success", "enabled": True,
                   "data": {"op": "timeline", "args": {"module": "regime", "instrument": "NIFTY",
                                                       "before": 40, "limit": 10}}}  # fmt: skip
    bridge.get(f"{PREFIX}/changes/12", headers=JSON)
    assert signals.calls[-1] == ("change_get", {"id": 12})
    bad = bridge.get(f"{PREFIX}/timeline?limit=ten", headers=JSON)
    assert bad.status_code == 400 and error_of(bad)["message"] == WORDS["bad_request"]
    assert len(signals.calls) == 2


def test_the_write_needs_csrf_and_sends_its_body(bridge, signals):
    login_session(bridge)
    refused = bridge.put(f"{PREFIX}/settings", json={"version": 1, "lang": "gu"}, headers=JSON)
    assert refused.status_code == 400 and signals.calls == []
    done = bridge.put(f"{PREFIX}/settings", json={"version": 1, "lang": "gu"},
                      headers={**JSON, **csrf_headers(bridge)})  # fmt: skip
    assert done.status_code == 200 and signals.calls == [("settings_set", {"version": 1,
                                                                            "lang": "gu"})]  # fmt: skip


def test_switched_off_is_a_success_that_says_so(bridge, signals):
    as_owner(bridge)
    signals.answers["state"] = (
        200,
        {"status": "success", "data": {"enabled": False, "message": WORDS["not_set_up"]}},
    )
    got = bridge.get(f"{PREFIX}/state", headers=JSON)
    assert got.status_code == 200 and got.get_json() == {
        "status": "success",
        "enabled": False,
        "data": None,
        "message": WORDS["not_set_up"],
    }


@pytest.mark.parametrize(
    ("answer", "status", "code", "message", "field"),
    [
        ((409, {"status": "error", "code": "version_conflict", "message": WORDS["version_conflict"]}),
         409, "version_conflict", WORDS["version_conflict"], None),
        ((400, {"status": "error", "code": "bad_request", "message": WORDS["bad_lang"],
                "field": "lang"}), 400, "bad_request", WORDS["bad_lang"], "lang"),
        ((404, {"status": "error", "code": "not_found", "message": WORDS["not_found"]}),
         404, "not_found", WORDS["not_found"], None),
        ((501, {"status": "error", "code": "not_ready", "message": "x"}),
         501, "not_ready", WORDS["not_ready"], None),
        ((404, {"status": "error", "code": "not_found", "message": "not found"}),
         501, "not_ready", WORDS["not_ready"], None),
        ((403, {"status": "error", "code": "forbidden", "message": WORDS["sealed"]}),
         503, "unavailable", WORDS["sealed"], None),
        ((503, {"status": "error", "code": "busy", "message": WORDS["busy"]}),
         503, "unavailable", WORDS["busy"], None),
        ((500, {"status": "error", "code": "failed", "message": "Traceback <b>"}),
         503, "unavailable", WORDS["failed"], None),
        ((401, {"status": "error", "code": "unauthorized", "message": "x"}),
         503, "unavailable", WORDS["failed"], None),
    ],
)  # fmt: skip
def test_every_answer_of_the_service_in_the_pages_words(bridge, signals, answer, status, code,
                                                        message, field):  # fmt: skip
    headers = as_owner(bridge)
    signals.answers["settings_set"] = answer
    got = bridge.put(f"{PREFIX}/settings", json={"version": 1}, headers={**JSON, **headers})
    assert got.status_code == status
    error = error_of(got)
    assert (error["code"], error["message"], error.get("field")) == (code, message, field)


def test_nobody_on_the_port_is_not_answering(bridge, monkeypatch):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setenv("SUDARSHAN_SIGNALS_API_URL", f"http://127.0.0.1:{port}")
    as_owner(bridge)
    got = bridge.get(f"{PREFIX}/state", headers=JSON)
    assert got.status_code == 503 and error_of(got)["message"] == WORDS["not_answering"]


def test_an_unusable_answer_asks_health_which_may_say_off(bridge, signals):
    headers = as_owner(bridge)
    signals.answers["state"] = "garbage"
    signals.health = (200, {"service": "sudarshan-signals", "signals": "off"})
    got = bridge.get(f"{PREFIX}/state", headers=JSON)
    assert got.get_json() == {"status": "success", "enabled": False, "data": None,
                              "message": WORDS["off"]}  # fmt: skip
    signals.health = (200, {"service": "sudarshan-signals", "signals": "on"})
    assert error_of(bridge.get(f"{PREFIX}/state", headers=JSON))["code"] == "unavailable"
    signals.answers["settings_set"] = "garbage"
    write = bridge.put(f"{PREFIX}/settings", json={"version": 1}, headers={**JSON, **headers})
    assert write.status_code == 504 and error_of(write)["message"] == WORDS["not_confirmed"]


def test_a_write_left_unanswered_is_not_confirmed(bridge, signals, monkeypatch):
    headers = as_owner(bridge)
    monkeypatch.setitem(contract.TIMEOUTS_S, "write", 0.3)
    signals.delay_s = 1.0
    got = bridge.put(f"{PREFIX}/settings", json={"version": 1}, headers={**JSON, **headers})
    assert got.status_code == 504 and error_of(got)["code"] == "not_confirmed"


def test_the_seal_is_the_api_purpose_and_never_the_others(bridge, signals, monkeypatch):
    as_owner(bridge)
    assert bridge.get(f"{PREFIX}/state", headers=JSON).status_code == 200
    other = fernet_for(os.environ["API_KEY_PEPPER"], SIGNALS_JOURNAL_INFO)
    monkeypatch.setattr(bp_module, "_fernet", lambda: other)
    got = bridge.get(f"{PREFIX}/state", headers=JSON)
    assert error_of(got)["message"] == WORDS["sealed"] and signals.refused == ["seal"]


def test_writes_are_limited_to_30_a_minute(signals, monkeypatch):
    http = httpx.Client(trust_env=False)
    monkeypatch.setattr(bp_module, "get_httpx_client", lambda: http)
    client = make_app(rate_limits=True).test_client()
    try:
        headers = as_owner(client)
        codes = [
            client.put(f"{PREFIX}/settings", json={"version": 1},
                       headers={**JSON, **headers}).status_code
            for _ in range(31)
        ]  # fmt: skip
    finally:
        http.close()
    assert codes[:30] == [200] * 30 and codes[30] == 429
