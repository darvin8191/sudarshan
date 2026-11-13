"""SUDARSHAN review HIGH-2: ending every session on one device ends it on every device at once.

Flask's session is a signed cookie the server cannot reach. A logout (which also signs Upstox out)
or a password change cleared the cookie of the device it came from, removed every session row and
told the open screens (force_logout), but every other device's cookie kept passing every check
until the daily expiry at 03:00: its reads and writes, its pages and its live connection's
reconnect. A phone asleep at the time woke up still signed in. Now the server keeps, per user, the
moment every session was last ended at once (database.auth_db.SessionCutoff), and every check
refuses a cookie that logged in before it. A login after it is untouched, so a second device's
login never signs the first out, and nothing in it reads or changes the broker feed.

Each test fails on the code before the change, for the reason its docstring gives. They run
through Flask's test client (threading, as the dev server runs it) and last through a real
gunicorn worker, eventlet and gthread.
"""

from __future__ import annotations

import gc
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
import pytz
from bridge_app import OWNER, OWNER_PASSWORD, csrf_headers, make_app, reset_users
from flask import session
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool

os.environ.setdefault("LOGS_DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/logs-unused.db")

import blueprints.auth as auth_module  # noqa: E402
import blueprints.security as security_module  # noqa: E402
import database.auth_db as auth_db  # noqa: E402
import database.cache_invalidation as cache_invalidation  # noqa: E402
import services.order_update_service as order_updates  # noqa: E402
import utils.auth_utils as auth_utils  # noqa: E402
import websocket_proxy.broker_factory as broker_factory  # noqa: E402
from database import user_db  # noqa: E402
from extensions import socketio  # noqa: E402
from utils import session as session_utils  # noqa: E402
from utils.session import check_session_validity  # noqa: E402
from utils.socketio_error_handler import init_socketio_error_handling  # noqa: E402

TESTS_DIR = Path(__file__).resolve().parent
REPO = TESTS_DIR.parents[2]
IST = pytz.timezone("Asia/Kolkata")
TOKEN = "fake-upstox-token"  # pragma: allowlist secret
NEW_PASSWORD = "Fake-Pass-456!"  # pragma: allowlist secret
JSON = {"Accept": "application/json"}


def reset_sessions() -> None:
    """No session rows, no stored Upstox login, no logout on record, nothing cached."""
    auth_db.Base.metadata.create_all(auth_db.engine)
    for model in (auth_db.Auth, auth_db.ActiveSession, auth_db.LoginAttempt):
        model.query.delete()
    auth_db.db_session.commit()
    auth_db.db_session.remove()
    for cache in (auth_db.auth_cache, auth_db.feed_token_cache, auth_db.broker_cache):
        cache.clear()
    reset_users()  # the owner again, and no logout of every device on record


@pytest.fixture
def world(monkeypatch):
    """The bridge app with OpenAlgo's login and logout routes, the Security screen's session list,
    Socket.IO bound as app.py binds it, and two routes behind the session check every page and
    read uses. Nothing reaches a broker, the ZeroMQ bus or the master contract; what reaches the
    feed teardown is recorded. The daily expiry is at 00:00 IST, so a login today is inside its
    day at any hour."""
    reset_sessions()
    monkeypatch.delenv("DISABLE_SESSION_EXPIRY", raising=False)
    monkeypatch.setenv("SESSION_EXPIRY_TIME", "00:00")
    teardown: list = []
    monkeypatch.setattr(cache_invalidation, "publish_all_cache_invalidation",
                        lambda name: teardown.append(("publish", name)) or True)  # fmt: skip
    monkeypatch.setattr(broker_factory, "cleanup_pools_for_user",
                        lambda name, broker_name=None: teardown.append(("pools", name)) or 0)  # fmt: skip
    monkeypatch.setattr(order_updates, "start_order_update_adapter", lambda *a, **k: None)
    monkeypatch.setattr(order_updates, "stop_order_update_adapter", lambda *a, **k: None)
    monkeypatch.setattr(auth_utils, "should_download_master_contract", lambda b: (False, "test"))
    monkeypatch.setattr(auth_utils, "try_start_cached_master_contract_load", lambda b: None)
    monkeypatch.setattr(socketio, "default_exception_handler", None)  # put back after the test
    app = make_app()
    socketio.init_app(app)
    init_socketio_error_handling(socketio)
    app.register_blueprint(auth_module.auth_bp)
    app.register_blueprint(security_module.security_bp)
    app.teardown_appcontext(lambda exception=None: auth_db.db_session.remove())

    @app.route("/login-for-tests")
    def login_for_tests():
        """A device's login as OpenAlgo finishes it: the password step leaves the user in the
        session, and the broker step (the callback, or a resumed session) ends here."""
        session.clear()
        session["user"] = OWNER
        return auth_utils.handle_auth_success(TOKEN, OWNER, "upstox")

    @app.route("/read-for-tests")
    @check_session_validity
    def read_for_tests():
        return {"status": "success"}

    @app.route("/page-for-tests")
    @check_session_validity
    def page_for_tests():
        return "<p>a page</p>"

    def connect(http):
        return socketio.test_client(app, flask_test_client=http)

    yield app, connect, teardown
    reset_users()  # no logout of every device left on record for a later test's earlier clock


