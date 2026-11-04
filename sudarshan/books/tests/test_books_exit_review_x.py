"""The v0.4.1 exits review's findings X1, X2, X7 and L2, over the simulated Upstox (M6.9, M5.13).

TradingView's real shape is used throughout: ``quantity`` is the contracts of the order and
``position_size`` the size after it, so its exit is SELL 65 with a position size of 0, never a
quantity of "0". The app's Exit and Flow's close send a quantity of "0".
"""

from __future__ import annotations

import time

import pytest
from books_upstox_world import INSTRUMENTS, Broker, install

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
    assert body == {"status": "success", "message": NOTHING_OPEN, "nothing_sent": True}


def test_y1_a_size_0_order_on_the_side_opposite_to_the_last_fill_sends_nothing(monkeypatch, clock):
    """Flat after the owner's exit (a SELL); TradingView's size-0 BUY is not an entry, because
    its entries carry the size after the order. It is a stray exit and opens nothing."""
    world = install(monkeypatch, Broker({Z: 10}, 60.0))
    world.phone_exit(Z)
    clock.advance(2.0)
    _fresh_book()
    code, body = world.tv_smart_exit(Z, quantity="10", action="BUY")
    assert body["message"] == NOTHING_OPEN
    assert _sent(world.broker, Z) == [("SELL", 10)]
    assert world.broker.true_qty(Z) == 0


@pytest.mark.parametrize("action", ["BUY", "SELL"])
def test_y1_a_flat_position_with_no_fill_today_sends_nothing(monkeypatch, action):
    """The entry that this exit belongs to was refused or never filled."""
    world = install(monkeypatch, Broker({Z: 0}, 0.0))
    code, body = world.tv_smart_exit(Z, quantity="10", action=action)
    assert body["message"] == NOTHING_OPEN
    assert world.broker.orders == []


class StopModified(Broker):
    """Flat. 10:00 BUY 65; 10:01 a stop SELL 65 SL-M is placed; 10:30 a second BUY 65 and the stop
    is modified to 130; 11:00 the stop fills. The order book dates the stop 10:01 (when it was
    placed), so it looks older than the add it closed."""

    def _filled_rows(self):
        token, exchange, product, _oa = INSTRUMENTS[X]
        base = {"instrument_token": token, "exchange": exchange, "product": product,
                "tradingsymbol": X, "status": "complete", "pending_quantity": 0,
                "price": 0, "trigger_price": 0}  # fmt: skip
        return [
            {**base, "order_id": "1", "transaction_type": "BUY", "order_type": "MARKET",
             "quantity": 65, "filled_quantity": 65, "order_timestamp": "2026-10-27 10:00:00"},
            {**base, "order_id": "2", "transaction_type": "SELL", "order_type": "SL-M",
             "quantity": 130, "filled_quantity": 130, "trigger_price": 98.5,
             "order_timestamp": "2026-10-27 10:01:00"},
            {**base, "order_id": "3", "transaction_type": "BUY", "order_type": "MARKET",
             "quantity": 65, "filled_quantity": 65, "order_timestamp": "2026-10-27 10:30:00"},
        ]  # fmt: skip


@pytest.mark.parametrize("action", ["SELL", "BUY"])
def test_y1_flat_after_a_stop_placed_before_an_add_and_modified_to_cover_it_sends_nothing(
    monkeypatch, action
):
    broker = StopModified({X: 0}, 0.0)
    broker.base_day[X] = (130, 130)
    world = install(monkeypatch, broker)
    code, body = world.tv_smart_exit(X, quantity="130", action=action)
    assert body["message"] == NOTHING_OPEN
    assert broker.orders == []


def test_y1_the_flat_answer_is_a_sentence_for_telegram(monkeypatch):
    """The service marks the answer nothing_sent, so Telegram tells the owner, in plain words."""
    from sudarshan.books import telegram_words

    order = {"symbol": X, "action": "SELL", "quantity": "65", "position_size": "0"}
    answer = {"status": "error", "message": NOTHING_OPEN, "nothing_sent": True}
    title = telegram_words.alert_template("placesmartorder", order, answer, "unused")
    lines = telegram_words.smart_order_action_lines(order, answer)
    assert title.startswith("*Smart order not sent*")
    assert lines == ["Action: nothing sent", "No open position found. Not placing exit order."]


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
        assert body["message"] == exit_guard.exit_not_sized(("NFO", X, "MIS"), orders_read=False)
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


# -- L2: a working market order on the closing side holds a single exit -------------------------------


