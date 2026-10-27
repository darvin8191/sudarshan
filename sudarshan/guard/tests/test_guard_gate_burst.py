"""The burst brake in the gate (M5.14): orders adding risk beyond the owner's number in a rolling
window are refused, with when new orders go again; exits are never counted or refused; a batch
counts once; the owner is told once a window."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from test_guard_gate import CE, PE, Book, broker, clock, order, refused_message, row  # noqa: F401

from sudarshan.books.exit_guard import clock as india_clock
from sudarshan.guard import flag, gate, words
from sudarshan.guard.store import KillSwitchState


@pytest.fixture
def braked(risk_store, monkeypatch, clock):  # noqa: F811 - the fake clock of test_guard_gate
    book = Book()
    fresh = gate.Gate(store=risk_store, read=book)
    fresh.set_state(KillSwitchState())
    monkeypatch.setattr(gate, "GATE", fresh)
    monkeypatch.setenv(flag.ENV, "on")
    risk_store.save_settings("owner", burst_on=True, burst_n=3, burst_window_s=60.0)
    told: list[str] = []
    monkeypatch.setattr(gate, "_tell_owner", told.append)
    return SimpleNamespace(gate=fresh, book=book, store=risk_store, told=told, clock=clock)


def _new(upstox, symbol=PE):
    return upstox.place(order("BUY", 65, symbol=symbol), "token")


def test_past_the_owner_s_number_a_new_order_is_refused_with_when_it_goes_again(braked):
    upstox = broker()
    first = braked.clock.time()
    for _ in range(3):
        assert _new(upstox)[0].status == 200
    braked.clock.now += 10
    message = refused_message(_new(upstox))
    assert message == words.burst_refused(3, 60.0, first + 60)
    assert message.startswith(
        "New orders are paused by your burst brake: 3 new orders went in the last 1 minute, the "
        "most it allows (Settings > Risk). Exits and cancels still go. New orders go again at "
    )
    assert message.endswith(f"{india_clock(first + 60)}.")
    assert len(upstox.reached) == 3


def test_exits_are_never_counted_or_refused(braked):
    braked.book.rows = [row(CE, 650)]
    upstox = broker()
    for _ in range(5):
        assert upstox.place(order("SELL", 65), "token")[0].status == 200  # exits: not counted
    for _ in range(3):
        assert _new(upstox)[0].status == 200
    refused_message(_new(upstox))
    assert upstox.place(order("SELL", 65), "token")[0].status == 200  # full: an exit still goes


def test_the_window_rolls(braked):
    upstox = broker()
    for _ in range(3):
        _new(upstox)
    refused_message(_new(upstox))
    braked.clock.now += 60
    for _ in range(3):  # the first three have left the window, and the refusal never counted
        assert _new(upstox)[0].status == 200
    refused_message(_new(upstox))


def test_the_owner_is_told_once_a_window(braked):
    upstox = broker()
    for _ in range(3):
        _new(upstox)
    for _ in range(4):
        refused_message(_new(upstox))
    assert len(braked.told) == 1
    assert braked.told[0].startswith("Burst brake: 3 new orders went in 1 minute, so new orders ")
    braked.clock.now += 60
    for _ in range(3):
        _new(upstox)
    refused_message(_new(upstox))
    assert len(braked.told) == 2


def test_a_batch_counts_once(braked):
    parts = [gate._upstox_place(order("BUY", 65, symbol=s), "token") for s in (CE, PE, CE)]
    assert braked.gate.check_together(parts) is None  # one admission, its three parts noted
    upstox = broker()
    for symbol in (CE, PE, CE):
        assert _new(upstox, symbol)[0].status == 200  # the batch's parts: not counted again
    assert _new(upstox)[0].status == 200  # two
    assert _new(upstox)[0].status == 200  # three
    refused_message(_new(upstox))


def test_a_batch_is_refused_whole_once_the_brake_is_full(braked):
    upstox = broker()
    for _ in range(3):
        _new(upstox)
    parts = [gate._upstox_place(order("BUY", 65, symbol=s), "token") for s in (CE, PE)]
    found = braked.gate.check_together(parts)
    assert found is not None
    index, decision = found
    assert index is None and decision.code == gate.BURST_BRAKE


def test_with_the_brake_off_nothing_is_counted(braked):
    braked.store.save_settings("owner", burst_on=False)
    upstox = broker()
    for _ in range(10):
        assert _new(upstox)[0].status == 200
    assert braked.book.calls == []
