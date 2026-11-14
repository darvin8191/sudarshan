"""A minimal OpenAlgo app for the portfolio bridge, and the service's real local API beside it.

The app has the real blueprint, CSRF, the limiter and OpenAlgo's user table (the owner check reads
it). The service is the real ``PortfolioApi`` on 127.0.0.1 in a thread with its own event loop, as
sudarshan-portfolio runs it, answering from a registry the test controls. Every name and key is a
fake.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime

import pytz
from flask import Flask
from flask_wtf.csrf import CSRFProtect, generate_csrf

import blueprints.sudarshan_portfolio as bp_module
from database import user_db
from limiter import limiter
from sudarshan.portfolio.api import PortfolioApi
from sudarshan.portfolio.core import kinds
from sudarshan.vault import PORTFOLIO_API_INFO, PORTFOLIO_FAMILY_API_INFO, fernet_for

OWNER = "owner"
VIEWER = "family-viewer"
OWNER_PASSWORD = "Fake-Pass-123!"  # pragma: allowlist secret


def make_app(*, rate_limits: bool = False) -> Flask:
    app = Flask("sudarshan-portfolio-bridge-tests")
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
    app.register_blueprint(bp_module.sudarshan_portfolio_bp)

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
    now_ist = datetime.now(pytz.timezone("Asia/Kolkata"))
    with client.session_transaction() as session:
        session["user"] = username
        session["logged_in"] = True
        session["login_time"] = now_ist.isoformat()


def csrf_headers(client) -> dict:
    token = client.get("/csrf-token-for-tests").get_json()["csrf_token"]
    return {"X-CSRFToken": token}


class Desk:
    enabled = True

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def health(self) -> dict:
        return {"service": "sudarshan-portfolio", "portfolio_state": {"state": "running"}}


class Calls:
    """Records the ops that reached the service and answers from ``answers[op]``: data, an
    exception to raise (an ApiError is the service's own refusal), or a callable taking the
    message (it may be a coroutine function). An op with no answer is not registered at all."""

    def __init__(self, **answers):
        self.answers, self.calls = answers, []

    def registry(self, everything: bool = True) -> dict:
        book = {}
        for name in kinds.OP_NAMES:
            if everything or name in self.answers:
                book[name] = self._handler(name)
        return book

    def _handler(self, name):
        async def handler(ctx, message):
            self.calls.append((name, {"args": dict(message)}))
            answer = self.answers.get(name, {"op": name, "args": dict(message)})
            if isinstance(answer, BaseException):
                raise answer
            if callable(answer):
                answer = answer(message)
                if asyncio.iscoroutine(answer):
                    answer = await answer
            return answer

        return handler


class Service:
    """The service's PortfolioApi on 127.0.0.1:<free port>, in a thread with its own loop. With
    ``family``, it also opens the family purpose's seals, as sudarshan-portfolio does."""

    def __init__(self, desk, registry, pepper: str, *, family: bool = False):
        self.desk = desk
        self.api = PortfolioApi(
            desk,
            fernet_for(pepper, PORTFOLIO_API_INFO),
            registry=registry,
            family_fernet=fernet_for(pepper, PORTFOLIO_FAMILY_API_INFO) if family else None,
        )
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, name="portfolio-api-test", daemon=True)
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
        self.url = f"http://127.0.0.1:{self.api.port}"
        return self

    def __exit__(self, *exc) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(10)
