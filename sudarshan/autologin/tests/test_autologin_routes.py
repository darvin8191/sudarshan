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
from sudarshan.autologin.handoff import MAX_SEALED_BYTES, seal
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


def test_the_switch_never_ends_a_pause_but_a_saved_secret_does(setup):
    _, client, vault, _ = setup
    login_session(client)
    headers = csrf_headers(client)

    def switch(value):
        return _call(client, "post", "/sudarshan/autologin/enabled", {"enabled": value}, headers)

    assert switch("yes").status_code == 400
    vault.update_status(paused_code="invalid_credentials")
    for value in (False, True, False, True):
        assert switch(value).status_code == 200
        assert vault.state().enabled is value
        assert vault.state().paused_code == "invalid_credentials"
    status = _call(client, "get", "/sudarshan/autologin/status").get_json()["data"]
    assert status["paused"] == {"message": "Upstox did not accept the saved TOTP key or PIN."}
    assert status["next_run"] is None

    _call(client, "post", "/sudarshan/autologin/pin", {"pin": FAKE_PIN}, headers)
    assert vault.state().paused_code is None
    vault.update_status(paused_code="totp_not_enabled")
    _call(client, "post", "/sudarshan/autologin/totp-secret", {"secret": FAKE_TOTP_SECRET}, headers)
    assert vault.state().paused_code is None
    vault.update_status(paused_code="upstox_refused")
    _call(client, "post", "/sudarshan/autologin/pin", {"pin": "12"}, headers)  # invalid: not saved
    assert vault.state().paused_code == "upstox_refused"


def test_test_now_waits_for_a_new_secret_after_a_refusal(setup, monkeypatch):
    _, client, vault, _ = setup
    _ready(vault)
    vault.update_status(paused_code="invalid_credentials")
    started = []
    monkeypatch.setattr(bp_module, "run_test_child", lambda run_id: started.append(run_id))
    login_session(client)
    headers = csrf_headers(client)
    response = _call(client, "post", "/sudarshan/autologin/test", None, headers)
    assert response.status_code == 409 and started == []
    assert response.get_json()["code"] == "test_paused"
    assert "Save the TOTP key or the PIN again first" in response.get_json()["message"]
    status = _call(client, "get", "/sudarshan/autologin/status").get_json()["data"]
    assert status["test"]["allowed"] is False and "paused" in status["test"]["reason"]

    _call(client, "post", "/sudarshan/autologin/totp-secret", {"secret": FAKE_TOTP_SECRET}, headers)
    _call(client, "post", "/sudarshan/autologin/test", None, headers)
    assert len(started) == 1


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


@pytest.mark.parametrize(
    ("hour", "minute", "refused"),
    [(8, 59, False), (9, 0, True), (9, 5, True), (10, 0, True), (15, 29, True), (15, 30, False)],
)
def test_test_now_refuses_from_the_pre_open_to_the_close_when_connected(
    setup, monkeypatch, hour, minute, refused
):
    _, client, vault, calls = setup
    _ready(vault)
    now = datetime.now(IST)
    # Any trading day (a weekday off the fake holiday list) at this time, logged in since 03:30.
    day = next(
        d
        for d in (now.date() + timedelta(days=k) for k in range(7))
        if bp_module._calendar().is_trading_day(d)
    )
    at = datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)
    monkeypatch.setattr(bp_module, "_now", lambda: at)
    monkeypatch.setattr(bp_module, "_holds_fresh_token", lambda username, now=None: True)
    started = []
    monkeypatch.setattr(bp_module, "run_test_child", lambda run_id: started.append(run_id))
    login_session(client)
    response = _call(client, "post", "/sudarshan/autologin/test", None, csrf_headers(client))
    if refused:
        assert response.status_code == 409 and started == []
        message = response.get_json()["message"]
        assert "Test now replaces today's login" in message and "09:00 to 15:30" in message
    else:
        assert started


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
    _, client, _, calls = setup
    blob = seal(fernet_for(pepper, HANDOFF_INFO), "preflight")
    response = _post_sealed(client, "preflight", blob)
    assert response.status_code == 200
    assert response.get_json() == {
        "status": "success",
        "user": True,
        "broker": "upstox",
        "fresh_session": False,
        "token": "none",  # no stored login: nothing to ask Upstox about
    }
    assert [c for c in calls if c[0] == "upstox_token_check"] == []
    assert _post_sealed(client, "preflight", blob).status_code == 403  # a nonce is used once
    _fresh_upstox_login()
    again = _post_sealed(client, "preflight", seal(fernet_for(pepper, HANDOFF_INFO), "preflight"))
    assert again.get_json()["fresh_session"] is True
    assert again.get_json()["token"] == "dead"  # what the (stubbed) check answered
    ((_, (_, base, token), _),) = [c for c in calls if c[0] == "upstox_token_check"]
    assert (base, token) == ("https://api.upstox.com", FAKE_TOKEN)  # the decrypted stored token


