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


# -- X2: a position converted between products (MIS to NRML in Upstox's app) -----------------------
#
# Upstox's documentation does not say how the positions rows look after a conversion. The order
# book keeps the original BUY 65 under product I. Three shapes are tried for the I row; the D row
# carries the converted 65 as bought today in every one.

_I_ROWS = {
    "I row zeroed": {"quantity": 0, "day_buy_quantity": 0, "day_sell_quantity": 0},
    "I row as a sell": {"quantity": 0, "day_buy_quantity": 65, "day_sell_quantity": 65},
    "I row gone": None,
}


class Converted(Broker):
    """Long 65 NIFTY CE bought MIS today (complete in the order book under I), then converted to
    NRML in Upstox's app. Records the product of every order it is sent."""

    def __init__(self, i_row):
        super().__init__({X: 0}, 0.0)
        self.base_day = {X: (65, 0)}  # the order book lists BUY 65 under product I, complete
        self.i_row = i_row
        self.products: list[tuple[str, str, int]] = []

    def get(self, url, headers=None, **kwargs):
        from books_upstox_world import INSTRUMENTS, ORDERS_URL, FakeResponse

        if url.endswith(ORDERS_URL):
            return self._order_book(kwargs.get("timeout"))
        token = INSTRUMENTS[X][0]
        common = {"instrument_token": token, "exchange": "NFO", "tradingsymbol": X,
                  "average_price": 100.0, "last_price": 101.0, "pnl": 0.0}  # fmt: skip
        rows = []
        if self.i_row is not None:
            rows.append({**common, "product": "I", **self.i_row})
        rows.append({**common, "product": "D", "quantity": 65, "overnight_quantity": 0,
                     "day_buy_quantity": 65, "day_sell_quantity": 0})  # fmt: skip
        return FakeResponse(200, {"status": "success", "data": rows})

    def post(self, url, headers=None, content=None, **kwargs):
        import json

        payload = json.loads(content)
        self.products.append(
            (payload["transaction_type"], payload["product"], int(payload["quantity"]))
        )
        return super().post(url, headers=headers, content=content, **kwargs)


def _app_exit(world, product):
    reply = world._client().post(
        "/close_position", json={"symbol": X, "exchange": "NFO", "product": product}
    )
    return reply.status_code, reply.get_json()


@pytest.mark.parametrize("shape", list(_I_ROWS), ids=list(_I_ROWS))
def test_x2_the_apps_exit_of_a_converted_position_sells_it_in_its_own_product(monkeypatch, shape):
    broker = Converted(_I_ROWS[shape])
    world = install(monkeypatch, broker)
    code, body = _app_exit(world, "NRML")
    assert code == 200, body
    assert broker.products == [("SELL", "D", 65)]


@pytest.mark.parametrize("shape", list(_I_ROWS), ids=list(_I_ROWS))
def test_x2_exit_all_closes_a_converted_position_in_its_own_product(monkeypatch, shape):
    broker = Converted(_I_ROWS[shape])
    world = install(monkeypatch, broker)
    code, body = world.app_exit_all()
    assert (code, body["status"]) == (200, "success"), body
    assert broker.products == [("SELL", "D", 65)]


@pytest.mark.parametrize("shape", list(_I_ROWS), ids=list(_I_ROWS))
def test_x2_the_apps_exit_of_the_old_mis_row_sends_nothing(monkeypatch, shape):
    broker = Converted(_I_ROWS[shape])
    world = install(monkeypatch, broker)
    _code, body = _app_exit(world, "MIS")
    assert body["message"] == NOTHING_OPEN
    assert broker.products == []


@pytest.mark.parametrize("shape", list(_I_ROWS), ids=list(_I_ROWS))
def test_x2_a_tv_exit_set_up_as_mis_sends_nothing_and_says_where_the_position_is(
    monkeypatch, shape
):
    """TradingView's SELL 65 with a position size of 0, set up as MIS, after the position was
    converted: sent as given it is a short in MIS beside the long in NRML."""
    broker = Converted(_I_ROWS[shape])
    world = install(monkeypatch, broker)
    code, body = world.tv_smart_exit(X, quantity="65", action="SELL")
    assert broker.products == []
    assert code == 409
    assert "nothing is held in MIS now, but 65 in NRML is" in body["message"]


