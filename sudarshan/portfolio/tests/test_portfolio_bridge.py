"""The portfolio page's bridge in OpenAlgo (M7.18): owner routes, CSRF, limiters, the words for
every way the service can fail, and the sealed forward to the service's real local API."""

from __future__ import annotations

import asyncio
import io
import os
import re
import socket
import time
import types
from pathlib import Path

import httpx
import pytest
from portfolio_bridge_app import (
    VIEWER,
    Calls,
    Desk,
    Service,
    csrf_headers,
    login_session,
    make_app,
    reset_users,
)

import blueprints.sudarshan_portfolio as bp_module
from sudarshan.portfolio.api import ApiError
from sudarshan.portfolio.core import kinds
from utils.thread_safe_cache import LockedTTLCache

REPO = Path(__file__).resolve().parents[3]
JSON = {"Accept": "application/json"}
WORDS = kinds.WORDS
PREFIX = kinds.API_PREFIX


def path_of(spec, ident: int = 7) -> str:
    return PREFIX + spec.route.replace("<id>", str(ident))


def body_of(spec):
    if spec.method == "GET" or spec.upload:
        return None
    return {"version": 1}


@pytest.fixture
def calls():
    return Calls()


@pytest.fixture
def desk():
    return Desk()


@pytest.fixture
def service(desk, calls, monkeypatch):
    with Service(desk, calls.registry(), os.environ["API_KEY_PEPPER"]) as running:
        monkeypatch.setenv("SUDARSHAN_PORTFOLIO_API_URL", running.url)
        yield running


@pytest.fixture
def bridge(monkeypatch):
    reset_users()
    http = httpx.Client(trust_env=False)
    monkeypatch.setattr(bp_module, "get_httpx_client", lambda: http)
    monkeypatch.setattr(bp_module, "_loopback_warned", LockedTTLCache(maxsize=8, ttl=600))
    app = make_app()
    yield app.test_client()
    http.close()


def send(client, spec, headers=None, **kwargs):
    return client.open(
        path_of(spec), method=spec.method, headers={**JSON, **(headers or {})}, **kwargs
    )


def as_owner(client) -> dict:
    login_session(client)
    return csrf_headers(client)


def error_of(response) -> dict:
    body = response.get_json()
    assert body["status"] == "error"
    return body["error"]


# -- the table ---------------------------------------------------------------------------------


def test_every_op_of_the_contract_has_its_route():
    app = make_app()
    rules = {
        (rule.endpoint.rpartition(".")[2], tuple(sorted(rule.methods - {"HEAD", "OPTIONS"})))
        for rule in app.url_map.iter_rules()
        if rule.endpoint.startswith("sudarshan_portfolio.")
    }
    assert rules == {(spec.name, (spec.method,)) for spec in kinds.OPS}
    for rule in app.url_map.iter_rules():
        if rule.endpoint.startswith("sudarshan_portfolio."):
            assert rule.rule.startswith("/sudarshan/portfolio/api/")


def test_app_registers_the_blueprint_with_two_marked_lines():
    lines = [
        line for line in (REPO / "app.py").read_text().splitlines() if "sudarshan_portfolio" in line
    ]
    assert len(lines) == 2 and all("SUDARSHAN" in line for line in lines)


# -- who may call ------------------------------------------------------------------------------


@pytest.mark.parametrize("spec", kinds.OPS, ids=lambda spec: spec.name)
def test_no_session_is_401_in_words(bridge, spec):
    headers = csrf_headers(bridge)  # a write meets CSRF first; an anonymous page has a token
    response = send(bridge, spec, headers, json=body_of(spec))
    assert response.status_code == 401
    assert error_of(response) == {"code": "unauthorized", "message": WORDS["unauthorized"]}


