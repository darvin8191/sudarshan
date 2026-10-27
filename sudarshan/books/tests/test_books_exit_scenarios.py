"""Orderings of exits against the real code, over a simulated Upstox whose book lags (M6.9).

S1 to S11 are the independent review's orderings of 16b0997..ec0c6bd (its test_review_scenarios.py),
their assertions unchanged: each asserts the safe outcome, no position closed twice and none left
open without a sentence saying why. S12 onwards are this round's own. The simulated Upstox is
books_upstox_world.py: instant fills, a position book that shows them ``LAG`` seconds late, and an
order book.
"""

from __future__ import annotations

import threading
import time

import pytest
from books_upstox_world import Broker, install

import broker.upstox.api.order_api as upstox
from sudarshan.books import exit_guard

LAG = 1.5
X = "NIFTY27OCT2625900CE"  # long 65, MIS
Y = "BANKNIFTY27OCT2657500PE"  # short 30, NRML
Z = "SBIN"  # long 10, MIS
HELD = {X: 65, Y: -30, Z: 10}


def never_reversed(broker: Broker, symbol: str) -> bool:
    history = broker.history(symbol)
    start = history[0]
    return all(q >= 0 for q in history) if start > 0 else all(q <= 0 for q in history)


def summary(world, *symbols):
    b = world.broker
    return {
        "orders": [(o["symbol"][:6], o["side"], o["qty"]) for o in b.orders],
        "history": {s[:6]: b.history(s) for s in symbols},
        "answers": [(w, c, (body or {}).get("message")) for w, c, body, *_ in world.answers],
    }


@pytest.fixture
def world(monkeypatch):
    return install(monkeypatch, Broker(HELD, LAG))


# -- what round 7 fixed (the review's controls) ------------------------------------------------


def test_s1_phone_exit_then_exit_all_does_not_reverse(world):
    world.phone_exit(X)
    time.sleep(0.3)
    world.api_exit_all()
    s = summary(world, X, Y, Z)
    assert all(never_reversed(world.broker, sym) for sym in (X, Y, Z)), s
    assert world.broker.true_qty(Y) == 0 and world.broker.true_qty(Z) == 0, s


def test_s6_tradingview_exit_all_twice_a_second_apart_does_not_reverse(world):
    world.api_exit_all()
    time.sleep(1.0)
    world.api_exit_all()
    s = summary(world, X, Y, Z)
    assert all(never_reversed(world.broker, sym) for sym in (X, Y, Z)), s


def test_s6b_app_exit_all_then_flow_exit_all(world):
    world.app_exit_all()
    time.sleep(0.2)
    world.api_exit_all()
    s = summary(world, X, Y, Z)
    assert all(never_reversed(world.broker, sym) for sym in (X, Y, Z)), s


# -- the orderings the review found round 7 did not cover ---------------------------------------


def test_s2_exit_all_then_a_phone_exit_from_a_second_phone(world):
    world.api_exit_all()
    time.sleep(0.3)
    world.phone_exit(X)
    s = summary(world, X)
    assert never_reversed(world.broker, X), s


def test_s3_phone_exit_then_the_same_exit_from_a_second_phone(world):
    world.phone_exit(X)
    time.sleep(0.3)
    world.phone_exit(X)
    s = summary(world, X)
    assert never_reversed(world.broker, X), s


def test_s4_tradingview_smart_exit_then_exit_all(world):
    world.tv_smart_exit(X)
    time.sleep(0.3)
    world.api_exit_all()
    s = summary(world, X, Y, Z)
    assert never_reversed(world.broker, X), s


def test_s5_exit_all_then_tradingview_smart_exit(world):
    world.api_exit_all()
    time.sleep(0.3)
    world.tv_smart_exit(X)
    s = summary(world, X)
    assert never_reversed(world.broker, X), s


def test_s7_exit_all_twice_when_two_reads_disagree(world):
    """The book check and close-all read the book separately; a lagging second read reverses."""
    world.api_exit_all()  # one read of the book (read 1), three exits
    time.sleep(0.3)
    world.broker.read_flip = True  # from here even reads are fresh, odd ones lag (two replicas)
    world.broker.fresh_parity = 0
    world.api_exit_all()  # its book check reads 2 (fresh: flat), close-all reads 3 (lagging)
    s = summary(world, X, Y, Z)
    assert all(never_reversed(world.broker, sym) for sym in (X, Y, Z)), s


def test_s8_an_exit_that_never_reached_upstox_blocks_every_other_exit(world, monkeypatch):
    monkeypatch.setattr(exit_guard, "EXIT_SHOWS_WITHIN_S", 2.0, raising=False)
    world.broker.unreachable.add(X)
    code, body = world.phone_exit(X)  # "not confirmed": noted
    time.sleep(0.3)
    world.broker.unreachable.discard(X)
    world.api_exit_all()  # an unattended Exit all: TradingView does not resend
    s = summary(world, X, Y, Z)
    assert world.broker.true_qty(Y) == 0 and world.broker.true_qty(Z) == 0, s


