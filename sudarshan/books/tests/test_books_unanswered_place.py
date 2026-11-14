"""An order Upstox took whose reply never came back answers 502, and every reader of that answer
reads it as "may have reached Upstox" (upstream 34ce7ca3 in SUDARSHAN, M6.9).

The real services, the real Upstox module and the real shared client transport
(utils/httpx_client._BrokerTransport) run over the simulated Upstox of books_upstox_world.py; only
the socket under the transport is replaced, so the order really is taken (or not) before the reply
is lost. No test here opens a connection.

- unanswered_vectors.json pins the place and the smart-order answers, case by case.
- The readers: sudarshan/books/surface_exits.py (the scalping terminal's closes and the strategy
  module's exits) holds the position on a 502 and sends no second order; the phone's
  sendOutcome.ts reads any 5xx of the app's own as not confirmed (sendOutcome.test.ts).
- A read made under a time limit of its own is never sent a second time (NO_READ_RETRY).
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import httpx
import pytest
from books_upstox_world import Broker, install

from sudarshan.books import exit_guard, surface_exits
from sudarshan.books.single_exit import EXIT_NOT_CONFIRMED
from utils.httpx_client import NO_READ_RETRY, UNANSWERED_ORDER_MESSAGE, _BrokerTransport

X = "NIFTY27OCT2625900CE"
POSITION = ("NFO", X, "MIS")
VECTORS = json.loads((Path(__file__).parent / "unanswered_vectors.json").read_text())

_LOST = {
    "read_error": httpx.ReadError,
    "read_timeout": httpx.ReadTimeout,
    "protocol_error": httpx.RemoteProtocolError,
}


class Wire:
    """The socket under the shared client's transport, served by the simulated Upstox."""

    def __init__(self, broker: Broker):
        self.broker = broker
        self.post = "answered"  # what becomes of a POST to /v3/order/place
        self.post_takes = 0.0  # seconds Upstox takes before the POST's fate
        self.break_reads = 0  # how many GETs break before one is answered
        self.sent: list[str] = []
        self.lock = threading.Lock()

    def handle(self, request: httpx.Request) -> httpx.Response:
        with self.lock:
            self.sent.append(request.method)
        url = str(request.url)
        if request.method == "GET":
            with self.lock:
                breaks = self.break_reads > 0
                self.break_reads -= 1 if breaks else 0
            if breaks:
                raise httpx.ReadError("connection reset", request=request)
            limit = request.extensions.get("timeout", {}).get("read")
            answer = self.broker.get(url, timeout=limit)
            return httpx.Response(answer.status_code, json=answer.json(), request=request)
        if self.post_takes:
            time.sleep(self.post_takes)
        if self.post == "connect_error":
            raise httpx.ConnectError("All connection attempts failed", request=request)
        if self.post == "refused":
            body = {
                "status": "error",
                "errors": [{"errorCode": "UDAPI1052", "message": "The order was rejected."}],
            }
            return httpx.Response(400, json=body, request=request)
        answer = self.broker.post(url, content=request.content)  # Upstox takes the order
        if self.post in _LOST:
            raise _LOST[self.post]("the reply never came back", request=request)
        return httpx.Response(answer.status_code, json=answer.json(), request=request)

    def posts(self) -> int:
        return self.sent.count("POST")


@pytest.fixture
def wire(monkeypatch):
    import restx_api  # noqa: F401, I001  (first, or place_order_service's import cycle fails)

    import broker.upstox.api.order_api as upstox
    import services.place_order_service as place_service

    held = Broker({X: 65}, lag=0.0)
    install(monkeypatch, held)
    on_wire = Wire(held)
    monkeypatch.setattr(
        httpx.HTTPTransport, "handle_request", lambda self, request: on_wire.handle(request)
    )
    client = httpx.Client(transport=_BrokerTransport(), timeout=120.0)
    monkeypatch.setattr(upstox, "get_httpx_client", lambda: client)
    monkeypatch.setattr(place_service, "get_analyze_mode", lambda: False)
    monkeypatch.setattr(surface_exits, "upstox_statuses", lambda auth, broker: {})
    yield on_wire
    client.close()


def _order(action="SELL", quantity="65"):
    return {"apikey": "KEY", "strategy": "S", "symbol": X, "exchange": "NFO", "action": action,
            "quantity": quantity, "product": "MIS", "pricetype": "MARKET", "price": "0",
            "trigger_price": "0", "disclosed_quantity": "0"}  # fmt: skip


def _place():
    from services.place_order_service import place_order

    return place_order(_order(), auth_token="token", broker="upstox")


def _smart_exit():
    from services.place_smart_order_service import place_smart_order

    order = {**_order(quantity="0"), "position_size": "0"}
    return place_smart_order(order, auth_token="token", broker="upstox")


# -- the answers, vector by vector ----------------------------------------------------------------


def test_the_vectors_name_upstream_s_sentence():
    assert VECTORS["unanswered_message"] == UNANSWERED_ORDER_MESSAGE


@pytest.mark.parametrize("case", VECTORS["cases"], ids=lambda case: case["name"])
def test_the_answer_of_a_place_and_of_a_smart_order(wire, case):
    wire.post = case["wire"]
    ok, body, status = _place() if case["path"] == "place" else _smart_exit()

    assert (ok, status) == (case["success"], case["status"])
    if "body" in case:
        want = dict(case["body"])
        if want.get("message") == "<unanswered>":
            want["message"] = UNANSWERED_ORDER_MESSAGE
        assert body == want
    if case.get("message_is_not_unanswered"):
        assert body["status"] == "error" and body["message"] != UNANSWERED_ORDER_MESSAGE
    assert wire.posts() == 1, "an order is sent once, whatever became of its reply"
    assert (len(wire.broker.orders) == 1) is case["reached"]


