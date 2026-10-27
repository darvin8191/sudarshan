"""What Telegram says about an exit (M6.9, sudarshan/books/telegram_words.py).

The app's own Exit of one position is a smart order to a position size of 0, and its request
carries placeholders ("BUY", quantity 0). The owner's Analyze-mode Exit of RELIANCE (long 1, CNC)
arrived as "Smart Order Placed ... Action: BUY / Quantity: 0 / Position Size: 0", which reads as a
buy of nothing; it was a sell of 1 to close. The alert now says what was sent, in the app's words,
in both modes, and a smart order to a position size of 0 from any surface never shows the
placeholders: the side and quantity sent when known (they are written where the order is sized),
else "to close the position".
"""

from __future__ import annotations

from datetime import datetime

import pytest

import blueprints.orders as orders
import utils.session as session_utils

AT = datetime(2026, 10, 27, 10, 52, 13)
LIVE = "*LIVE MODE - Real Order*\n─────────────────────\n"
ANALYZE = "*ANALYZE MODE - No Real Order*\n─────────────────────\n"

# What blueprints/orders.py's Exit sends for RELIANCE, live (its log copy) and in Analyze mode.
LIVE_EXIT = {
    "strategy": "UI Exit Position",
    "exchange": "NSE",
    "symbol": "RELIANCE",
    "action": "BUY",
    "product": "CNC",
    "pricetype": "MARKET",
    "quantity": "0",
    "price": "0",
    "trigger_price": "0",
    "disclosed_quantity": "0",
    "position_size": "0",
    "api_type": "closeposition",
}
ANALYZE_EXIT = {
    **{key: value for key, value in LIVE_EXIT.items() if key != "product"},
    "product_type": "CNC",
    "api_type": "placesmartorder",
}
SOLD_1 = {"action": "SELL", "quantity": 1, "closes": True}
NOT_CONFIRMED = (
    "This exit is not confirmed: it may have gone through. Check Orders before sending it again."
)
BUSY = "Another order for RELIANCE is still being processed. Try again in a moment."


@pytest.fixture
def alert(monkeypatch):
    """The real alert service at 10:52:13, its user lookups stubbed: the message it would send."""
    import services.telegram_alert_service as alert_module

    class Clock:
        @staticmethod
        def now():
            return AT

    service = alert_module.TelegramAlertService()
    monkeypatch.setattr(alert_module, "datetime", Clock)
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

    def send(order_type, order_data, response):
        service.send_order_alert(order_type, dict(order_data), dict(response), "key")
        [message] = sent[-1:]
        return message

    return send


# -- The app's own Exit, in both modes ----------------------------------------------------------