@pytest.mark.parametrize("spec", kinds.OPS, ids=lambda spec: spec.name)
def test_a_viewer_session_is_403_on_every_route(bridge, spec, service, calls):
    login_session(bridge, VIEWER)
    headers = csrf_headers(bridge)
    response = send(bridge, spec, headers, json=body_of(spec))
    assert response.status_code == 403
    assert error_of(response) == {"code": "forbidden", "message": WORDS["forbidden"]}
    assert calls.calls == []  # nothing was sent to the service


@pytest.mark.parametrize("spec", [s for s in kinds.OPS if s.method != "GET"], ids=lambda s: s.name)
def test_a_write_without_a_csrf_token_never_reaches_the_service(bridge, spec, service, calls):
    login_session(bridge)
    response = send(bridge, spec, json=body_of(spec))
    assert response.status_code == 400
    assert calls.calls == []


# -- the forward -------------------------------------------------------------------------------


def test_a_read_reaches_the_service_sealed_and_comes_back_in_the_envelope(bridge, service, calls):
    headers = as_owner(bridge)
    response = bridge.get(PREFIX + "/state", headers={**JSON, **headers})
    assert response.status_code == 200
    assert response.get_json() == {
        "status": "success", "enabled": True, "data": {"op": "state", "args": {}},
    }  # fmt: skip
    ((name, message),) = calls.calls
    assert name == "state" and message == {"args": {}}  # the service checked the owner itself


def test_query_values_are_read_as_the_table_says(bridge, service, calls):
    as_owner(bridge)
    query = "as_of=20261104&view=3&accounts=1,2&classes=equity,gold&tags=a&held=over"
    response = bridge.get(f"{PREFIX}/tabs/12?{query}&unknown=1", headers=JSON)
    assert response.status_code == 200
    assert calls.calls[0][1]["args"] == {
        "as_of": 20261104, "view": 3, "accounts": [1, 2], "classes": ["equity", "gold"],
        "tags": ["a"], "held": "over", "id": 12,
    }  # fmt: skip


def test_a_query_that_cannot_be_read_is_told_in_words_and_not_sent(bridge, service, calls):
    as_owner(bridge)
    response = bridge.get(f"{PREFIX}/tabs/12?as_of=tomorrow", headers=JSON)
    assert response.status_code == 400
    assert error_of(response)["code"] == "bad_request"
    assert calls.calls == []


def test_a_write_carries_its_body_and_the_route_names_the_target(bridge, service, calls):
    headers = as_owner(bridge)
    response = bridge.patch(
        f"{PREFIX}/tabs/5",
        json={"id": 99, "version": 4, "name": "Gold"},
        headers={**JSON, **headers},
    )
    assert response.status_code == 200
    assert calls.calls[0][1]["args"] == {"id": 5, "version": 4, "name": "Gold"}


def test_a_service_refusal_comes_back_with_its_code_and_entry(bridge, desk, calls, monkeypatch):
    calls.answers["activity_update"] = ApiError(
        422, "refused", "Zerodha holds 10 RELIANCE on 12 Mar 2025.", None
    )
    calls.answers["tab_update"] = ApiError(409, "version_conflict", WORDS["version_conflict"])
    with Service(desk, calls.registry(), os.environ["API_KEY_PEPPER"]) as running:
        monkeypatch.setenv("SUDARSHAN_PORTFOLIO_API_URL", running.url)
        headers = as_owner(bridge)
        conflict = bridge.patch(
            f"{PREFIX}/tabs/1", json={"version": 1}, headers={**JSON, **headers}
        )
        refused = bridge.patch(
            f"{PREFIX}/activities/1", json={"version": 1}, headers={**JSON, **headers}
        )
    assert conflict.status_code == 409
    assert error_of(conflict)["code"] == "version_conflict"
    assert refused.status_code == 422
    assert "RELIANCE" in error_of(refused)["message"]


