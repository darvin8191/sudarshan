"""An exit or a cancel that may have reached the broker reads as "not confirmed" (M6.9).

The phone holds its Exit while an exit may still be on its way, and releases it only on an
answer that says nothing was sent: a trader told an exit failed sends it again, and a second
exit sized from a book that has not caught up reverses the position instead of closing it.

These pin the server's half of that contract, in the SUDARSHAN blocks of
`blueprints/orders.py`, `broker/upstox/api/order_api.py` and
`services/close_position_service.py`. An answer that may have reached Upstox is a 5xx or
carries `not_confirmed`; a refusal made before anything was sent is a 4xx without it; and no
answer carries an exception's words, which belong in the log.
"""

from __future__ import annotations

import httpx
import pytest

import blueprints.orders as orders
import broker.upstox.api.order_api as upstox
import utils.session as session_utils
from utils.broker_backpressure import BusyResponse
from utils.event_bus import bus

EXIT = {"symbol": "SBIN", "exchange": "NSE", "product": "MIS"}
SECRET_DETAIL = "The read operation timed out"


class Answer:
    """An order API's response object: what the route reads is its .status."""

    def __init__(self, status: int):
        self.status = status
        self.status_code = status


@pytest.fixture
def client(monkeypatch):
    """The orders blueprint behind a logged-in session, in live mode."""
    from flask import Flask

    import database.settings_db as settings_db
    from database.latency_db import init_latency_db

    init_latency_db()  # the routes record their latency; a throwaway database (conftest)
    monkeypatch.setattr(session_utils, "is_session_valid", lambda: True)
    monkeypatch.setattr(orders, "get_auth_token", lambda user: "token")
    monkeypatch.setattr(orders, "get_analyze_mode", lambda: False)
    monkeypatch.setattr(settings_db, "get_analyze_mode", lambda: False)
    monkeypatch.setattr(orders, "get_api_key_for_tradingview", lambda user: None)
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(orders.orders_bp)
    test_client = app.test_client()
    with test_client.session_transaction() as sess:
        sess["user"] = "owner"
        sess["broker"] = "upstox"
    return test_client


@pytest.fixture
def exit_route(client, monkeypatch):
    """POST /close_position, the broker's smart order answering (or raising) `reply`."""

    def post(reply):
        def place_smartorder_api(order_data, auth_token):
            if isinstance(reply, Exception):
                raise reply
            return reply

        monkeypatch.setattr(
            orders,
            "dynamic_import",
            lambda broker, module, names: {
                "place_smartorder_api": place_smartorder_api,
                "get_open_position": lambda *args: "0",
            },
        )
        response = client.post("/close_position", json=EXIT)
        return response.status_code, response.get_json()

    return post


def _not_confirmed(code, body, message=orders.EXIT_NOT_CONFIRMED):
    assert code >= 500, code
    assert body == {"status": "error", "message": message, "not_confirmed": True}


@pytest.mark.parametrize(
    "reply",
    [
        # Upstox took it and named no order.
        (Answer(200), {"status": "success", "data": {"order_ids": []}}, None),
        # An error carried by a 2xx: nothing says it was not placed.
        (Answer(200), {"status": "error", "message": "?"}, None),
    ],
)
def test_an_answer_naming_no_order_is_not_confirmed(exit_route, reply):
    code, body = exit_route(reply)
    assert code == 502
    _not_confirmed(code, body)


def test_a_failure_after_the_send_is_not_confirmed_without_the_exception_s_words(exit_route):
    code, body = exit_route(
        (upstox._ErrorResponse(500), {"status": "error", "message": SECRET_DETAIL}, None)
    )
    assert code == 500
    _not_confirmed(code, body)


def test_an_upstox_5xx_is_not_confirmed(exit_route):
    code, body = exit_route((Answer(503), {"status": "error", "message": "Service down"}, None))
    assert code == 503
    _not_confirmed(code, body)