def device(app, ip: str):
    """A browser on its own address, as a phone on mobile data or a laptop on Wi-Fi is."""
    http = app.test_client()
    http.environ_base["REMOTE_ADDR"] = ip
    http.environ_base["HTTP_USER_AGENT"] = f"device at {ip}"
    return http


def log_in(http) -> None:
    answer = http.get("/login-for-tests", headers=JSON)
    assert answer.status_code == 200, answer.get_data(as_text=True)


def answered(http) -> bool:
    """A device's next read: answered (True), or refused as a session that has ended."""
    answer = http.get("/read-for-tests", headers=JSON)
    if answer.status_code == 401:
        assert answer.get_json()["error"] == "session_expired"
        return False
    assert answer.status_code == 200, answer.get_data(as_text=True)
    return True


def end_every_session_on(http, ending: str) -> None:
    if ending == "logout":
        answer = http.post("/auth/logout", headers=csrf_headers(http))
    else:
        fields = {"old_password": OWNER_PASSWORD, "new_password": NEW_PASSWORD,
                  "confirm_password": NEW_PASSWORD}  # fmt: skip
        answer = http.post("/auth/change-password", data=fields, headers=csrf_headers(http))
    assert answer.status_code == 200, answer.get_data(as_text=True)


def devices_listed() -> list[str]:
    return sorted(row["device_info"] for row in auth_db.get_active_sessions(OWNER))


def answer_to(http, ending: str):
    """The answer a logout, a password change (the Profile page's or the older form's) or a reset
    gives the device it came from."""
    if ending == "logout":
        return http.post("/auth/logout", headers=csrf_headers(http))
    if ending == "password change":
        fields = {"old_password": OWNER_PASSWORD, "new_password": NEW_PASSWORD,
                  "confirm_password": NEW_PASSWORD}  # fmt: skip
        return http.post("/auth/change-password", data=fields, headers=csrf_headers(http))
    if ending == "password form":
        fields = {"old_password": OWNER_PASSWORD, "new_password": NEW_PASSWORD}
        return http.post("/auth/change", json=fields, headers=csrf_headers(http))
    assert ending == "reset"
    import pyotp

    email = user_db.find_user_by_username().email
    code = pyotp.TOTP(user_db.find_user_by_username().get_totp_secret()).now()
    user_db.db_session.remove()
    http.post("/auth/reset-password", json={"step": "email", "email": email},
              headers=csrf_headers(http))  # fmt: skip
    token = http.post(
        "/auth/reset-password", json={"step": "totp", "email": email, "totp_code": code},
        headers=csrf_headers(http),
    ).get_json()["token"]  # fmt: skip
    return http.post(
        "/auth/reset-password",
        json={"step": "password", "email": email, "token": token, "password": NEW_PASSWORD},
        headers=csrf_headers(http),
    )  # fmt: skip


