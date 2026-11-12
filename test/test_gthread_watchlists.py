"""The Watchlists v2 bridge under a real gunicorn gthread worker (M8.4, W4).

The bridge is the first SUDARSHAN code to sit in OpenAlgo's request path with state of its own (the
record check's cache, the shared limiter), so it is run where the threads are real: a tiny Flask app
with the real blueprint, CSRF and the shared limiter, six or more requests released by a barrier
at once. The session checks are replaced by a cookie the app sets itself (OpenAlgo's own checks are
tested in-process beside the bridge); everything after them is the production code.

Three things are held: concurrent reads each answer correctly (no request sees another's fields,
and the cold first requests that open the store all succeed), a switched-off server answers every
registered op as off and opens nothing, and the per-class limit lets no more than its count
through when the requests arrive together.

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
    from flask import Blueprint, Flask, jsonify, session
    from flask_wtf.csrf import CSRFProtect, generate_csrf

    import blueprints.sudarshan_watchlists as bridge
    from limiter import limiter
    from sudarshan.watchlists import contract, ops

    app = Flask(__name__)
    app.secret_key = "test-only-secret-key-" + "0" * 32  # pragma: allowlist secret
    app.config.update(WTF_CSRF_ENABLED=True, WTF_CSRF_TIME_LIMIT=None)
    app.csrf = CSRFProtect(app)
    limiter.init_app(app)
    bridge.is_session_valid = lambda: bool(session.get("in"))
    bridge.is_owner_session = lambda: session.get("in") == "owner"

    def echo(name):
        return lambda args: {"op": name, "args": args}

    handlers = {op.name: echo(op.name) for op in contract.OPS}
    handlers.update(ops.discover())  # the real state op where there is one
    app.register_blueprint(bridge.attach(
        Blueprint("wl_gthread", __name__, url_prefix=contract.API_PREFIX), handlers))

    @app.route("/ping")
    def ping():
        return "ok"

    @app.route("/login")
    def login():
        session["in"] = "owner"
        return jsonify(csrf=generate_csrf())
    """
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Server:
    def __init__(self, tmp_path: Path, switch: str):
        self.db = tmp_path / "wl.db"
        (tmp_path / "fixture_wl_app.py").write_text(APP)
        env = dict(os.environ)
        env.pop("GUNICORN_CMD_ARGS", None)
        env.update(
            PYTHONPATH=os.pathsep.join([str(tmp_path), str(ROOT)]),
            SUDARSHAN_WATCHLISTS_V2=switch,
            SUDARSHAN_WATCHLISTS_DB=str(self.db),
            API_KEY_PEPPER="f" * 64,  # fake, test only
            APP_KEY="test-only-app-key-" + "0" * 32,
            DATABASE_URL=f"sqlite:///{tmp_path}/unused.db",
            LOG_DIR=str(tmp_path / "log"),
        )
        self.port = _free_port()
        self.log = tmp_path / "gunicorn.log"
        self.logfile = self.log.open("w")
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "gunicorn",
                "--worker-class",
                "gthread",
                "--threads",
                "16",
                "--workers",
                "1",
                "--bind",
                f"127.0.0.1:{self.port}",
                "--no-control-socket",
                "--log-level",
                "warning",
                "fixture_wl_app:app",
            ],  # fmt: skip
            cwd=tmp_path,
            env=env,
            stdout=self.logfile,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                if self.request("GET", "/ping")[0] == 200:
                    return
            except OSError:
                time.sleep(0.2)
        self.process.kill()
        raise AssertionError("gunicorn did not start:\n" + self.log.read_text())

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        data = json.dumps(body) if body is not None else None
        hdrs = {"Content-Type": "application/json", **(headers or {})}
        conn.request(method, path, body=data, headers=hdrs)
        r = conn.getresponse()
        raw = r.read()
        cookie = (r.getheader("Set-Cookie") or "").split(";")[0]
        conn.close()
        return r.status, raw, cookie

    def login(self):
        """A signed-in owner's cookie and CSRF token."""
        status, raw, cookie = self.request("GET", "/login")
        assert status == 200
        return {"Cookie": cookie, "X-CSRFToken": json.loads(raw)["csrf"]}

    def stop(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.logfile.close()


@pytest.fixture
def server_on(tmp_path):
    server = Server(tmp_path, "on")
    yield server
    server.stop()


@pytest.fixture
def server_off(tmp_path):
    server = Server(tmp_path, "off")
    yield server
    server.stop()


def at_once(count, call):
    """Run ``call(i)`` on ``count`` threads released together; the results in order."""
    barrier = threading.Barrier(count)
    results = [None] * count

    def run(i):
        barrier.wait(30)
        results[i] = call(i)

    threads = [threading.Thread(target=run, args=(i,), name=f"wl-test-{i}") for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert all(r is not None for r in results), "a request did not finish"
    return results


def test_six_concurrent_reads_each_answer_for_themselves(server_on):
    headers = server_on.login()
    base = "/sudarshan/watchlists/api"
    states = at_once(6, lambda i: server_on.request("GET", f"{base}/state", headers=headers))
    for status, raw, _ in states:
        body = json.loads(raw)
        assert status == 200 and body["status"] == "ok"
        assert body["data"]["enabled"] is True
        assert body["data"]["limits"]["lists"] == 50
    assert server_on.db.exists(), "the first requests did not open the store"
    # another op at once, each with its own fields: no request may answer with another's
    seen = at_once(
        6,
        lambda i: server_on.request("GET", f"{base}/strikes?u=U{i}&x=NFO&n={i}", headers=headers),
    )
    for i, (status, raw, _) in enumerate(seen):
        assert status == 200
        assert json.loads(raw)["data"]["args"] == {"u": f"U{i}", "x": "NFO", "n": i}


def test_switched_off_every_registered_op_answers_off(server_off):
    from sudarshan.watchlists import contract

    headers = server_off.login()
    ops = list(contract.OPS)
    results = at_once(
        len(ops),
        lambda i: server_off.request(
            ops[i].method, "/sudarshan/watchlists/api" + ops[i].route.replace("<id>", "7")
            .replace("<k>", "k").replace("<ref>", "r"),
            body={} if ops[i].method not in ("GET", "DELETE") else None, headers=headers,
        ),
    )  # fmt: skip
    for op, (status, raw, _) in zip(ops, results, strict=True):
        body = json.loads(raw)
        if op.name == "state":
            assert (status, body) == (200, {"status": "ok", "data": {"enabled": False}})
        else:
            assert (status, body["code"]) == (503, "off"), op.name
    assert not server_off.db.exists(), "the store was opened while switched off"


def test_the_limit_of_a_class_lets_no_more_through_than_it_allows(server_on):
    from sudarshan.watchlists import contract

    headers = server_on.login()
    allowed = contract.LIMITS_PER_MIN["preview"]
    results = at_once(
        allowed * 4,
        lambda i: server_on.request(
            "POST", "/sudarshan/watchlists/api/tv/preview", body={"text": "x"}, headers=headers
        ),
    )
    codes = sorted(status for status, _, _ in results)
    assert codes.count(200) == allowed, codes
    assert codes.count(429) == len(results) - allowed
    busy = next(json.loads(raw) for status, raw, _ in results if status == 429)
    assert busy["code"] == "busy" and busy["status"] == "error"
