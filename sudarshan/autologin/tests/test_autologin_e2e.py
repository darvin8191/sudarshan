"""End to end: the worker, a real OpenAlgo app over HTTP on 127.0.0.1, and a fake Upstox.

This test process plays the running gunicorn worker: it holds the app, its databases and its
in-memory token caches. The worker runs in-process (scheduled runs) or as the real child process
"Test now" starts, and both talk to the app only over loopback HTTP, as in production.
"""

from __future__ import annotations

import re
from datetime import timedelta
from types import SimpleNamespace

import pytest
from autologin_app import (
    FAKE_OPENALGO_API_KEY,
    OWNER,
    TELEGRAM_ID,
    LiveServer,
    WallClock,
    add_owner,
    csrf_headers,
    login_session,
    make_app,
    reset_openalgo_db,
    stub_outside_world,
    use_app_state,
)
from autologin_fakes import (
    FAKE_API_KEY,
    FAKE_PIN,
    FAKE_TOKEN,
    FAKE_TOTP_SECRET,
    MONDAY_0835,
    OLD_TOKEN,
    FakeUpstox,
    error_payload,
)

import blueprints.sudarshan_autologin as bp_module
from database import auth_db
from sudarshan.autologin import worker
from sudarshan.autologin.totp import totp
from sudarshan.autologin.worker import EXIT_FAILED, EXIT_OK, SECRET_PIN, SECRET_TOTP
from sudarshan.vault import Vault
from utils.session import has_login_this_trading_session


@pytest.fixture
def world(monkeypatch, tmp_path, vault_path, holidays_file, openalgo_env_file, pepper):
    # Before seeding (upsert_auth calls these too). The stored-token check stays real: it goes to
    # the fake Upstox below, which accepts only the token it issued last.
    calls = stub_outside_world(monkeypatch, token_check=None)
    reset_openalgo_db()
    add_owner()
    # Yesterday's token: the row stays unrevoked until a browser request runs the 03:00 sweep.
    auth_db.upsert_auth(OWNER, OLD_TOKEN, "upstox")
    auth_db.upsert_api_key(OWNER, FAKE_OPENALGO_API_KEY)
    auth_db.db_session.remove()
    calls.clear()

    worker_vault = Vault(vault_path, pepper=pepper)
    worker_vault.set_secret(SECRET_TOTP, FAKE_TOTP_SECRET)
    worker_vault.set_secret(SECRET_PIN, FAKE_PIN)
    worker_vault.set_enabled(True)
    # The app keeps its own Vault object on the same file, as the other process does.
    use_app_state(
        monkeypatch,
        vault=Vault(vault_path, pepper=pepper),
        holidays_file=holidays_file,
        pepper=pepper,
    )
    test_log = tmp_path / "log" / "sudarshan_autologin.log"
    monkeypatch.setenv("SUDARSHAN_AUTOLOGIN_TEST_LOG", str(test_log))

    upstox = FakeUpstox().start()
    monkeypatch.setenv("SUDARSHAN_UPSTOX_API_BASE", upstox.api_base)  # OpenAlgo's check, too
    app = make_app()
    with LiveServer(app) as server:
        env = {
            "SUDARSHAN_OPENALGO_URL": server.url,
            "SUDARSHAN_UPSTOX_API_BASE": upstox.api_base,
            "SUDARSHAN_OPENALGO_ENV_FILE": str(openalgo_env_file),
            "SUDARSHAN_VAULT_DB": str(vault_path),
            "SUDARSHAN_NSE_HOLIDAYS_FILE": str(holidays_file),
        }
        yield SimpleNamespace(
            app=app, calls=calls, env=env, upstox=upstox, vault=worker_vault, test_log=test_log
        )
    upstox.stop()


def _no_secret_in(text: str, *codes: str) -> None:
    for secret in (FAKE_TOTP_SECRET, FAKE_PIN, FAKE_TOKEN, FAKE_API_KEY):
        assert secret not in text
    for code in codes:
        assert not re.search(rf"\b{code}\b", text)