ENDINGS = ["logout", "password change", "password form", "reset"]


@pytest.mark.parametrize("ending", ["logout", "password change"])
def test_ending_every_session_on_one_device_ends_the_others_on_their_next_request(world, ending):
    """Before: after the laptop's logout or password change the phone's socket was let back in on
    its reconnect, its read answered, the tablet's session check read signed in and the desktop's
    page opened, until 03:00. Now each is refused on its next request the way an ended session is:
    the socket is refused, the read gets the 401 the app answers with the login page, the session
    check reads signed out, the page goes to the login page. The refusals tear nothing down: the
    logout already did what it does to the broker login, and a password change keeps it, so a
    refusal running the daily expiry's teardown (no login after 03:00 left on record) would
    revoke the Upstox login the owner still trades on. Their rows are gone, and a fresh login on
    any device works."""
    app, connect, teardown = world
    laptop, phone, tablet, desktop = (device(app, f"192.0.2.{number}") for number in (1, 2, 3, 4))
    for http in (laptop, phone, tablet, desktop):
        log_in(http)
    assert all(answered(http) for http in (laptop, phone, tablet, desktop))
    socket = connect(phone)
    assert socket.is_connected()

    end_every_session_on(laptop, ending)
    after_ending = list(teardown)
    assert not socket.is_connected()  # closed by the ending, as since SIO1
    assert not connect(phone).is_connected()  # its reconnect, with the cookie it still holds
    assert not answered(phone)
    assert tablet.get("/auth/session-status").get_json()["authenticated"] is False
    page = desktop.get("/page-for-tests")
    assert page.status_code == 302 and page.headers["Location"].endswith("/auth/login")
    assert teardown == after_ending
    if ending == "password change":
        assert auth_db.get_auth_token(OWNER) == TOKEN  # the Upstox login is kept
    assert devices_listed() == []

    log_in(phone)
    assert answered(phone)
    assert connect(phone).is_connected()
    assert devices_listed() == ["device at 192.0.2.2"]


def test_a_second_device_s_login_never_signs_the_first_out_or_touches_the_feed(world):
    """A second login from the same address replaces the first device's session row
    (register_session). The login page's own check read the missing row as a logout and signed
    the first device out (SIO1's accepted edge, until the server kept this time). Now no check
    reads the row: the first device stays signed in everywhere and its live connection stays
    admitted, and the second login, which stores the same Upstox login, leaves the feed alone
    (issue #1591)."""
    app, connect, teardown = world
    laptop, phone = device(app, "192.0.2.7"), device(app, "192.0.2.7")
    log_in(laptop)
    socket = connect(laptop)
    teardown.clear()  # the first login's own new token
    log_in(phone)
    assert teardown == []
    assert len(auth_db.get_active_sessions(OWNER)) == 1  # the laptop's row was replaced
    assert socket.is_connected()
    assert answered(laptop)
    for path in ("/auth/session-status", "/auth/session-status?for=login"):
        status = laptop.get(path).get_json()
        assert (status["authenticated"], status["logged_in"]) == (True, True), path
    assert connect(laptop).is_connected()
    assert answered(phone)


def test_rows_of_sessions_the_daily_expiry_ended_go_at_a_login_and_from_the_screen(world):
    """Two phones' rows from October stayed in Active Sessions: a phone whose cookie lapsed in its
    browser never comes back to remove its own row, and only a logout cleared them. Now each login
    removes the rows of sessions the daily expiry has already ended, and so does opening the Active
    Sessions screen; today's rows stay."""
    app, connect, teardown = world

    def october_rows():
        for number in (1, 2):
            session_id = f"{number}" * 64
            assert auth_db.register_session(
                OWNER, session_id, f"Android phone {number}", f"198.51.100.{number}", "upstox"
            )
            row = auth_db.ActiveSession.query.filter_by(session_id=session_id).one()
            row.login_time = row.last_seen = datetime(2026, 10, 20 + number, 9, 15)  # IST
            auth_db.db_session.commit()
        auth_db.db_session.remove()

    october_rows()
    laptop = device(app, "192.0.2.1")
    log_in(laptop)
    assert devices_listed() == ["device at 192.0.2.1"]

    october_rows()  # as they stood on the server when this release was installed
    listed = laptop.get("/security/api/active-sessions", headers=JSON).get_json()
    assert [row["device_info"] for row in listed["sessions"]] == ["device at 192.0.2.1"]
    assert devices_listed() == ["device at 192.0.2.1"]