@pytest.mark.parametrize(
    ("order_type", "order_data", "response", "text"),
    [
        (
            "closeposition",
            LIVE_EXIT,
            {
                "status": "success",
                "message": "Position close order placed successfully.",
                "orderid": "251027000000123",
                "sent": SOLD_1,
            },
            "*Exit sent*\n" + LIVE + "RELIANCE (NSE, CNC), sell 1 to close\n"
            "Order ID: `251027000000123`\nTime: 10:52:13",
        ),
        (
            "placesmartorder",
            ANALYZE_EXIT,
            {"status": "success", "orderid": "26102710521301", "mode": "analyze", "sent": SOLD_1},
            "*Exit sent*\n" + ANALYZE + "RELIANCE (NSE, CNC), sell 1 to close\n"
            "Order ID: `26102710521301`\nTime: 10:52:13",
        ),
        (
            "closeposition",
            {
                **LIVE_EXIT,
                "symbol": "BANKNIFTY27OCT2657500PE",
                "exchange": "NFO",
                "product": "NRML",
            },
            {
                "status": "success",
                "orderid": "251027000000124",
                "sent": {"action": "BUY", "quantity": 30, "closes": True},
            },
            "*Exit sent*\n" + LIVE + "BANKNIFTY27OCT2657500PE (NFO, NRML), buy 30 to close\n"
            "Order ID: `251027000000124`\nTime: 10:52:13",
        ),
        (
            "closeposition",
            LIVE_EXIT,
            {"status": "success", "orderid": "251027000000125"},
            "*Exit sent*\n" + LIVE + "RELIANCE (NSE, CNC), to close the position\n"
            "Order ID: `251027000000125`\nTime: 10:52:13",
        ),
        (
            "closeposition",
            LIVE_EXIT,
            {
                "status": "success",
                "orderid": "251027000000126",
                "orderids": ["251027000000126", "251027000000127"],
                "sent": {"action": "SELL", "quantity": 1950, "closes": True},
            },
            "*Exit sent*\n" + LIVE + "RELIANCE (NSE, CNC), sell 1950 to close\n"
            "Order IDs: `251027000000126`, `251027000000127`\nTime: 10:52:13",
        ),
        (
            "closeposition",
            LIVE_EXIT,
            {"status": "error", "message": NOT_CONFIRMED, "not_confirmed": True},
            "*Exit not confirmed*\n"
            + LIVE
            + "RELIANCE (NSE, CNC), to close the position\n"
            + NOT_CONFIRMED
            + "\nTime: 10:52:13",
        ),
        (
            "placesmartorder",
            ANALYZE_EXIT,
            {"status": "error", "message": BUSY, "mode": "analyze"},
            "*Exit not sent*\n" + ANALYZE + "RELIANCE (NSE, CNC)\n" + BUSY + "\nTime: 10:52:13",
        ),
        (
            "closeposition",
            LIVE_EXIT,
            {"status": "error", "message": "UDAPI100500: order_type is invalid"},
            "*Exit not sent*\n" + LIVE + "RELIANCE (NSE, CNC)\nUDAPI100500: order\\_type is invalid"
            "\nTime: 10:52:13",
        ),
        (
            "placesmartorder",
            ANALYZE_EXIT,
            {
                "status": "success",
                "message": "No OpenPosition Found. Not placing Exit order.",
                "mode": "analyze",
            },
            "*No exit sent*\n" + ANALYZE + "RELIANCE (NSE, CNC): no position was open, so "
            "nothing was sent.\nTime: 10:52:13",
        ),
    ],
    ids=[
        "live, sold 1",
        "analyze, sold 1 (the owner's RELIANCE)",
        "a short covered",
        "what was sent not known",
        "sliced",
        "not confirmed",
        "refused",
        "refused, its reason kept whole in Markdown",
        "nothing was open",
    ],
)
def test_the_app_s_exit_says_what_it_sent(alert, order_type, order_data, response, text):
    message = alert(order_type, order_data, response)
    assert message == text
    assert "Quantity: 0" not in message and "Position Size" not in message
    assert "UI Exit Position" not in message


# -- Any other smart order to a position size of 0 ----------------------------------------------

TV_EXIT = {
    "strategy": "Nifty breakout",
    "symbol": "NIFTY27OCT2625900CE",
    "action": "BUY",
    "exchange": "NFO",
    "pricetype": "MARKET",
    "product": "MIS",
    "quantity": "0",
    "position_size": "0",
}


def _smart(lines: str, order_id: str | None = "251027000000456") -> str:
    tail = f"\nOrder ID: `{order_id}`" if order_id else ""
    return (
        "*Smart Order Placed*\nStrategy: *Nifty breakout*\n"
        + LIVE
        + "Symbol: `NIFTY27OCT2625900CE`\n"
        + lines
        + "\nExchange: NFO"
        + tail
        + "\nTime: 10:52:13"
    )


@pytest.mark.parametrize(
    ("order_data", "response", "text"),
    [
        (
            TV_EXIT,
            {"status": "success", "orderid": "251027000000456", "sent": {**SOLD_1, "quantity": 65}},
            _smart("Action: SELL 65 to close"),
        ),
        (
            TV_EXIT,
            {"status": "success", "orderid": "251027000000456"},
            _smart("Action: to close the position"),
        ),
        (
            {**TV_EXIT, "quantity": "65"},  # flat, so a fresh order of what the request asked
            {
                "status": "success",
                "orderid": "251027000000456",
                "sent": {"action": "BUY", "quantity": 65, "closes": False},
            },
            _smart("Action: BUY 65"),
        ),
        (
            TV_EXIT,
            {"status": "success", "message": "Positions Already Matched. No Action needed."},
            _smart(
                "Action: nothing sent\nPositions Already Matched. No Action needed.", order_id="N/A"
            ),
        ),
    ],
    ids=["closed, sent known", "sent not known", "flat: a fresh order", "nothing sent"],
)
def test_a_smart_order_to_a_position_size_of_0_never_shows_the_placeholders(
    alert, order_data, response, text
):
    message = alert("placesmartorder", order_data, response)
    assert message == text
    assert "Action: BUY\n" not in message and "Quantity: 0" not in message


def test_every_other_smart_order_keeps_its_layout(alert):
    message = alert(
        "placesmartorder",
        {**TV_EXIT, "quantity": "65", "position_size": "65"},
        {
            "status": "success",
            "orderid": "251027000000457",
            "sent": {"action": "BUY", "quantity": 65, "closes": False},
        },
    )
    assert message == (
        "*Smart Order Placed*\nStrategy: *Nifty breakout*\n"
        + LIVE
        + "Symbol: `NIFTY27OCT2625900CE`\nAction: BUY\nQuantity: 65\nPosition Size: 65\n"
        "Exchange: NFO\nOrder ID: `251027000000457`\nTime: 10:52:13"
    )