def test_s9_an_exit_rejected_after_upstox_took_it_blocks_every_other_exit(world, monkeypatch):
    monkeypatch.setattr(exit_guard, "EXIT_SHOWS_WITHIN_S", 2.0, raising=False)
    world.broker.rejected_later.add(X)
    world.phone_exit(X)  # an order id, then rejected at the exchange: no fill
    world.broker.rejected_later.discard(X)
    time.sleep(0.3)
    world.api_exit_all()
    s = summary(world, X, Y, Z)
    assert world.broker.true_qty(Y) == 0 and world.broker.true_qty(Z) == 0, s


def test_s10_a_part_filled_exit_still_working_is_closed_again(world, monkeypatch):
    monkeypatch.setattr(exit_guard, "EXIT_SHOWS_WITHIN_S", 1.0, raising=False)
    world.broker.part[X] = 30  # 30 of 65 fill, 35 stay working at the exchange
    world.phone_exit(X)
    del world.broker.part[X]
    time.sleep(1.2 + LAG)  # the note has expired and the book shows 35 left
    world.api_exit_all()
    world.broker.fill_working()  # the market comes back: the first exit's 35 fill
    s = summary(world, X)
    assert never_reversed(world.broker, X), s


def test_s11_a_slow_exit_locks_every_other_exit_out_and_then_nothing_is_closed(world, monkeypatch):
    monkeypatch.setattr(exit_guard, "EXIT_ALL_WAITS_S", 1.0, raising=False)
    world.broker.hang[X] = 3.0  # one slow Upstox call
    slow, _ = world.background(world.phone_exit, X)
    time.sleep(0.3)
    everything, _ = world.background(world.api_exit_all)
    time.sleep(0.3)
    code, body = world.phone_exit(Y)  # the trader exits another position meanwhile
    everything.join(10)
    y_now = world.broker.true_qty(Y)
    slow.join(10)
    s = summary(world, X, Y, Z)
    assert y_now == 0, (code, body, s)


# -- this round's own --------------------------------------------------------------------------


def _answer(world, what):
    [(code, body)] = [(c, b) for w, c, b, *_ in world.answers if w == what]
    return code, body


def test_s2_the_second_phone_is_told_why_in_its_own_sentence(world):
    world.api_exit_all()
    world.phone_exit(X)
    code, body = _answer(world, f"phone exit {X}")
    assert code == 409
    assert body["message"].startswith("An exit for NIFTY 25900 CE is already on its way (sent ")
    assert body["message"].endswith("Check Orders, and exit it again only if it is still open.")
    assert [o["symbol"] for o in world.broker.orders].count(X) == 1


def test_s11_the_exit_all_closes_the_rest_at_once_and_says_why_it_skipped_one(world):
    world.broker.hang[X] = 2.0
    slow, _ = world.background(world.phone_exit, X)
    time.sleep(0.3)
    code, body = world.api_exit_all()
    took = world.answers[-1][4] - world.answers[-1][3]
    assert took < 1.0, f"it waited {took:.2f}s"
    assert (world.broker.true_qty(Y), world.broker.true_qty(Z)) == (0, 0)
    assert code == 409 and body["not_confirmed"] is True  # two went, one was not sent
    assert "Of 3 exits, 2 were sent and 1 was not sent." in body["message"]
    assert "An exit for NIFTY 25900 CE is already on its way" in body["message"]
    slow.join(10)
    assert world.broker.history(X) == [65, 0]


def test_s12_a_stop_loss_working_elsewhere_is_not_closed_twice(world):
    """A sell order resting in Upstox's order book (a stop loss placed in its own app): Exit all
    sends nothing for that position, because when the stop fills it would reverse it."""
    world.broker.rest(X, "SELL", 65)
    code, body = world.api_exit_all()
    assert (world.broker.true_qty(Y), world.broker.true_qty(Z)) == (0, 0)
    assert [o["symbol"] for o in world.broker.orders] == [Y, Z]
    assert code == 409 and body["not_confirmed"] is True
    assert "NIFTY 25900 CE already has an order working to close it (placed " in body["message"]
    world.broker.fill_working()
    assert never_reversed(world.broker, X)
    assert world.broker.order_book_reads == 1  # read once for the whole Exit all


