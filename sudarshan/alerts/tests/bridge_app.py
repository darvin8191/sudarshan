"""A minimal OpenAlgo app for the market alerts bridge, and the service's local API beside it.

The app has the real blueprint, CSRF, the limiter and OpenAlgo's user table (the owner check reads
it). The service is the real ``MarketApi`` on 127.0.0.1 in a thread with its own event loop, as
sudarshan-alerts runs it, answering from a desk the test controls. Every name and key is a fake.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime

import pytz
from flask import Flask
from flask_wtf.csrf import CSRFProtect, generate_csrf

import blueprints.sudarshan_alerts as bp_module
from database import user_db
from limiter import limiter
from sudarshan.alerts.market.api import MarketApi
from sudarshan.vault import ALERTS_API_INFO, fernet_for

OWNER = "owner"
VIEWER = "family-viewer"
OWNER_PASSWORD = "Fake-Pass-123!"  # pragma: allowlist secret


def make_app(*, rate_limits: bool = False) -> Flask:
    app = Flask("sudarshan-alerts-bridge-tests")
    app.secret_key = "test-only-secret-key-" + "0" * 32  # pragma: allowlist secret
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
    app.register_blueprint(bp_module.sudarshan_alerts_bp)

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
    user_db.db_session.remove()


def login_session(client, username: str = OWNER) -> None:
    now_ist = datetime.now(pytz.timezone("Asia/Kolkata"))
    with client.session_transaction() as session:
        session["user"] = username
        session["logged_in"] = True
        session["login_time"] = now_ist.isoformat()


def csrf_headers(client) -> dict:
    token = client.get("/csrf-token-for-tests").get_json()["csrf_token"]
    return {"X-CSRFToken": token}


class Desk:
    """Records the ops that reached the service; answers ``answers[op]``: data, or an exception
    to raise (an ApiError is the service's own refusal), or a callable taking the message."""

    def __init__(self, **answers):
        self.answers, self.calls = answers, []
        self.on = True

    def health(self) -> dict:
        return {"service": "sudarshan-alerts", "market_alerts": "on" if self.on else "off",
                "state": "running", "alerts_active": 0, "instruments": 0}  # fmt: skip

    async def call(self, op: str, message) -> object:
        self.calls.append((op, dict(message)))
        answer = self.answers.get(op, {"op": op})
        if isinstance(answer, BaseException):
            raise answer
        if callable(answer):
            answer = answer(message)
            if asyncio.iscoroutine(answer):
                answer = await answer
        return answer


class Service:
    """The service's MarketApi on 127.0.0.1:<free port>, in a thread with its own loop."""

    def __init__(self, desk, pepper: str):
        self.desk = desk
        self.api = MarketApi(desk, fernet_for(pepper, ALERTS_API_INFO))
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, name="alerts-api-test", daemon=True)
        self._ready = threading.Event()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self.api.start(0))
        self._ready.set()
        self.loop.run_forever()
        pending = asyncio.all_tasks(self.loop)  # a request still in flight, as at a stop
        for task in pending:
            task.cancel()
        self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        self.loop.run_until_complete(self.api.close())
        self.loop.close()

    def __enter__(self) -> Service:
        self.thread.start()
        assert self._ready.wait(10), "the test service did not start"
        self.url = f"http://127.0.0.1:{self.api.server.sockets[0].getsockname()[1]}"
        return self

    def __exit__(self, *exc) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(10)
