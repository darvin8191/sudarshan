"""A minimal OpenAlgo app for the family bridge (B1), and a stand-in for the family service's admin door.

The app has the real blueprint, CSRF, the limiter and OpenAlgo's user and watchlist tables (the
owner check and the internal route read them). sudarshan-family is built by another lane at the
same time, so its admin door here is a fake on 127.0.0.1 in a thread: it opens every request with
the real key for ``FAMILY_ADMIN_INFO`` and ``open_sealed`` (60 s, once, the op in the path must be
the op inside), checks the owner's principal, records the op's fields and answers as the test
says. Every name and key is a fake; no family member is named anywhere.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytz
from flask import Flask
from flask_wtf.csrf import CSRFProtect, generate_csrf

import blueprints.sudarshan_family as bp_module
from database import user_db, watchlist_db
from limiter import limiter
from sudarshan.autologin.handoff import HandoffRejected, NonceBook, open_sealed, seal
from sudarshan.family import contract
from sudarshan.vault import FAMILY_ADMIN_INFO, FAMILY_INTERNAL_INFO, fernet_for

OWNER = "owner"
VIEWER = "not-the-owner"
OWNER_PASSWORD = "Fake-Pass-123!"  # pragma: allowlist secret
#: What a family phone sends OpenAlgo: the family cookie and nothing else.
FAMILY_COOKIE = f"{contract.COOKIE}={secrets.token_urlsafe(32)}"
#: The service's own sentence for a seal it could not open (sudarshan/family/words.py).
SEAL_WORDS = (
    "The family service could not confirm that this request came from this server. If the "
    "server's keys were changed, the next deploy run makes the family's keys again."
)


def make_app(*, rate_limits: bool = False) -> Flask:
    app = Flask("sudarshan-family-bridge-tests")
    app.secret_key = "test-only-secret-key-" + "2" * 32  # pragma: allowlist secret
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
    app.register_blueprint(bp_module.sudarshan_family_bp)

    @app.route("/csrf-token-for-tests")
    def csrf_token_for_tests():
        return {"csrf_token": generate_csrf()}

    @app.teardown_appcontext
    def remove_sessions(exception=None):
        user_db.db_session.remove()
        watchlist_db.db_session.remove()

    return app


def reset_users() -> None:
    user_db.Base.metadata.create_all(user_db.engine)
    user_db.User.query.delete()
    user_db.db_session.commit()
    user_db.db_session.remove()
    user_db.add_user(OWNER, "owner@example.invalid", OWNER_PASSWORD, is_admin=True)
    user_db.add_user(VIEWER, "viewer@example.invalid", OWNER_PASSWORD, is_admin=False)
    user_db.db_session.remove()


def reset_lists() -> dict[str, int]:
    """Three synthetic lists, two of the owner's and one of another account's: their ids."""
    watchlist_db.Base.metadata.create_all(watchlist_db.engine)
    watchlist_db.db_session.query(watchlist_db.WatchlistItem).delete()
    watchlist_db.db_session.query(watchlist_db.Watchlist).delete()
    watchlist_db.db_session.commit()
    watchlist_db.db_session.remove()
    made = {
        "swing": watchlist_db.create_watchlist(OWNER, "Swing", items=[
            {"symbol": "INFY", "exchange": "NSE"},
            {"symbol": "NIFTY26NOV24000CE", "exchange": "NFO"},
            {"symbol": "NOTLISTED", "exchange": "NSE"},
        ]),
        "empty": watchlist_db.create_watchlist(OWNER, "Empty", items=[]),
        "other": watchlist_db.create_watchlist(VIEWER, "Not the owner's", items=[
            {"symbol": "SBIN", "exchange": "NSE"},
        ]),
    }  # fmt: skip
    watchlist_db.db_session.remove()
    return {name: row["id"] for name, row in made.items()}


def login_session(client, username: str = OWNER) -> None:
    """A signed session for ``username``: for anyone else, a session forged with the app's key."""
    now_ist = datetime.now(pytz.timezone("Asia/Kolkata"))
    with client.session_transaction() as session:
        session["user"] = username
        session["logged_in"] = True
        session["login_time"] = now_ist.isoformat()


def csrf_headers(client) -> dict:
    token = client.get("/csrf-token-for-tests").get_json()["csrf_token"]
    return {"X-CSRFToken": token}


def internal_seal(pepper: str | None = None, info: bytes = FAMILY_INTERNAL_INFO, **fields) -> bytes:
    """A request to the internal route as the family service seals it (the fields over the
    defaults: op ``watchlists``, principal ``sudarshan-family``)."""
    fernet = fernet_for(pepper or os.environ["API_KEY_PEPPER"], info)
    fields = {"principal": bp_module.INTERNAL_PRINCIPAL, **fields}
    return seal(fernet, fields.pop("op", bp_module.INTERNAL_OP), **fields)


class FakeFamily:
    """sudarshan-family's admin door as the bridge meets it, on 127.0.0.1:<free port>.

    ``POST /v1/admin/<op>``: the body must open under ``FAMILY_ADMIN_INFO`` for that op and carry
    the owner's principal, else 403 ``forbidden`` in the service's words. Each message is kept in
    ``calls`` as ``(op, fields)`` (the seal less op, nonce and principal) with its raw body in
    ``bodies``, and answered from ``answers[op]``: a ``(status, body)`` pair, a callable taking
    the message and returning one, or raw bytes to send as they are. Without an answer an op
    echoes ``{"op", "fields"}`` as its data. ``delay`` holds every answer back that long.
    """

    def __init__(self, pepper: str | None = None):
        self.fernet = fernet_for(pepper or os.environ["API_KEY_PEPPER"], FAMILY_ADMIN_INFO)
        self.nonces = NonceBook(4096)
        self.answers: dict = {}
        self.calls: list[tuple[str, dict]] = []
        self.paths: list[str] = []
        self.bodies: list[bytes] = []
        self.refused: list[str] = []
        self.delay = 0.0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # quiet
                pass

            def _send(self, status: int, data: bytes) -> None:
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(data)))
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # the bridge stopped waiting (a timeout test)

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                status, answer = fake.answer(self.path, body)
                if fake.delay:
                    threading.Event().wait(fake.delay)
                self._send(
                    status, answer if isinstance(answer, bytes) else json.dumps(answer).encode()
                )

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.05},
            name="family-admin-test", daemon=True,
        )  # fmt: skip

    def answer(self, path: str, body: bytes) -> tuple[int, dict | bytes]:
        self.paths.append(path)
        self.bodies.append(body)
        op = path.removeprefix(contract.ADMIN_PREFIX)
        if not path.startswith(contract.ADMIN_PREFIX) or op not in contract.ADMIN_OP_NAMES:
            return 404, {"status": "error", "message": "not found"}
        try:
            message = open_sealed(self.fernet, body, op=op, seen=self.nonces,
                                  max_bytes=contract.LIMITS["admin_bytes"])  # fmt: skip
        except HandoffRejected as exc:
            self.refused.append(str(exc))
            return 403, {"status": "error", "code": "forbidden", "message": SEAL_WORDS}
        if message.get("principal") != contract.ADMIN_PRINCIPAL:
            self.refused.append("principal")
            return 403, {"status": "error", "code": "forbidden", "message": SEAL_WORDS}
        fields = {k: v for k, v in message.items() if k not in ("op", "nonce", "principal")}
        self.calls.append((op, fields))
        answer = self.answers.get(op)
        if callable(answer):
            return answer(message)
        if answer is not None:
            return answer
        return 200, {"status": "success", "data": {"op": op, "fields": fields}}

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def __enter__(self) -> FakeFamily:
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
