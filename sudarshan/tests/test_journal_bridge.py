"""The journal's bridge in OpenAlgo (M14.1, JC1): owner routes, CSRF, limiters, the seal, the words
for every way the service can fail, and the download's headers.

sudarshan-journal is built by another lane at the same time; the service here is the stand-in in
journal_bridge_app.py, which opens each request with the real JOURNAL_API_INFO key.
"""

from __future__ import annotations

import base64
import hashlib
import importlib
import json
import os
import re
import socket
import time
import types
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet, InvalidToken
from journal_bridge_app import (
    OWNER,
    VIEWER,
    FakeJournal,
    csrf_headers,
    login_session,
    make_app,
    reset_users,
)

import blueprints.sudarshan_journal as bp_module
from sudarshan.tests.fdwatch import FdWatch
from sudarshan.vault import JOURNAL_API_INFO, JOURNAL_DELIVERY_INFO, PORTFOLIO_API_INFO, fernet_for
from utils.thread_safe_cache import LockedTTLCache

REPO = Path(__file__).resolve().parents[2]
JSON = {"Accept": "application/json"}
PREFIX = bp_module.API_PREFIX
WORDS = bp_module.WORDS
OPS = bp_module.OPS
PDF = b"%PDF-1.7\n1 0 obj\n<< >>\nendobj\n%%EOF\n"


def path_of(spec, ident: int = 7) -> str:
    return PREFIX + spec.route.replace("<id>", str(ident))


def body_of(spec):
    return None if spec.method == "GET" else {"version": 1}


@pytest.fixture(scope="module", autouse=True)
def _users():
    reset_users()  # once a module: each user's password hash costs most of a second


@pytest.fixture
def journal(monkeypatch):
    with FakeJournal() as running:
        monkeypatch.setenv("SUDARSHAN_JOURNAL_API_URL", running.url)
        yield running


@pytest.fixture
def bridge(monkeypatch):
    http = httpx.Client(trust_env=False)
    monkeypatch.setattr(bp_module, "get_httpx_client", lambda: http)
    monkeypatch.setattr(bp_module, "_warned", LockedTTLCache(maxsize=8, ttl=600))
    yield make_app().test_client()
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
    assert body["status"] == "error", body
    return body["error"]


# -- the table ---------------------------------------------------------------------------------


def test_every_op_of_the_table_has_its_route():
    app = make_app()
    rules = {
        (
            rule.endpoint.rpartition(".")[2],
            rule.rule,
            tuple(sorted(rule.methods - {"HEAD", "OPTIONS"})),
        )
        for rule in app.url_map.iter_rules()
        if rule.endpoint.startswith("sudarshan_journal.")
    }
    expected = {
        (spec.name, PREFIX + spec.route.replace("<id>", "<int:id>"), (spec.method,)) for spec in OPS
    }
    assert rules == expected
    assert {spec.name for spec in OPS} == {
        "state", "trips", "trip_get", "trip_update", "tags_list", "tag_create", "tag_update",
        "playbooks_list", "playbook_create", "playbook_update", "analytics", "reports_list",
        "report_get", "report_file", "report_build", "settings_get", "settings_set",
        "reconcile_now",
    }  # fmt: skip


def test_the_table_matches_the_journal_contract_once_it_exists():
    """The contract (sudarshan/journal/core/kinds.py) is another lane's; when it is in the tree,
    every route of the bridge must be one of its ops, with its method, route and class."""
    try:
        kinds = importlib.import_module("sudarshan.journal.core.kinds")
    except ModuleNotFoundError:
        pytest.skip("the journal's contract is not in this tree yet")
    contract = {(s.name, s.method, s.route, s.cls) for s in kinds.OPS}
    assert {(s.name, s.method, s.route, s.cls) for s in OPS} == contract