def test_a_cookie_also_past_the_daily_expiry_takes_the_expiry_s_path_as_before(world, monkeypatch):
    """The order of the two checks. A cookie from before a logout of every device that is also past
    the daily expiry is ended by the expiry, which revokes the stored token when no login after
    the rollover is on record (#1419); only a cookie the expiry has not ended is signed out
    without it. Before the change no logout was on record and the expiry path ran alone."""
    app, connect, teardown = world
    revoked = []
    monkeypatch.setattr(auth_db, "upsert_auth", lambda *a, **k: revoked.append(k) or 1)
    monkeypatch.setattr(session_utils, "_has_fresher_session", lambda *a, **k: False)
    yesterday = datetime.now(IST) - timedelta(days=1)
    today = datetime.now(IST) - timedelta(seconds=5)
    if today.date() != datetime.now(IST).date():
        pytest.skip("too close to midnight IST for 'today' to be today")
    with app.test_request_context("/"):
        session_utils.end_every_session(OWNER)  # a logout of every device, now
        for login_time, expected in ((today, []), (yesterday, [{"revoke": True}])):
            revoked.clear()
            session.update(user=OWNER, logged_in=True, login_time=login_time.isoformat())
            assert not session_utils.is_session_valid()
            session_utils.revoke_user_tokens()
            assert revoked == expected, login_time


def test_a_logout_record_that_cannot_be_read_signs_nobody_out_and_is_read_again(world, monkeypatch):
    """A database hiccup must not sign every device out: the check is skipped for that request
    (and logged), the daily expiry still applies, and the next request reads again (a failed read
    is never cached). Before the change there was no such read."""
    app, connect, teardown = world
    phone = device(app, "192.0.2.2")
    log_in(phone)
    real = auth_db._load_sessions_valid_after
    calls = []

    def unreadable(username):
        calls.append(username)
        raise RuntimeError("the session_cutoffs table could not be read")

    auth_db.session_cutoff_cache.invalidate()
    monkeypatch.setattr(auth_db, "_load_sessions_valid_after", unreadable)
    assert answered(phone)
    assert answered(phone)
    assert len(calls) >= 2  # read again each time, never cached
    monkeypatch.setattr(auth_db, "_load_sessions_valid_after", real)
    with app.test_request_context("/"):
        session_utils.end_every_session(OWNER)
    assert not answered(phone)


@pytest.mark.parametrize("ending", ENDINGS)
def test_a_logout_time_that_cannot_be_written_is_tried_again_and_then_said(
    world, monkeypatch, ending
):
    """Review L6: the write of the logout time failed silently (the database locked past its
    retries, or a full disk): the answer said logged out, or that the password was changed, and
    every other device stayed signed in until 03:00. Now it is tried once more, and when that
    fails too the answer says the other devices were not signed out and what to do."""
    app, connect, teardown = world
    laptop = device(app, "192.0.2.1")
    log_in(laptop)
    calls = []

    def cannot_write(username, when):
        calls.append(when)
        return False

    monkeypatch.setattr(auth_db, "set_sessions_valid_after", cannot_write)
    answer = answer_to(laptop, ending)

    assert answer.status_code == 200, answer.get_data(as_text=True)
    body = answer.get_json()
    assert body["status"] == "error"
    assert "your other devices could not be signed out" in body["message"]
    assert "log out" in body["message"].lower()  # what to do
    assert len(calls) == 2  # tried once more, at the same moment
    assert calls[0] == calls[1]


