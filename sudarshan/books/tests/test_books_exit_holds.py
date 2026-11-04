"""An exit's hold ends by Upstox's order book, not by a clock, and sizes come from fills (M6.9).

N1 and N3 of the v0.4.1 release review: a market exit Upstox accepted can rest as a limit order at
its protected price, and a second exit of the same position 15 s later went beside it. Now the
answer's order ids hold the position until the order book shows them complete, cancelled or
rejected (read only when a second exit arrives), and for at most ``EXIT_HOLD_UNCONFIRMED_S`` when
the book cannot say. N2 and N4: every exit the server sizes is sized from the positions row
corrected by the order book's fills, and refused (or left out of Exit all) when that cannot be
worked out. The simulated Upstox is books_upstox_world.py: its positions book lags ``lag``
seconds, its order book does not.
"""

from __future__ import annotations

import time

import pytest
from books_upstox_world import Broker, install

import broker.upstox.api.order_api as upstox
from sudarshan.books import exit_guard

X = "NIFTY27OCT2625900CE"  # MIS
Y = "BANKNIFTY27OCT2657500PE"  # NRML


@pytest.fixture
def clock(monkeypatch):
    """time.monotonic moved forward for every module at once (the lag and the holds)."""
    real = time.monotonic
    offset = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: real() + offset[0])

    class Clock:
        @staticmethod
        def advance(seconds: float) -> None:
            offset[0] += seconds

    return Clock()


def _off(monkeypatch):
    from sudarshan.guard import flag

    monkeypatch.setenv(flag.ENV, "off")  # Live as it runs today


def _sent(broker, symbol=X):
    return [(o["side"], o["qty"]) for o in broker.orders if o["symbol"] == symbol]


def _resting_first_exit(monkeypatch, held=65):
    """The app's Exit is accepted and rests at Upstox: a market order held at its price."""
    _off(monkeypatch)
    world = install(monkeypatch, Broker({X: held}, 0.0))
    world.broker.part[X] = 0
    code, body = world.phone_exit(X)
    assert code == 200, body
    return world, body["orderid"]


@pytest.mark.parametrize("second", ["tv", "flow", "phone", "exit_all_leaves_it"])
def test_a_second_exit_is_refused_while_the_first_still_works_however_long_after(
    monkeypatch, clock, second
):
    world, _first = _resting_first_exit(monkeypatch)
    clock.advance(100.0)  # long past the old 15 s note
    upstox._POSITION_BOOK.invalidate("token")
    if second == "tv":
        code, body = world.tv_smart_exit(X)
    elif second == "flow":
        code, body = world.flow_close(X)
    elif second == "phone":
        code, body = world.phone_exit(X)
    else:
        code, body = world.app_exit_all()
    assert _sent(world.broker) == [("SELL", 65)], body
    if second != "exit_all_leaves_it":
        assert code == 409 and "still working at Upstox" in body["message"], body
        assert "Cancel or modify it in Orders" in body["message"], body


def test_the_hold_ends_when_the_book_shows_the_order_complete(monkeypatch, clock):
    world, _first = _resting_first_exit(monkeypatch)
    del world.broker.part[X]
    world.broker.fill_working()  # the market came back: the order is complete
    clock.advance(5.0)
    upstox._POSITION_BOOK.invalidate("token")
    code, body = world.tv_smart_exit(X)
    # the position is flat by the order book: nothing more to sell, and no refusal
    assert _sent(world.broker) == [("SELL", 65)] and world.broker.true_qty(X) == 0, (code, body)
    assert "already on its way" not in str(body)


def test_the_hold_ends_when_the_book_shows_the_order_cancelled_and_the_exit_goes(
    monkeypatch, clock
):
    world, first = _resting_first_exit(monkeypatch)
    with world.broker.lock:
        world.broker.working = [o for o in world.broker.working if o["id"] != first]
        world.broker.orders[0]["filled"] = 0  # cancelled with nothing filled
    clock.advance(40.0)
    upstox._POSITION_BOOK.invalidate("token")
    code, body = world.tv_smart_exit(X)
    assert code == 200 and _sent(world.broker) == [("SELL", 65), ("SELL", 65)], body


def test_a_book_that_cannot_be_read_holds_up_to_150_s_and_says_it_is_not_confirmed(
    monkeypatch, clock
):
    world, _first = _resting_first_exit(monkeypatch)
    world.broker.orders_unreadable = True
    clock.advance(60.0)
    upstox._POSITION_BOOK.invalidate("token")
    code, body = world.tv_smart_exit(X)
    assert code in (409, 424), body
    assert _sent(world.broker) == [("SELL", 65)]
    assert "not confirmed" in body["message"] or "could not be confirmed" in body["message"]
    assert "Orders" in body["message"]


