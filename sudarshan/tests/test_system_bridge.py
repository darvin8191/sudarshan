"""M20.8, M17.9: the System page's API (blueprints/sudarshan_system.py): the owner reads what
the watch last saw, everyone else is refused, and Disconnect ends the assistant's access."""

from __future__ import annotations

import json
import time
from datetime import datetime

import pytest
import pytz
from flask import Blueprint, Flask
from flask_wtf.csrf import CSRFProtect, generate_csrf

import blueprints.sudarshan_system as bp_module
from database import user_db
from limiter import limiter
from sudarshan.tests.fdwatch import FdWatch

OWNER, VIEWER = "owner", "family-viewer"
PASSWORD = "Fake-Pass-123!"  # pragma: allowlist secret
STATUS = "/sudarshan/system/api/status"
DISCONNECT = "/sudarshan/system/api/assistant/disconnect"
REPORT = {
    "v": 1,
    "at": 0,
    "release": "v0.13.1",
    "heartbeat": {"state": "not_set", "words": "No outside heartbeat is set up."},
    "checks": [
        {"name": "disk", "level": "fine", "told": "fine", "since": None,
         "facts": {"free_gb": "41.2", "cause": "space"}, "extra": "dropped"},
        "not a check",
    ],
    "extra": "dropped",
}  # fmt: skip


def make_app(*, rate_limits: bool = False) -> Flask:
    app = Flask("sudarshan-system-bridge-tests")
    app.secret_key = "test-only-secret-key-" + "1" * 32  # pragma: allowlist secret
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=True, WTF_CSRF_TIME_LIMIT=None,
                      RATELIMIT_ENABLED=rate_limits)  # fmt: skip
    app.csrf = CSRFProtect(app)
    limiter.init_app(app)
    if rate_limits:
        limiter.reset()
    app.register_blueprint(bp_module.sudarshan_system_bp)
    auth = Blueprint("auth", __name__)  # where a visitor without a session is sent
    auth.add_url_rule("/auth/login", "login", lambda: "sign in")
    app.register_blueprint(auth)

    @app.route("/csrf-token-for-tests")
    def csrf_token_for_tests():
        return {"csrf_token": generate_csrf()}

    @app.teardown_appcontext
    def remove_sessions(exception=None):
        user_db.db_session.remove()

    return app


def login(client, username: str = OWNER) -> None:
    with client.session_transaction() as session:
        session["user"] = username
        session["logged_in"] = True
        session["login_time"] = datetime.now(pytz.timezone("Asia/Kolkata")).isoformat()


def csrf(client) -> dict:
    return {"X-CSRFToken": client.get("/csrf-token-for-tests").get_json()["csrf_token"]}


@pytest.fixture
def server(tmp_path, monkeypatch):
    user_db.Base.metadata.create_all(user_db.engine)
    user_db.User.query.delete()
    user_db.db_session.commit()
    user_db.db_session.remove()
    user_db.add_user(OWNER, "owner@example.invalid", PASSWORD, is_admin=True)
    user_db.add_user(VIEWER, "viewer@example.invalid", PASSWORD, is_admin=False)
    user_db.db_session.remove()
    watch, backup = tmp_path / "watch", tmp_path / "backup"
    watch.mkdir()
    backup.mkdir()
    monkeypatch.setenv("SUDARSHAN_WATCH_STATE_DIR", str(watch))
    monkeypatch.setenv("SUDARSHAN_BACKUP_STATE_DIR", str(backup))
    for name in ("MCP_HTTP_ENABLED", "OPENALGO_MCP_READ_ONLY", "MCP_OAUTH_WRITE_SCOPE_ENABLED",
                 "MCP_OAUTH_REQUIRE_APPROVAL", "MCP_PUBLIC_URL"):  # fmt: skip
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(bp_module, "get_active_sessions", lambda user: [{}, {}])
    ended = []
    monkeypatch.setattr(
        bp_module.cutoff, "end_all_access",
        lambda reason, **kw: ended.append(reason) or {"cutoff": True, "revoked": 2},
    )  # fmt: skip
    app = make_app()
    app.ended, app.watch_dir, app.backup_dir = ended, watch, backup
    return app


def test_the_owner_reads_what_the_watch_last_saw(server):
    client = server.test_client()
    login(client)
    (server.watch_dir / "report.json").write_text(json.dumps({**REPORT, "at": time.time() - 30}))
    (server.backup_dir / "drill.json").write_text(
        json.dumps({"at": "2026-11-01T11:00:05+05:30", "outcome": "failed", "item": "vault_key",
                    "words": "The vault does not open.", "archive": "a.tar.gz", "secret": "x"})
    )  # fmt: skip
    answer = client.get(STATUS)
    assert answer.status_code == 200 and answer.headers["Cache-Control"] == "no-store"
    data = answer.get_json()["data"]
    assert data["sessions"] == 2
    watch = data["watch"]
    assert watch["release"] == "v0.13.1" and watch["stale"] is False and 25 <= watch["age_s"] < 90
    assert watch["checks"] == [
        {"name": "disk", "level": "fine", "told": "fine", "since": None,
         "facts": {"free_gb": "41.2", "cause": "space"}}
    ]  # fmt: skip
    assert "extra" not in watch
    assert data["drill"]["outcome"] == "failed" and "secret" not in data["drill"]
    assert data["assistant"] == {
        "switched_on": False, "read_only": False, "write_scope_offered": True,
        "approval_required": False, "running": False, "registered": False, "address": "",
        "words": "The assistant is switched off.",
    }  # fmt: skip


