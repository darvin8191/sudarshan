"""The family view's two doors in OpenAlgo (P6 B1; M20.6, M8.15): the owner bridge's checks, the
listener check by the family account's uid (Y14), the seal, the reading of a request against the
family contract, the answers in words, and the sealed internal route for the shared lists.

sudarshan-family is built by another lane at the same time; its admin door here is the stand-in in
family_bridge_app.py, which opens each request with the real FAMILY_ADMIN_INFO key.
"""

from __future__ import annotations

import ast
import json
import os
import re
import threading
import time
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet, InvalidToken
from family_bridge_app import (
    FAMILY_COOKIE,
    OWNER,
    SEAL_WORDS,
    VIEWER,
    FakeFamily,
    csrf_headers,
    internal_seal,
    login_session,
    make_app,
    note_shares,
    reset_lists,
    reset_users,
)

import blueprints.sudarshan_family as bp_module
from sudarshan.autologin.handoff import NonceBook
from sudarshan.family import contract
from sudarshan.tests.fdwatch import FdWatch
from sudarshan.vault import (
    FAMILY_ADMIN_INFO,
    FAMILY_INTERNAL_INFO,
    FAMILY_NUDGE_INFO,
    JOURNAL_API_INFO,
    Vault,
    fernet_for,
)
from utils.lazy import LazyInit
from utils.thread_safe_cache import LockedTTLCache

REPO = Path(__file__).resolve().parents[2]
JSON = {"Accept": "application/json"}
PREFIX = bp_module.API_PREFIX
WORDS = bp_module.WORDS
OPS = contract.ADMIN_OPS
WRITES = [op for op in OPS if op.name not in bp_module.READS]
HEADER = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid\n"
#: A request each op takes, by the contract (synthetic values only).
SAMPLE = {
    "state": {}, "member_list": {}, "share_list": {},
    "device_list": {"member_id": 2},
    "audit_list": {"member_id": 2, "kind": "login_ok", "before": 1731500000000, "limit": 50},
    "member_create": {"display_name": "Member A", "role": "viewer", "language": "gu",
                      "privacy": {"pnl": True, "qty": True, "funds": False, "orders": True},
                      "request_id": "req-1"},
    "member_update": {"id": 2, "version": 3, "display_name": "Member B", "role": "child"},
    "member_status": {"id": 2, "version": 3, "status": "suspended"},
    "member_remove": {"id": 2, "version": 4},
    "invite_create": {"member_id": 2, "request_id": "req-2"},
    "invite_revoke": {"member_id": 2},
    "signin_reset": {"member_id": 2, "version": 4},
    "device_revoke": {"member_id": 2, "id": "a1b2c3d4e5f60718"},
    "share_set": {"kind": "watchlist", "ref": "7", "member_id": None, "role": "viewer",
                  "level": "view", "expires_at": None},
    "share_remove": {"id": 9, "version": 1},
}  # fmt: skip


def send(client, op, fields=None, headers=None, **kwargs):
    fields = SAMPLE[op.name] if fields is None else fields
    if op.name in bp_module.READS:
        query = "&".join(f"{k}={v}" for k, v in fields.items())
        return client.get(f"{PREFIX}/{op.name}" + (f"?{query}" if query else ""),
                          headers={**JSON, **(headers or {})}, **kwargs)  # fmt: skip
    return client.post(f"{PREFIX}/{op.name}", json=fields, headers={**JSON, **(headers or {})},
                       **kwargs)  # fmt: skip


def error_of(response) -> dict:
    body = response.get_json()
    assert body["status"] == "error", body
    return {k: v for k, v in body.items() if k != "status"}


def as_owner(client) -> dict:
    login_session(client)
    return csrf_headers(client)


def tables(tmp_path, port: int, uids: list[int]):
    """Fake /proc/net/tcp and tcp6 naming a listener on 127.0.0.1:``port`` for each uid."""
    rows = "".join(
        f"   0: 0100007F:{port:04X} 00000000:0000 0A 00000000:00000000 00:00000000 "
        f"00000000 {uid:>5}        0 12345 1 0000000000000000 100 0 0 10 0\n"
        for uid in uids
    )
    (tmp_path / "tcp").write_text(HEADER + rows)
    (tmp_path / "tcp6").write_text(HEADER)
    return (tmp_path / "tcp", tmp_path / "tcp6")


@pytest.fixture(scope="module", autouse=True)
def _users():
    reset_users()  # once a module: each user's password hash costs most of a second


@pytest.fixture
def family(monkeypatch):
    with FakeFamily() as running:
        monkeypatch.setenv("SUDARSHAN_FAMILY_API_URL", running.url)
        yield running


@pytest.fixture(autouse=True)
def _note_of_shares(monkeypatch, tmp_path):
    """M3 (feat/family-c): the note of shared lists in a vault of each test's own, and the sample
    share naming one of the owner's own lists (the bridge checks that before it sends a share)."""
    vault = LazyInit(lambda: Vault(tmp_path / "vault.db", pepper=os.environ["API_KEY_PEPPER"]),
                     "test-family-vault")  # fmt: skip
    monkeypatch.setattr(bp_module, "_vault_lazy", vault)
    monkeypatch.setitem(SAMPLE, "share_set",
                        {**SAMPLE["share_set"], "ref": str(reset_lists()["swing"])})  # fmt: skip


@pytest.fixture
def bridge(monkeypatch):
    """The app, with the shared client replaced by one of its own and the family account's uid
    made this process's own: in CI one account runs everything (Y14's tests below say otherwise)."""
    http = httpx.Client(trust_env=False)
    monkeypatch.setattr(bp_module, "get_httpx_client", lambda: http)
    monkeypatch.setattr(bp_module, "_warned", LockedTTLCache(maxsize=16, ttl=600))
    monkeypatch.setattr(bp_module, "_family_uid", os.getuid)
    yield make_app().test_client()
    http.close()


# -- the table -----------------------------------------------------------------------------------


def test_every_admin_op_has_one_route_named_as_it_and_the_internal_route_is_one_more():
    app = make_app()
    rules = {
        (rule.endpoint, rule.rule, tuple(sorted(rule.methods - {"HEAD", "OPTIONS"})))
        for rule in app.url_map.iter_rules()
        if rule.endpoint.startswith("sudarshan_family.")
    }
    expected = {
        (f"sudarshan_family.{op.name}", f"{PREFIX}/{op.name}",
         ("GET",) if op.name in bp_module.READS else ("POST",))
        for op in OPS
    }  # fmt: skip
    expected.add(("sudarshan_family.internal_watchlists", bp_module.INTERNAL_PATH, ("POST",)))
    assert rules == expected
    assert bp_module.READS == {"state", "member_list", "device_list", "audit_list", "share_list"}
    assert bp_module.READS <= set(contract.ADMIN_OP_NAMES)