def test_a_transport_refusal_of_the_service_is_never_shown_as_a_code(
    bridge, desk, calls, monkeypatch
):
    calls.answers["state"] = ApiError(503, "busy", "The portfolio service is busy. Wait a minute.")
    with Service(desk, calls.registry(), os.environ["API_KEY_PEPPER"]) as running:
        monkeypatch.setenv("SUDARSHAN_PORTFOLIO_API_URL", running.url)
        as_owner(bridge)
        response = bridge.get(PREFIX + "/state", headers=JSON)
    assert response.status_code == 503
    error = error_of(response)
    assert error["code"] == "unavailable" and "busy" in error["message"]


def test_an_op_the_service_does_not_have_is_not_ready(bridge, desk, monkeypatch):
    calls = Calls(state={"x": 1})
    with Service(desk, calls.registry(everything=False), os.environ["API_KEY_PEPPER"]) as running:
        monkeypatch.setenv("SUDARSHAN_PORTFOLIO_API_URL", running.url)
        as_owner(bridge)
        response = bridge.get(PREFIX + "/summary", headers=JSON)
    assert response.status_code == 501
    assert error_of(response) == {"code": "not_ready", "message": WORDS["not_ready"]}


def test_switched_off_is_a_success_that_says_so(bridge, calls, monkeypatch):
    with Service(Desk(enabled=False), calls.registry(), os.environ["API_KEY_PEPPER"]) as running:
        monkeypatch.setenv("SUDARSHAN_PORTFOLIO_API_URL", running.url)
        as_owner(bridge)
        response = bridge.get(PREFIX + "/state", headers=JSON)
    assert response.status_code == 200
    assert response.get_json() == {
        "status": "success", "enabled": False, "data": None, "message": WORDS["off"],
    }  # fmt: skip
    assert calls.calls == []


def test_the_service_down_is_told_in_words(bridge, monkeypatch):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setenv("SUDARSHAN_PORTFOLIO_API_URL", f"http://127.0.0.1:{port}")
    headers = as_owner(bridge)
    read = bridge.get(PREFIX + "/state", headers=JSON)
    write = bridge.post(PREFIX + "/tabs", json={"name": "x"}, headers={**JSON, **headers})
    for response in (read, write):
        assert response.status_code == 503
        assert error_of(response) == {"code": "unavailable", "message": WORDS["not_answering"]}


def test_a_port_held_by_another_users_program_is_not_sent_to(bridge, calls, service, monkeypatch):
    monkeypatch.setattr(
        bp_module.loopback, "holder", lambda url, tables=None: bp_module.loopback.OTHER
    )
    as_owner(bridge)
    response = bridge.get(PREFIX + "/state", headers=JSON)
    assert response.status_code == 503
    assert "holds the portfolio service's port" in error_of(response)["message"]
    assert calls.calls == []


def test_a_slow_read_is_not_answering(bridge, desk, calls, monkeypatch):
    async def slow(message):
        await asyncio.sleep(1.0)
        return {}

    calls.answers["state"] = slow
    monkeypatch.setitem(kinds.TIMEOUTS_S, "read", 0.2)
    with Service(desk, calls.registry(), os.environ["API_KEY_PEPPER"]) as running:
        monkeypatch.setenv("SUDARSHAN_PORTFOLIO_API_URL", running.url)
        as_owner(bridge)
        start = time.monotonic()
        response = bridge.get(PREFIX + "/state", headers=JSON)
        took = time.monotonic() - start
    assert response.status_code == 503 and took < 0.9
    assert error_of(response)["message"] == WORDS["not_answering"]


def test_a_slow_write_is_not_confirmed_and_is_sent_once(bridge, desk, calls, monkeypatch):
    async def slow(message):
        await asyncio.sleep(1.0)
        return {}

    calls.answers["tab_create"] = slow
    monkeypatch.setitem(kinds.TIMEOUTS_S, "write", 0.2)
    with Service(desk, calls.registry(), os.environ["API_KEY_PEPPER"]) as running:
        monkeypatch.setenv("SUDARSHAN_PORTFOLIO_API_URL", running.url)
        headers = as_owner(bridge)
        response = bridge.post(PREFIX + "/tabs", json={"name": "x"}, headers={**JSON, **headers})
        time.sleep(1.2)
    assert response.status_code == 504
    assert error_of(response) == {"code": "not_confirmed", "message": WORDS["not_confirmed"]}
    assert len(calls.calls) == 1  # never retried