def test_app_registers_the_blueprint_with_two_marked_lines_after_the_other_bridges():
    text = (REPO / "app.py").read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if "sudarshan_journal" in line]
    assert len(lines) == 2 and all("SUDARSHAN" in line for line in lines)
    registered = re.findall(r"app\.register_blueprint\((sudarshan_\w+)\)", text)
    for earlier in ("autologin", "alerts", "risk", "portfolio", "screener"):
        assert registered.index(f"sudarshan_{earlier}_bp") < registered.index(
            "sudarshan_journal_bp"
        )


def test_the_service_url_is_always_this_server(monkeypatch):
    monkeypatch.setenv("SUDARSHAN_JOURNAL_API_URL", "http://example.invalid:5586")
    assert bp_module._service_url() == "http://127.0.0.1:5586"
    monkeypatch.delenv("SUDARSHAN_JOURNAL_API_URL")
    monkeypatch.setenv("SUDARSHAN_JOURNAL_HTTP_PORT", "6586")
    assert bp_module._service_url() == "http://127.0.0.1:6586"


# -- who may call ------------------------------------------------------------------------------


@pytest.mark.parametrize("spec", OPS, ids=lambda spec: spec.name)
def test_no_session_is_401_in_words(bridge, journal, spec):
    headers = csrf_headers(bridge)  # a write meets CSRF first; an anonymous page has a token
    response = send(bridge, spec, headers, json=body_of(spec))
    assert response.status_code == 401
    assert error_of(response) == {"code": "unauthorized", "message": WORDS["unauthorized"]}
    assert journal.calls == []


@pytest.mark.parametrize("spec", OPS, ids=lambda spec: spec.name)
def test_a_forged_non_owner_session_is_403_on_every_route(bridge, journal, spec):
    login_session(bridge, VIEWER)  # signed with the app's key, as a family viewer's would be
    headers = csrf_headers(bridge)
    response = send(bridge, spec, headers, json=body_of(spec))
    assert response.status_code == 403
    assert error_of(response) == {"code": "forbidden", "message": WORDS["forbidden"]}
    assert journal.calls == []  # nothing was sent to the service


def test_a_session_for_a_user_that_does_not_exist_is_403(bridge, journal):
    login_session(bridge, "nobody-at-all")
    response = bridge.get(PREFIX + "/state", headers=JSON)
    assert response.status_code == 403 and journal.calls == []


@pytest.mark.parametrize("spec", [s for s in OPS if s.method != "GET"], ids=lambda s: s.name)
def test_a_write_without_a_csrf_token_never_reaches_the_service(bridge, journal, spec):
    login_session(bridge)
    response = send(bridge, spec, json=body_of(spec))
    assert response.status_code == 400
    assert journal.calls == []


# -- the seal ----------------------------------------------------------------------------------


def test_each_request_is_one_sealed_token_for_its_op_and_the_owner(bridge, journal):
    headers = as_owner(bridge)
    bridge.patch(f"{PREFIX}/trips/12", json={"version": 3, "notes": "late entry"},
                 headers={**JSON, **headers})  # fmt: skip
    bridge.patch(f"{PREFIX}/trips/12", json={"version": 3, "notes": "late entry"},
                 headers={**JSON, **headers})  # fmt: skip
    (first, second) = journal.bodies
    assert b"late entry" not in first  # nothing in clear on the loopback either
    key = fernet_for(os.environ["API_KEY_PEPPER"], JOURNAL_API_INFO)
    message = json.loads(key.decrypt(first, ttl=60))
    assert message["op"] == "trip_update" and message["principal"] == "owner"
    assert message["args"] == {"id": 12, "version": 3, "notes": "late entry"}
    assert len(message["nonce"]) == 32
    assert json.loads(key.decrypt(second))["nonce"] != message["nonce"]  # each one its own
    assert len(journal.calls) == 2


def test_the_seal_is_the_api_purpose_and_no_other(bridge, journal):
    as_owner(bridge)
    bridge.get(PREFIX + "/state", headers=JSON)
    (blob,) = journal.bodies
    for other in (JOURNAL_DELIVERY_INFO, PORTFOLIO_API_INFO):
        with pytest.raises(InvalidToken):
            fernet_for(os.environ["API_KEY_PEPPER"], other).decrypt(blob)