def test_no_field_of_an_admin_op_can_stand_in_for_the_seals_own():
    for op in OPS:
        names = {f.name for f in contract.parse_fields(op.req)}
        assert not names & {"op", "nonce", "principal", "now"}, op.name


def test_app_registers_the_blueprint_with_two_marked_lines_after_the_journal():
    text = (REPO / "app.py").read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if "sudarshan_family" in line]
    assert len(lines) == 2 and all("SUDARSHAN" in line for line in lines)
    registered = re.findall(r"app\.register_blueprint\((sudarshan_\w+)\)", text)
    for earlier in ("autologin", "alerts", "risk", "portfolio", "screener", "journal"):
        assert registered.index(f"sudarshan_{earlier}_bp") < registered.index(
            "sudarshan_family_bp"
        )  # Y36: autologin, alerts, risk, portfolio, screener, journal, family


def test_the_service_url_is_always_this_server(monkeypatch):
    monkeypatch.setenv("SUDARSHAN_FAMILY_API_URL", "http://example.invalid:5585")
    assert bp_module._service_url() == "http://127.0.0.1:5585"
    monkeypatch.setenv("SUDARSHAN_FAMILY_API_URL", "https://127.0.0.1:5585")
    assert bp_module._service_url() == "http://127.0.0.1:5585"
    monkeypatch.delenv("SUDARSHAN_FAMILY_API_URL")
    assert bp_module._service_url() == f"http://127.0.0.1:{contract.PORT}"