def test_no_redirect_is_followed(bridge, monkeypatch):
    seen = {}

    def post(url, **kwargs):
        seen.update(kwargs)
        raise httpx.ConnectError("refused")

    client = types.SimpleNamespace(post=post)
    monkeypatch.setattr(bp_module, "get_httpx_client", lambda: client)
    monkeypatch.setattr(bp_module.loopback, "holder", lambda url: bp_module.loopback.OURS)
    as_owner(bridge)
    bridge.get(PREFIX + "/state", headers=JSON)
    assert seen["follow_redirects"] is False
    assert seen["timeout"].read == kinds.TIMEOUTS_S["read"]
    assert seen["timeout"].connect <= 1.0


@pytest.mark.parametrize(
    ("name", "cls", "key"),
    [("upstox_book", "upstox", "upstox"), ("import_preview", "import", "import"),
     ("tab_create", "write", "write"), ("state", "read", "read")],
)  # fmt: skip
def test_each_class_has_its_timeout(name, cls, key):
    spec = next(s for s in kinds.OPS if s.name == name)
    assert bp_module._timeout_for(spec) == float(kinds.TIMEOUTS_S[key])


# -- import ------------------------------------------------------------------------------------


def upload(client, headers, data: bytes, **form):
    return client.post(
        f"{PREFIX}/tabs/3/imports",
        data={"file": (io.BytesIO(data), "trades.csv"), "format": "generic", **form},
        content_type="multipart/form-data",
        headers={**JSON, **headers},
    )


def test_an_import_passes_the_file_on_as_text(bridge, service, calls):
    headers = as_owner(bridge)
    text = "date,action,symbol\n2026-01-02,BUY,सुदर्शन\n"
    response = upload(bridge, headers, ("﻿" + text).encode("utf-8"), account_id="4")
    assert response.status_code == 200
    assert calls.calls[0][1]["args"] == {
        "id": 3, "filename": "trades.csv", "text": text, "format": "generic", "account_id": 4,
    }  # fmt: skip


