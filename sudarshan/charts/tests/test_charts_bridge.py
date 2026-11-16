"""The chart documents' bridge (M4.18, T7a): the checks in order, a family phone and another
sign-in refused on every route, the switch, the envelope, the revision check through the routes,
the bounds on a request, and the registration in app.py."""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest
from charts_bridge_app import (
    API,
    OWNER,
    ROUTES,
    VIEWER,
    csrf_headers,
    login_session,
    make_app,
    reset_users,
)

import blueprints.sudarshan_charts as bp_module
from sudarshan.charts import schema, store
from sudarshan.charts.words import MESSAGES, RECORD_NEWER
from sudarshan.tests.fdwatch import FdWatch

REPO = Path(__file__).resolve().parents[3]
DOC = f"{API}/doc"
LEVEL = {"version": 2, "drawings": [{"id": "d1", "tool": "horizontal-line"}]}


@pytest.fixture(scope="module")
def users():
    """OpenAlgo's user table with the owner and one other sign-in (hashing a password is slow, so
    once for the file)."""
    reset_users()


@pytest.fixture
def switch(monkeypatch):
    monkeypatch.setenv(bp_module.SWITCH_ENV, "on")
    return monkeypatch


@pytest.fixture(autouse=True)
def fresh(chart_db):
    bp_module._record.clear()
    yield
    bp_module._record.clear()


@pytest.fixture
def owner(users, switch):
    client = make_app().test_client()
    login_session(client)
    return client


def put(client, body: dict, headers: dict | None = None):
    return client.put(DOC, json=body, headers=csrf_headers(client) if headers is None else headers)


def call(client, method: str, path: str, headers: dict | None = None):
    if method == "PUT":
        return client.put(path, json={"kind": "drawings", "key": "NSE:INFY", "base": 0,
                                      "body": LEVEL}, headers=headers or {})  # fmt: skip
    return client.get(path + ("?kind=drawings&key=NSE:INFY" if path == DOC else ""))


# -- the checks, in order ------------------------------------------------------------------------
@pytest.mark.parametrize("method, path", ROUTES)
def test_no_session_and_a_family_phone_are_refused_on_every_route_before_anything_opens(
    users, switch, chart_db, method, path
):
    anonymous = make_app().test_client()
    family = make_app().test_client()
    # A family phone sends only its own cookie, which OpenAlgo does not read (P6 0.1).
    family.set_cookie("__Host-sudarshan_family", "a-viewer-session")
    for client in (anonymous, family):
        refused = call(client, method, path)
        assert refused.status_code == 401
        assert refused.get_json() == {"status": "error", "code": "session",
                                      "message": MESSAGES["session"]}  # fmt: skip
    assert not chart_db.exists()


@pytest.mark.parametrize("method, path", ROUTES)
def test_another_sign_in_is_refused_on_every_route_reads_included(
    users, switch, chart_db, method, path
):
    viewer = make_app().test_client()
    login_session(viewer, VIEWER)
    refused = call(viewer, method, path, headers=csrf_headers(viewer))
    assert refused.status_code == 403 and refused.get_json()["code"] == "owner_only"
    assert not chart_db.exists()


def test_the_owner_check_comes_before_the_csrf_check(users, switch):
    viewer = make_app().test_client()
    login_session(viewer, VIEWER)
    assert call(viewer, "PUT", DOC).get_json()["code"] == "owner_only"


def test_a_write_needs_the_csrf_token_and_a_lapsed_one_reads_as_signed_out(owner, chart_db):
    refused = put(owner, {"kind": "drawings", "key": "NSE:INFY", "base": 0, "body": LEVEL}, {})
    assert refused.status_code == 401 and refused.get_json()["code"] == "session"
    forged = put(owner, {"kind": "drawings", "key": "NSE:INFY", "base": 0, "body": LEVEL},
                 {"X-CSRFToken": "forged"})  # fmt: skip
    assert forged.status_code == 401 and forged.get_json()["code"] == "session"
    assert store.read(OWNER, "drawings", "NSE:INFY") is None


