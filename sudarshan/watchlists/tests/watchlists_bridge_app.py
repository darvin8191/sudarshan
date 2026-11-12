"""A minimal OpenAlgo app for the watchlists bridge: the real checks, CSRF, the limiter and
OpenAlgo's user table (the owner check reads it). Every name and key is a fake."""

from __future__ import annotations

from datetime import datetime

import pytz
from flask import Blueprint, Flask
from flask_wtf.csrf import CSRFProtect, generate_csrf

import blueprints.sudarshan_watchlists as bp_module
from database import user_db
from limiter import limiter
from sudarshan.watchlists import contract

OWNER = "owner"
VIEWER = "family-viewer"
PASSWORD = "Fake-Pass-123!"  # pragma: allowlist secret


def echo_handlers(calls: list | None = None) -> dict:
    """A handler for every op of the contract: it records the call and echoes its request."""

    def make(name):
        def handler(args):
            if calls is not None:
                calls.append((name, args))
            return {"op": name, "args": args}

        return handler

    return {op.name: make(op.name) for op in contract.OPS}


def make_app(handlers: dict, *, rate_limits: bool = False, real: bool = False) -> Flask:
    app = Flask("sudarshan-watchlists-bridge-tests")
    app.secret_key = "test-only-secret-key-" + "0" * 32  # pragma: allowlist secret
    app.config.update(
        TESTING=True, WTF_CSRF_ENABLED=True, WTF_CSRF_TIME_LIMIT=None, RATELIMIT_ENABLED=rate_limits
    )
    app.csrf = CSRFProtect(app)
    limiter.init_app(app)
    if rate_limits:
        limiter.reset()
    if real:  # the module's own blueprint, with the handlers found on disk
        app.register_blueprint(bp_module.sudarshan_watchlists_bp)
    else:
        app.register_blueprint(bp_module.attach(
            Blueprint("wl_test", __name__, url_prefix=contract.API_PREFIX), handlers))  # fmt: skip

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
    user_db.add_user(OWNER, "owner@example.invalid", PASSWORD, is_admin=True)
    user_db.add_user(VIEWER, "viewer@example.invalid", PASSWORD, is_admin=False)
    user_db.db_session.remove()


def login_session(client, username: str = OWNER) -> None:
    now_ist = datetime.now(pytz.timezone("Asia/Kolkata"))
    with client.session_transaction() as session:
        session["user"] = username
        session["logged_in"] = True
        session["login_time"] = now_ist.isoformat()


def csrf_headers(client) -> dict:
    return {"X-CSRFToken": client.get("/csrf-token-for-tests").get_json()["csrf_token"]}


def path_of(op, ident="7") -> str:
    return contract.API_PREFIX + op.route.replace("<id>", ident).replace("<k>", ident).replace(
        "<ref>", ident
    )