def test_a_service_that_refuses_the_seal_is_told_in_words(bridge, journal):
    journal.fernet = Fernet(Fernet.generate_key())  # a service started with another pepper
    as_owner(bridge)
    response = bridge.get(PREFIX + "/state", headers=JSON)
    assert response.status_code == 503
    assert error_of(response) == {"code": "unavailable", "message": WORDS["sealed"]}


# -- the forward -------------------------------------------------------------------------------


def test_a_read_comes_back_in_the_envelope(bridge, journal):
    as_owner(bridge)
    response = bridge.get(PREFIX + "/state", headers=JSON)
    assert response.status_code == 200
    assert response.get_json() == {
        "status": "success", "enabled": True, "data": {"op": "state", "args": {}},
    }  # fmt: skip


def test_query_values_are_read_as_the_table_says(bridge, journal):
    as_owner(bridge)
    query = "mode=sandbox&status=closed&before=1731500000000&limit=50&strategy=ORB&tag=3"
    response = bridge.get(f"{PREFIX}/trips?{query}&from=20261101&to=20261130&unknown=1",
                          headers=JSON)  # fmt: skip
    assert response.status_code == 200
    assert journal.calls == [("trips", {
        "mode": "sandbox", "status": "closed", "before": 1731500000000, "limit": 50,
        "strategy": "ORB", "tag": 3, "from": 20261101, "to": 20261130,
    })]  # fmt: skip
    bridge.get(f"{PREFIX}/analytics?period=month&expiry=only&playbook=2", headers=JSON)
    bridge.get(f"{PREFIX}/reports?kind=eod&limit=20", headers=JSON)
    assert journal.calls[1:] == [
        ("analytics", {"period": "month", "expiry": "only", "playbook": 2}),
        ("reports_list", {"kind": "eod", "limit": 20}),
    ]


@pytest.mark.parametrize("query", ["limit=ten", "tag=1.5", "strategy=" + "x" * 201])
def test_a_query_that_cannot_be_read_is_told_in_words_and_not_sent(bridge, journal, query):
    as_owner(bridge)
    response = bridge.get(f"{PREFIX}/trips?{query}", headers=JSON)
    assert response.status_code == 400
    assert error_of(response) == {"code": "bad_request", "message": WORDS["unreadable"]}
    assert journal.calls == []


def test_a_write_carries_its_body_and_the_route_names_the_target(bridge, journal):
    headers = as_owner(bridge)
    response = bridge.patch(f"{PREFIX}/tags/5", json={"id": 99, "version": 4, "name": "FOMO"},
                            headers={**JSON, **headers})  # fmt: skip
    assert response.status_code == 200
    assert journal.calls == [("tag_update", {"id": 5, "version": 4, "name": "FOMO"})]


def test_a_body_that_is_not_an_object_or_too_large_is_not_sent(bridge, journal):
    headers = as_owner(bridge)
    listed = bridge.post(PREFIX + "/tags", data=b"[1, 2]", content_type="application/json",
                         headers={**JSON, **headers})  # fmt: skip
    assert error_of(listed) == {"code": "bad_request", "message": WORDS["unreadable"]}
    large = bridge.post(
        PREFIX + "/tags", data=b'{"name":"' + b"x" * (64 * 1024) + b'"}',
        content_type="application/json", headers={**JSON, **headers},
    )  # fmt: skip
    assert large.status_code == 400
    assert error_of(large) == {"code": "bad_request", "message": WORDS["too_big"]}
    assert journal.calls == []


