"""The burst brake in the gate (M5.14): orders adding risk beyond the owner's number in a rolling
window are refused, with when new orders go again; exits and changes to an order are never
counted or refused; a batch counts once, its every order (each slice of a split) then going on
a credit no other order can use; the owner is told once a window."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from test_guard_gate import (  # noqa: F401 - clock: the fake clock fixture
    CE,
    PE,
    Book,
    broker,
    change,
    clock,
    order,
    refused_message,
    row,
    working,
)
from test_guard_gate_size import services, sized  # noqa: F401 - fixtures

from sudarshan.books.exit_guard import clock as india_clock
from sudarshan.guard import flag, gate, rules, words
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


def test_a_change_to_an_order_is_never_counted(braked):
    """Chasing a fill by raising a working entry's limit is not a burst of new orders."""
    braked.book.orders = {"E1": working(rules.BUY, 65, price=100.0)}
    upstox = broker()
    for price in (101, 102, 103, 104):
        assert upstox.modify(change("E1", 65, price), "token")[1] == 200
    for _ in range(3):
        assert _new(upstox)[0].status == 200
    refused_message(_new(upstox))


def test_a_full_brake_never_refuses_a_batch_of_exits(braked):
    braked.book.rows = [row(CE, 130), row(PE, -65)]
    upstox = broker()
    for _ in range(3):
        _new(upstox, CE)
    exits = [
        gate._upstox_place(order("SELL", 65, symbol=CE), "token"),
        gate._upstox_place(order("BUY", 65, symbol=PE), "token"),
    ]
    assert braked.gate.check_together(exits) is None


def test_live_and_the_sandbox_are_counted_apart(braked, monkeypatch):
    upstox = broker()
    for _ in range(3):
        _new(upstox)
    refused_message(_new(upstox))
    monkeypatch.setattr(braked.book, "sandbox_position", lambda *args: 0, raising=False)

    @gate.sandbox_place
    def sandbox_place_order(manager, order_data):
        return True, {"status": "success", "orderid": "S1"}, 200

    manager = SimpleNamespace(user_id="owner")
    for _ in range(3):  # the sandbox's own count: Live's full brake is not its
        assert sandbox_place_order(manager, order("BUY", 65, symbol=PE))[2] == 200
    assert sandbox_place_order(manager, order("BUY", 65, symbol=PE))[2] == 403


def test_a_batch_s_credits_end_after_thirty_seconds(braked):
    parts = [gate._upstox_place(order("BUY", 65, symbol=s), "token") for s in (CE, PE)]
    assert braked.gate.check_together(parts) is None  # one admission
    upstox = broker()
    assert _new(upstox, CE)[0].status == 200  # the batch's CE part: on its credit
    braked.clock.now += gate.BATCH_PARTS_WITHIN_S + 0.5
    assert _new(upstox, PE)[0].status == 200  # the PE part came too late: counted (two)
    assert _new(upstox, CE)[0].status == 200  # three
    refused_message(_new(upstox, PE))


def test_an_order_between_a_batch_s_parts_takes_none_of_its_credits(braked):
    """Credits are the batch's own: an order of another size, from another surface, sent while
    the basket's legs are on their way is counted, so the basket is never left half placed."""
    upstox = broker()
    for _ in range(2):
        assert _new(upstox, CE)[0].status == 200
    legs = [
        gate._upstox_place(order("BUY", 65, symbol=CE), "token"),
        gate._upstox_place(order("SELL", 65, symbol=PE), "token"),
    ]
    assert braked.gate.check_together(legs) is None  # the third admission
    refused_message(upstox.place(order("BUY", 130, symbol=CE), "token"))  # not the basket's
    assert upstox.place(order("BUY", 65, symbol=CE), "token")[0].status == 200  # leg 1
    assert upstox.place(order("SELL", 65, symbol=PE), "token")[0].status == 200  # leg 2


def test_a_split_counts_once_and_every_slice_goes(services, sized, clock):  # noqa: F811
    """Brake at 3 a minute: a split of 6 slices (5 of 65 and 15) is one new order."""
    sized.store.save_settings("owner", size_on=False, burst_on=True, burst_n=3)
    data = {**order("BUY", 340, symbol=PE), "splitsize": 65, "apikey": "key"}
    ok, body, status = services.split.split_order_with_auth(data, "token", "upstox", data)
    assert (ok, status) == (True, 200)
    assert [int(part["quantity"]) for _, part in services.reached] == [65] * 5 + [15]
    assert all(result["status"] == "success" for result in body["results"])
    upstox = broker()
    for _ in range(2):
        assert _new(upstox)[0].status == 200
    refused_message(_new(upstox))


def test_a_multi_leg_order_s_split_legs_count_once(services, sized, clock, monkeypatch):  # noqa: F811
    sized.store.save_settings("owner", size_on=False, burst_on=True, burst_n=3)

    def resolved(leg, common, api_key, ltp):
        return True, {"symbol": CE if leg["option_type"] == "CE" else PE, "exchange": "NFO"}, 200

    monkeypatch.setattr(services.multi, "resolve_leg_symbol", resolved)
    monkeypatch.setattr(services.multi, "get_underlying_ltp", lambda *args: (True, 25900.0, None))
    monkeypatch.setattr(services.multi, "get_order_rate_limit", lambda: 0)
    legs = [
        {"offset": "ATM", "option_type": "CE", "action": "BUY", "quantity": 195, "splitsize": 65},
        {"offset": "ATM", "option_type": "PE", "action": "BUY", "quantity": 130, "splitsize": 65},
    ]
    data = {"apikey": "key", "underlying": "NIFTY", "exchange": "NSE_INDEX",
            "expiry_date": "27OCT26", "strategy": "s", "legs": legs}  # fmt: skip
    ok, _body, status = services.multi.process_multiorder_with_auth(
        data, "token", "upstox", "key", data
    )
    assert (ok, status) == (True, 200)
    assert len(services.reached) == 5  # every slice of both legs