def test_the_module_reads_no_part_of_watchlists_v2():
    """The internal route reads OpenAlgo's own lists only: no flag, note or section can reach it."""
    tree = ast.parse((REPO / "blueprints" / "sudarshan_family.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    assert not any(name.startswith("sudarshan.watchlists") for name in imported), imported


# -- who may call --------------------------------------------------------------------------------


@pytest.mark.parametrize("op", OPS, ids=lambda op: op.name)
def test_no_session_is_401_in_words(bridge, family, op):
    response = send(bridge, op, headers=csrf_headers(bridge))
    assert response.status_code == 401
    assert error_of(response) == {"code": "signed_out", "message": WORDS["signed_out"]}
    assert family.calls == []


@pytest.mark.parametrize("op", OPS, ids=lambda op: op.name)
def test_the_family_cookie_alone_means_nothing_to_openalgo(bridge, family, op):
    """What a family phone sends OpenAlgo: refused as a stranger is, on every owner route."""
    response = send(bridge, op, headers={"Cookie": FAMILY_COOKIE, **csrf_headers(bridge)})
    assert response.status_code == 401 and family.calls == []
    assert b'"success"' not in response.data


@pytest.mark.parametrize("op", OPS, ids=lambda op: op.name)
def test_a_forged_non_owner_session_is_403_on_every_route(bridge, family, op):
    login_session(bridge, VIEWER)  # signed with the app's key, for a user who is not the owner
    response = send(bridge, op, headers=csrf_headers(bridge))
    assert response.status_code == 403
    assert error_of(response) == {"code": "forbidden", "message": WORDS["forbidden"]}
    assert family.calls == []


def test_a_session_for_a_user_that_does_not_exist_is_403(bridge, family):
    login_session(bridge, "nobody-at-all")
    response = bridge.get(f"{PREFIX}/state", headers=JSON)
    assert response.status_code == 403 and family.calls == []


@pytest.mark.parametrize("op", WRITES, ids=lambda op: op.name)
def test_a_write_without_a_good_csrf_token_never_reaches_the_service(bridge, family, op):
    login_session(bridge)
    for headers in ({}, {"X-CSRFToken": "not-a-token"}):
        response = send(bridge, op, headers=headers)
        assert response.status_code == 403
        assert error_of(response) == {"code": "csrf", "message": WORDS["csrf"]}
    assert family.calls == []


def test_a_read_needs_no_csrf_token(bridge, family):
    login_session(bridge)
    assert bridge.get(f"{PREFIX}/member_list", headers=JSON).status_code == 200
    assert family.calls == [("member_list", {})]


def test_the_write_limit_counts_only_the_owner_and_answers_in_words(monkeypatch, family):
    http = httpx.Client(trust_env=False)
    monkeypatch.setattr(bp_module, "get_httpx_client", lambda: http)
    monkeypatch.setattr(bp_module, "_family_uid", os.getuid)
    client = make_app(rate_limits=True).test_client()
    try:
        login_session(client, VIEWER)
        stranger = csrf_headers(client)
        for _ in range(35):  # another sign-in's writes are refused before they count
            assert send(client, WRITES[0], headers=stranger).status_code == 403
        headers = as_owner(client)
        answers = [send(client, op, headers=headers).status_code
                   for op in (WRITES * 4)[:31]]  # fmt: skip
        assert answers[:30] == [200] * 30
        assert answers[30] == 429
        last = send(client, WRITES[0], headers=headers)
        assert error_of(last) == {"code": "busy", "message": WORDS["busy"]}
        assert client.get(f"{PREFIX}/state", headers=JSON).status_code == 200  # reads: own limit
        assert len(family.calls) == 31
    finally:
        http.close()


def test_the_read_limit_is_120_a_minute(monkeypatch, family):
    http = httpx.Client(trust_env=False)
    monkeypatch.setattr(bp_module, "get_httpx_client", lambda: http)
    monkeypatch.setattr(bp_module, "_family_uid", os.getuid)
    client = make_app(rate_limits=True).test_client()
    try:
        login_session(client)
        answers = [client.get(f"{PREFIX}/state", headers=JSON).status_code for _ in range(121)]
        assert answers.count(200) == 120 and answers[-1] == 429
    finally:
        http.close()


# -- who listens on 5585 (cross-design Y14) ------------------------------------------------------


def port_of(family) -> int:
    return int(family.url.rsplit(":", 1)[1])


def test_the_port_must_be_held_by_the_family_account(bridge, family, monkeypatch, tmp_path):
    """The live check, with a fake /proc table: the family account's uid passes, and the port held
    by OpenAlgo's own account (or any other) does not, though a check by our own uid would pass."""
    me = os.getuid()
    family_uid = me + 1000
    monkeypatch.setattr(bp_module, "_family_uid", lambda: family_uid)
    login_session(bridge)
    monkeypatch.setattr(bp_module, "TCP_TABLES", tables(tmp_path, port_of(family), [family_uid]))
    assert bridge.get(f"{PREFIX}/state", headers=JSON).status_code == 200
    for holders in ([me], [family_uid + 1], [family_uid, me]):
        monkeypatch.setattr(bp_module, "TCP_TABLES", tables(tmp_path, port_of(family), holders))
        response = bridge.get(f"{PREFIX}/state", headers=JSON)
        assert response.status_code == 503
        assert error_of(response) == {"code": "unavailable", "message": WORDS["held"]}
    assert len(family.calls) == 1  # only the first one was sent


def test_nothing_is_sent_before_the_family_set_up_made_its_account(bridge, family, monkeypatch):
    monkeypatch.setattr(bp_module, "_family_uid", lambda: None)
    headers = as_owner(bridge)
    response = send(bridge, WRITES[0], headers=headers)
    assert response.status_code == 503
    assert error_of(response) == {"code": "unavailable", "message": WORDS["not_set_up"]}
    assert family.calls == [] and family.bodies == []


def test_the_family_account_is_looked_up_by_name(monkeypatch):
    import pwd

    seen = []

    def lookup(name):
        seen.append(name)
        raise KeyError(name)

    monkeypatch.setattr(pwd, "getpwnam", lookup)
    assert bp_module._family_uid() is None and seen == ["sudarshan-family"]


def test_nobody_listening_and_an_unreadable_table_send_nothing(bridge, family, monkeypatch,
                                                               tmp_path):  # fmt: skip
    login_session(bridge)
    monkeypatch.setattr(bp_module, "TCP_TABLES", tables(tmp_path, port_of(family), []))
    response = bridge.get(f"{PREFIX}/state", headers=JSON)
    assert error_of(response) == {"code": "unavailable", "message": WORDS["not_answering"]}
    monkeypatch.setattr(bp_module, "TCP_TABLES", (tmp_path / "a-dir",) * 2)
    (tmp_path / "a-dir").mkdir()
    response = bridge.get(f"{PREFIX}/state", headers=JSON)
    assert error_of(response) == {"code": "unavailable", "message": WORDS["unchecked"]}
    assert family.calls == []


# -- the seal ------------------------------------------------------------------------------------


def test_each_request_is_one_sealed_token_for_its_op_the_owner_and_its_fields(bridge, family):
    headers = as_owner(bridge)
    op = next(op for op in OPS if op.name == "member_create")
    send(bridge, op, headers=headers)
    send(bridge, op, headers=headers)
    first, second = family.bodies
    assert b"Member A" not in first  # nothing in clear on the loopback either
    key = fernet_for(os.environ["API_KEY_PEPPER"], FAMILY_ADMIN_INFO)
    message = json.loads(key.decrypt(first, ttl=60))
    nonce = message.pop("nonce")
    assert message == {"op": "member_create", "principal": "owner", **SAMPLE["member_create"]}
    assert len(nonce) == 32 and json.loads(key.decrypt(second))["nonce"] != nonce
    assert family.paths == [f"{contract.ADMIN_PREFIX}member_create"] * 2
    assert len(family.calls) == 2  # each request once


def test_the_seal_is_the_admin_purpose_and_no_other(bridge, family):
    login_session(bridge)
    bridge.get(f"{PREFIX}/state", headers=JSON)
    (blob,) = family.bodies
    for other in (FAMILY_INTERNAL_INFO, FAMILY_NUDGE_INFO, JOURNAL_API_INFO):
        with pytest.raises(InvalidToken):
            fernet_for(os.environ["API_KEY_PEPPER"], other).decrypt(blob)


@pytest.mark.parametrize("op", OPS, ids=lambda op: op.name)
def test_every_op_reaches_the_service_as_itself_with_its_fields(bridge, family, op):
    headers = as_owner(bridge)
    response = send(bridge, op, headers=headers)
    assert response.status_code == 200, response.get_json()
    assert family.calls == [(op.name, SAMPLE[op.name])]
    assert response.get_json() == {"status": "success",
                                   "data": {"op": op.name, "fields": SAMPLE[op.name]}}  # fmt: skip
    assert response.headers["Cache-Control"] == "no-store"


def test_only_the_ops_own_fields_go_on(bridge, family):
    headers = as_owner(bridge)
    body = {"id": 2, "version": 3, "privacy": {"pnl": False, "qty": True, "funds": False,
            "orders": True, "extra": 1}, "principal": "someone", "op": "member_remove",
            "nonce": "0" * 32, "now": 1, "unknown": True}  # fmt: skip
    response = bridge.post(f"{PREFIX}/member_update", json=body, headers={**JSON, **headers})
    assert response.status_code == 200
    assert family.calls == [("member_update", {
        "id": 2, "version": 3, "privacy": {"pnl": False, "qty": True, "funds": False,
                                           "orders": True}})]  # fmt: skip


@pytest.mark.parametrize(
    ("op", "body", "field"),
    [
        ("member_create", {"display_name": "A", "role": "owner"}, "role"),
        ("member_create", {"role": "viewer"}, "display_name"),
        ("member_create", {"display_name": 7, "role": "viewer"}, "display_name"),
        ("member_update", {"id": "2", "version": 1}, "id"),
        ("member_update", {"id": True, "version": 1}, "id"),
        ("member_update", {"id": 2**60, "version": 1}, "id"),
        ("member_update", {"id": 2, "version": 1, "privacy": {"pnl": True}}, "privacy"),
        ("member_update", {"id": 2, "version": 1, "language": "fr"}, "language"),
        ("member_status", {"id": 2, "version": 1, "status": "removed"}, "status"),
        ("share_set", {"kind": "tab", "ref": "1", "level": "view"}, "kind"),
        ("audit_list", {"kind": "anything"}, "kind"),
        ("device_list", {"member_id": "two"}, "member_id"),
        ("device_list", {}, "member_id"),
    ],
)
def test_a_request_the_contract_refuses_is_invalid_with_its_field_and_not_sent(
    bridge, family, op, body, field
):
    headers = as_owner(bridge)
    spec = next(s for s in OPS if s.name == op)
    response = send(bridge, spec, body, headers=headers)
    assert response.status_code == 400
    assert error_of(response) == {"code": "invalid", "message": WORDS["invalid"], "field": field}
    assert family.calls == []


def test_a_body_that_is_not_an_object_or_is_too_large_is_not_sent(bridge, family):
    headers = {**JSON, **as_owner(bridge)}
    listed = bridge.post(f"{PREFIX}/member_create", data=b"[1, 2]",
                         content_type="application/json", headers=headers)  # fmt: skip
    assert error_of(listed) == {"code": "invalid", "message": WORDS["invalid"], "field": "body"}
    large = bridge.post(
        f"{PREFIX}/member_create",
        data=b'{"display_name":"' + b"x" * (64 * 1024) + b'","role":"viewer"}',
        content_type="application/json", headers=headers,
    )  # fmt: skip
    assert large.status_code == 413
    assert error_of(large) == {"code": "too_large", "message": WORDS["too_large"]}
    assert family.calls == []


def test_query_fields_are_read_as_the_contract_says(bridge, family):
    login_session(bridge)
    response = bridge.get(f"{PREFIX}/audit_list?member_id=4&kind=lockout&limit=20&what=1",
                          headers=JSON)  # fmt: skip
    assert response.status_code == 200
    assert family.calls == [("audit_list", {"member_id": 4, "kind": "lockout", "limit": 20})]


# -- the answers ---------------------------------------------------------------------------------


def test_switched_off_is_the_services_off_record_passed_on(bridge, family):
    off = {"enabled": False, "message": "The family view is switched off on the server."}
    family.answers["member_list"] = (200, {"status": "success", "data": off})
    login_session(bridge)
    response = bridge.get(f"{PREFIX}/member_list", headers=JSON)
    assert response.status_code == 200
    assert response.get_json() == {"status": "success", "data": off}


@pytest.mark.parametrize(
    ("status", "code", "message", "field"),
    [
        (409, "conflict", "This was changed on another screen. Reload and try again.", None),
        (404, "not_found", "This is no longer there; it may have been removed on another screen.",
         None),
        (422, "limit", "The family view has room for 4 members. Remove one first.", None),
        (400, "invalid", "A display name is 1 to 40 characters.", "display_name"),
        (503, "store_newer", "The family data was written by a newer release. Install that "
         "release again; nothing was changed.", None),
        (503, "busy", "The family view is busy. Wait a minute, then try again.", None),
    ],
)  # fmt: skip
def test_the_services_refusals_keep_their_code_and_sentence(bridge, family, status, code,
                                                            message, field):  # fmt: skip
    answer = {"status": "error", "code": code, "message": message}
    if field:
        answer["field"] = field
    family.answers["member_create"] = (status, answer)
    op = next(op for op in OPS if op.name == "member_create")
    response = send(bridge, op, headers=as_owner(bridge))
    assert response.status_code == contract.ERROR_STATUS[code]
    expected = {"code": code, "message": message}
    if field:
        expected["field"] = field
    assert error_of(response) == expected


@pytest.mark.parametrize(
    "message",
    [
        "HTTP 500",
        "HTTP/1.1 500 Internal Server Error.",
        "Traceback (most recent call last):",
        "sqlite3.OperationalError: database is locked.",
        "KeyError raised in the store.",
        "See http://127.0.0.1:5585/v1/admin/state.",
        "x" * 299 + "\n.",
        "",
        None,
        42,
    ],
)
def test_a_message_that_is_not_a_plain_sentence_is_never_shown(bridge, family, message):
    family.answers["member_list"] = (500, {"status": "error", "code": "failed",
                                           "message": message})  # fmt: skip
    login_session(bridge)
    response = bridge.get(f"{PREFIX}/member_list", headers=JSON)
    assert error_of(response) == {"code": "failed", "message": WORDS["failed"]}


@pytest.mark.parametrize("code", sorted(bp_module._VIEWER_CODES))
def test_a_viewer_doors_code_from_the_admin_door_is_never_passed_on(bridge, family, code):
    family.answers["state"] = (401, {"status": "error", "code": code,
                                     "message": "You were signed out on this device."})  # fmt: skip
    login_session(bridge)
    response = bridge.get(f"{PREFIX}/state", headers=JSON)
    assert error_of(response)["code"] == "unavailable" and response.status_code == 503


def test_a_service_that_cannot_open_the_seal_is_told_in_its_words(bridge, family):
    family.fernet = Fernet(Fernet.generate_key())  # a service holding another key
    login_session(bridge)
    response = bridge.get(f"{PREFIX}/state", headers=JSON)
    assert response.status_code == 503
    assert error_of(response) == {"code": "unavailable", "message": SEAL_WORDS}
    family.answers["state"] = (403, {"status": "error", "code": "forbidden", "message": "403"})
    family.fernet = fernet_for(os.environ["API_KEY_PEPPER"], FAMILY_ADMIN_INFO)
    response = bridge.get(f"{PREFIX}/state", headers=JSON)
    assert error_of(response) == {"code": "unavailable", "message": WORDS["sealed"]}


@pytest.mark.parametrize(
    "answer",
    [
        (501, {"status": "error", "code": "not_ready",
               "message": "This part of the family view is not ready yet."}),
        (404, {"status": "error", "message": "not found"}),
        (404, {"status": "error", "code": "not_found", "message": "Not Found"}),
    ],
)  # fmt: skip
def test_an_op_the_service_does_not_have_yet_is_not_ready(bridge, family, answer):
    family.answers["audit_list"] = answer
    login_session(bridge)
    response = bridge.get(f"{PREFIX}/audit_list", headers=JSON)
    assert response.status_code == 501
    assert error_of(response) == {"code": "not_ready", "message": WORDS["not_ready"]}


def test_a_service_that_takes_no_request_changed_nothing(bridge, monkeypatch):
    """The port is closed after the listener check passed (the service stopping)."""
    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    monkeypatch.setenv("SUDARSHAN_FAMILY_API_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(bp_module.loopback, "holder", lambda *a, **k: bp_module.loopback.OURS)
    headers = as_owner(bridge)
    response = send(bridge, WRITES[0], headers=headers)
    assert response.status_code == 503
    assert error_of(response) == {"code": "unavailable", "message": WORDS["not_answering"]}


def test_a_write_sent_and_not_answered_is_not_confirmed_and_never_sent_again(bridge, family,
                                                                             monkeypatch):  # fmt: skip
    monkeypatch.setattr(bp_module, "TIMEOUTS_S", {"read": 0.3, "write": 0.3})
    family.delay = 1.0
    headers = as_owner(bridge)
    response = send(bridge, WRITES[0], headers=headers)
    assert response.status_code == 504
    assert error_of(response) == {"code": "not_confirmed", "message": WORDS["not_confirmed"]}
    time.sleep(1.2)
    assert len(family.calls) == len(family.bodies) == 1  # once, never retried, not even as is
    login_session(bridge)
    response = bridge.get(f"{PREFIX}/state", headers=JSON)  # a read: nothing changed, say so
    assert error_of(response) == {"code": "unavailable", "message": WORDS["not_answering"]}


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b'{"status": "maybe"}',
        b"[1]",
        b'{"status": "success", "data": "' + b"x" * (3 << 20) + b'"}',
    ],
)
def test_an_answer_without_the_envelope_or_past_the_size_is_not_used(bridge, family, raw):
    family.answers["member_remove"] = (200, raw)
    family.answers["state"] = (200, raw)
    headers = as_owner(bridge)
    op = next(op for op in OPS if op.name == "member_remove")
    assert error_of(send(bridge, op, headers=headers))["code"] == "not_confirmed"
    assert error_of(bridge.get(f"{PREFIX}/state", headers=JSON))["code"] == "unavailable"


def test_a_slow_drip_answer_is_cut_at_its_time(bridge, monkeypatch):
    """A service that keeps a request open with a trickle of bytes never holds it past the time."""
    import socket

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    stop = threading.Event()

    def drip():
        conn, _ = listener.accept()
        with conn:
            conn.recv(65536)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                         b"Content-Length: 100000\r\n\r\n")  # fmt: skip
            while not stop.wait(0.1):
                try:
                    conn.sendall(b" ")
                except OSError:
                    return

    thread = threading.Thread(target=drip, name="family-drip-test", daemon=True)
    thread.start()
    monkeypatch.setenv("SUDARSHAN_FAMILY_API_URL", f"http://127.0.0.1:{listener.getsockname()[1]}")
    monkeypatch.setattr(bp_module.loopback, "holder", lambda *a, **k: bp_module.loopback.OURS)
    monkeypatch.setattr(bp_module, "TIMEOUTS_S", {"read": 0.5, "write": 0.5})
    login_session(bridge)
    start = time.monotonic()
    response = bridge.get(f"{PREFIX}/state", headers=JSON)
    took = time.monotonic() - start
    stop.set()
    listener.close()
    thread.join(5)
    assert error_of(response)["code"] == "unavailable"
    assert took < 2.5  # 1 s to connect + 0.5 s, never the drip's length


