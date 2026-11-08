"""The three Live exit gaps closed after v0.4.1 (M6.9), over the simulated Upstox.

1. Exit all exits a position Upstox's positions book does not list yet, from the order book's
   fills, through the same claim, hold and time limit as every listed position. Until this change
   it left the position out with a sentence (the Y3 mitigation).
2. A smart order to a size other than 0 that reduces or crosses a position claims the position
   while it is placed, so two identical reversal alerts in one second cannot both go. The race
   itself is in test/test_gthread_brokers_b_smart_order.py; the surface behaviour is here.

Each test here fails on 376dc58 (v0.4.1) unless it says it is a control.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from books_upstox_world import Broker, install

from sudarshan.books import exit_guard, fills

X = "NIFTY27OCT2625900CE"  # MIS
Y = "BANKNIFTY27OCT2657500PE"  # NRML

VECTORS = json.loads((Path(__file__).parent / "fill_vectors.json").read_text())["cases"]


def _sent(broker, symbol):
    return [(o["side"], o["qty"]) for o in broker.orders if o["symbol"] == symbol]


def _bought_not_listed(world, symbol, quantity):
    """A fill in the order book of an instrument the positions book has no row for yet."""
    world.broker.fills.append((time.monotonic(), symbol, quantity))


def _fresh_book():
    import broker.upstox.api.order_api as upstox

    upstox._POSITION_BOOK.invalidate("token")


# -- 1. Exit all with a position the positions book does not list yet ---------------------------------


def test_an_empty_positions_book_with_one_fill_is_exited_from_the_fills(monkeypatch):
    """Bought 30 BANKNIFTY PE a second ago: the positions book is empty, the order book has the
    fill. Exit all sells the 30, as NRML (the order row's product), and answers success with the
    order it placed, so the phone can hold on it."""
    world = install(monkeypatch, Broker({}, 0.0))
    _bought_not_listed(world, Y, 30)
    code, body = world.app_exit_all()
    assert (code, body["status"]) == (200, "success"), body
    assert _sent(world.broker, Y) == [("SELL", 30)]
    assert world.broker.true_qty(Y) == 0
    sent = [p for p in body["positions"] if p["symbol"] == Y]
    assert sent and sent[0]["outcome"] == "sent" and sent[0]["product"] == "NRML"
    assert sent[0]["orderids"]


def test_the_unlisted_exit_goes_in_the_product_of_its_order_rows(monkeypatch):
    world = install(monkeypatch, Broker({}, 0.0))
    products = []
    real_post = world.broker.post

    def post(url, headers=None, content=None, **kwargs):
        products.append(json.loads(content)["product"])
        return real_post(url, headers=headers, content=content, **kwargs)

    monkeypatch.setattr(world.broker, "post", post)
    _bought_not_listed(world, X, 65)  # X's order rows carry product I
    code, _body = world.app_exit_all()
    assert code == 200 and products == ["I"]


def test_a_listed_and_an_unlisted_position_are_both_exited(monkeypatch):
    world = install(monkeypatch, Broker({X: 65}, 0.0))
    _bought_not_listed(world, Y, -30)
    code, body = world.app_exit_all()
    assert (code, body["status"]) == (200, "success"), body
    assert _sent(world.broker, X) == [("SELL", 65)] and _sent(world.broker, Y) == [("BUY", 30)]
    assert {p["symbol"]: p["outcome"] for p in body["positions"]} == {X: "sent", Y: "sent"}


def test_an_unlisted_position_part_closed_already_is_exited_for_what_is_left(monkeypatch):
    world = install(monkeypatch, Broker({}, 0.0))
    _bought_not_listed(world, Y, 90)
    _bought_not_listed(world, Y, -30)
    code, _body = world.app_exit_all()
    assert code == 200 and _sent(world.broker, Y) == [("SELL", 60)]
    assert world.broker.true_qty(Y) == 0


@pytest.fixture
def cnc(monkeypatch):
    from books_upstox_world import BY_TOKEN
    from books_upstox_world import INSTRUMENTS as world_instruments

    token = "NSE_EQ|INE002A01018"
    monkeypatch.setitem(world_instruments, "RELIANCE", (token, "NSE", "D", "CNC"))
    monkeypatch.setitem(BY_TOKEN, token, "RELIANCE")
    return "RELIANCE"


def test_a_cnc_net_sale_from_holdings_is_still_left_alone(monkeypatch, cnc):
    """Control: a CNC net sell with no row is a sale from holdings, not a position to buy back."""
    world = install(monkeypatch, Broker({X: 65}, 0.0))
    _bought_not_listed(world, cnc, -10)
    code, body = world.app_exit_all()
    assert (code, body["status"]) == (200, "success")
    assert _sent(world.broker, cnc) == [] and _sent(world.broker, X) == [("SELL", 65)]
    assert "traded today" not in str(body)


def test_a_conversion_whose_row_carries_the_quantity_sends_nothing_extra(monkeypatch):
    """Bought 65 MIS (an I order in the order book), converted to NRML in Upstox's app: the I row
    is gone and the D row carries the 65. The I fills with no I row are not a second position."""
    from test_books_exit_review_x import _I_ROWS, Converted

    broker = Converted(_I_ROWS["I row gone"])
    world = install(monkeypatch, broker)
    code, body = world.app_exit_all()
    assert (code, body["status"]) == (200, "success"), body
    assert broker.products == [("SELL", "D", 65)]


def test_an_unreadable_order_book_sends_nothing_and_never_answers_success(monkeypatch):
    broker = Broker({X: 65}, 0.0)
    broker.orders_unreadable = True
    world = install(monkeypatch, broker)
    code, body = world.app_exit_all()
    assert code != 200 and body["status"] == "error"
    assert broker.orders == []
    assert "Upstox did not send your orders in time" in body["message"]


def test_an_unreadable_order_book_with_an_empty_positions_book_says_so(monkeypatch):
    broker = Broker({}, 0.0)
    broker.orders_unreadable = True
    world = install(monkeypatch, broker)
    code, body = world.app_exit_all()
    assert code == 424 and "position opened in the last minute may not show yet" in body["message"]
    assert broker.orders == []


def test_an_unlisted_position_whose_size_cannot_be_worked_out_is_left_out_with_a_sentence(
    monkeypatch,
):
    """An order row of the instrument with no filled quantity: the fills show something held with
    no row, and nobody can say how much. Nothing is sent for it, and the answer is not success."""
    import broker.upstox.api.order_api as upstox

    world = install(monkeypatch, Broker({}, 0.0))
    _bought_not_listed(world, Y, 30)
    real = upstox.get_order_book

    def with_a_blank_row(auth):
        book = real(auth)
        if isinstance(book, dict) and book.get("status") == "success":
            token = "NSE_FO|1002"
            book["data"].append({"order_id": "blank", "instrument_token": token, "exchange": "NFO",
                                 "tradingsymbol": Y, "product": "D", "transaction_type": "SELL",
                                 "status": "open", "order_type": "LIMIT", "quantity": 30,
                                 "filled_quantity": None, "pending_quantity": 30})  # fmt: skip
        return book

    monkeypatch.setattr(upstox, "get_order_book", with_a_blank_row)
    code, body = world.app_exit_all()
    assert code != 200 and body["status"] == "error"
    assert _sent(world.broker, Y) == []
    assert "BANKNIFTY 57500 PE was traded today" in body["message"]
    assert "could not work out from its orders what to send" in body["message"]
    assert "All Open Positions" not in body["message"]


def test_an_unlisted_position_whose_contract_is_unknown_is_left_out_with_a_sentence(monkeypatch):
    import broker.upstox.api.order_api as upstox

    world = install(monkeypatch, Broker({}, 0.0))
    _bought_not_listed(world, Y, 30)
    monkeypatch.setattr(upstox, "get_symbol", lambda token, exchange: None)
    code, body = world.app_exit_all()
    assert code != 200 and body["status"] == "error"
    assert world.broker.orders == []
    assert "BANKNIFTY 57500 PE was traded today" in body["message"]


def test_an_unlisted_position_claimed_by_a_single_exit_is_left_to_it(monkeypatch):
    """The same claim as every listed position: an exit of it being placed leaves it out."""
    world = install(monkeypatch, Broker({}, 0.0))
    _bought_not_listed(world, Y, 30)
    with exit_guard.one_exit("live", "NFO", Y, "NRML") as busy:
        assert busy is None
        code, body = world.app_exit_all()
    assert code == 409
    assert _sent(world.broker, Y) == []
    assert "An exit for BANKNIFTY 57500 PE is already on its way" in body["message"]


def test_an_unlisted_position_whose_exit_still_works_is_held(monkeypatch):
    """The same hold as every listed position: an exit for it that Upstox answered, still working
    in the order book, means no second exit."""
    world = install(monkeypatch, Broker({}, 0.0))
    _bought_not_listed(world, Y, 30)
    world.broker.rest(Y, "SELL", 30, kind="MARKET", trigger=0)
    order_id = world.broker.working[0]["id"]
    exit_guard.exit_placed("live", "NFO", Y, "NRML", (order_id,))
    code, body = world.app_exit_all()
    assert code != 200
    assert _sent(world.broker, Y) == []
    assert "still working at Upstox" in body["message"]


def test_an_unlisted_position_with_a_stop_working_on_its_closing_side_is_left_out(monkeypatch):
    world = install(monkeypatch, Broker({}, 0.0))
    _bought_not_listed(world, Y, 30)
    world.broker.rest(Y, "SELL", 30, kind="SL-M", trigger=98.5)
    code, body = world.app_exit_all()
    assert code != 200
    assert _sent(world.broker, Y) == []
    assert "SELL 30 SL-M order" in body["message"] and "Cancel it in Orders" in body["message"]


def test_an_unlisted_exit_obeys_exit_all_s_time_limit(monkeypatch):
    monkeypatch.setattr(exit_guard, "EXIT_ALL_SENDS_FOR_S", 0.0)
    world = install(monkeypatch, Broker({}, 0.0))
    _bought_not_listed(world, Y, 30)
    code, body = world.app_exit_all()
    assert code != 200 and _sent(world.broker, Y) == []
    assert "ran out of its 0 seconds" in body["message"]


def test_the_hold_after_an_unlisted_exit_refuses_a_second_exit_while_it_works(monkeypatch):
    """Exit all's exit of the unlisted position rests at Upstox (nothing filled yet). The phone's
    Exit of the same position a moment later is refused, naming the working order."""
    world = install(monkeypatch, Broker({}, 0.0))
    _bought_not_listed(world, Y, 30)
    world.broker.part[Y] = 0  # the exit rests at its protected price
    code, _body = world.app_exit_all()
    assert code == 200 and _sent(world.broker, Y) == [("SELL", 30)]
    _fresh_book()
    code, body = world.phone_exit(Y)
    assert code == 409
    assert "still working at Upstox" in body["message"]
    assert _sent(world.broker, Y) == [("SELL", 30)]


def test_two_exit_alls_at_once_send_the_unlisted_exit_once(monkeypatch):
    world = install(monkeypatch, Broker({}, 0.0))
    _bought_not_listed(world, Y, 30)
    world.broker.hang[Y] = 0.5
    first, out1 = world.background(world.app_exit_all)
    time.sleep(0.2)
    second, out2 = world.background(world.app_exit_all)
    first.join(10)
    second.join(10)
    codes = sorted([out1["result"][0], out2["result"][0]])
    assert codes == [200, 409], codes
    assert _sent(world.broker, Y) == [("SELL", 30)]


# -- the sizing contract: fill_vectors.json also decides what Exit all exits with no row ----------------


@pytest.mark.parametrize("case", VECTORS, ids=[case["name"] for case in VECTORS])
def test_unlisted_nets_follow_the_golden_vectors(case):
    """For every product of the vector's instrument with no positions row, Exit all exits what
    the vector says is held there, and leaves it out (None) when the vector says it cannot be
    worked out."""
    listed = {str(row["product"]) for row in case["positions"]}
    traded = {str(order["product"]) for order in case["orders"] or []}
    found = {
        product: held
        for _ex, _name, token, product, held in fills.unlisted_nets(
            case["positions"], case["orders"]
        )
        if token == case["token"]
    }
    for product in traded - listed:
        if case["held"] is None:
            raw = sum(
                (1 if o["transaction_type"] == "BUY" else -1) * (o["filled_quantity"] or 0)
                for o in case["orders"]
                if o["product"] == product and o["instrument_token"] == case["token"]
            )
            assert found.get(product, "absent") == (None if raw else "absent")
        elif case["held"].get(product, 0):
            assert found[product] == case["held"][product]
        else:
            assert product not in found


def test_an_unlisted_product_that_cannot_be_sized_is_named_with_none():
    orders = [
        {"instrument_token": "T", "product": "I", "transaction_type": "BUY", "filled_quantity": 65,
         "exchange": "NFO", "tradingsymbol": "N"},
        {"instrument_token": "T", "product": "I", "transaction_type": "SELL", "filled_quantity": None,
         "exchange": "NFO", "tradingsymbol": "N"},
    ]  # fmt: skip
    assert fills.unlisted_nets([], orders) == [("NFO", "N", "T", "I", None)]


def test_the_unlisted_sentence_is_for_a_trader():
    said = exit_guard.unlisted_not_sized(("NFO", Y, "NRML"))
    assert said.startswith("BANKNIFTY 57500 PE was traded today")
    assert "Upstox app" in said
    for word in ("None", "token", "HTTP", "424", "409", "order book"):
        assert word not in said


def test_nothing_waits_on_a_lock_across_the_exit(monkeypatch):
    """Control for the eventlet rule: the guard's lock is never held while Exit all sends."""
    world = install(monkeypatch, Broker({}, 0.0))
    _bought_not_listed(world, Y, 30)
    taken = []

    def post(url, headers=None, content=None, **kwargs):
        taken.append(exit_guard.EXITS._lock.locked())
        return Broker.post(world.broker, url, headers=headers, content=content, **kwargs)

    monkeypatch.setattr(world.broker, "post", post)
    assert world.app_exit_all()[0] == 200
    assert taken == [False]