@pytest.mark.parametrize("how", ["app restarted", "answer named no order"])
def test_l2_a_resting_first_exit_still_blocks_a_second_after_a_restart_or_an_unnamed_answer(
    monkeypatch, clock, how
):
    """The app's Exit is accepted and rests at Upstox (a market order held at its protected
    price). Then the app restarts (the holds live in memory only) or the first answer had named
    no order id, and 20 s pass. TradingView's exit is sized from the fills (nothing filled: 65)
    and must not go beside it."""
    world = install(monkeypatch, Broker({X: 65}, 0.0))
    world.broker.part[X] = 0
    code1, _ = world.phone_exit(X)
    monkeypatch.setattr(exit_guard, "EXITS", exit_guard.ExitsInFlight())
    if how == "answer named no order":
        exit_guard.exit_placed("live", "NFO", X, "MIS")  # the note an unnamed answer leaves
    clock.advance(20.0)
    _fresh_book()
    code2, body2 = world.tv_smart_exit(X)
    assert code1 == 200 and code2 == 409
    message = body2["message"]
    assert "SELL 65 MARKET order" in message and "Orders" in message
    assert "wait for it" in message.lower() and "cancel" in message.lower()
    assert _sent(world.broker, X) == [("SELL", 65)]


@pytest.mark.parametrize("kind", ["SL-M", "SL", "LIMIT"])
def test_l2_a_resting_stop_or_target_does_not_block_a_single_exit(monkeypatch, kind):
    world = install(monkeypatch, Broker({X: 65}, 0.0))
    world.broker.rest(X, "SELL", 65, kind=kind, trigger=98.5 if kind != "LIMIT" else 0)
    code, _body = world.phone_exit(X)
    assert code == 200
    assert _sent(world.broker, X) == [("SELL", 65)]


def test_l2_a_working_market_order_on_the_other_side_does_not_block_an_exit(monkeypatch):
    """A market BUY still working on a long is an add, not a way out of it."""
    world = install(monkeypatch, Broker({X: 65}, 0.0))
    world.broker.rest(X, "BUY", 65, kind="MARKET", trigger=0)
    code, _body = world.phone_exit(X)
    assert code == 200
    assert _sent(world.broker, X) == [("SELL", 65)]


# -- Y2: a reversal or part exit is refused while a market order works on the closing side -----------


@pytest.mark.parametrize(
    "symbol,held,quantity,action,size",
    [(X, 65, "130", "SELL", "-65"), (Y, -30, "60", "BUY", "30"), (X, 130, "65", "SELL", "65")],
    ids=["long reversed to short", "short reversed to long", "part exit of a long"],
)
def test_y2_a_repeated_order_that_reduces_or_crosses_is_refused_while_the_first_rests(
    monkeypatch, clock, symbol, held, quantity, action, size
):
    world = install(monkeypatch, Broker({symbol: held}, 0.0))
    world.broker.part[symbol] = 0  # the first rests at its protected price, nothing filled
    first = _reversal(world, symbol, quantity, action, size)
    clock.advance(2.0)
    _fresh_book()
    code, body = _reversal(world, symbol, quantity, action, size)
    assert first[0] == 200
    assert code == 409
    assert f"{action} {quantity} MARKET order" in body["message"]
    assert "still working at Upstox" in body["message"] and "Orders" in body["message"]
    assert _sent(world.broker, symbol) == [(action, int(quantity))]


@pytest.mark.parametrize("kind", ["SL-M", "SL", "LIMIT"])
def test_y2_a_resting_stop_or_target_does_not_block_a_reversal(monkeypatch, kind):
    world = install(monkeypatch, Broker({X: 65}, 0.0))
    world.broker.rest(X, "SELL", 65, kind=kind, trigger=98.5 if kind != "LIMIT" else 0)
    code, _body = _reversal(world, X, "130", "SELL", "-65")
    assert code == 200
    assert _sent(world.broker, X) == [("SELL", 130)]


def test_y2_a_market_order_on_the_other_side_does_not_block_a_reversal(monkeypatch):
    world = install(monkeypatch, Broker({X: 65}, 0.0))
    world.broker.rest(X, "BUY", 65, kind="MARKET", trigger=0)
    code, _body = _reversal(world, X, "130", "SELL", "-65")
    assert code == 200
    assert _sent(world.broker, X) == [("SELL", 130)]


def test_y2_an_order_that_only_adds_reads_no_order_book_whatever_is_working(monkeypatch):
    broker = Broker({X: 65}, 0.0)
    broker.rest(X, "SELL", 65, kind="MARKET", trigger=0)
    world = install(monkeypatch, broker)
    assert _reversal(world, X, "65", "BUY", "130")[0] == 200
    assert broker.order_book_reads == 0