def test_an_import_of_the_largest_size_gets_through_the_seal(bridge, service, calls):
    headers = as_owner(bridge)
    row = b'"2026-01-02","BUY","RELIANCE","10","1234.50","\xe2\x82\xb9"\n'
    data = (row * (kinds.IMPORT_MAX_FILE_BYTES // len(row)))[: kinds.IMPORT_MAX_FILE_BYTES]
    data = data[: data.rfind(b"\n") + 1]
    response = upload(bridge, headers, data)
    assert response.status_code == 200, response.get_json()
    assert len(calls.calls[0][1]["args"]["text"].encode()) == len(data)


def test_a_file_over_the_limit_is_told_and_never_sent(bridge, service, calls):
    headers = as_owner(bridge)
    response = upload(bridge, headers, b"x" * (kinds.IMPORT_MAX_FILE_BYTES + 1))
    assert response.status_code == 400
    assert error_of(response)["message"] == WORDS["import_too_big"]
    assert calls.calls == []


def test_a_body_over_the_seal_is_told_the_same_way(bridge, service, calls):
    headers = as_owner(bridge)
    response = upload(bridge, headers, b"x" * (kinds.IMPORT_MAX_BYTES + 10))
    assert response.status_code == 400
    assert error_of(response)["message"] == WORDS["import_too_big"]
    assert calls.calls == []


def test_a_file_that_is_not_text_and_no_file_are_told_in_words(bridge, service, calls):
    headers = as_owner(bridge)
    binary = upload(bridge, headers, b"\xff\xfe\x00\x01\x80")
    assert "not text" in error_of(binary)["message"]
    none = bridge.post(
        f"{PREFIX}/tabs/3/imports", data={"format": "generic"},
        content_type="multipart/form-data", headers={**JSON, **headers},
    )  # fmt: skip
    assert error_of(none)["message"] == "Choose a file to import."
    assert calls.calls == []


def test_an_ordinary_body_over_64_kib_is_refused_before_it_is_read(bridge, service, calls):
    headers = as_owner(bridge)
    response = bridge.post(
        PREFIX + "/tabs", data=b'{"name":"' + b"x" * (kinds.API_MAX_BYTES + 1) + b'"}',
        content_type="application/json", headers={**JSON, **headers},
    )  # fmt: skip
    assert response.status_code == 400
    assert calls.calls == []


# -- limiters ----------------------------------------------------------------------------------


@pytest.fixture
def limited(monkeypatch):
    reset_users()
    app = make_app(rate_limits=True)
    monkeypatch.setattr(bp_module, "_call", lambda spec, args: ({"status": "success"}, 200))
    client = app.test_client()
    login_session(client)
    return client, csrf_headers(client)


def hits(fn, count: int) -> list[int]:
    return [fn().status_code for _ in range(count)]


def test_reads_are_limited_to_120_a_minute(limited):
    client, _ = limited
    codes = hits(lambda: client.get(PREFIX + "/state", headers=JSON), 121)
    assert codes[:120] == [200] * 120 and codes[120] == 429
    assert error_of(client.get(PREFIX + "/summary", headers=JSON)) == {
        "code": "rate_limited",
        "message": WORDS["rate_limited"],
    }  # one limit for the class, not one per route


def test_the_upstox_ops_count_as_reads(limited):
    client, _ = limited
    codes = hits(lambda: client.get(PREFIX + "/upstox", headers=JSON), 121)
    assert codes[120] == 429


def test_writes_are_limited_to_60_a_minute(limited):
    client, headers = limited
    codes = hits(lambda: client.post(PREFIX + "/tabs", json={}, headers={**JSON, **headers}), 61)
    assert codes[:60] == [200] * 60 and codes[60] == 429
    assert client.get(PREFIX + "/state", headers=JSON).status_code == 200  # reads have their own


def test_imports_are_limited_to_10_a_minute(limited):
    client, headers = limited
    codes = hits(lambda: upload(client, headers, b"a,b\n1,2\n"), 11)
    assert codes[:10] == [200] * 10 and codes[10] == 429


def test_a_dry_run_has_its_own_limit_and_spares_the_write_limit(limited):
    client, headers = limited
    dry = lambda: client.post(  # noqa: E731
        PREFIX + "/tabs/1/activities", json={"dry_run": True}, headers={**JSON, **headers}
    )
    codes = hits(dry, 121)
    assert codes[:120] == [200] * 120 and codes[120] == 429
    real = client.post(PREFIX + "/tabs/1/activities", json={}, headers={**JSON, **headers})
    assert real.status_code == 200  # 120 dry runs used none of the 60 writes
    edit = hits(
        lambda: client.patch(
            PREFIX + "/activities/1", json={"dry_run": True}, headers={**JSON, **headers}
        ),
        1,
    )
    assert edit == [429]  # activity_update's dry runs share the dry-run limit


def test_a_real_entry_is_a_write_even_when_dry_run_is_false(limited):
    client, headers = limited
    codes = hits(
        lambda: client.post(
            PREFIX + "/tabs/1/activities", json={"dry_run": False}, headers={**JSON, **headers}
        ),
        61,
    )
    assert codes[60] == 429


def test_the_service_url_is_always_this_server(monkeypatch):
    monkeypatch.setenv("SUDARSHAN_PORTFOLIO_API_URL", "http://example.invalid:5583")
    assert re.fullmatch(r"http://127\.0\.0\.1:\d+", bp_module._service_url())
