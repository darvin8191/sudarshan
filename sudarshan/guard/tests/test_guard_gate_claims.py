"""Claims end with their orders (M5.13, the gate review's H1).

A claim is what this process sent on a position that the broker's book does not show yet. It
must never outlive the order it stands for, or an exit is refused as "already on its way" under
the kill switch, the daily lock, the size cap or the burst brake:

- a send let go while nothing judged it counts only while it waits for its answer;
- a cancel the broker confirmed ends the claims on its order at once;
- before an order is refused only because of claims, the order book is read once, and a claim
  whose order shows cancelled, rejected or still resting is dropped.

The Upstox book is the fake of test_guard_gate.py, with the order book's statuses beside it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from test_guard_gate import CE, ON, Book, broker, order, refused_message, row, working
from test_guard_gate_size import sized  # noqa: F401 - fixture

from sudarshan.guard import flag, gate, readers, rules
from sudarshan.guard.store import KillSwitchState


@pytest.fixture
def claims(risk_store, monkeypatch):
    book = Book()
    fresh = gate.Gate(store=risk_store, read=book)
    fresh.set_state(KillSwitchState())
    monkeypatch.setattr(gate, "GATE", fresh)
    monkeypatch.setenv(flag.ENV, "on")
    return SimpleNamespace(gate=fresh, book=book, store=risk_store)


def _stop(quantity=65):
    return order("SELL", quantity, pricetype="SL-M", trigger_price="98.5")


def test_a_send_never_judged_counts_only_until_it_is_answered(claims):
    """Gate review H1 (a): 130 held, 65 sold while nothing was refusing (filled: the book shows
    65), then the switch goes on: the exit of the other 65 goes."""
    claims.book.rows = [row(CE, 130)]
    upstox = broker()
    assert upstox.place(order("SELL", 65), "token")[2] == "OID0"  # never judged
    claims.book.rows = [row(CE, 65)]
    claims.gate.set_state(ON)
    assert upstox.place(order("SELL", 65), "token")[2] == "OID1"


def test_a_send_never_judged_counts_while_it_is_on_its_way(claims):
    """The same exposure as before the gate, no more: while the first exit waits for Upstox's
    answer, a second one for what it already sells is refused under the switch."""
    claims.book.rows = [row(CE, 130)]
    said = []

    def slow_upstox(data):
        if not said:  # inside the first call: it has no answer yet
            claims.gate.set_state(ON)
            said.append(refused_message(upstox.place(order("SELL", 65), "token")))
        return SimpleNamespace(status=200, status_code=200), {"status": "success"}, "OID"

    upstox = broker(slow_upstox)
    assert upstox.place(order("SELL", 130), "token")[2] == "OID"
    assert said and "already on its way" in said[0]


def test_a_cancel_the_broker_confirmed_ends_its_order_s_claims(claims):
    """Gate review H1 (b): a stop placed a moment ago, cancelled (by the kill switch's own
    sweep, say): the exit that follows is not refused for it."""
    claims.gate.set_state(ON)
    claims.book.rows = [row(CE, 65)]
    upstox = broker()
    assert upstox.place(_stop(), "token")[2] == "OID0"  # an exit: it goes, and claims 65
    assert "already on its way" in refused_message(upstox.place(order("SELL"), "token"))
    gate.cancelled("OID0")
    assert upstox.place(order("SELL"), "token")[2] == "OID1"


def test_upstox_s_cancel_order_ends_the_claims_when_it_went_through(claims, monkeypatch):
    from broker.upstox.api import order_api

    claims.gate.set_state(ON)
    claims.book.rows = [row(CE, 65)]
    upstox = broker()
    assert upstox.place(_stop(), "token")[2] == "OID0"
    answers = iter(
        [
            {"status": "error", "message": "timed out", "not_confirmed": True},
            {"status": "success", "data": {"order_id": "OID0"}},
        ]
    )
    monkeypatch.setattr(order_api, "get_api_response", lambda *args, **kwargs: next(answers))
    assert order_api.cancel_order("OID0", "token")[1] == 502  # not confirmed: the claim stays
    refused_message(upstox.place(order("SELL"), "token"))
    assert order_api.cancel_order("OID0", "token")[1] == 200
    assert upstox.place(order("SELL"), "token")[2] == "OID1"


@pytest.mark.parametrize(
    ("status", "goes"),
    [
        ("cancelled", True),  # cancelled in Upstox's app: the gate never saw it
        ("rejected", True),
        ("trigger pending", True),  # still resting: nothing of it is on its way to the book
        ("open", True),
        ("complete", False),  # filled: the book is about to show it
        (None, False),  # not in the order book yet
    ],
)
def test_an_exit_held_up_by_claims_reads_the_order_book_once(sized, status, goes):  # noqa: F811
    """Gate review H1 (c): the size cap at 2 lots, 3 lots held, a 3-lot stop resting; the
    stop is cancelled outside SUDARSHAN and the owner exits at market."""
    sized.book.rows = [row(CE, 195)]
    upstox = broker()
    assert upstox.place(_stop(195), "token")[2] == "OID0"  # an exit: never held to the cap
    if status:
        sized.book.statuses = {"OID0": status}
    answer = upstox.place(order("SELL", 195), "token")
    if goes:
        assert answer[2] == "OID1"
    else:
        refused_message(answer)
    assert sized.book.calls.count("statuses") == 1


def test_an_order_book_that_cannot_be_read_ends_no_claim(claims):
    claims.gate.set_state(ON)
    claims.book.rows = [row(CE, 65)]
    upstox = broker()
    assert upstox.place(_stop(), "token")[2] == "OID0"
    claims.book.fail.add("statuses")
    refused_message(upstox.place(order("SELL"), "token"))


def test_no_order_book_read_for_an_order_that_adds_risk_whatever_was_sent(claims):
    claims.gate.set_state(ON)
    claims.book.rows = [row(CE, 65)]
    upstox = broker()
    assert upstox.place(_stop(), "token")[2] == "OID0"
    refused_message(upstox.place(order("SELL", 130), "token"))  # past the position anyway
    refused_message(upstox.place(order("BUY", 65), "token"))  # the other side: adds
    assert claims.book.calls.count("statuses") == 0


def test_claims_on_the_intraday_position_never_hold_up_the_carried_one(claims):
    """MIS and NRML are two positions at Upstox (I and D): a claim on one is not on the other
    (mutant G18)."""
    claims.gate.set_state(ON)
    claims.book.rows = [row(CE, 65, "I"), row(CE, 65, "D")]
    upstox = broker()
    assert upstox.place(order("SELL", 65, product="MIS"), "token")[2] == "OID0"
    assert upstox.place(order("SELL", 65, product="NRML"), "token")[2] == "OID1"
    refused_message(upstox.place(order("SELL", 65, product="MIS"), "token"))


def test_a_change_answered_with_an_error_may_still_have_gone(claims):
    """Upstox's modify answers 400 for a timeout after it may have acted, so the change keeps
    its claim (mutant G33): here it went through and filled, which the positions book does
    not show yet."""
    claims.gate.set_state(ON)
    claims.book.rows = [row(CE, 65)]
    claims.book.orders = {"X1": working(rules.SELL, 65, price=140.0)}
    claims.book.statuses = {"X1": "complete"}

    @gate.upstox_modify
    def modify_order(data, auth):
        return {"status": "error", "message": "Read timed out"}, 400

    change = {"orderid": "X1", "quantity": "65", "pricetype": "LIMIT", "price": "139"}
    assert modify_order(change, "token")[1] == 400
    refused = refused_message(broker().place(order("SELL", 65), "token"))
    assert "already on its way" in refused


def test_the_sandbox_reads_its_own_orders_for_claims(risk_store, monkeypatch):
    class SandboxBook:
        def __init__(self):
            self.statuses = {}
            self.position = 65
            self.calls = []

        def sandbox_position(self, owner, symbol, exchange, product):
            return self.position

        def sandbox_order_statuses(self, owner, refs):
            self.calls.append(("statuses", owner, tuple(refs)))
            return {ref: self.statuses[ref] for ref in refs if ref in self.statuses}

    book = SandboxBook()
    fresh = gate.Gate(store=risk_store, read=book)
    fresh.set_state(ON)
    monkeypatch.setattr(gate, "GATE", fresh)
    monkeypatch.setenv(flag.ENV, "on")
    sent = []

    @gate.sandbox_place
    def place_order(manager, order_data):
        sent.append(order_data)
        return True, {"status": "success", "orderid": f"S{len(sent)}"}, 200

    manager = SimpleNamespace(user_id="owner")
    stop = {**order("SELL", 65, symbol="SBIN", exchange="NSE"), "price_type": "SL-M"}
    assert place_order(manager, stop)[2] == 200  # an exit, claimed: S1
    book.statuses = {"S1": "trigger pending"}
    assert place_order(manager, order("SELL", 65, symbol="SBIN", exchange="NSE"))[2] == 200
    assert book.calls == [("statuses", "owner", ("S1",))]


def test_readers_name_each_order_s_status_as_openalgo_does(monkeypatch):
    from broker.upstox.api import order_api

    book = {
        "status": "success",
        "data": [
            {"order_id": "A", "status": "cancelled"},
            {"order_id": "B", "status": "trigger pending"},
            {"order_id": "C", "status": "complete"},
            {"order_id": "D", "status": "after market order req received"},
            {"status": "open"},
        ],
    }
    monkeypatch.setattr(order_api, "get_order_book", lambda auth: book)
    statuses = readers.live_order_statuses("token")
    assert statuses["A"] == "cancelled" and statuses["C"] == "complete"
    assert statuses["B"] == "open" and "D" in statuses  # a resting stop reads as open
    monkeypatch.setattr(order_api, "get_order_book", lambda auth: {"status": "error"})
    with pytest.raises(readers.Unreadable):
        readers.live_order_statuses("token")