def test_scheduled_login_lands_exactly_like_a_manual_upstox_login(
    world, monkeypatch, caplog, capfd
):
    caplog.set_level("DEBUG")
    # The running OpenAlgo has yesterday's token in memory, for the API, the bot and the proxy.
    assert auth_db.get_auth_token_broker(FAKE_OPENALGO_API_KEY) == (OLD_TOKEN, "upstox")
    assert auth_db.get_auth_token(OWNER) == OLD_TOKEN
    auth_db.db_session.remove()

    # OpenAlgo's clock is the worker's: the session row and the 03:00 rollover below read it.
    clock = WallClock(monkeypatch, MONDAY_0835)
    assert worker.main([], env=world.env, clock=clock) == EXIT_OK

    code = totp(FAKE_TOTP_SECRET, MONDAY_0835.timestamp())
    (request,) = world.upstox.requests
    assert request["path"] == "/v2/totp-login/authorization/token"
    assert request["headers"]["x-api-key"] == FAKE_API_KEY
    assert request["json"] == {"totp": code, "pin": FAKE_PIN}

    # Stored as the OAuth callback stores an Upstox login: no feed token, no user id.
    row = auth_db.Auth.query.filter_by(name=OWNER).one()
    assert auth_db.decrypt_token(row.auth) == FAKE_TOKEN
    assert (row.broker, row.feed_token, row.user_id, bool(row.is_revoked)) == (
        "upstox",
        None,
        None,
        False,
    )
    # Served at once by this process, with no restart: its caches were cleared in-process.
    assert auth_db.get_auth_token_broker(FAKE_OPENALGO_API_KEY) == (FAKE_TOKEN, "upstox")
    assert auth_db.get_auth_token(OWNER) == FAKE_TOKEN
    assert auth_db.get_feed_token(OWNER) is None
    # The real-token-change path ran here: proxy told, pools dropped, order feed restarted.
    assert ("publish_invalidation", (OWNER,), {}) in world.calls
    assert ("cleanup_pools", (OWNER,), {"broker_name": "upstox"}) in world.calls
    assert ("start_order_adapter", (OWNER, "upstox"), {}) in world.calls
    assert (
        "try_start_master_contract_download",
        ("upstox",),
        {"reset_status": True},
    ) in world.calls
    emits = [c for c in world.calls if c[0] == "socketio.emit"]
    assert emits and emits[0][1][0] == "active_sessions_update"
    # The login after 03:00 is on record: a stale phone cookie at 09:00 no longer revokes it,
    # and a restart starts the order-update adapter for it.
    assert has_login_this_trading_session(OWNER)
    (session_row,) = auth_db.ActiveSession.query.filter_by(username=OWNER).all()
    assert session_row.device_info == bp_module.AUTOLOGIN_DEVICE and session_row.broker == "upstox"
    assert session_row.login_time.replace(tzinfo=None) == MONDAY_0835.replace(tzinfo=None)
    (attempt,) = auth_db.LoginAttempt.query.filter_by(username=OWNER).all()
    assert (attempt.status, attempt.login_type, attempt.broker) == (
        "success",
        "auto_totp",
        "upstox",
    )
    auth_db.db_session.remove()

    (telegram,) = [c for c in world.calls if c[0] == "telegram"]
    assert telegram[1][0] == TELEGRAM_ID
    assert telegram[1][1] == "सुदर्शन / SUDARSHAN\nUpstox auto-login OK (08:35 IST)."
    state = world.vault.state()
    assert (state.last_outcome, state.last_success_at) == ("ok", MONDAY_0835)

    # 08:45: Upstox still accepts the stored login, so nothing is done and nothing is revoked.
    clock.at = MONDAY_0835 + timedelta(minutes=10)
    assert worker.main([], env=world.env, clock=clock) == EXIT_OK
    assert len(world.upstox.requests) == 1
    assert world.vault.state().last_code == "already_logged_in"
    assert world.upstox.checks == ["/v3/user/get-funds-and-margin"] * 2  # one check per run

    out = capfd.readouterr()
    _no_secret_in(caplog.text + out.out + out.err, code)


def test_second_auto_login_replaces_its_own_session_row(world):
    assert worker.main(["--test"], env=world.env, clock=lambda: MONDAY_0835) == EXIT_OK
    first = world.vault.state().active_session_id
    world.upstox.default = (200, {"access_token": FAKE_TOKEN + "-2"})
    later = MONDAY_0835 + timedelta(minutes=5)
    assert worker.main(["--test"], env=world.env, clock=lambda: later) == EXIT_OK
    rows = auth_db.ActiveSession.query.filter_by(username=OWNER).all()
    assert [r.session_id for r in rows] == [world.vault.state().active_session_id] != [first]
    assert auth_db.get_auth_token(OWNER) == FAKE_TOKEN + "-2"
    auth_db.db_session.remove()