# -- the switch ----------------------------------------------------------------------------------
def test_off_the_state_says_so_and_the_documents_are_off_with_nothing_opened(
    users, monkeypatch, chart_db
):
    monkeypatch.delenv(bp_module.SWITCH_ENV, raising=False)
    client = make_app().test_client()
    login_session(client)
    state = client.get(f"{API}/state")
    assert state.status_code == 200 and state.get_json() == {"status": "ok",
                                                             "data": {"enabled": False}}  # fmt: skip
    for method, path in ROUTES[1:]:
        refused = call(client, method, path, headers=csrf_headers(client))
        assert refused.status_code == 503
        assert refused.get_json() == {"status": "error", "code": "off", "message": MESSAGES["off"]}
    assert not chart_db.exists()


@pytest.mark.parametrize("value", ["on", "'on'", "ON", " true ", "1", "yes"])
def test_the_switch_reads_its_on_words(monkeypatch, value):
    monkeypatch.setenv(bp_module.SWITCH_ENV, value)
    assert bp_module.enabled()


@pytest.mark.parametrize("value", ["off", "", "0", "no", "sandbox"])
def test_anything_else_is_off(monkeypatch, value):
    monkeypatch.setenv(bp_module.SWITCH_ENV, value)
    assert not bp_module.enabled()


def test_on_the_state_names_the_limits(owner):
    data = owner.get(f"{API}/state").get_json()["data"]
    assert data == {"enabled": True, "max_bytes": schema.MAX_BODY_BYTES,
                    "limits": dict(store.LIMITS)}  # fmt: skip


# -- documents through the routes ----------------------------------------------------------------
def test_a_document_is_written_read_and_read_again_without_its_body(owner):
    assert owner.get(f"{DOC}?kind=drawings&key=NSE:INFY").get_json() == {
        "status": "ok", "data": {"doc": None}}  # fmt: skip
    saved = put(owner, {"kind": "drawings", "key": "NSE:INFY", "base": 0, "body": LEVEL})
    assert saved.status_code == 200 and saved.headers["Cache-Control"] == "no-store"
    doc = saved.get_json()["data"]["doc"]
    assert set(doc) == {"kind", "key", "revision", "updated_ms"} and doc["revision"] >= 1
    got = owner.get(f"{DOC}?kind=drawings&key=NSE:INFY").get_json()["data"]["doc"]
    assert got == {**doc, "body": LEVEL}
    held = owner.get(f"{DOC}?kind=drawings&key=NSE:INFY&have={doc['revision']}").get_json()
    assert held["data"]["doc"] == doc
    assert store.read(OWNER, "drawings", "NSE:INFY").revision == doc["revision"]  # the owner's


def test_a_stale_write_is_refused_with_the_newer_document_to_show(owner):
    first = put(owner, {"kind": "drawings", "key": "NSE:INFY", "base": 0, "body": LEVEL})
    base = first.get_json()["data"]["doc"]["revision"]
    newer = {"version": 2, "drawings": [{"id": "from-the-tablet"}]}
    second = put(owner, {"kind": "drawings", "key": "NSE:INFY", "base": base, "body": newer})
    refused = put(owner, {"kind": "drawings", "key": "NSE:INFY", "base": base, "body": LEVEL})
    assert refused.status_code == 409
    answer = refused.get_json()
    assert (answer["status"], answer["code"], answer["message"]) == ("error", "stale",
                                                                     MESSAGES["stale"])  # fmt: skip
    assert answer["doc"] == {**second.get_json()["data"]["doc"], "body": newer}
    gone = put(owner, {"kind": "phone_set", "key": "phone", "base": 77,
                       "body": {"version": 1, "studies": []}})  # fmt: skip
    assert gone.status_code == 409 and gone.get_json()["doc"] is None