# -- Y3: Exit all says what it left out when the positions do not list an instrument yet --------------

UNLISTED = (
    "BANKNIFTY 57500 PE was traded today but Upstox's positions do not show it yet, so Exit all "
    "did not close it. Close it from the Upstox app, or tap Exit all again in a minute."
)


def _bought_not_listed(world, symbol, quantity):
    """A fill in the order book of an instrument the positions book has no row for yet."""
    world.broker.fills.append((time.monotonic(), symbol, quantity))


def test_y3_exit_all_leaves_out_an_instrument_the_positions_do_not_list_and_says_so(monkeypatch):
    world = install(monkeypatch, Broker({X: 65}, 0.0))
    _bought_not_listed(world, Y, 30)
    code, body = world.app_exit_all()
    assert _sent(world.broker, X) == [("SELL", 65)] and _sent(world.broker, Y) == []
    assert code != 200 and body.get("status") != "success"
    assert UNLISTED in body["message"]
    assert "All Open Positions" not in body["message"]
    assert any(p["symbol"] == Y and p["outcome"] == "not_sent" for p in body["positions"])


def test_y3_the_same_for_a_short_that_is_not_listed(monkeypatch):
    world = install(monkeypatch, Broker({X: 65}, 0.0))
    _bought_not_listed(world, Y, -30)
    code, body = world.app_exit_all()
    assert UNLISTED in body["message"]
    assert _sent(world.broker, Y) == []


def test_y3_an_unlisted_instrument_that_nets_to_zero_is_not_mentioned(monkeypatch):
    world = install(monkeypatch, Broker({X: 65}, 0.0))
    _bought_not_listed(world, Y, 30)
    _bought_not_listed(world, Y, -30)
    code, body = world.app_exit_all()
    assert code == 200 and "traded today" not in str(body)


@pytest.fixture
def cnc(monkeypatch):
    from books_upstox_world import BY_TOKEN
    from books_upstox_world import INSTRUMENTS as world_instruments

    token = "NSE_EQ|INE002A01018"
    monkeypatch.setitem(world_instruments, "RELIANCE", (token, "NSE", "D", "CNC"))
    monkeypatch.setitem(BY_TOKEN, token, "RELIANCE")
    return "RELIANCE"


def test_y3_a_cnc_net_sale_from_holdings_is_not_an_open_position(monkeypatch, cnc):
    world = install(monkeypatch, Broker({X: 65}, 0.0))
    _bought_not_listed(world, cnc, -10)
    code, body = world.app_exit_all()
    assert code == 200 and "traded today" not in str(body)
    assert _sent(world.broker, X) == [("SELL", 65)]


def test_y3_a_cnc_net_purchase_is_left_out_with_its_sentence(monkeypatch, cnc):
    world = install(monkeypatch, Broker({X: 65}, 0.0))
    _bought_not_listed(world, cnc, 10)
    code, body = world.app_exit_all()
    assert "RELIANCE was traded today but Upstox's positions do not show it yet" in body["message"]
    assert _sent(world.broker, cnc) == []


def _row(token, product, quantity, bought, sold):
    return {"instrument_token": token, "product": product, "quantity": quantity,
            "overnight_quantity": 0, "day_buy_quantity": bought, "day_sell_quantity": sold}  # fmt: skip


def _fill(token, product, side, quantity, name="N"):
    return {"instrument_token": token, "product": product, "transaction_type": side,
            "filled_quantity": quantity, "exchange": "NFO", "tradingsymbol": name}  # fmt: skip


def test_y3_unlisted_nets_by_instrument_and_product():
    orders = [_fill("T", "I", "BUY", 100), _fill("T", "I", "SELL", 40), _fill("U", "D", "SELL", 5)]
    assert fills.unlisted_nets([], orders) == [
        ("NFO", "N", "T", "I", 60),
        ("NFO", "N", "U", "D", -5),
    ]
    assert fills.unlisted_nets([], None) == []
    assert fills.unlisted_nets([], [_fill("T", "I", "BUY", 0)]) == []


def test_y3_a_listed_row_or_a_conversion_is_not_unlisted():
    orders = [_fill("T", "I", "BUY", 65)]
    assert fills.unlisted_nets([_row("T", "I", 65, 65, 0)], orders) == []
    # converted to NRML: the I row is gone, the D row carries the 65
    assert fills.unlisted_nets([_row("T", "D", 65, 65, 0)], orders) == []


