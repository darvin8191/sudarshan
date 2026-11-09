"""The screener page's bridge in OpenAlgo (BR1): owner routes, 401 and 403, CSRF, limiters, the
sealed forward to the service's real local API, the words for every way the service can fail, the
internal watchlist and push routes with their 404 when forwarded and 403 for a bad seal, and the
registration in app.py."""

from __future__ import annotations

import os
import re
from pathlib import Path

import httpx
import pytest
from screener_bridge_app import (
    OWNER,
    VIEWER,
    Calls,
    Desk,
    Service,
    csrf_headers,
    login_session,
    make_app,
    reset_users,
    reset_watchlists,
)

import blueprints.sudarshan_screener as bp_module
from database import watchlist_db
from sudarshan.autologin.handoff import seal
from sudarshan.screener import contract
from sudarshan.screener.ops import ApiError
from sudarshan.vault import SCREENER_API_INFO, SCREENER_INTERNAL_INFO, fernet_for
from utils.thread_safe_cache import LockedTTLCache

REPO = Path(__file__).resolve().parents[3]
JSON = {"Accept": "application/json"}
PREFIX = contract.API_PREFIX
INTERNAL = contract.INTERNAL_PREFIX
PEPPER = os.environ["API_KEY_PEPPER"]


def path_of(spec, ident: int = 7) -> str:
    return PREFIX + spec.route.replace("<id>", str(ident))


def body_of(spec):
    return None if spec.method in ("GET", "DELETE") else {"version": 1}


@pytest.fixture
def calls():
    return Calls()


@pytest.fixture
def service(calls, monkeypatch):
    with Service(Desk(), calls.registry(), PEPPER) as running:
        monkeypatch.setenv("SUDARSHAN_SCREENER_API_URL", running.url)
        yield running


@pytest.fixture
def bridge(monkeypatch):
    reset_users()
    reset_watchlists()
    http = httpx.Client(trust_env=False)
    monkeypatch.setattr(bp_module, "get_httpx_client", lambda: http)
    monkeypatch.setattr(bp_module, "_warned", LockedTTLCache(maxsize=8, ttl=600))
    monkeypatch.setattr(bp_module, "_made", LockedTTLCache(maxsize=64, ttl=3600), raising=False)
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


def internal(client, route: str, op: str, info=SCREENER_INTERNAL_INFO, headers=None, **args):
    blob = seal(fernet_for(PEPPER, info), op, principal="owner", args=args)
    query = f"?op={op}" if route == "watchlist" else ""
    return client.post(
        f"{INTERNAL}/{route}{query}",
        data=blob,
        headers={"Content-Type": "application/octet-stream", **(headers or {})},
    )


# -- the table and the registration ------------------------------------------------------------
def test_every_op_of_the_contract_has_its_route_and_the_internal_routes_exist():
    app = make_app()
    rules = {
        (rule.endpoint.rpartition(".")[2], tuple(sorted(rule.methods - {"HEAD", "OPTIONS"})))
        for rule in app.url_map.iter_rules()
        if rule.endpoint.startswith("sudarshan_screener.")
    }
    assert rules == {(spec.name, (spec.method,)) for spec in contract.OPS}
    internal_rules = {
        rule.rule
        for rule in app.url_map.iter_rules()
        if rule.endpoint.startswith("sudarshan_screener_internal.")
    }
    assert internal_rules == {f"{INTERNAL}/watchlist", f"{INTERNAL}/push"}


def test_app_registers_both_blueprints_with_marked_lines():
    text = (REPO / "app.py").read_text(encoding="utf-8")
    assert "sudarshan_screener_bp,\n" in text and "sudarshan_screener_internal_bp,\n" in text
    assert "app.register_blueprint(sudarshan_screener_bp)  # SUDARSHAN M9" in text
    assert "app.register_blueprint(sudarshan_screener_internal_bp)  # SUDARSHAN M9" in text


# -- who may call ------------------------------------------------------------------------------
@pytest.mark.parametrize("spec", contract.OPS, ids=[s.name for s in contract.OPS])
def test_no_session_is_401_in_the_envelope_for_every_route(bridge, spec):
    headers = csrf_headers(bridge)  # a write meets CSRF first; an anonymous page has a token
    response = send(bridge, spec, headers, json=body_of(spec))
    assert response.status_code == 401
    assert error_of(response) == {"code": "unauthorized", "message": contract.WORDS["unauthorized"]}


