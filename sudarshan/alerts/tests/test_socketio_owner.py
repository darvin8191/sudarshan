"""SUDARSHAN SIO1: OpenAlgo's Socket.IO admits the owner's valid session only.

Upstream sends every order event (order_event: symbol, action, order id, exchange, price type,
product) to every connected client, accepts any origin, and has no connect handler on the default
namespace, so anyone who reached /socket.io/ through nginx could watch the owner's orders as they
happened. Now every connect, a reconnect included, is refused unless its session is a valid session
of OpenAlgo's owner (the check the alerts room's join makes); /market makes the same check; and when
the owner's session ends every socket is closed, so each reconnect is checked again.

python-socketio puts a connecting socket in its namespace's room of everyone before the connect
handler runs, and dispatches that socket's events while the check is still running. So a
broadcast goes only to the room the check joins on success (extensions.ADMITTED_ROOM), and an
event from a socket not yet admitted is dropped (review HIGH-1).

Each test fails on the code before the change it pins, for the reason its docstring gives (the
first round: 376dc58; the review's round: 8c178eb; the re-review's round: 3c75d18). The guard tests
at the end pin today's registrations and pass on both by design, as do the tests that say so. They
run through Flask-SocketIO's test client (threading, as the dev server runs it) with app.py's
default Socket.IO error handler bound, and last through a real gunicorn worker, eventlet and
gthread: a real client over HTTP polling (the app's transport) and a raw one that ignores its
refusal and keeps listening.
"""

from __future__ import annotations

import ast
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import flask
import httpx
import pytest
import pytz
from bridge_app import OWNER, VIEWER, csrf_headers, make_app, reset_users
from socketio import packet

# Imported here, before any test binds Socket.IO to an app: a handler registered after that is
# kept by the server of the moment only (Flask-SocketIO), and lost at the next init_app.
import blueprints.websocket_example as market_module  # the /market namespace
import database.auth_db as auth_db
import database.user_db as user_db
from events.order_events import OrderPlacedEvent
from extensions import socketio
from subscribers import socketio_subscriber
from utils import session as session_utils
from utils.event_bus import EventBus
from utils.socketio_error_handler import init_socketio_error_handling

TESTS_DIR = Path(__file__).resolve().parent
REPO = TESTS_DIR.parents[2]
PEPPER = os.environ["API_KEY_PEPPER"]  # the conftest's fake
IST = pytz.timezone("Asia/Kolkata")
ORDER = {"symbol": "SBIN", "action": "BUY", "orderid": "SIO1-1", "exchange": "NSE",
         "price_type": "MARKET", "product_type": "MIS", "mode": "live"}  # fmt: skip


def at(hour: int, minute: int, day: int = 9) -> datetime:
    return IST.localize(datetime(2026, 11, day, hour, minute))


def sign_in(http, user: str, when: datetime, *, half: bool = False, session_id=None) -> None:
    """The session OpenAlgo's login leaves: the user after the password step; logged in, with its
    time, after the broker step (half: stopped before it); with the id of its active session row
    and the broker when `session_id` is given, as handle_auth_success writes them."""
    with http.session_transaction() as session:
        session.clear()
        session["user"] = user
        if not half:
            session["logged_in"] = True
            session["login_time"] = when.isoformat()
        if session_id is not None:
            session["session_id"] = session_id
            session["broker"] = "upstox"