def test_upstox_refusal_keeps_the_old_row_pauses_and_tells_the_owner(world, caplog):
    caplog.set_level("DEBUG")
    world.upstox.default = (401, error_payload("UDAPI100097", "Invalid Credentials"))
    assert worker.main([], env=world.env, clock=lambda: MONDAY_0835) == EXIT_FAILED
    row = auth_db.Auth.query.filter_by(name=OWNER).one()
    assert auth_db.decrypt_token(row.auth) == OLD_TOKEN
    auth_db.db_session.remove()
    assert world.vault.state().paused_code == "invalid_credentials"
    (telegram,) = [c for c in world.calls if c[0] == "telegram"]
    assert "Upstox did not accept the saved TOTP key or PIN." in telegram[1][1]
    assert telegram[1][1].endswith("Please log in by hand before 09:15.")
    assert not [c for c in world.calls if c[0] == "publish_invalidation"]
    _no_secret_in(caplog.text)  # the error body echoed the PIN; it never reached a log


def test_a_failed_store_leaves_no_login_on_record(world, monkeypatch):
    """If OpenAlgo cannot store the token, the next wake-up must not think the day is done."""

    def broken_upsert(*args, **kwargs):
        raise RuntimeError("disk full (simulated)")

    working_upsert = bp_module.upsert_auth
    monkeypatch.setattr(bp_module, "upsert_auth", broken_upsert)
    assert worker.main([], env=world.env, clock=lambda: MONDAY_0835) == EXIT_FAILED
    assert world.vault.state().last_code == "store_failed"
    assert world.vault.state().active_session_id is None
    assert auth_db.ActiveSession.query.filter_by(username=OWNER).count() == 0
    auth_db.db_session.remove()
    assert not has_login_this_trading_session(OWNER)
    (telegram,) = [c for c in world.calls if c[0] == "telegram"]
    assert "OpenAlgo could not save it" in telegram[1][1]

    # Storage works again: the 08:45 wake-up logs in instead of skipping the day.
    monkeypatch.setattr(bp_module, "upsert_auth", working_upsert)
    later = MONDAY_0835 + timedelta(minutes=10)
    assert worker.main([], env=world.env, clock=lambda: later) == EXIT_OK
    assert len(world.upstox.requests) == 2
    assert auth_db.get_auth_token(OWNER) == FAKE_TOKEN
    auth_db.db_session.remove()


def test_a_store_that_finds_the_database_locked_leaves_no_login_on_record(world, monkeypatch):
    """A real write lock held by another connection past the retry budget.

    The failed commit leaves the session unusable until it is rolled back; without that the
    session row cannot be removed, and the 08:45 wake-up would take the day as done.
    """
    import sqlite3

    import database

    monkeypatch.setattr(database, "LOCK_RETRY_BUDGET_S", 0.3)
    db_file = auth_db.engine.url.database
    working_upsert = bp_module.upsert_auth
    held = []

    def upsert_while_another_writer_holds_the_lock(*args, **kwargs):
        holder = sqlite3.connect(db_file, isolation_level=None, factory=sqlite3.Connection)
        try:
            holder.execute("BEGIN IMMEDIATE")  # the write lock, as a long transaction holds it
            held.append(db_file)
            return working_upsert(*args, **kwargs)
        finally:
            if holder.in_transaction:
                holder.execute("ROLLBACK")
            holder.close()

    monkeypatch.setattr(bp_module, "upsert_auth", upsert_while_another_writer_holds_the_lock)
    assert worker.main([], env=world.env, clock=lambda: MONDAY_0835) == EXIT_FAILED
    assert held and world.vault.state().last_code == "store_failed"
    assert world.vault.state().active_session_id is None
    assert auth_db.ActiveSession.query.filter_by(username=OWNER).count() == 0
    row = auth_db.Auth.query.filter_by(name=OWNER).one()
    assert auth_db.decrypt_token(row.auth) == OLD_TOKEN  # nothing half-written
    auth_db.db_session.remove()
    assert not has_login_this_trading_session(OWNER)

    monkeypatch.setattr(bp_module, "upsert_auth", working_upsert)
    later = MONDAY_0835 + timedelta(minutes=10)
    assert worker.main([], env=world.env, clock=lambda: later) == EXIT_OK
    assert auth_db.get_auth_token(OWNER) == FAKE_TOKEN
    auth_db.db_session.remove()


MONDAY_1000 = MONDAY_0835.replace(hour=10, minute=0)