# -- What was sent comes from where the order is sized, and never into an answer ----------------


@pytest.fixture
def upstox_book(monkeypatch):
    """Upstox's order module over a book holding RELIANCE long 1 (CNC); records what is placed."""
    from broker.upstox.api import order_api as upstox

    placed = []

    class Answer:
        status = 200
        status_code = 200

    monkeypatch.setattr(upstox, "get_open_position", lambda *args: "1")
    monkeypatch.setattr(upstox, "get_br_symbol", lambda symbol, exchange: symbol)

    def place_order_api(data, auth):
        placed.append((data["action"], data["quantity"]))
        return (
            Answer(),
            {"status": "success", "data": {"order_id": "251027000000123"}},
            "251027000000123",
        )

    monkeypatch.setattr(upstox, "place_order_api", place_order_api)
    return upstox, placed


def test_upstox_s_smart_order_says_what_it_sent(upstox_book):
    upstox, placed = upstox_book
    exit_order = {"symbol": "RELIANCE", "exchange": "NSE", "product": "CNC", "action": "BUY"}
    res, answer, orderid = upstox.place_smartorder_api(
        {**exit_order, "quantity": "0", "position_size": "0"}, "token"
    )
    assert placed == [("SELL", "1")]
    assert answer["sent"] == {"action": "SELL", "quantity": 1, "closes": True}
    assert orderid == "251027000000123"


def test_upstox_s_fresh_order_from_flat_says_it_opened(upstox_book, monkeypatch):
    upstox, placed = upstox_book
    monkeypatch.setattr(upstox, "get_open_position", lambda *args: "0")
    order = {"symbol": "RELIANCE", "exchange": "NSE", "product": "CNC", "action": "buy"}
    _, answer, _ = upstox.place_smartorder_api(
        {**order, "quantity": "5", "position_size": "0"}, "t"
    )
    assert placed == [("buy", "5")]
    assert answer["sent"] == {"action": "BUY", "quantity": 5, "closes": False}


def test_the_sandbox_s_smart_order_says_what_it_sent():
    from services.sandbox_service import _place_smart_order_locked

    class Book:
        @staticmethod
        def get_open_positions():
            row = {"symbol": "RELIANCE", "exchange": "NSE", "product": "CNC", "quantity": 1}
            return True, {"data": [row]}, 200

    class Orders:
        placed = []

        def place_order(self, data):
            self.placed.append((data["action"], data["quantity"]))
            return True, {"status": "success", "orderid": "26102710521301", "mode": "analyze"}, 200

    orders_ = Orders()
    ok, answer, code = _place_smart_order_locked(
        Book(), orders_, {**ANALYZE_EXIT}, "RELIANCE", "NSE", "CNC", 0, 0
    )
    assert (ok, code, orders_.placed) == (True, 200, [("SELL", 1)])
    assert answer["sent"] == {"action": "SELL", "quantity": 1, "closes": True}


@pytest.fixture
def published(monkeypatch):
    import services.place_smart_order_service as smart_service

    seen = []
    monkeypatch.setattr(smart_service.bus, "publish", lambda event: seen.append(event))
    return seen


def test_the_smart_order_service_takes_it_out_of_the_answer_in_analyze_mode(published, monkeypatch):
    import services.place_smart_order_service as smart_service
    import services.sandbox_service as sandbox_service

    monkeypatch.setattr(smart_service, "get_analyze_mode", lambda: True)
    monkeypatch.setattr(
        sandbox_service,
        "sandbox_place_smart_order",
        lambda order_data, api_key, original_data: (
            True,
            {"status": "success", "orderid": "26102710521301", "mode": "analyze", "sent": SOLD_1},
            200,
        ),
    )
    order = {**ANALYZE_EXIT, "apikey": "key"}
    ok, answer, code = smart_service.place_smart_order_with_auth(dict(order), "", "upstox", order)
    assert (ok, code) == (True, 200)
    assert answer == {"status": "success", "orderid": "26102710521301", "mode": "analyze"}
    [event] = published
    assert event.response_data["sent"] == SOLD_1