def test_the_service_refusals_come_back_with_their_code_and_words(bridge, journal):
    conflict = (
        "This trade's notes were changed on another screen. Nothing was saved; open it again."
    )
    journal.answers["trip_update"] = (409, {
        "status": "error", "code": "version_conflict", "message": conflict, "field": "notes",
    })  # fmt: skip
    journal.answers["trip_get"] = (404, {
        "status": "error", "code": "not_found", "message": "This trade is not in the journal.",
    })  # fmt: skip
    headers = as_owner(bridge)
    response = bridge.patch(f"{PREFIX}/trips/1", json={"version": 1}, headers={**JSON, **headers})
    assert response.status_code == 409
    assert error_of(response) == {"code": "version_conflict", "message": conflict, "field": "notes"}
    gone = bridge.get(f"{PREFIX}/trips/1", headers=JSON)
    assert gone.status_code == 404
    assert error_of(gone) == {"code": "not_found", "message": "This trade is not in the journal."}


def test_a_transport_refusal_is_never_shown_as_a_code(bridge, journal):
    journal.answers["state"] = (503, {
        "status": "error", "code": "busy",
        "message": "The journal service is busy. Wait a minute, then try again.",
    })  # fmt: skip
    as_owner(bridge)
    response = bridge.get(PREFIX + "/state", headers=JSON)
    assert response.status_code == 503
    assert error_of(response) == {
        "code": "unavailable",
        "message": "The journal service is busy. Wait a minute, then try again.",
    }


@pytest.mark.parametrize("how", ["missing", "unknown"])
def test_an_op_the_service_lacks_is_not_ready(bridge, journal, how):
    getattr(journal, how).add("analytics")
    as_owner(bridge)
    response = bridge.get(PREFIX + "/analytics", headers=JSON)
    assert response.status_code == 501
    assert error_of(response) == {"code": "not_ready", "message": WORDS["not_ready"]}


def test_switched_off_is_a_success_that_says_so_in_the_services_words(bridge, journal):
    journal.answers["state"] = (200, {"status": "success", "data": {"enabled": False}})
    newer = (
        "Your trade journal was written by a newer release. Install that release again; "
        "nothing was changed."
    )
    journal.answers["trips"] = (200, {"status": "success",
                                      "data": {"enabled": False, "message": newer}})  # fmt: skip
    as_owner(bridge)
    off = bridge.get(PREFIX + "/state", headers=JSON)
    assert off.status_code == 200
    assert off.get_json() == {
        "status": "success", "enabled": False, "data": None, "message": WORDS["off"],
    }  # fmt: skip
    assert bridge.get(PREFIX + "/trips", headers=JSON).get_json()["message"] == newer


# -- when the service does not answer: the words from its /health (Y37) -----------------------


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_nothing_listening_is_not_answering_for_reads_and_writes(bridge, monkeypatch):
    monkeypatch.setenv("SUDARSHAN_JOURNAL_API_URL", f"http://127.0.0.1:{free_port()}")
    headers = as_owner(bridge)
    read = bridge.get(PREFIX + "/state", headers=JSON)
    write = bridge.post(PREFIX + "/tags", json={"name": "x"}, headers={**JSON, **headers})
    for response in (read, write):  # never sent, so a write is not "not confirmed"
        assert response.status_code == 503
        assert error_of(response) == {"code": "unavailable", "message": WORDS["not_answering"]}


def slow(seconds: float):
    def answer(message):
        time.sleep(seconds)
        return 200, {"status": "success", "data": {}}

    return answer


def test_a_slow_read_is_not_answering_within_its_timeout(bridge, journal, monkeypatch):
    journal.answers["state"] = slow(1.0)
    monkeypatch.setitem(bp_module.TIMEOUTS_S, "read", 0.2)
    as_owner(bridge)
    start = time.monotonic()
    response = bridge.get(PREFIX + "/state", headers=JSON)
    assert time.monotonic() - start < 0.9
    assert response.status_code == 503
    assert error_of(response)["message"] == WORDS["not_answering"]


