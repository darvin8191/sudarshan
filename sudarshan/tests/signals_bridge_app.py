"""A minimal OpenAlgo app for the TRINETRA page's bridge (B1), and a stand-in for sudarshan-signals.

The app has the real blueprint, CSRF, the limiter and OpenAlgo's user table (the owner check reads
it; the users are journal_bridge_app's, made the same way). The service is a fake on 127.0.0.1 in
a thread: it opens every request with the real key for ``SIGNALS_API_INFO`` and ``open_sealed``
(60 s, once, the op in the path must be the op inside), records it, and answers as the test says.
Every name and key is a fake.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from flask import Flask
from flask_wtf.csrf import CSRFProtect, generate_csrf
from journal_bridge_app import OWNER, VIEWER, csrf_headers, login_session, reset_users

import blueprints.sudarshan_signals as bp_module
from database import user_db
from limiter import limiter
from sudarshan.autologin.handoff import HandoffRejected, NonceBook, open_sealed
from sudarshan.vault import SIGNALS_API_INFO, fernet_for

__all__ = ["OWNER", "VIEWER", "FakeSignals", "csrf_headers", "login_session", "make_app",
           "reset_users"]  # fmt: skip


def make_app(*, rate_limits: bool = False) -> Flask:
    app = Flask("sudarshan-signals-bridge-tests")
    app.secret_key = "test-only-secret-key-" + "2" * 32  # pragma: allowlist secret
    app.config.update(
        TESTING=True, WTF_CSRF_ENABLED=True, WTF_CSRF_TIME_LIMIT=None, RATELIMIT_ENABLED=rate_limits
    )
    app.csrf = CSRFProtect(app)
    limiter.init_app(app)
    if rate_limits:
        limiter.reset()
    app.register_blueprint(bp_module.sudarshan_signals_bp)

    @app.route("/csrf-token-for-tests")
    def csrf_token_for_tests():
        return {"csrf_token": generate_csrf()}

    @app.teardown_appcontext
    def remove_sessions(exception=None):
        user_db.db_session.remove()

    return app


class FakeSignals:
    """sudarshan-signals' local API as the bridge meets it, on 127.0.0.1:<free port>.

    ``POST /v1/<op>``: the body must open under ``SIGNALS_API_INFO`` for that op with the owner's
    principal (else 403, as the service answers). Each message is kept in ``calls`` as ``(op,
    args)`` and answered from ``answers[op]`` (a ``(status, body)`` pair, or ``"garbage"`` for a
    body that is not the envelope); without one an op echoes ``{"op", "args"}`` as its data.
    ``GET /health``: ``health``, a ``(status, body)`` pair.
    """

    def __init__(self):
        self.fernet = fernet_for(os.environ["API_KEY_PEPPER"], SIGNALS_API_INFO)
        self.nonces = NonceBook(1024)
        self.answers: dict = {}
        self.calls: list[tuple[str, dict]] = []
        self.refused: list[str] = []
        self.delay_s = 0.0
        self.health = (200, {"service": "sudarshan-signals", "signals": "on"})
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _reply(self, status: int, data: bytes) -> None:
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def do_GET(self):
                status, body = fake.health if self.path == "/health" else (404, {})
                self._reply(status, json.dumps(body).encode())

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if fake.delay_s:
                    threading.Event().wait(fake.delay_s)
                status, answer = fake.answer(self.path, body)
                data = answer if isinstance(answer, bytes) else json.dumps(answer).encode()
                self._reply(status, data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05},
            name="signals-api-test", daemon=True,
        )  # fmt: skip

    def answer(self, path: str, body: bytes) -> tuple[int, dict | bytes]:
        op = path.removeprefix("/v1/")
        try:
            message = open_sealed(self.fernet, body, op=op, seen=self.nonces,
                                  max_bytes=bp_module.API_MAX_BYTES)  # fmt: skip
        except HandoffRejected as exc:
            self.refused.append(str(exc))
            return 403, {"status": "error", "code": "forbidden", "message": "Forbidden."}
        if message.get("principal") != "owner":
            return 403, {"status": "error", "code": "principal", "message": "Owner only."}
        args = message.get("args") or {}
        self.calls.append((op, args))
        answer = self.answers.get(op)
        if answer == "garbage":
            return 200, b"<html>not the envelope</html>"
        if answer is not None:
            return answer
        return 200, {"status": "success", "data": {"op": op, "args": args}}

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self) -> FakeSignals:
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