def test_the_unconfirmed_hold_holds_150_s_from_the_answer_and_no_longer(monkeypatch, clock):
    """The book answers but does not list the order: it cannot be told, so it holds 150 s."""
    _off(monkeypatch)
    world = install(monkeypatch, Broker({X: 65}, 0.0))
    exit_guard.exit_placed("live", "NFO", X, "MIS", ["260000000999"])  # an order not in the book
    code, body = world.tv_smart_exit(X)
    assert code == 409 and "not confirmed" in body["message"], body
    assert _sent(world.broker) == []
    clock.advance(exit_guard.EXIT_HOLD_UNCONFIRMED_S + 1)
    upstox._POSITION_BOOK.invalidate("token")
    code, body = world.tv_smart_exit(X)
    assert code == 200 and _sent(world.broker) == [("SELL", 65)], body


def test_a_resting_stop_placed_earlier_does_not_block_a_single_exit(monkeypatch):
    """The owner's decided behaviour, as Upstox's own app: only Exit all leaves it out."""
    _off(monkeypatch)
    world = install(monkeypatch, Broker({X: 65}, 0.0))
    world.broker.rest(X, "SELL", 65)  # a stop loss placed before
    code, body = world.phone_exit(X)
    assert code == 200 and _sent(world.broker) == [("SELL", 65)], body


def test_exit_all_still_leaves_a_position_with_a_resting_stop_out(monkeypatch):
    _off(monkeypatch)
    world = install(monkeypatch, Broker({X: 65}, 0.0))
    world.broker.rest(X, "SELL", 65)
    code, body = world.app_exit_all()
    assert _sent(world.broker) == [] and "working to close" in body["message"], body


def test_a_first_exit_reads_the_order_book_once_and_a_second_adds_no_read(monkeypatch, clock):
    """The hold is judged from the book read that sizes the exit: no extra read."""
    _off(monkeypatch)
    world = install(monkeypatch, Broker({X: 65, Y: -30}, 0.0))
    world.broker.part[X] = 0
    world.phone_exit(X)
    assert world.broker.order_book_reads == 1
    upstox._POSITION_BOOK.invalidate("token")
    world.tv_smart_exit(X)  # refused by the hold
    assert world.broker.order_book_reads == 2


def test_an_answer_that_names_no_order_holds_by_the_short_note_only(monkeypatch, clock):
    _off(monkeypatch)
    world = install(monkeypatch, Broker({X: 65}, 0.0))
    exit_guard.exit_placed("live", "NFO", X, "MIS")  # an answer with no order id
    code, body = world.tv_smart_exit(X)
    assert code == 409, body
    clock.advance(exit_guard.EXIT_SHOWS_WITHIN_S + 1)
    code, body = world.tv_smart_exit(X)
    assert code == 200, body


# -- sizes come from the book's fills (N2, N4) ----------------------------------------------------


@pytest.mark.parametrize("lag", [0.0, 60.0])
def test_a_second_exit_after_the_first_filled_is_sized_from_the_books_fills(
    monkeypatch, clock, lag
):
    """The first exit filled; the positions book shows it a minute late. A TradingView exit 16 s
    later sizes from the fills and finds nothing held, instead of selling again."""
    _off(monkeypatch)
    world = install(monkeypatch, Broker({X: 130}, lag))
    world.phone_exit(X)
    clock.advance(16.0)
    upstox._POSITION_BOOK.invalidate("token")
    world.tv_smart_exit(X)
    assert _sent(world.broker) == [("SELL", 130)] and world.broker.true_qty(X) == 0


def test_a_part_fill_not_yet_in_the_row_is_counted(monkeypatch, clock):
    """Long 195 by the order book, 260 by the row: the exit sells 195."""
    _off(monkeypatch)
    world = install(monkeypatch, Broker({X: 260}, 60.0))
    with world.broker.lock:
        world.broker.fills.append((time.monotonic(), X, -65))  # a lot of a target filled
    world.phone_exit(X)
    assert _sent(world.broker) == [("SELL", 195)] and world.broker.true_qty(X) == 0


def test_exit_all_sizes_each_position_from_the_fills(monkeypatch):
    _off(monkeypatch)
    world = install(monkeypatch, Broker({X: 260, Y: -30}, 60.0))
    with world.broker.lock:
        world.broker.fills.append((time.monotonic(), X, -65))
        world.broker.fills.append((time.monotonic(), Y, 30))  # Y was closed, row not updated
    code, body = world.app_exit_all()
    assert _sent(world.broker, X) == [("SELL", 195)] and _sent(world.broker, Y) == []
    assert world.broker.true_qty(X) == 0 and world.broker.true_qty(Y) == 0, body