def test_a_slow_read_while_health_says_switched_off_says_switched_off(bridge, journal, monkeypatch):
    journal.answers["state"] = slow(1.0)
    journal.health = (200, {"service": "sudarshan-journal",
                            "journal_state": {"enabled": False}})  # fmt: skip
    monkeypatch.setitem(bp_module.TIMEOUTS_S, "read", 0.2)
    as_owner(bridge)
    response = bridge.get(PREFIX + "/state", headers=JSON)
    assert response.get_json() == {
        "status": "success", "enabled": False, "data": None, "message": WORDS["off"],
    }  # fmt: skip
    reason = "The journal cannot open its store. Your fills are kept by Upstox; check the server."
    journal.health = (200, {"journal_state": {"enabled": False, "reason": reason}})
    assert bridge.get(PREFIX + "/state", headers=JSON).get_json()["message"] == reason


def test_health_words_that_are_not_a_plain_sentence_are_not_shown(bridge, journal, monkeypatch):
    journal.answers["state"] = slow(1.0)
    journal.health = (200, {"journal_state": {"enabled": False, "reason": "<b>off</b>\x07"}})
    monkeypatch.setitem(bp_module.TIMEOUTS_S, "read", 0.2)
    as_owner(bridge)
    assert bridge.get(PREFIX + "/state", headers=JSON).get_json()["message"] == WORDS["off"]


def test_a_slow_write_is_not_confirmed_and_is_sent_once(bridge, journal, monkeypatch):
    journal.answers["tag_create"] = slow(1.0)
    monkeypatch.setitem(bp_module.TIMEOUTS_S, "write", 0.2)
    headers = as_owner(bridge)
    response = bridge.post(PREFIX + "/tags", json={"name": "x"}, headers={**JSON, **headers})
    time.sleep(1.1)
    assert response.status_code == 504
    assert error_of(response) == {"code": "not_confirmed", "message": WORDS["not_confirmed"]}
    assert len(journal.calls) == 1  # never retried


def test_an_answer_without_the_envelope_is_not_answering_or_not_confirmed(bridge, journal):
    journal.answers["state"] = (200, {"hello": "world"})
    journal.answers["reconcile_now"] = (200, {"status": "maybe"})
    headers = as_owner(bridge)
    read = bridge.get(PREFIX + "/state", headers=JSON)
    assert error_of(read) == {"code": "unavailable", "message": WORDS["not_answering"]}
    reconcile = bridge.post(PREFIX + "/reconcile", json={}, headers={**JSON, **headers})
    assert error_of(reconcile) == {"code": "not_confirmed", "message": WORDS["not_confirmed"]}


def test_a_port_held_by_another_users_program_is_not_sent_to(bridge, journal, monkeypatch):
    monkeypatch.setattr(
        bp_module.loopback, "holder", lambda url, tables=None: bp_module.loopback.OTHER
    )
    as_owner(bridge)
    response = bridge.get(PREFIX + "/state", headers=JSON)
    assert response.status_code == 503
    assert error_of(response) == {"code": "unavailable", "message": WORDS["held"]}
    assert journal.calls == [] and journal.bodies == []


def test_no_redirect_is_followed_and_each_class_has_its_timeout(bridge, monkeypatch):
    seen = []

    def post(url, **kwargs):
        seen.append(kwargs)
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(bp_module, "get_httpx_client", lambda: types.SimpleNamespace(post=post))
    monkeypatch.setattr(bp_module.loopback, "holder", lambda url: bp_module.loopback.OURS)
    headers = as_owner(bridge)
    bridge.get(PREFIX + "/state", headers=JSON)
    bridge.post(PREFIX + "/tags", json={}, headers={**JSON, **headers})
    bridge.get(PREFIX + "/reports/3/file", headers=JSON)
    bridge.post(PREFIX + "/reconcile", json={}, headers={**JSON, **headers})
    assert [kwargs["timeout"].read for kwargs in seen] == [3.0, 6.0, 20.0, 6.0]
    assert all(k["follow_redirects"] is False and k["timeout"].connect <= 1.0 for k in seen)


# -- the download (Y32) ------------------------------------------------------------------------


