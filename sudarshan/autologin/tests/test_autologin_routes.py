"""The OpenAlgo side: owner-only routes, CSRF, write-only secrets, "Test now", sealed hand-off."""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest
from autologin_app import (
    OWNER,
    TELEGRAM_ID,
    add_owner,
    csrf_headers,
    login_session,
    make_app,
    reset_openalgo_db,
    stub_outside_world,
    use_app_state,
)
from autologin_fakes import FAKE_PIN, FAKE_TOKEN, FAKE_TOTP_SECRET

import blueprints.sudarshan_autologin as bp_module
from database import auth_db
from sudarshan.autologin.handoff import seal
from sudarshan.autologin.schedule import IST
from sudarshan.autologin.worker import EXIT_BUSY, SECRET_PIN, SECRET_TOTP
from sudarshan.vault import HANDOFF_INFO, fernet_for

JSON = {"Accept": "application/json"}
OWNER_ROUTES = [
    ("get", "/sudarshan/autologin/status", None),
    ("post", "/sudarshan/autologin/totp-secret", {"secret": FAKE_TOTP_SECRET}),
    ("delete", "/sudarshan/autologin/totp-secret", None),
    ("post", "/sudarshan/autologin/pin", {"pin": FAKE_PIN}),
    ("delete", "/sudarshan/autologin/pin", None),
    ("post", "/sudarshan/autologin/enabled", {"enabled": True}),
    ("post", "/sudarshan/autologin/test", None),
]


@pytest.fixture
def setup(monkeypatch, vault, holidays_file, pepper):
    reset_openalgo_db()
    add_owner()
    use_app_state(monkeypatch, vault=vault, holidays_file=holidays_file, pepper=pepper)
    calls = stub_outside_world(monkeypatch)
    app = make_app()
    return app, app.test_client(), vault, calls


def _call(client, method, path, body=None, headers=None):
    return getattr(client, method)(path, json=body, headers={**JSON, **(headers or {})})


# -- who may call -------------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path", "body"), OWNER_ROUTES)
def test_owner_routes_need_a_logged_in_session(setup, method, path, body):
    _, client, vault, _ = setup
    headers = csrf_headers(client)  # a token, but no login
    response = _call(client, method, path, body, headers)
    assert response.status_code == 401
    assert vault.secret_info(SECRET_TOTP).is_set is False


@pytest.mark.parametrize(("method", "path", "body"), OWNER_ROUTES)
def test_owner_routes_refuse_another_account(setup, method, path, body):
    _, client, vault, _ = setup
    login_session(client, "someone-else")
    response = _call(client, method, path, body, csrf_headers(client))
    assert response.status_code == 403
    assert response.get_json()["message"] == bp_module.OWNER_ONLY


@pytest.mark.parametrize(("method", "path", "body"), [r for r in OWNER_ROUTES if r[0] != "get"])
def test_changes_need_the_csrf_token(setup, method, path, body):
    _, client, vault, _ = setup
    login_session(client)
    assert _call(client, method, path, body).status_code == 400
    assert vault.secret_info(SECRET_TOTP).is_set is False and vault.state().enabled is False


# -- secrets are write-only -------------------------------------------------------------------


def test_secrets_are_saved_but_never_served(setup):
    _, client, vault, _ = setup
    login_session(client)
    headers = csrf_headers(client)
    spaced = "gezd gnbv gy3t qojq gezd gnbv gy3t qojq"
    assert (
        _call(
            client, "post", "/sudarshan/autologin/totp-secret", {"secret": spaced}, headers
        ).status_code
        == 200
    )
    assert (
        _call(client, "post", "/sudarshan/autologin/pin", {"pin": FAKE_PIN}, headers).status_code
        == 200
    )
    assert vault.get_secret(SECRET_TOTP) == FAKE_TOTP_SECRET  # stored normalised
    assert vault.get_secret(SECRET_PIN) == FAKE_PIN

    response = _call(client, "get", "/sudarshan/autologin/status")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    for value in (FAKE_TOTP_SECRET, FAKE_PIN, spaced, "GEZD"):
        assert value not in body
    secrets_view = response.get_json()["data"]["secrets"]
    assert secrets_view["totp_secret"]["set"] and secrets_view["pin"]["set"]
    assert secrets_view["totp_secret"]["readable"] and secrets_view["pin"]["updated_at"]

    for path in ("/sudarshan/autologin/totp-secret", "/sudarshan/autologin/pin"):
        assert _call(client, "get", path).status_code == 405  # nothing to read back


