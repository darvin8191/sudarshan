"""A minimal OpenAlgo app for the auto-login tests: the real blueprint, CSRF, limiter and DBs.

Side effects that would leave the machine (the broker order-update socket, the ZeroMQ bus, the
WebSocket pools, Socket.IO, Telegram, the master contract download) are replaced by recorders, so
the tests can assert that each was asked for, the way a manual login asks for it.
"""

from __future__ import annotations

import threading
from datetime import datetime

import pytz
from flask import Flask
from flask_wtf.csrf import CSRFProtect, generate_csrf
from werkzeug.serving import make_server

import blueprints.sudarshan_autologin as bp_module
from database import auth_db, user_db
from limiter import limiter

OWNER = "owner"
# Fake credentials, for the throwaway test database only.
OWNER_PASSWORD = "Fake-Pass-123!"  # pragma: allowlist secret
FAKE_OPENALGO_API_KEY = "fake-openalgo-api-key-" + "0" * 40  # pragma: allowlist secret
TELEGRAM_ID = 4242


def make_app(*, rate_limits: bool = False) -> Flask:
    app = Flask("sudarshan-autologin-tests")
    app.secret_key = "test-only-secret-key-" + "0" * 32  # pragma: allowlist secret
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=True,
        WTF_CSRF_TIME_LIMIT=None,
        RATELIMIT_ENABLED=rate_limits,
    )
    app.csrf = CSRFProtect(app)
    limiter.init_app(app)
    if rate_limits:
        limiter.reset()  # storage exists only when limits are on
    app.register_blueprint(bp_module.sudarshan_autologin_bp)

    @app.route("/csrf-token-for-tests")
    def csrf_token_for_tests():
        return {"csrf_token": generate_csrf()}

    @app.teardown_appcontext
    def remove_sessions(exception=None):
        auth_db.db_session.remove()
        user_db.db_session.remove()

    return app


def use_app_state(monkeypatch, *, vault, holidays_file=None, pepper=None) -> None:
    """Point the blueprint's lazily built vault, calendar and hand-off key at this test's."""
    from sudarshan.autologin.schedule import TradingCalendar
    from sudarshan.vault import HANDOFF_INFO, fernet_for
    from utils.thread_safe_cache import LockedTTLCache

    monkeypatch.setattr(bp_module, "_rejections_warned", LockedTTLCache(maxsize=4, ttl=600))
    monkeypatch.setattr(bp_module, "_vault", lambda: vault)
    if holidays_file is not None:
        calendar = TradingCalendar(holidays_file)
        monkeypatch.setattr(bp_module, "_calendar", lambda: calendar)
    if pepper is not None:
        cipher = fernet_for(pepper, HANDOFF_INFO)
        monkeypatch.setattr(bp_module, "_handoff", lambda: cipher)


class WallClock:
    """One wall clock for the worker and for this server process, as on the real machine.

    The worker takes its clock as an argument; OpenAlgo and the blueprint read the real one when
    they stamp a session's login time (``auth_db._now_ist``), work out today's 03:00 rollover
    (``utils.session``) and decide whether the login on record is today's (``_now``). A test that
    runs the worker at "Monday 08:35" and asserts on those must move all of them together, or it
    passes only when the suite happens to run after 03:00 IST: CI at 00:19 IST failed one.

    Pass the object as the worker's ``clock``; set ``at`` to move every reader at once.
    """

    def __init__(self, monkeypatch, at: datetime):
        import utils.session as openalgo_session
        from sudarshan.autologin.schedule import IST

        self.at = at
        clock = self

        class _ClockDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    return clock.at.astimezone().replace(tzinfo=None)
                return clock.at.astimezone(tz)

        india = pytz.timezone("Asia/Kolkata")
        monkeypatch.setattr(openalgo_session, "datetime", _ClockDatetime)
        monkeypatch.setattr(auth_db, "_now_ist", lambda: clock.at.astimezone(india))
        monkeypatch.setattr(bp_module, "_now", lambda: clock.at.astimezone(IST))

    def __call__(self) -> datetime:
        return self.at


def reset_openalgo_db() -> None:
    auth_db.Base.metadata.create_all(auth_db.engine)
    user_db.Base.metadata.create_all(user_db.engine)
    for model in (auth_db.Auth, auth_db.ApiKeys, auth_db.ActiveSession, auth_db.LoginAttempt):
        model.query.delete()
    auth_db.db_session.commit()
    user_db.User.query.delete()
    user_db.db_session.commit()
    for cache in (
        auth_db.auth_cache,
        auth_db.feed_token_cache,
        auth_db.broker_cache,
        auth_db.verified_api_key_cache,
        auth_db.invalid_api_key_cache,
    ):
        cache.clear()
    auth_db.db_session.remove()
    user_db.db_session.remove()