def test_no_descriptor_is_left_open(bridge, family, monkeypatch):
    monkeypatch.setitem(bp_module.TIMEOUTS_S, "write", 0.2)
    headers = as_owner(bridge)
    bridge.get(f"{PREFIX}/state", headers=JSON)  # warm-up
    watch = FdWatch()
    for _ in range(20):
        assert bridge.get(f"{PREFIX}/member_list", headers=JSON).status_code == 200
        assert send(bridge, WRITES[1], headers=headers).status_code == 200
    family.delay = 0.5
    for _ in range(3):  # a write that is not answered in time
        assert send(bridge, WRITES[1], headers=headers).status_code == 504
    time.sleep(0.8)  # the slow answers finish on the stand-in's side
    watch.assert_clean()


# -- the internal route (M8.15) ------------------------------------------------------------------


INTERNAL = bp_module.INTERNAL_PATH
FAKE_MASTER = {
    ("INFY", "NSE"): {"token": "NSE_EQ|INE009A01021", "name": "INFOSYS", "tick_size": 0.05,
                      "lotsize": 1},
    ("NIFTY26NOV24000CE", "NFO"): {"token": "NSE_FO|12345", "name": "NIFTY", "tick_size": 0.05,
                                   "lotsize": 65},
}  # fmt: skip