@pytest.mark.parametrize(
    ("reply", "code", "message"),
    [
        # Upstox refused it.
        (
            (Answer(400), {"status": "error", "message": "UDAPI1026: Insufficient funds"}, None),
            400,
            "UDAPI1026: Insufficient funds",
        ),
        # Nothing to exit: no order was sent.
        (
            (None, {"status": "success", "message": "No open position found."}, None),
            400,
            "No open position found.",
        ),
        # Refused by the order window before it was sent.
        ((BusyResponse(429), {"status": "error", "message": "Busy"}, None), 429, "Busy"),
    ],
)
def test_a_refusal_before_anything_was_sent_stays_a_refusal(exit_route, reply, code, message):
    got_code, body = exit_route(reply)
    assert got_code == code
    assert body == {"status": "error", "message": message}


def test_a_placed_exit_stays_placed_when_logging_it_fails(exit_route, monkeypatch):
    monkeypatch.setattr(orders, "get_api_key_for_tradingview", lambda user: "key")

    def broken_publish(event):
        raise RuntimeError("event bus is down")

    monkeypatch.setattr(bus, "publish", broken_publish)
    code, body = exit_route((Answer(200), {"status": "success"}, "251021000000001"))
    assert code == 200
    assert body["status"] == "success"
    assert body["orderid"] == "251021000000001"


def test_the_route_breaking_is_not_confirmed_without_the_exception_s_words(exit_route):
    code, body = exit_route(RuntimeError(SECRET_DETAIL))
    assert code == 500
    _not_confirmed(code, body)


@pytest.mark.parametrize(
    ("route", "message"),
    [
        ("/close_all_positions", orders.EXIT_ALL_NOT_CONFIRMED),
        ("/cancel_all_orders", orders.CANCEL_ALL_NOT_CONFIRMED),
        ("/cancel_order", orders.CANCEL_NOT_CONFIRMED),
    ],
)
def test_the_other_routes_breaking_are_not_confirmed(client, monkeypatch, route, message):
    import services.cancel_all_order_service as cancel_all_service
    import services.cancel_order_service as cancel_service
    import services.close_position_service as close_service

    def broken(*args, **kwargs):
        raise RuntimeError(SECRET_DETAIL)

    monkeypatch.setattr(close_service, "close_position", broken)
    monkeypatch.setattr(cancel_all_service, "cancel_all_orders", broken)
    monkeypatch.setattr(cancel_service, "cancel_order", broken)
    response = client.post(route, json={"orderid": "251021000000001"})
    _not_confirmed(response.status_code, response.get_json(), message)


# -- Upstox: what a send that broke says -------------------------------------------------------


class FakeClient:
    """Stands in for the shared httpx client: each verb answers `reply`, or raises it."""

    def __init__(self, reply):
        self.reply = reply

    def _answer(self, method, url, **kwargs):
        if isinstance(self.reply, Exception):
            raise self.reply
        status, body = self.reply
        return httpx.Response(status, json=body, request=httpx.Request(method, url))

    def delete(self, url, **kwargs):
        return self._answer("DELETE", url, **kwargs)

    def get(self, url, **kwargs):
        return self._answer("GET", url, **kwargs)


@pytest.fixture
def upstox_answers(monkeypatch):
    monkeypatch.setenv("BROKER_API_KEY", "test-only")
    monkeypatch.setattr(upstox, "apply_rate_limit", lambda category="standard": None)

    def answer(reply):
        monkeypatch.setattr(upstox, "get_httpx_client", lambda: FakeClient(reply))

    return answer


def test_a_timeout_on_a_cancel_is_not_confirmed(upstox_answers):
    upstox_answers(httpx.ReadTimeout(SECRET_DETAIL))
    assert upstox.get_api_response("/x", "token", method="DELETE")["not_confirmed"] is True
    body, code = upstox.cancel_order("251021000000001", "token")
    assert code == 502
    assert body == {
        "status": "error",
        "message": upstox._CANCEL_NOT_CONFIRMED,
        "not_confirmed": True,
    }


