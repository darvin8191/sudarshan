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
from automation_world import NOW, TV, at, indicator_alert, strategy_alert

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

    # The clock is a named moment plus the time this server has been up, never the wall clock:
    # the desk's contract list is fixed (NIFTY futures to 25 Jan 2027), so on the wall clock
    # every test that expects an order turned red near each expiry and for good after the last.
    BASE, STARTED = float(os.environ["TEST_CLOCK_BASE"]), time.monotonic()

    def now():
        return BASE + (time.monotonic() - STARTED)

    MARKS, ROUTER = [], []

    class RealSender(FakeDesk):
        # The real sender (Desk.place) over OpenAlgo's smart order service, whose last step is
        # stood in below: what the mark reads there, under this worker, is what is tested.

        def place(self, order, api_key, analyze):
            self.placed.append({**order, "analyze": analyze})
            return Desk.place(self, order, api_key, analyze)

    def with_auth(order, auth_token, broker, original, analyze=None):
        from sudarshan.books import add_sizing
        who = order.get("strategy")
        MARKS.append({"who": who, "asked": add_sizing.asked(), "analyze": analyze})
        time.sleep(DESK.delay)  # a slow broker: a cooperative wait under eventlet
        MARKS.append({"who": who, "asked": add_sizing.asked(), "analyze": analyze})
        return True, {"status": "success", "orderid": f"M{len(MARKS)}"}, 200

    REAL = os.environ.get("TEST_REAL_SENDER") == "1"
    if REAL:
        import services.order_router_service as router
        import services.place_smart_order_service as service
        from sudarshan.automation.desk import Desk
        service.place_smart_order_with_auth = with_auth
        router.should_route_to_pending = lambda *a, **k: (ROUTER.append(a), True)[1]
    DESK = RealSender() if REAL else FakeDesk()
    DESK.now = now
    intake.DESK = lambda: DESK
    STORE = store.shared()

    app = Flask(__name__)
    app.config.update(RATELIMIT_ENABLED=os.environ.get("TEST_LIMITS") == "1")
    limiter.init_app(app)
    app.register_blueprint(routes.sudarshan_automation_bp)

    @app.route("/ping")
    def ping():
        return "ok"

    @app.route("/now")
    def clock():
        return jsonify(now=now())

    @app.route("/plain", methods=["POST"])
    def plain():
        # A smart order that is not the intake's (the REST API's own, say), on this worker.
        from services.place_smart_order_service import place_smart_order
        order = {"strategy": "REST", "symbol": "SBIN", "exchange": "NSE", "position_size": "1"}
        ok, body, status = place_smart_order(order, auth_token="t", broker="upstox", analyze=True)
        return jsonify(body), status

    @app.route("/marks")
    def marks():
        return jsonify(marks=MARKS, router=len(ROUTER))

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

    @app.route("/tune", methods=["POST"])
    def tune():
        for key, value in request.get_json().items():
            setattr(intake, key, value)
        return jsonify(ok=True)

    @app.route("/switch", methods=["POST"])
    def switch():
        from sudarshan.automation import control
        body = request.get_json()
        try:
            changed = control.switch(body["id"], None, body["state"], "the app", desk=DESK, store=STORE)
        except control.Refused as refused:
            return jsonify(status="error", message=str(refused)), refused.status
        return jsonify(status="success", state=changed.state)

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
        self,
        tmp_path: Path,
        worker: str,
        *,
        stage: str = "sandbox",
        limits: bool = False,
        base: float = NOW,
        real_sender: bool = False,
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
            # the caller's own path last: where gunicorn and eventlet are when they are not in
            # the interpreter's site-packages (a developer's side folder)
            PYTHONPATH=os.pathsep.join(
                [str(app_dir), str(HERE), str(REPO), *filter(None, [env.get("PYTHONPATH")])]
            ),
            LOG_DIR=str(tmp_path / "log"),
            XDG_RUNTIME_DIR=str(tmp_path),
            SUDARSHAN_AUTOMATION=stage,
            SUDARSHAN_AUTOMATION_DB=str(self.db),
            DATABASE_URL=f"sqlite:///{tmp_path}/openalgo.db",
            TRUST_PROXY_HEADERS="TRUE",  # as behind the server's nginx, which sets X-Real-IP
            TEST_LIMITS="1" if limits else "0",
            TEST_CLOCK_BASE=repr(float(base)),  # the moment the server's clock starts from
            TEST_REAL_SENDER="1" if real_sender else "0",
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

    def now(self) -> float:
        """The server's clock, which every alert here takes its fire time from."""
        return float(self.json("/now")["now"])

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
    status, body, _ = server.alert(token, indicator_alert("flat", server.now()))
    assert (status, body) == (200, {"status": "success", "message": "handled"})
    seen = server.seen(sid)
    assert len(seen["alerts"]) == 1 and seen["locks"] == 0


