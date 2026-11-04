"""Gate review M5 (the coordinator's decision b): a risk refusal of an order that did not come
from the app's own screens is told on Telegram, merged per symbol and reason per minute; a
refusal that left an exit unsent says which."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from flask import Flask, session
from test_guard_gate import (  # noqa: F401 - clock, the_gate, book: fixtures
    CE,
    ON,
    PE,
    book,
    broker,
    clock,
    order,
    row,
    the_gate,
)
from test_guard_gate_size import services, sized  # noqa: F401 - fixtures

from sudarshan.books import telegram_words
from sudarshan.guard import gate, words


@pytest.fixture
def heard(monkeypatch):
    told: list[str] = []
    monkeypatch.setattr(gate, "_tell_owner", told.append)
    return told


def _over_the_cap(upstox):
    return upstox.place(order("BUY", 195, symbol=PE), "token")


def test_an_order_from_outside_the_app_is_refused_in_words_and_marked_to_be_told(sized, clock):
    upstox = broker()
    res, body, _ref = _over_the_cap(upstox)
    assert res.status == 403 and body["nothing_sent"] is True
    assert "Refused" not in body["message"]  # the first of its kind


def test_those_like_it_in_the_next_minute_are_counted_and_told_with_the_next(sized, clock):
    upstox = broker()
    _over_the_cap(upstox)
    clock.now += 5
    second = _over_the_cap(upstox)[1]
    assert "nothing_sent" not in second  # told a moment ago: counted, not told again
    clock.now += gate.TOLD_EVERY_S
    third = _over_the_cap(upstox)[1]
    assert third["nothing_sent"] is True
    assert third["message"].endswith(" Refused 1 more time like this since the last message.")


def test_another_symbol_is_told_at_once(sized, clock):
    upstox = broker()
    _over_the_cap(upstox)
    other = upstox.place(order("BUY", 195, symbol=CE), "token")[1]
    assert other["nothing_sent"] is True


def test_an_order_from_the_app_s_own_screens_is_not_told(sized, clock):
    app = Flask("told")
    app.secret_key = "test"
    with app.test_request_context("/api/v1/placeorder", method="POST"):
        session["user"] = "owner"  # the owner's logged in session: the phone's ticket
        body = _over_the_cap(broker())[1]
    assert "nothing_sent" not in body


def test_a_refused_gtt_is_told_through_telegram_at_once(sized, clock, heard):
    upstox = broker()
    refused = upstox.gtt_place(order("BUY", 195, symbol=PE, trigger_type="SINGLE"), "token")
    assert refused[1]["status"] == "error" or refused[0] is not None
    assert len(heard) == 1 and heard[0].startswith("Order not sent (Settings > Risk)\n")
    upstox.gtt_place(order("BUY", 195, symbol=PE, trigger_type="SINGLE"), "token")
    assert len(heard) == 1  # merged within the minute


def test_the_place_order_service_passes_it_to_the_failure_event(
    services, sized, clock, monkeypatch
):
    from services import place_order_service

    events = []
    monkeypatch.setattr(place_order_service, "bus", SimpleNamespace(publish=events.append))
    data = {**order("BUY", 195, symbol=PE), "apikey": "key", "strategy": "s"}
    ok, body, status = place_order_service.place_order_with_auth(data, "token", "upstox", data)
    assert (ok, status) == (False, 403)
    assert [event.response_data.get("nothing_sent") for event in events] == [True]


def test_a_basket_refused_whole_is_told_and_names_the_exit_it_did_not_send(
    services, sized, clock, monkeypatch
):
    events = []
    monkeypatch.setattr("utils.event_bus.bus.publish", events.append)
    sized.book.rows = [row(CE, 65)]
    data = {"apikey": "key", "strategy": "roll",
            "orders": [order("SELL", 65, symbol=CE), order("BUY", 195, symbol=PE)]}  # fmt: skip
    ok, body, status = services.basket.process_basket_order_with_auth(data, "token", "upstox", data)
    assert (ok, status) == (False, 403) and services.reached == []
    assert "exit was not sent either" in body["message"] and "Order 1" in body["message"]
    assert [event.response_data.get("nothing_sent") for event in events] == [True]
    assert events[0].response_data["message"] == body["message"]


def test_the_alert_says_what_was_asked_and_why_it_was_not_sent():
    response = {"status": "error", "message": "This order is 3 lots. Nothing was sent.",
                "nothing_sent": True}  # fmt: skip
    asked = {"symbol": PE, "exchange": "NFO", "action": "BUY", "quantity": "195"}
    lines = telegram_words.not_sent_lines("placeorder", asked, response)
    assert lines[-1] == "This order is 3 lots. Nothing was sent."
    assert f"Symbol: `{PE}`" in lines and "Asked: BUY 195" in lines
    template = telegram_words.alert_template("placeorder", asked, response, "Placed\n{details}")
    assert template.startswith("*Order not sent*")
    assert telegram_words.not_sent_lines("placeorder", asked, {"status": "error"}) is None


def test_the_words_for_those_not_told_yet():
    assert words.refused_again(0) == "" and "2 more times" in words.refused_again(2)


def test_an_order_refused_for_an_unreadable_book_under_the_switch_is_told(
    the_gate, book, clock, heard
):
    """The fail-closed decision d, and M5: told on Telegram, from a webhook or a script."""
    the_gate.set_state(ON)
    book.fail = {"rows"}
    body = broker().place(order("SELL"), "token")[1]
    assert body["nothing_sent"] is True and "urgent exit, use the Upstox app" in body["message"]
