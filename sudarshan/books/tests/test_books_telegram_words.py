"""What Telegram says about Exit all and Cancel all (M6.9, sudarshan/books/telegram_words.py).

The bot's /closeall reply comes through the openalgo SDK, which wraps every answer that is not a
200 as "HTTP <code>: <body>"; the trader reads the app's own sentence, never that. The order alert
gives a failed or partial Exit all, and a Cancel all that did not cancel everything, titles of
their own; "Position Closed" and "All Orders Cancelled" stay for what did.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from sudarshan.books import telegram_words as words
from sudarshan.books.exit_guard import EXIT_ALL_BEING_PLACED

NOT_READ = (
    "Nothing was sent: the positions could not be read from Upstox. Check Positions and try again."
)
NOT_CANCELLED = "Nothing was cancelled: the orders could not be read from the broker. Check Orders and try again."
SEMI_AUTO = (
    "Cancel all orders operation is not allowed in Semi-Auto mode. Please switch to Auto mode to "
    "cancel orders."
)
PARTIAL = (
    "Exit all did not close every position. Of 3 exits, 2 were sent and 1 was refused "
    "(UDAPI1026_X: margin). Check Positions and Orders before sending again."
)


def _sdk_answer(handler):
    """What the real openalgo SDK hands the bot for /closeall, the server answering ``handler``."""
    from openalgo import api as openalgo_api

    client = openalgo_api(api_key="test-only-key", host="http://sdk.test")
    client.client = httpx.Client(transport=httpx.MockTransport(handler))
    return client.closeposition()


def _json(code, body):
    return lambda request: httpx.Response(code, json=body)


def _raise(error):
    def handler(request):
        raise error("no answer", request=request)

    return handler


@pytest.mark.parametrize(
    ("handler", "title", "sentence"),
    [
        (
            _json(424, {"status": "error", "message": NOT_READ}),
            words.EXIT_ALL_NOT_ALL_CLOSED,
            NOT_READ,
        ),
        (
            _json(409, {"status": "error", "message": EXIT_ALL_BEING_PLACED}),
            words.EXIT_ALL_NOT_ALL_CLOSED,
            EXIT_ALL_BEING_PLACED,
        ),
        (
            _json(502, {"status": "error", "message": PARTIAL, "not_confirmed": True}),
            words.EXIT_ALL_NOT_ALL_CLOSED,
            PARTIAL,
        ),
        (
            _json(200, {"status": "error", "message": "Upstox refused: market closed."}),
            words.EXIT_ALL_NOT_ALL_CLOSED,
            "Upstox refused: market closed.",
        ),
        (
            lambda request: httpx.Response(502, text="<html><h1>502 Bad Gateway</h1></html>"),
            words.EXIT_ALL_NOT_CONFIRMED_TITLE,
            words.EXIT_ALL_NOT_CONFIRMED,
        ),
        (_raise(httpx.ReadTimeout), words.EXIT_ALL_NOT_CONFIRMED_TITLE, words.EXIT_ALL_NO_ANSWER),
        (_raise(httpx.ConnectError), words.EXIT_ALL_NOT_SENT_TITLE, words.EXIT_ALL_UNREACHABLE),
    ],
    ids=["424", "409", "502 partial", "200 error", "gateway page", "timeout", "unreachable"],
)
def test_the_sdk_s_answer_reads_as_the_app_s_own_sentence(handler, title, sentence):
    answer = _sdk_answer(handler)
    assert words.sdk_sentence(answer) == (title, sentence)
    reply = words.exit_all_failed_reply(answer)
    assert reply.startswith(f"*{title}*\n\n")
    for leak in ("HTTP", "{", "status", "error_type", "<html"):
        assert leak not in reply


def test_a_title_is_not_said_twice_and_markdown_stays_whole():
    answer = _sdk_answer(_json(502, {"status": "error", "message": PARTIAL, "not_confirmed": True}))
    assert words.exit_all_failed_reply(answer) == (
        "*Exit all did not close every position*\n\n"
        "Of 3 exits, 2 were sent and 1 was refused (UDAPI1026\\_X: margin). "
        "Check Positions and Orders before sending again."
    )
    assert words.exit_all_failed_line(answer) == (
        "Exit all did not close every position: Of 3 exits, 2 were sent and 1 was refused "
        "(UDAPI1026\\_X: margin). Check Positions and Orders before sending again."
    )


@pytest.mark.parametrize(
    ("callback", "said"),
    [
        ("confirm_closeall", f"*Exit all did not close every position*\n\n{NOT_READ}"),
        ("confirm_closeall_with_strategies", f"Exit all did not close every position: {NOT_READ}"),
    ],
    ids=["/closeall", "/closeall and stop strategies"],
)
def test_the_bot_s_closeall_reply_says_the_app_s_sentence(monkeypatch, callback, said):
    import services.telegram_bot_service as bot

    service = bot.TelegramBotService()
    client = MagicMock()
    client.closeposition.return_value = _sdk_answer(
        _json(424, {"status": "error", "message": NOT_READ})
    )
    monkeypatch.setattr(bot, "get_telegram_user", lambda telegram_id: {"telegram_id": telegram_id})
    monkeypatch.setattr(bot, "log_command", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "_running_python_strategies", lambda: [])
    monkeypatch.setattr(service, "_get_sdk_client", lambda telegram_id: client)
    query = MagicMock(data=callback, answer=AsyncMock(), edit_message_text=AsyncMock())
    query.from_user.id = 7
    query.message.chat.id = 9
    context = MagicMock()
    context.bot.send_message = AsyncMock()

    asyncio.run(service.button_callback(MagicMock(callback_query=query), context))

    sent = context.bot.send_message.call_args.kwargs
    assert said in sent["text"]
    assert "HTTP" not in sent["text"] and "{" not in sent["text"]
    assert sent["parse_mode"] == "Markdown"


# -- The order alert's titles ------------------------------------------------------------------


@pytest.fixture
def alerts(monkeypatch):
    """The real alert service, its user lookups stubbed; collects each message it would send."""
    import services.telegram_alert_service as alert_module

    service = alert_module.TelegramAlertService()
    monkeypatch.setattr(service, "is_bot_active", lambda: True)
    monkeypatch.setattr(alert_module, "get_username_by_apikey", lambda key: "owner")
    monkeypatch.setattr(
        alert_module,
        "get_telegram_user_by_username",
        lambda user: {"telegram_id": 1, "notifications_enabled": True},
    )
    sent: list[str] = []
    monkeypatch.setattr(
        alert_module.alert_executor, "submit", lambda fn, telegram_id, message: sent.append(message)
    )

    def send(order_type, response, order_data=None):
        service.send_order_alert(
            order_type, {"strategy": "Exit all", **(order_data or {})}, response, "key"
        )
        [message] = sent[-1:]
        return message

    return send


@pytest.mark.parametrize(
    ("response", "title", "line"),
    [
        (
            {"status": "error", "message": PARTIAL, "not_confirmed": True},
            "*Exit all did not close every position*",
            "Of 3 exits, 2 were sent and 1 was refused (UDAPI1026\\_X: margin). Check Positions",
        ),
        (
            {"status": "error", "message": EXIT_ALL_BEING_PLACED},
            "*Exit all did not close every position*",
            EXIT_ALL_BEING_PLACED,
        ),
        (
            {"status": "success", "message": "All Open Positions Squared Off"},
            "*Position Closed*",
            "All positions closed successfully",
        ),
    ],
    ids=["partial", "refused while one is placed", "everything closed"],
)
def test_an_exit_all_alert_has_its_own_title(alerts, response, title, line):
    message = alerts("closeposition", response)
    assert message.startswith(title + "\n")
    assert line in message
    assert "Error:" not in message


@pytest.mark.parametrize(
    ("response", "title", "line"),
    [
        (
            {"status": "success", "canceled_orders": [], "failed_cancellations": []},
            "*No open orders to cancel*",
            "Cancelled: 0 orders",
        ),
        (
            {
                "status": "success",
                "canceled_orders": ["A1"],
                "failed_cancellations": ["A2", "A3"],
                "not_confirmed_cancellations": ["A3"],
            },
            "*Cancel all did not cancel every order*",
            "Not confirmed: 1 order (may have been cancelled; check their status before "
            "cancelling again)",
        ),
        (
            {"status": "success", "canceled_orders": [], "failed_cancellations": ["A2"]},
            "*Cancel all did not cancel every order*",
            "Failed: 1 orders",
        ),
        (
            {"status": "success", "canceled_orders": ["A1", "A2"], "failed_cancellations": []},
            "*All Orders Cancelled*",
            "Cancelled: 2 orders",
        ),
        (
            {"status": "error", "message": NOT_CANCELLED},
            "*Cancel all did not cancel every order*",
            NOT_CANCELLED,
        ),
        (
            {"status": "error", "message": SEMI_AUTO},
            "*Cancel all did not cancel every order*",
            SEMI_AUTO,
        ),
    ],
    ids=[
        "none open",
        "some not confirmed",
        "none cancelled",
        "all cancelled",
        "orders not read",
        "refused in semi-auto",
    ],
)
def test_a_cancel_all_alert_says_what_it_did(alerts, response, title, line):
    message = alerts("cancelallorder", response)
    assert message.startswith(title + "\n")
    assert line in message