@pytest.mark.parametrize("spec", contract.OPS, ids=[s.name for s in contract.OPS])
def test_a_viewer_session_is_403_on_every_route_reads_included(bridge, calls, spec):
    login_session(bridge, VIEWER)
    response = send(bridge, spec, headers=csrf_headers(bridge), json=body_of(spec))
    assert response.status_code == 403
    assert error_of(response)["code"] == "forbidden"
    assert calls.calls == []


def test_a_write_without_a_csrf_token_is_refused_and_a_read_needs_none(bridge, service, calls):
    login_session(bridge)
    create = next(s for s in contract.OPS if s.name == "create")
    response = send(bridge, create, json={"spec": {}})
    assert response.status_code == 400 and calls.calls == []
    state = next(s for s in contract.OPS if s.name == "state")
    response = send(bridge, state)
    assert response.status_code == 200 and calls.calls[0][0] == "state"


# -- the forward -------------------------------------------------------------------------------
def test_the_forward_carries_the_route_id_the_query_and_the_body(bridge, service, calls):
    headers = as_owner(bridge)
    runs = next(s for s in contract.OPS if s.name == "runs")
    response = bridge.get(f"{PREFIX}/screens/7/runs?before=40&limit=10", headers=JSON)
    assert response.status_code == 200
    assert calls.calls[-1] == ("runs", {"before": 40, "limit": 10, "id": 7})
    response = bridge.delete(f"{PREFIX}/screens/7?remove_added=1", headers={**JSON, **headers})
    assert response.status_code == 200 and calls.calls[-1] == (
        "delete",
        {"remove_added": True, "id": 7},
    )
    response = bridge.patch(
        f"{PREFIX}/screens/7", json={"id": 99, "version": 3, "action": "pause"}, headers=headers
    )
    assert calls.calls[-1] == ("update", {"id": 7, "version": 3, "action": "pause"})
    body = response.get_json()
    assert body == {
        "status": "success",
        "enabled": True,
        "data": {"op": "update", "args": {"id": 7, "version": 3, "action": "pause"}},
    }
    response = bridge.get(f"{PREFIX}/screens/7/runs?before=abc", headers=JSON)
    assert response.status_code == 400 and error_of(response)["code"] == "bad_request"
    assert runs.cls == "read"


def test_the_services_refusals_keep_their_code_and_words(bridge, monkeypatch):
    calls = Calls(
        results=ApiError.of("not_found", contract.WORDS["gone"]),
        create=ApiError.of("refused", "Type a number, like 55 or 2500.50.", "c1.right"),
        update=ApiError.of("version_conflict", contract.WORDS["version_conflict"]),
        today=ApiError(500, "failed", "The screener could not do this just now."),
    )
    with Service(Desk(), calls.registry(), PEPPER) as running:
        monkeypatch.setenv("SUDARSHAN_SCREENER_API_URL", running.url)
        headers = as_owner(bridge)
        response = bridge.get(f"{PREFIX}/screens/7/results", headers=JSON)
        assert (
            response.status_code == 404 and error_of(response)["message"] == contract.WORDS["gone"]
        )
        response = bridge.post(f"{PREFIX}/screens", json={"spec": {}}, headers=headers)
        assert response.status_code == 422
        assert error_of(response) == {
            "code": "refused",
            "message": "Type a number, like 55 or 2500.50.",
            "field": "c1.right",
        }
        response = bridge.patch(
            f"{PREFIX}/screens/7", json={"version": 1, "action": "pause"}, headers=headers
        )
        assert response.status_code == 409 and error_of(response)["code"] == "version_conflict"
        response = bridge.get(f"{PREFIX}/today", headers=JSON)
        assert response.status_code == 503 and error_of(response)["code"] == "unavailable"


def test_switched_off_is_said_in_words_not_as_an_error(bridge, monkeypatch):
    with Service(Desk(enabled=False), {}, PEPPER) as running:
        monkeypatch.setenv("SUDARSHAN_SCREENER_API_URL", running.url)
        login_session(bridge)
        response = bridge.get(f"{PREFIX}/state", headers=JSON)
        assert response.status_code == 200
        assert response.get_json() == {
            "status": "success",
            "enabled": False,
            "data": None,
            "message": contract.WORDS["off"],
        }


