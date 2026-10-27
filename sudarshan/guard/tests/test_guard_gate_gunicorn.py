"""The risk gate under a real gunicorn worker, eventlet and gthread, one worker as in production.

A small Flask app carries the real gate (sudarshan/guard/gate.py) on a fake broker whose order
call is a sleep, which under eventlet is a cooperative wait like a slow Upstox call, and a
position book held in the app. What is proven:

- an order being sent, however slow, never stops the worker answering (/ping);
- with the kill switch on, a new order is refused at once, even while an exit is being sent;
- an admission racing the switch being turned on is never lost: every order let go before the
  switch went on is in the in-flight set the activation waits on, and none is let go after.

Pattern: sudarshan/books/tests/test_books_exit_guard_gunicorn.py. The server runs in a process
group of its own, and the teardown stops the whole group.
"""

from __future__ import annotations

import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

pytestmark = pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")

APP = textwrap.dedent(
    """
    import os, threading, time
    from datetime import UTC, datetime
    from flask import Flask, jsonify, request
    from sudarshan.guard import gate, store
    from sudarshan.guard.store import KillSwitchState

    BOOK = {"rows": []}
    CALLS = {"started": 0}
    COUNT = threading.Lock()

    class Book:
        def live_cached_rows(self, auth):
            return list(BOOK["rows"])
        def live_rows(self, auth):
            return list(BOOK["rows"])
        def instrument_token(self, symbol, exchange):
            return "NSE_FO|" + symbol

    gate.GATE = gate.Gate(store=store.RiskStore(os.environ["SUDARSHAN_RISK_DB"]), read=Book())
    gate.GATE.set_state(KillSwitchState())

    class Sent:
        status = 200
        status_code = 200

    @gate.upstox_place
    def slow_place(data, auth):
        with COUNT:
            CALLS["started"] += 1
        time.sleep(float(data.get("hold", 0)))  # under eventlet a cooperative wait
        return Sent(), {"status": "success"}, "OID"

    app = Flask(__name__)

    @app.route("/ping")
    def ping():
        return "ok"

    @app.route("/hold", methods=["POST"])
    def hold():
        BOOK["rows"] = request.get_json()["rows"]
        return jsonify(held=len(BOOK["rows"]))

    @app.route("/place", methods=["POST"])
    def place():
        res, body, ref = slow_place(request.get_json(), "token")
        return jsonify(status=res.status, body=body), res.status

    @app.route("/flip", methods=["POST"])
    def flip():
        state = KillSwitchState(on=True, since=datetime.now(UTC), running=True)
        outcome, _state, tokens = gate.GATE.turn_on(state)
        return jsonify(outcome=outcome, in_flight=len(tokens))

    @app.route("/calls")
    def calls():
        with COUNT:
            return jsonify(started=CALLS["started"], pid=os.getpid())

    @app.route("/switch")
    def switch():
        return jsonify(on=gate.GATE.state().on)

    # The real kill switch routes, for the owner's session; nothing they reach leaves the box.
    from datetime import timedelta, timezone
    from flask import session
    from database import auth_db, user_db
    from limiter import limiter
    import blueprints.sudarshan_risk as risk_routes
    from sudarshan.guard import killswitch

    store.STORE = gate.GATE._the_store()
    auth_db.init_db()
    user_db.init_db()
    if user_db.find_user_by_username() is None:
        user_db.add_user("owner", "owner@example.invalid", "Fake-Pass-123!", is_admin=True)
    user_db.db_session.remove()
    killswitch._tell = lambda owner, text: (True, "told", {})
    killswitch._announce = lambda state: None
    killswitch._engage_agent = lambda: (True, "agent", {"engaged_by_us": False})
    app.secret_key = "test-only-secret-key-" + "2" * 32  # pragma: allowlist secret
    app.config.update(WTF_CSRF_ENABLED=False, RATELIMIT_ENABLED=False)
    limiter.init_app(app)
    app.register_blueprint(risk_routes.sudarshan_risk_bp)

    @app.route("/login", methods=["POST"])
    def login():
        session["user"] = "owner"
        session["logged_in"] = True
        india = timezone(timedelta(hours=5, minutes=30))
        session["login_time"] = datetime.now(india).isoformat()
        return jsonify(ok=True)

    @app.teardown_appcontext
    def remove_sessions(exception=None):
        auth_db.db_session.remove()
        user_db.db_session.remove()
    """
)

