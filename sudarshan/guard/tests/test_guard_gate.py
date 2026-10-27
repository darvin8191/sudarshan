"""The gate on Upstox's order calls (M5.13): admit, claim, settle, refuse, and the error policy.

A Gate of each test's own reads a fake book (``Book``) and a throwaway risk database, and fake
order functions carry the real decorators. The flag-off pin and the barrier test are here; the
real services and Upstox module over a simulated Upstox are in test_guard_gate_services.py.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from sudarshan.guard import flag, gate, readers, rules, words
from sudarshan.guard.store import KillSwitchState

CE = "NIFTY27OCT2625900CE"
PE = "NIFTY27OCT2625900PE"
TOKENS = {(CE, "NFO"): "NSE_FO|49213", (PE, "NFO"): "NSE_FO|49214", ("RELIANCE", "NSE"): "NSE_EQ|R"}
SINCE = datetime(2026, 10, 27, 5, 12, tzinfo=UTC)  # 10:42 in India
ON = KillSwitchState(on=True, since=SINCE, surface="app", actor="owner", activation_id="ks-1")
WAIT = 10


def row(symbol: str, quantity: int, product: str = "I") -> dict:
    return {"instrument_token": TOKENS[(symbol, "NFO")], "product": product, "quantity": quantity}


class Book:
    """The broker's books as the gate reads them, with every read counted."""

    def __init__(self):
        self.rows: list[dict] = []
        self.cached: list[dict] | None = None
        self.orders: dict[str, readers.Working] = {}
        self.gtts: dict[str, readers.Gtt] = {}
        self.calls: list[str] = []
        self.fail: set[str] = set()
        self.before_rows = None  # called inside a positions read (the barrier tests)

    def _read(self, name):
        self.calls.append(name)
        if name in self.fail:
            raise readers.Unreadable(name)

    def live_cached_rows(self, auth):
        self.calls.append("cached")
        return None if self.cached is None else list(self.cached)

    def live_rows(self, auth):
        self._read("rows")
        if self.before_rows:
            self.before_rows()
        return list(self.rows)

    def instrument_token(self, symbol, exchange):
        self._read("token")
        return TOKENS.get((symbol, exchange))

    def live_working_order(self, auth, ref):
        self._read("orders")
        return self.orders.get(ref)

    def live_gtt(self, auth, ref):
        self._read("gtts")
        return self.gtts.get(ref)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def time(self):
        return 1_761_541_925.0 + self.now - 1000.0


@pytest.fixture
def book():
    return Book()


@pytest.fixture
def the_gate(risk_store, book, monkeypatch):
    fresh = gate.Gate(store=risk_store, read=book)
    monkeypatch.setattr(gate, "GATE", fresh)
    monkeypatch.setenv(flag.ENV, "on")
    return fresh


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(gate, "time", SimpleNamespace(monotonic=fake.monotonic, time=fake.time))
    return fake


class Sent:
    status = 200
    status_code = 200


def broker(answer=None):
    """Fake Upstox order functions carrying the real decorators, and what reached them."""
    reached: list[tuple[str, dict]] = []
    placed = answer or (lambda data: (Sent(), {"status": "success"}, f"OID{len(reached) - 1}"))

    @gate.upstox_place
    def place_order_api(data, auth):
        reached.append(("place", data))
        return placed(data)

    @gate.upstox_modify
    def modify_order(data, auth):
        reached.append(("modify", data))
        return {"status": "success", "orderid": data.get("orderid")}, 200

    @gate.upstox_gtt_place
    def place_gtt_order(data, auth):
        reached.append(("gtt_place", data))
        return Sent(), {"status": "success"}, "GTT-1"

    @gate.upstox_gtt_modify
    def modify_gtt_order(data, auth):
        reached.append(("gtt_modify", data))
        return {"status": "success", "trigger_id": data.get("trigger_id")}, 200

    return SimpleNamespace(
        place=place_order_api,
        modify=modify_order,
        gtt_place=place_gtt_order,
        gtt_modify=modify_gtt_order,
        reached=reached,
    )


def order(action="BUY", quantity=65, symbol=CE, product="MIS", exchange="NFO", **extra):
    return {
        "symbol": symbol,
        "exchange": exchange,
        "action": action,
        "quantity": str(quantity),
        "product": product,
        "pricetype": "MARKET",
        **extra,
    }


