"""The TRINETRA view in the owner's open apps (B2): OpenAlgo's sealed push route and his room.

The route's checks (only this server's service, sealed for the push purpose by sudarshan-signals,
once, within its size; no CSRF cookie to forge) and the room (only the owner's sessions join;
nothing is broadcast) are tested through Flask-SocketIO's test clients. Then the production
shape: the service's real push_once posts to a real gunicorn worker, eventlet and gthread, and a
Socket.IO client signed in as the owner gets the update while one signed in as someone else gets
nothing. The service's own side is tested in sudarshan/signals/tests/test_signals_push.py.
"""

from __future__ import annotations

import asyncio
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from signals_bridge_app import OWNER, VIEWER, csrf_headers, login_session, make_app, reset_users

import blueprints.sudarshan_signals as bp_module
from extensions import socketio
from sudarshan.autologin.handoff import NonceBook, seal
from sudarshan.signals import contract as c
from sudarshan.vault import SIGNALS_API_INFO, SIGNALS_PUSH_INFO, fernet_for
from utils.thread_safe_cache import LockedTTLCache

TESTS_DIR = Path(__file__).resolve().parent
REPO = TESTS_DIR.parents[1]
PEPPER = os.environ["API_KEY_PEPPER"]  # the conftest's fake
PUSH = c.PUSH_PATH
UPDATE = {"cause": "document", "state": {"now_ms": 1794800712250, "lang": "hi", "readings": []}}


def sealed(items, fernet=None, principal="sudarshan-signals") -> bytes:
    return seal(fernet or fernet_for(PEPPER, SIGNALS_PUSH_INFO), "push", principal=principal,
                items=items)  # fmt: skip


@pytest.fixture
def room(monkeypatch):
    """The app with Socket.IO bound, a Flask client per session, and the route's nonce book."""
    reset_users()
    monkeypatch.setattr(bp_module, "_push_nonces", NonceBook(64))
    monkeypatch.setattr(bp_module, "_warned", LockedTTLCache(maxsize=8, ttl=600))
    app = make_app()
    socketio.init_app(app)

    def client(user: str | None = OWNER, join: bool = True):
        http = app.test_client()
        if user is not None:
            login_session(http, user)
        sio = socketio.test_client(app, flask_test_client=http)
        # SIO1: a session that is not the owner's is refused at connect: no socket, no ack.
        ack = sio.emit(c.JOIN_EVENT, callback=True) if join and sio.is_connected() else None
        return http, sio, ack

    return app, client


def received(sio) -> list[dict]:
    return [m["args"][0] for m in sio.get_received() if m["name"] == c.EVENT_UPDATE]


def test_only_the_owner_s_sessions_join_and_a_push_reaches_only_them(room):
    app, client = room
    http, owner, ack = client()
    assert ack == {"ok": True}
    _, viewer, refused = client(VIEWER)
    assert not viewer.is_connected() and refused is None
    _, nobody, unsigned = client(None)
    assert not nobody.is_connected() and unsigned is None
    _, quiet, _ = client(OWNER, join=False)  # the owner's, but it never joined
    answer = http.post(
        PUSH,
        data=sealed(
            [
                {"event": c.EVENT_UPDATE, "data": UPDATE},
                {"event": "order_update", "data": {"x": 1}},
                {"event": c.EVENT_UPDATE, "data": "not an object"},
            ]
        ),
    )
    assert answer.status_code == 200
    assert answer.get_json() == {"status": "success", "screens": 1}
    assert [(m["name"], m["args"][0]) for m in owner.get_received()] == [(c.EVENT_UPDATE, UPDATE)]
    assert quiet.get_received() == []
    for refused_socket in (viewer, nobody):  # get_received() raises on a socket never connected
        assert refused_socket.queue == []


def test_only_this_server_s_service_may_push(room):
    """Through nginx: 404 before the body is read. Not sealed, sealed for another purpose or by
    another principal, replayed, too large or too many items: refused, and nothing is emitted."""
    app, client = room
    http, owner, _ = client()
    body = sealed([{"event": c.EVENT_UPDATE, "data": UPDATE}])
    for header in bp_module.PROXY_HEADERS:
        assert http.post(PUSH, data=body, headers={header: "203.0.113.9"}).status_code == 404
    other = fernet_for(PEPPER, SIGNALS_API_INFO)
    assert http.post(PUSH, data=sealed([], other)).status_code == 403
    assert http.post(PUSH, data=b"not a seal").status_code == 403
    assert http.post(PUSH, data=sealed([], principal="owner")).status_code == 403
    assert http.post(PUSH, data=body).status_code == 200
    assert http.post(PUSH, data=body).status_code == 403  # the same seal again
    big = sealed([{"event": c.EVENT_UPDATE, "data": {"cause": "document", "state": "x" * 40_000}}])
    assert http.post(PUSH, data=big).status_code == 413
    many = sealed([{"event": c.EVENT_UPDATE, "data": UPDATE}] * (bp_module.MAX_PUSH_ITEMS + 1))
    assert http.post(PUSH, data=many).status_code == 400
    not_a_list = seal(fernet_for(PEPPER, SIGNALS_PUSH_INFO), "push", principal="sudarshan-signals",
                      items={"event": c.EVENT_UPDATE})  # fmt: skip
    assert http.post(PUSH, data=not_a_list).status_code == 400
    assert http.get(PUSH).status_code == 405
    assert len(received(owner)) == 1