ENTRY = {"symbol": "CE", "exchange": "NFO", "action": "BUY", "quantity": "65", "product": "MIS"}
EXIT = {**ENTRY, "action": "SELL"}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _stop_process_group(proc: subprocess.Popen, grace_s: float = 5.0) -> None:
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


class Server:
    def __init__(self, tmp_path: Path, worker: str):
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        (app_dir / "risk_gate_fixture_app.py").write_text(APP)
        self.port = _free_port()
        self.log = tmp_path / "gunicorn.log"
        self.last_cookie: str | None = None
        env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
        env.pop("GUNICORN_CMD_ARGS", None)
        env.update(
            PYTHONPATH=os.pathsep.join([str(app_dir), str(REPO)]),
            LOG_DIR=str(tmp_path / "log"),
            XDG_RUNTIME_DIR=str(tmp_path),
            SUDARSHAN_RISK_GUARD="on",
            SUDARSHAN_RISK_DB=str(tmp_path / "sudarshan_risk.db"),
            DATABASE_URL=f"sqlite:///{tmp_path}/openalgo.db",
        )
        command = [
            sys.executable, "-m", "gunicorn", "--worker-class", worker, "--workers", "1",
            *(["--threads", "64"] if worker == "gthread" else []),  # production's pool
            "--bind", f"127.0.0.1:{self.port}", "--timeout", "60",
            "--graceful-timeout", "2", "--log-level", "info", "risk_gate_fixture_app:app",
        ]  # fmt: skip
        with self.log.open("wb") as log:
            self.process = subprocess.Popen(
                command,
                cwd=app_dir,
                env=env,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                if self.get("/ping", timeout=2)[0] == 200:
                    return
            except OSError:
                time.sleep(0.2)
        self.stop()
        raise AssertionError("gunicorn did not start:\n" + self.log.read_text())

    def get(self, path: str, timeout: float = 10):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            conn.request("GET", path)
            reply = conn.getresponse()
            return reply.status, reply.read()
        finally:
            conn.close()

    def post(self, path: str, body: dict, timeout: float = 60, cookie: str | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        try:
            started = time.monotonic()
            conn.request("POST", path, body=json.dumps(body), headers=headers)
            reply = conn.getresponse()
            data = reply.read()
            self.last_cookie = reply.getheader("Set-Cookie")
            return reply.status, json.loads(data or b"{}"), time.monotonic() - started
        finally:
            conn.close()

    def login(self) -> str:
        """The owner's session cookie, as the browser would hold it."""
        assert self.post("/login", {})[0] == 200
        return self.last_cookie.split(";", 1)[0]

    def stop(self) -> None:
        _stop_process_group(self.process)


@pytest.fixture(params=["eventlet", "gthread"])
def server(request, tmp_path):
    pytest.importorskip("gunicorn", reason="gunicorn is installed by the production installer")
    if request.param == "eventlet":
        pytest.importorskip("eventlet", reason="eventlet is installed by the production installer")
    srv = Server(tmp_path, request.param)
    yield srv
    srv.stop()


def _in_background(fn, *args):
    out: dict = {}

    def run():
        out["result"] = fn(*args)

    thread = threading.Thread(target=run)
    thread.start()
    return thread, out


def test_a_slow_order_never_stops_the_worker_answering(server):
    sending, out = _in_background(server.post, "/place", {**ENTRY, "hold": 3})
    time.sleep(0.5)
    for _ in range(5):
        started = time.monotonic()
        assert server.get("/ping")[0] == 200
        assert time.monotonic() - started < 1.0
    sending.join(15)
    assert out["result"][0] == 200


def test_with_the_switch_on_a_new_order_is_refused_at_once_while_an_exit_is_sent(server):
    server.post(
        "/hold", {"rows": [{"instrument_token": "NSE_FO|CE", "product": "I", "quantity": 65}]}
    )
    assert server.post("/flip", {})[1]["outcome"] == "on"
    exiting, out = _in_background(server.post, "/place", {**EXIT, "hold": 3})
    time.sleep(0.5)
    status, body, took = server.post("/place", ENTRY)
    assert status == 403 and body["body"]["message"].startswith("New orders are refused")
    assert took < 1.0, f"the refusal waited {took:.2f}s"
    status, body, took = server.post("/place", EXIT)  # the second exit of the same 65
    assert status == 403 and "already on its way" in body["body"]["message"]
    assert took < 1.0
    exiting.join(15)
    assert out["result"][0] == 200


def test_an_admission_racing_the_switch_is_never_lost(server):
    """Twenty orders arrive while the switch goes on: every one let go is in the in-flight set
    the activation waits on (each is still being sent when it flips), and none is let go after."""
    senders = [_in_background(server.post, "/place", {**ENTRY, "hold": 4}) for _ in range(20)]
    deadline = time.monotonic() + 3
    while json.loads(server.get("/calls")[1])["started"] < 15 and time.monotonic() < deadline:
        time.sleep(0.02)  # flip while the last few are still arriving
    flip = server.post("/flip", {})[1]
    late = [_in_background(server.post, "/place", {**ENTRY, "hold": 4}) for _ in range(5)]
    for thread, _out in senders + late:
        thread.join(30)
    answers = [out["result"][0] for _thread, out in senders + late]
    let_go = answers.count(200)
    assert answers.count(403) == len(answers) - let_go
    assert [out["result"][0] for _thread, out in late] == [403] * 5
    assert json.loads(server.get("/calls")[1])["started"] == let_go == flip["in_flight"]
    assert let_go >= 15  # not vacuous: most were let go, and every one of them was waited on


def test_turning_the_switch_on_keeps_the_worker_answering_and_refuses_a_new_order(server):
    """The real activation route waits up to ten seconds for an order already on its way. All
    that time the worker answers, and an order arriving once the switch is on is refused at
    once; the route answers when the order on its way has its answer."""
    cookie = server.login()
    sending, sent = _in_background(server.post, "/place", {**ENTRY, "hold": 3})
    time.sleep(0.4)
    turning, turned = _in_background(
        server.post, "/sudarshan/risk/killswitch", {"phrase": "STOP"}, 60, cookie
    )
    deadline = time.monotonic() + 10
    while not json.loads(server.get("/switch")[1])["on"]:
        assert time.monotonic() < deadline, "the switch never went on"
        time.sleep(0.05)
    for _ in range(5):
        started = time.monotonic()
        assert server.get("/ping")[0] == 200
        assert time.monotonic() - started < 1.0
    status, body, took = server.post("/place", ENTRY)
    assert status == 403 and body["body"]["message"].startswith("New orders are refused")
    assert took < 1.0, f"the refusal waited {took:.2f}s"
    assert turning.is_alive()  # still waiting for the order on its way
    turning.join(30)
    sending.join(30)
    status, body, took = turned["result"]
    assert status == 200, body
    assert body["data"]["on"] is True and body["data"]["surface"] == "app"
    drain = next(step for step in body["data"]["report"]["steps"] if step["step"] == "drain")
    assert drain["detail"] == {"sent": 1, "waiting": 0}
    assert took > 1.5  # it waited for that order, not for its ten seconds
    assert sent["result"][0] == 200
