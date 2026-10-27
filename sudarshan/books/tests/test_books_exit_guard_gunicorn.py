"""The exit guard under a real gunicorn worker, eventlet and gthread, one worker as in production.

A small Flask app carries the real code: the app's Exit as blueprints/orders.py sends it (the
guard's answer first, then sudarshan/books/single_exit.py through Upstox's place_smartorder_api),
and Exit all as every surface sends it (``close_all_once`` over Upstox's close_all_positions).
Only Upstox's HTTP client and its symbol master are replaced: a book that never shows an exit (it
lags that long), an empty order book, and orders that take as long as a test says (a cooperative
wait under eventlet, like a slow Upstox call).

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
    import json, os, time
    from flask import Flask, jsonify, request

    import broker.upstox.api.order_api as upstox
    from sudarshan.books import exit_guard
    from sudarshan.books.single_exit import exit_live

    if os.environ.get("SENDS_FOR_S"):
        exit_guard.EXIT_ALL_SENDS_FOR_S = float(os.environ["SENDS_FOR_S"])

    ROWS = {  # symbol: instrument token, exchange, Upstox product, quantity (never changes)
        "NIFTY27OCT2625900CE": ("NSE_FO|1001", "NFO", "I", 65),
        "BANKNIFTY27OCT2657500PE": ("NSE_FO|1002", "NFO", "D", -30),
        "SBIN": ("NSE_EQ|1", "NSE", "I", 10),
    }
    BY_TOKEN = {row[0]: symbol for symbol, row in ROWS.items()}
    STATE = {"hang": {}, "refuse": False, "orders": [], "started": []}


    class Reply:
        def __init__(self, status, body):
            self.status_code = self.status = status
            self._body = body
            self.text = json.dumps(body)
            self.headers = {}

        def json(self):
            return self._body

        def raise_for_status(self):
            if self.status_code >= 400:
                import httpx

                request = httpx.Request("POST", "https://upstox.invalid")
                raise httpx.HTTPStatusError("refused", request=request, response=self)


    class FakeUpstox:
        def get(self, url, headers=None, **kwargs):
            if url.endswith("/v2/order/retrieve-all"):
                return Reply(200, {"status": "success", "data": []})
            rows = [
                {"instrument_token": token, "exchange": exchange, "product": product,
                 "tradingsymbol": symbol, "quantity": quantity}
                for symbol, (token, exchange, product, quantity) in ROWS.items()
            ]
            return Reply(200, {"status": "success", "data": rows})

        def post(self, url, headers=None, content=None, **kwargs):
            symbol = BY_TOKEN[json.loads(content)["instrument_token"]]
            STATE["started"].append(symbol)
            time.sleep(STATE["hang"].get(symbol, 0))  # under eventlet a cooperative wait
            if STATE["refuse"]:
                return Reply(400, {"status": "error", "errors": [{"message": "margin"}]})
            STATE["orders"].append(symbol)
            orderid = f"2610270000{len(STATE['orders']):02d}"
            return Reply(200, {"status": "success", "data": {"order_ids": [orderid]}})


    FAKE = FakeUpstox()
    os.environ["BROKER_API_KEY"] = "fake"
    upstox.get_httpx_client = lambda: FAKE
    upstox.apply_rate_limit = lambda category="standard": None
    upstox.get_token = lambda symbol, exchange: ROWS[symbol][0]
    upstox.get_br_symbol = lambda symbol, exchange: symbol
    upstox.get_symbol = lambda token, exchange: BY_TOKEN.get(token)

    app = Flask(__name__)


    @app.route("/ping")
    def ping():
        return "ok"


    @app.route("/claims")
    def claims():
        return jsonify(
            held=len(exit_guard.EXITS), pid=os.getpid(), orders=STATE["orders"],
            started=STATE["started"],
        )


    @app.route("/setup", methods=["POST"])
    def setup():
        STATE.update(request.get_json())
        return "ok"


    @app.route("/close_position", methods=["POST"])  # the app's Exit, as blueprints/orders.py
    def close_position():
        data = request.get_json()
        where = (data["exchange"], data["symbol"], data["product"])
        if request.args.get("raise") or request.args.get("block"):
            with exit_guard.one_exit("live", *where) as busy:
                if busy:
                    return jsonify(status="error", message=busy), 409
                if request.args.get("block"):
                    import eventlet.patcher

                    eventlet.patcher.original("time").sleep(float(request.args["block"]))
                raise RuntimeError("broke inside the claim")
        busy = exit_guard.why_busy("live", *where)
        if busy:
            return jsonify(status="error", message=busy), 409
        body, status, _sent = exit_live(
            upstox.place_smartorder_api, "token", symbol=data["symbol"],
            exchange=data["exchange"], product=data["product"], strategy="UI Exit Position",
        )
        return jsonify(body), status


    def exit_all():
        body, status = exit_guard.close_all_once("live", upstox.close_all_positions, "key", "token")
        return jsonify(body), status


    @app.route("/close_all_positions", methods=["POST"])  # the app's Exit all
    def close_all_positions():
        return exit_all()


    @app.route("/api/v1/closeposition", methods=["POST"])  # TradingView, scripts, Telegram, Flow
    def api_close_position():
        return exit_all()
    """
)