EMPTY_BOOK_UNREAD = (
    "Upstox's positions show nothing open, but its order list did not come in time, so a "
    "position opened in the last minute may not show yet. Check Positions in a minute."
)


def test_y3_exit_all_with_an_empty_positions_book_names_what_the_fills_show(monkeypatch):
    world = install(monkeypatch, Broker({}, 0.0))
    _bought_not_listed(world, Y, 30)
    code, body = world.app_exit_all()
    assert code != 200 and body.get("status") != "success"
    assert UNLISTED in body["message"]
    assert world.broker.orders == []


def test_y3_exit_all_with_an_empty_positions_book_and_no_fills_finds_nothing_open(monkeypatch):
    import broker.upstox.api.order_api as upstox

    world = install(monkeypatch, Broker({}, 0.0))
    body, code = upstox.close_all_positions("KEY", "token")
    assert (code, body["message"]) == (200, "No Open Positions Found")
    assert world.broker.orders == []


def test_y3_exit_all_with_an_empty_positions_book_and_a_cnc_net_sale_finds_nothing_open(
    monkeypatch, cnc
):
    import broker.upstox.api.order_api as upstox

    world = install(monkeypatch, Broker({}, 0.0))
    _bought_not_listed(world, cnc, -10)
    body, code = upstox.close_all_positions("KEY", "token")
    assert (code, body["message"]) == (200, "No Open Positions Found")


def test_y3_exit_all_with_an_empty_positions_book_and_no_order_list_says_so(monkeypatch):
    broker = Broker({}, 0.0)
    broker.orders_unreadable = True
    world = install(monkeypatch, broker)
    code, body = world.app_exit_all()
    assert code == 424 and body["status"] == "error"
    assert body["message"] == EMPTY_BOOK_UNREAD
    assert broker.orders == []


def test_z2_a_flat_position_with_its_own_exit_still_working_says_so(monkeypatch, clock):
    """The app's Exit rests at Upstox; the owner then sells in Upstox's own app and that fills, so
    the position is flat and SUDARSHAN's SELL still works. TradingView's exit must be told about
    that order (it can still open a short), not that nothing is open."""
    world = install(monkeypatch, Broker({X: 65}, 0.0))
    world.broker.part[X] = 0
    assert world.phone_exit(X)[0] == 200
    world.broker.fills.append((time.monotonic(), X, -65))  # sold in Upstox's app
    clock.advance(20.0)
    _fresh_book()
    code, body = world.tv_smart_exit(X, quantity="65", action="SELL")
    assert code == 409
    assert "still working at Upstox" in body["message"] and "Orders" in body["message"]
    assert body["message"] != NOTHING_OPEN
    assert _sent(world.broker, X) == [("SELL", 65)]


def test_z3_the_unlisted_sentence_names_the_contract_from_its_openalgo_symbol(monkeypatch):
    import broker.upstox.api.order_api as upstox

    raw = {
        **_fill("NSE_FO|1002", "D", "BUY", 30, "BANKNIFTY 57500 PE 27 OCT 26"),
        "exchange": "NFO",
    }
    monkeypatch.setattr(upstox, "get_symbol", lambda token, exchange: Y)
    skipped, outcomes = upstox._unlisted_left_out([], [raw])
    assert skipped == [UNLISTED] and outcomes[0]["symbol"] == Y
    monkeypatch.setattr(upstox, "get_symbol", lambda token, exchange: None)  # lookup finds nothing
    skipped, _ = upstox._unlisted_left_out([], [raw])
    assert skipped[0].startswith("BANKNIFTY 57500 PE 27 OCT 26 was traded today")


# -- Y4: positions rows with no instrument token cannot be matched to the order book ---------------


@pytest.mark.parametrize("token", [None, "", "  "])
def test_y4_rows_that_carry_no_token_are_not_flat(monkeypatch, token):
    import broker.upstox.api.order_api as upstox

    row = _row(token, "I", 65, 65, 0)
    assert upstox._held_products([row], X, "NFO", []) is None
    assert upstox._held_products(
        [_row("T", "I", 65, 65, 0)], X, "NFO", [_fill("T", "I", "BUY", 65)]
    ) == {"I": 65}


def test_y4_the_apps_exit_of_a_row_with_no_token_says_it_could_not_be_worked_out(monkeypatch):
    import broker.upstox.api.order_api as upstox

    world = install(monkeypatch, Broker({X: 65}, 0.0))
    row = {**_row(None, "I", 65, 65, 0), "tradingsymbol": X, "exchange": "NFO"}
    monkeypatch.setattr(upstox, "_position_rows", lambda *args: [row])
    code, body = world.phone_exit(X)
    assert code == 424
    assert body["message"] == exit_guard.exit_not_sized(("NFO", X, "MIS"), orders_read=True)
    assert world.broker.orders == []