class _Info:
    def __init__(self, **values):
        self.__dict__.update(values)


@pytest.fixture
def lists(monkeypatch):
    ids = reset_lists()
    monkeypatch.setattr(bp_module, "get_symbol_info",
                        lambda symbol, exchange: (lambda v: v and _Info(**v))(
                            FAKE_MASTER.get((symbol, exchange))))  # fmt: skip
    monkeypatch.setattr(bp_module, "_nonces", NonceBook(64))
    note_shares(ids)  # M3 (feat/family-c): each shared with member 2, as the owner's page does
    return ids


def post_internal(client, blob: bytes, headers=None):
    return client.post(INTERNAL, data=blob, headers={"Content-Type": "application/octet-stream",
                                                     **(headers or {})})  # fmt: skip


def test_the_shared_lists_answer_exactly_the_rows_asked_for(bridge, lists):
    blob = internal_seal(list_ids=[lists["empty"], lists["swing"], 999999, lists["other"]])
    response = post_internal(bridge, blob)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    data = response.get_json()["data"]
    assert [lst["id"] for lst in data["lists"]] == [lists["empty"], lists["swing"]]
    assert data["gone"] == [999999, lists["other"]]  # another account's list is not the owner's
    empty, swing = data["lists"]
    assert (empty["name"], empty["rows"]) == ("Empty", [])
    assert swing["name"] == "Swing" and re.fullmatch(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(\.\d+)?",
                                                     swing["created_at"])  # fmt: skip
    assert set(swing) == {"id", "name", "created_at", "rows"}
    assert swing["rows"] == [
        {"symbol": "INFY", "exchange": "NSE", "instrument_key": "NSE_EQ|INE009A01021",
         "name": "INFOSYS", "tick_size": 0.05, "lot_size": 1},
        {"symbol": "NIFTY26NOV24000CE", "exchange": "NFO", "instrument_key": "NSE_FO|12345",
         "name": "NIFTY", "tick_size": 0.05, "lot_size": 65},
        {"symbol": "NOTLISTED", "exchange": "NSE", "instrument_key": None, "name": None,
         "tick_size": None, "lot_size": None},
    ]  # fmt: skip
    assert set(data) == {"lists", "gone"}


def test_the_rows_are_resolved_through_openalgos_master():
    from database import token_db_enhanced

    assert bp_module.get_symbol_info is token_db_enhanced.get_symbol_info


@pytest.mark.parametrize("header", bp_module.PROXY_HEADERS)
def test_anything_nginx_forwarded_is_404_before_the_seal_is_opened(bridge, lists, header):
    blob = internal_seal(list_ids=[lists["swing"]])
    response = post_internal(bridge, blob, {header: "198.51.100.7"})
    assert response.status_code == 404 and b"Swing" not in response.data
    assert post_internal(bridge, blob).status_code == 200  # its nonce was never spent


def test_the_internal_route_needs_no_csrf_token_and_no_session(bridge, lists):
    assert post_internal(bridge, internal_seal(list_ids=[lists["swing"]])).status_code == 200