def add_owner() -> None:
    user_db.add_user(OWNER, "owner@example.invalid", OWNER_PASSWORD, is_admin=True)
    user_db.db_session.remove()


def login_session(client, username: str = OWNER) -> None:
    now_ist = datetime.now(pytz.timezone("Asia/Kolkata"))
    with client.session_transaction() as session:
        session["user"] = username
        session["logged_in"] = True
        session["login_time"] = now_ist.isoformat()


def csrf_headers(client) -> dict:
    token = client.get("/csrf-token-for-tests").get_json()["csrf_token"]
    return {"X-CSRFToken": token}


class Recorder:
    """Records calls; optionally returns a value or raises."""

    def __init__(self, name: str, log: list, result=None, error: Exception | None = None):
        self.name, self.log, self.result, self.error = name, log, result, error

    def __call__(self, *args, **kwargs):
        self.log.append((self.name, args, kwargs))
        if self.error:
            raise self.error
        return self.result


class FakeSocketIO:
    def __init__(self, log: list):
        self.log = log

    def emit(self, event, data=None, **kwargs):
        self.log.append(("socketio.emit", (event, data), kwargs))


def stub_outside_world(
    monkeypatch, *, should_download: bool = True, token_check: str | None = "dead"
) -> list:
    """Replace everything that would leave the process; returns the shared call log.

    ``token_check`` is what Upstox says about the stored token in the preflight; None keeps the
    real check, for a test that points ``SUDARSHAN_UPSTOX_API_BASE`` at a fake Upstox.
    """
    import database.cache_invalidation as cache_invalidation
    import database.telegram_db as telegram_db
    import services.order_update_service as order_updates
    import services.telegram_alert_service as telegram_alerts
    import websocket_proxy.broker_factory as broker_factory

    calls: list = []
    # Inside upsert_auth (imported there at call time): the teardown of a changed token.
    monkeypatch.setattr(
        cache_invalidation,
        "publish_all_cache_invalidation",
        Recorder("publish_invalidation", calls, True),
    )
    monkeypatch.setattr(
        broker_factory, "cleanup_pools_for_user", Recorder("cleanup_pools", calls, 0)
    )
    monkeypatch.setattr(
        order_updates, "start_order_update_adapter", Recorder("start_order_adapter", calls, True)
    )
    monkeypatch.setattr(
        order_updates, "stop_order_update_adapter", Recorder("stop_order_adapter", calls)
    )
    # After upsert_auth: the master contract (same functions handle_auth_success calls).
    monkeypatch.setattr(
        bp_module,
        "should_download_master_contract",
        Recorder("should_download_master_contract", calls, (should_download, "test")),
    )
    monkeypatch.setattr(
        bp_module,
        "try_start_master_contract_download",
        Recorder("try_start_master_contract_download", calls, True),
    )
    monkeypatch.setattr(
        bp_module,
        "try_start_cached_master_contract_load",
        Recorder("try_start_cached_master_contract_load", calls, True),
    )
    monkeypatch.setattr(bp_module, "socketio", FakeSocketIO(calls))
    if token_check is not None:
        monkeypatch.setattr(
            bp_module, "check_token", Recorder("upstox_token_check", calls, token_check)
        )
    # Telegram: the bot is running and the owner is linked; delivery is recorded.
    monkeypatch.setattr(telegram_alerts.TelegramAlertService, "is_bot_active", lambda self: True)
    monkeypatch.setattr(
        telegram_db,
        "get_telegram_user_by_username",
        lambda username: {"telegram_id": TELEGRAM_ID} if username == OWNER else None,
    )
    monkeypatch.setattr(
        telegram_alerts.alert_executor,
        "submit",
        lambda fn, *args: calls.append(("telegram", args, {"fn": getattr(fn, "__name__", "")})),
    )
    return calls


class LiveServer:
    """The test app on 127.0.0.1 over real HTTP, so the worker's loopback calls are real."""

    def __init__(self, app: Flask):
        self.server = make_server("127.0.0.1", 0, app, threaded=True)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def __enter__(self) -> LiveServer:
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()
