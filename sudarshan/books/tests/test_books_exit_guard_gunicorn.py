"""The exit guard under a real gunicorn worker, eventlet and gthread, one worker as in production.

A small Flask app carries the real claims from sudarshan/books/exit_guard.py: the app's close route
decorated with ``one_exit_at_a_time``, and two Exit all surfaces (the app's and the API's) that pass
through ``close_all_once`` as services/close_position_service.py does. Its broker call is a sleep,
which under eventlet is a cooperative wait like a slow Upstox call.

Adopted from the re-check's probe of 76462c9. The server runs in a process group of its own, and
the teardown stops the whole group (see sudarshan/autologin/tests/test_autologin_gunicorn.py: an
eventlet worker outlives an arbiter stopped on its own).
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
    import os, time
    from flask import Flask, jsonify, request
    from sudarshan.books import exit_guard

    exit_guard._book = lambda: "live"  # no settings database here
    if os.environ.get("GUARD_WAIT_S"):
        exit_guard.EXIT_ALL_WAITS_S = float(os.environ["GUARD_WAIT_S"])

    app = Flask(__name__)
    closed = []  # for each Exit all that reached the broker: how many exits were being placed

    def slow_close_all(hold):
        closed.append(len(exit_guard.EXITS._positions))
        time.sleep(hold)  # under eventlet a cooperative wait, as a slow Upstox call is
        return {"status": "success", "message": "All Open Positions SquaredOff"}, 200

    def answered(body_and_code):
        body, code = body_and_code
        return jsonify(body), code

    @app.route("/ping")
    def ping():
        return "ok"

    @app.route("/claims")
    def claims():
        return jsonify(held=len(exit_guard.EXITS), pid=os.getpid(), closed=closed)

    @app.route("/close_position", methods=["POST"])
    @exit_guard.one_exit_at_a_time()
    def close_position():
        hold = float(request.args.get("hold", "0"))
        if request.args.get("raise"):
            raise RuntimeError("broke inside the view")
        if request.args.get("block"):
            import eventlet.patcher
            eventlet.patcher.original("time").sleep(hold)  # blocks the whole hub
        else:
            time.sleep(hold)
        return jsonify(status="success", orderid="1")

    @app.route("/close_all_positions", methods=["POST"])  # the app's Exit all
    def close_all_positions():
        hold = float(request.args.get("hold", "0"))
        return answered(exit_guard.close_all_once("live", slow_close_all, hold))

    @app.route("/api/v1/closeposition", methods=["POST"])  # TradingView, scripts, Telegram
    def api_close_position():
        hold = float(request.args.get("hold", "0"))
        return answered(exit_guard.close_all_once("live", slow_close_all, hold))
    """
)

EXIT = {"symbol": "NIFTY27OCT2625900CE", "exchange": "NFO", "product": "MIS"}
OTHER = {"symbol": "BANKNIFTY27OCT2657500PE", "exchange": "NFO", "product": "NRML"}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _stop_process_group(proc: subprocess.Popen, grace_s: float = 5.0) -> None:
    """SIGTERM to the whole group, then SIGKILL for whatever is left, then reap the leader."""
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
            pass  # every member has exited already
        proc.wait(timeout=15)


class Server:
    def __init__(self, tmp_path: Path, worker: str, timeout: int = 30, wait_s: float | None = None):
        app_dir = tmp_path / "app"
        app_dir.mkdir()
        (app_dir / "guard_fixture_app.py").write_text(APP)
        self.port = _free_port()
        self.log = tmp_path / "gunicorn.log"
        env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
        env.pop("GUNICORN_CMD_ARGS", None)
        env.update(
            PYTHONPATH=os.pathsep.join([str(app_dir), str(REPO)]),
            LOG_DIR=str(tmp_path / "log"),
            # gunicorn 25.1+ opens a control socket; this keeps it in the test's own folder.
            XDG_RUNTIME_DIR=str(tmp_path),
        )
        if wait_s is not None:
            env["GUARD_WAIT_S"] = str(wait_s)
        command = [
            sys.executable, "-m", "gunicorn", "--worker-class", worker, "--workers", "1",
            *(["--threads", "8"] if worker == "gthread" else []),
            "--bind", f"127.0.0.1:{self.port}", "--timeout", str(timeout),
            "--graceful-timeout", "2", "--log-level", "info", "guard_fixture_app:app",
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
        deadline = time.monotonic() + 60
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

    def post(self, path: str, body: dict, timeout: float = 30):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=timeout)
        try:
            started = time.monotonic()
            conn.request(
                "POST", path, body=json.dumps(body), headers={"Content-Type": "application/json"}
            )
            reply = conn.getresponse()
            data = reply.read()
            return reply.status, json.loads(data or b"{}"), time.monotonic() - started
        finally:
            conn.close()

    def claims(self) -> dict:
        return json.loads(self.get("/claims")[1])

    def stop(self) -> None:
        _stop_process_group(self.process)