def test_an_upstox_5xx_on_a_cancel_is_not_confirmed(upstox_answers):
    upstox_answers((503, {"status": "error", "errors": [{"message": "down"}]}))
    body, code = upstox.cancel_order("251021000000001", "token")
    assert code == 502
    assert body["not_confirmed"] is True


def test_an_upstox_refusal_of_a_cancel_stays_a_refusal(upstox_answers):
    upstox_answers((400, {"status": "error", "message": "Order already completed"}))
    assert "not_confirmed" not in upstox.get_api_response("/x", "token", method="DELETE")
    body, code = upstox.cancel_order("251021000000001", "token")
    assert code == 400
    assert body == {"status": "error", "message": "Order already completed"}


def test_the_cancel_route_says_a_timed_out_cancel_is_not_confirmed(
    client, monkeypatch, upstox_answers
):
    import services.cancel_order_service as cancel_service

    monkeypatch.setattr(cancel_service, "get_analyze_mode", lambda: False)
    upstox_answers(httpx.ReadTimeout(SECRET_DETAIL))
    response = client.post("/cancel_order", json={"orderid": "251021000000001"})
    assert response.status_code == 502
    assert SECRET_DETAIL not in response.get_json()["message"]


def _smart_exit(monkeypatch, *, position="10", broken_after_send=False):
    monkeypatch.setattr(upstox, "get_open_position", lambda *args: position)
    monkeypatch.setattr(
        upstox,
        "place_order_api",
        lambda data, auth: (Answer(200), {"status": "success"}, "251021000000001"),
    )
    if broken_after_send:

        def broken(auth):
            raise RuntimeError(SECRET_DETAIL)

        monkeypatch.setattr(upstox, "_invalidate_position_cache", broken)
    return upstox.place_smartorder_api(
        {**EXIT, "action": "BUY", "quantity": "0", "position_size": "0"}, "token"
    )


def test_a_smart_exit_that_breaks_after_the_send_is_not_confirmed(monkeypatch):
    res, body, orderid = _smart_exit(monkeypatch, broken_after_send=True)
    assert res.status == 500
    assert orderid is None
    assert body["not_confirmed"] is True
    assert SECRET_DETAIL not in body["message"]


def test_a_smart_exit_that_breaks_before_the_send_is_a_refusal_in_a_trader_s_words(monkeypatch):
    def broken(*args):
        raise RuntimeError(SECRET_DETAIL)

    monkeypatch.setattr(upstox, "get_open_position", broken)
    res, body, orderid = upstox.place_smartorder_api(
        {**EXIT, "action": "BUY", "quantity": "0", "position_size": "0"}, "token"
    )
    assert (res, orderid) == (None, None)
    assert body == upstox._SMART_NOT_SENT
    assert "not_confirmed" not in body


def _close_all(monkeypatch, answers):
    import services.close_position_service as close_service

    positions = [
        {"quantity": 10, "instrument_token": f"NSE_EQ|{n}", "exchange": "NSE", "product": "I"}
        for n in range(len(answers))
    ]
    replies = iter(answers)
    monkeypatch.setattr(
        upstox, "get_positions", lambda auth: {"status": "success", "data": positions}
    )
    monkeypatch.setattr(upstox, "get_symbol", lambda token, exchange: "SBIN")
    monkeypatch.setattr(upstox, "place_order_api", lambda data, auth: next(replies))
    monkeypatch.setattr(close_service, "get_analyze_mode", lambda: False)
    return close_service.close_position(position_data={}, auth_token="token", broker="upstox")