@pytest.mark.parametrize("ending", ENDINGS)
def test_a_logout_time_written_on_the_second_try_ends_the_other_devices(world, monkeypatch, ending):
    app, connect, teardown = world
    laptop, phone = device(app, "192.0.2.1"), device(app, "192.0.2.2")
    log_in(laptop)
    log_in(phone)
    real, tries = auth_db.set_sessions_valid_after, []

    def second_try_works(username, when):
        tries.append(when)
        return False if len(tries) == 1 else real(username, when)

    monkeypatch.setattr(auth_db, "set_sessions_valid_after", second_try_works)
    answer = answer_to(laptop, ending)

    assert answer.get_json()["status"] == "success", answer.get_json()
    assert len(tries) == 2
    assert not answered(phone)


@pytest.mark.parametrize("ending", ["password change", "password form", "reset"])
def test_a_password_change_keeps_the_auto_login_s_row_so_an_old_cookie_revokes_nothing(
    world, ending
):
    """Review L7. Auto-login signs Upstox in at 08:45 and records a session row with no address
    (it makes no cookie); a password change at 11:00 keeps the Upstox login. Before, the change
    removed that row with the rest, so a tablet still holding yesterday's cookie that opened a page
    before the laptop signed in again found no login after the rollover on record, and the daily
    expiry's teardown revoked the Upstox login the change had kept, and told every screen to log
    out. The row is kept now: it is no device's session, every cookie is still ended by the
    logout time. A logout still removes it, as it revokes the Upstox login anyway."""
    from blueprints.sudarshan_autologin import AUTOLOGIN_DEVICE

    app, connect, teardown = world
    laptop, tablet = device(app, "192.0.2.1"), device(app, "192.0.2.3")
    log_in(laptop)
    assert auth_db.register_session(OWNER, "a" * 64, AUTOLOGIN_DEVICE, None, "upstox")
    with tablet.session_transaction() as cookie:  # signed in yesterday, before the rollover
        cookie.update(user=OWNER, logged_in=True, broker="upstox", session_id="t" * 64,
                      login_time=(datetime.now(IST) - timedelta(days=1)).isoformat())  # fmt: skip
    teardown.clear()

    assert answer_to(laptop, ending).get_json()["status"] == "success"
    page = tablet.get("/page-for-tests")

    assert page.status_code == 302 and page.headers["Location"].endswith("/auth/login")
    assert auth_db.get_auth_token(OWNER) == TOKEN  # the Upstox login the change kept
    assert teardown == []  # no feed teardown
    assert devices_listed() == [AUTOLOGIN_DEVICE]  # and no browser's row


def test_a_logout_still_removes_the_auto_login_s_row(world):
    from blueprints.sudarshan_autologin import AUTOLOGIN_DEVICE

    app, connect, teardown = world
    laptop = device(app, "192.0.2.1")
    log_in(laptop)
    assert auth_db.register_session(OWNER, "a" * 64, AUTOLOGIN_DEVICE, None, "upstox")
    assert answer_to(laptop, "logout").get_json()["status"] == "success"
    assert devices_listed() == []


def test_removing_ended_rows_leaves_none_of_them_in_the_request_s_identity_map(world):
    """Review L8: remove_expired_sessions deleted with synchronize_session=False, which leaves a
    row the request had already loaded in the session's identity map. SQLite gives the next
    insert the highest deleted id, and the new row then collided with the stale object (CI
    logged "SAWarning: Identity map already had an identity for (ActiveSession, (1,))")."""
    import warnings

    from sqlalchemy.exc import SAWarning

    assert auth_db.register_session(OWNER, "o" * 64, "October phone", "198.51.100.9", "upstox")
    row = auth_db.ActiveSession.query.filter_by(session_id="o" * 64).one()
    row.login_time = row.last_seen = datetime(2026, 10, 21, 9, 15)  # IST, long ended
    auth_db.db_session.commit()
    loaded = auth_db.ActiveSession.query.filter_by(session_id="o" * 64).one()  # in the map

    with warnings.catch_warnings():
        warnings.simplefilter("error", SAWarning)
        assert auth_db.register_session(OWNER, "n" * 64, "new phone", "198.51.100.10", "upstox")

    assert devices_listed() == ["new phone"]
    assert loaded not in auth_db.db_session  # the deleted row left the session with the delete