X = {"symbol": "NIFTY27OCT2625900CE", "exchange": "NFO", "product": "MIS"}
Y = {"symbol": "BANKNIFTY27OCT2657500PE", "exchange": "NFO", "product": "NRML"}
EXIT_ALL_BEING_PLACED = "Exit all is still being placed. Wait for it, then check Orders."
ON_ITS_WAY = "An exit for NIFTY 25900 CE is already on its way (sent "


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
    def __init__(
        self, tmp_path: Path, worker: str, timeout: int = 30, sends_for_s: float | None = None
    ):
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
        if sends_for_s is not None:
            env["SENDS_FOR_S"] = str(sends_for_s)
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
            try:
                answer = json.loads(data or b"{}")
            except ValueError:
                answer = {"page": data.decode(errors="replace")}
            return reply.status, answer, time.monotonic() - started
        finally:
            conn.close()

    def claims(self) -> dict:
        return json.loads(self.get("/claims")[1])

    def setup(self, **state) -> None:
        assert self.post("/setup", state)[0] == 200

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


def _until(condition, wait_s: float = 10.0) -> None:
    deadline = time.monotonic() + wait_s
    while not condition():
        assert time.monotonic() < deadline, "never happened"
        time.sleep(0.05)


def _pinged_at_once(server) -> bool:
    started = time.monotonic()
    return server.get("/ping")[0] == 200 and time.monotonic() - started < 1.0


def test_a_second_exit_is_refused_at_once_while_the_first_is_placed(server):
    server.setup(hang={X["symbol"]: 3})
    first, out = _in_background(server.post, "/close_position", X)
    _until(lambda: server.claims()["started"])
    status, body, took = server.post("/close_position", X)
    assert status == 409 and body["message"].startswith(ON_ITS_WAY), (status, body)
    assert took < 1.0, f"the refusal waited {took:.2f}s"
    status, body, took = server.post("/close_position", Y)  # another position: not held up
    assert (status, body["status"]) == (200, "success") and took < 1.5, (status, body, took)
    assert _pinged_at_once(server)  # the worker keeps serving meanwhile
    first.join(15)
    assert out["result"][0] == 200
    assert server.claims()["held"] == 0
    status, body, _ = server.post("/close_position", X)  # placed: noted, the book not showing it
    assert status == 409 and body["message"].startswith(ON_ITS_WAY)
    assert server.claims()["orders"] == [Y["symbol"], X["symbol"]]


def test_an_exit_all_during_an_exit_closes_the_rest_at_once(server):
    """A TradingView Exit all lands while the phone's exit is being placed: it does not wait."""
    server.setup(hang={X["symbol"]: 3, "SBIN": 1.5})
    single, placed = _in_background(server.post, "/close_position", X)
    _until(lambda: server.claims()["started"])
    everything, closed = _in_background(server.post, "/api/v1/closeposition", {})
    _until(lambda: "SBIN" in server.claims()["started"])  # the Exit all is placing its exits
    # Meanwhile no new exit starts, a second Exit all is refused at once, and the worker serves.
    status, body, took = server.post("/close_position", Y)
    assert (status, body["message"], took < 1.0) == (409, EXIT_ALL_BEING_PLACED, True), took
    status, body, took = server.post("/close_all_positions", {})
    assert (status, body["message"], took < 1.0) == (409, EXIT_ALL_BEING_PLACED, True), took
    assert _pinged_at_once(server)
    everything.join(15)
    status, body, took = closed["result"]
    assert took < 2.5, f"it took {took:.2f}s: it waited for the exit in flight"
    assert status == 409 and body["not_confirmed"] is True
    assert "Of 3 exits, 2 were sent and 1 was not sent." in body["message"]
    assert ON_ITS_WAY in body["message"]
    single.join(15)
    assert placed["result"][0] == 200
    assert sorted(server.claims()["orders"]) == sorted([X["symbol"], Y["symbol"], "SBIN"])
    assert server.claims()["held"] == 0