def test_a_close_all_that_sent_some_exits_is_not_a_plain_refusal(monkeypatch):
    busy = (BusyResponse(429), {"status": "error", "message": "Busy"}, None)
    placed = (Answer(200), {"status": "success"}, "251021000000001")
    success, body, code = _close_all(monkeypatch, [busy, placed])
    assert (success, code) == (False, 429)
    assert body["not_confirmed"] is True


def test_a_close_all_that_sent_nothing_stays_a_refusal(monkeypatch):
    busy = (BusyResponse(429), {"status": "error", "message": "Busy"}, None)
    success, body, code = _close_all(monkeypatch, [busy, busy])
    assert (success, code) == (False, 429)
    assert "not_confirmed" not in body


# -- Exit all says what happened, never "squared off" when it did not -------------------------

PLACED = (Answer(200), {"status": "success"}, "251021000000001")
REFUSED = (Answer(400), {"status": "error", "message": "Quantity exceeds freeze limit"}, None)
TIMED_OUT = (upstox._ErrorResponse(500), {"status": "error", "message": SECRET_DETAIL}, None)
NO_ID = (Answer(200), {"status": "success", "data": {"order_ids": []}}, None)


@pytest.fixture
def published(monkeypatch):
    import services.close_position_service as close_service

    seen = []
    monkeypatch.setattr(close_service.bus, "publish", lambda event: seen.append(event))
    return seen


@pytest.mark.parametrize(
    "read",
    [
        {"status": "error", "message": SECRET_DETAIL, "not_confirmed": True},  # timed out
        {"status": "error", "errors": [{"errorCode": "UDAPI100050", "message": "Invalid token"}]},
    ],
)
def test_exit_all_whose_positions_read_failed_says_nothing_was_sent(
    client, published, monkeypatch, read
):
    import services.close_position_service as close_service

    placed = []
    monkeypatch.setattr(upstox, "get_positions", lambda auth: read)
    monkeypatch.setattr(upstox, "place_order_api", lambda data, auth: placed.append(data))
    monkeypatch.setattr(close_service, "get_analyze_mode", lambda: False)
    response = client.post("/close_all_positions", json={})
    assert response.status_code == 424
    assert response.get_json() == {"status": "error", "message": upstox._CLOSE_ALL_NOT_READ}
    assert placed == []
    assert [event.response_data["status"] for event in published] == ["error"]


def test_exit_all_with_no_open_positions_is_still_a_success(monkeypatch):
    monkeypatch.setattr(upstox, "get_positions", lambda auth: {"status": "success", "data": []})
    assert upstox.close_all_positions("key", "token") == (
        {"message": "No Open Positions Found"},
        200,
    )


def test_exit_all_that_closed_everything_answers_as_before(published, monkeypatch):
    success, body, code = _close_all(monkeypatch, [PLACED, PLACED])
    assert (success, code) == (True, 200)
    assert body == {"status": "success", "message": "All Open Positions Squared Off"}
    assert published[0].response_data["status"] == "success"


@pytest.mark.parametrize(
    ("answers", "code", "flagged", "words"),
    [
        (
            [REFUSED, TIMED_OUT],
            502,
            True,
            "Of 2 exits, 1 was refused (Quantity exceeds freeze limit) and 1 is not confirmed "
            "and may have gone through.",
        ),
        ([PLACED, NO_ID], 502, True, "Of 2 exits, 1 was sent and 1 is not confirmed"),
        ([PLACED, REFUSED], 400, True, "Of 2 exits, 1 was sent and 1 was refused"),
        ([REFUSED, REFUSED], 400, False, "Of 2 exits, 2 were refused"),
    ],
)
def test_exit_all_that_did_not_close_everything_says_how_many_went(
    published, monkeypatch, answers, code, flagged, words
):
    success, body, got = _close_all(monkeypatch, answers)
    assert (success, got) == (False, code)
    assert body["message"].startswith("Exit all did not close every position.")
    assert words in body["message"]
    assert SECRET_DETAIL not in body["message"]
    assert body.get("not_confirmed", False) is flagged
    [event] = published
    assert event.response_data["status"] == "error"