def test_two_hundred_unknown_links_change_nothing_and_ban_nobody(worker, tmp_path):
    srv = Server(tmp_path, worker, limits=False)
    try:
        sid, token = srv.make()
        for n in range(
            20
        ):  # the worker's own pool warms up first (gthread starts threads as needed)
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
        assert srv.alert(token, indicator_alert("flat", srv.now()))[0] == 200
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
        # The link's limit counts by the clock minute: a burst that straddled the turn of a minute
        # would be counted in two (this failed once at :58). Start it with room to finish.
        if srv.now() % 60 > 40:
            time.sleep(61 - srv.now() % 60)
        fired = srv.now() - 20
        codes = [srv.alert(token, indicator_alert("flat", fired + n / 10))[0] for n in range(130)]
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
    server.alert(token, strategy_alert("long", server.now(), before="flat"))
    from utils.url_redaction import redact_url_credentials

    for line in server.log.read_text().splitlines():
        if "/sudarshan/tv/" in line:
            assert token not in redact_url_credentials(line)
    assert token not in server.db.read_bytes().decode("latin-1")


# ---- A5: alerts place smart orders ----------------------------------------------------------------


def _pings_stay_fast(server, seconds: float) -> None:
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        started = time.monotonic()
        assert server.get("/ping")[0] == 200
        assert time.monotonic() - started < 1.0, "the worker stopped answering"
        time.sleep(0.1)


def test_a_slow_order_holds_one_alert_while_the_worker_and_another_strategy_go_on(server):
    """A fake broker that takes 4 s: /ping keeps answering and a second strategy's alert is
    handled meanwhile. Under eventlet this is the hub staying alive across the order call."""
    first, first_token = server.make()
    second, second_token = server.make(name="Second", underlying="BANKNIFTY")
    server.json("/desk", {"delay": 4.0})
    slow, out = _in_background(server.alert, first_token, indicator_alert("long", server.now()))
    time.sleep(0.5)
    server.json("/desk", {"delay": 0.0})
    _pings_stay_fast(server, 1.0)
    status, _, took = server.alert(second_token, indicator_alert("long", server.now()))
    assert status == 200 and took < 2.0  # not behind the first strategy's order
    assert [a["verdict"] for a in server.seen(second)["alerts"]] == ["sent"]
    assert [a["verdict"] for a in server.seen(first)["alerts"]] == ["claimed"]  # still being placed
    slow.join(20)
    assert out["result"][0] == 200 and out["result"][2] >= 3.5
    done = server.seen(first)
    assert [a["verdict"] for a in done["alerts"]] == ["sent"] and done["locks"] == 0
    assert done["alerts"][0]["answer_ms"] >= 3500  # the time to answer is on the line


def test_the_sender_hangs_up_and_the_order_is_still_placed_once(server):
    """TradingView stops waiting after 3 s. Here the sender is gone after 0.5 s while the broker
    takes 3 s: still one order, one finished line, and the worker answers throughout."""
    sid, token = server.make()
    server.json("/desk", {"delay": 3.0})
    body = indicator_alert("long", server.now())
    with socket.create_connection(("127.0.0.1", server.port), timeout=5) as raw:
        raw.sendall(
            f"POST /sudarshan/tv/{token} HTTP/1.1\r\nHost: x\r\nX-Real-IP: {TV}\r\n"
            f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n".encode()
            + body
        )
        time.sleep(0.5)  # the request is in; now the sender goes away
    _pings_stay_fast(server, 1.0)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        seen = server.seen(sid)
        if seen["alerts"] and seen["alerts"][0]["verdict"] != "claimed":
            break
        time.sleep(0.2)
    assert [a["verdict"] for a in seen["alerts"]] == ["sent"] and seen["alerts"][0]["order_id"]
    assert len(seen["placed"]) == 1 and seen["state"]["last_target"] == 1 and seen["locks"] == 0
    # and TradingView's own second try of it, if it makes one, is a repeat
    server.json("/desk", {"delay": 0.0})
    assert server.alert(token, body)[1] == {"status": "success", "message": "already handled"}
    assert len(server.seen(sid)["placed"]) == 1