def test_a_new_key_past_its_kinds_limit_is_refused_in_words(owner, monkeypatch):
    monkeypatch.setitem(store.LIMITS, "workspace", 1)
    body = {"version": 1, "revision": 1, "workspaces": [], "templates": []}
    assert put(owner, {"kind": "workspace", "key": "a", "base": 0, "body": body}).status_code == 200
    refused = put(owner, {"kind": "workspace", "key": "b", "base": 0, "body": body})
    assert refused.status_code == 422
    assert refused.get_json() == {"status": "error", "code": "limit", "message": MESSAGES["limit"]}


@pytest.mark.parametrize(
    "query, field",
    [("kind=boxes&key=a", "kind"), ("key=a", "kind"), ("kind=drawings", "key"),
     ("kind=drawings&key=a b", "key"), ("kind=drawings&key=a&have=x", "have"),
     ("kind=drawings&key=a&have=-1", "have")],
)  # fmt: skip
def test_a_read_that_is_not_understood_names_its_field(owner, query, field):
    refused = owner.get(f"{DOC}?{query}")
    assert refused.status_code == 400
    assert refused.get_json() == {"status": "error", "code": "invalid",
                                  "message": MESSAGES["invalid"], "field": field}  # fmt: skip


@pytest.mark.parametrize(
    "payload, field",
    [
        ({"kind": "boxes", "key": "a", "base": 0, "body": LEVEL}, "kind"),
        ({"kind": "drawings", "key": "", "base": 0, "body": LEVEL}, "key"),
        ({"kind": "drawings", "key": "a", "base": -1, "body": LEVEL}, "base"),
        ({"kind": "drawings", "key": "a", "base": True, "body": LEVEL}, "base"),
        ({"kind": "drawings", "key": "a", "base": "0", "body": LEVEL}, "base"),
        ({"kind": "drawings", "key": "a", "base": 2**53, "body": LEVEL}, "base"),
        ({"kind": "drawings", "key": "a", "base": 0}, "body"),
        ({"kind": "drawings", "key": "a", "base": 0, "body": {"version": 2}}, "body"),
        ([1, 2], "body"),
    ],
)
def test_a_write_that_is_not_understood_names_its_field_and_changes_nothing(owner, payload, field):
    refused = put(owner, payload)
    assert refused.status_code == 400 and refused.get_json()["field"] == field
    assert store.read(OWNER, "drawings", "a") is None


@pytest.mark.parametrize("raw", [b"", b"{not json", b"[" * 100_000, b"NaN"])
def test_a_body_that_does_not_parse_is_refused_as_not_understood(owner, raw):
    refused = owner.put(DOC, data=raw, content_type="application/json",
                        headers=csrf_headers(owner))  # fmt: skip
    assert refused.status_code == 400 and refused.get_json()["field"] == "body"


def test_a_request_past_the_bound_is_refused_from_its_length_before_it_is_parsed(
    owner, monkeypatch
):
    def never(*args, **kwargs):
        raise AssertionError("a body past the bound was parsed")

    monkeypatch.setattr(bp_module, "json", types.SimpleNamespace(loads=never, dumps=json.dumps))
    raw = b'{"kind": "template", "key": "t", "base": 0, "body": {"version": 1, "s": "'
    raw += b"a" * bp_module.MAX_REQUEST_BYTES + b'"}}'
    refused = owner.put(DOC, data=raw, content_type="application/json",
                        headers=csrf_headers(owner))  # fmt: skip
    assert refused.status_code == 413
    assert refused.get_json() == {"status": "error", "code": "too_big",
                                  "message": MESSAGES["too_big"]}  # fmt: skip


def test_a_body_inside_the_request_bound_but_past_the_engines_limit_is_too_big(owner):
    body = {"version": 1, "s": "a" * schema.MAX_BODY_BYTES}
    refused = put(owner, {"kind": "template", "key": "t", "base": 0, "body": body})
    assert refused.status_code == 413 and refused.get_json()["code"] == "too_big"
    assert store.read(OWNER, "template", "t") is None