def test_a_service_sealed_with_another_pepper_reads_as_unavailable_in_words(bridge, monkeypatch):
    with Service(Desk(), Calls().registry(), "z" * 48) as running:
        monkeypatch.setenv("SUDARSHAN_SCREENER_API_URL", running.url)
        login_session(bridge)
        response = bridge.get(f"{PREFIX}/state", headers=JSON)
        assert response.status_code == 503
        words = error_of(response)["message"]
        assert "restart sudarshan-screener" in words and not re.search(
            r"\b(403|Fernet|seal)\b", words
        )


def test_nothing_listening_is_not_answering_and_a_write_that_got_no_answer_is_not_confirmed(
    bridge, monkeypatch
):
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free = probe.getsockname()[1]
    monkeypatch.setenv("SUDARSHAN_SCREENER_API_URL", f"http://127.0.0.1:{free}")
    headers = as_owner(bridge)
    response = bridge.get(f"{PREFIX}/state", headers=JSON)
    assert (
        response.status_code == 503
        and error_of(response)["message"] == contract.WORDS["not_answering"]
    )
    monkeypatch.setattr(bp_module, "_listener", lambda: None)

    class Hangs:
        def post(self, *args, **kwargs):
            raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(bp_module, "get_httpx_client", lambda: Hangs())
    response = bridge.post(f"{PREFIX}/screens", json={"spec": {}}, headers=headers)
    assert response.status_code == 504 and error_of(response)["code"] == "not_confirmed"
    response = bridge.get(f"{PREFIX}/state", headers=JSON)
    assert response.status_code == 503, "a read that got no answer is simply not answering"


def test_the_preview_limiter_is_six_a_minute_with_its_own_words(monkeypatch, service):
    reset_users()
    app = make_app(rate_limits=True)
    client = app.test_client()
    headers = as_owner(client)
    for _ in range(contract.LIMITS_PER_MIN["preview"]):
        assert (
            client.post(f"{PREFIX}/preview", json={"spec": {}}, headers=headers).status_code == 200
        )
    response = client.post(f"{PREFIX}/preview", json={"spec": {}}, headers=headers)
    assert (
        response.status_code == 429
        and error_of(response)["message"] == contract.WORDS["preview_limited"]
    )
    assert client.get(f"{PREFIX}/state", headers=JSON).status_code == 200, (
        "reads are a class of their own"
    )


# -- the internal routes ---------------------------------------------------------------------
def test_internal_routes_refuse_a_forwarded_request_a_bad_seal_and_another_purpose(bridge):
    response = internal(
        bridge, "push", "push", headers={"X-Forwarded-For": "203.0.113.9"}, items=[]
    )
    assert response.status_code == 404
    response = internal(bridge, "push", "push", info=SCREENER_API_INFO, items=[])
    assert response.status_code == 403
    response = internal(bridge, "watchlist", "watchlist_read", info=SCREENER_API_INFO, list_id=1)
    assert response.status_code == 403
    response = bridge.post(f"{INTERNAL}/push", data=b"x" * (contract.INTERNAL_MAX_BYTES + 1))
    assert response.status_code == 413
    assert bridge.post(f"{INTERNAL}/watchlist?op=explode", data=b"x").status_code == 404


def test_a_push_is_emitted_to_the_owners_room_only_and_needs_no_csrf(bridge, monkeypatch):
    emitted: list = []
    monkeypatch.setattr(
        bp_module,
        "emit_from_any_thread",
        lambda event, data, **kw: emitted.append((event, data, kw)),
    )
    items = [
        {"event": contract.EVENT_RUN, "data": {"screen_id": 1, "run_id": 4}},
        {"event": "order_update", "data": {"x": 1}},  # not one of the screener's events
        "junk",
    ]
    response = internal(bridge, "push", "push", items=items)
    assert response.status_code == 200 and response.get_json()["status"] == "success"
    assert emitted == [
        (
            contract.EVENT_RUN,
            {"screen_id": 1, "run_id": 4},
            {"to": bp_module.ROOM, "namespace": "/"},
        )
    ]
    assert internal(bridge, "push", "push", items="no").status_code == 400


