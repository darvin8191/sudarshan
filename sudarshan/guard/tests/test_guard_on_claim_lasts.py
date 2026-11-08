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
from sudarshan.guard import flag, gate, readers
from sudarshan.guard.store import KillSwitchState

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


def test_l1_a_claim_seen_working_then_complete_stands_150_s_from_that_read(switch_on, book, clock):
    """Defence in depth (review LOW-4): the read at 160 s showed the order working, so its claim
    counts from then; at 250 s the order shows complete while the positions row still lags, and
    the claim stands (90 s since the read that showed it working), as it would for a fresh exit."""
    upstox = broker()
    _first_exit(book, upstox)
    clock.now += PAST
    assert "already on its way" in refused_message(upstox.place(order("SELL"), "token"))
    book.statuses["OID0"] = "complete"
    clock.now += 90
    assert "already on its way" in refused_message(upstox.place(order("SELL"), "token"))
    clock.now += gate.SEND_HELD_AT_MOST_S  # well past 150 s from that read: dropped on this one
    assert upstox.place(order("SELL"), "token")[2] == "OID1"


# -- the review of this branch, HIGH-1: only a market exit's claim outlasts 150 s --------------


LIMIT = 4000.0


def _day_row(symbol: str, quantity: int) -> dict:
    return {**row(symbol, quantity), "realised": 0.0, "unrealised": 0.0}


@pytest.fixture
def judged(risk_store, monkeypatch, book):
    """Stage `on` with a daily loss limit set: every order is judged, entries included, so an
    entry's send is registered as a claim too."""
    from types import SimpleNamespace

    import database.user_db as user_db
    import extensions
    from sudarshan.guard import killswitch

    fresh = gate.Gate(store=risk_store, read=book)
    fresh.set_state(KillSwitchState())
    monkeypatch.setattr(gate, "GATE", fresh)
    monkeypatch.setenv(flag.ENV, "on")
    risk_store.save_settings("owner", daily_loss_limit=LIMIT)
    monkeypatch.setattr(fresh, "_in_session", lambda exchange, at: True)
    monkeypatch.setattr(killswitch, "_tell", lambda owner, text: None)
    monkeypatch.setattr(user_db, "find_user_by_username", lambda: SimpleNamespace(username="o"))
    monkeypatch.setattr(extensions, "emit_from_any_thread", lambda *args, **kw: None)
    return fresh


def _short_then_long(book, clock, upstox):
    """10:00 TradingView sells 65 (short). 10:30 it reverses: BUY 130 (long 65). Both fill."""
    book.rows = []
    assert upstox.place(order("SELL", 65), "token")[2] == "OID0"
    book.rows = [_day_row(CE, -65)]
    clock.now += 1800
    assert upstox.place(order("BUY", 130), "token")[2] == "OID1"
    book.rows = [_day_row(CE, 65)]
    book.statuses.update({"OID0": "complete", "OID1": "complete"})
    clock.now += 1800  # an hour after the entry, half an hour after the reversal


def test_h1_under_the_switch_a_reversal_still_sends_its_exit_after_an_old_entry_on_its_side(
    judged, book, clock
):
    """Decision a: under the kill switch a reversal sends its exit to 0 and only the part beyond
    is refused. On 7fd4e44 the 10:00 SELL entry's claim, kept 16 h, took the whole room of the
    11:00 SELL 130: refused whole, the long left open, "an exit is already on its way"."""
    upstox = broker()
    _short_then_long(book, clock, upstox)
    judged.set_state(ON)
    res, body, ref = upstox.place(order("SELL", 130), "token")
    sent = [(what, data["quantity"]) for what, data in upstox.reached][2:]
    assert sent == [("place", "65")], f"exit part not sent: {body.get('message')}"


def test_h1_under_the_switch_a_plain_exit_is_not_refused_for_an_old_entry_when_the_book_fails(
    judged, book, clock
):
    upstox = broker()
    _short_then_long(book, clock, upstox)
    judged.set_state(ON)
    book.fail.add("statuses")
    res, body, ref = upstox.place(order("SELL", 65), "token")
    assert ref == "OID2", f"exit refused: {body.get('message')}"


def test_h1_under_the_switch_a_plain_exit_pays_no_order_book_read_for_an_old_entry(
    judged, book, clock
):
    upstox = broker()
    _short_then_long(book, clock, upstox)
    judged.set_state(ON)
    book.calls.clear()
    assert upstox.place(order("SELL", 65), "token")[2] == "OID2"
    assert book.calls.count("statuses") == 0, book.calls


def test_h1_entries_do_not_pile_up_as_claims_all_day(judged, book, clock):
    upstox = broker()
    _short_then_long(book, clock, upstox)
    assert judged.sends() == 0


def test_h1_a_resting_stop_s_claim_ends_150_s_after_its_answer_as_before(judged, book, clock):
    upstox = broker()
    book.rows = [_day_row(CE, 65)]
    assert upstox.place(order("SELL", pricetype="SL-M", trigger_price="98.5"), "token")[2]
    assert judged.sends() == 1
    clock.now += PAST
    judged.set_state(ON)
    assert upstox.place(order("SELL"), "token")[2] == "OID1"  # no read, no refusal
    assert judged.sends() == 1  # the exit's own claim; the stop's is gone


def test_h1_e2e_a_tradingview_reversal_under_the_switch_still_exits_after_an_entry_that_day(
    monkeypatch, risk_store, ticking
):
    """Through the real smart order service over the simulated Upstox: 10:00 SELL 65, 10:30 BUY
    130 to +65, the switch on, 11:00 SELL 130 to -65: the exit goes and the account ends flat."""
    from broker.upstox.api import order_api

    world = _world(monkeypatch, risk_store, {CE: 0}, KillSwitchState())
    # A burst brake set high judges every new order (entries registered as claims) with no day
    # figure to read; the daily limit does the same in production.
    risk_store.save_settings("owner", burst_on=True, burst_n=100, burst_window_s=60.0)
    upstox = world.broker
    world.tv_smart_exit(CE, quantity="65", action="SELL", position_size="-65")
    assert upstox.true_qty(CE) == -65
    ticking.advance(1800)
    order_api._POSITION_BOOK.invalidate("token")
    world.tv_smart_exit(CE, quantity="130", action="BUY", position_size="65")
    assert upstox.true_qty(CE) == 65
    ticking.advance(1800)
    order_api._POSITION_BOOK.invalidate("token")
    gate.GATE.set_state(SWITCH_ON)
    code, body = world.tv_smart_exit(CE, quantity="130", action="SELL", position_size="-65")
    assert upstox.true_qty(CE) == 0, f"long left open: {code} {body}"