def stored(data: bytes = PDF, name: str = "SUDARSHAN_Monthly-statement_202610.pdf",
           mime: str = "application/pdf", sha: str | None = None) -> tuple[int, dict]:  # fmt: skip
    return 200, {"status": "success", "data": {
        "id": 3, "file_name": name, "file_mime": mime,
        "file_sha256": hashlib.sha256(data).hexdigest() if sha is None else sha,
        "file_b64": base64.b64encode(data).decode(),
    }}  # fmt: skip


def test_a_download_is_the_stored_file_with_its_name_type_and_nosniff(bridge, journal):
    journal.answers["report_file"] = stored()
    as_owner(bridge)
    response = bridge.get(PREFIX + "/reports/3/file")
    assert response.status_code == 200
    assert response.data == PDF
    assert response.headers["Content-Type"] == "application/pdf"
    assert response.headers["Content-Disposition"] == (
        'attachment; filename="SUDARSHAN_Monthly-statement_202610.pdf"'
    )
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"
    assert journal.calls == [("report_file", {"id": 3})]


def test_a_csv_keeps_its_type_without_a_charset(bridge, journal):
    data = b"date,net_paise\n20261109,81250\n"
    journal.answers["report_file"] = stored(data, "SUDARSHAN_Monthly-statement_202610.csv",
                                            "text/csv")  # fmt: skip
    as_owner(bridge)
    response = bridge.get(PREFIX + "/reports/3/file")
    assert response.headers["Content-Type"] == "text/csv" and response.data == data


CSV = b"Date,Symbol,Side,Net\n2026-11-09,NIFTY,LONG,812.50\n"
CSV_NAME = "SUDARSHAN_Monthly-statement_202610.csv"


def test_a_monthly_statements_csv_is_asked_for_with_format_and_comes_as_its_own_file(
    bridge, journal
):
    """JB9: the service answers a statement's CSV for format=csv; the bridge forwards exactly
    that and sends it as an attachment under its stored ASCII name, text/csv and nosniff."""
    journal.answers["report_file"] = stored(CSV, CSV_NAME, "text/csv")
    as_owner(bridge)
    response = bridge.get(PREFIX + "/reports/3/file?format=csv")
    assert response.status_code == 200 and response.data == CSV
    assert response.headers["Content-Type"] == "text/csv"
    assert response.headers["Content-Disposition"] == f'attachment; filename="{CSV_NAME}"'
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"
    assert journal.calls == [("report_file", {"id": 3, "format": "csv"})]
    bridge.get(PREFIX + "/reports/3/file?format=")  # empty: the stored file, as without it
    assert journal.calls[1] == ("report_file", {"id": 3})


@pytest.mark.parametrize("value", ["pdf", "CSV", "csv%20", "xlsx", "csv,pdf", "csv%0A"])
def test_any_other_format_is_refused_in_words_and_nothing_is_sent(bridge, journal, value):
    as_owner(bridge)
    response = bridge.get(f"{PREFIX}/reports/3/file?format={value}", headers=JSON)
    assert response.status_code == 400
    assert error_of(response) == {"code": "bad_request", "message": WORDS["format"]}
    assert "Content-Disposition" not in response.headers
    assert journal.calls == []


def test_a_csv_asked_for_must_come_back_as_a_csv(bridge, journal):
    as_owner(bridge)
    journal.answers["report_file"] = stored()  # the statement's PDF where its CSV was asked for
    pdf = bridge.get(PREFIX + "/reports/3/file?format=csv", headers=JSON)
    assert error_of(pdf) == {"code": "unavailable", "message": WORDS["bad_file"]}
    assert "Content-Disposition" not in pdf.headers
    journal.answers["report_file"] = stored(CSV, CSV_NAME.replace(".csv", ".pdf"), "text/csv")
    renamed = bridge.get(PREFIX + "/reports/3/file?format=csv", headers=JSON)
    assert error_of(renamed) == {"code": "unavailable", "message": WORDS["bad_file"]}
    journal.answers["report_file"] = (
        200, {"status": "success", "data": {"file_name": None, "file_b64": None}},
    )  # fmt: skip
    none = bridge.get(PREFIX + "/reports/3/file?format=csv", headers=JSON)
    assert error_of(none) == {"code": "not_found", "message": WORDS["no_csv"]}


