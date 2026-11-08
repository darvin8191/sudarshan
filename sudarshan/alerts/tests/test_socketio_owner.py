"""SUDARSHAN SIO1: OpenAlgo's Socket.IO admits the owner's valid session only.

Upstream sends every order event (order_event: symbol, action, order id, exchange, price type,
product) to every connected client, accepts any origin, and has no connect handler on the default
namespace, so anyone who reached /socket.io/ through nginx could watch the owner's orders as they
happened. Now every connect, a reconnect included, is refused unless its session is a valid session
of OpenAlgo's owner (the check the alerts room's join makes); /market makes the same check; and when
the owner's session ends every socket is closed, so each reconnect is checked again.

Each test fails on the code before SIO1, for the reason its docstring gives. They run through
Flask-SocketIO's test client (threading, as the dev server runs it) with app.py's default Socket.IO
error handler bound, and last through a real gunicorn worker, eventlet and gthread: a real client
over HTTP polling (the app's transport) and a raw one that ignores its refusal and keeps listening.
"""

from __future__ import annotations

import ast
import json
import os
import signal
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
import pytz
from bridge_app import OWNER, VIEWER, csrf_headers, make_app, reset_users

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


def sign_in(http, user: str, when: datetime, *, half: bool = False) -> None:
    """The session OpenAlgo's login leaves: the user after the password step; logged in, with its
    time, after the broker step (half: stopped before it)."""
    with http.session_transaction() as session:
        session.clear()
        session["user"] = user
        if not half:
            session["logged_in"] = True
            session["login_time"] = when.isoformat()


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
    import blueprints.websocket_example  # noqa: F401  (registers /market, as app.py's import does)

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
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                        and node.func.attr in ("on", "on_event")
                        and isinstance(node.func.value, ast.Name)
                        and node.func.value.id == "socketio" and node.args
                        and isinstance(node.args[0], ast.Constant)
                        and node.args[0].value == "connect"):  # fmt: skip
                    continue
                namespace = next((k.value for k in node.keywords if k.arg == "namespace"), None)
                if namespace is None or getattr(namespace, "value", None) == "/":
                    found.append(path.relative_to(REPO).as_posix())
    assert found == ["extensions.py"]


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