def test_the_smart_order_service_takes_it_out_of_the_answer_live(published, monkeypatch):
    import services.place_smart_order_service as smart_service

    class Answer:
        status = 200

    monkeypatch.setattr(smart_service, "get_analyze_mode", lambda: False)
    monkeypatch.setattr(
        smart_service,
        "import_broker_module",
        lambda broker: type(
            "Upstox",
            (),
            {
                "place_smartorder_api": staticmethod(
                    lambda data, auth: (
                        Answer(),
                        {"status": "success", "sent": {**SOLD_1, "quantity": 65}},
                        "251027000000456",
                    )
                )
            },
        ),
    )
    order = {**TV_EXIT, "apikey": "key"}
    ok, answer, code = smart_service.place_smart_order_with_auth(
        dict(order), "tok", "upstox", order
    )
    assert (ok, code, answer) == (True, 200, {"status": "success", "orderid": "251027000000456"})
    [event] = published
    assert event.response_data["sent"] == {**SOLD_1, "quantity": 65}


def test_the_app_s_live_exit_tells_its_alert_and_not_the_phone(alert, monkeypatch):
    from flask import Flask

    import database.settings_db as settings_db
    from database.latency_db import init_latency_db
    from utils.event_bus import bus

    init_latency_db()
    monkeypatch.setattr(session_utils, "is_session_valid", lambda: True)
    monkeypatch.setattr(orders, "get_auth_token", lambda user: "token")
    monkeypatch.setattr(orders, "get_analyze_mode", lambda: False)
    monkeypatch.setattr(settings_db, "get_analyze_mode", lambda: False)
    monkeypatch.setattr(orders, "get_api_key_for_tradingview", lambda user: "key")
    published = []
    monkeypatch.setattr(bus, "publish", lambda event: published.append(event))

    class Answer:
        status = 200

    monkeypatch.setattr(
        orders,
        "dynamic_import",
        lambda broker, module, names: {
            "place_smartorder_api": lambda data, auth: (
                Answer(),
                {"status": "success", "sent": SOLD_1},
                "251027000000123",
            ),
            "get_open_position": lambda *args: "1",
        },
    )
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(orders.orders_bp)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user"] = "owner"
        sess["broker"] = "upstox"
    answer = client.post(
        "/close_position", json={"symbol": "RELIANCE", "exchange": "NSE", "product": "CNC"}
    )
    assert answer.status_code == 200
    assert "sent" not in answer.get_json()  # the phone's answer is as it was
    [event] = published
    assert event.api_type == "closeposition"
    assert event.response_data["sent"] == SOLD_1
    message = alert(event.api_type, event.request_data, event.response_data)
    assert message == (
        "*Exit sent*\n" + LIVE + "RELIANCE (NSE, CNC), sell 1 to close\n"
        "Order ID: `251027000000123`\nTime: 10:52:13"
    )


def test_the_owner_s_analyze_mode_exit_from_the_route_to_the_alert(alert, monkeypatch):
    """The route's own request, through the smart order service and the sandbox, to the words."""
    from flask import Flask

    import database.settings_db as settings_db
    import services.order_router_service as order_router
    import services.place_smart_order_service as smart_service
    import services.sandbox_service as sandbox_service
    from database.latency_db import init_latency_db
    from utils.event_bus import bus

    init_latency_db()
    monkeypatch.setattr(session_utils, "is_session_valid", lambda: True)
    monkeypatch.setattr(orders, "get_auth_token", lambda user: "token")
    monkeypatch.setattr(orders, "get_analyze_mode", lambda: True)
    monkeypatch.setattr(settings_db, "get_analyze_mode", lambda: True)
    monkeypatch.setattr(smart_service, "get_analyze_mode", lambda: True)
    monkeypatch.setattr(orders, "get_api_key_for_tradingview", lambda user: "key")
    monkeypatch.setattr(order_router, "should_route_to_pending", lambda key, kind: False)
    monkeypatch.setattr(smart_service, "get_auth_token_broker", lambda key: ("token", "upstox"))
    asked = []

    def sandbox(order_data, api_key, original_data):
        asked.append(dict(order_data))
        return (
            True,
            {"status": "success", "orderid": "26102710521301", "mode": "analyze", "sent": SOLD_1},
            200,
        )

    monkeypatch.setattr(sandbox_service, "sandbox_place_smart_order", sandbox)
    published = []
    monkeypatch.setattr(bus, "publish", lambda event: published.append(event))
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(orders.orders_bp)
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["user"] = "owner"
        sess["broker"] = "upstox"

    answer = client.post(
        "/close_position", json={"symbol": "RELIANCE", "exchange": "NSE", "product": "CNC"}
    )
    assert answer.status_code == 200
    assert "sent" not in answer.get_json()
    assert asked[0]["position_size"] == "0"
    [event] = published
    message = alert(event.api_type, event.request_data, event.response_data)
    assert message == (
        "*Exit sent*\n" + ANALYZE + "RELIANCE (NSE, CNC), sell 1 to close\n"
        "Order ID: `26102710521301`\nTime: 10:52:13"
    )
