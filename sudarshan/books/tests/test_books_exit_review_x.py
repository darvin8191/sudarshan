"""The v0.4.1 exits review's findings X1, X2, X7 and L2, over the simulated Upstox (M6.9, M5.13).

TradingView's real shape is used throughout: ``quantity`` is the contracts of the order and
``position_size`` the size after it, so its exit is SELL 65 with a position size of 0, never a
quantity of "0". The app's Exit and Flow's close send a quantity of "0".
"""

from __future__ import annotations

import time

import pytest
from books_upstox_world import Broker, install

from sudarshan.books import exit_guard, fills

X = "NIFTY27OCT2625900CE"  # MIS
Y = "BANKNIFTY27OCT2657500PE"  # NRML
Z = "SBIN"  # MIS

NOTHING_OPEN = "No open position found. Not placing exit order."


@pytest.fixture
def clock(monkeypatch):
    real = time.monotonic
    offset = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: real() + offset[0])

    class Clock:
        @staticmethod
        def advance(seconds: float) -> None:
            offset[0] += seconds

    return Clock()


def _fresh_book():
    import broker.upstox.api.order_api as upstox

    upstox._POSITION_BOOK.invalidate("token")


def _sent(broker, symbol):
    return [(o["side"], o["qty"]) for o in broker.orders if o["symbol"] == symbol]


# -- X1: a smart order to size 0 never opens a position by accident ------------------------------


@pytest.mark.parametrize(
    "lag,after",
    [(60.0, 2.0), (60.0, 20.0), (0.0, 2.0)],
    ids=["row 60 s behind, 2 s later", "row 60 s behind, 20 s later", "row up to date, 2 s later"],
)
def test_x1_a_tv_exit_with_its_contracts_after_the_app_exit_filled_sends_nothing(
    monkeypatch, clock, lag, after
):
    world = install(monkeypatch, Broker({X: 65}, lag))
    world.phone_exit(X)
    clock.advance(after)
    _fresh_book()
    code, body = world.tv_smart_exit(X, quantity="65", action="SELL")
    assert body["message"] == NOTHING_OPEN
    assert _sent(world.broker, X) == [("SELL", 65)]
    assert world.broker.true_qty(X) == 0


@pytest.mark.parametrize("lag", [60.0, 0.0])
def test_x1_a_repeated_tv_exit_alert_sends_nothing(monkeypatch, clock, lag):
    world = install(monkeypatch, Broker({X: 65}, lag))
    first = world.tv_smart_exit(X, quantity="65", action="SELL")
    clock.advance(1.0)
    _fresh_book()
    second = world.tv_smart_exit(X, quantity="65", action="SELL")
    assert first[0] == 200
    assert second[1]["message"] == NOTHING_OPEN
    assert _sent(world.broker, X) == [("SELL", 65)]
    assert world.broker.true_qty(X) == 0


@pytest.mark.parametrize(
    "symbol,held,action",
    [(X, 65, "SELL"), (Y, -30, "BUY")],
    ids=["long closed by SELL", "short closed by BUY"],
)
@pytest.mark.parametrize("lag", [60.0, 0.0])
def test_x1_both_sides_the_repeat_of_the_exit_that_flattened_it_sends_nothing(
    monkeypatch, clock, symbol, held, action, lag
):
    world = install(monkeypatch, Broker({symbol: held}, lag))
    world.phone_exit(symbol)
    clock.advance(2.0)
    _fresh_book()
    code, body = world.tv_smart_exit(symbol, quantity=str(abs(held)), action=action)
    assert body["message"] == NOTHING_OPEN
    assert len(world.broker.orders) == 1
    assert world.broker.true_qty(symbol) == 0


def test_x1_a_tv_exit_while_exit_all_is_still_placing_is_refused_not_placed(monkeypatch, clock):
    broker = Broker({X: 65, Y: -30}, 60.0)
    world = install(monkeypatch, broker)
    broker.hang[Y] = 1.5  # Y's exit takes 1.5 s at Upstox
    thread, out = world.background(world.app_exit_all)
    deadline = time.time() + 5
    while not _sent(broker, X) and time.time() < deadline:
        time.sleep(0.02)
    time.sleep(0.2)
    _fresh_book()
    code, body = world.tv_smart_exit(X, quantity="65", action="SELL")
    thread.join(10)
    assert code == 409
    assert body["message"] == exit_guard.EXIT_ALL_BEING_PLACED
    assert _sent(broker, X) == [("SELL", 65)]
    assert broker.true_qty(X) == 0


def test_x1_the_broker_answers_a_repeated_exit_as_it_does_an_exit_with_nothing_open(monkeypatch):
    import broker.upstox.api.order_api as upstox

    install(monkeypatch, Broker({X: 65}, 0.0))
    exit_order = {"symbol": X, "exchange": "NFO", "product": "MIS", "pricetype": "MARKET",
                  "quantity": "65", "action": "SELL", "position_size": "0"}  # fmt: skip
    first = upstox.place_smartorder_api(exit_order, "token")
    _fresh_book()
    res, body, orderid = upstox.place_smartorder_api(exit_order, "token")
    assert first[2] and (res, orderid) == (None, None)
    assert body == {"status": "success", "message": NOTHING_OPEN}


def test_x1_an_opposite_side_entry_right_after_an_exit_is_still_placed(monkeypatch, clock):
    """The fork's s15, with the row lagging as well: the last fill was the exit's SELL, so a BUY
    with a position size of 0 is upstream's fresh order."""
    world = install(monkeypatch, Broker({Z: 10}, 60.0))
    world.phone_exit(Z)
    clock.advance(2.0)
    _fresh_book()
    code, body = world.tv_smart_exit(Z, quantity="10", action="BUY")
    assert (code, body["status"]) == (200, "success")
    assert _sent(world.broker, Z) == [("SELL", 10), ("BUY", 10)]


def test_x1_a_flat_position_with_no_fill_today_places_the_order_as_given(monkeypatch):
    world = install(monkeypatch, Broker({Z: 0}, 0.0))
    code, body = world.tv_smart_exit(Z, quantity="10", action="BUY")
    assert (code, body["status"]) == (200, "success")
    assert _sent(world.broker, Z) == [("BUY", 10)]


def _order(**fields):
    return {
        "instrument_token": "T",
        "product": "I",
        "transaction_type": "SELL",
        "filled_quantity": 65,
        "order_timestamp": "2026-10-27 09:20:00",
        **fields,
    }


def test_x1_the_last_fill_decides_by_its_timestamp_not_its_place_in_the_list():
    early_sell = _order(order_timestamp="2026-10-27 09:20:00")
    late_buy = _order(transaction_type="BUY", order_timestamp="2026-10-27 10:00:00")
    assert fills.repeats_last_fill([late_buy, early_sell], "T", "I", "BUY") is True
    assert fills.repeats_last_fill([early_sell, late_buy], "T", "I", "SELL") is False


def test_x1_without_a_usable_timestamp_nothing_is_sent():
    for stamp in (None, "", "yesterday"):
        rows = [_order(order_timestamp=stamp)]
        assert fills.repeats_last_fill(rows, "T", "I", "BUY") is True, stamp


def test_x1_orders_that_filled_nothing_or_belong_elsewhere_do_not_count():
    rows = [
        _order(filled_quantity=0, order_timestamp=None),
        _order(instrument_token="OTHER", order_timestamp=None),
        _order(product="D", order_timestamp=None),
    ]
    assert fills.repeats_last_fill(rows, "T", "I", "SELL") is False