@pytest.mark.parametrize(
    "answer",
    [
        stored(sha="0" * 64),  # the bytes are not the ones recorded
        stored(name="statement one.pdf"),  # a name that is not plain ASCII
        stored(name="report.html", mime="text/html"),
        stored(mime="image/png"),  # a type that is not the name's
        (200, {"status": "success", "data": {"file_name": "a.pdf", "file_mime": "application/pdf",
                                             "file_b64": "%%%not base64%%%"}}),
    ],
    ids=["sha256", "name", "html", "mime", "base64"],
)  # fmt: skip
def test_a_file_that_is_not_what_was_stored_is_never_sent(bridge, journal, answer):
    journal.answers["report_file"] = answer
    as_owner(bridge)
    response = bridge.get(PREFIX + "/reports/3/file", headers=JSON)
    assert response.status_code == 503
    assert error_of(response) == {"code": "unavailable", "message": WORDS["bad_file"]}
    assert "Content-Disposition" not in response.headers


def test_a_report_without_a_file_and_a_download_refused_by_the_service(bridge, journal):
    journal.answers["report_file"] = (
        200,
        {"status": "success", "data": {"file_name": None, "file_b64": None}},
    )
    as_owner(bridge)
    none = bridge.get(PREFIX + "/reports/3/file", headers=JSON)
    assert error_of(none) == {"code": "not_found", "message": WORDS["no_file"]}
    journal.answers["report_file"] = (
        404,
        {"status": "error", "code": "not_found", "message": "This report is not in the archive."},
    )
    gone = bridge.get(PREFIX + "/reports/3/file", headers=JSON)
    assert error_of(gone) == {"code": "not_found", "message": "This report is not in the archive."}
    journal.answers["report_file"] = (200, {"status": "success", "data": {"enabled": False}})
    off = bridge.get(PREFIX + "/reports/3/file", headers=JSON)
    assert off.status_code == 503
    assert error_of(off) == {"code": "unavailable", "message": WORDS["off"]}


def test_a_download_while_switched_off_is_refused_in_words_never_a_200(
    bridge, journal, monkeypatch
):
    """Review H4 (v0.11.0): a browser saves a 200 under the name it asked for, so a switched-off
    answer of 200 came to the phone as a "PDF" holding JSON. Every way of saying off is a 503 with
    the sentence, and no file name goes with it."""
    reason = "The journal is switched off until the owner turns it on in OpenAlgo's .env."
    journal.answers["report_file"] = (
        200, {"status": "success", "data": {"enabled": False, "message": reason}},
    )  # fmt: skip
    as_owner(bridge)
    answers = [bridge.get(PREFIX + "/reports/3/file", headers=JSON),
               bridge.get(PREFIX + "/reports/3/file?format=csv", headers=JSON)]  # fmt: skip
    # the service does not answer in time, and its /health says it is switched off
    journal.answers["report_file"] = slow(1.0)
    journal.health = (200, {"journal_state": {"enabled": False, "reason": reason}})
    monkeypatch.setitem(bp_module.TIMEOUTS_S, "download", 0.2)
    answers.append(bridge.get(PREFIX + "/reports/3/file", headers=JSON))
    for response in answers:
        assert response.status_code == 503
        assert response.mimetype == "application/json"
        assert error_of(response) == {"code": "unavailable", "message": reason}
        assert "Content-Disposition" not in response.headers
    # a read keeps its switched-off success: the pages say it on their own card
    journal.answers["state"] = (
        200, {"status": "success", "data": {"enabled": False, "message": reason}},
    )  # fmt: skip
    assert bridge.get(PREFIX + "/state", headers=JSON).get_json() == {
        "status": "success", "enabled": False, "data": None, "message": reason,
    }  # fmt: skip