# -- the migration --------------------------------------------------------------------------


MIGRATION = REPO / "upgrade" / "migrate_session_cutoffs.py"


def _run_migration(database: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "API_KEY_PEPPER")}
    env["DATABASE_URL"] = f"sqlite:///{database}"
    return subprocess.run([sys.executable, str(MIGRATION), *args], cwd=REPO / "upgrade", env=env,
                          capture_output=True, text=True, timeout=60)  # fmt: skip


def _counts(engine) -> dict:
    tables = ("users", "auth", "active_sessions", "login_attempts")
    with engine.connect() as connection:
        return {t: connection.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar() for t in tables}


def test_the_migration_adds_the_table_to_a_populated_database_and_touches_nothing_else(tmp_path):
    """A populated openalgo.db as a server has it before this release: users, the stored Upstox
    login, session rows and login records, with the new table dropped. --status reports it and
    changes nothing; the migration adds it, exactly as the app's model declares it, and leaves
    every row where it was; a second run does nothing. The app's own start creates it too
    (auth_db.init_db), so the migration is not required."""
    database = tmp_path / "openalgo.db"
    engine = create_engine(f"sqlite:///{database}", poolclass=NullPool)
    try:
        auth_db.Base.metadata.create_all(engine)
        user_db.Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.execute(text(
                "INSERT INTO users (username, email, password_hash, totp_secret, is_admin, "
                "totp_enabled, totp_required_for_login, totp_required_for_mcp, "
                "totp_required_for_password_reset) VALUES ('owner', 'owner@example.invalid', "
                "'x', 'x', 1, 0, 0, 0, 0)"))  # fmt: skip
            connection.execute(text("INSERT INTO auth (name, auth, broker, is_revoked) "
                                    "VALUES ('owner', 'sealed', 'upstox', 0)"))  # fmt: skip
            for number in (1, 2):
                connection.execute(text(
                    "INSERT INTO active_sessions (username, session_id, device_info, ip_address, "
                    "broker, login_time, last_seen) VALUES ('owner', :sid, 'Android phone', "
                    "'198.51.100.1', 'upstox', '2026-10-21 09:15:00', '2026-10-21 09:15:00')"),
                    {"sid": str(number) * 64})  # fmt: skip
            connection.execute(text("INSERT INTO login_attempts (username, status) "
                                    "VALUES ('owner', 'success')"))  # fmt: skip
            connection.execute(text("DROP TABLE session_cutoffs"))  # the schema before
        before = _counts(engine)

        status = _run_migration(database, "--status")
        assert status.returncode == 0, status.stdout + status.stderr
        assert "MISSING" in status.stdout and "Migration needed" in status.stdout
        assert "session_cutoffs" not in inspect(engine).get_table_names()

        applied = _run_migration(database)
        assert applied.returncode == 0, applied.stdout + applied.stderr
        assert "Created session_cutoffs" in applied.stdout
        columns = {c["name"]: c for c in inspect(engine).get_columns("session_cutoffs")}
        model = auth_db.SessionCutoff.__table__.columns
        assert set(columns) == {column.name for column in model}
        for column in model:
            made = columns[column.name]
            assert bool(made["primary_key"]) == column.primary_key
            assert made["nullable"] == (column.nullable and not column.primary_key)
            assert str(made["type"]) == column.type.compile(dialect=engine.dialect)
        assert _counts(engine) == before

        again = _run_migration(database)
        assert again.returncode == 0 and "Up to date" in again.stdout, again.stdout
        status = _run_migration(database, "--status")
        assert "Up to date" in status.stdout and _counts(engine) == before
    finally:
        engine.dispose()