@pytest.mark.parametrize(
    "secret", ["", "SHORT", "A" * 65, "GEZDGNBVGY3TQOJ1", None, 12345678901234567, ["x"]]
)
def test_totp_key_validation(setup, secret):
    _, client, vault, _ = setup
    login_session(client)
    response = _call(
        client, "post", "/sudarshan/autologin/totp-secret", {"secret": secret}, csrf_headers(client)
    )
    assert response.status_code == 400
    assert response.get_json()["message"] == bp_module.TOTP_INVALID
    assert vault.secret_info(SECRET_TOTP).is_set is False


@pytest.mark.parametrize(
    "pin", ["", "12345", "1234567", "12a456", "١٢٣٤٥٦", " 123456", 123456, None]
)
def test_pin_validation(setup, pin):
    _, client, vault, _ = setup
    login_session(client)
    response = _call(client, "post", "/sudarshan/autologin/pin", {"pin": pin}, csrf_headers(client))
    assert response.status_code == 400
    assert response.get_json()["message"] == bp_module.PIN_INVALID
    assert vault.secret_info(SECRET_PIN).is_set is False


def test_pin_keeps_leading_zero(setup):
    _, client, vault, _ = setup
    login_session(client)
    assert (
        _call(
            client, "post", "/sudarshan/autologin/pin", {"pin": "024680"}, csrf_headers(client)
        ).status_code
        == 200
    )
    assert vault.get_secret(SECRET_PIN) == "024680"


def test_clearing(setup):
    _, client, vault, _ = setup
    vault.set_secret(SECRET_TOTP, FAKE_TOTP_SECRET)
    vault.set_secret(SECRET_PIN, FAKE_PIN)
    login_session(client)
    headers = csrf_headers(client)
    assert (
        _call(client, "delete", "/sudarshan/autologin/totp-secret", None, headers).status_code
        == 200
    )
    assert _call(client, "delete", "/sudarshan/autologin/pin", None, headers).status_code == 200
    assert not vault.secret_info(SECRET_TOTP).is_set and not vault.secret_info(SECRET_PIN).is_set


def test_switch_and_resume_after_a_pause(setup):
    _, client, vault, _ = setup
    login_session(client)
    headers = csrf_headers(client)
    assert (
        _call(
            client, "post", "/sudarshan/autologin/enabled", {"enabled": "yes"}, headers
        ).status_code
        == 400
    )
    vault.update_status(paused_code="invalid_credentials")
    assert (
        _call(
            client, "post", "/sudarshan/autologin/enabled", {"enabled": False}, headers
        ).status_code
        == 200
    )
    assert vault.state().enabled is False and vault.state().paused_code == "invalid_credentials"
    status = _call(client, "get", "/sudarshan/autologin/status").get_json()["data"]
    assert "paused" in status["paused"]["message"] and status["next_run"] is None
    assert (
        _call(
            client, "post", "/sudarshan/autologin/enabled", {"enabled": True}, headers
        ).status_code
        == 200
    )
    assert vault.state().enabled is True and vault.state().paused_code is None

    vault.update_status(paused_code="totp_not_enabled")
    _call(client, "post", "/sudarshan/autologin/totp-secret", {"secret": FAKE_TOTP_SECRET}, headers)
    assert vault.state().paused_code is None


def test_status_shows_the_next_run_and_the_last_result(setup):
    _, client, vault, _ = setup
    login_session(client)
    vault.set_enabled(True)
    vault.update_status(
        last_run_at=datetime(2026, 10, 19, 8, 35, 10, tzinfo=IST),
        last_run_kind="scheduled",
        last_outcome="failed",
        last_code="network",
        last_message="Could not reach Upstox.",
    )
    data = _call(client, "get", "/sudarshan/autologin/status").get_json()["data"]
    assert data["enabled"] is True and data["broker_supported"] is True
    assert data["last_run"] == {
        "at": "2026-10-19T08:35:10+05:30",
        "kind": "scheduled",
        "outcome": "failed",
        "message": "Could not reach Upstox.",
    }
    next_run = datetime.fromisoformat(data["next_run"])
    assert next_run > datetime.now(IST) and next_run.strftime("%H:%M") in data["schedule"]
    assert data["test"] == {"allowed": False, "reason": "The TOTP key or the PIN is not saved yet."}


# -- Test now -----------------------------------------------------------------------------------


def _ready(vault):
    vault.set_secret(SECRET_TOTP, FAKE_TOTP_SECRET)
    vault.set_secret(SECRET_PIN, FAKE_PIN)