def refused_message(answer) -> str:
    res, body, ref = answer
    assert (res.status, res.status_code, ref) == (403, 403, None)
    assert body["status"] == "error"
    return body["message"]


# -- the flag off: as before, byte for byte ----------------------------------------------------


def test_with_the_flag_off_the_function_runs_as_before_and_nothing_else(monkeypatch, book):
    """One environment lookup, then the function's own answer, the very same object."""
    monkeypatch.delenv(flag.ENV, raising=False)
    looked = []
    original = flag.stage
    monkeypatch.setattr(flag, "stage", lambda: looked.append(1) or original())

    def no_gate(*args, **kwargs):
        raise AssertionError("the gate was asked")

    monkeypatch.setattr(gate.Gate, "run", no_gate)
    answer = (Sent(), {"status": "success"}, "OID")
    calls = []

    @gate.upstox_place
    def place_order_api(data, auth):
        calls.append((data, auth))
        return answer

    data = order()
    assert place_order_api(data, "token") is answer
    assert calls == [(data, "token")] and data == order()  # its arguments untouched
    assert looked == [1]
    assert book.calls == []


@pytest.mark.parametrize("stage", ["off", "sandbox", "garbled"])
def test_a_stage_that_leaves_live_out_never_asks_the_gate(monkeypatch, stage):
    monkeypatch.setenv(flag.ENV, stage)
    monkeypatch.setattr(gate.Gate, "run", lambda *a, **k: pytest.fail("the gate was asked"))
    upstox = broker()
    upstox.place(order(), "token")
    upstox.modify({"orderid": "1", "quantity": "65", "pricetype": "LIMIT", "price": "1"}, "token")
    upstox.gtt_place(order(trigger_type="OCO"), "token")
    upstox.gtt_modify({"trigger_id": "GTT-1", "quantity": "65"}, "token")
    assert [what for what, _ in upstox.reached] == ["place", "modify", "gtt_place", "gtt_modify"]


def test_the_decorators_keep_the_function_s_name():
    from broker.upstox.api import gtt_api, order_api

    for fn in (
        order_api.place_order_api,
        order_api.modify_order,
        gtt_api.place_gtt_order,
        gtt_api.modify_gtt_order,
    ):
        assert fn.__wrapped__.__name__ == fn.__name__


# -- the switch off: let go, no read -----------------------------------------------------------


def test_with_the_switch_off_an_order_goes_with_no_read(the_gate, book):
    the_gate.set_state(KillSwitchState())
    upstox = broker()
    answer = upstox.place(order(), "token")
    assert answer[2] == "OID0"
    assert book.calls == []  # no positions, no token, nothing
    assert the_gate.sends() == 1  # recorded, for an activation to wait on


def test_the_switch_is_read_once_from_the_store(the_gate, risk_store, book):
    risk_store.save_state(on=True, since=SINCE)
    upstox = broker()
    message = refused_message(upstox.place(order(), "token"))
    assert message.startswith("New orders are refused: the kill switch is on since 10:42.")
    assert the_gate.state().on


# -- the switch on ---------------------------------------------------------------------------------


def test_every_upstox_order_call_refuses_new_risk_with_a_sentence(the_gate, book):
    the_gate.set_state(ON)
    book.gtts["GTT-9"] = readers.Gtt("SINGLE", rules.BUY, "MIS", CE, "NFO")
    book.orders["W1"] = readers.Working(
        rules.WorkingOrder(rules.BUY, 0, rules.OrderTerms(65, "LIMIT", 100.0)),
        "NFO",
        CE,
        "I",
        "NSE_FO|49213",
        final=False,
    )
    upstox = broker()
    entry = (
        "New orders are refused: the kill switch is on since 10:42. Exits and cancels still go. "
        "To trade again, resume from Settings > Risk or send /killswitch in Telegram."
    )
    assert refused_message(upstox.place(order(), "token")) == entry
    assert upstox.modify(
        {"orderid": "W1", "quantity": "65", "pricetype": "LIMIT", "price": "105"}, "token"
    ) == ({"status": "error", "message": entry}, 403)
    gtt_entry = refused_message(upstox.gtt_place(order(trigger_type="SINGLE"), "token"))
    assert gtt_entry == entry
    assert upstox.gtt_modify(
        {"trigger_id": "GTT-9", "quantity": "130", "action": "BUY"}, "token"
    ) == ({"status": "error", "message": entry}, 403)
    assert upstox.reached == []  # nothing was sent