def test_exit_all_counts_a_position_it_could_not_match_as_not_sent(monkeypatch):
    import services.close_position_service as close_service

    monkeypatch.setattr(
        upstox,
        "get_positions",
        lambda auth: {
            "status": "success",
            "data": [
                {"quantity": 10, "instrument_token": "NSE_EQ|1", "exchange": "NSE", "product": "I"},
                {"quantity": 10, "instrument_token": "NSE_EQ|2", "exchange": "NSE", "product": "I"},
            ],
        },
    )
    monkeypatch.setattr(
        upstox, "get_symbol", lambda token, exchange: "SBIN" if token.endswith("1") else None
    )
    monkeypatch.setattr(upstox, "place_order_api", lambda data, auth: PLACED)
    monkeypatch.setattr(close_service, "get_analyze_mode", lambda: False)
    success, body, code = close_service.close_position(
        position_data={}, auth_token="token", broker="upstox"
    )
    assert (success, code) == (False, 400)
    assert "1 was sent and 1 could not be sent, its contract not found" in body["message"]
    assert body["not_confirmed"] is True


def test_exit_all_that_breaks_after_an_exit_went_is_not_confirmed(monkeypatch):
    calls = []

    def symbol(token, exchange):
        calls.append(token)
        if len(calls) > 1:
            raise RuntimeError(SECRET_DETAIL)
        return "SBIN"

    monkeypatch.setattr(
        upstox,
        "get_positions",
        lambda auth: {
            "status": "success",
            "data": [
                {
                    "quantity": 10,
                    "instrument_token": f"NSE_EQ|{n}",
                    "exchange": "NSE",
                    "product": "I",
                }
                for n in range(2)
            ],
        },
    )
    monkeypatch.setattr(upstox, "get_symbol", symbol)
    monkeypatch.setattr(upstox, "place_order_api", lambda data, auth: PLACED)
    body, code = upstox.close_all_positions("key", "token")
    assert code == 500
    assert body["not_confirmed"] is True
    assert SECRET_DETAIL not in body["message"]


def test_exit_all_that_breaks_before_its_first_exit_is_not_flagged(monkeypatch):
    def broken(token, exchange):
        raise RuntimeError(SECRET_DETAIL)

    monkeypatch.setattr(
        upstox,
        "get_positions",
        lambda auth: {
            "status": "success",
            "data": [
                {"quantity": 10, "instrument_token": "NSE_EQ|1", "exchange": "NSE", "product": "I"}
            ],
        },
    )
    monkeypatch.setattr(upstox, "get_symbol", broken)
    monkeypatch.setattr(upstox, "place_order_api", lambda data, auth: PLACED)
    assert upstox.close_all_positions("key", "token") == (
        {"status": "error", "message": "Failed to close all positions"},
        500,
    )


@pytest.mark.parametrize(
    ("answered", "status", "flagged"),
    [
        ({"status": "success", "message": "All Open Positions Squared Off"}, "success", False),
        ({"status": "error", "message": "Exit all did not close every position."}, "error", False),
        (
            {"status": "error", "message": "Exit all did not ...", "not_confirmed": True},
            "error",
            True,
        ),
    ],
)
def test_the_close_event_carries_its_real_status(monkeypatch, answered, status, flagged):
    import subscribers.socketio_subscriber as subscriber
    from events import PositionClosedEvent

    emitted = []
    monkeypatch.setattr(
        subscriber.socketio, "emit", lambda name, data: emitted.append((name, data))
    )
    subscriber.on_position_closed(
        PositionClosedEvent(mode="live", message=answered["message"], response_data=answered)
    )
    [(name, data)] = emitted
    assert name == "close_position_event"
    assert data["status"] == status
    assert data.get("not_confirmed", False) is flagged


# -- Cancel all says what happened to each cancel ----------------------------------------------


