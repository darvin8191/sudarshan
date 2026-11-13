"""The family view's bridge under a real gunicorn gthread worker (SUDARSHAN P6 B1).

The owner bridge and the internal route run in OpenAlgo's request path with state shared across
requests: the seal keys (built once, lazily), the nonce book of the internal route, the shared
limiter and the shared httpx client. Under eventlet a check-then-act on any of them was atomic by
accident; under gthread the threads are real. So they are run where they are real: a tiny Flask
app with the real blueprint, CSRF and the shared limiter, requests released by a barrier at once.
The session checks are replaced by a cookie the app sets itself (OpenAlgo's own checks are tested
in-process beside the bridge), and the family account's uid is this process's own; everything
after them is the production code, talking to a stand-in admin door that opens every seal.

Held: twelve cold owner reads at once each answer for themselves and each reaches the service
once; one sealed internal request sent twelve times at once is taken exactly once; twelve
different internal requests at once each read their own lists from a real database; and the write
limit lets exactly its count through when the writes arrive together.

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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or importlib.util.find_spec("gunicorn") is None,
    reason="needs gunicorn on Linux (the production server)",
)

PEPPER = "f" * 64  # fake, test only

APP = textwrap.dedent(
    """
    import os
    from types import SimpleNamespace

    from flask import Flask, jsonify, session
    from flask_wtf.csrf import CSRFProtect, generate_csrf

    import blueprints.sudarshan_family as bridge
    from database import watchlist_db
    from limiter import limiter

    app = Flask(__name__)
    app.secret_key = "test-only-secret-key-" + "0" * 32  # pragma: allowlist secret
    app.config.update(WTF_CSRF_ENABLED=True, WTF_CSRF_TIME_LIMIT=None)
    app.csrf = CSRFProtect(app)
    limiter.init_app(app)
    bridge.is_session_valid = lambda: bool(session.get("in"))
    bridge.is_owner_session = lambda: session.get("in") == "owner"
    bridge._family_uid = os.getuid  # the stand-in service runs as this test's own account
    bridge.find_user_by_username = lambda: SimpleNamespace(username="owner")
    bridge.get_symbol_info = lambda symbol, exchange: SimpleNamespace(
        token=f"NSE_EQ|{symbol}", name=symbol.title(), tick_size=0.05, lotsize=1)
    app.register_blueprint(bridge.sudarshan_family_bp)

    watchlist_db.Base.metadata.create_all(watchlist_db.engine)
    if not watchlist_db.get_watchlists("owner"):
        for i in range(12):
            watchlist_db.create_watchlist("owner", f"List {i}",
                                          items=[{"symbol": f"SYM{i}", "exchange": "NSE"}])
    watchlist_db.db_session.remove()

    @app.teardown_appcontext
    def release(exception=None):
        from utils.db_sessions import remove_all_scoped_sessions

        remove_all_scoped_sessions()

    @app.route("/ping")
    def ping():
        return "ok"

    @app.route("/lists")
    def lists():
        found = {row["name"]: row["id"] for row in watchlist_db.get_watchlists("owner")}
        return jsonify(found)

    @app.route("/login")
    def login():
        session["in"] = "owner"
        session["user"] = "owner"
        return jsonify(csrf=generate_csrf())
    """
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class FamilyDoor:
    """The family service's admin door: opens each seal with the real key, counts each op once."""

    def __init__(self):
        from cryptography.fernet import InvalidToken

        from sudarshan.vault import FAMILY_ADMIN_INFO, fernet_for

        fernet = fernet_for(PEPPER, FAMILY_ADMIN_INFO)
        self.calls: list[tuple[str, dict]] = []
        self.lock = threading.Lock()
        door = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                op = self.path.removeprefix("/v1/admin/")
                try:
                    message = json.loads(fernet.decrypt(body, ttl=60))
                    ok = message.get("op") == op and message.get("principal") == "owner"
                except (InvalidToken, ValueError):
                    ok = False
                if ok:
                    fields = {k: v for k, v in message.items()
                              if k not in ("op", "nonce", "principal")}  # fmt: skip
                    with door.lock:
                        door.calls.append((op, fields))
                    time.sleep(0.05)  # long enough for the other threads to be in flight
                    answer = {"status": "success", "data": {"op": op, "fields": fields}}
                    status = 200
                else:
                    answer = {"status": "error", "code": "forbidden", "message": "Refused."}
                    status = 403
                data = json.dumps(answer).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       name="family-door-test", daemon=True)  # fmt: skip
        self.thread.start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)