def test_a_replay_is_refused(bridge, lists):
    blob = internal_seal(list_ids=[lists["swing"]])
    assert post_internal(bridge, blob).status_code == 200
    replayed = post_internal(bridge, blob)
    assert replayed.status_code == 403 and replayed.get_json()["code"] == "forbidden"


@pytest.mark.parametrize(
    "blob",
    [
        b"{}",
        b"not a token",
        "admin",
        "nudge",
        "journal",
        "op",
        "principal",
        "expired",
    ],
)
def test_only_the_internal_purpose_for_the_family_service_opens_it(bridge, lists, blob):
    ids = [lists["swing"]]
    if blob in ("admin", "nudge", "journal"):
        info = {"admin": FAMILY_ADMIN_INFO, "nudge": FAMILY_NUDGE_INFO,
                "journal": JOURNAL_API_INFO}[blob]  # fmt: skip
        blob = internal_seal(info=info, list_ids=ids)
    elif blob == "op":
        blob = internal_seal(op="members", list_ids=ids)
    elif blob == "principal":
        blob = internal_seal(principal="owner", list_ids=ids)
    elif blob == "expired":
        fernet = fernet_for(os.environ["API_KEY_PEPPER"], FAMILY_INTERNAL_INFO)
        from sudarshan.autologin.handoff import seal

        blob = seal(fernet, "watchlists", now=time.time() - 120, principal="sudarshan-family",
                    list_ids=ids)  # fmt: skip
    response = post_internal(bridge, blob)
    assert response.status_code == 403, response.get_json()
    assert response.get_json()["code"] == "forbidden" and b"Swing" not in response.data


def test_a_family_phone_reaches_nothing_here(bridge, lists):
    response = bridge.post(INTERNAL, json={}, headers={"Cookie": FAMILY_COOKIE,
                                                       "X-Forwarded-For": "198.51.100.7"})  # fmt: skip
    assert response.status_code == 404
    response = bridge.post(INTERNAL, json={}, headers={"Cookie": FAMILY_COOKIE})
    assert response.status_code == 403


@pytest.mark.parametrize(
    "ids", [[], list(range(1, 52)), [1, 1], ["1"], [True], [0], [-3], [2**31], None, "1"]
)
def test_list_ids_must_be_1_to_50_distinct_ids(bridge, lists, ids):
    response = post_internal(bridge, internal_seal(list_ids=ids))
    assert response.status_code == 400 and response.get_json()["code"] == "invalid"


def test_size_and_length_are_checked_before_the_seal(bridge, lists):
    big = post_internal(bridge, b"x" * (bp_module.INTERNAL_MAX_BYTES + 1))
    assert big.status_code == 413 and big.get_json()["code"] == "too_large"
    unsized = bridge.post(INTERNAL, data=internal_seal(list_ids=[lists["swing"]]),
                          headers={"Transfer-Encoding": "chunked"})  # fmt: skip
    assert unsized.status_code == 411  # a body with no length is never read
    empty = bridge.post(INTERNAL, data=b"", environ_overrides={"CONTENT_LENGTH": "0"})
    assert empty.status_code == 403  # a length, and no seal


def test_a_full_nonce_book_is_busy_not_forgetful(bridge, lists, monkeypatch):
    monkeypatch.setattr(bp_module, "_nonces", NonceBook(1))
    assert post_internal(bridge, internal_seal(list_ids=[lists["swing"]])).status_code == 200
    full = post_internal(bridge, internal_seal(list_ids=[lists["swing"]]))
    assert full.status_code == 503 and full.get_json()["code"] == "busy"


def test_a_list_read_that_fails_is_told_without_its_cause(bridge, lists, monkeypatch):
    def broken(ids, member_id, role):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(bp_module, "shared_lists", broken)
    response = post_internal(bridge, internal_seal(list_ids=[lists["swing"]]))
    assert response.status_code == 500
    assert response.get_json() == {"status": "error", "code": "failed",
                                   "message": bp_module.INTERNAL_WORDS["failed"]}  # fmt: skip


def test_the_internal_route_leaves_no_descriptor_open(bridge, lists):
    post_internal(bridge, internal_seal(list_ids=[lists["swing"]]))
    watch = FdWatch()
    for _ in range(30):
        assert post_internal(bridge, internal_seal(list_ids=[lists["swing"]])).status_code == 200
    watch.assert_clean()


def test_the_family_services_admin_door_opens_what_the_bridge_sends(bridge, family):
    """Once the service's admin door (F2, another lane) is in the same tree: every op the bridge
    seals opens there as that op, under the owner's principal, with the op's fields as its
    arguments. Skipped until then (it runs at integration)."""
    import asyncio
    import importlib

    try:
        admin_api = importlib.import_module("sudarshan.family.admin_api")
        listener = importlib.import_module("sudarshan.family.listener")
    except ModuleNotFoundError:
        pytest.skip("the family service's admin door (F2) is not in this tree yet")
    seen = {}

    def handler(name):
        async def run(args):
            seen[name] = args
            return {"ran": name}

        return run

    doors = admin_api.SealedDoors(
        admin_key=fernet_for(os.environ["API_KEY_PEPPER"], FAMILY_ADMIN_INFO),
        nudge_key=fernet_for(os.environ["API_KEY_PEPPER"], FAMILY_NUDGE_INFO),
        ops={op.name: handler(op.name) for op in OPS},
        nudged=lambda *args: True,
        enabled=lambda: True,
        off_words=lambda: "The family view is switched off on the server.",
    )
    headers = as_owner(bridge)
    for op in OPS:
        assert send(bridge, op, headers=headers).status_code == 200
    for (op_name, _), body in zip(family.calls, family.bodies, strict=True):
        request = listener.Request(method="POST", path=f"{contract.ADMIN_PREFIX}{op_name}",
                                   query={}, headers={}, door="admin", head=b"", body=body)  # fmt: skip
        response = asyncio.run(doors.admin(request))
        assert response.payload == {"status": "success", "data": {"ran": op_name}}
    assert seen == {op.name: SAMPLE[op.name] for op in OPS}


# -- the note of shared lists (review M3) and one list of forwarded headers (review L7) -----------
# SUDARSHAN feat/family-c (F7): the internal route serves only what OpenAlgo's own note shares
# with the member asked about; the owner's share ops write the note as they pass.

OP = {op.name: op for op in OPS}


def share_answer(share_id: int, fields: dict, version: int = 1):
    """The family service's ``Share`` for a share_set it took."""
    return (200, {"status": "success", "data": {
        "id": share_id, "kind": fields["kind"], "ref": fields["ref"],
        "member_id": fields.get("member_id"), "role": fields.get("role"),
        "level": fields["level"], "expires_at": fields.get("expires_at"),
        "created_at": 1731500000000, "version": version}})  # fmt: skip


