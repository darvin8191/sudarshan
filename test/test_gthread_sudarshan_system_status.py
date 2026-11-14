"""The System page's routes under a real gunicorn gthread worker (SUDARSHAN M20.8, P8 10.7).

The status route reads files the watch replaces every two minutes, from pool threads that are
real; Disconnect writes the assistant's cutoff to the risk store behind the shared limiter.
Under eventlet both were one greenlet at a time by accident. So they are run where the threads
are real: a tiny Flask app with the real blueprint, CSRF, the shared limiter, OpenAlgo's own
session check and user table, and the real cutoff on a risk store of the test's own.

Held: twelve owner reads at once, again and again while the report is being replaced, each
get a whole report of one generation (never a torn one, never "no report"); a caller who is
not the owner is refused while they run; and twelve Disconnects sent together pass exactly the
limit's five, each of which ended access, with the cutoff recorded.

Linux with gunicorn installed only (the production server); skipped elsewhere.
"""

from __future__ import annotations

import http.client
import importlib.util
import json
import os
import socket
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or importlib.util.find_spec("gunicorn") is None,
    reason="needs gunicorn on Linux (the production server)",
)

APP = textwrap.dedent(
    """
    from datetime import datetime

    import pytz
    from flask import Flask, jsonify, session
    from flask_wtf.csrf import CSRFProtect, generate_csrf

    import blueprints.sudarshan_system as bridge
    from database import user_db
    from limiter import limiter
    from sudarshan.assistant import cutoff

    app = Flask(__name__)
    app.secret_key = "test-only-secret-key-" + "0" * 32  # pragma: allowlist secret
    app.config.update(WTF_CSRF_ENABLED=True, WTF_CSRF_TIME_LIMIT=None)
    app.csrf = CSRFProtect(app)
    limiter.init_app(app)
    bridge.get_active_sessions = lambda user: [{}]
    app.register_blueprint(bridge.sudarshan_system_bp)

    user_db.Base.metadata.create_all(user_db.engine)
    if user_db.find_user_by_username() is None:
        user_db.add_user("owner", "owner@example.invalid", "Fake-Pass-123!", is_admin=True)
    user_db.db_session.remove()

    @app.teardown_appcontext
    def release(exception=None):
        from utils.db_sessions import remove_all_scoped_sessions

        remove_all_scoped_sessions()

    @app.route("/ping")
    def ping():
        return "ok"

    @app.route("/login/<who>")
    def login(who):
        session["user"] = who
        session["logged_in"] = True
        session["login_time"] = datetime.now(pytz.timezone("Asia/Kolkata")).isoformat()
        return jsonify(csrf=generate_csrf())

    @app.route("/cutoff")
    def read_cutoff():
        return jsonify(after=cutoff.valid_after_ms())
    """
)
STATUS = "/sudarshan/system/api/status"
DISCONNECT = "/sudarshan/system/api/assistant/disconnect"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Server:
    def __init__(self, tmp_path: Path):
        (tmp_path / "fixture_system_app.py").write_text(APP)
        self.watch = tmp_path / "watch"
        self.watch.mkdir()
        env = dict(os.environ)
        env.pop("GUNICORN_CMD_ARGS", None)
        env.update(
            PYTHONPATH=os.pathsep.join([str(tmp_path), str(ROOT)]),
            API_KEY_PEPPER="f" * 64,
            APP_KEY="test-only-app-key-" + "0" * 32,
            DATABASE_URL=f"sqlite:///{tmp_path}/openalgo-test.db",
            LOG_DIR=str(tmp_path / "log"),
            SUDARSHAN_RISK_DB=str(tmp_path / "risk.db"),
            SUDARSHAN_WATCH_STATE_DIR=str(self.watch),
            SUDARSHAN_BACKUP_STATE_DIR=str(tmp_path / "backup"),
        )
        self.port = _free_port()
        self.log = tmp_path / "gunicorn.log"
        self.logfile = self.log.open("w")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "gunicorn", "--worker-class", "gthread", "--threads", "16",
             "--workers", "1", "--bind", f"127.0.0.1:{self.port}", "--no-control-socket",
             "--log-level", "warning", "fixture_system_app:app"],  # fmt: skip
            cwd=tmp_path, env=env, stdout=self.logfile, stderr=subprocess.STDOUT,
        )  # fmt: skip
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                if self.request("GET", "/ping")[0] == 200:
                    return
            except OSError:
                time.sleep(0.2)
        self.process.kill()
        raise AssertionError("gunicorn did not start:\n" + self.log.read_text())

    def request(self, method, path, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.request(method, path, headers=headers or {})
        answer = conn.getresponse()
        payload = answer.read()
        cookie = (answer.getheader("Set-Cookie") or "").split(";")[0]
        conn.close()
        return answer.status, payload, cookie

    def login(self, who: str = "owner") -> dict:
        status, payload, cookie = self.request("GET", f"/login/{who}")
        assert status == 200
        return {"Cookie": cookie, "X-CSRFToken": json.loads(payload)["csrf"]}

    def write_report(self, generation: int) -> None:
        """The watch's own way: the whole document beside the file, then moved into place."""
        checks = [{"name": f"check_{n}", "level": "fine", "told": "fine", "since": None,
                   "facts": {"generation": str(generation)}} for n in range(40)]  # fmt: skip
        document = {"v": 1, "at": time.time(), "release": f"v0.0.{generation}",
                    "heartbeat": {"state": "not_set", "words": "none"}, "checks": checks}  # fmt: skip
        temp = self.watch / ".report.json.new"
        temp.write_text(json.dumps(document))
        os.replace(temp, self.watch / "report.json")

    def stop(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.logfile.close()


@pytest.fixture
def server(tmp_path):
    running = Server(tmp_path)
    yield running
    running.stop()


def at_once(count, call):
    """Run ``call(i)`` on ``count`` threads released together; the results in order."""
    barrier = threading.Barrier(count)
    results = [None] * count

    def run(i):
        barrier.wait(30)
        results[i] = call(i)

    threads = [
        threading.Thread(target=run, args=(i,), name=f"system-test-{i}") for i in range(count)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    return results


def test_reads_at_once_each_get_a_whole_report_while_it_is_replaced(server):
    owner, viewer = server.login(), server.login("someone-else")
    server.write_report(0)
    stop = threading.Event()

    def rewrite():
        generation = 0
        while not stop.is_set():
            generation += 1
            server.write_report(generation)

    writer = threading.Thread(target=rewrite, name="system-test-writer")
    writer.start()
    try:
        for _ in range(5):
            answers = at_once(12, lambda i: server.request("GET", STATUS, owner))
            for status, payload, _ in answers:
                assert status == 200, payload
                watch = json.loads(payload)["data"]["watch"]
                assert watch is not None, "a report being replaced is never 'no report'"
                generations = {check["facts"]["generation"] for check in watch["checks"]}
                assert len(watch["checks"]) == 40 and len(generations) == 1, "never a torn one"
                assert watch["release"] == f"v0.0.{generations.pop()}"
            refused = server.request("GET", STATUS, viewer)
            assert refused[0] == 403 and b"checks" not in refused[1]
    finally:
        stop.set()
        writer.join(30)


def test_disconnects_sent_together_pass_the_limit_and_record_the_cutoff(server):
    owner = server.login()
    assert json.loads(server.request("GET", "/cutoff")[1]) == {"after": None}
    before = int(time.time() * 1000)
    answers = at_once(12, lambda i: server.request("POST", DISCONNECT, owner))
    codes = sorted(status for status, _, _ in answers)
    assert codes == [200] * 5 + [429] * 7, codes
    for status, payload, _ in answers:
        body = json.loads(payload)
        if status == 200:
            assert body["data"] == {"ended": True, "revoked": 0}  # no MCP tables: none to revoke
        else:
            assert body["message"] == "Too many requests. Wait a minute, then try again."
    time.sleep(1.1)  # the cutoff's one-second cache
    after = json.loads(server.request("GET", "/cutoff")[1])["after"]
    assert after is not None and after >= before
    assert server.request("POST", DISCONNECT, {"Cookie": owner["Cookie"]})[0] == 400, "no token"