def _needs(worker: str) -> None:
    pytest.importorskip("gunicorn", reason="gunicorn is installed by the production installer")
    if worker == "eventlet":
        pytest.importorskip("eventlet", reason="eventlet is installed by the production installer")


@pytest.fixture(params=["eventlet", "gthread"])
def server(request, tmp_path):
    _needs(request.param)
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


def test_a_second_exit_is_refused_at_once_while_the_first_is_placed(server):
    first, out = _in_background(server.post, "/close_position?hold=3", EXIT)
    time.sleep(0.7)
    status, body, took = server.post("/close_position", EXIT)
    assert status == 409 and "still being placed" in body["message"], (status, body)
    assert took < 1.0, f"the refusal waited {took:.2f}s"
    other_status, _, other_took = server.post("/close_position?hold=0.2", OTHER)
    assert other_status == 200 and other_took < 1.5, (other_status, other_took)
    assert server.get("/ping")[0] == 200  # the worker keeps serving meanwhile
    first.join(15)
    assert out["result"][0] == 200
    assert server.claims()["held"] == 0
    assert server.post("/close_position", EXIT)[0] == 200


EXIT_ALL_BEING_PLACED = "Exit all is still being placed. Wait for it, then check Orders."


def test_an_exit_all_waits_for_the_exit_in_flight_then_closes(server):
    """A TradingView Exit all lands while the phone's exit is being placed: it waits, then closes."""
    single, placed = _in_background(server.post, "/close_position?hold=3", EXIT)
    time.sleep(0.7)
    everything, closed = _in_background(server.post, "/api/v1/closeposition", {})
    time.sleep(0.3)
    # Meanwhile no new exit starts, a second Exit all is refused at once, and the worker serves.
    status, body, took = server.post("/close_position", OTHER)
    assert (status, body["message"], took < 1.0) == (409, EXIT_ALL_BEING_PLACED, True), took
    status, body, took = server.post("/close_all_positions", {})
    assert (status, body["message"], took < 1.0) == (409, EXIT_ALL_BEING_PLACED, True), took
    ping_started = time.monotonic()
    assert server.get("/ping")[0] == 200 and time.monotonic() - ping_started < 1.0
    single.join(15)
    everything.join(15)
    assert placed["result"][0] == 200
    status, body, took = closed["result"]
    assert (status, body["status"]) == (200, "success")
    assert took >= 1.5, f"it went after {took:.2f}s, before the exit had ended"
    assert server.claims() | {"pid": 0} == {"held": 0, "pid": 0, "closed": [0]}


def test_an_exit_all_waiting_on_two_exits_keeps_the_worker_serving(server):
    """After the first exit ends the wait goes on yielding: the worker serves the second's end."""
    first, _ = _in_background(server.post, "/close_position?hold=1.5", EXIT)
    second, _ = _in_background(server.post, "/close_position?hold=3", OTHER)
    time.sleep(0.5)
    everything, closed = _in_background(server.post, "/api/v1/closeposition", {})
    first.join(15)  # one exit has ended; the Exit all waits on for the other
    time.sleep(0.5)
    started = time.monotonic()
    assert server.get("/ping", timeout=5)[0] == 200
    assert time.monotonic() - started < 1.0
    second.join(15)
    everything.join(15)
    assert closed["result"][0] == 200
    assert server.claims()["closed"] == [0]