def test_a_report_that_is_old_missing_or_broken_is_said_as_such(server):
    client = server.test_client()
    login(client)
    assert client.get(STATUS).get_json()["data"]["watch"] is None
    assert client.get(STATUS).get_json()["data"]["drill"] is None
    report = server.watch_dir / "report.json"
    report.write_text(json.dumps({**REPORT, "at": time.time() - 3600}))
    watch = client.get(STATUS).get_json()["data"]["watch"]
    assert watch["stale"] is True and watch["age_s"] >= 3600
    for broken in ("{not json", "[]", json.dumps({"at": "x", "checks": []}),
                   json.dumps({"at": 5})):  # fmt: skip
        report.write_text(broken)
        assert client.get(STATUS).get_json()["data"]["watch"] is None
    report.unlink()
    report.symlink_to(server.backup_dir / "elsewhere.json")
    (server.backup_dir / "elsewhere.json").write_text(json.dumps({**REPORT, "at": time.time()}))
    assert client.get(STATUS).get_json()["data"]["watch"] is None, "a link is not followed"


def test_the_assistants_state_and_address_when_it_is_on_and_locked(server, monkeypatch):
    monkeypatch.setenv("MCP_HTTP_ENABLED", "True")
    monkeypatch.setenv("OPENALGO_MCP_READ_ONLY", "1")
    monkeypatch.setenv("MCP_OAUTH_WRITE_SCOPE_ENABLED", "False")
    monkeypatch.setenv("MCP_PUBLIC_URL", "https://app.example.invalid/")
    client = server.test_client()
    login(client)
    assistant = client.get(STATUS).get_json()["data"]["assistant"]
    assert (
        assistant["running"] is True and assistant["address"] == "https://app.example.invalid/mcp"
    )
    monkeypatch.setenv("MCP_OAUTH_WRITE_SCOPE_ENABLED", "True")
    refused = client.get(STATUS).get_json()["data"]["assistant"]
    assert refused["running"] is False and refused["address"] == ""
    assert refused["words"].startswith("The assistant is switched off: its settings would allow")


@pytest.mark.parametrize("who", ["nobody", VIEWER])
def test_everyone_but_the_owner_is_refused_both_routes(server, who):
    client = server.test_client()
    if who != "nobody":
        login(client, who)
    read = client.get(STATUS)
    assert read.status_code in (302, 401, 403) and b"checks" not in read.data
    headers = csrf(client)
    write = client.post(DISCONNECT, headers=headers)
    assert write.status_code in (302, 401, 403) and server.ended == []


def test_disconnect_needs_the_page_token_and_ends_every_connection(server):
    client = server.test_client()
    login(client)
    assert client.post(DISCONNECT).status_code == 400 and server.ended == [], "no CSRF token"
    assert client.get(DISCONNECT).status_code == 405
    answer = client.post(DISCONNECT, headers=csrf(client))
    assert answer.status_code == 200 and server.ended == ["sudarshan_disconnect"]
    body = answer.get_json()
    assert body["status"] == "success" and body["message"] == bp_module.DISCONNECTED
    assert body["data"] == {"ended": True, "revoked": 2}


@pytest.mark.parametrize(
    "done", [{"cutoff": False, "revoked": 2}, {"cutoff": True, "revoked": None}]
)
def test_a_disconnect_that_did_not_fully_work_says_what_to_do(server, monkeypatch, done):
    monkeypatch.setattr(bp_module.cutoff, "end_all_access", lambda reason, **kw: done)
    client = server.test_client()
    login(client)
    answer = client.post(DISCONNECT, headers=csrf(client))
    assert answer.status_code == 500
    assert answer.get_json() == {
        "status": "error", "message": bp_module.DISCONNECT_FAILED,
        "data": {"ended": False, "revoked": done["revoked"]},
    }  # fmt: skip
    assert "kill switch on the Risk page" in bp_module.DISCONNECT_FAILED


def test_disconnect_is_limited_and_the_limit_is_said_in_words(tmp_path, monkeypatch, server):
    app = make_app(rate_limits=True)
    client = app.test_client()
    login(client)
    headers = csrf(client)
    codes = [client.post(DISCONNECT, headers=headers).status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[-1] == 429
    assert client.post(DISCONNECT, headers=headers).get_json()["message"] == bp_module.TOO_MANY
    limiter.reset()


def test_the_write_route_list_is_the_blueprints_own(server):
    writes = {
        (method, rule.rule)
        for rule in server.url_map.iter_rules()
        if rule.endpoint.startswith("sudarshan_system.")
        for method in rule.methods - {"GET", "HEAD", "OPTIONS"}
    }
    assert writes == set(bp_module.WRITE_ROUTES)


def test_the_status_read_opens_no_connection_and_leaves_no_descriptor(server, monkeypatch):
    import socket

    def refuse(*args, **kwargs):
        raise AssertionError("the status read opened a connection")

    client = server.test_client()
    login(client)
    (server.watch_dir / "report.json").write_text(json.dumps({**REPORT, "at": time.time()}))
    client.get(STATUS)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    watch = FdWatch()
    for _ in range(50):
        assert client.get(STATUS).status_code == 200
    watch.assert_clean()