def test_two_identical_alerts_together_place_one_order(server):
    sid, token = server.make()
    server.json("/desk", {"delay": 0.5})
    body = strategy_alert("long", server.now(), before="flat")
    a, out_a = _in_background(server.alert, token, body)
    b, out_b = _in_background(server.alert, token, body)
    a.join(20)
    b.join(20)
    said = sorted(out["result"][1]["message"] for out in (out_a, out_b))
    assert said == ["already handled", "handled"]
    seen = server.seen(sid)
    assert len(seen["placed"]) == 1
    assert sorted(a["verdict"] for a in seen["alerts"]) == ["repeat", "sent"]


def test_the_chain_wait_does_not_stall_the_worker(server):
    """An alert whose "before" does not fit waits up to 1.5 s for its sibling, without the lock."""
    sid, token = server.make()
    waiting, out = _in_background(
        server.alert, token, strategy_alert("flat", server.now(), before="long", order_id="X")
    )
    time.sleep(0.2)
    _pings_stay_fast(server, 0.8)
    waiting.join(10)
    status, _, took = out["result"]
    assert status == 200 and 1.2 < took < 4.0
    (line,) = server.seen(sid)["alerts"]
    assert line["verdict"] == "no_action" and "an earlier alert did not arrive" in line["sentence"]


def test_a_stop_and_an_alert_under_a_real_worker(server):
    sid, token = server.make()
    server.json("/desk", {"delay": 1.0})
    alert, out = _in_background(server.alert, token, indicator_alert("long", server.now()))
    time.sleep(0.3)  # the alert holds the strategy's lock and its order is on its way
    started = time.monotonic()
    answer = server.json("/switch", {"id": sid, "state": "stopped"})
    waited = time.monotonic() - started
    alert.join(10)
    assert answer == {"status": "success", "state": "stopped"}
    assert waited > 0.5  # the stop waited for the alert being handled, then was answered
    seen = server.seen(sid)
    assert [a["verdict"] for a in seen["alerts"]] == ["sent"]
    server.json("/desk", {"delay": 0.0})
    server.alert(token, indicator_alert("short", server.now()))
    assert [a["verdict"] for a in server.seen(sid)["alerts"]] == ["sent", "stopped"]
    assert len(server.seen(sid)["placed"]) == 1  # nothing after the stop was answered


def test_a_long_wait_for_the_strategys_lock(server):
    """Under gthread a wait past the limit (30 s in production, 1 s here) answers the busy
    sentence and sends nothing; under eventlet the alert waits its turn, as the smart order's own
    lock does."""
    sid, token = server.make()
    server.json("/tune", {"LOCK_WAIT_S": 1.0})
    server.json("/desk", {"delay": 3.0})
    first, _ = _in_background(server.alert, token, indicator_alert("long", server.now() - 1))
    time.sleep(0.4)
    status, _, took = server.alert(token, indicator_alert("flat", server.now()))
    first.join(15)
    assert status == 200
    verdicts = [a["verdict"] for a in server.seen(sid)["alerts"]]
    if server.worker == "gthread":
        assert took < 2.5 and sorted(verdicts) == ["order_refused", "sent"]
        busy = next(a for a in server.seen(sid)["alerts"] if a["verdict"] == "order_refused")
        assert "an earlier alert of this strategy is still being handled" in busy["sentence"]
        assert len(server.seen(sid)["placed"]) == 1
    else:
        assert took > 2.0 and verdicts == ["sent", "sent"]  # it waited, then closed the position
        assert len(server.seen(sid)["placed"]) == 2


def test_five_hundred_alerts_leave_no_descriptor_behind(worker, tmp_path):
    srv = Server(tmp_path, worker)
    try:
        sid, token = srv.make(max_alerts_day=200, max_age_s=120)
        base = srv.now() - 60
        for n in range(30):  # the worker's pool and the store warm up first
            srv.alert(token, indicator_alert("long" if n % 2 == 0 else "flat", base + n / 10))
        before = srv.seen(sid)
        srv.json("/tune", {"_per_link": {}})  # the link's own limit is not what is measured here
        for n in range(30, 530):
            position = ("long", "flat", "short", "flat")[n % 4]
            assert srv.alert(token, indicator_alert(position, base + n / 10))[0] == 200
            if n % 50 == 0:
                srv.json("/tune", {"_per_link": {}})
        after = srv.seen(sid)
        assert after["pid"] == before["pid"] and after["locks"] == 0
        assert after["fds"] <= before["fds"] + 2, (before["fds"], after["fds"])
        assert len(srv.seen(sid)["alerts"]) >= 500
    finally:
        srv.stop()


# ---- M6: the clock is a named moment, at each boundary of the fixture's contract list ------------