def test_an_exit_goes_and_a_second_one_for_the_same_quantity_does_not(the_gate, book):
    the_gate.set_state(ON)
    book.rows = [row(CE, 65)]
    upstox = broker()
    assert upstox.place(order("SELL"), "token")[2] == "OID0"
    message = refused_message(upstox.place(order("SELL"), "token"))
    assert message.startswith("An exit for NIFTY 25900 CE is already on its way (sent ")
    assert message.endswith(
        "The kill switch is on since 10:42, so nothing beyond the open position can be sent."
    )
    assert len(upstox.reached) == 1


def test_exits_already_sent_leave_what_is_left_to_exit(the_gate, book):
    the_gate.set_state(ON)
    book.rows = [row(PE, -130)]
    upstox = broker()
    upstox.place(order("BUY", 65, PE), "token")
    crossing = refused_message(upstox.place(order("BUY", 130, PE), "token"))
    assert "to exit, buy 65." in crossing
    assert upstox.place(order("BUY", 65, PE), "token")[2] == "OID1"
    assert [d["quantity"] for _, d in upstox.reached] == ["65", "65"]


def test_an_exit_the_book_shows_no_longer_counts(the_gate, book):
    the_gate.set_state(ON)
    book.rows = [row(CE, 130)]
    upstox = broker()
    upstox.place(order("SELL", 65), "token")
    upstox.place(order("SELL", 65), "token")
    assert refused_message(upstox.place(order("SELL", 65), "token"))  # both claimed
    assert the_gate.sends() == 2
    book.rows = [row(CE, 65)]  # the first has filled; the second has not
    assert refused_message(upstox.place(order("SELL", 65), "token"))
    assert the_gate.sends() == 1  # the first, shown, was dropped as the book was read
    book.rows = [row(CE, 0)]
    refused_message(upstox.place(order("SELL", 65), "token"))  # flat now: it would open
    assert the_gate.sends() == 0  # the second, shown too


def test_a_claim_lasts_until_shortly_after_its_answer(the_gate, book, clock):
    the_gate.set_state(ON)
    book.rows = [row(CE, 65)]
    upstox = broker()
    upstox.place(order("SELL"), "token")
    clock.now += gate.SHOWS_WITHIN_S - 1
    refused_message(upstox.place(order("SELL"), "token"))
    clock.now += 2  # the book still shows 65: an exit gone and not shown, after its time
    assert upstox.place(order("SELL"), "token")[2] == "OID1"


def test_a_send_with_no_answer_counts_for_150_s_at_most(the_gate, book, clock):
    the_gate.set_state(ON)
    book.rows = [row(CE, 65)]
    kind = gate._Kind(gate.LIVE, gate._upstox_place, gate._refuse_with_response, None)
    held = the_gate.admit(kind, (order("SELL"), "token"), {})
    assert not held.refused  # admitted, never answered
    clock.now += 100
    assert the_gate.admit(kind, (order("SELL"), "token"), {}).refused
    clock.now += gate.SEND_HELD_AT_MOST_S - 100 + 1
    assert not the_gate.admit(kind, (order("SELL"), "token"), {}).refused


def test_an_exit_the_broker_refused_frees_its_quantity_at_once(the_gate, book):
    the_gate.set_state(ON)
    book.rows = [row(CE, 65)]

    class Refused:
        status = 400
        status_code = 400

    upstox = broker(lambda data: (Refused(), {"status": "error", "message": "no"}, None))
    upstox.place(order("SELL"), "token")
    upstox.place(order("SELL"), "token")
    assert len(upstox.reached) == 2  # the first sent nothing, so the second is not held back


def test_an_exit_that_raised_still_counts(the_gate, book):
    the_gate.set_state(ON)
    book.rows = [row(CE, 65)]

    def boom(data):
        raise RuntimeError("the connection dropped after the send")

    upstox = broker(boom)
    with pytest.raises(RuntimeError):
        upstox.place(order("SELL"), "token")
    refused_message(upstox.place(order("SELL"), "token"))


