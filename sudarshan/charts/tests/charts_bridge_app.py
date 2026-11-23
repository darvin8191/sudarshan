"""A minimal OpenAlgo app for the chart documents' bridge: the real blueprint and its checks,
CSRF, the limiter and OpenAlgo's user table (the owner check reads it). Every name and key is a
fake."""

from __future__ import annotations

from datetime import datetime

import pytz
from flask import Flask
from flask_wtf.csrf import CSRFProtect, generate_csrf

import blueprints.sudarshan_charts as bp_module
from database import user_db
from limiter import limiter
from sudarshan.tests.session_clock import login_moment

OWNER = "owner"
VIEWER = "family-viewer"
PASSWORD = "Fake-Pass-123!"  # pragma: allowlist secret
API = "/sudarshan/charts/api"
#: Every route, with the method it answers.
ROUTES = [("GET", f"{API}/state"), ("GET", f"{API}/doc"), ("PUT", f"{API}/doc")]


def make_app(*, rate_limits: bool = False) -> Flask:
    app = Flask("sudarshan-charts-bridge-tests")
    app.secret_key = "test-only-secret-key-" + "4" * 32  # pragma: allowlist secret
    app.config.update(
        TESTING=True, WTF_CSRF_ENABLED=True, WTF_CSRF_TIME_LIMIT=None, RATELIMIT_ENABLED=rate_limits
    )
    app.csrf = CSRFProtect(app)
    limiter.init_app(app)
    if rate_limits:
        limiter.reset()
    app.register_blueprint(bp_module.sudarshan_charts_bp)

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
    now_ist = login_moment()
    with client.session_transaction() as session:
        session["user"] = username
        session["logged_in"] = True
        session["login_time"] = now_ist.isoformat()


def csrf_headers(client) -> dict:
    return {"X-CSRFToken": client.get("/csrf-token-for-tests").get_json()["csrf_token"]}