class Clock:
    """utils.session's clock, held at an instant the test moves."""

    def __init__(self, monkeypatch, when: datetime):
        self.at = when
        clock = self

        class Held(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock.at.astimezone(tz) if tz is not None else clock.at.replace(tzinfo=None)

        monkeypatch.setattr(session_utils, "datetime", Held)


def publish_order() -> None:
    """An order placed, through an event bus with the Socket.IO subscriber, as the order service
    publishes it; returns once the subscriber has run."""
    bus = EventBus(workers=1)
    bus.subscribe("order.placed", socketio_subscriber.on_order_placed, "socketio:order_placed")
    bus.publish(OrderPlacedEvent(mode="live", strategy="sio1", symbol="SBIN", exchange="NSE",
                                 action="BUY", quantity=1, pricetype="MARKET", product="MIS",
                                 orderid="SIO1-1"))  # fmt: skip
    bus._executor.shutdown(wait=True)


def heard(client, event: str = "order_event") -> list[dict]:
    """What reached this socket (connected or not), emptied as it is read."""
    got = [m["args"][0] for m in client.queue if m["name"] == event]
    client.queue.clear()
    return got


class HeldCheck:
    """The connect check held inside its handler until the test lets it go, as a preemption or a
    GIL hand-off holds it under gthread or the dev server (a log line, a database read)."""

    def __init__(self, monkeypatch):
        self.inside, self.go, self.box = threading.Event(), threading.Event(), {}
        real = session_utils.is_session_valid

        def held():
            self.box["sid"] = flask.request.sid
            self.inside.set()
            assert self.go.wait(10)
            return real()

        monkeypatch.setattr(session_utils, "is_session_valid", held)

    def start(self, opener) -> None:
        self.thread = threading.Thread(target=lambda: self.box.update(client=opener()))
        self.thread.start()
        assert self.inside.wait(10), "the connect check never ran"

    def eio_sid(self) -> str:
        return socketio.server.manager.eio_sid_from_sid(self.box["sid"], "/")

    def finish(self):
        self.go.set()
        self.thread.join(10)
        return self.box["client"]


@pytest.fixture
def world(monkeypatch):
    """The bridge app with Socket.IO bound as app.py binds it, app.py's default error handler
    included (it answers True to an error); a browser per session and the sockets it opens. The
    daily expiry is at 00:00 IST here, so a login the day before is past it at any hour."""
    reset_users()
    monkeypatch.delenv("DISABLE_SESSION_EXPIRY", raising=False)
    monkeypatch.setenv("SESSION_EXPIRY_TIME", "00:00")
    monkeypatch.setattr(socketio, "default_exception_handler", None)  # put back after the test
    app = make_app()
    socketio.init_app(app)
    init_socketio_error_handling(socketio)

    def browser(user: str | None = OWNER, *, expired: bool = False, half: bool = False):
        http = app.test_client()
        if user is not None:
            now = datetime.now(IST)
            sign_in(http, user, now - timedelta(days=1) if expired else now, half=half)
        return http

    def connect(http, namespace: str | None = None):
        return socketio.test_client(app, flask_test_client=http, namespace=namespace)

    return app, browser, connect


def test_a_client_without_a_session_is_refused_and_hears_no_order(world):
    """Before SIO1 the stranger's socket was admitted and the order reached it."""
    app, browser, connect = world
    stranger = connect(browser(None))
    owner = connect(browser(OWNER))  # proves the order went out
    assert not stranger.is_connected()
    publish_order()
    assert heard(owner) == [ORDER]
    assert heard(stranger) == []


def test_the_owner_s_session_connects_and_hears_the_order_and_no_other_session_does(world):
    """Before SIO1 another account's session, a login stopped before the broker step and an owner
    cookie past the daily expiry were all admitted and all heard the order."""
    app, browser, connect = world
    owner = connect(browser(OWNER))
    others = {
        "another account": connect(browser(VIEWER)),
        "the password step only": connect(browser(OWNER, half=True)),
        "past the daily expiry": connect(browser(OWNER, expired=True)),
    }
    assert owner.is_connected()
    connected = {name: sio.is_connected() for name, sio in others.items()}
    assert connected == dict.fromkeys(others, False)
    publish_order()
    assert heard(owner) == [ORDER]
    assert {name: heard(sio) for name, sio in others.items()} == dict.fromkeys(others, [])


def test_a_socket_admitted_before_the_daily_expiry_is_refused_when_it_reconnects_after(
    world, monkeypatch
):
    """Before SIO1 the reconnect after 03:00 was admitted. A reconnect while the session is valid
    (a phone waking, a network change) is admitted, then and after the next login."""
    monkeypatch.setenv("SESSION_EXPIRY_TIME", "03:00")
    clock = Clock(monkeypatch, at(2, 50))
    app, browser, connect = world
    http = app.test_client()
    sign_in(http, OWNER, at(21, 0, day=8))
    sio = connect(http)
    assert sio.is_connected()
    sio.disconnect()  # the phone sleeps and its transport drops; it wakes before 03:00
    sio.connect()
    assert sio.is_connected()
    publish_order()
    assert heard(sio) == [ORDER]
    clock.at = at(3, 10)
    sio.disconnect()
    sio.connect()  # the same cookie, after the expiry
    assert not sio.is_connected()
    publish_order()
    assert heard(sio) == []
    sign_in(http, OWNER, clock.at)  # the morning's login
    sio.connect()
    assert sio.is_connected()
    publish_order()
    assert heard(sio) == [ORDER]


@pytest.mark.parametrize("ending", ["logout", "the daily expiry"])
def test_when_the_owner_s_session_ends_every_socket_closes_and_its_reconnect_is_refused(
    world, monkeypatch, ending
):
    """Before SIO1 a socket that had not joined the alerts room outlived the session (only the
    room was closed), and the browser's reconnect after the end was admitted."""
    import blueprints.auth as auth_module
    import database.cache_invalidation as cache_invalidation

    monkeypatch.setenv("SESSION_EXPIRY_TIME", "03:00")
    clock = Clock(monkeypatch, at(2, 50))
    for module in (auth_module, auth_db):
        monkeypatch.setattr(module, "upsert_auth", lambda *a, **k: 1)
    monkeypatch.setattr(auth_db, "clear_user_sessions", lambda *a, **k: None)
    monkeypatch.setattr(session_utils, "_has_fresher_session", lambda *a, **k: False)
    monkeypatch.setattr(cache_invalidation, "publish_all_cache_invalidation", lambda *a: None)
    app, browser, connect = world
    app.register_blueprint(auth_module.auth_bp)
    http = app.test_client()
    sign_in(http, OWNER, at(21, 0, day=8))
    joined, unjoined = connect(http), connect(http)
    assert joined.emit("sudarshan_alerts_join", callback=True) == {"ok": True}
    assert unjoined.is_connected()
    if ending == "logout":
        assert http.post("/auth/logout", headers=csrf_headers(http)).status_code == 200
    else:  # the first request after 03:00 finds the session expired and ends it
        clock.at = at(3, 10)
        answer = http.get("/sudarshan/alerts/api/state", headers={"Accept": "application/json"})
        assert answer.status_code == 401
    assert not joined.is_connected() and not unjoined.is_connected()
    for sio in (joined, unjoined):
        heard(sio)  # empties what came before the end (force_logout, active_sessions_update)
    publish_order()
    assert heard(joined) == [] and heard(unjoined) == []
    again = connect(http)  # the reconnect, with what the end left in the browser
    assert not again.is_connected()
    sign_in(http, OWNER, clock.at)  # a new login
    again = connect(http)
    assert again.is_connected()
    publish_order()
    assert heard(again) == [ORDER]


def test_a_check_that_fails_refuses_the_socket_instead_of_admitting_it(world, monkeypatch):
    """Flask-SocketIO hands an exception raised in a connect handler to app.py's default error
    handler, whose answer (True) becomes the handler's: a check that raised would admit the
    socket. Before SIO1 there was no check and the socket was admitted."""
    app, browser, connect = world
    http = browser(OWNER)
    readable = user_db.find_user_by_username

    def unreadable():
        raise RuntimeError("the users table could not be read")

    monkeypatch.setattr(user_db, "find_user_by_username", unreadable)
    sio = connect(http)
    assert not sio.is_connected()
    publish_order()
    assert heard(sio) == []
    monkeypatch.setattr(user_db, "find_user_by_username", readable)
    sio.connect()  # readable again: the app's next connect is admitted
    assert sio.is_connected()


def test_the_market_namespace_admits_the_owner_s_valid_session_only(world):
    """/market refused only a cookie without a user. Before SIO1 a login stopped before the broker
    step, a cookie past the daily expiry and another account's session were admitted."""
    app, browser, connect = world
    owner = connect(browser(OWNER), namespace="/market")
    refused = {
        "no session": connect(browser(None), namespace="/market"),
        "the password step only": connect(browser(OWNER, half=True), namespace="/market"),
        "past the daily expiry": connect(browser(OWNER, expired=True), namespace="/market"),
        "another account": connect(browser(VIEWER), namespace="/market"),
    }
    assert owner.is_connected("/market")
    assert [m["name"] for m in owner.get_received("/market")] == ["connected"]
    assert {name: sio.is_connected("/market") for name, sio in refused.items()} == dict.fromkeys(
        refused, False
    )


def test_a_broadcast_made_while_a_connect_is_checked_never_reaches_that_socket(world, monkeypatch):
    """Review HIGH-1. Before this round the connecting socket sat in the room of everyone while its
    check ran, so an order published meanwhile reached it and it was refused afterwards (the review
    measured 953 of 1000 orders on gthread with yesterday's owner cookie). Broadcasts now go to the
    admitted room, which a socket joins only once its check has passed."""
    app, browser, connect = world
    owner = connect(browser(OWNER))
    hold = HeldCheck(monkeypatch)
    hold.start(lambda: connect(browser(OWNER, expired=True)))
    publish_order()  # the owner's order goes out while the stranger's check is held
    stranger = hold.finish()
    assert not stranger.is_connected()
    assert heard(stranger) == []
    assert heard(owner) == [ORDER]


def test_an_event_sent_while_its_connect_is_checked_is_not_handled(world, monkeypatch):
    """Review HIGH-1, the same window for events: python-socketio dispatched an event from a socket
    whose check was still running, so a room join made there (strategy_subscribe takes yesterday's
    owner cookie) would put a socket the check then refused in a strategy's room. An event from a
    socket the check has not admitted is now dropped; an admitted one's is handled as before."""
    app, browser, connect = world
    handled = []

    @socketio.on("sio1_probe")  # on this test's server only: the next init_app forgets it
    def probe(*args):
        handled.append(flask.request.sid)
        return {"handled": True}

    hold = HeldCheck(monkeypatch)
    try:
        hold.start(lambda: connect(browser(None)))
        socketio.server._handle_eio_message(hold.eio_sid(), '2["sio1_probe"]')
        stranger = hold.finish()
    finally:
        hold.go.set()
    assert not stranger.is_connected()
    assert handled == []
    owner = connect(browser(OWNER))
    assert owner.emit("sio1_probe", callback=True) == {"handled": True}


def test_a_check_that_fails_tells_the_app_to_try_again(world, monkeypatch):
    """Review MEDIUM-2. A check that failed was refused with the words a session that is not the
    owner's gets, so the app could only treat it as final. Its refusal now carries retry: true; a
    session that is not the owner's is refused without it."""
    app, browser, connect = world
    sio = connect(browser(OWNER))
    sio.disconnect()
    stranger = connect(browser(None))  # refused once here, before the recorder below
    refusals = []
    send = socketio.server._send_packet  # the newest test client's; it delivers to any of them

    def record(eio_sid, pkt):
        if pkt.packet_type == packet.CONNECT_ERROR:
            refusals.append(pkt.data)
        return send(eio_sid, pkt)

    monkeypatch.setattr(socketio.server, "_send_packet", record)
    readable = user_db.find_user_by_username

    def unreadable():
        raise RuntimeError("the users table could not be read")

    monkeypatch.setattr(user_db, "find_user_by_username", unreadable)
    sio.connect()
    monkeypatch.setattr(user_db, "find_user_by_username", readable)
    stranger.connect()
    assert not sio.is_connected() and not stranger.is_connected()
    assert [refusal.get("data") for refusal in refusals] == [{"retry": True}, None]
    assert all(isinstance(refusal.get("message"), str) for refusal in refusals)


def test_the_rollover_closes_the_sockets_opened_before_it_when_a_fresher_login_exists(
    world, monkeypatch
):
    """Review MEDIUM-1. With a login after 03:00 on record (another device, or each morning's
    auto-login), the stale phone's first request took revoke_user_tokens' early return, which closed
    nothing, so yesterday's socket heard today's orders. That branch now closes every socket whose
    session logged in before the rollover; today's sockets stay."""
    monkeypatch.setenv("SESSION_EXPIRY_TIME", "03:00")
    clock = Clock(monkeypatch, at(2, 50))
    monkeypatch.setattr(session_utils, "_has_fresher_session", lambda *a, **k: True)
    app, browser, connect = world
    phone = app.test_client()
    sign_in(phone, OWNER, at(21, 0, day=8))
    yesterday = connect(phone)
    yesterday_market = connect(phone, namespace="/market")
    assert yesterday.is_connected() and yesterday_market.is_connected("/market")
    clock.at = at(9, 5)
    laptop = app.test_client()
    sign_in(laptop, OWNER, clock.at)
    today = connect(laptop)
    stale = phone.get("/sudarshan/alerts/api/state", headers={"Accept": "application/json"})
    assert stale.status_code == 401
    assert not yesterday.is_connected() and not yesterday_market.is_connected("/market")
    assert today.is_connected()
    publish_order()
    assert heard(yesterday) == [] and heard(today) == [ORDER]


def test_a_market_socket_closes_when_the_owner_s_session_ends(world, monkeypatch):
    """Review LOW-1. drop_owner_room closed the default namespace's sockets only, so a /market
    socket admitted before a logout kept reading quotes and changing the feed's subscriptions."""
    import blueprints.auth as auth_module

    monkeypatch.setattr(auth_module, "upsert_auth", lambda *a, **k: 1)
    monkeypatch.setattr(auth_db, "clear_user_sessions", lambda *a, **k: None)
    app, browser, connect = world
    app.register_blueprint(auth_module.auth_bp)
    http = browser(OWNER)
    market = connect(http, namespace="/market")
    assert market.is_connected("/market")
    assert http.post("/auth/logout", headers=csrf_headers(http)).status_code == 200
    assert not market.is_connected("/market")


def _devices(app, *ips: str) -> list:
    """Browsers signed in now as the owner, each with its own active session row from its IP. Each
    request reads the rows afresh, as app.py's teardown (every scoped session removed) makes it."""
    app.teardown_appcontext(lambda exception=None: auth_db.db_session.remove())
    auth_db.init_db()
    auth_db.clear_user_sessions(OWNER)
    browsers = []
    for number, ip in enumerate(ips):
        http, session_id = app.test_client(), f"{number}" * 64
        sign_in(http, OWNER, datetime.now(IST), session_id=session_id)
        assert auth_db.register_session(OWNER, session_id, f"device {number}", ip, "upstox")
        browsers.append(http)
    return browsers


def test_after_a_logout_elsewhere_the_login_page_and_the_session_read_sign_the_device_out(
    world, monkeypatch
):
    """Re-review, the loop: after a logout on the laptop the phone's cookie still read as signed in
    to the login page's check and to /auth/active-sessions (200), though the logout had removed
    every session row. The login page sent the phone to the dashboard, whose guard sent it back,
    about 300 reads in 2 s. Both now take a cookie whose row the server removed as signed out, and
    clear it."""
    import blueprints.auth as auth_module

    monkeypatch.setattr(auth_module, "upsert_auth", lambda *a, **k: 1)
    app, browser, connect = world
    app.register_blueprint(auth_module.auth_bp)
    laptop, phone, tablet = _devices(app, "192.0.2.1", "192.0.2.2", "192.0.2.3")
    json_read = {"Accept": "application/json"}
    assert phone.get("/auth/session-status?for=login").get_json()["logged_in"] is True
    assert tablet.get("/auth/active-sessions", headers=json_read).status_code == 200
    assert laptop.post("/auth/logout", headers=csrf_headers(laptop)).status_code == 200

    assert phone.get("/auth/session-status?for=login").get_json()["authenticated"] is False
    assert phone.get("/auth/session-status").get_json()["authenticated"] is False  # cleared
    assert tablet.get("/auth/active-sessions", headers=json_read).status_code == 401
    assert tablet.get("/auth/session-status").get_json()["authenticated"] is False  # cleared


def test_a_page_load_s_own_check_still_ignores_a_row_a_same_ip_login_replaced(world, monkeypatch):
    """register_session replaces the row of an earlier login from the same IP (a laptop and a phone
    on one Wi-Fi), so only the login page asks about the row: the laptop's page loads and its live
    connection carry on. Those checks pass before and after: they pin that the row is asked about
    nowhere else. The login page's own check does ask, and signs such a laptop out (on 3c75d18 it
    did not ask): the accepted edge until the server keeps a "sessions valid after" time, since the
    row is its only record of a logout."""
    import blueprints.auth as auth_module

    app, browser, connect = world
    app.register_blueprint(auth_module.auth_bp)
    laptop, phone = _devices(app, "192.0.2.7", "192.0.2.7")
    assert [row["device_info"] for row in auth_db.get_active_sessions(OWNER)] == ["device 1"]
    status = laptop.get("/auth/session-status").get_json()
    assert status["authenticated"] is True and status["logged_in"] is True
    assert connect(laptop).is_connected()
    assert laptop.get("/auth/session-status?for=login").get_json()["authenticated"] is False


def test_an_admitted_market_socket_s_request_is_answered(world):
    """Re-review L5: the event gate reads the admitted room of the event's own namespace. Reading
    the default namespace's instead would drop every /market request; nothing pinned that."""
    app, browser, connect = world
    market = connect(browser(OWNER), namespace="/market")
    market.get_received("/market")  # its greeting
    market.emit("get_ltp", {"symbol": "SBIN", "exchange": "NSE"}, namespace="/market")
    assert [m["name"] for m in market.get_received("/market")] == ["ltp_data"]


# -- guards: what replaces the owner check would go unnoticed otherwise ---------------------


def _connect_registrations(tree: ast.AST) -> list[str]:
    """Where a source registers a connect handler on the default namespace, or a class-based
    namespace (which could carry its own): socketio.on/on_event("connect"[, namespace="/"]), a
    function named connect under @socketio.event, socketio.on_namespace(...)."""
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "connect":
            for decorator in node.decorator_list:
                if (isinstance(decorator, ast.Attribute) and decorator.attr == "event"
                        and isinstance(decorator.value, ast.Name)
                        and decorator.value.id == "socketio"):  # fmt: skip
                    found.append("socketio.event connect")
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "socketio"):  # fmt: skip
            continue
        if node.func.attr == "on_namespace":
            found.append("socketio.on_namespace")
        elif (node.func.attr in ("on", "on_event") and node.args
              and isinstance(node.args[0], ast.Constant) and node.args[0].value == "connect"):  # fmt: skip
            namespace = next((k.value for k in node.keywords if k.arg == "namespace"), None)
            if namespace is None or getattr(namespace, "value", None) == "/":
                found.append(f"socketio.{node.func.attr} connect")
    return found