# -- under a real gunicorn worker -----------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _stop_group(proc: subprocess.Popen, grace_s: float = 5.0) -> None:
    """SIGTERM to the server's process group, SIGKILL for what is left, the leader reaped."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        deadline = time.monotonic() + grace_s
        while proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
    except ProcessLookupError:
        pass
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=15)


@contextmanager
def serving(tmp_path, worker_class: str):
    """sessions_gunicorn_app on a free port, with its own database."""
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    command = [
        sys.executable, "-m", "gunicorn", "--worker-class", worker_class, "-w", "1",
        *(["--threads", "8"] if worker_class == "gthread" else []),
        "--bind", f"127.0.0.1:{port}", "--timeout", "120", "--chdir", str(REPO),
        "sessions_gunicorn_app:app",
    ]  # fmt: skip
    env = {
        **{k: v for k, v in os.environ.items()
           if not k.lower().endswith("_proxy") and k != "DISABLE_SESSION_EXPIRY"},
        "API_KEY_PEPPER": os.environ["API_KEY_PEPPER"],
        "APP_KEY": "test-only-app-key-" + "0" * 32,
        "DATABASE_URL": f"sqlite:///{tmp_path}/openalgo-gunicorn-test.db",
        "LOGS_DATABASE_URL": f"sqlite:///{tmp_path}/logs-gunicorn-test.db",
        "LOG_DIR": str(tmp_path / "log"),
        "REDIRECT_URL": "http://127.0.0.1:5000/upstox/callback",
        "PYTHONPATH": os.pathsep.join([str(REPO), str(TESTS_DIR)]),
        "XDG_RUNTIME_DIR": str(tmp_path),
        "SESSION_EXPIRY_TIME": "00:00",
    }  # fmt: skip
    with (tmp_path / "gunicorn.log").open("wb") as server_log:
        server = subprocess.Popen(command, env=env, stdout=server_log, stderr=subprocess.STDOUT,
                                  start_new_session=True)  # fmt: skip
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                if httpx.get(f"{url}/csrf-token-for-tests", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            assert server.poll() is None, (tmp_path / "gunicorn.log").read_text()
            assert time.monotonic() < deadline, "gunicorn did not start"
            time.sleep(0.3)
        yield url
    finally:
        _stop_group(server)


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")
@pytest.mark.parametrize("worker_class", ["eventlet", "gthread"])
def test_under_gunicorn_a_logout_ends_the_other_device_and_a_new_login_works(
    tmp_path, worker_class
):
    """The worker's own cache and database, under each worker class: before the change the phone
    kept reading and was let back in on its reconnect after the laptop's logout."""
    pytest.importorskip("gunicorn", reason="gunicorn is installed by the production installer")
    if worker_class == "eventlet":
        pytest.importorskip("eventlet", reason="eventlet is installed by the production installer")
    import requests
    import socketio as sio_client

    with serving(tmp_path, worker_class) as url:
        browsers = []

        def browser():
            http = requests.Session()
            http.trust_env = False
            browsers.append(http)
            assert http.get(f"{url}/login-for-tests", headers=JSON, timeout=20).status_code == 200
            return http

        def reads(http) -> int:
            return http.get(f"{url}/read-for-tests", headers=JSON, timeout=20).status_code

        def admitted(http) -> bool:
            client = sio_client.Client(http_session=http, reconnection=False)
            try:
                client.connect(url, transports=["polling"], wait_timeout=20)
            except sio_client.exceptions.ConnectionError:
                return False
            client.disconnect()
            return True

        try:
            laptop, phone = browser(), browser()
            assert [reads(phone) for _ in range(5)] == [200] * 5  # the quiet path, as before
            assert admitted(phone)
            token = laptop.get(f"{url}/csrf-token-for-tests", timeout=20).json()["csrf_token"]
            logout = laptop.post(f"{url}/auth/logout", headers={"X-CSRFToken": token}, timeout=20)
            assert logout.status_code == 200
            assert not admitted(phone)
            assert reads(phone) == 401
            status = phone.get(f"{url}/auth/session-status", timeout=20).json()
            assert status["authenticated"] is False
            again = browser()
            assert reads(again) == 200 and admitted(again)
        finally:
            for http in browsers:
                http.close()
            gc.collect()