@pytest.mark.parametrize(
    ("base", "expected"),
    [("http://192.0.2.10", "unknown"), ("http://127.0.0.1:9", "dead")],
)
def test_the_token_is_sent_only_over_https_or_to_this_server(
    setup, pepper, monkeypatch, base, expected
):
    _, client, _, calls = setup
    monkeypatch.setenv("SUDARSHAN_UPSTOX_API_BASE", base)
    _fresh_upstox_login()
    blob = seal(fernet_for(pepper, HANDOFF_INFO), "preflight")
    assert _post_sealed(client, "preflight", blob).get_json()["token"] == expected
    sent = [c for c in calls if c[0] == "upstox_token_check"]
    assert len(sent) == (0 if expected == "unknown" else 1)


@pytest.mark.parametrize(
    ("minutes_after_midnight", "fresh"),
    [(3 * 60 + 10, False), (3 * 60 + 29, False), (3 * 60 + 30, True), (8 * 60, True)],
)
def test_only_a_login_after_0330_counts_as_today(setup, monkeypatch, minutes_after_midnight, fresh):
    """Upstox ends its tokens at 03:30; OpenAlgo's own rollover is 03:00."""
    _, client, _, _ = setup
    auth_db.upsert_auth(OWNER, FAKE_TOKEN, "upstox")
    login = datetime(2026, 10, 19, tzinfo=IST) + timedelta(minutes=minutes_after_midnight)
    auth_db.register_session(OWNER, "fake-session-id", device_info="phone", broker="upstox")
    row = auth_db.ActiveSession.query.filter_by(session_id="fake-session-id").one()
    row.login_time = login.replace(tzinfo=None)  # stored as IST wall-clock time
    auth_db.db_session.commit()
    now = datetime(2026, 10, 19, 9, 0, tzinfo=IST)
    assert bp_module._holds_fresh_token(OWNER, now) is fresh
    auth_db.db_session.remove()


@pytest.mark.parametrize(
    "make_blob",
    [
        lambda pepper: b"not a sealed request",
        lambda pepper: seal(fernet_for("e5" * 32, HANDOFF_INFO), "preflight"),  # another key
        lambda pepper: seal(
            fernet_for(pepper, HANDOFF_INFO), "report", outcome="failed"
        ),  # wrong op
        lambda pepper: seal(fernet_for(pepper, HANDOFF_INFO), "preflight", now=time.time() - 120),
        lambda pepper: seal(fernet_for(pepper, HANDOFF_INFO), "preflight", now=time.time() + 600),
        lambda pepper: b"x" * MAX_SEALED_BYTES,
    ],
)
def test_preflight_rejects_anything_not_genuine(setup, pepper, make_blob):
    _, client, _, _ = setup
    response = _post_sealed(client, "preflight", make_blob(pepper))
    assert response.status_code == 403
    assert set(response.get_json()) == {"status", "message"}


