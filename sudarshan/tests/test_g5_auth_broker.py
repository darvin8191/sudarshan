"""G5: POST /auth/broker answers as GET does instead of failing.

The view (blueprints/auth.py) answered a GET only and returned nothing for a POST, which Flask
raised as an error, so every POST, from anyone, came back as the error page. Nothing posts there
since the broker page moved to React; a POST now gets the GET's answer: the login page without a
session, the broker page for a login waiting for its broker step, the dashboard once logged in.
"""

from __future__ import annotations

from datetime import datetime

import pytest
import pytz
from flask import Flask

import blueprints.auth as auth_module
import database.auth_db as auth_db
from limiter import limiter

OWNER = "owner"


@pytest.fixture
def app():
    app = Flask("sudarshan-g5-auth-broker-tests")
    app.secret_key = "test-only-secret-key-" + "0" * 32  # pragma: allowlist secret
    app.config.update(TESTING=True, RATELIMIT_ENABLED=False)
    limiter.init_app(app)
    app.register_blueprint(auth_module.auth_bp)
    return app


def browser(app, who: str):
    client = app.test_client()
    if who != "none":
        with client.session_transaction() as session:
            session["user"] = OWNER
            if who == "logged in":
                session["logged_in"] = True
                session["login_time"] = datetime.now(pytz.timezone("Asia/Kolkata")).isoformat()
    return client


@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize(
    ("who", "goes_to"),
    [("none", "/login"), ("password step only", "/broker"), ("logged in", "/dashboard")],
)
def test_a_post_gets_the_answer_a_get_gets(app, monkeypatch, method, who, goes_to):
    """Before G5 every POST raised (the view returned None); the GETs pass on both."""
    monkeypatch.setattr(auth_db, "get_auth_token", lambda user: "fake-token" if user == OWNER else None)
    answer = browser(app, who).open("/auth/broker", method=method)
    assert (answer.status_code, answer.headers["Location"]) == (302, goes_to)