def test_an_exit_all_whose_caller_gave_up_still_waits_and_closes(server):
    """TradingView gives up on a webhook after 3 s; the view goes on, waits, and closes the rest."""
    single, _ = _in_background(server.post, "/close_position?hold=3", EXIT)
    time.sleep(0.5)
    sock = socket.create_connection(("127.0.0.1", server.port))
    sock.sendall(
        b"POST /api/v1/closeposition HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
        b"Content-Length: 2\r\n\r\n{}"
    )
    time.sleep(0.5)
    sock.close()  # the caller gave up while the Exit all was waiting
    single.join(15)
    deadline = time.monotonic() + 10
    while not server.claims()["closed"] and time.monotonic() < deadline:
        time.sleep(0.2)
    assert server.claims()["closed"] == [0]  # it reached the broker, after the exit had ended
    while server.claims()["held"] and time.monotonic() < deadline:
        time.sleep(0.2)
    assert server.claims()["held"] == 0


@pytest.mark.parametrize("worker", ["eventlet", "gthread"])
def test_an_exit_all_whose_wait_runs_out_is_refused(tmp_path, worker):
    _needs(worker)
    srv = Server(tmp_path, worker, wait_s=1.0)
    try:
        single, _ = _in_background(srv.post, "/close_position?hold=4", EXIT)
        time.sleep(0.5)
        status, body, took = srv.post("/api/v1/closeposition", {})
        assert (status, body["message"]) == (
            409,
            "An exit is still being placed. Wait for it, then check Orders.",
        )
        assert 0.8 <= took < 3.0, f"it waited {took:.2f}s for a wait of 1 s"
        assert srv.claims()["held"] == 1  # its own claim let go; the exit's still held
        single.join(15)
        assert srv.claims() | {"pid": 0} == {"held": 0, "pid": 0, "closed": []}
    finally:
        srv.stop()


def test_two_exit_alls_from_two_surfaces_send_one(server):
    first, out = _in_background(server.post, "/close_all_positions?hold=3", {})
    time.sleep(0.7)
    status, body, took = server.post("/api/v1/closeposition", {})
    assert (status, body["message"]) == (
        409,
        "Exit all is still being placed. Wait for it, then check Orders.",
    )
    assert took < 1.0, f"the refusal waited {took:.2f}s"
    status, body, _ = server.post("/close_position", EXIT)
    assert status == 409 and body["message"].startswith("Exit all is still being placed"), body
    first.join(15)
    assert out["result"][0] == 200
    assert server.claims()["held"] == 0
    assert server.post("/api/v1/closeposition", {})[0] == 200


def test_a_client_that_gives_up_leaves_the_claim_until_the_view_ends(server):
    sock = socket.create_connection(("127.0.0.1", server.port))
    payload = json.dumps(EXIT).encode()
    sock.sendall(
        b"POST /close_position?hold=3 HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
        + f"Content-Length: {len(payload)}\r\n\r\n".encode()
        + payload
    )
    time.sleep(0.7)
    sock.close()  # the phone gave up
    time.sleep(0.3)
    status, body, _ = server.post("/close_position", EXIT)
    assert status == 409, (status, body)  # still being placed on the server: held
    deadline = time.monotonic() + 10
    while server.claims()["held"] and time.monotonic() < deadline:
        time.sleep(0.2)
    assert server.claims()["held"] == 0
    assert server.post("/close_position", EXIT)[0] == 200


def test_a_view_that_raises_lets_its_claim_go(server):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    conn.request(
        "POST",
        "/close_position?raise=1",
        body=json.dumps(EXIT),
        headers={"Content-Type": "application/json"},
    )
    assert conn.getresponse().status == 500
    conn.close()
    assert server.claims()["held"] == 0
    assert server.post("/close_position", EXIT)[0] == 200


def test_a_worker_killed_on_its_timeout_takes_its_claims_with_it(tmp_path):
    _needs("eventlet")
    srv = Server(tmp_path, "eventlet", timeout=3)
    try:
        before = srv.claims()["pid"]
        blocked, _ = _in_background(_swallow, srv.post, "/close_position?block=1&hold=12", EXIT, 40)
        time.sleep(1.0)
        deadline = time.monotonic() + 40
        status = None
        while time.monotonic() < deadline:
            try:
                status = srv.post("/close_position", EXIT, timeout=5)[0]
                if status == 200:
                    break
            except OSError:
                pass
            time.sleep(0.5)
        assert status == 200  # the new worker holds no claim the old one took
        assert srv.claims()["pid"] != before, "the worker was not replaced"
        blocked.join(45)
    finally:
        srv.stop()


def _swallow(fn, *args):
    try:
        return fn(*args)
    except Exception as exc:  # the worker was killed under it
        return repr(exc)