def test_the_guard_sees_every_way_to_register_a_connect_handler():
    """Review LOW-4: the scan missed @socketio.event def connect and on_namespace."""
    sources = {
        "@socketio.on('connect')\ndef a(auth=None): pass": ["socketio.on connect"],
        "socketio.on_event('connect', h, namespace='/')": ["socketio.on_event connect"],
        "@socketio.event\ndef connect(auth=None): pass": ["socketio.event connect"],
        "socketio.on_namespace(Gate('/'))": ["socketio.on_namespace"],
        "@socketio.on('connect', namespace='/market')\ndef b(auth): pass": [],
    }  # fmt: skip
    assert {code: _connect_registrations(ast.parse(code)) for code in sources} == sources


def test_nothing_else_registers_a_connect_handler_on_the_default_namespace():
    """Flask-SocketIO keeps one connect handler per namespace and the last one registered wins,
    so a second one anywhere in the app would silently replace the owner check. Before SIO1 there
    was none at all."""
    skip = {".git", ".venv", "venv", "node_modules", "frontend", "test", "tests", "__pycache__",
            "db", "log", "logs", "strategies"}  # fmt: skip
    found = []
    for folder, subfolders, files in os.walk(REPO):
        subfolders[:] = [name for name in subfolders if name not in skip]
        for name in files:
            path = Path(folder, name)
            if path.suffix != ".py":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "socketio" not in text or "connect" not in text:
                continue
            try:
                tree = ast.parse(text)
            except (SyntaxError, ValueError):
                continue
            found += [f"{path.relative_to(REPO).as_posix()}: {kind}"
                      for kind in _connect_registrations(tree)]  # fmt: skip
    assert found == ["extensions.py: socketio.on connect"]


