"""A reversal back inside the positions' lag (M6.9, GA1.2).

A smart order to a position size other than 0 that Upstox's positions row already showed was
answered "No action needed" on the row alone. The row can show a fill a minute late: long 65,
reversed to short 65, and reversed back to long 65 while the row still shows +65, the order read
as nothing to do and the account stayed short 65, with a success for an answer.

Such an order is now judged on the row corrected by the order book's fills, as every order that
reduces or crosses a position is (broker/upstox/api/order_api.py, ``place_smartorder_api``).
What it does when the corrected figure is not the size asked for is a switch that is off by
default (sudarshan/books/lag_flag.py): off, nothing is sent, as before, and the answer says the
positions are behind; on, the reversal back is sent. Tests marked DEFECT fail on 21511c32.

Over the simulated Upstox of books_upstox_world.py: instant fills, positions 60 s behind.
"""

from __future__ import annotations

import pytest
from books_upstox_world import Broker, install

from sudarshan.books import exit_guard, lag_flag

X = "NIFTY27OCT2625900CE"
POSITION = ("NFO", X, "MIS")
LAG = 60.0
NO_ACTION = "Positions Already Matched. No Action needed."  # the service's words for it


@pytest.fixture
def world(monkeypatch):
    monkeypatch.delenv(lag_flag.ENV, raising=False)
    return install(monkeypatch, Broker({X: 65}, LAG))


def _to(world, size: int):
    """A TradingView strategy alert that takes the position to ``size``."""
    return world.tv_smart_exit(
        X, quantity="130", action="BUY" if size > 0 else "SELL", position_size=str(size)
    )


def _reversed_to_short(world):
    code, body = _to(world, -65)
    assert code == 200 and world.broker.true_qty(X) == -65, body
    assert world.broker.shown_qty(X, 0.0) == 65  # the positions still show the long


def test_the_switch_is_off_unless_it_says_on(monkeypatch):
    monkeypatch.delenv(lag_flag.ENV, raising=False)
    assert lag_flag.sends() is False
    for value, sends in (
        ("on", True),
        (" 'ON' ", True),
        ("off", False),
        ("yes", False),
        ("", False),
    ):
        monkeypatch.setenv(lag_flag.ENV, value)
        assert lag_flag.sends() is sends, value


def test_defect_a_reversal_back_is_not_answered_as_matching(world):
    """DEFECT. Off, the default: nothing is sent, as before, but the answer no longer says the
    position matches. On 21511c32 it was 200 "No action needed" with the account short 65."""
    _reversed_to_short(world)
    code, body = _to(world, 65)
    assert world.broker.true_qty(X) == -65 and len(world.broker.orders) == 1
    assert code == 409 and body["status"] == "error", body
    assert body["message"] == exit_guard.fills_not_shown_yet(POSITION)
    assert "NIFTY 25900 CE" in body["message"] and "was not sent" in body["message"]


def test_defect_with_the_switch_on_a_reversal_back_goes_sized_from_the_fills(world, monkeypatch):
    """DEFECT. On: BUY 130 from the corrected -65, so the account is long 65 as asked."""
    monkeypatch.setenv(lag_flag.ENV, "on")
    _reversed_to_short(world)
    code, body = _to(world, 65)
    assert code == 200 and world.broker.true_qty(X) == 65, body
    assert [(o["side"], o["qty"]) for o in world.broker.orders] == [("SELL", 130), ("BUY", 130)]
    assert exit_guard.EXITS.placed_lately("live") == {POSITION}  # held as any reversal is


def test_with_the_switch_on_a_repeat_while_the_first_still_works_is_refused(world, monkeypatch):
    """On: the reversal back part-fills and rests; the alert repeats. Nothing goes beside it."""
    monkeypatch.setenv(lag_flag.ENV, "on")
    _reversed_to_short(world)
    world.broker.part[X] = 65  # of BUY 130, 65 fills and 65 rests at its protected price
    code, _body = _to(world, 65)
    assert code == 200 and world.broker.true_qty(X) == 0
    code, body = _to(world, 65)
    assert code == 409 and len(world.broker.orders) == 2, body
    assert world.broker.true_qty(X) == 0 and "was not sent" in body["message"]
    world.broker.fill_working()  # the rest fills: long 65, once
    assert world.broker.true_qty(X) == 65


def test_with_the_switch_on_an_order_that_would_only_add_is_still_not_sent(world, monkeypatch):
    """On: the switch is for the reversal back. After an exit the row shows +65 and today's
    fills say flat; an order to +65 would be a fresh entry, and it is not sent on this rule."""
    monkeypatch.setenv(lag_flag.ENV, "on")
    code, _body = world.tv_smart_exit(X, quantity="65", action="SELL")
    assert code == 200 and world.broker.true_qty(X) == 0
    code, body = _to(world, 65)
    assert code == 409 and world.broker.true_qty(X) == 0, body
    assert body["message"] == exit_guard.fills_not_shown_yet(POSITION)


@pytest.mark.parametrize("switch", ["off", "on"])
def test_an_order_the_row_and_the_fills_both_match_needs_no_action_at_one_read(
    world, monkeypatch, switch
):
    monkeypatch.setenv(lag_flag.ENV, switch)
    code, body = _to(world, 65)
    assert code == 200 and body["message"] == NO_ACTION, body
    assert world.broker.orders == [] and world.broker.order_book_reads == 1


def test_control_with_the_order_book_down_the_row_stands(world):
    """Control: nothing is sent either way, and no read is tried twice."""
    _reversed_to_short(world)
    world.broker.orders_unreadable = True
    code, body = _to(world, 65)
    assert code == 200 and body["message"] == NO_ACTION, body
    assert len(world.broker.orders) == 1


def test_control_an_order_that_adds_as_the_row_says_reads_no_order_book(world):
    reads = world.broker.order_book_reads
    code, _body = _to(world, 130)
    assert code == 200 and world.broker.true_qty(X) == 130
    assert world.broker.order_book_reads == reads
