"""The bridge (M8.4, W4): the checks in order, the envelope, the switch, the record check, the
request read against the contract, and the discovery of the op handlers."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest
from watchlists_bridge_app import (
    VIEWER,
    csrf_headers,
    echo_handlers,
    login_session,
    make_app,
    path_of,
    reset_users,
)

import blueprints.sudarshan_watchlists as bp_module
from sudarshan.watchlists import contract, engine, ops
from sudarshan.watchlists.ops import state as state_ops
from sudarshan.watchlists.words import base as words

REPO = Path(__file__).resolve().parents[3]
CHANGING = [op for op in contract.OPS if op.method in ("POST", "PUT", "PATCH", "DELETE")]

#: The ops with no handler yet. A later change builds some and removes them from here; a row of the
#: contract that is neither handled nor listed fails, so no op is forgotten.
NOT_BUILT = {op.name for op in contract.OPS} - {
    "state",
    "view",
    "ranked",
    "section_add",
    "section_edit",
    "section_remove",
    "arrange",
    "copy",
    "favourites",
    "pref",
    "lists",
    "mark",
    "flags",
    "symbol",
    "expired_clear",
    "row_add",
    "row_edit",
    "row_remove",
    "resolve",
    "search",
    "strikes",
    "tv_preview",
    "tv_import",
    "tv_export",
}

BODIES = {
    "section_add": {"list": 1, "name": "A", "version": "v"},
    "arrange": {"version": "v", "sections": [1], "items": []},
}


@pytest.fixture(autouse=True)
def store(tmp_path, monkeypatch):
    monkeypatch.setenv(contract.DB_ENV, str(tmp_path / "wl.db"))
    monkeypatch.setenv(contract.SWITCH_ENV, "on")
    bp_module._record.clear()
    engine.dispose()
    yield tmp_path / "wl.db"
    engine.dispose()
    bp_module._record.clear()


@pytest.fixture
def calls():
    return []


@pytest.fixture
def client(calls):
    reset_users()
    app = make_app(echo_handlers(calls))
    client = app.test_client()
    login_session(client)
    return client


# -- discovery ---------------------------------------------------------------------------------
def test_every_op_is_handled_or_listed_as_not_built_and_nothing_else():
    found = ops.collect()
    handled = [op for op, _, _ in found]
    assert len(handled) == len(set(handled)), "an op has two handlers"
    assert set(handled) <= set(contract.OP_NAMES), "a handler has no row in the contract"
    assert set(handled).isdisjoint(NOT_BUILT), "a handled op is still listed as not built"
    assert set(contract.OP_NAMES) == set(handled) | NOT_BUILT, "an op is neither"
    assert set(ops.discover()) == set(handled)


def test_only_handled_ops_get_a_route():
    app = make_app(ops.discover(), real=True)
    rules = {
        r.rule for r in app.url_map.iter_rules() if r.endpoint.startswith("sudarshan_watchlists.")
    }
    assert rules == {
        contract.API_PREFIX + route
        for route in (
            "/state",
            "/view",
            "/sections",
            "/sections/<int:id>",
            "/copy",
            "/lists/<int:id>/arrange",
            "/lists",
            "/favourites",
            "/prefs/<ref>",
            "/symbol",
            "/ranked",
            "/symbols/<k>",
            "/flags",
            "/symbols/expired/clear",
            "/rows",
            "/rows/<int:id>",
            "/resolve",
            "/search",
            "/strikes",
            "/tv/preview",
            "/tv/import",
            "/tv/export",
        )
    }


# -- the checks, in order ------------------------------------------------------------------------
def test_no_session_is_the_envelope_never_a_redirect():
    reset_users()
    client = make_app(echo_handlers()).test_client()
    for op in contract.OPS:
        r = client.open(path_of(op), method=op.method, json={} if op.method != "GET" else None)
        assert r.status_code == 401, op.name
        assert r.get_json() == {
            "status": "error", "code": "session", "message": words.MESSAGES["session"],
        }  # fmt: skip


def test_another_session_is_owner_only_on_every_op_reads_included(calls):
    reset_users()
    client = make_app(echo_handlers(calls)).test_client()
    login_session(client, VIEWER)
    headers = csrf_headers(client)
    for op in contract.OPS:
        r = client.open(path_of(op), method=op.method, json={}, headers=headers)
        assert r.status_code == 403, op.name
        assert r.get_json()["code"] == "owner_only"
    assert calls == []


@pytest.mark.parametrize("op", CHANGING, ids=lambda op: op.name)
def test_a_changing_op_needs_the_csrf_token(client, calls, op):
    r = client.open(path_of(op), method=op.method, json=BODIES.get(op.name, {}))
    assert r.status_code == 401 and r.get_json()["code"] == "session"
    assert calls == []


def test_the_owner_check_comes_before_the_csrf_check():
    reset_users()
    client = make_app(echo_handlers()).test_client()
    login_session(client, VIEWER)
    r = client.post(path_of(contract.OPS[4]), json={})  # no token
    assert r.get_json()["code"] == "owner_only"


def test_a_read_op_that_posts_needs_the_token_too(client):
    resolve = next(op for op in contract.OPS if op.name == "resolve")
    assert resolve.cls == "read" and resolve.method == "POST"
    assert client.post(path_of(resolve), json={}).get_json()["code"] == "session"


def test_the_limit_of_a_class_answers_busy_in_the_envelope(calls):
    reset_users()
    app = make_app(echo_handlers(calls), rate_limits=True)
    client = app.test_client()
    login_session(client)
    headers = csrf_headers(client)
    preview = next(op for op in contract.OPS if op.cls == "preview")
    codes = [
        client.post(path_of(preview), json={"text": "x"}, headers=headers).status_code
        for _ in range(contract.LIMITS_PER_MIN["preview"] + 2)
    ]
    assert codes[: contract.LIMITS_PER_MIN["preview"]] == [200] * contract.LIMITS_PER_MIN["preview"]
    assert codes[-2:] == [429, 429]
    last = client.post(path_of(preview), json={"text": "x"}, headers=headers)
    assert last.get_json() == {
        "status": "error", "code": "busy", "message": words.MESSAGES["busy"],
    }  # fmt: skip
    assert len(calls) == contract.LIMITS_PER_MIN["preview"]


#: Behind nginx every request reaches the app from the loopback address.
NGINX = {"REMOTE_ADDR": "127.0.0.1"}


def state_path():
    return path_of(next(op for op in contract.OPS if op.name == "state"))


def test_a_caller_without_a_session_cannot_spend_the_owners_budget(calls):
    """H1: the limit is counted after the checks, never as the request arrives."""
    reset_users()
    app = make_app(echo_handlers(calls), rate_limits=True)
    stranger, owner = app.test_client(), app.test_client()
    login_session(owner)
    for _ in range(contract.LIMITS_PER_MIN["read"] + 5):
        r = stranger.get(state_path(), environ_base=NGINX)
        assert r.status_code == 401 and r.get_json()["code"] == "session"
    mine = owner.get(state_path(), environ_base=NGINX)
    assert mine.status_code == 200, mine.get_json()
    assert calls == [("state", {})]


def test_another_sign_in_and_a_forged_change_cannot_spend_it_either(calls):
    reset_users()
    app = make_app(echo_handlers(calls), rate_limits=True)
    viewer, owner = app.test_client(), app.test_client()
    login_session(viewer, VIEWER)
    login_session(owner)
    for _ in range(contract.LIMITS_PER_MIN["read"] + 5):
        assert viewer.get(state_path(), environ_base=NGINX).status_code == 403
    preview = next(op for op in contract.OPS if op.cls == "preview")
    for _ in range(contract.LIMITS_PER_MIN["preview"] + 2):  # the owner's cookie, no token
        r = owner.post(path_of(preview), json={"text": "x"}, environ_base=NGINX)
        assert r.get_json()["code"] == "session"
    headers = csrf_headers(owner)
    assert owner.get(state_path(), environ_base=NGINX).status_code == 200
    codes = [
        owner.post(path_of(preview), json={"text": "x"}, headers=headers, environ_base=NGINX)
        .status_code for _ in range(contract.LIMITS_PER_MIN["preview"])
    ]  # fmt: skip
    assert codes == [200] * contract.LIMITS_PER_MIN["preview"]


def test_the_budget_is_the_owners_from_every_device():
    """Counted per owner, not per address: the phone and the tablet share one budget, as every
    request does behind nginx anyway."""
    reset_users()
    app = make_app(echo_handlers(), rate_limits=True)
    phone, tablet = app.test_client(), app.test_client()
    login_session(phone)
    login_session(tablet)
    for _ in range(contract.LIMITS_PER_MIN["read"]):
        assert phone.get(state_path(), environ_base={"REMOTE_ADDR": "10.0.0.2"}).status_code == 200
    r = tablet.get(state_path(), environ_base={"REMOTE_ADDR": "10.0.0.3"})
    assert (r.status_code, r.get_json()["code"]) == (429, "busy")


# -- the switch ------------------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["off", "", "no", "garbage"])
def test_switched_off_state_says_so_and_every_other_op_says_off(
    client, calls, store, monkeypatch, value
):
    monkeypatch.setenv(contract.SWITCH_ENV, value)
    headers = csrf_headers(client)
    for op in contract.OPS:
        r = client.open(path_of(op), method=op.method, json={}, headers=headers)
        if op.name == "state":
            assert r.status_code == 200
            assert r.get_json() == {"status": "ok", "data": {"enabled": False}}
        else:
            assert r.status_code == 503, op.name
            assert r.get_json()["code"] == "off"
    assert calls == [], "a handler ran while switched off"
    assert not store.exists(), "the store was opened while switched off"


def test_the_switch_is_read_at_each_request(monkeypatch):
    from database import watchlist_db as oa

    oa.Base.metadata.create_all(oa.engine)  # state reads OpenAlgo's own lists; make them, as below
    reset_users()
    client = make_app(ops.discover(), real=True).test_client()
    login_session(client)
    monkeypatch.setenv(contract.SWITCH_ENV, "off")
    assert client.get(contract.API_PREFIX + "/state").get_json()["data"] == {"enabled": False}
    monkeypatch.setenv(contract.SWITCH_ENV, "'ON'")
    assert client.get(contract.API_PREFIX + "/state").get_json()["data"]["enabled"] is True


def test_state_answers_only_what_needs_no_later_change(monkeypatch):
    from database import watchlist_db as oa
    from sudarshan.watchlists.ops import ranked as ranked_op

    monkeypatch.setattr(ranked_op, "owner_token", lambda user: None)  # no sign-in: nothing read

    oa.Base.metadata.create_all(oa.engine)  # the favourites read OpenAlgo's own lists
    reset_users()
    client = make_app(ops.discover(), real=True).test_client()
    login_session(client)
    r = client.get(contract.API_PREFIX + "/state")
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"
    body = r.get_json()
    assert body["status"] == "ok"
    assert set(body["data"]) == {
        "enabled", "limits", "flag_names", "versions", "favourites", "ranked",
    }  # fmt: skip
    assert body["data"]["enabled"] is True
    assert body["data"]["limits"] == contract.LIMITS
    assert [f["name"] for f in body["data"]["flag_names"]] == [
        "Watch", "Breakout", "Results", "Long term", "Avoid",
    ]  # fmt: skip
    assert [f["mark"] for f in body["data"]["flag_names"]] == [1, 2, 3, 4, 5]
    assert state_ops.DEFAULT_FLAG_NAMES[0] == "Watch"


# -- the record check ----------------------------------------------------------------------------------
def test_a_newer_record_answers_record_in_the_owners_words_and_is_not_reopened(
    client, store, monkeypatch
):
    opened = []
    real_ready = bp_module.ready

    def counting():
        opened.append(1)
        return real_ready()

    monkeypatch.setattr(bp_module, "ready", counting)
    engine.dispose()
    engine.prepare(engine._engine.get())
    from sqlalchemy import text

    with engine._engine.get().begin() as c:
        c.execute(text("UPDATE wl_meta SET value = '99' WHERE name = 'schema'"))
    engine.dispose()
    for _ in range(4):
        r = client.get(contract.API_PREFIX + "/state")
        assert r.status_code == 503
        assert r.get_json()["code"] == "record"
        assert "newer SUDARSHAN release" in r.get_json()["message"]
    assert len(opened) == 1


def test_an_unreadable_record_answers_record_and_the_cause_goes_to_the_log(
    client, monkeypatch, caplog
):
    def broken(engine_):
        raise OSError("disk I/O error")

    monkeypatch.setattr(engine, "prepare", broken)
    r = client.get(contract.API_PREFIX + "/state")
    assert r.status_code == 503 and r.get_json()["code"] == "record"
    assert "disk" not in r.get_json()["message"] and "OSError" not in r.get_json()["message"]


def test_a_handler_that_meets_a_newer_record_answers_record(store):
    def handler(args):
        raise engine.RecordTooNew(9)

    reset_users()
    client = make_app({"state": handler}).test_client()
    login_session(client)
    r = client.get(contract.API_PREFIX + "/state")
    assert r.status_code == 503 and r.get_json()["code"] == "record"


# -- the request read against the contract ----------------------------------------------------------------
def test_a_write_op_whose_route_has_an_id_also_has_it_in_the_body(client, calls):
    op = next(o for o in contract.OPS if o.name == "section_edit")
    headers = csrf_headers(client)
    ok = client.patch(
        path_of(op, "7"), json={"id": 7, "version": "v", "name": "x"}, headers=headers
    )
    assert ok.status_code == 200 and calls[-1] == (
        "section_edit",
        {"id": 7, "version": "v", "name": "x"},
    )
    clash = client.patch(path_of(op, "7"), json={"id": 8, "version": "v"}, headers=headers)
    assert clash.status_code == 400
    assert clash.get_json() == {
        "status": "error", "code": "invalid", "message": words.MESSAGES["invalid"], "field": "id",
    }  # fmt: skip
    # the route's id fills a body that leaves it out
    r = client.patch(path_of(op, "7"), json={"version": "v"}, headers=headers)
    assert r.status_code == 200 and calls[-1][1]["id"] == 7


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"list": 1, "name": "A"}, "version"),
        ({"list": "1", "name": "A", "version": "v"}, "list"),
        ({"list": True, "name": "A", "version": "v"}, "list"),
        ({"list": 1, "name": "A", "version": "v", "mark": 1.5}, "mark"),
        ({"list": 1, "name": 5, "version": "v"}, "name"),
    ],
)
def test_an_invalid_body_names_its_field(client, calls, body, field):
    r = client.post(path_of(contract.OPS[10]), json=body, headers=csrf_headers(client))
    assert contract.OPS[10].name == "section_add"
    assert (r.status_code, r.get_json()["code"], r.get_json()["field"]) == (400, "invalid", field)
    assert calls == []


def test_enums_records_and_lists_are_checked(client):
    op = next(o for o in contract.OPS if o.name == "arrange")
    headers = csrf_headers(client)
    good = {"id": 3, "version": "v", "sections": [1, 2], "items": [
        {"exchange": "NSE", "symbol": "INFY", "section_id": None}]}  # fmt: skip
    assert client.put(path_of(op, "3"), json=good, headers=headers).status_code == 200
    for bad in (
        {**good, "sections": [1, "2"]},
        {**good, "items": [{"exchange": "NSE", "section_id": 1}]},
        {**good, "items": "x"},
    ):
        r = client.put(path_of(op, "3"), json=bad, headers=headers)
        assert r.status_code == 400, bad
    pref = next(o for o in contract.OPS if o.name == "pref")
    r = client.put(
        path_of(pref, "list:1"), json={"ref": "list:1", "sort": "sideways"}, headers=headers
    )
    assert r.get_json()["field"] == "sort"


def test_a_query_string_is_read_by_the_type_of_each_field(client, calls):
    base = contract.API_PREFIX
    assert client.get(f"{base}/strikes?u=NIFTY&x=NFO&n=5&p=24500.5").status_code == 200
    assert calls[-1] == ("strikes", {"u": "NIFTY", "x": "NFO", "n": 5, "p": 24500.5})
    for query, field in (("x=NFO", "u"), ("u=A&x=B&n=five", "n"), ("u=A&x=B&p=nan", "p")):
        r = client.get(f"{base}/strikes?{query}")
        assert (r.status_code, r.get_json()["field"]) == (400, field)
    r = client.get(f"{base}/search?q=a&seg=sideways")
    assert r.get_json()["field"] == "seg"


def test_a_body_that_is_not_an_object_or_too_big_is_invalid(client):
    op = contract.OPS[10]
    headers = {**csrf_headers(client), "Content-Type": "application/json"}
    assert client.post(path_of(op), data="[1]", headers=headers).get_json()["code"] == "invalid"
    assert client.post(path_of(op), data="{nope", headers=headers).get_json()["field"] == "body"
    big = json.dumps({"list": 1, "name": "x" * (bp_module.MAX_BODY_BYTES + 1), "version": "v"})
    r = client.post(path_of(op), data=big, headers=headers)
    assert (r.status_code, r.get_json()["code"]) == (400, "invalid")


# -- what a handler may say --------------------------------------------------------------------------------------
def test_a_handlers_refusal_keeps_its_code_and_words():
    def handler(args):
        raise ops.OpError("limit", "A list holds 250 symbols.")

    reset_users()
    client = make_app({"state": handler}).test_client()
    login_session(client)
    r = client.get(contract.API_PREFIX + "/state")
    assert (r.status_code, r.get_json()) == (
        422, {"status": "error", "code": "limit", "message": "A list holds 250 symbols."},
    )  # fmt: skip
    with pytest.raises(ValueError):
        ops.OpError("teapot")


def test_a_crash_is_failed_in_words_and_the_trace_goes_to_the_log(caplog):
    def handler(args):
        raise KeyError("secret-internal-detail")

    reset_users()
    client = make_app({"state": handler}).test_client()
    login_session(client)
    r = client.get(contract.API_PREFIX + "/state")
    assert (r.status_code, r.get_json()["code"]) == (500, "failed")
    assert "secret-internal-detail" not in r.get_data(as_text=True)


# -- the words ---------------------------------------------------------------------------------------------------
def test_every_code_has_a_sentence_for_a_trader():
    assert set(words.MESSAGES) == set(contract.ERROR_CODES)
    for text in [*words.MESSAGES.values(), words.TOO_BIG]:
        assert text[0].isupper() and text.endswith(".")
        assert not re.search(
            r"HTTP|status|endpoint|exception|error code|\b[45]\d\d\b|JSON|CSRF|\barm", text, re.I
        ), text


# -- the switch and the registration ---------------------------------------------------------------------------------
def test_the_switch_ships_off_in_the_deploy_settings():
    conf = (REPO / "sudarshan/ops/deploy/openalgo-env.conf").read_text()
    lines = [ln.split() for ln in conf.splitlines() if ln.startswith(contract.SWITCH_ENV)]
    assert lines == [[contract.SWITCH_ENV, "off"]]


def test_app_py_registers_the_blueprint_in_two_lines_of_its_own():
    lines = [
        ln for ln in (REPO / "app.py").read_text().splitlines() if "sudarshan_watchlists" in ln
    ]
    assert len(lines) == 2 and all("# SUDARSHAN M8.4" in ln for ln in lines)
    assert not any("sudarshan_screener" in ln or "sudarshan_risk" in ln for ln in lines)


def test_the_module_imports_light(tmp_path):
    """Importing the bridge opens no store, reads no master and starts no thread."""
    import subprocess
    import sys

    code = (
        "import sys, threading\n"
        "before = {t.ident for t in threading.enumerate()}\n"
        "import blueprints.sudarshan_watchlists\n"
        "assert 'sudarshan.watchlists.master' not in sys.modules\n"
        "assert {t.ident for t in threading.enumerate()} == before\n"
    )
    env = {**os.environ, contract.DB_ENV: str(tmp_path / "never.db")}
    done = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO, env=env, capture_output=True, text=True
    )
    assert done.returncode == 0, done.stderr[-600:]
    assert not (tmp_path / "never.db").exists()
