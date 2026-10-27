"""Market alerts in the open apps (M10.7): OpenAlgo's sealed push route and the owner's room.

The route's checks (only this server's service, sealed, once, within its size; no CSRF cookie to
forge) and the room (only the owner's sessions join; nothing is broadcast) are tested through
Flask-SocketIO's test clients. Then the production shape: the service's real Pusher posts to a
real gunicorn worker, eventlet and gthread, and a Socket.IO client signed in as the owner gets the
toast while one signed in as someone else gets nothing.
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

import httpx
import pytest
from bridge_app import OWNER, VIEWER, login_session, make_app, reset_users

import blueprints.sudarshan_alerts as bp_module
from extensions import socketio
from sudarshan.alerts.market.push import ALERT_EVENT, STATE_EVENT, Pusher
from sudarshan.autologin.handoff import NonceBook, seal
from sudarshan.vault import ALERTS_API_INFO, ALERTS_PUSH_INFO, fernet_for
from utils.thread_safe_cache import LockedTTLCache

TESTS_DIR = Path(__file__).resolve().parent
REPO = TESTS_DIR.parents[2]
PEPPER = os.environ["API_KEY_PEPPER"]  # the conftest's fake
PUSH = "/sudarshan/alerts/internal/push"
FIRE = {"event_id": 1, "alert_id": 1, "symbol": "SBIN", "exchange": "NSE",
        "title": "SBIN crossed above ₹100.00", "detail": "now ₹100.05", "note": "",
        "at": "2026-10-21T10:00:02+05:30", "at_ms": 1792557002000, "found_later": False,
        "opening": False, "auction": False, "test": False}  # fmt: skip


def sealed(items, fernet=None) -> bytes:
    return seal(fernet or fernet_for(PEPPER, ALERTS_PUSH_INFO), "push", items=items)


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
        ack = sio.emit("sudarshan_alerts_join", callback=True) if join else None
        return http, sio, ack

    return app, client


def received(sio, event: str = ALERT_EVENT) -> list[dict]:
    return [m["args"][0] for m in sio.get_received() if m["name"] == event]


def test_only_the_owner_s_sessions_join_and_a_push_reaches_only_them(room):
    app, client = room
    http, owner, ack = client()
    assert ack == {"ok": True}
    _, viewer, refused = client(VIEWER)
    assert refused == {"ok": False, "message": "Only the account owner can manage market alerts."}
    _, nobody, unsigned = client(None)
    assert unsigned["ok"] is False
    _, quiet, _ = client(OWNER, join=False)  # the owner's, but it never joined
    state = {"engine": {"code": "ok", "line": "Watching · Real-time", "real_time": True},
             "alerts": [], "removed": [], "server_time": 1}  # fmt: skip
    answer = http.post(PUSH, data=sealed([{"event": ALERT_EVENT, "data": FIRE},
                                          {"event": STATE_EVENT, "data": state},
                                          {"event": "order_update", "data": {"x": 1}}]))  # fmt: skip
    assert answer.status_code == 200
    assert answer.get_json() == {"status": "success", "screens": 1}
    messages = owner.get_received()  # the two it may carry, never another event name
    assert [(m["name"], m["args"][0]) for m in messages] == [(ALERT_EVENT, FIRE),
                                                             (STATE_EVENT, state)]  # fmt: skip
    for other in (viewer, nobody, quiet):
        assert other.get_received() == []


def test_only_this_server_s_service_may_push(room):
    """Through nginx: 404 before the body is read. Not sealed, sealed for another purpose,
    replayed, too large or without a length: refused, and nothing is emitted."""
    app, client = room
    http, owner, _ = client()
    body = sealed([{"event": ALERT_EVENT, "data": FIRE}])
    assert http.post(PUSH, data=body, headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 404
    assert http.post(PUSH, data=sealed([], fernet_for(PEPPER, ALERTS_API_INFO))).status_code == 403
    assert http.post(PUSH, data=b"not a seal").status_code == 403
    assert http.post(PUSH, data=body).status_code == 200
    assert http.post(PUSH, data=body).status_code == 403  # the same seal again
    big = sealed([{"event": ALERT_EVENT, "data": FIRE | {"note": "x" * 130_000}}])
    assert http.post(PUSH, data=big).status_code == 413
    assert (
        http.post(PUSH, data=sealed([{"event": ALERT_EVENT, "data": FIRE}] * 51)).status_code == 400
    )
    assert len(received(owner)) == 1


def test_a_full_nonce_book_answers_busy(room, monkeypatch):
    app, client = room
    http, owner, _ = client()
    monkeypatch.setattr(bp_module, "_push_nonces", NonceBook(1))
    assert http.post(PUSH, data=sealed([])).status_code == 200
    busy = http.post(PUSH, data=sealed([]))
    assert busy.status_code == 503


def test_the_push_needs_no_csrf_token_and_the_owner_routes_still_do(room):
    app, client = room
    http, _, _ = client()
    assert http.post(PUSH, data=sealed([])).status_code == 200  # no X-CSRFToken: the seal is
    assert http.post("/sudarshan/alerts/api/test", json={}).status_code == 400  # CSRF


# -- under a real gunicorn worker -----------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _stop_group(proc: subprocess.Popen, grace_s: float = 5.0) -> None:
    """The server and everything it started, as systemd stops a unit: SIGTERM to the group, then
    SIGKILL for whatever is left, and the leader reaped (pattern: test_autologin_gunicorn)."""
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
def test_a_push_reaches_the_owner_s_open_app_under_gunicorn(tmp_path, worker_class):
    pytest.importorskip("gunicorn", reason="gunicorn is installed by the production installer")
    if worker_class == "eventlet":
        pytest.importorskip("eventlet", reason="eventlet is installed by the production installer")
    import requests
    import socketio as sio_client

    port = _free_port()
    url = f"http://127.0.0.1:{port}"
    command = [
        sys.executable, "-m", "gunicorn", "--worker-class", worker_class, "-w", "1",
        *(["--threads", "8"] if worker_class == "gthread" else []),
        "--bind", f"127.0.0.1:{port}", "--timeout", "120", "--chdir", str(REPO),
        "bridge_gunicorn_app:app",
    ]  # fmt: skip
    env = {
        **{k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")},
        "API_KEY_PEPPER": PEPPER,
        "APP_KEY": "test-only-app-key-" + "0" * 32,
        "DATABASE_URL": f"sqlite:///{tmp_path}/openalgo-gunicorn-test.db",
        "LOG_DIR": str(tmp_path / "log"),
        "REDIRECT_URL": "http://127.0.0.1:5000/upstox/callback",
        "PYTHONPATH": os.pathsep.join([str(REPO), str(TESTS_DIR)]),
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
            client.on(ALERT_EVENT, got.append)
            client.connect(url, transports=["polling"], wait_timeout=20)
            return client, got, client.call("sudarshan_alerts_join", timeout=20)

        owner, owner_got, joined = signed_in(OWNER)
        viewer, viewer_got, refused = signed_in(VIEWER)
        assert joined == {"ok": True} and refused["ok"] is False

        async def push() -> list:
            results: list = []
            async with httpx.AsyncClient(trust_env=False) as client:
                pusher = Pusher(url, client, fernet_for(PEPPER, ALERTS_PUSH_INFO),
                                lambda ids, screens: results.append((ids, screens)))  # fmt: skip
                pusher.add(ALERT_EVENT, FIRE, 1)
                assert await pusher.flush(10) == 0
            return results

        started = time.monotonic()
        assert asyncio.run(push()) == [([1], 1)]  # shown on one open screen
        assert time.monotonic() - started < 5
        deadline = time.monotonic() + 20
        while not owner_got and time.monotonic() < deadline:
            time.sleep(0.05)
        assert owner_got == [FIRE]
        time.sleep(0.5)
        assert viewer_got == []
    finally:
        for client in (owner, viewer):
            if client is not None:
                client.disconnect()
        _stop_group(server)