def shown(client, ids, **member) -> dict:
    response = post_internal(client, internal_seal(list_ids=ids, **member))
    assert response.status_code == 200, response.get_json()
    return response.get_json()["data"]


def test_m3_an_owners_list_that_is_not_shared_is_gone_with_no_content(bridge, lists):
    """The review's test: a correctly sealed request for a list of the owner's that no share
    names gets it back in gone, and nothing of the list."""
    bp_module._note_change(lambda shares: bool(shares.clear()) or True)
    response = post_internal(bridge, internal_seal(list_ids=[lists["swing"], lists["empty"]]))
    assert response.status_code == 200
    assert response.get_json()["data"] == {"lists": [], "gone": [lists["swing"], lists["empty"]]}
    assert b"Swing" not in response.data and b"INFY" not in response.data


def test_m3_a_list_shared_with_someone_else_is_gone(bridge, lists):
    bp_module._note_change(lambda shares: bool(shares.clear()) or True)
    note_shares({"swing": lists["swing"]}, member_id=3)
    note_shares({"empty": lists["empty"]}, member_id=None, role="child")
    asked = [lists["swing"], lists["empty"]]
    assert shown(bridge, asked)["gone"] == asked  # member 2, a viewer
    assert [x["id"] for x in shown(bridge, asked, member_id=3)["lists"]] == [lists["swing"]]
    child = shown(bridge, asked, member_id=5, role="child")
    assert ([x["id"] for x in child["lists"]], child["gone"]) == (
        [lists["empty"]],
        [lists["swing"]],
    )


def test_m3_a_list_shared_with_every_member_is_each_members(bridge, lists):
    """The owner's Family page shares with every member as no member and no role."""
    bp_module._note_change(lambda shares: bool(shares.clear()) or True)
    note_shares({"swing": lists["swing"]}, member_id=None, role=None)
    for member in ({"member_id": 2, "role": "viewer"}, {"member_id": 9, "role": "child"}):
        found = shown(bridge, [lists["swing"], lists["empty"]], **member)
        assert ([x["id"] for x in found["lists"]], found["gone"]) == (
            [lists["swing"]], [lists["empty"]])  # fmt: skip


def test_m3_a_later_list_given_the_same_id_is_gone(bridge, lists):
    """OpenAlgo gives a deleted list's id to the next list made (its ids are not AUTOINCREMENT):
    the note keeps the created_at the shared list had, and a list with another one was never
    shared."""
    from datetime import timedelta

    from database import watchlist_db as oa

    newest = oa.create_watchlist(OWNER, "Newest", items=[])["id"]
    note_shares({"newest": newest})
    assert oa.delete_watchlist(OWNER, newest)
    again = oa.create_watchlist(OWNER, "Made later", items=[{"symbol": "INFY", "exchange": "NSE"}])
    assert again["id"] == newest  # the same id, for another list
    later = oa.db_session.get(oa.Watchlist, newest)
    later.created_at = later.created_at + timedelta(seconds=5)  # made in a later second
    oa.db_session.commit()
    oa.db_session.remove()
    data = shown(bridge, [newest, lists["empty"]])
    assert ([x["id"] for x in data["lists"]], data["gone"]) == ([lists["empty"]], [newest])
    assert b"Made later" not in post_internal(bridge, internal_seal(list_ids=[newest])).data


def test_m3_an_expired_share_is_gone(bridge, lists):
    bp_module._note_change(lambda shares: bool(shares.clear()) or True)
    now = int(time.time() * 1000)
    note_shares({"swing": lists["swing"]}, until=now - 1)
    note_shares({"empty": lists["empty"]}, until=now + 60_000)
    data = shown(bridge, [lists["swing"], lists["empty"]])
    assert ([x["id"] for x in data["lists"]], data["gone"]) == ([lists["empty"]], [lists["swing"]])


@pytest.mark.parametrize(
    "member",
    [{"member_id": None}, {"role": None}, {"member_id": 0}, {"member_id": True},
     {"member_id": "2"}, {"member_id": 2**31}, {"role": "owner"}, {"role": ["viewer"]}],
)  # fmt: skip
def test_m3_a_request_must_say_which_member_it_is_for(bridge, lists, member):
    response = post_internal(bridge, internal_seal(list_ids=[lists["swing"]], **member))
    assert response.status_code == 400 and response.get_json()["code"] == "invalid"
    assert b"Swing" not in response.data


def test_m3_a_confirmed_share_is_noted_and_then_served(bridge, family, lists):
    bp_module._note_change(lambda shares: bool(shares.clear()) or True)
    fields = {"kind": "watchlist", "ref": str(lists["swing"]), "member_id": 4, "level": "view",
              "expires_at": None}  # fmt: skip
    family.answers["share_set"] = share_answer(31, fields)
    response = send(bridge, OP["share_set"], fields, headers=as_owner(bridge))
    assert response.status_code == 200 and response.get_json()["data"]["id"] == 31
    made = bp_module._list_made(str(lists["swing"]))[1]
    assert made is not None and bp_module._note() == {"31": {
        "list": lists["swing"], "made": made, "member": 4, "role": None, "until": None}}  # fmt: skip
    assert [x["id"] for x in shown(bridge, [lists["swing"]], member_id=4)["lists"]] == [
        lists["swing"]]  # fmt: skip


def test_m3_a_list_not_the_owners_is_never_sent(bridge, family, lists):
    before = bp_module._note()
    for ref in (str(lists["other"]), "999999"):
        fields = {"kind": "watchlist", "ref": ref, "role": "viewer", "level": "view"}
        response = send(bridge, OP["share_set"], fields, headers=as_owner(bridge))
        assert response.status_code == 404
        assert error_of(response) == {"code": "not_found",
                                      "message": bp_module.NOTE_WORDS["not_mine"]}  # fmt: skip
    assert family.calls == [] and bp_module._note() == before