def test_a_cnc_sell_goes_without_a_read_and_an_oco_is_refused_without_one(the_gate, book):
    the_gate.set_state(ON)
    upstox = broker()
    cnc = order("SELL", 40, "RELIANCE", "CNC", "NSE")
    assert upstox.place(cnc, "token")[2] == "OID0"
    oco = refused_message(upstox.gtt_place(order("SELL", trigger_type="OCO"), "token"))
    assert "A two-leg GTT opens a position at Upstox" in oco
    assert book.calls == []


def test_cnc_on_a_derivative_is_judged_like_nrml(the_gate, book, clock):
    the_gate.set_state(ON)
    book.rows = []
    upstox = broker()
    refused_message(upstox.place(order("SELL", product="CNC"), "token"))
    book.rows = [row(CE, 65, "D")]
    clock.now += gate.BOOK_REUSE_S + 1
    assert upstox.place(order("SELL", product="CNC"), "token")[2] == "OID0"


# -- reads that fail, a state that cannot be read, a check that breaks -------------------------


def test_a_book_that_cannot_be_read_refuses_while_the_switch_is_on(the_gate, book, risk_store):
    the_gate.set_state(ON)
    book.fail = {"rows"}
    upstox = broker()
    message = refused_message(upstox.place(order("SELL"), "token"))
    assert message == words.book_unreadable("live", "10:42")
    assert "For an urgent exit, use the Upstox app." in message
    assert risk_store.history()[0].kind == "guard_unavailable"


def test_a_state_that_cannot_be_read_refuses_new_risk_and_lets_exits_go(
    the_gate, book, risk_store, clock, monkeypatch
):
    def broken():
        raise RuntimeError("disk")

    monkeypatch.setattr(risk_store, "state", broken)
    book.rows = [row(CE, 65)]
    upstox = broker()
    assert refused_message(upstox.place(order("BUY"), "token")) == words.STATE_UNREADABLE
    assert upstox.place(order("SELL"), "token")[2] == "OID0"
    monkeypatch.undo()
    monkeypatch.setenv(flag.ENV, "on")
    monkeypatch.setattr(gate, "GATE", the_gate)
    clock.now += gate.STATE_RETRY_S + 1  # read again: off
    book.calls.clear()
    assert upstox.place(order("BUY"), "token")[2] == "OID1"
    assert book.calls == []


@pytest.mark.parametrize(("on", "sent"), [(False, True), (True, False)])
def test_a_check_that_breaks_refuses_only_while_new_risk_is_refused(the_gate, risk_store, on, sent):
    the_gate.set_state(ON if on else KillSwitchState())
    upstox = broker()
    answer = upstox.place(None, "token")  # nothing the check can read
    assert bool(upstox.reached) is sent
    if not sent:
        assert refused_message(answer) == words.check_broke("live")
    assert risk_store.history()[0].kind in ("guard_error", "order_refused")


def test_refusals_of_one_kind_share_an_audit_row(the_gate, risk_store):
    the_gate.set_state(ON)
    upstox = broker()
    for _ in range(3):
        upstox.place(order("BUY"), "token")
    the_gate.flush_notes()
    (entry,) = risk_store.history()
    assert (entry.kind, entry.count, entry.pipe, entry.surface) == (
        "order_refused",
        3,
        "live",
        "background",
    )
    assert entry.detail["code"] == gate.KILL_SWITCH_ACTIVE and entry.detail["symbol"] == CE


# -- modifies and GTT modifies -----------------------------------------------------------------


def working(side, quantity, filled=0, price_type="LIMIT", price=100.0, trigger=0.0, final=False):
    return readers.Working(
        rules.WorkingOrder(side, filled, rules.OrderTerms(quantity, price_type, price, trigger)),
        "NFO",
        CE,
        "I",
        "NSE_FO|49213",
        final=final,
    )


def change(ref, quantity, price, pricetype="LIMIT", **extra):
    return {"orderid": ref, "quantity": str(quantity), "pricetype": pricetype, "price": str(price),
            **extra}  # fmt: skip