def test_a_download_answer_over_its_limit_is_not_sent(bridge, journal, monkeypatch):
    monkeypatch.setattr(bp_module, "DOWNLOAD_MAX_BYTES", 256)
    journal.answers["report_file"] = stored(PDF * 20)
    as_owner(bridge)
    response = bridge.get(PREFIX + "/reports/3/file", headers=JSON)
    assert error_of(response) == {"code": "unavailable", "message": WORDS["bad_file"]}


# -- descriptors -------------------------------------------------------------------------------


def test_forwards_leave_no_descriptor_behind(bridge, journal, monkeypatch):
    """Reads, writes, downloads and the failure paths, over and over: every socket closes."""
    journal.answers["report_file"] = stored()
    journal.answers["playbooks_list"] = slow(0.5)
    monkeypatch.setitem(bp_module.TIMEOUTS_S, "read", 0.1)
    headers = as_owner(bridge)
    bridge.get(PREFIX + "/state", headers=JSON)  # warm-up
    watch = FdWatch()
    for _ in range(40):
        assert bridge.get(PREFIX + "/state", headers=JSON).status_code == 200
        assert bridge.post(PREFIX + "/tags", json={"name": "x"},
                           headers={**JSON, **headers}).status_code == 200  # fmt: skip
        assert bridge.get(PREFIX + "/reports/3/file").status_code == 200
    for _ in range(5):  # a read that times out, then the health check that words it
        assert bridge.get(PREFIX + "/playbooks", headers=JSON).status_code == 503
    time.sleep(0.6)  # the slow answers finish on the service's side
    watch.assert_clean()


# -- limiters ----------------------------------------------------------------------------------


@pytest.fixture
def limited(monkeypatch):
    app = make_app(rate_limits=True)
    monkeypatch.setattr(bp_module, "_call", lambda spec, args: ({"status": "success"}, 200))
    client = app.test_client()
    login_session(client)
    return client, csrf_headers(client)


def hits(fn, count: int) -> list[int]:
    return [fn().status_code for _ in range(count)]


def test_reads_are_limited_to_120_a_minute_for_the_class(limited):
    client, _ = limited
    codes = hits(lambda: client.get(PREFIX + "/state", headers=JSON), 121)
    assert codes[:120] == [200] * 120 and codes[120] == 429
    assert error_of(client.get(PREFIX + "/trips", headers=JSON)) == {
        "code": "rate_limited", "message": WORDS["rate_limited"],
    }  # fmt: skip


def test_writes_are_limited_to_60_a_minute(limited):
    client, headers = limited
    codes = hits(lambda: client.post(PREFIX + "/tags", json={}, headers={**JSON, **headers}), 61)
    assert codes[:60] == [200] * 60 and codes[60] == 429
    assert client.get(PREFIX + "/state", headers=JSON).status_code == 200  # reads have their own


def test_downloads_are_limited_to_20_a_minute(limited):
    client, _ = limited
    codes = hits(lambda: client.get(PREFIX + "/reports/4/file", headers=JSON), 21)
    assert codes[:20] == [200] * 20 and codes[20] == 429
    assert client.get(PREFIX + "/reports/4", headers=JSON).status_code == 200


def test_the_reconcile_is_limited_to_2_a_minute(limited):
    client, headers = limited
    codes = hits(
        lambda: client.post(PREFIX + "/reconcile", json={}, headers={**JSON, **headers}), 3
    )
    assert codes == [200, 200, 429]
    assert client.post(PREFIX + "/tags", json={}, headers={**JSON, **headers}).status_code == 200


def test_the_owner_is_the_admin_user():
    """The owner check is OpenAlgo's admin account, as the other SUDARSHAN bridges read it."""
    from database.user_db import find_user_by_username

    assert find_user_by_username().username == OWNER