# -- the record, the limits and failures -----------------------------------------------------------
def test_a_record_from_a_newer_release_is_answered_in_words(owner, chart_db):
    store.ready()
    store.dispose()
    import sqlite3
    from contextlib import closing

    with closing(sqlite3.connect(chart_db)) as db, db:
        db.execute("UPDATE chart_meta SET value = '9' WHERE name = 'schema'")
    refused = owner.get(f"{DOC}?kind=drawings&key=NSE:INFY")
    assert refused.status_code == 503
    assert refused.get_json() == {"status": "error", "code": "record", "message": RECORD_NEWER}
    assert (
        owner.get(f"{API}/state").get_json()["data"]["enabled"] is True
    )  # the state opens nothing


def test_a_failure_is_the_envelope_never_the_apps_error_page(owner, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("disk went away")

    monkeypatch.setattr(store, "read", broken)
    failed = owner.get(f"{DOC}?kind=drawings&key=NSE:INFY")
    assert failed.status_code == 500
    assert failed.get_json() == {"status": "error", "code": "failed", "message": MESSAGES["failed"]}


def test_each_class_has_its_limit_counted_for_the_owner_only(users, switch):
    reset_limits = make_app(rate_limits=True)
    viewer = reset_limits.test_client()
    login_session(viewer, VIEWER)
    for _ in range(5):  # refused before the limit: never counted
        assert viewer.get(f"{API}/state").status_code == 403
    client = reset_limits.test_client()
    login_session(client)
    allowed = int(bp_module.LIMITS["read"].split()[0])
    codes = [client.get(f"{API}/state").status_code for _ in range(allowed + 1)]
    assert codes[:allowed] == [200] * allowed and codes[-1] == 429
    busy = client.get(f"{DOC}?kind=drawings&key=NSE:INFY")  # the same class, shared
    assert busy.get_json() == {"status": "error", "code": "busy", "message": MESSAGES["busy"]}
    written = put(client, {"kind": "drawings", "key": "NSE:INFY", "base": 0, "body": LEVEL})
    assert written.status_code == 200  # writes are a class of their own


def test_requests_through_the_routes_leave_no_descriptor_open(owner):
    first = put(owner, {"kind": "drawings", "key": "NSE:INFY", "base": 0, "body": LEVEL})
    revision = first.get_json()["data"]["doc"]["revision"]
    headers = csrf_headers(owner)
    watch = FdWatch()
    for _ in range(60):
        saved = put(owner, {"kind": "drawings", "key": "NSE:INFY", "base": revision,
                            "body": LEVEL}, headers)  # fmt: skip
        revision = saved.get_json()["data"]["doc"]["revision"]
        assert owner.get(f"{DOC}?kind=drawings&key=NSE:INFY").status_code == 200
        assert put(owner, {"kind": "drawings", "key": "NSE:INFY", "base": 1, "body": LEVEL},
                   headers).status_code == 409  # fmt: skip
    watch.assert_clean()


# -- registered once in app.py -------------------------------------------------------------------
def test_the_bridge_is_imported_and_registered_once_in_app_py_with_its_mark():
    source = (REPO / "app.py").read_text(encoding="utf-8")
    assert source.count("from blueprints.sudarshan_charts import sudarshan_charts_bp") == 1
    lines = [
        line for line in source.splitlines() if "register_blueprint(sudarshan_charts_bp)" in line
    ]
    assert len(lines) == 1 and "# SUDARSHAN M4.18" in lines[0]


def test_the_migration_is_listed_in_migrate_all_and_not_required():
    source = (REPO / "upgrade" / "migrate_all.py").read_text(encoding="utf-8")
    assert source.count('("migrate_sudarshan_charts.py",') == 1
    required = source[source.index("REQUIRED_MIGRATIONS") :]
    assert "migrate_sudarshan_charts.py" not in required