def test_a_modify_is_judged_as_the_order_the_broker_has(the_gate, book):
    the_gate.set_state(ON)
    book.orders = {
        "E1": working(rules.BUY, 130, price=100.0),  # an entry still working
        "X1": working(rules.SELL, 65, price=140.0),  # an exit
        "D1": working(rules.BUY, 65, final=True),
    }
    book.rows = [row(CE, 65)]
    upstox = broker()
    assert upstox.modify(change("E1", 65, 95), "token")[1] == 200  # less risk
    body, status = upstox.modify(change("E1", 130, 101), "token")  # more marketable
    assert status == 403 and body["message"].startswith("New orders are refused")
    assert upstox.modify(change("X1", 65, 135, action="BUY"), "token")[1] == 200  # side: broker's
    assert upstox.modify(change("D1", 130, 120), "token")[1] == 200  # done: the broker answers
    body, status = upstox.modify(change("NOPE", 65, 1), "token")
    assert status == 403 and body["message"] == words.order_not_found("live")


def test_a_modify_of_an_exit_this_process_sent_leaves_its_own_claim_out(the_gate, book):
    the_gate.set_state(ON)
    book.rows = [row(CE, 65)]
    upstox = broker()
    assert upstox.place(order("SELL"), "token")[2] == "OID0"
    book.orders = {"OID0": working(rules.SELL, 65, price=140.0)}
    assert upstox.modify(change("OID0", 65, 138), "token")[1] == 200
    assert upstox.modify(change("OID0", 65, 137), "token")[1] == 200
    # A new exit still sees one exit of 65 on its way: the order's newest version.
    refused_message(upstox.place(order("SELL"), "token"))


def test_a_gtt_modify_is_judged_as_the_gtt_the_broker_has(the_gate, book):
    the_gate.set_state(ON)
    book.rows = [row(CE, 65, "D")]
    book.gtts = {
        "S1": readers.Gtt("SINGLE", rules.SELL, "NRML", CE, "NFO"),
        "M1": readers.Gtt("OCO", rules.SELL, "NRML", CE, "NFO"),
    }
    upstox = broker()
    assert (
        upstox.gtt_modify({"trigger_id": "S1", "quantity": "65", "action": "BUY"}, "token")[1]
        == 200
    )
    assert upstox.gtt_modify({"trigger_id": "S1", "quantity": "130"}, "token")[1] == 403
    assert upstox.gtt_modify({"trigger_id": "M1", "quantity": "65"}, "token")[1] == 403
    body, status = upstox.gtt_modify({"trigger_id": "GONE", "quantity": "65"}, "token")
    assert status == 403 and body["message"] == words.order_not_found("live")


# -- reads under the switch ------------------------------------------------------------------------


def test_a_flat_book_read_a_moment_ago_refuses_without_reading_again(the_gate, book, clock):
    the_gate.set_state(ON)
    upstox = broker()
    refused_message(upstox.place(order("BUY"), "token"))
    assert book.calls.count("rows") == 1
    clock.now += gate.BOOK_REUSE_S - 1
    for _ in range(5):
        refused_message(upstox.place(order("BUY"), "token"))
    assert book.calls.count("rows") == 1
    clock.now += 2
    refused_message(upstox.place(order("BUY"), "token"))
    assert book.calls.count("rows") == 2


def test_the_broker_s_own_fresh_book_is_always_preferred(the_gate, book):
    the_gate.set_state(ON)
    upstox = broker()
    refused_message(upstox.place(order("SELL"), "token"))  # read flat
    book.cached = [row(CE, 65)]  # the smart order just read it: the position is there
    assert upstox.place(order("SELL"), "token")[2] == "OID0"
    assert book.calls.count("rows") == 1


# -- turning the switch on -------------------------------------------------------------------------


def test_turning_on_returns_the_sends_still_waiting(the_gate, book):
    the_gate.set_state(KillSwitchState())
    kind = gate._Kind(gate.LIVE, gate._upstox_place, gate._refuse_with_response, None)
    waiting = the_gate.admit(kind, (order(), "token"), {}).token
    answered = the_gate.admit(kind, (order(), "token"), {}).token
    the_gate.settle(answered, gate.ANSWERED, "OID")
    outcome, state, tokens = the_gate.turn_on(ON)
    assert (outcome, state, tokens) == ("on", ON, [waiting])
    assert the_gate.turn_on(ON)[0] == "already"
    the_gate.set_state(KillSwitchState(on=True, since=SINCE, running=True))
    assert the_gate.turn_on(ON)[0] == "running"
    the_gate.settle(waiting, gate.ANSWERED, "OID2")
    assert the_gate.unanswered(tokens) == []
    assert the_gate.admit(kind, (order(), "token"), {}).refused


# -- two exits at once ------------------------------------------------------------------------------