@pytest.mark.parametrize(
    "answer",
    [
        (409, {"status": "error", "code": "conflict",
               "message": "This was changed on another screen. Reload and try again."}),
        (200, {"status": "success", "data": {"enabled": False,
                                             "message": "The family view is switched off."}}),
        (200, {"status": "success", "data": {"op": "share_set"}}),
        "late",
    ],
)  # fmt: skip
def test_m3_a_share_the_service_did_not_confirm_is_not_noted(bridge, family, lists, monkeypatch,
                                                            answer):  # fmt: skip
    if answer == "late":
        monkeypatch.setattr(bp_module, "TIMEOUTS_S", {"read": 0.3, "write": 0.3})
        family.delay = 0.8
        answer = share_answer(32, {"kind": "watchlist", "ref": str(lists["swing"]),
                                   "role": "viewer", "level": "view"})  # fmt: skip
    family.answers["share_set"] = answer
    fields = {"kind": "watchlist", "ref": str(lists["swing"]), "role": "viewer", "level": "view"}
    before = bp_module._note()
    response = send(bridge, OP["share_set"], fields, headers=as_owner(bridge))
    assert len(family.calls) == 1
    assert bp_module._note() == before
    if family.delay:
        assert error_of(response)["code"] == "not_confirmed"
        time.sleep(0.6)  # the stand-in finishes its slow answer


def test_m3_a_share_of_a_part_of_the_books_is_sent_as_it_is(bridge, family, lists):
    fields = {"kind": "book", "ref": "positions", "role": "viewer", "level": "view"}
    family.answers["share_set"] = share_answer(33, fields)
    before = bp_module._note()
    assert send(bridge, OP["share_set"], fields, headers=as_owner(bridge)).status_code == 200
    assert family.calls == [("share_set", fields)] and bp_module._note() == before


def test_m3_a_removed_share_is_forgotten_first_and_kept_only_while_the_service_has_it(
    bridge, family, lists
):
    headers = as_owner(bridge)
    entry = {"list": lists["swing"], "made": None, "member": 2, "role": None, "until": None}
    for answer, kept in [
        ((409, {"status": "error", "code": "conflict", "message": "Changed. Reload."}), True),
        ((503, {"status": "error", "code": "busy", "message": "Busy. Wait a minute."}), True),
        ((200, {"status": "success", "data": {"enabled": False, "message": "Off."}}), True),
        ((404, {"status": "error", "code": "not_found", "message": "No longer there."}), False),
        ((200, {"status": "success", "data": {"ok": True, "words": None}}), False),
    ]:
        bp_module._note_change(lambda shares: bool(shares.update({"9": dict(entry)})) or True)
        seen = []
        family.answers["share_remove"] = lambda message, a=answer, s=seen: (
            s.append(dict(bp_module._note())) or a)  # fmt: skip
        send(bridge, OP["share_remove"], {"id": 9, "version": 1}, headers=headers)
        assert "9" not in seen[0]  # forgotten before the service was asked
        assert ("9" in bp_module._note()) is kept, answer


def test_m3_a_note_that_cannot_be_written_is_said_and_nothing_is_sent(bridge, family, lists,
                                                                       monkeypatch):  # fmt: skip
    headers = as_owner(bridge)

    def broken(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(bp_module, "_note_change", broken)
    response = send(bridge, OP["share_remove"], {"id": 9, "version": 1}, headers=headers)
    assert (response.status_code, error_of(response)) == (
        500, {"code": "failed", "message": bp_module.NOTE_WORDS["unforgotten"]})  # fmt: skip
    assert family.calls == []
    fields = {"kind": "watchlist", "ref": str(lists["swing"]), "role": "viewer", "level": "view"}
    family.answers["share_set"] = share_answer(34, fields)
    response = send(bridge, OP["share_set"], fields, headers=headers)
    assert (response.status_code, error_of(response)) == (
        500, {"code": "failed", "message": bp_module.NOTE_WORDS["unnoted"]})  # fmt: skip
    assert [op for op, _ in family.calls] == ["share_set"]


def test_m3_a_removed_members_shares_are_forgotten(bridge, family, lists):
    bp_module._note_change(lambda shares: bool(shares.clear()) or True)
    note_shares({"swing": lists["swing"]}, member_id=2)
    note_shares({"empty": lists["empty"]}, member_id=None, role="viewer")
    note_shares({"other": lists["other"]}, member_id=3)
    note_shares({"swing": lists["swing"]}, member_id=None)  # every member's
    family.answers["member_remove"] = (409, {"status": "error", "code": "conflict",
                                             "message": "Changed. Reload."})  # fmt: skip
    headers = as_owner(bridge)
    send(bridge, OP["member_remove"], {"id": 2, "version": 4}, headers=headers)
    assert len(bp_module._note()) == 4  # refused: nothing forgotten
    family.answers["member_remove"] = (200, {"status": "success",
                                             "data": {"ok": True, "words": None}})  # fmt: skip
    send(bridge, OP["member_remove"], {"id": 2, "version": 4}, headers=headers)
    assert {(e["member"], e["role"]) for e in bp_module._note().values()} == {
        (3, None), (None, "viewer"), (None, None)}  # fmt: skip


def test_m3_the_internal_route_answers_120_a_minute(monkeypatch, tmp_path, lists):
    monkeypatch.setattr(bp_module, "_nonces", NonceBook(512))
    client = make_app(rate_limits=True).test_client()
    for _ in range(120):
        assert post_internal(client, internal_seal(list_ids=[lists["swing"]])).status_code == 200
    refused = post_internal(client, internal_seal(list_ids=[lists["swing"]]))
    assert refused.status_code == 429 and b"Swing" not in refused.data


def test_l7_the_internal_route_and_the_family_doors_refuse_one_list_of_headers():
    assert bp_module.PROXY_HEADERS is contract.FORWARDED_HEADERS
    assert set(contract.FORWARDED_HEADERS) == {"X-Forwarded-For", "X-Real-IP", "Forwarded",
                                               "X-Forwarded-Proto", "X-Forwarded-Host"}  # fmt: skip


def test_m3_the_share_ops_and_their_note_leave_no_descriptor_open(bridge, family, lists):
    """fd-audit (F7): the note lives in the vault (its engine built once, a session per read or
    write) and the list check reads OpenAlgo's table with the session removed after."""
    headers = as_owner(bridge)
    fields = {"kind": "watchlist", "ref": str(lists["swing"]), "role": "child", "level": "view"}
    family.answers["share_set"] = share_answer(40, fields)
    family.answers["share_remove"] = (200, {"status": "success",
                                            "data": {"ok": True, "words": None}})  # fmt: skip
    send(bridge, OP["share_set"], fields, headers=headers)  # warm up: the vault's engine
    watch = FdWatch()
    for _ in range(30):
        assert send(bridge, OP["share_set"], fields, headers=headers).status_code == 200
        assert send(bridge, OP["share_remove"], {"id": 40, "version": 1},
                    headers=headers).status_code == 200  # fmt: skip
        assert post_internal(bridge, internal_seal(list_ids=[lists["swing"]])).status_code == 200
    watch.assert_clean()
