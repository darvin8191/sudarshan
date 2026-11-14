"""The alert route under a real gunicorn worker, eventlet and gthread, one worker as in production.

A small Flask app carries the real blueprint, the real intake and the real store on the tests'
fake desk (automation_world.py), whose order call can be made slow: under eventlet a sleep is a
cooperative wait, like a slow Upstox call. Alerts are sent the way the server's nginx hands them
on: to the app's port with X-Real-IP set to the sender's address.

Pattern: sudarshan/guard/tests/test_guard_gate_gunicorn.py. The server runs in a process group of
its own, and the teardown stops the whole group.
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
from automation_world import TV, indicator_alert, strategy_alert

REPO = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent

pytestmark = pytest.mark.skipif(os.name != "posix", reason="process groups are POSIX")

APP = textwrap.dedent(
    """
    import os, threading, time
    from flask import Flask, jsonify, request
    from limiter import limiter
    import blueprints.sudarshan_automation as routes
    from automation_world import FakeDesk
    from sudarshan.automation import contract, intake, store

    DESK = FakeDesk()
    DESK.now = time.time  # the wall clock: alerts carry real fire times
    intake.DESK = lambda: DESK
    STORE = store.shared()

    app = Flask(__name__)
    app.config.update(RATELIMIT_ENABLED=os.environ.get("TEST_LIMITS") == "1")
    limiter.init_app(app)
    app.register_blueprint(routes.sudarshan_automation_bp)

    @app.route("/ping")
    def ping():
        return "ok"

    @app.route("/make", methods=["POST"])
    def make():
        body = request.get_json()
        strategy, token = STORE.create(contract.clean(body["form"]))
        STORE.set_state(strategy.id, strategy.version, body.get("state", "active"))
        return jsonify(id=strategy.id, token=token)

    @app.route("/desk", methods=["POST"])
    def desk():
        for key, value in request.get_json().items():
            setattr(DESK, key, value)
        return jsonify(ok=True)

    @app.route("/seen")
    def seen():
        sid = int(request.args.get("id", 0))
        rows = list(reversed(STORE.alerts(sid or None, limit=500)))
        fds = len(os.listdir("/proc/self/fd"))
        return jsonify(
            placed=DESK.placed,
            positions={"|".join(map(str, k)): v for k, v in DESK.positions.items()},
            alerts=[{k: r[k] for k in ("verdict", "sentence", "order_id", "answer_ms", "target")} for r in rows],
            state=STORE.state(sid).__dict__ if sid else None,
            fds=fds, threads=threading.active_count(), pid=os.getpid(), locks=len(intake.LOCKS),
        )
    """
)

FORM = {"name": "ORB", "kind": "future", "underlying": "NIFTY", "product": "MIS", "lots": 1}


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
    def __init__(
        self, tmp_path: Path, worker: str, *, stage: str = "sandbox", limits: bool = False
    ):
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        (app_dir / "automation_fixture_app.py").write_text(APP)
        self.port, self.worker = _free_port(), worker
        self.log = tmp_path / "gunicorn.log"
        self.db = tmp_path / "sudarshan_automation.db"
        env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
        env.pop("GUNICORN_CMD_ARGS", None)
        env.update(
            PYTHONPATH=os.pathsep.join([str(app_dir), str(HERE), str(REPO)]),
            LOG_DIR=str(tmp_path / "log"),
            XDG_RUNTIME_DIR=str(tmp_path),
            SUDARSHAN_AUTOMATION=stage,
            SUDARSHAN_AUTOMATION_DB=str(self.db),
            DATABASE_URL=f"sqlite:///{tmp_path}/openalgo.db",
            TRUST_PROXY_HEADERS="TRUE",  # as behind the server's nginx, which sets X-Real-IP
            TEST_LIMITS="1" if limits else "0",
        )
        command = [
            sys.executable, "-m", "gunicorn", "--worker-class", worker, "--workers", "1",
            *(["--threads", "64"] if worker == "gthread" else []),  # production's pool
            "--bind", f"127.0.0.1:{self.port}", "--timeout", "120",
            "--graceful-timeout", "2", "--log-level", "info", "--access-logfile", "-",
            "automation_fixture_app:app",
        ]  # fmt: skip
        with self.log.open("wb") as log:
            self.process = subprocess.Popen(
                command, cwd=app_dir, env=env, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True,
            )  # fmt: skip
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                if self.get("/ping", timeout=2)[0] == 200:
                    return
            except OSError:
                time.sleep(0.2)
        self.stop()
        raise AssertionError("gunicorn did not start:\\n" + self.log.read_text())

    def _ask(self, method, path, body=None, headers=None, timeout=60.0):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            started = time.monotonic()
            conn.request(method, path, body=body, headers=headers or {})
            reply = conn.getresponse()
            data = reply.read()
            return reply.status, data, time.monotonic() - started
        finally:
            conn.close()

    def get(self, path: str, timeout: float = 10):
        return self._ask("GET", path, timeout=timeout)

    def json(self, path: str, body: dict | None = None):
        if body is None:
            return json.loads(self._ask("GET", path)[1])
        head = {"Content-Type": "application/json"}
        return json.loads(self._ask("POST", path, json.dumps(body), head)[1])

    def make(self, **form) -> tuple[int, str]:
        made = self.json("/make", {"form": {**FORM, **form}})
        return made["id"], made["token"]

    def alert(self, token: str, body: bytes, ip: str = TV, timeout: float = 60.0):
        head = {"Content-Type": "application/json", "X-Real-IP": ip}
        status, data, took = self._ask("POST", f"/sudarshan/tv/{token}", body, head, timeout)
        return status, json.loads(data or b"{}"), took

    def seen(self, sid: int = 0) -> dict:
        return self.json(f"/seen?id={sid}")

    def stop(self) -> None:
        _stop_process_group(self.process)


@pytest.fixture(params=["eventlet", "gthread"])
def worker(request):
    pytest.importorskip("gunicorn", reason="gunicorn is installed by the production installer")
    if request.param == "eventlet":
        pytest.importorskip("eventlet", reason="eventlet is installed by the production installer")
    return request.param


@pytest.fixture
def server(worker, tmp_path):
    srv = Server(tmp_path, worker)
    yield srv
    srv.stop()


def _in_background(fn, *args, **kwargs):
    out: dict = {}

    def run():
        try:
            out["result"] = fn(*args, **kwargs)
        except Exception as exc:  # a hang-up on purpose is the caller's business
            out["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    return thread, out


def test_an_alert_is_taken_and_written_under_a_real_worker(server):
    sid, token = server.make()
    status, body, _ = server.alert(token, indicator_alert("flat", time.time()))
    assert (status, body) == (200, {"status": "success", "message": "handled"})
    seen = server.seen(sid)
    assert len(seen["alerts"]) == 1 and seen["locks"] == 0


def test_two_hundred_unknown_links_change_nothing_and_ban_nobody(worker, tmp_path):
    srv = Server(tmp_path, worker, limits=False)
    try:
        sid, token = srv.make()
        for n in range(20):  # the worker's own pool warms up first (gthread starts threads as needed)
            srv.alert("sdt_" + f"{n:043d}", b"{}")
        before = srv.seen(sid)
        for n in range(20, 220):
            status, body, _ = srv.alert("sdt_" + f"{n:043d}", b"{}")
            assert (status, body) == (404, {"status": "error", "message": "Not found"})
        after = srv.seen(sid)
        assert after["alerts"] == [] and after["pid"] == before["pid"]
        # The automation starts no thread of its own, so only descriptors are counted: the
        # worker's pool (64 threads under gthread) grows as gunicorn pleases.
        assert after["fds"] <= before["fds"] + 2, (before["fds"], after["fds"])
        # the sender's own next alert is still taken: nothing was banned on the way
        assert srv.alert(token, indicator_alert("flat", time.time()))[0] == 200
        assert srv.get("/ping")[0] == 200
        log = srv.log.read_text()
        assert log.count("refused an alert link nobody made") == 1  # one line, not two hundred
    finally:
        srv.stop()


def test_the_limits_answer_413_and_429_and_the_worker_keeps_answering(worker, tmp_path):
    srv = Server(tmp_path, worker, limits=True)
    try:
        sid, token = srv.make(max_alerts_day=200)
        assert srv.alert(token, b"x" * (20 * 1024))[0] == 413
        codes = [
            srv.alert(token, indicator_alert("flat", time.time() - 20 + n / 10))[0]
            for n in range(130)
        ]
        assert codes.count(200) == 60 and set(codes[60:120]) == {429}  # the link's 60 a minute
        assert set(codes[120:]) == {429}  # and the caller's 120
        assert srv.get("/ping")[0] == 200
        assert len(srv.seen(sid)["alerts"]) == 60
    finally:
        srv.stop()


def test_no_log_of_the_worker_holds_the_token(server):
    """gunicorn's own access log is the one place the path is written by code that is not ours;
    production runs without it, and the app's traffic log redacts the path."""
    sid, token = server.make()
    server.alert(token, strategy_alert("long", time.time(), before="flat"))
    from utils.url_redaction import redact_url_credentials

    for line in server.log.read_text().splitlines():
        if "/sudarshan/tv/" in line:
            assert token not in redact_url_credentials(line)
    assert token not in server.db.read_bytes().decode("latin-1")