def test_a_full_nonce_book_answers_busy(room, monkeypatch):
    app, client = room
    http, _, _ = client()
    monkeypatch.setattr(bp_module, "_push_nonces", NonceBook(1))
    assert http.post(PUSH, data=sealed([])).status_code == 200
    assert http.post(PUSH, data=sealed([])).status_code == 503


def test_the_push_needs_no_csrf_token_and_the_owner_routes_still_do(room):
    app, client = room
    http, _, _ = client()
    assert http.post(PUSH, data=sealed([])).status_code == 200  # no X-CSRFToken: the seal is
    assert http.put(c.API_PREFIX + "/settings", json={"version": 1}).status_code == 400  # CSRF
    assert csrf_headers(http)  # the owner's page has its token as usual


def test_the_answer_counts_the_open_screens_in_the_room(room):
    app, client = room
    http, first, _ = client()
    client()
    client(OWNER, join=False)  # never joined: not counted
    assert http.post(PUSH, data=sealed([])).get_json()["screens"] == 2
    first.disconnect()
    assert http.post(PUSH, data=sealed([])).get_json()["screens"] == 1


def test_a_join_is_refused_when_the_owner_check_fails_or_the_session_has_expired(room, monkeypatch):
    from utils import session as session_utils

    app, client = room
    _, sio, _ = client(join=False)

    def broken():
        raise RuntimeError("the users table cannot be read")

    monkeypatch.setattr(bp_module, "is_owner_session", broken)
    refused = sio.emit(c.JOIN_EVENT, callback=True)
    assert refused == {"ok": False, "message": c.WORDS["forbidden"]}
    monkeypatch.undo()
    monkeypatch.setattr(session_utils, "is_session_valid", lambda: False)
    assert sio.emit(c.JOIN_EVENT, callback=True)["ok"] is False
    assert sio.get_received() == []


# -- under a real gunicorn worker -----------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _stop_group(proc: subprocess.Popen, grace_s: float = 5.0) -> None:
    """The server and everything it started, as systemd stops a unit: SIGTERM to the group, then
    SIGKILL for whatever is left, and the leader reaped (pattern: test_alerts_bridge_push)."""
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


@pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")
@pytest.mark.parametrize("worker_class", ["eventlet", "gthread"])
def test_a_push_reaches_the_owner_s_open_app_under_gunicorn(tmp_path, monkeypatch, worker_class):
    pytest.importorskip("gunicorn", reason="gunicorn is installed by the production installer")
    if worker_class == "eventlet":
        pytest.importorskip("eventlet", reason="eventlet is installed by the production installer")
    import requests
    import socketio as sio_client

    from sudarshan.signals import ops, push

    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    command = [
        sys.executable, "-m", "gunicorn", "--worker-class", worker_class, "-w", "1",
        *(["--threads", "8"] if worker_class == "gthread" else []),
        "--bind", f"127.0.0.1:{port}", "--timeout", "120", "--chdir", str(REPO),
        "signals_gunicorn_app:app",
    ]  # fmt: skip
    env = {
        **{k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")},
        "API_KEY_PEPPER": PEPPER,
        "APP_KEY": "test-only-app-key-" + "0" * 32,
        "DATABASE_URL": f"sqlite:///{tmp_path}/openalgo-gunicorn-test.db",
        "LOG_DIR": str(tmp_path / "log"),
        "REDIRECT_URL": "http://127.0.0.1:5000/upstox/callback",
        # the repository and these tests first; a path the run already has is kept after them
        "PYTHONPATH": os.pathsep.join(
            [str(REPO), str(TESTS_DIR), *filter(None, [os.environ.get("PYTHONPATH")])]
        ),
        "XDG_RUNTIME_DIR": str(tmp_path),  # gunicorn's control socket stays in this test's folder
    }
    owner = viewer = None
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

        def signed_in(user: str):
            http = requests.Session()
            http.trust_env = False
            assert http.get(f"{url}/login-for-tests", params={"user": user}).status_code == 200
            client = sio_client.Client(http_session=http, reconnection=False)
            got: list = []
            client.on(c.EVENT_UPDATE, got.append)
            try:
                client.connect(url, transports=["polling"], wait_timeout=20)
            except sio_client.exceptions.ConnectionError:
                return client, got, None  # SIO1: refused at connect, so no join and no ack
            return client, got, client.call(c.JOIN_EVENT, timeout=20)

        owner, owner_got, joined = signed_in(OWNER)
        viewer, viewer_got, refused = signed_in(VIEWER)
        assert joined == {"ok": True} and refused is None and not viewer.connected

        async def state_now(service):
            return UPDATE["state"]

        monkeypatch.setattr(ops, "state_of", state_now)
        warned: list = []
        service = SimpleNamespace(
            push_key=fernet_for(PEPPER, SIGNALS_PUSH_INFO), store=object(),
            settings=SimpleNamespace(openalgo_url=url), _warn=lambda *a: warned.append(a),
        )  # fmt: skip
        started = time.monotonic()
        assert asyncio.run(push.push_once(service, "document")) == 1  # one open screen
        assert time.monotonic() - started < 5 and warned == []
        deadline = time.monotonic() + 20
        while not owner_got and time.monotonic() < deadline:
            time.sleep(0.05)
        assert owner_got == [UPDATE]
        time.sleep(0.5)
        assert viewer_got == []
    finally:
        for client in (owner, viewer):
            if client is not None:
                client.disconnect()
        _stop_group(server)