# nginx sets these on every proxied location; the addresses are RFC 5737 documentation ones.
PROXIED = [
    {"X-Forwarded-For": "192.0.2.10"},
    {"X-Real-IP": "192.0.2.10"},
    {"Forwarded": "for=192.0.2.10;proto=https"},
    {"X-Forwarded-Proto": "https"},
    {"X-Forwarded-Host": "example.invalid"},
    {"x-forwarded-for": "127.0.0.1"},  # any value, any spelling
]


@pytest.mark.parametrize("op", ["preflight", "report"])
@pytest.mark.parametrize("proxied", PROXIED)
def test_requests_that_came_through_nginx_are_not_found(setup, pepper, monkeypatch, op, proxied):
    _, client, _, _ = setup
    opened = []
    real_open = bp_module.open_sealed
    monkeypatch.setattr(
        bp_module, "open_sealed", lambda *a, **k: opened.append(op) or real_open(*a, **k)
    )
    fields = {"outcome": "failed", "telegram": "x"} if op == "report" else {}
    blob = seal(fernet_for(pepper, HANDOFF_INFO), op, **fields)
    response = client.post(
        f"/sudarshan/autologin/internal/{op}",
        data=blob,
        headers={"Content-Type": "application/octet-stream", **JSON, **proxied},
    )
    assert response.status_code == 404 and opened == []  # refused before the body was read
    assert _post_sealed(client, op, blob).status_code == 200  # its nonce was never used


def test_sealed_routes_need_a_declared_small_body(setup):
    _, client, _, _ = setup
    path = "/sudarshan/autologin/internal/preflight"
    assert _post_sealed(client, "preflight", b"").status_code == 411
    chunked = client.post(
        path,
        data=b"x" * 64,
        headers={"Content-Type": "application/octet-stream", "Transfer-Encoding": "chunked"},
    )
    assert chunked.status_code == 411
    assert _post_sealed(client, "preflight", b"x" * (MAX_SEALED_BYTES + 1)).status_code == 413


def test_junk_cannot_use_up_a_limit_and_lock_out_the_worker(
    monkeypatch, vault, holidays_file, pepper, caplog
):
    """Behind nginx every client is 127.0.0.1, so a per-address limit here was a shared one."""
    reset_openalgo_db()
    add_owner()
    use_app_state(monkeypatch, vault=vault, holidays_file=holidays_file, pepper=pepper)
    stub_outside_world(monkeypatch)
    client = make_app(rate_limits=True).test_client()
    caplog.set_level("DEBUG", logger=bp_module.logger.name)
    codes = set()
    for op in ("preflight", "report"):
        path = f"/sudarshan/autologin/internal/{op}"
        for index in range(60):
            junk = f"junk {index}".encode()
            codes.add(client.post(path, data=junk, headers=PROXIED[index % 3]).status_code)
            codes.add(_post_sealed(client, op, junk).status_code)
    assert codes == {403, 404}

    cipher = fernet_for(pepper, HANDOFF_INFO)
    assert _post_sealed(client, "preflight", seal(cipher, "preflight")).status_code == 200
    report = seal(cipher, "report", outcome="failed", telegram="x")
    assert _post_sealed(client, "report", report).status_code == 200

    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert [r.getMessage().split(" request")[0] for r in warnings] == [
        "Auto-login: rejected a preflight",
        "Auto-login: rejected a report",
    ]
    assert (
        sum(r.levelname == "DEBUG" and "rejected" in r.getMessage() for r in caplog.records) == 120
    )


def test_starting_openalgo_moves_a_legacy_vault_and_never_fails_on_it(monkeypatch):
    moved = []
    monkeypatch.setattr(bp_module, "move_legacy_vault", lambda: moved.append(1) or True)
    make_app()
    assert moved == [1]

    def read_only_disk():
        raise OSError("read-only file system")

    monkeypatch.setattr(bp_module, "move_legacy_vault", read_only_disk)
    assert make_app().test_client() is not None  # OpenAlgo still starts


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
