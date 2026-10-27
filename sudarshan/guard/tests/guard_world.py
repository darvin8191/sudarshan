"""Shared by the risk layer's tests: the services a kill switch step calls, recorded, and a
minimal OpenAlgo app carrying the real risk blueprint, CSRF, the limiter and throwaway databases.
"""

from __future__ import annotations

from datetime import datetime

import pytz
from flask import Flask
from flask_wtf.csrf import CSRFProtect, generate_csrf

from sudarshan.guard import killswitch, words

OWNER = "owner"
# Fake credentials, for the throwaway test database only.
OWNER_PASSWORD = "Fake-Pass-123!"  # pragma: allowlist secret
FAKE_API_KEY = "fake-risk-api-key-" + "0" * 46  # pragma: allowlist secret


class Account:
    """Every service a kill switch step calls, recorded in order."""

    def __init__(self, monkeypatch):
        import broker.upstox.api.order_api as order_api
        import database.oauth_db as oauth_db
        import services.cancel_all_order_service as cancel_all
        import services.cancel_gtt_order_service as cancel_gtt
        import services.close_position_service as close
        import services.gtt_orderbook_service as gtt_book
        import services.sandbox_service as sandbox
        from services.agent import settings as agent_settings

        self.calls: list[str] = []
        self.broken: set[str] = set()
        self.exit_all_status = 200
        self.agent_on = False
        self.gtts = [{"trigger_id": "GTT-1"}, {"trigger_id": "GTT-2"}]
        self.exit_all_body = {"status": "success", "message": "All Open Positions Squared Off"}
        # Upstox's order book as the kill switch reads it after cancelling, one per read (the
        # last repeats); by default both cancelled orders already show as cancelled.
        self.order_books: list[list[dict]] = []
        self.book_reads = 0
        self.reads_before_exit: int | None = None

        def record(name, answer):
            def call(*args, **kwargs):
                self.calls.append(name)
                if name == "live_exit_all":
                    self.reads_before_exit = self.book_reads
                if name in self.broken:
                    raise RuntimeError(f"{name} broke")
                if name.startswith("live") and kwargs.get("force_live") is not True:
                    raise AssertionError(f"{name} was not sent with force_live")
                return answer() if callable(answer) else answer

            return call

        cancelled = {"status": "success", "canceled_orders": ["1", "2"], "failed_cancellations": []}
        monkeypatch.setattr(
            cancel_all,
            "cancel_all_orders_with_auth",
            record("live_cancel_all", (True, cancelled, 200)),
        )
        monkeypatch.setattr(
            gtt_book,
            "get_gtt_orderbook_with_auth",
            record("gtt_book", lambda: (True, {"status": "success", "data": self.gtts}, 200)),
        )
        monkeypatch.setattr(
            cancel_gtt,
            "cancel_gtt_order_with_auth",
            record("live_cancel_gtt", (True, {"status": "success"}, 200)),
        )
        monkeypatch.setattr(
            close,
            "close_position_with_auth",
            record(
                "live_exit_all",
                lambda: (self.exit_all_status == 200, self.exit_all_body, self.exit_all_status),
            ),
        )
        monkeypatch.setattr(
            sandbox,
            "sandbox_cancel_all_orders",
            record("sandbox_cancel_all", (True, {"status": "success", "canceled_count": 1}, 200)),
        )
        monkeypatch.setattr(
            sandbox,
            "sandbox_gtt_orderbook",
            record(
                "sandbox_gtt_book",
                (True, {"status": "success", "data": [{"trigger_id": "S1"}]}, 200),
            ),
        )
        monkeypatch.setattr(
            sandbox,
            "sandbox_cancel_gtt_order",
            record("sandbox_cancel_gtt", (True, {"status": "success"}, 200)),
        )
        monkeypatch.setattr(
            sandbox,
            "sandbox_close_position",
            record(
                "sandbox_exit_all",
                (True, {"status": "success", "message": "Closed 1 positions"}, 200),
            ),
        )
        monkeypatch.setattr(oauth_db, "revoke_all_tokens", record("mcp", 3))

        def order_book(auth):
            self.book_reads += 1
            rows = (
                self.order_books[0]
                if self.order_books
                else [
                    {"order_id": "1", "status": "cancelled"},
                    {"order_id": "2", "status": "cancelled"},
                ]
            )
            if len(self.order_books) > 1:
                self.order_books.pop(0)
            return {"status": "success", "data": rows}

        monkeypatch.setattr(order_api, "get_order_book", order_book)

        def engaged(*args, **kwargs):
            return self.agent_on

        def set_agent(on):
            self.calls.append(f"agent_{'on' if on else 'off'}")
            self.agent_on = on
            return on

        monkeypatch.setattr(agent_settings, "is_kill_switch_engaged", engaged)
        monkeypatch.setattr(agent_settings, "set_kill_switch", set_agent)
        monkeypatch.setattr(killswitch, "preview_live", lambda owner, gtts=True: {
            "orders": 2, "stops": 1, "protected": 1, "positions": 1})  # fmt: skip
        monkeypatch.setattr(killswitch, "_tell", lambda owner, text: self.told(text))
        monkeypatch.setattr(killswitch, "_announce", lambda state: None)
        self.messages: list[str] = []

    def told(self, text):
        self.calls.append("telegram")
        self.messages.append(text)
        return True, words.KS_TELEGRAM_SENT, {}