def test_a_login_that_still_works_is_never_replaced(world):
    """Market hours, no login on record since 03:30, but Upstox still accepts the stored token
    (for example the session row was removed): a TOTP login would end it, so none is made."""
    world.upstox.working_token = OLD_TOKEN
    assert worker.main([], env=world.env, clock=lambda: MONDAY_1000) == EXIT_OK
    assert world.upstox.requests == [] and len(world.upstox.checks) == 1
    assert world.vault.state().last_code == "already_logged_in"
    assert auth_db.get_auth_token(OWNER) == OLD_TOKEN
    auth_db.db_session.remove()


def test_an_unchecked_login_is_not_replaced_in_market_hours(world):
    world.upstox.funds_status = 503  # the check cannot tell
    assert worker.main([], env=world.env, clock=lambda: MONDAY_1000) == EXIT_FAILED
    assert world.upstox.requests == []
    assert world.vault.state().last_code == "token_unchecked"
    (telegram,) = [c for c in world.calls if c[0] == "telegram"]
    assert "could not confirm" in telegram[1][1] and "log in by hand now" in telegram[1][1]
    # Before 09:15 the same answer does not stop the morning login: without a login since 03:30
    # the stored token cannot be today's.
    tuesday_0835 = MONDAY_0835 + timedelta(days=1)
    assert worker.main([], env=world.env, clock=lambda: tuesday_0835) == EXIT_OK
    assert len(world.upstox.requests) == 1


def test_openalgo_down_means_upstox_is_not_asked(world, monkeypatch):
    monkeypatch.setitem(worker.PREFLIGHT_PATIENCE_S, "scheduled", 0.0)  # no 75 s wait here
    env = dict(world.env, SUDARSHAN_OPENALGO_URL="http://127.0.0.1:9")  # nothing listens there
    assert worker.main([], env=env, clock=lambda: MONDAY_0835) == EXIT_FAILED
    assert world.upstox.requests == []
    assert world.vault.state().last_code == "openalgo_unreachable"


def test_openalgo_starting_late_is_waited_for(world, monkeypatch):
    """A boot or a deploy: OpenAlgo refuses connections for a while, then answers."""
    real_preflight = worker.OpenAlgoLink.preflight
    refusals = []

    def not_up_yet(self):
        if len(refusals) < 2:
            refusals.append(1)
            raise worker.LinkError("unreachable")
        return real_preflight(self)

    monkeypatch.setattr(worker.OpenAlgoLink, "preflight", not_up_yet)
    monkeypatch.setattr(worker, "PREFLIGHT_RETRY_S", 0.01)  # real sleeps, made short
    assert worker.main([], env=world.env, clock=lambda: MONDAY_0835) == EXIT_OK
    assert len(refusals) == 2 and len(world.upstox.requests) == 1


def test_test_now_button_runs_the_real_worker_process(world, monkeypatch, tmp_path):
    for key, value in world.env.items():
        monkeypatch.setenv(key, value)  # the child process inherits them
    # The unit's EnvironmentFile reaches the child too, and wins, as it does under systemd.
    settings = tmp_path / "autologin.env"
    env_file = world.env["SUDARSHAN_OPENALGO_ENV_FILE"]
    settings.write_text(f"# settings only\nSUDARSHAN_OPENALGO_ENV_FILE='{env_file}'\n")
    monkeypatch.setenv("SUDARSHAN_AUTOLOGIN_ENV_FILE", str(settings))
    monkeypatch.setenv("SUDARSHAN_OPENALGO_ENV_FILE", str(tmp_path / "missing.env"))  # file wins
    client = world.app.test_client()
    login_session(client)
    response = client.post(
        "/sudarshan/autologin/test",
        headers={"Accept": "application/json", **csrf_headers(client)},
    )
    assert response.status_code == 200, response.get_data(as_text=True)
    data = response.get_json()["data"]
    assert (data["outcome"], data["message"]) == ("ok", "Logged in to Upstox.")

    (request,) = world.upstox.requests
    assert request["json"]["pin"] == FAKE_PIN and len(request["json"]["totp"]) == 6
    assert auth_db.get_auth_token(OWNER) == FAKE_TOKEN
    auth_db.db_session.remove()
    (telegram,) = [c for c in world.calls if c[0] == "telegram"]
    assert telegram[1][1].startswith("सुदर्शन / SUDARSHAN\nTest: Upstox auto-login OK (")

    log_text = world.test_log.read_text()
    assert "auto-login test run finished: ok (ok)" in log_text
    _no_secret_in(log_text, request["json"]["totp"])
    assert world.vault.state().lease_owner is None