def _fresh_upstox_login():
    auth_db.upsert_auth(OWNER, FAKE_TOKEN, "upstox")
    auth_db.register_session(OWNER, "fake-session-id", device_info="phone", broker="upstox")
    auth_db.db_session.remove()


def test_test_now_refuses_without_secrets(setup, monkeypatch):
    _, client, _, _ = setup
    monkeypatch.setattr(bp_module, "run_test_child", lambda run_id: pytest.fail("must not start"))
    login_session(client)
    response = _call(client, "post", "/sudarshan/autologin/test", None, csrf_headers(client))
    assert response.status_code == 409
    assert response.get_json()["message"] == "The TOTP key or the PIN is not saved yet."


def test_test_now_refuses_in_market_hours_when_already_connected(setup, monkeypatch):
    _, client, vault, calls = setup
    _ready(vault)
    _fresh_upstox_login()
    monkeypatch.setattr(bp_module, "_now", lambda: datetime(2026, 10, 19, 10, 0, tzinfo=IST))
    monkeypatch.setattr(bp_module, "run_test_child", lambda run_id: pytest.fail("must not start"))
    login_session(client)
    response = _call(client, "post", "/sudarshan/autologin/test", None, csrf_headers(client))
    assert response.status_code == 409
    assert "would cut off the live session" in response.get_json()["message"]


def test_test_now_refuses_while_another_attempt_runs_or_too_soon(setup, monkeypatch):
    _, client, vault, _ = setup
    _ready(vault)
    monkeypatch.setattr(bp_module, "run_test_child", lambda run_id: pytest.fail("must not start"))
    login_session(client)
    headers = csrf_headers(client)
    now = datetime.now(IST)
    assert vault.acquire_lease("scheduled-run", now=now, ttl=timedelta(seconds=180))
    response = _call(client, "post", "/sudarshan/autologin/test", None, headers)
    assert response.status_code == 409 and "Another login attempt" in response.get_json()["message"]
    vault.release_lease("scheduled-run")
    vault.start_attempt(kind="scheduled", at=now - timedelta(seconds=20))
    response = _call(client, "post", "/sudarshan/autologin/test", None, headers)
    assert response.status_code == 409 and "Try again shortly" in response.get_json()["message"]


@pytest.mark.parametrize(
    ("outcome", "status_code"), [("ok", 200), ("failed", 200), ("refused", 409)]
)
def test_test_now_returns_the_workers_own_result(setup, monkeypatch, outcome, status_code):
    _, client, vault, _ = setup
    _ready(vault)
    started = []

    def fake_child(run_id):
        started.append(run_id)
        vault.update_status(
            last_run_id=run_id,
            last_run_at=datetime.now(IST),
            last_run_kind="test",
            last_outcome=outcome,
            last_code="x",
            last_message=f"worker says {outcome}",
        )
        return 0

    monkeypatch.setattr(bp_module, "run_test_child", fake_child)
    login_session(client)
    response = _call(client, "post", "/sudarshan/autologin/test", None, csrf_headers(client))
    assert response.status_code == status_code
    assert response.get_json()["data"]["message"] == f"worker says {outcome}"
    assert len(started) == 1 and len(started[0]) == 32


@pytest.mark.parametrize(
    ("exit_status", "status_code", "words"),
    [
        (EXIT_BUSY, 409, "Another login attempt"),
        (None, 504, "took too long"),
        (1, 500, "stopped unexpectedly"),
    ],
)
def test_test_now_without_a_recorded_result(setup, monkeypatch, exit_status, status_code, words):
    _, client, vault, _ = setup
    _ready(vault)
    monkeypatch.setattr(bp_module, "run_test_child", lambda run_id: exit_status)
    login_session(client)
    response = _call(client, "post", "/sudarshan/autologin/test", None, csrf_headers(client))
    assert response.status_code == status_code and words in response.get_json()["message"]


def test_test_now_is_rate_limited(monkeypatch, vault, holidays_file):
    reset_openalgo_db()
    add_owner()
    use_app_state(monkeypatch, vault=vault, holidays_file=holidays_file)
    stub_outside_world(monkeypatch)
    monkeypatch.setattr(bp_module, "_test_refusal", lambda *a: "too_soon")
    client = make_app(rate_limits=True).test_client()
    login_session(client)
    headers = csrf_headers(client)
    codes = [
        _call(client, "post", "/sudarshan/autologin/test", None, headers).status_code
        for _ in range(3)
    ]
    assert codes == [409, 409, 429]
    response = _call(client, "post", "/sudarshan/autologin/test", None, headers)
    assert response.status_code == 429 and response.get_json()["status"] == "error"


