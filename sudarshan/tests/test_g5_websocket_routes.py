"""G5: OpenAlgo's /api/websocket/* routes answer the owner's valid session only.

Upstream's routes (blueprints/websocket_example.py) read only whether the session named a user:
nine answered every other caller with a 200 carrying an error, and /api/websocket/apikey handed
the order-capable API key to any session naming a user, a login stopped after the password step
included. Now each answers a valid session (logged in through the broker step, not past the daily
expiry, not ended by a logout of every device) and refuses anything else with a 401 in the JSON
check_session_validity answers a fetch with, leaving the session as it was.

Each test says how the code before G5 failed it. The owner's screens fetch only the API key and
the stream address, and those answer the owner's session as before.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
import pytz
from flask import Flask

# Imported before any test binds Socket.IO to an app, as the SIO1 tests do: a handler registered
# after that is kept by that server only and lost at the next init_app.
import blueprints.websocket_example as market
import database.auth_db as auth_db
from database import user_db

IST = pytz.timezone("Asia/Kolkata")
OWNER = "owner"
FAKE_API_KEY = "g5-fake-api-key-for-tests"  # pragma: allowlist secret
#: Every /api/websocket/* rule: its method, and the body a POST sends.
ROUTES = [
    ("GET", "/api/websocket/status", None),
    ("GET", "/api/websocket/subscriptions", None),
    ("POST", "/api/websocket/subscribe", {"symbols": [], "mode": "Quote"}),
    ("POST", "/api/websocket/unsubscribe", {"symbols": [], "mode": "Quote"}),
    ("POST", "/api/websocket/unsubscribe-all", {}),
    ("GET", "/api/websocket/market-data", None),
    ("GET", "/api/websocket/apikey", None),
    ("GET", "/api/websocket/config", None),
    ("GET", "/api/websocket/health", None),
    ("GET", "/api/websocket/trade-safe", None),
    ("GET", "/api/websocket/metrics", None),
]
REFUSED = {
    "status": "error",
    "error": "session_expired",
    "message": "Your session has expired. Please log in again.",
}


@pytest.fixture(scope="module", autouse=True)
def _stop_the_health_monitor():
    """Importing the market data service starts its health monitor thread; no test here needs it."""
    yield
    from services.market_data_service import get_market_data_service

    get_market_data_service().health_monitor.stop()


class _Health:
    status, connected, authenticated, data_flow_healthy = "healthy", True, True, True
    last_data_age_seconds, cache_size, total_subscribers, critical_subscribers = 1.0, 0, 0, 0
    total_updates_processed = validation_errors = stale_data_events = reconnect_count = 0
    uptime_seconds, message = 1.0, "ok"


class _Service:
    def get_cache_metrics(self):
        return {"hits": 0}


@pytest.fixture
def app(monkeypatch):
    """The real blueprint on a bare app, with the services behind each view replaced by fakes
    that say they were reached. The daily expiry is 00:00 IST, so a login yesterday is past it."""
    monkeypatch.delenv("DISABLE_SESSION_EXPIRY", raising=False)
    monkeypatch.setenv("SESSION_EXPIRY_TIME", "00:00")
    for name in ("get_websocket_status", "get_websocket_subscriptions", "get_market_data"):
        monkeypatch.setattr(market, name, lambda *a, _n=name: (True, {"reached": _n}, 200))
    for name in ("subscribe_to_symbols", "unsubscribe_from_symbols", "unsubscribe_all"):
        monkeypatch.setattr(market, name, lambda *a, _n=name: (True, {"reached": _n}, 200))
    monkeypatch.setattr(market, "get_health_status", lambda: _Health())
    monkeypatch.setattr(market, "is_trade_management_safe", lambda: (True, "ok"))
    monkeypatch.setattr(market, "is_data_fresh", lambda max_age_seconds=30: True)
    monkeypatch.setattr(market, "get_market_data_service", lambda: _Service())
    monkeypatch.setattr(
        auth_db, "get_api_key_for_tradingview", lambda user: FAKE_API_KEY if user == OWNER else None
    )
    auth_db.SessionCutoff.__table__.create(auth_db.engine, checkfirst=True)
    auth_db.SessionCutoff.query.delete()
    auth_db.db_session.commit()
    auth_db.db_session.remove()
    auth_db.session_cutoff_cache.invalidate()

    app = Flask("sudarshan-g5-websocket-tests")
    app.secret_key = "test-only-secret-key-" + "0" * 32  # pragma: allowlist secret
    app.config["TESTING"] = True
    app.register_blueprint(market.websocket_bp)

    @app.teardown_appcontext
    def remove_sessions(exception=None):
        auth_db.db_session.remove()
        user_db.db_session.remove()

    yield app
    auth_db.SessionCutoff.query.delete()
    auth_db.db_session.commit()
    auth_db.db_session.remove()
    auth_db.session_cutoff_cache.invalidate()


def browser(app, who: str):
    """A client whose cookie is the session ``who`` names."""
    client = app.test_client()
    now = datetime.now(IST)
    with client.session_transaction() as session:
        if who == "none":
            return client
        session["user"] = OWNER
        if who == "password step only":
            return client
        session["logged_in"] = True
        session["login_time"] = (now - timedelta(days=1) if who == "past the daily expiry"
                                 else now - timedelta(minutes=30)).isoformat()  # fmt: skip
    if who == "ended on every device":
        assert auth_db.set_sessions_valid_after(OWNER, now - timedelta(minutes=5))
    return client


def ask(client, method: str, path: str, body):
    return client.open(path, method=method, json=body) if method == "POST" else client.get(path)


@pytest.mark.parametrize(
    "who", ["none", "password step only", "past the daily expiry", "ended on every device"]
)
@pytest.mark.parametrize(("method", "path", "body"), ROUTES, ids=[r[1] for r in ROUTES])
def test_every_route_refuses_a_session_that_is_not_valid_with_a_401(app, who, method, path, body):
    """Before G5 nine of these answered 200 (an error in the body, or for a session naming a user
    the view's own answer), and the API key and the stream address went to every session naming a
    user: the password step only, past the daily expiry, ended by a logout of every device."""
    answer = ask(browser(app, who), method, path, body)
    assert (answer.status_code, answer.get_json()) == (401, REFUSED)


def test_the_api_key_goes_to_the_owner_s_valid_session_and_to_nothing_else(app):
    """Before G5 a login stopped after the password step read the order-capable key."""
    for who in ("password step only", "past the daily expiry", "ended on every device", "none"):
        answer = browser(app, who).get("/api/websocket/apikey")
        assert FAKE_API_KEY not in answer.get_data(as_text=True), who
        auth_db.SessionCutoff.query.delete()
        auth_db.db_session.commit()
        auth_db.session_cutoff_cache.invalidate()
    owner = browser(app, "owner").get("/api/websocket/apikey")
    assert (owner.status_code, owner.get_json()) == (200, {"status": "success", "api_key": FAKE_API_KEY})


@pytest.mark.parametrize(("method", "path", "body"), ROUTES, ids=[r[1] for r in ROUTES])
def test_the_owner_s_valid_session_is_answered_as_before(app, method, path, body):
    """The owner's screens fetch the key and the stream address; every route answers the owner's
    session with its view, as before (passes on the code before G5 too, by design)."""
    answer = ask(browser(app, "owner"), method, path, body)
    assert answer.status_code == 200, answer.get_data(as_text=True)
    data = answer.get_json()
    if path.endswith("/config"):
        assert data["status"] == "success" and data["websocket_url"]
    elif path.endswith(("/health", "/trade-safe", "/metrics", "/apikey")):
        assert data.get("status") != "error"
    else:
        assert data == {"reached": data["reached"]}


def test_the_market_namespace_s_disconnect_handler_runs_to_its_end(app, monkeypatch):
    """Flask-SocketIO 5.6 hands a disconnect handler the reason. /market's took no argument, so
    every disconnect raised there: app.py's Socket.IO error handler logged it as an error and the
    handler's cleanup never ran. Before G5 the subscriber entry below was left behind."""
    from extensions import socketio
    from utils.socketio_error_handler import init_socketio_error_handling

    user_db.Base.metadata.create_all(user_db.engine)
    user_db.User.query.delete()
    user_db.db_session.commit()
    user_db.add_user(OWNER, "owner@example.invalid", "Fake-Pass-123!", is_admin=True)
    user_db.db_session.remove()
    monkeypatch.setattr(socketio, "default_exception_handler", None)  # put back after the test
    socketio.init_app(app)
    init_socketio_error_handling(socketio)  # as app.py binds it
    sio = socketio.test_client(app, flask_test_client=browser(app, "owner"), namespace="/market")
    assert sio.is_connected("/market")
    sid = next(iter(socketio.server.manager.rooms["/market"][f"user_{OWNER}"]))
    monkeypatch.setitem(market.socketio_subscribers, sid, "a subscriber of this socket")
    sio.disconnect(namespace="/market")
    assert sid not in market.socketio_subscribers


def test_a_refusal_leaves_a_login_waiting_for_its_broker_step_signed_in(app):
    """The market data manager outlives the page that started it, so it can ask for the stream
    address while a new login waits for its broker step; the refusal must not end that login.
    (Before G5 that session was answered, with a 200.)"""
    client = browser(app, "password step only")
    assert client.get("/api/websocket/config").status_code == 401
    with client.session_transaction() as session:
        assert session.get("user") == OWNER