def test_low_the_held_in_another_product_sentence_does_not_guess_the_cause():
    one = exit_guard.held_in_another_product(("NFO", X, "MIS"), {"NRML": 65})
    assert one == (
        "No exit was sent for NIFTY 25900 CE as MIS: nothing is held in MIS now, but 65 in NRML "
        "is. Send the exit for NRML, or close it from the Upstox app."
    )
    assert "converted" not in one
    two = exit_guard.held_in_another_product(("NFO", X, "MIS"), {"NRML": -65, "CNC": 10})
    assert "but 65 in NRML and 10 in CNC are." in two and "for NRML or CNC," in two


# -- Words (X6, L4) -----------------------------------------------------------------------------------


def test_words_upstox_s_refusal_keeps_its_message_and_loses_its_code():
    import broker.upstox.api.order_api as upstox

    class Refused:
        status = 400

    response = {
        "status": "error",
        "message": "UDAPI100050: Invalid token used | UDAPI1040: Bad price",
    }
    outcome = upstox._exit_outcome(("NFO", X, "MIS"), "refused", Refused(), response, None)
    assert outcome["reason"] == "Upstox refused its exit: Invalid token used | Bad price"
    assert "UDAPI" not in outcome["reason"]


def test_words_exit_all_s_summary_carries_the_refusal_without_its_code(monkeypatch):
    import broker.upstox.api.order_api as upstox

    class Refused:
        status = 400
        status_code = 400

    broker = Broker({X: 65}, 0.0)
    install(monkeypatch, broker)
    refusal = {"status": "error", "message": "UDAPI100050: Invalid token used"}
    monkeypatch.setattr(upstox, "place_order_api", lambda data, auth: (Refused(), refusal, None))
    body, code = upstox.close_all_positions("key", "token")
    assert code == 400
    assert "1 was refused (Invalid token used)" in body["message"]
    assert "UDAPI" not in str(body)


def test_words_a_single_exit_says_the_order_list_did_not_come_and_to_use_the_upstox_app(
    monkeypatch,
):
    broker = Broker({X: 65}, 0.0)
    broker.orders_unreadable = True
    world = install(monkeypatch, broker)
    code, body = world.phone_exit(X)
    assert code == 424 and broker.orders == []
    assert body["message"] == (
        "Upstox did not send your orders in time, so SUDARSHAN could not confirm how much you "
        "hold in NIFTY 25900 CE and sent no exit. Close it from the Upstox app, or try again in "
        "a minute."
    )


def test_words_exit_all_with_no_order_list_sends_nothing_and_does_not_say_an_order_was_not_looked_for(
    monkeypatch,
):
    broker = Broker({X: 65, Y: -30}, 0.0)
    broker.orders_unreadable = True
    world = install(monkeypatch, broker)
    code, body = world.app_exit_all()
    assert code == 409 and broker.orders == []
    assert "not looked for" not in body["message"] and "Check Orders." not in body["message"]
    assert body["message"].count("sent no exit. Close it from the Upstox app") == 2


def test_words_an_exit_held_by_a_working_order_names_it_and_says_to_cancel_or_modify_it(
    monkeypatch,
):
    broker = Broker({X: 65}, 0.0)
    broker.part[X] = 0  # the first exit rests at Upstox
    world = install(monkeypatch, broker)
    world.phone_exit(X)
    code, body = world.phone_exit(X)
    assert code == 409
    assert body["message"].startswith("The earlier exit for NIFTY 25900 CE (order ending ")
    assert body["message"].endswith(
        "is still working at Upstox, so no exit was sent. Cancel or modify it in Orders, then exit."
    )
    assert "only if it is still open" not in body["message"]


def test_words_a_busy_position_is_said_before_the_not_sized_one_in_exit_all(monkeypatch):
    broker = Broker({X: 65, Y: -30}, 0.0)
    broker.part[X] = 0
    world = install(monkeypatch, broker)
    world.phone_exit(X)
    broker.orders_unreadable = True
    code, body = world.app_exit_all()
    reasons = {each["symbol"]: each["reason"] for each in body["positions"]}
    assert code == 409
    assert "The earlier exit for NIFTY 25900 CE" in reasons[X]
    assert "Upstox did not send your orders in time" in reasons[Y]
