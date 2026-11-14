"""G5 (observation 5): the Telegram webhook refuses a call it cannot verify.

/api/v1/telegram/webhook checks the secret header Telegram sends, worked out from
TELEGRAM_WEBHOOK_SECRET or the bot token. With neither set it skipped the check and answered every
POST as though it came from Telegram, against its own get_webhook_secret, which says such a call
must be refused. Now it answers 403. With a secret, a call is checked as before.
"""

from __future__ import annotations

import hashlib

import pytest
from flask import Flask

from limiter import limiter

UPDATE = {"update_id": 1, "message": {"text": "/start"}}
SECRET = "g5-fake-webhook-secret"  # pragma: allowlist secret
BOT_TOKEN = "123456:g5-fake-bot-token"  # pragma: allowlist secret
HEADER = "X-Telegram-Bot-Api-Secret-Token"


@pytest.fixture(scope="module", autouse=True)
def _stop_the_health_monitor():
    """Importing the REST API starts the market data health monitor; no test here needs it."""
    yield
    from services.market_data_service import get_market_data_service

    get_market_data_service().health_monitor.stop()


@pytest.fixture
def webhook(monkeypatch):
    """POST an update to the real route, with the bot's stored settings and the environment the
    test gives; the answer's status."""
    import restx_api.telegram_bot as telegram
    from restx_api import api_v1_bp

    app = Flask("sudarshan-g5-telegram-webhook-tests")
    app.config.update(TESTING=True, RATELIMIT_ENABLED=False)
    limiter.init_app(app)
    app.register_blueprint(api_v1_bp)
    client = app.test_client()

    def post(*, secret=None, bot=None, header=None) -> int:
        if secret is None:
            monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
        else:
            monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", secret)
        monkeypatch.setattr(telegram, "get_bot_config", lambda: bot)
        headers = {HEADER: header} if header is not None else {}
        return client.post("/api/v1/telegram/webhook", json=UPDATE, headers=headers).status_code

    return post


@pytest.mark.parametrize("stored", [{"bot_token": None}, {}], ids=["no bot token", "unreadable"])
@pytest.mark.parametrize("header", [None, "anything"], ids=["no header", "a header"])
def test_a_call_that_cannot_be_verified_is_refused(webhook, stored, header):
    """Before G5 each of these answered 200, as though Telegram had sent it. (The bot settings
    read as {} when the database cannot be read.)"""
    assert webhook(secret=None, bot=stored, header=header) == 403


def test_a_call_is_checked_against_the_secret_as_before(webhook):
    """Unchanged by G5, so these pass on the code before it too."""
    derived = hashlib.sha256(BOT_TOKEN.encode()).hexdigest()[:32]
    assert webhook(secret=SECRET, bot={"bot_token": None}) == 401
    assert webhook(secret=SECRET, bot={"bot_token": None}, header="wrong") == 403
    assert webhook(secret=SECRET, bot={"bot_token": None}, header=SECRET) == 200
    assert webhook(secret=None, bot={"bot_token": BOT_TOKEN}) == 401
    assert webhook(secret=None, bot={"bot_token": BOT_TOKEN}, header=derived) == 200