def test_the_connect_handlers_the_server_runs_are_the_owner_checks(world):
    """Review LOW-4: what the server will actually call, after every module that registers a
    Socket.IO handler has been imported (as app.py imports them)."""
    import blueprints.strategy_module  # noqa: F401
    import blueprints.sudarshan_alerts  # noqa: F401
    import extensions

    handlers = socketio.server.handlers
    assert handlers["/"]["connect"].__wrapped__ is extensions._connect_owner_only
    assert handlers["/market"]["connect"].__wrapped__ is market_module.handle_connect


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
    """bridge_gunicorn_app on a free port; the daily expiry at 00:00 IST in the server's env."""
    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    command = [
        sys.executable, "-m", "gunicorn", "--worker-class", worker_class, "-w", "1",
        *(["--threads", "8"] if worker_class == "gthread" else []),
        "--bind", f"127.0.0.1:{port}", "--timeout", "120", "--chdir", str(REPO),
        "bridge_gunicorn_app:app",
    ]  # fmt: skip
    env = {
        **{k: v for k, v in os.environ.items()
           if not k.lower().endswith("_proxy") and k != "DISABLE_SESSION_EXPIRY"},
        "API_KEY_PEPPER": PEPPER,
        "APP_KEY": "test-only-app-key-" + "0" * 32,
        "DATABASE_URL": f"sqlite:///{tmp_path}/openalgo-gunicorn-test.db",
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


class RawPolling:
    """An Engine.IO polling session driven by hand, as a stranger's script would: it asks for the
    default namespace and keeps listening whatever the answer."""

    def __init__(self, url: str):
        import requests

        self.url, self.http = f"{url}/socket.io/", requests.Session()
        self.http.trust_env = False
        opened = self.http.get(self.url, params={"EIO": "4", "transport": "polling"}, timeout=10)
        assert opened.text.startswith("0"), opened.text
        self.sid = json.loads(opened.text[1:])["sid"]

    def _params(self) -> dict:
        return {"EIO": "4", "transport": "polling", "sid": self.sid}

    def send(self, packet: str) -> None:
        sent = self.http.post(self.url, params=self._params(), data=packet.encode(),
                              headers={"Content-Type": "text/plain;charset=UTF-8"}, timeout=10)  # fmt: skip
        assert sent.status_code == 200 and sent.text.lower() == "ok", sent.text

    def read(self, seconds: float) -> list[str]:
        """The packets waiting for this session, or what arrives within `seconds`; pings answered."""
        import requests

        try:
            payload = self.http.get(self.url, params=self._params(), timeout=(5, seconds)).text
        except requests.exceptions.ReadTimeout:
            return []
        packets = payload.split("\x1e")
        if "2" in packets:
            self.send("3")
        return [p for p in packets if p != "2"]

    def close(self) -> None:
        try:
            self.send("1")
        except Exception:
            pass
        self.http.close()


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")
@pytest.mark.parametrize("worker_class", ["eventlet", "gthread"])
def test_under_gunicorn_only_the_owner_s_session_connects_and_hears_orders(tmp_path, worker_class):
    """Before SIO1 the raw client's connect was answered with a CONNECT and the order reached it,
    and the expired and half signed-in sessions were admitted on both namespaces."""
    pytest.importorskip("gunicorn", reason="gunicorn is installed by the production installer")
    if worker_class == "eventlet":
        pytest.importorskip("eventlet", reason="eventlet is installed by the production installer")
    import requests
    import socketio as sio_client

    with serving(tmp_path, worker_class) as url:
        stranger = RawPolling(url)
        owner = None
        try:
            stranger.send("40")
            answer = stranger.read(10)
            assert [packet[:2] for packet in answer] == ["44"], answer  # CONNECT_ERROR

            def browser(**params):
                http = requests.Session()
                http.trust_env = False
                login = http.get(f"{url}/login-for-tests", params={"user": OWNER, **params})
                assert login.status_code == 200
                return http

            def admitted(http, namespaces) -> bool:
                client = sio_client.Client(http_session=http, reconnection=False)
                try:
                    client.connect(url, transports=["polling"], namespaces=namespaces,
                                   wait_timeout=20)  # fmt: skip
                except sio_client.exceptions.ConnectionError:
                    return False
                client.disconnect()
                return True

            cases = {
                f"{kind} on {namespace}": admitted(browser(**params), [namespace])
                for kind, params in (("past the daily expiry", {"expired": "1"}),
                                     ("the password step only", {"half": "1"}))
                for namespace in ("/", "/market")
            }  # fmt: skip
            assert cases == dict.fromkeys(cases, False)

            owner = sio_client.Client(http_session=browser(), reconnection=False)
            got: list = []
            owner.on("order_event", got.append)
            owner.connect(url, transports=["polling"], namespaces=["/", "/market"],
                          wait_timeout=20)  # fmt: skip
            assert set(owner.namespaces) == {"/", "/market"}
            assert httpx.get(f"{url}/order-event-for-tests", timeout=10).status_code == 200
            deadline = time.monotonic() + 20
            while not got and time.monotonic() < deadline:
                time.sleep(0.05)
            assert got == [ORDER]
            late = stranger.read(2)  # the owner has it, so anything sent to the stranger is queued
            assert not [packet for packet in late if packet.startswith("42")], late
        finally:
            if owner is not None:
                owner.disconnect()
            stranger.close()