#: The moments the wall clock used to turn these tests red on, and what a "long" alert of the
#: file's own strategy (NIFTY future, month rule, intraday, no entry in a contract's last 15
#: minutes) does at each. The list holds 24 Nov 2026, 29 Dec 2026 and 25 Jan 2027.
BOUNDARIES = [
    ("2026-11-16 10:15:02", "sent", "NIFTY24NOV26FUT"),  # NOW, the base every other test uses
    ("2026-11-24 15:14:00", "sent", "NIFTY24NOV26FUT"),  # expiry day, before the last 15 minutes
    ("2026-11-24 15:30:00", "not_mapped", None),  # too close to the November stop
    ("2026-11-24 15:41:00", "sent", "NIFTY29DEC26FUT"),  # after it: the December contract
    ("2026-12-29 15:20:00", "not_mapped", None),  # too close to the December stop
    ("2027-01-25 15:14:00", "sent", "NIFTY25JAN27FUT"),
    ("2027-01-25 15:20:00", "not_mapped", None),  # too close to the last contract's stop
    ("2027-01-25 15:31:00", "not_mapped", None),  # past the list: no open future, for good
]


def test_the_servers_clock_starts_at_the_named_moment_and_runs(server):
    first = server.now()
    assert 0 <= first - NOW < 120  # NOW plus the time the server has been up, never today's date
    time.sleep(0.3)
    assert 0.2 < server.now() - first < 5


@pytest.mark.parametrize(("when", "verdict", "symbol"), BOUNDARIES, ids=[b[0] for b in BOUNDARIES])
def test_a_long_alert_at_each_boundary_of_the_contract_list(tmp_path, when, verdict, symbol):
    """Under one real worker (gthread: the clock is the fixture's, whichever worker runs it)."""
    pytest.importorskip("gunicorn", reason="gunicorn is installed by the production installer")
    srv = Server(tmp_path, "gthread", base=at(when))
    try:
        sid, token = srv.make()
        assert srv.alert(token, indicator_alert("long", srv.now()))[0] == 200
        seen = srv.seen(sid)
        assert [a["verdict"] for a in seen["alerts"]] == [verdict], seen["alerts"]
        assert [o["symbol"] for o in seen["placed"]] == ([symbol] if symbol else [])
    finally:
        srv.stop()


def test_no_alert_in_this_file_takes_its_time_from_the_wall_clock():
    source = Path(__file__).read_text()
    tests = source[source.index("def _in_background") :]
    assert "time.time" + "()" not in tests
    assert "DESK.now = " + "time.time" not in source


# ---- A7 and H1 under a real worker: the mark stays with its request -----------------------------


def test_the_add_sizing_mark_stays_with_the_intakes_own_request(worker, tmp_path):
    """A7's mark is a context variable set around the intake's one call. While an alert's order
    is being placed (2 s, marked), other smart orders are placed on the same worker: none of
    them reads the mark, during or after, on any thread of the pool or any green thread. And the
    intake's call never asks the order mode (H1), which here would answer Semi-Auto."""
    srv = Server(tmp_path, worker, real_sender=True)
    try:
        sid, token = srv.make()
        srv.json("/desk", {"delay": 2.0})
        slow, out = _in_background(srv.alert, token, indicator_alert("long", srv.now()))
        deadline = time.monotonic() + 10
        while not srv.json("/marks")["marks"] and time.monotonic() < deadline:
            time.sleep(0.05)
        srv.json("/desk", {"delay": 0.0})
        during = [_in_background(srv.json, "/plain", {}) for _ in range(12)]
        for thread, _ in during:
            thread.join(20)
        assert all(done["result"]["status"] == "success" for _, done in during)
        halfway = srv.json("/marks")["marks"]
        assert [m["asked"] for m in halfway if m["who"] == "ORB"] == [True]  # still being placed
        assert [m["asked"] for m in halfway if m["who"] == "REST"] == [False] * 24
        slow.join(20)
        assert out["result"][0] == 200
        # afterwards, on every thread the pool has (64 under gthread): no mark is left behind
        for _ in range(4):
            after = [_in_background(srv.json, "/plain", {}) for _ in range(40)]
            for thread, _ in after:
                thread.join(20)
        seen = srv.json("/marks")
        mine = [m for m in seen["marks"] if m["who"] == "ORB"]
        rest = [m for m in seen["marks"] if m["who"] == "REST"]
        assert [(m["asked"], m["analyze"]) for m in mine] == [(True, True), (True, True)]
        assert len(rest) == 2 * (12 + 160) and not any(m["asked"] for m in rest)
        assert seen["router"] == 0  # the order mode was never asked
        line = srv.seen(sid)["alerts"][0]
        assert line["verdict"] == "sent" and line["order_id"] and srv.seen(sid)["locks"] == 0
    finally:
        srv.stop()