def _cancel_all(client, monkeypatch, replies, book_status="success"):
    import services.cancel_all_order_service as cancel_all_service

    monkeypatch.setattr(cancel_all_service, "get_analyze_mode", lambda: False, raising=False)
    orders_ = [{"order_id": f"A{n}", "status": "open"} for n in range(len(replies))]
    monkeypatch.setattr(
        upstox, "get_order_book", lambda auth: {"status": book_status, "data": orders_}
    )
    answers = iter(replies)
    monkeypatch.setattr(upstox, "cancel_order", lambda orderid, auth: next(answers))
    response = client.post("/cancel_all_orders", json={})
    return response.status_code, response.get_json()


CANCELLED = ({"status": "success", "orderid": "A"}, 200)
CANCEL_REFUSED = ({"status": "error", "message": "Order already completed"}, 400)
CANCEL_UNSURE = ({"status": "error", "message": "x", "not_confirmed": True}, 502)


def test_cancel_all_whose_every_cancel_timed_out_is_not_confirmed(client, monkeypatch):
    code, body = _cancel_all(client, monkeypatch, [CANCEL_UNSURE, CANCEL_UNSURE])
    assert code == 502
    assert body["not_confirmed"] is True
    assert "Of 2 open orders, 2 are not confirmed and may have gone through." in body["message"]


def test_cancel_all_that_was_refused_says_so(client, monkeypatch):
    code, body = _cancel_all(client, monkeypatch, [CANCELLED, CANCEL_REFUSED])
    assert code == 400
    assert "not_confirmed" not in body
    assert "Of 2 open orders, 1 was cancelled and 1 was refused." in body["message"]


def test_cancel_all_with_none_open_says_there_were_none(client, monkeypatch):
    # 58422ca answered this "Successfully canceled 0 orders", and gave "No open orders to
    # cancel" to a Cancel all whose every cancel had failed.
    assert _cancel_all(client, monkeypatch, []) == (
        200,
        {"status": "info", "message": "No open orders to cancel"},
    )


def test_cancel_all_that_cancelled_every_order_answers_as_before(client, monkeypatch):
    code, body = _cancel_all(client, monkeypatch, [CANCELLED, CANCELLED])
    assert code == 200
    assert body["status"] == "success"
    assert body["message"] == "Successfully canceled 2 orders"


def test_cancel_all_whose_order_book_could_not_be_read_says_nothing_was_cancelled(
    client, monkeypatch
):
    code, body = _cancel_all(client, monkeypatch, [CANCELLED], book_status="error")
    assert code == 424
    assert body["message"].startswith("Nothing was cancelled: the orders could not be read")


# -- The rest of what a send that broke says ---------------------------------------------------


def test_a_cancel_that_breaks_after_its_answer_is_not_confirmed(upstox_answers, monkeypatch):
    monkeypatch.setattr(upstox, "get_api_response", lambda *args, **kwargs: ["not", "a", "dict"])
    body, code = upstox.cancel_order("251021000000001", "token")
    assert code == 500
    assert body == {
        "status": "error",
        "message": upstox._CANCEL_NOT_CONFIRMED,
        "not_confirmed": True,
    }


def test_a_new_smart_order_that_breaks_after_its_send_is_not_confirmed(monkeypatch):
    monkeypatch.setattr(upstox, "get_open_position", lambda *args: "0")
    monkeypatch.setattr(upstox, "place_order_api", lambda data, auth: PLACED)

    def broken(auth):
        raise RuntimeError(SECRET_DETAIL)

    monkeypatch.setattr(upstox, "_invalidate_position_cache", broken)
    res, body, orderid = upstox.place_smartorder_api(
        {**EXIT, "action": "BUY", "quantity": "65", "position_size": "0"}, "token"
    )
    assert res.status == 500
    assert body["not_confirmed"] is True
    assert SECRET_DETAIL not in body["message"]