def test_s12_a_working_order_on_the_other_side_does_not_hold_the_position(world):
    world.broker.rest(X, "BUY", 65)  # adds to the long: it closes nothing
    world.broker.rest(Y, "SELL", 30)  # adds to the short: it closes nothing
    code, body = world.api_exit_all()
    assert (code, body["status"]) == (200, "success")
    assert sorted(o["symbol"] for o in world.broker.orders) == sorted([X, Y, Z])


def test_s13_an_order_book_that_cannot_be_read_is_said_and_the_rest_goes(world):
    world.broker.orders_unreadable = True
    code, body = world.api_exit_all()
    assert (code, body["status"]) == (200, "success")
    assert [world.broker.true_qty(s) for s in (X, Y, Z)] == [0, 0, 0]
    assert body["message"] == (
        "All Open Positions Squared Off. The open orders could not be read from Upstox, so an "
        "order already working to close a position was not looked for. Check Orders."
    )


def test_s13_with_a_busy_position_too_both_are_said(world):
    world.phone_exit(X)
    world.broker.orders_unreadable = True
    code, body = world.api_exit_all()
    assert code == 409
    assert "An exit for NIFTY 25900 CE is already on its way" in body["message"]
    assert body["message"].endswith("was not looked for. Check Orders.")


def test_s14_flow_s_close_node_with_a_symbol_closes_that_position_only(world):
    code, body = world.flow_close(Z)
    assert (code, body["status"]) == (200, "success") and body["orderid"]
    assert [o["symbol"] for o in world.broker.orders] == [Z]
    assert world.broker.true_qty(X) == 65 and world.broker.true_qty(Y) == -30
    # A second, a second later, from the same node: the book still shows it open.
    time.sleep(0.3)
    code, body = world.flow_close(Z)
    assert code == 409 and body["message"].startswith("An exit for SBIN is already on its way")
    world.api_exit_all()
    assert all(never_reversed(world.broker, sym) for sym in (X, Y, Z)), summary(world, X, Y, Z)


def test_s15_a_tradingview_entry_right_after_an_exit_is_never_refused(world, monkeypatch):
    """Position size 0 with a quantity, from flat, is upstream's fresh order: an entry."""
    monkeypatch.setattr(world.broker, "lag", 0.0)  # the book shows the exit at once
    world.phone_exit(Z)
    code, body = world.tv_smart_exit(Z, quantity="10", action="BUY")
    assert (code, body["status"]) == (200, "success")
    assert [(o["symbol"], o["side"]) for o in world.broker.orders] == [(Z, "SELL"), (Z, "BUY")]


def test_s16_an_exit_all_that_runs_out_of_time_says_which_it_did_not_send(world, monkeypatch):
    monkeypatch.setattr(exit_guard, "EXIT_ALL_SENDS_FOR_S", 0.5)
    world.broker.hang.update({X: 0.6, Y: 0.6, Z: 0.6})  # a slow Upstox
    code, body = world.api_exit_all()
    took = world.answers[-1][4] - world.answers[-1][3]
    assert took < 0.5 + 0.6 + 0.5, f"it took {took:.2f}s"
    assert [o["symbol"] for o in world.broker.orders] == [X]  # the only one started in time
    assert code == 409 and body["not_confirmed"] is True
    assert "Of 3 exits, 1 was sent and 2 were not sent in time." in body["message"]
    assert (
        "Upstox was slow to answer, so Exit all stopped after 0.5 s and sent nothing for "
        "BANKNIFTY 57500 PE and SBIN. Check Positions, then send Exit all again."
    ) in body["message"]
    world.broker.hang.clear()
    world.api_exit_all()  # sent again: the first one's exit is on its way, the rest go now
    assert [world.broker.true_qty(s) for s in (X, Y, Z)] == [0, 0, 0]
    assert all(never_reversed(world.broker, sym) for sym in (X, Y, Z))


def _released_together(world, *calls):
    start = threading.Barrier(len(calls))

    def run(fn, *args):
        start.wait(10)
        fn(*args)

    threads = [threading.Thread(target=run, args=call) for call in calls]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(15)
    assert not any(thread.is_alive() for thread in threads)


@pytest.mark.parametrize("run", range(3))
def test_s17_two_exit_alls_released_together_close_each_position_once(world, run):
    """Barrier-synchronised on real threads (gthread): one is refused while the other places."""
    _released_together(world, (world.api_exit_all,), (world.app_exit_all,))
    assert [world.broker.true_qty(s) for s in (X, Y, Z)] == [0, 0, 0]
    assert sorted(o["symbol"] for o in world.broker.orders) == sorted([X, Y, Z])


@pytest.mark.parametrize("run", range(3))
def test_s18_a_phone_exit_and_an_exit_all_released_together_close_it_once(world, run):
    _released_together(world, (world.phone_exit, X), (world.api_exit_all,))
    assert [world.broker.true_qty(s) for s in (X, Y, Z)] == [0, 0, 0]
    assert [o["symbol"] for o in world.broker.orders].count(X) == 1
    assert all(never_reversed(world.broker, sym) for sym in (X, Y, Z))