def test_the_watchlist_route_reads_makes_adds_and_removes_as_the_owner(bridge, monkeypatch):
    emitted: list = []
    monkeypatch.setattr(
        bp_module, "emit_from_any_thread", lambda event, data, **kw: emitted.append((event, data))
    )
    response = internal(bridge, "watchlist", "watchlist_read", list_id=424242)
    assert response.get_json() == {"status": "success", "found": False}
    response = internal(
        bridge, "watchlist", "watchlist_apply", list_id=None, create_name="Screener: Breakout",
        add=[{"key": "NSE_EQ|A", "symbol": "sbin", "exchange": "nse"}], remove=[], ref="1:v1:20261028:15:4",
    )  # fmt: skip
    body = response.get_json()
    assert body["found"] is True and body["name"] == "Screener: Breakout"
    assert [(i["symbol"], i["exchange"]) for i in body["items"]] == [("SBIN", "NSE")]
    list_id, item_id = body["list_id"], body["items"][0]["id"]
    assert emitted == [(contract.EVENT_LISTS, {"list_id": list_id})]
    # the same create name again gets " (2)"; an add that exists is a no-op; a remove by id works
    response = internal(
        bridge,
        "watchlist",
        "watchlist_apply",
        list_id=None,
        create_name="Screener: Breakout",
        add=[],
        remove=[],
        ref="x",
    )
    assert response.get_json()["name"] == "Screener: Breakout (2)"
    response = internal(
        bridge, "watchlist", "watchlist_apply", list_id=list_id, create_name=None,
        add=[{"key": "NSE_EQ|A", "symbol": "SBIN", "exchange": "NSE"}, {"key": "k", "symbol": "INFY", "exchange": "NSE"}],
        remove=[item_id, 999999], ref="y",
    )  # fmt: skip
    body = response.get_json()
    assert [i["symbol"] for i in body["items"]] == ["INFY"]
    stored = {w["id"]: w for w in watchlist_db.get_watchlists(OWNER)}
    assert [i["symbol"] for i in stored[list_id]["items"]] == ["INFY"]
    response = internal(bridge, "watchlist", "watchlist_read", list_id=list_id)
    assert response.get_json()["items"][0]["symbol"] == "INFY"
    response = internal(
        bridge,
        "watchlist",
        "watchlist_apply",
        list_id=424242,
        create_name=None,
        add=[],
        remove=[],
        ref="z",
    )
    assert response.get_json() == {"status": "success", "found": False}
    response = internal(
        bridge,
        "watchlist",
        "watchlist_apply",
        list_id=None,
        create_name="",
        add=[],
        remove=[],
        ref="z",
    )
    assert response.status_code == 400


def test_a_create_tried_again_answers_the_list_it_made(bridge, monkeypatch):
    """The service's create lost its answer and is tried again under the same key: the same list,
    never "Screener: Breakout (2)"; a list deleted meanwhile is made again (release review HIGH-5)."""
    monkeypatch.setattr(bp_module, "emit_from_any_thread", lambda *a, **kw: None)

    def create(ref):
        return internal(
            bridge, "watchlist", "watchlist_apply", list_id=None,
            create_name="Screener: Breakout", add=[], remove=[], ref=ref,
        ).get_json()  # fmt: skip

    first = create("screen:4:list")
    again = create("screen:4:list")
    assert again["list_id"] == first["list_id"] and again["name"] == "Screener: Breakout"
    names = sorted(w["name"] for w in watchlist_db.get_watchlists(OWNER))
    assert names == ["Screener: Breakout"], "a retried create made another list"
    watchlist_db.delete_watchlist(OWNER, first["list_id"])
    assert watchlist_db.get_watchlists(OWNER) == []
    remade = create("screen:4:list")
    assert remade["found"] is True and remade["name"] == "Screener: Breakout"
    assert [w["name"] for w in watchlist_db.get_watchlists(OWNER)] == ["Screener: Breakout"]


def test_the_join_handler_admits_the_owner_only(bridge, monkeypatch):
    joined: list = []
    monkeypatch.setattr(bp_module, "join_room", joined.append)
    app = bridge.application
    with app.test_request_context():
        from flask import session

        session.clear()
        answer = bp_module._join_owner_room()
        assert answer["ok"] is False and joined == []
    with app.test_request_context():
        from datetime import datetime

        import pytz
        from flask import session

        session["user"], session["logged_in"] = VIEWER, True
        session["login_time"] = datetime.now(pytz.timezone("Asia/Kolkata")).isoformat()
        assert bp_module._join_owner_room()["ok"] is False
        session["user"] = OWNER
        assert bp_module._join_owner_room()["ok"] is True and joined == [bp_module.ROOM]