def test_a_tradingview_exit_all_sent_twice_a_second_apart_sends_once(server):
    assert server.post("/api/v1/closeposition", {})[0] == 200
    time.sleep(1.0)
    status, body, took = server.post("/api/v1/closeposition", {})
    assert took < 1.0, f"it took {took:.2f}s"
    assert status == 409 and "not_confirmed" not in body  # nothing went, and it says why
    assert "Of 3 exits, 3 were not sent." in body["message"]
    assert sorted(server.claims()["orders"]) == sorted([X["symbol"], Y["symbol"], "SBIN"])


def test_an_exit_all_after_one_whose_exits_were_refused_goes_at_once(server):
    server.setup(refuse=True)
    status, body, _ = server.post("/close_all_positions", {})
    assert status == 400 and "3 were refused" in body["message"]
    server.setup(refuse=False)
    status, body, took = server.post("/api/v1/closeposition", {})
    assert (status, body["status"]) == (200, "success") and took < 1.0
    assert len(server.claims()["orders"]) == 3


@pytest.mark.parametrize("worker", ["eventlet", "gthread"])
def test_an_exit_all_that_runs_out_of_time_stops_sending_and_the_worker_serves(tmp_path, worker):
    _needs(worker)
    srv = Server(tmp_path, worker, sends_for_s=1.0)
    try:
        srv.setup(hang={X["symbol"]: 1.5, Y["symbol"]: 1.5, "SBIN": 1.5})  # a slow Upstox
        everything, closed = _in_background(srv.post, "/api/v1/closeposition", {})
        _until(lambda: srv.claims()["started"])
        assert _pinged_at_once(srv)  # the slow call yields: the worker serves meanwhile
        everything.join(15)
        status, body, took = closed["result"]
        assert took < 1.0 + 1.5 + 1.0, took
        assert srv.claims()["orders"] == [X["symbol"]]  # the only one started in time
        assert status == 409 and body["not_confirmed"] is True
        assert "Of 3 exits, 1 was sent and 2 were not sent in time." in body["message"]
    finally:
        srv.stop()


def test_a_client_that_gives_up_leaves_the_claim_until_the_view_ends(server):
    server.setup(hang={X["symbol"]: 3})
    sock = socket.create_connection(("127.0.0.1", server.port))
    payload = json.dumps(X).encode()
    sock.sendall(
        b"POST /close_position HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
        + f"Content-Length: {len(payload)}\r\n\r\n".encode()
        + payload
    )
    _until(lambda: server.claims()["started"])
    sock.close()  # the phone gave up
    time.sleep(0.3)
    status, body, _ = server.post("/close_position", X)
    assert status == 409, (status, body)  # still being placed on the server: held
    _until(lambda: server.claims()["held"] == 0)
    assert server.claims()["orders"] == [X["symbol"]]  # it went, once


def test_an_exit_all_whose_caller_gave_up_still_closes(server):
    """TradingView gives up on a webhook after 3 s; the view goes on and closes every position."""
    server.setup(hang={X["symbol"]: 2})
    sock = socket.create_connection(("127.0.0.1", server.port))
    sock.sendall(
        b"POST /api/v1/closeposition HTTP/1.1\r\nHost: x\r\nContent-Type: application/json\r\n"
        b"Content-Length: 2\r\n\r\n{}"
    )
    _until(lambda: server.claims()["started"])
    sock.close()
    _until(lambda: len(server.claims()["orders"]) == 3)
    _until(lambda: server.claims()["held"] == 0)


def test_a_view_that_raises_lets_its_claim_go(server):
    status, _body, _ = server.post("/close_position?raise=1", X)
    assert status == 500
    assert server.claims()["held"] == 0
    assert server.post("/close_position", X)[0] == 200


def test_a_worker_killed_on_its_timeout_takes_its_claims_with_it(tmp_path):
    _needs("eventlet")
    srv = Server(tmp_path, "eventlet", timeout=3)
    try:
        before = srv.claims()["pid"]
        blocked, _ = _in_background(_swallow, srv.post, "/close_position?block=12", X, 40)
        time.sleep(1.0)
        deadline = time.monotonic() + 40
        status = None
        while time.monotonic() < deadline:
            try:
                status = srv.post("/close_position", X, timeout=5)[0]
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