def test_x2_a_tv_exit_of_the_converted_position_in_its_own_product_goes(monkeypatch):
    import books_upstox_world as world_module

    broker = Converted(_I_ROWS["I row zeroed"])
    world = install(monkeypatch, broker)
    monkeypatch.setitem(world_module.INSTRUMENTS, X, ("NSE_FO|1001", "NFO", "D", "NRML"))
    code, _body = world.tv_smart_exit(X, quantity="65", action="SELL")
    assert code == 200
    assert broker.products == [("SELL", "D", 65)]


# -- X7: a smart order to a size other than 0 is sized from the corrected figure ---------------------


def _reversal(world, symbol, quantity, action, size):
    return world.tv_smart_exit(symbol, quantity=quantity, action=action, position_size=size)


def test_x7_a_tv_reversal_after_the_app_exit_lands_on_its_target(monkeypatch, clock):
    """Long 65; the owner taps Exit (fills; the row lags). His strategy then reverses: SELL 130
    with a position size of -65. Sized from the row it would sell 130 and end short 130."""
    world = install(monkeypatch, Broker({X: 65}, 60.0))
    world.phone_exit(X)
    clock.advance(2.0)
    _fresh_book()
    code, body = _reversal(world, X, "130", "SELL", "-65")
    assert code == 200, body
    assert world.broker.true_qty(X) == -65
    assert _sent(world.broker, X) == [("SELL", 65), ("SELL", 65)]


def test_x7_a_partial_exit_repeated_while_the_row_lags_lands_on_its_target(monkeypatch, clock):
    world = install(monkeypatch, Broker({X: 130}, 60.0))
    assert _reversal(world, X, "65", "SELL", "65")[0] == 200
    clock.advance(1.0)
    _fresh_book()
    _reversal(world, X, "65", "SELL", "65")  # the same alert again
    assert world.broker.true_qty(X) == 65


def test_x7_a_short_reversed_to_long_after_its_exit_lands_on_its_target(monkeypatch, clock):
    world = install(monkeypatch, Broker({Y: -30}, 60.0))
    world.phone_exit(Y)
    clock.advance(2.0)
    _fresh_book()
    code, _body = _reversal(world, Y, "60", "BUY", "30")
    assert code == 200
    assert world.broker.true_qty(Y) == 30


def test_x7_an_order_that_cannot_be_sized_is_refused_when_it_reduces_or_crosses(monkeypatch):
    broker = Broker({X: 65}, 0.0)
    broker.orders_unreadable = True
    world = install(monkeypatch, broker)
    for quantity, size in (("130", "-65"), ("65", "0"), ("30", "35")):
        code, body = _reversal(world, X, quantity, "SELL", size)
        assert code == 424, (size, body)
        assert body["message"] == exit_guard.exit_not_sized(("NFO", X, "MIS"))
    assert broker.orders == []


def test_x7_an_order_that_only_adds_reads_no_order_book(monkeypatch):
    broker = Broker({X: 65, Z: 0}, 0.0)
    world = install(monkeypatch, broker)
    assert _reversal(world, X, "65", "BUY", "130")[0] == 200  # adds to a long
    assert _reversal(world, Z, "10", "BUY", "10")[0] == 200  # opens from flat
    assert broker.order_book_reads == 0
    assert broker.true_qty(X) == 130 and broker.true_qty(Z) == 10


def test_x7_an_order_that_only_adds_goes_when_the_order_book_cannot_be_read(monkeypatch):
    broker = Broker({X: 65}, 0.0)
    broker.orders_unreadable = True
    world = install(monkeypatch, broker)
    assert _reversal(world, X, "65", "BUY", "130")[0] == 200
    assert broker.true_qty(X) == 130