@pytest.mark.parametrize("run", range(3))
def test_s19_two_phones_and_tradingview_exiting_one_position_together_send_one(world, run):
    _released_together(
        world, (world.phone_exit, X), (world.phone_exit, X), (world.tv_smart_exit, X)
    )
    assert [o["symbol"] for o in world.broker.orders] == [X]
    assert world.broker.history(X) == [65, 0]
    refused = [body["message"] for w, code, body, *_ in world.answers if code == 409]
    assert len(refused) == 2 and all("already on its way" in words for words in refused)


def test_s20_a_tradingview_exit_whose_read_outlasts_the_note_is_not_sent(world, monkeypatch):
    """Busy when it began sizing the close: its read answered after the note ran out, still
    showing the position as it was before the phone's exit, and would have sold it again."""
    monkeypatch.setattr(exit_guard, "EXIT_SHOWS_WITHIN_S", 1.0)
    world.phone_exit(X)
    world.broker.slow_read = 1.3  # answers after the note has run out, rows as at its start
    code, body = world.tv_smart_exit(X)
    world.broker.slow_read = 0.0
    assert code == 409, summary(world, X)
    assert body["message"].startswith("An exit for NIFTY 25900 CE is already on its way (sent ")
    assert [(o["symbol"], o["side"]) for o in world.broker.orders] == [(X, "SELL")]
    assert never_reversed(world.broker, X)


# -- the second review's orderings (its N2, N3, N3b) ---------------------------------------------


def _slow_first_read(world, seconds):
    """The first positions read answers ``seconds`` late, its rows as at its start."""
    world.broker.read_delay = lambda number: seconds if number == 1 else 0.0


def test_s21_a_tradingview_exit_whose_slow_read_an_exit_all_overtakes_closes_once(
    world, monkeypatch
):
    """The exit claims its position before its read, so the Exit all that starts during the read
    leaves that position to it; the read answers after the note would have run out."""
    monkeypatch.setattr(exit_guard, "EXIT_SHOWS_WITHIN_S", 1.0)
    _slow_first_read(world, 1.5)
    tv, _ = world.background(world.tv_smart_exit, X)
    time.sleep(0.2)
    code, body = world.api_exit_all()
    tv.join(10)
    assert never_reversed(world.broker, X), summary(world, X)
    assert [o["symbol"] for o in world.broker.orders].count(X) == 1
    assert code == 409 and body["not_confirmed"] is True  # Y and Z went, X was left to the exit
    assert "An exit for NIFTY 25900 CE is already on its way (sent " in body["message"]
    assert [world.broker.true_qty(s) for s in (X, Y, Z)] == [0, 0, 0]


def test_s22_the_app_s_exit_whose_slow_read_an_exit_all_overtakes_closes_once(world, monkeypatch):
    monkeypatch.setattr(exit_guard, "EXIT_SHOWS_WITHIN_S", 1.0)
    _slow_first_read(world, 1.5)
    phone, out = world.background(world.phone_exit, X)
    time.sleep(0.2)
    world.api_exit_all()
    phone.join(10)
    assert never_reversed(world.broker, X), summary(world, X)
    assert [o["symbol"] for o in world.broker.orders].count(X) == 1
    assert out["result"][0] == 200  # the phone's exit is the one that closed it
    assert [world.broker.true_qty(s) for s in (X, Y, Z)] == [0, 0, 0]


def test_s22b_the_same_with_a_read_inside_the_note_closes_once(world, monkeypatch):
    monkeypatch.setattr(exit_guard, "EXIT_SHOWS_WITHIN_S", 1.0)
    _slow_first_read(world, 0.6)
    phone, _ = world.background(world.phone_exit, X)
    time.sleep(0.2)
    world.api_exit_all()
    phone.join(10)
    assert never_reversed(world.broker, X), summary(world, X)
    assert [o["symbol"] for o in world.broker.orders].count(X) == 1


def test_s23_a_claim_taken_for_a_read_that_finds_nothing_to_close_is_let_go(world):
    """A position size of 0 for a flat position sends nothing, and its claim ends with it."""
    world.broker.base[X] = 0
    _code, body = world.tv_smart_exit(X)
    assert body["message"] == "No open position found. Not placing exit order."
    assert world.broker.orders == [] and len(exit_guard.EXITS) == 0
    world.broker.base[X] = 65
    upstox._POSITION_BOOK.invalidate("token")  # the read just made is cached for a second
    world.phone_exit(X)  # the position is free: nothing stranded a claim on it
    assert [o["symbol"] for o in world.broker.orders] == [X]