def make_app(*, rate_limits: bool = False) -> Flask:
    """The real risk blueprint on an app of its own: CSRF and the limiter as OpenAlgo has them."""
    import blueprints.sudarshan_risk as risk_routes
    from database import auth_db, user_db
    from limiter import limiter

    app = Flask("sudarshan-risk-tests")
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
        limiter.reset()  # storage exists only when limits are on
    app.register_blueprint(risk_routes.sudarshan_risk_bp)

    @app.route("/csrf-token-for-tests")
    def csrf_token_for_tests():
        return {"csrf_token": generate_csrf()}

    @app.teardown_appcontext
    def remove_sessions(exception=None):
        auth_db.db_session.remove()
        user_db.db_session.remove()

    return app


def reset_openalgo_db() -> None:
    """Empty users and keys in whichever throwaway database OpenAlgo's modules are bound to."""
    from database import auth_db, user_db

    auth_db.Base.metadata.create_all(auth_db.engine)
    user_db.Base.metadata.create_all(user_db.engine)
    for model in (auth_db.Auth, auth_db.ApiKeys):
        model.query.delete()
    auth_db.db_session.commit()
    user_db.User.query.delete()
    user_db.db_session.commit()
    for cache in (
        auth_db.auth_cache,
        auth_db.feed_token_cache,
        auth_db.broker_cache,
        auth_db.verified_api_key_cache,
        auth_db.invalid_api_key_cache,
    ):
        cache.clear()
    auth_db.db_session.remove()
    user_db.db_session.remove()


def add_owner(*, api_key: bool = False) -> None:
    from database import auth_db, user_db

    user_db.add_user(OWNER, "owner@example.invalid", OWNER_PASSWORD, is_admin=True)
    user_db.db_session.remove()
    if api_key:
        auth_db.upsert_api_key(OWNER, FAKE_API_KEY)
        auth_db.db_session.remove()


def login_session(client, username: str = OWNER, broker: str | None = None) -> None:
    now_ist = datetime.now(pytz.timezone("Asia/Kolkata"))
    with client.session_transaction() as session:
        session["user"] = username
        session["logged_in"] = True
        session["login_time"] = now_ist.isoformat()
        if broker:
            session["broker"] = broker


def csrf_headers(client) -> dict:
    token = client.get("/csrf-token-for-tests").get_json()["csrf_token"]
    return {"X-CSRFToken": token}