def _two_exits_together(the_gate, book) -> list[str]:
    """Two threads each read the book (p = 65) before either decides; then both decide."""
    the_gate.set_state(ON)
    book.rows = [row(CE, 65)]
    both_read = threading.Barrier(2)
    book.before_rows = lambda: both_read.wait(WAIT)
    upstox = broker()
    start = threading.Barrier(2)
    outcomes = []

    def send():
        start.wait(WAIT)
        res = upstox.place(order("SELL"), "token")[0]
        outcomes.append("refused" if res.status == 403 else "sent")

    threads = [threading.Thread(target=send) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(WAIT)
    assert len(outcomes) == 2
    return sorted(outcomes)


def test_two_exits_released_together_send_one(the_gate, book):
    """Barrier-synchronised: the check and the claim are one hold, so one exit takes the 65."""
    assert _two_exits_together(the_gate, book) == ["refused", "sent"]


def test_without_claims_both_exits_would_go(the_gate, book, monkeypatch):
    """The defect the claims prevent, so the test above cannot pass vacuously: both exits would
    be sent against a book that still says 65, and the position would be reversed."""
    monkeypatch.setattr(gate.Gate, "_claimed", lambda self, order, position, now: (0, None))
    assert _two_exits_together(the_gate, book) == ["sent", "sent"]


# -- what counts as an exit on its way ------------------------------------------------------------


def test_a_gtt_exit_rests_and_does_not_hold_back_a_plain_exit(the_gate, book):
    the_gate.set_state(ON)
    book.rows = [row(CE, 65)]
    upstox = broker()
    assert upstox.gtt_place(order("SELL", trigger_type="SINGLE"), "token")[2] == "GTT-1"
    assert upstox.place(order("SELL"), "token")[2] == "OID1"


def test_an_exit_the_broker_was_too_busy_to_send_frees_its_quantity(the_gate, book):
    from utils.broker_backpressure import BusyResponse

    the_gate.set_state(ON)
    book.rows = [row(CE, 65)]
    upstox = broker(lambda data: (BusyResponse(429), {"status": "error", "message": "busy"}, None))
    upstox.place(order("SELL"), "token")
    upstox.place(order("SELL"), "token")
    assert len(upstox.reached) == 2


def test_a_modify_refused_before_it_was_sent_leaves_the_order_as_it_was(
    the_gate, book, monkeypatch
):
    """The exit X of 65 is raised to 130 (p is 130); a refusal before the send leaves X at 65."""
    the_gate.set_state(ON)
    book.rows = [row(CE, 130)]
    upstox = broker()
    assert upstox.place(order("SELL"), "token")[2] == "OID0"
    book.orders = {"OID0": working(rules.SELL, 65, price=140.0)}

    @gate.upstox_modify
    def busy_modify(data, auth):
        return {"status": "error", "message": "busy"}, 429

    assert busy_modify(change("OID0", 130, 140), "token")[1] == 429
    assert upstox.place(order("SELL"), "token")[2] == "OID1"  # 130 - 65 leaves 65


def test_a_part_filled_exit_s_modify_claims_only_what_is_left(the_gate, book):
    the_gate.set_state(ON)
    book.rows = [row(CE, 130)]  # 195 held, 65 of the exit filled
    book.orders = {"X1": working(rules.SELL, 130, filled=65, price=140.0)}
    upstox = broker()
    assert upstox.modify(change("X1", 130, 138), "token")[1] == 200  # 65 left
    assert upstox.place(order("SELL"), "token")[2] == "OID1"  # 130 - 65 leaves 65
    refused_message(upstox.place(order("SELL"), "token"))


def test_a_cnc_sell_s_modify_keeps_its_freedom_whatever_the_request_names(the_gate, book):
    the_gate.set_state(ON)
    held = readers.Working(
        rules.WorkingOrder(rules.SELL, 0, rules.OrderTerms(40, "LIMIT", 1500.0)),
        "NSE",
        "RELIANCE",
        "D",
        "NSE_EQ|R",
        final=False,
    )
    book.orders = {"C1": held}
    upstox = broker()
    assert upstox.modify(change("C1", 40, 1490), "token")[1] == 200  # no product named
    assert book.calls.count("rows") == 0


def test_a_switch_turned_off_while_the_book_was_read_lets_the_order_go(the_gate, book):
    the_gate.set_state(ON)
    book.before_rows = lambda: the_gate.set_state(KillSwitchState())  # resumed meanwhile
    upstox = broker()
    assert upstox.place(order("BUY"), "token")[2] == "OID0"


def test_a_switch_turned_on_while_its_state_was_first_read_stays_on(
    the_gate, risk_store, monkeypatch
):
    """Barrier-synchronised: the first read returns the old state after the switch went on."""
    reading, finish = threading.Event(), threading.Event()
    original = risk_store.state

    def slow_state():
        old = original()  # off, as saved
        reading.set()
        assert finish.wait(WAIT)
        return old

    monkeypatch.setattr(risk_store, "state", slow_state)
    reader = threading.Thread(target=the_gate.state)
    reader.start()
    assert reading.wait(WAIT)
    assert the_gate.turn_on(ON)[0] == "on"
    finish.set()
    reader.join(WAIT)
    assert the_gate.state() == ON


def test_one_order_and_its_modifies_count_once(the_gate, book):
    the_gate.set_state(ON)
    book.rows = [row(CE, 130)]
    upstox = broker()
    assert upstox.place(order("SELL"), "token")[2] == "OID0"
    book.orders = {"OID0": working(rules.SELL, 65, price=140.0)}
    assert upstox.modify(change("OID0", 65, 139), "token")[1] == 200
    assert upstox.modify(change("OID0", 65, 138), "token")[1] == 200
    assert upstox.place(order("SELL"), "token")[2] == "OID3"  # 130 less the one 65 on its way


def test_a_flat_book_is_not_reused_for_a_position_something_was_sent_on(the_gate, book):
    """An entry sent before the switch went on may have filled since the flat read."""
    upstox = broker()
    the_gate.set_state(KillSwitchState())
    assert upstox.place(order("BUY"), "token")[2] == "OID0"  # sent while off
    the_gate.set_state(ON)
    refused_message(upstox.place(order("BUY", symbol=PE), "token"))  # a flat read, kept
    book.rows = [row(CE, 65)]  # the entry has filled
    assert upstox.place(order("SELL"), "token")[2] == "OID1"


def test_a_burst_of_refusals_writes_the_audit_at_most_every_two_seconds(
    the_gate, risk_store, clock, monkeypatch
):
    """A SQLite write is disk work eventlet cannot make cooperative: a runaway script refused
    eight times a second must not become eight writes a second."""
    the_gate.set_state(ON)
    writes = []
    original = risk_store.audit
    monkeypatch.setattr(
        risk_store, "audit", lambda *a, **k: writes.append(k["count"]) or original(*a, **k)
    )
    upstox = broker()
    for _ in range(40):  # five seconds at eight a second
        upstox.place(order("BUY"), "token")
        clock.now += 0.125
    assert writes == [1, 16, 16]  # the first at once, then the counts every two seconds
    the_gate.flush_notes()
    assert writes == [1, 16, 16, 7]
    assert sum(entry.count for entry in risk_store.history()) == 40
    the_gate.flush_notes()
    assert len(writes) == 4  # nothing left to write


def test_a_count_kept_past_its_minute_is_written_by_the_next_refusal(the_gate, risk_store, clock):
    the_gate.set_state(ON)
    upstox = broker()
    upstox.place(order("BUY"), "token")
    upstox.place(order("BUY"), "token")  # counted in memory
    clock.now += gate.NOTE_KEPT_S + 1
    upstox.place(order("BUY", symbol=PE), "token")  # another kind: writes the old count too
    counts = {entry.detail["symbol"]: entry.count for entry in risk_store.history()}
    assert counts == {CE: 2, PE: 1}


@pytest.mark.parametrize(
    ("answer", "outcome"),
    [
        ((True, {"status": "success", "orderid": "S1"}, 200), (gate.ANSWERED, "S1")),
        ((True, {"status": "success", "trigger_id": "G1"}, 200), (gate.ANSWERED, "G1")),
        ((False, {"status": "error", "message": "no funds"}, 400), (gate.REFUSED, None)),
        ((False, {"status": "error", "message": "busy"}, 409), (gate.REFUSED, None)),
        ((False, {"status": "error", "message": "broke"}, 500), (gate.ANSWERED, None)),
    ],
)
def test_what_a_sandbox_answer_says_was_placed(answer, outcome):
    assert gate._answer_in_the_sandbox(answer) == outcome