def test_a_single_exit_is_refused_when_the_order_book_cannot_be_read(monkeypatch):
    _off(monkeypatch)
    world = install(monkeypatch, Broker({X: 65}, 0.0))
    world.broker.orders_unreadable = True
    for send in (world.phone_exit, world.tv_smart_exit, world.flow_close):
        code, body = send(X)
        assert code in (424, 400, 409) and _sent(world.broker) == [], (code, body)
        assert "Upstox did not send your orders in time" in body["message"], body
        assert "sent no exit" in body["message"] and "Upstox app" in body["message"], body


def test_a_single_exit_is_refused_when_the_row_has_no_day_quantities(monkeypatch):
    _off(monkeypatch)
    broker = Broker({X: 65}, 0.0)
    broker.day_fields = False
    world = install(monkeypatch, broker)
    code, body = world.phone_exit(X)
    assert _sent(broker) == [] and "could not work out how much" in body["message"], (code, body)


def test_a_single_exit_is_refused_when_the_row_shows_more_than_the_order_book(monkeypatch):
    """A negative difference cannot be computed."""
    _off(monkeypatch)
    broker = Broker({X: 65}, 0.0)
    broker.base_day[X] = (0, 0)
    world = install(monkeypatch, broker)
    real = broker.get

    def lying(url, headers=None, **kwargs):
        response = real(url, headers, **kwargs)
        if url.endswith("short-term-positions"):
            for row in response.json()["data"]:
                row["day_sell_quantity"] = 65  # more than the order book lists
        return response

    broker.get = lying
    code, body = world.phone_exit(X)
    assert _sent(broker) == [] and "could not work out how much" in body["message"], (code, body)


def test_exit_all_leaves_a_position_out_when_the_order_book_cannot_be_read(monkeypatch):
    _off(monkeypatch)
    world = install(monkeypatch, Broker({X: 65, Y: -30}, 0.0))
    world.broker.orders_unreadable = True
    code, body = world.app_exit_all()
    assert world.broker.orders == [], body
    assert body["message"].count("Upstox did not send your orders in time") >= 2, body
    assert "Close it from the Upstox app" in body["message"], body
    assert "was not looked for" not in body["message"], body
    assert {p["outcome"] for p in body["positions"]} == {"not_sent"}


def test_exit_all_leaves_out_a_position_whose_row_cannot_be_corrected_and_sends_the_rest(
    monkeypatch,
):
    _off(monkeypatch)
    broker = Broker({X: 65, Y: -30}, 0.0)
    world = install(monkeypatch, broker)
    real = broker.get

    def one_row_without_day_figures(url, headers=None, **kwargs):
        response = real(url, headers, **kwargs)
        if url.endswith("short-term-positions"):
            for row in response.json()["data"]:
                if row["tradingsymbol"] == Y:
                    row.pop("day_sell_quantity")
        return response

    broker.get = one_row_without_day_figures
    code, body = world.app_exit_all()
    assert _sent(broker, X) == [("SELL", 65)] and _sent(broker, Y) == [], body
    assert "could not work out how much you hold in BANKNIFTY 57500 PE" in body["message"]


# -- the late sentence (N7) -----------------------------------------------------------------------


def test_the_late_sentence_names_no_cause_and_says_what_to_do():
    said = upstox._late_words(["BANKNIFTY 57500 PE"])
    assert said == (
        "Exit all ran out of its 15 seconds before it could send an exit for BANKNIFTY 57500 PE, "
        "so nothing was sent for it. Check Positions, then send Exit all again."
    )
    assert "slow" not in said and "Upstox was" not in said
    assert "them" in upstox._late_words(["A", "B"])


# -- no clock decides a hold (unit) ---------------------------------------------------------------


def test_a_hold_is_judged_by_the_statuses_it_is_given(clock):
    guard = exit_guard.ExitsInFlight()
    position = ("NFO", X, "MIS")
    guard.placed("live", position, ("1", "2"))  # a sliced exit: two orders
    working = guard.hold_verdict("live", position, {"1": "complete", "2": "open"})
    assert "order ending 2" in working and "Cancel or modify it in Orders" in working
    clock.advance(10_000.0)  # no clock ends it while the book shows it working
    assert guard.hold_verdict("live", position, {"1": "complete", "2": "open"}) == working
    assert guard.hold_verdict("live", position, {"1": "complete", "2": "cancelled"}) is None
    assert guard.hold_verdict("live", position, {}) is None  # ended for good: dropped


def test_the_connection_error_is_not_a_hold_end(monkeypatch):
    """A read that fails is `None` statuses: the hold stays and says it is not confirmed."""
    guard = exit_guard.ExitsInFlight()
    position = ("NFO", X, "MIS")
    guard.placed("live", position, ("1",))
    assert "not confirmed" in guard.hold_verdict("live", position, None)