def test_the_upstox_module_s_own_not_confirmed_sentence_is_kept_with_the_502(wire, monkeypatch):
    """The fork's words win where the fork already gave them: a smart order that broke after its
    send says "This order is not confirmed", and takes upstream's 502 for the lost reply."""
    import broker.upstox.api.order_api as upstox

    def breaks(auth):
        raise RuntimeError("broke after the send")

    wire.post = "read_error"
    monkeypatch.setattr(upstox, "_invalidate_position_cache", breaks)
    ok, body, status = _smart_exit()
    assert (ok, status) == (False, 502)
    assert body == {"status": "error", "message": upstox._SMART_NOT_CONFIRMED["message"]}


# -- the readers ----------------------------------------------------------------------------------


def test_a_502_is_never_a_refusal_to_the_surfaces(wire):
    wire.post = "read_error"
    answer = _place()
    assert answer[2] == 502
    assert surface_exits.refused(answer) is False
    said = surface_exits.not_confirmed(answer)
    assert said[1]["message"] == EXIT_NOT_CONFIRMED and said[1]["not_confirmed"] is True
    assert said[2] == 502


def test_a_scalping_close_whose_reply_was_lost_is_not_confirmed_and_holds_the_position(
    wire, monkeypatch
):
    import blueprints.scalping as scalping

    monkeypatch.setattr(scalping, "_is_derivative", lambda exchange: False)  # no symbol master
    monkeypatch.setattr(scalping, "_current_mode", lambda: "live")

    def close():
        return scalping._reducing_exit(X, "NFO", "MIS", "SELL", 65, "token", "upstox", "KEY")

    wire.post = "read_error"
    ok, body, status = close()
    assert (ok, status) == (False, 502)
    assert body["message"] == EXIT_NOT_CONFIRMED and body["not_confirmed"] is True
    assert exit_guard.EXITS.placed_lately("live") == {POSITION}

    wire.post = "answered"  # Upstox is well again: the second close must still not go
    ok, body, status = close()
    assert (ok, status) == (False, 409) and surface_exits.held(body, status)
    assert wire.posts() == 1 and len(wire.broker.orders) == 1


def _strategy_exit():
    from services.strategy_module import order_dispatch

    order = {k: v for k, v in _order().items() if k != "apikey"}
    return order_dispatch.dispatch_order(mode="live", api_key="KEY", order=order, closes=True)


@pytest.fixture
def strategy(wire, monkeypatch):
    from services.strategy_module import order_dispatch

    monkeypatch.setattr(order_dispatch, "resolve_live_auth", lambda key: ("token", "upstox", None))
    return wire


def test_a_strategy_exit_whose_reply_was_lost_is_held_when_it_is_sent_again(strategy):
    """The engine releases its own leg claim on any failed dispatch and sends the exit again on
    the next tick. After a 502 that next send is a hold (nothing reaches Upstox), not a second
    order beside the one Upstox may hold."""
    strategy.post = "read_error"
    first = _strategy_exit()
    assert first.ok is False and first.held is False
    assert first.error == UNANSWERED_ORDER_MESSAGE

    strategy.post = "answered"
    again = _strategy_exit()
    assert again.ok is False and again.held is True
    assert strategy.posts() == 1 and len(strategy.broker.orders) == 1


def test_two_strategy_exits_released_together_while_the_reply_is_lost_send_one_order(strategy):
    """Barrier-synchronised: one claims the position and waits on Upstox, whose reply is then
    lost (502); the other finds the claim and is held. One order, and the hold stays after."""
    strategy.post = "read_error"
    strategy.post_takes = 0.4
    barrier = threading.Barrier(2)
    results = [None, None]

    def worker(index):
        barrier.wait(5)
        results[index] = _strategy_exit()

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)

    assert all(result is not None and result.ok is False for result in results)
    assert sorted(result.held for result in results) == [False, True]
    assert strategy.posts() == 1 and len(strategy.broker.orders) == 1
    assert len(exit_guard.EXITS) == 0, "the claim is let go once the answer is in"
    assert exit_guard.EXITS.placed_lately("live") == {POSITION}, "and the hold is kept"

    strategy.post = "answered"
    strategy.post_takes = 0.0
    assert _strategy_exit().held is True and strategy.posts() == 1


# -- reads -----------------------------------------------------------------------------------------


def test_a_broken_read_with_no_limit_of_its_own_is_sent_once_more(wire):
    import broker.upstox.api.order_api as upstox

    wire.break_reads = 1
    answer = upstox.get_api_response("/v2/portfolio/short-term-positions", "token")
    assert answer["status"] == "success" and wire.sent == ["GET", "GET"]


def test_a_broken_read_under_a_limit_of_its_own_is_never_sent_a_second_time(wire):
    import broker.upstox.api.order_api as upstox

    assert upstox.read_timeout() == {}
    wire.break_reads = 1
    rows = upstox._read_order_rows("token", exit_guard.EXIT_ORDERS_READ_S)
    assert rows is None, "a read that broke counts as not read"
    assert wire.sent == ["GET"]


def test_the_limit_and_the_mark_ride_on_the_request():
    import broker.upstox.api.order_api as upstox

    token = upstox._READ_LIMIT.set(4.0)
    try:
        assert upstox.read_timeout() == {"timeout": 4.0, "extensions": {NO_READ_RETRY: True}}
    finally:
        upstox._READ_LIMIT.reset(token)