# -- the worker's sealed calls --------------------------------------------------------------------


def _post_sealed(client, op, blob):
    return client.post(
        f"/sudarshan/autologin/internal/{op}",
        data=blob,
        headers={"Content-Type": "application/octet-stream", **JSON},
    )


def test_preflight_answers_a_sealed_request_without_any_session_or_csrf(setup, pepper):
    _, client, _, _ = setup
    blob = seal(fernet_for(pepper, HANDOFF_INFO), "preflight")
    response = _post_sealed(client, "preflight", blob)
    assert response.status_code == 200
    assert response.get_json() == {
        "status": "success",
        "user": True,
        "broker": "upstox",
        "fresh_session": False,
    }
    assert _post_sealed(client, "preflight", blob).status_code == 403  # a nonce is used once
    _fresh_upstox_login()
    again = _post_sealed(client, "preflight", seal(fernet_for(pepper, HANDOFF_INFO), "preflight"))
    assert again.get_json()["fresh_session"] is True


@pytest.mark.parametrize(
    "make_blob",
    [
        lambda pepper: b"",
        lambda pepper: b"not a sealed request",
        lambda pepper: seal(fernet_for("e5" * 32, HANDOFF_INFO), "preflight"),  # another key
        lambda pepper: seal(
            fernet_for(pepper, HANDOFF_INFO), "report", outcome="failed"
        ),  # wrong op
        lambda pepper: seal(fernet_for(pepper, HANDOFF_INFO), "preflight", now=time.time() - 120),
        lambda pepper: seal(fernet_for(pepper, HANDOFF_INFO), "preflight", now=time.time() + 600),
        lambda pepper: b"x" * 20_000,
    ],
)
def test_preflight_rejects_anything_not_genuine(setup, pepper, make_blob):
    _, client, _, _ = setup
    response = _post_sealed(client, "preflight", make_blob(pepper))
    assert response.status_code == 403
    assert set(response.get_json()) == {"status", "message"}


def test_report_of_a_failure_only_tells_the_owner(setup, pepper):
    _, client, vault, calls = setup
    text = (
        "SUDARSHAN\nUpstox auto-login failed.\x00\x1b[31m Please log in by hand before 09:15."
        + "x" * 900
    )
    blob = seal(
        fernet_for(pepper, HANDOFF_INFO),
        "report",
        outcome="failed",
        kind="scheduled",
        telegram=text,
    )
    response = _post_sealed(client, "report", blob)
    assert response.status_code == 200 and response.get_json() == {
        "status": "success",
        "applied": False,
    }
    (telegram,) = [c for c in calls if c[0] == "telegram"]
    telegram_id, sent = telegram[1]
    assert telegram_id == TELEGRAM_ID
    assert "\x00" not in sent and "\x1b" not in sent and len(sent) == bp_module.MAX_TEXT_LEN
    assert auth_db.Auth.query.count() == 0
    auth_db.db_session.remove()


def test_report_is_not_sent_when_the_bot_is_stopped(setup, pepper, monkeypatch):
    _, client, _, calls = setup
    import services.telegram_alert_service as telegram_alerts

    monkeypatch.setattr(telegram_alerts.TelegramAlertService, "is_bot_active", lambda self: False)
    blob = seal(fernet_for(pepper, HANDOFF_INFO), "report", outcome="failed", telegram="x")
    assert _post_sealed(client, "report", blob).status_code == 200
    assert [c for c in calls if c[0] == "telegram"] == []


def test_report_ok_refused_when_openalgo_uses_another_broker(setup, pepper, monkeypatch):
    _, client, _, calls = setup
    monkeypatch.setenv("REDIRECT_URL", "http://127.0.0.1:5000/zerodha/callback")
    blob = seal(fernet_for(pepper, HANDOFF_INFO), "report", outcome="ok", token=FAKE_TOKEN)
    response = _post_sealed(client, "report", blob)
    assert response.status_code == 409 and response.get_json()["applied"] is False
    assert auth_db.Auth.query.count() == 0
    auth_db.db_session.remove()


@pytest.mark.parametrize("token", ["", "   ", None, 12345, "t" * 9000])
def test_report_ok_needs_a_token(setup, pepper, token):
    _, client, _, _ = setup
    blob = seal(fernet_for(pepper, HANDOFF_INFO), "report", outcome="ok", token=token)
    response = _post_sealed(client, "report", blob)
    assert response.status_code == 400 and response.get_json()["applied"] is False
