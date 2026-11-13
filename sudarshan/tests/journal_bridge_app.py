"""A minimal OpenAlgo app for the journal bridge (JC1), and a stand-in for the journal's local API.

The app has the real blueprint, CSRF, the limiter and OpenAlgo's user table (the owner check reads
it). sudarshan-journal is built by another lane at the same time, so the service here is a fake
on 127.0.0.1 in a thread: it opens every request with the real key for ``JOURNAL_API_INFO`` and
``open_sealed`` (60 s, once, the op in the path must be the op inside), records it, and answers
as the test says. Every name and key is a fake.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytz
from flask import Flask
from flask_wtf.csrf import CSRFProtect, generate_csrf

import blueprints.sudarshan_journal as bp_module
from database import user_db
from limiter import limiter
from sudarshan.autologin.handoff import HandoffRejected, NonceBook, open_sealed
from sudarshan.vault import JOURNAL_API_INFO, fernet_for

OWNER = "owner"
VIEWER = "family-viewer"
OWNER_PASSWORD = "Fake-Pass-123!"  # pragma: allowlist secret


def make_app(*, rate_limits: bool = False) -> Flask:
    app = Flask("sudarshan-journal-bridge-tests")
    app.secret_key = "test-only-secret-key-" + "1" * 32  # pragma: allowlist secret
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=True,
        WTF_CSRF_TIME_LIMIT=None,
        RATELIMIT_ENABLED=rate_limits,
    )
    app.csrf = CSRFProtect(app)
    limiter.init_app(app)
    if rate_limits:
        limiter.reset()
    app.register_blueprint(bp_module.sudarshan_journal_bp)

    @app.route("/csrf-token-for-tests")
    def csrf_token_for_tests():
        return {"csrf_token": generate_csrf()}

    @app.teardown_appcontext
    def remove_sessions(exception=None):
        user_db.db_session.remove()

    return app


def reset_users() -> None:
    user_db.Base.metadata.create_all(user_db.engine)
    user_db.User.query.delete()
    user_db.db_session.commit()
    user_db.db_session.remove()
    user_db.add_user(OWNER, "owner@example.invalid", OWNER_PASSWORD, is_admin=True)
    user_db.add_user(VIEWER, "viewer@example.invalid", OWNER_PASSWORD, is_admin=False)
    user_db.db_session.remove()


def login_session(client, username: str = OWNER) -> None:
    """A signed session for ``username``: for the viewer, a session forged with the app's key."""
    now_ist = datetime.now(pytz.timezone("Asia/Kolkata"))
    with client.session_transaction() as session:
        session["user"] = username
        session["logged_in"] = True
        session["login_time"] = now_ist.isoformat()


def csrf_headers(client) -> dict:
    token = client.get("/csrf-token-for-tests").get_json()["csrf_token"]
    return {"X-CSRFToken": token}


class FakeJournal:
    """sudarshan-journal's local API as the bridge meets it, on 127.0.0.1:<free port>.

    ``POST /v1/<op>``: the body must open under ``JOURNAL_API_INFO`` for that op and carry the
    owner's principal (else 403, as the service answers). Each message is kept in ``calls`` as
    ``(op, args)`` with its raw body in ``bodies``, and answered from ``answers[op]``: a
    ``(status, body)`` pair or a callable taking the message and returning one. Without an answer
    an op echoes ``{"op", "args"}`` as its data. An op in ``missing`` answers 501 ``not_ready``,
    one in ``unknown`` the API's bare 404 for a path it has no route for.
    ``GET /health``: ``health``, a ``(status, body)`` pair.
    """

    def __init__(self, pepper: str | None = None):
        self.fernet = fernet_for(pepper or os.environ["API_KEY_PEPPER"], JOURNAL_API_INFO)
        self.nonces = NonceBook(1024)
        self.answers: dict = {}
        self.missing: set[str] = set()
        self.unknown: set[str] = set()
        self.calls: list[tuple[str, dict]] = []
        self.bodies: list[bytes] = []
        self.refused: list[str] = []
        self.health = (200, {"service": "sudarshan-journal", "journal_state": {"enabled": True}})
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # quiet
                pass

            def _reply(self, status: int, body: dict) -> None:
                data = json.dumps(body).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the bridge stopped waiting (a timeout test)

            def do_GET(self):
                if self.path == "/health":
                    self._reply(*fake.health)
                else:
                    self._reply(
                        404, {"status": "error", "code": "not_found", "message": "not found"}
                    )

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self._reply(*fake.answer(self.path, body))

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05},
            name="journal-api-test", daemon=True,
        )  # fmt: skip

    def answer(self, path: str, body: bytes) -> tuple[int, dict]:
        op = path.removeprefix("/v1/")
        self.bodies.append(body)
        if not path.startswith("/v1/") or op in self.unknown:
            return 404, {"status": "error", "code": "not_found", "message": "not found"}
        try:
            message = open_sealed(
                self.fernet, body, op=op, seen=self.nonces, max_bytes=bp_module.API_MAX_BYTES
            )
        except HandoffRejected as exc:
            self.refused.append(str(exc))
            return 403, {"status": "error", "code": "forbidden", "message": "Forbidden."}
        if message.get("principal") != "owner":
            return 403, {"status": "error", "code": "principal", "message": "Owner only."}
        args = message.get("args") or {}
        self.calls.append((op, args))
        if op in self.missing:
            return 501, {
                "status": "error", "code": "not_ready",
                "message": "This part of the journal is not ready yet.",
            }  # fmt: skip
        answer = self.answers.get(op)
        if callable(answer):
            return answer(message)
        if answer is not None:
            return answer
        return 200, {"status": "success", "data": {"op": op, "args": args}}

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self) -> FakeJournal:
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
