"""Release review v0.4.1, L1: the gate's claim on a market exit lasts while Upstox shows the order
working, not 150 s (M5.13, before the guard is switched on for Live).

A market exit is price-protected at Upstox and can rest. On 376dc58 its claim was dropped
``SEND_HELD_AT_MOST_S`` (150 s) after Upstox's answer whatever the book showed, and a plain exit
from a script then went beside the first, still working: the account ended short. Now the clock
only starts the asking: past 150 s from the answer (or from the last read that showed the order
working) the claim stands until the next read of the order book answers for it. Working: it
counts on, from now. Cancelled or rejected: dropped, as before. Complete, or not in the book:
dropped, for the positions book has had its time to show the exit. No claim outlives its
trading day (``CLAIM_KEPT_AT_MOST_S``).

Over the fake books of test_guard_gate.py, then over the simulated Upstox of the v0.4.1 tests.
"""

from __future__ import annotations

import time

import pytest
import test_guard_gate as gate_tests
from test_guard_gate import CE, ON, broker, order, refused_message, row
from v041_world import ON as SWITCH_ON
from v041_world import _order, _world

from broker.upstox.api import order_api
from sudarshan.guard import gate, readers

PAST = gate.SEND_HELD_AT_MOST_S + 10

# the fake books, the gate over them and the clock of test_guard_gate.py, as fixtures here
book, clock, the_gate = gate_tests.book, gate_tests.clock, gate_tests.the_gate


@pytest.fixture
def switch_on(the_gate):
    the_gate.set_state(ON)
    return the_gate


def _first_exit(book, upstox):
    book.rows = [row(CE, 65)]
    assert upstox.place(order("SELL"), "token")[2] == "OID0"
    book.statuses["OID0"] = "open"  # a price-protected market exit, resting at Upstox


def test_l1_a_claim_whose_order_still_works_counts_past_150_s(switch_on, book, clock):
    upstox = broker()
    _first_exit(book, upstox)
    clock.now += PAST
    said = refused_message(upstox.place(order("SELL"), "token"))
    assert "already on its way" in said
    assert book.calls.count("statuses") == 1  # asked once, and the order was working
    assert switch_on.sends() == 1


def test_l1_the_claim_counts_on_from_each_read_that_shows_it_working(switch_on, book, clock):
    """Every refusal for a claim reads the order book once (as before); each read that shows
    the order working keeps the claim, however long it has rested."""
    upstox = broker()
    _first_exit(book, upstox)
    for _hours in range(4):
        clock.now += PAST
        assert "already on its way" in refused_message(upstox.place(order("SELL"), "token"))
        assert switch_on.sends() == 1
    assert book.calls.count("statuses") == 4
    book.statuses["OID0"] = "cancelled"  # the owner cancels it in Upstox's app
    assert upstox.place(order("SELL"), "token")[2] == "OID1"


def test_l1_a_stale_claim_whose_order_shows_complete_is_dropped(switch_on, book, clock):
    """Complete, and the positions book still shows 65 after 150 s: the claim has nothing to
    wait for, so an exit of what the book shows goes."""
    upstox = broker()
    _first_exit(book, upstox)
    book.statuses["OID0"] = "complete"
    clock.now += PAST
    assert upstox.place(order("SELL"), "token")[2] == "OID1"
    assert book.calls.count("statuses") == 1


def test_l1_a_stale_claim_whose_order_the_book_does_not_list_is_dropped(switch_on, book, clock):
    upstox = broker()
    _first_exit(book, upstox)
    del book.statuses["OID0"]
    clock.now += PAST
    assert upstox.place(order("SELL"), "token")[2] == "OID1"


def test_l1_within_150_s_a_complete_order_s_claim_still_stands(switch_on, book, clock):
    """The control: before 150 s the positions book is still given its time to show the exit."""
    upstox = broker()
    _first_exit(book, upstox)
    book.statuses["OID0"] = "complete"
    clock.now += gate.SEND_HELD_AT_MOST_S - 1
    assert "already on its way" in refused_message(upstox.place(order("SELL"), "token"))


def test_l1_a_read_that_fails_ends_no_claim(switch_on, book, clock):
    upstox = broker()
    _first_exit(book, upstox)
    book.fail.add("statuses")
    clock.now += PAST
    assert "already on its way" in refused_message(upstox.place(order("SELL"), "token"))
    assert switch_on.sends() == 1


def test_l1_a_stale_resting_stop_s_claim_is_still_dropped(switch_on, book, clock):
    """A resting limit or stop stands for nothing once the book shows it resting, past 150 s
    as before it."""
    upstox = broker()
    book.rows = [row(CE, 65)]
    assert upstox.place(order("SELL", pricetype="SL-M", trigger_price="98.5"), "token")[2]
    book.statuses["OID0"] = "trigger pending"
    clock.now += PAST
    assert upstox.place(order("SELL"), "token")[2] == "OID1"


def test_l1_no_claim_outlives_its_trading_day(switch_on, book, clock):
    upstox = broker()
    _first_exit(book, upstox)
    clock.now += gate.CLAIM_KEPT_AT_MOST_S + 1
    assert upstox.place(order("SELL"), "token")[2] == "OID1"
    assert "statuses" not in book.calls  # forgotten before anything asked


def test_l1_the_day_cap_outlasts_any_indian_session():
    assert gate.CLAIM_KEPT_AT_MOST_S >= 15 * 3600  # MCX closes at 23:55 and opens at 09:00


# -- the review's probe, over the simulated Upstox ---------------------------------------------


@pytest.fixture
def ticking(monkeypatch):
    real = time.monotonic
    offset = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: real() + offset[0])

    class Clock:
        @staticmethod
        def advance(seconds: float) -> None:
            offset[0] += seconds

    return Clock()


def test_l1_a_second_exit_160_s_after_the_first_answer_is_refused_while_the_first_works(
    monkeypatch, risk_store, ticking
):
    """Stage `on`, switch on. Long 65 CE; a script's SELL 65 is accepted and rests at Upstox.
    160 s later the book still shows it open; a second SELL 65 is refused. On 376dc58 it was
    sent, and the account ended at -65 when both filled."""
    world = _world(monkeypatch, risk_store, {CE: 65}, SWITCH_ON)
    upstox = world.broker
    upstox.part[CE] = 0  # accepted, nothing filled: it rests at Upstox
    first = order_api.place_order_api(_order(CE, "SELL", 65), "token")
    ticking.advance(160.0)
    order_api._POSITION_BOOK.invalidate("token")
    assert readers.live_order_statuses("token") == {first[2]: "open"}
    second = order_api.place_order_api(_order(CE, "SELL", 65), "token")
    del upstox.part[CE]
    upstox.fill_working()
    assert first[2]
    assert second[2] is None, "a second exit was sent beside the first, still working"
    assert upstox.true_qty(CE) == 0