class Server:
    def __init__(self, tmp_path: Path, door: FamilyDoor):
        (tmp_path / "fixture_family_app.py").write_text(APP)
        env = dict(os.environ)
        env.pop("GUNICORN_CMD_ARGS", None)
        env.update(
            PYTHONPATH=os.pathsep.join([str(tmp_path), str(ROOT)]),
            API_KEY_PEPPER=PEPPER,
            APP_KEY="test-only-app-key-" + "0" * 32,
            DATABASE_URL=f"sqlite:///{tmp_path}/openalgo-test.db",
            LOG_DIR=str(tmp_path / "log"),
            SUDARSHAN_FAMILY_API_URL=door.url,
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
                "fixture_family_app:app",
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

    def request(self, method, path, body=None, headers=None, raw=False):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        if raw:
            data, hdrs = body, {"Content-Type": "application/octet-stream", **(headers or {})}
        else:
            data = json.dumps(body) if body is not None else None
            hdrs = {"Content-Type": "application/json", **(headers or {})}
        conn.request(method, path, body=data, headers=hdrs)
        r = conn.getresponse()
        payload = r.read()
        cookie = (r.getheader("Set-Cookie") or "").split(";")[0]
        conn.close()
        return r.status, payload, cookie

    def login(self):
        status, payload, cookie = self.request("GET", "/login")
        assert status == 200
        return {"Cookie": cookie, "X-CSRFToken": json.loads(payload)["csrf"]}

    def stop(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            self.process.kill()
        self.logfile.close()


@pytest.fixture
def door():
    running = FamilyDoor()
    yield running
    running.stop()


@pytest.fixture
def server(tmp_path, door):
    running = Server(tmp_path, door)
    yield running
    running.stop()


def at_once(count, call):
    """Run ``call(i)`` on ``count`` threads released together; the results in order."""
    barrier = threading.Barrier(count)
    results = [None] * count

    def run(i):
        barrier.wait(30)
        results[i] = call(i)

    threads = [threading.Thread(target=run, args=(i,), name=f"family-test-{i}")
               for i in range(count)]  # fmt: skip
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert all(r is not None for r in results), "a request did not finish"
    return results


def internal_blob(list_ids) -> bytes:
    from sudarshan.autologin.handoff import seal
    from sudarshan.vault import FAMILY_INTERNAL_INFO, fernet_for

    return seal(fernet_for(PEPPER, FAMILY_INTERNAL_INFO), "watchlists",
                principal="sudarshan-family", list_ids=list_ids)  # fmt: skip


def test_the_harness_sees_a_lost_update_in_a_counter_that_checks_then_acts():
    """The control: a counter read, then written back after a yield loses bumps under twelve
    threads, so the tests below can fail on a check-then-act."""
    box = {"n": 0}

    def racy(i):
        n = box["n"]
        time.sleep(0.001)
        box["n"] = n + 1
        return True

    at_once(12, racy)
    assert box["n"] < 12


def test_twelve_cold_owner_reads_at_once_each_answer_for_themselves(server, door):
    headers = server.login()  # no forward yet: the seal key and the client are still cold
    results = at_once(
        12,
        lambda i: server.request(
            "GET", f"/sudarshan/family/api/device_list?member_id={i + 1}", headers=headers
        ),
    )
    for i, (status, payload, _) in enumerate(results):
        assert status == 200, payload
        assert json.loads(payload)["data"] == {"op": "device_list", "fields": {"member_id": i + 1}}
    assert sorted(f["member_id"] for _, f in door.calls) == list(range(1, 13))  # each once


def test_one_sealed_request_sent_twelve_times_at_once_is_taken_once(server):
    _, payload, _ = server.request("GET", "/lists")
    blob = internal_blob([json.loads(payload)["List 0"]])
    results = at_once(
        12,
        lambda i: server.request("POST", "/sudarshan/family/internal/watchlists", blob, raw=True),
    )
    statuses = sorted(status for status, _, _ in results)
    assert statuses == [200] + [403] * 11, statuses


def test_twelve_internal_requests_at_once_each_read_their_own_list(server):
    _, payload, _ = server.request("GET", "/lists")
    ids = json.loads(payload)
    results = at_once(
        12,
        lambda i: server.request(
            "POST",
            "/sudarshan/family/internal/watchlists",
            internal_blob([ids[f"List {i}"]]),
            raw=True,
        ),
    )
    for i, (status, body, _) in enumerate(results):
        assert status == 200, body
        (shown,) = json.loads(body)["data"]["lists"]
        assert shown["name"] == f"List {i}"
        assert [r["symbol"] for r in shown["rows"]] == [f"SYM{i}"]


def test_the_write_limit_lets_exactly_its_count_through_when_writes_arrive_together(server, door):
    headers = server.login()
    results = at_once(
        40,
        lambda i: server.request(
            "POST", "/sudarshan/family/api/invite_revoke", {"member_id": i + 1}, headers
        ),
    )
    statuses = [status for status, _, _ in results]
    assert statuses.count(200) == 30 and statuses.count(429) == 10, statuses
    refused = [json.loads(body) for status, body, _ in results if status == 429]
    assert {(r["code"], r["status"]) for r in refused} == {("busy", "error")}
    assert len(door.calls) == 30  # a refused write never reached the service
