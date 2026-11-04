"""Independent review (d628ff3): a resting exit's claim outlives the order (gate-level fakes)."""

from __future__ import annotations

from test_guard_gate import CE, broker, order, row
from test_guard_gate_daily import LIMIT, day, day_row  # noqa: F401 - fixture
from test_guard_gate_size import SizedBook, sized  # noqa: F401 - fixture


def test_size_cap_refuses_a_market_exit_after_its_resting_stop_was_cancelled(sized):
    """Size cap on (2 lots). 3 lots held; a 3-lot stop loss rests (an exit: allowed). The owner
    cancels the stop at Upstox (cancels are not seen by the gate) and exits at market."""
    sized.book.rows = [row(CE, 195)]
    upstox = broker()
    stop = order("SELL", 195, pricetype="SL-M", trigger_price="98.5")
    assert upstox.place(stop, "token")[2] == "OID0"  # the stop: an exit, never limited
    # ... the stop is cancelled in Upstox's app; the book still shows 195 held ...
    sized.book.statuses = {"OID0": "cancelled"}
    res, body, oid = upstox.place(order("SELL", 195), "token")
    print("SIZE CAP EXIT:", res.status, body)
    assert oid == "OID1", body["message"]


def test_daily_lock_refuses_a_market_exit_after_its_resting_stop_was_cancelled(day):
    day.book.rows = [day_row(CE, 65, -4200.0, 0.0)]  # the day has locked
    upstox = broker()
    assert upstox.place(order("BUY", symbol=CE.replace("CE", "PE")), "token")[0].status == 403
    stop = order("SELL", 65, pricetype="SL-M", trigger_price="98.5")
    assert upstox.place(stop, "token")[2] == "OID0"
    day.book.statuses = {"OID0": "cancelled"}  # cancelled in Upstox's app: the gate never saw it
    res, body, oid = upstox.place(order("SELL", 65), "token")
    print("DAILY LOCK EXIT:", res.status, body)
    assert oid == "OID1", body["message"]


from test_guard_sandbox import ON as SANDBOX_ON  # noqa: E402
from test_guard_sandbox import USER, _place, _position, sandbox  # noqa: E402,F401 - fixture


def test_sandbox_stage_exit_of_the_rest_after_a_part_exit_under_the_switch(sandbox):
    """The shipping stage (sandbox): 100 held, 50 sold (filled at once in the sandbox), the
    switch goes on, the other 50 are sold: an exit, which must go."""
    assert _place("BUY", 100)[0] and _position() == 100
    assert _place("SELL", 50)[0] and _position() == 50  # filled: the book shows it
    sandbox.set_state(SANDBOX_ON)
    ok, body, status = _place("SELL", 50)
    print("SANDBOX EXIT:", ok, status, body)
    assert ok and _position() == 0, body


def test_a_webhook_s_url_token_is_kept_in_the_risk_audit(day):
    """/strategy/webhook/<token>: "the URL token is the credential" (blueprints/strategy_module.py).
    A refusal made inside that request writes request.path, token and all, to the audit."""
    from flask import Flask

    day.book.rows = [day_row(CE, 65, -4200.0, 0.0)]  # the day has locked: entries are refused
    app = Flask("review")
    with app.test_request_context("/strategy/webhook/SECRET-TOKEN-0123456789", method="POST"):
        res, body, oid = broker().place(order("BUY", symbol=CE.replace("CE", "PE")), "token")
    assert res.status == 403
    day.gate.flush_notes()
    surfaces = [entry.surface for entry in day.store.history()]
    print("AUDIT SURFACES:", surfaces)
    assert not any("SECRET-TOKEN" in (surface or "") for surface in surfaces)


def test_a_refused_sandbox_gtt_leg_fires_again_on_every_tick(sandbox, caplog):
    """A sandbox GTT whose leg the gate refuses (here a stop left over after its position was
    closed, under the kill switch's state as set by a lock-style refusal) goes back to pending and
    fires again on the next tick, every tick, each with SQLite commits and an error line."""
    import logging

    from database.sandbox_db import SandboxGTTLeg, db_session
    from sandbox import gtt_manager
    from sandbox.gtt_manager import GTTManager

    base = {"symbol": "SBIN", "exchange": "NSE", "action": "SELL", "quantity": 10,
            "product": "MIS", "pricetype": "LIMIT", "price": 780}  # fmt: skip
    ok, body, status = GTTManager(USER).place_gtt(
        {**base, "trigger_type": "SINGLE", "triggerprice_sl": 790}, 800
    )
    assert ok, body  # flat book, nothing refusing yet: a stop that would now open a short
    sandbox.set_state(SANDBOX_ON)  # new risk refused (stands in for the day's lock)
    db_session.remove()
    leg = SandboxGTTLeg.query.filter_by(gtt_id=body["trigger_id"]).first()
    leg_id = leg.id
    caplog.set_level(logging.ERROR)
    fired = 0
    for _tick in range(5):  # five ticks below the trigger
        if gtt_manager.try_claim_trigger(leg_id):
            fired += 1
            assert gtt_manager.fire_leg(leg_id, execution_price=789.0) is False
    rejected = [r for r in caplog.records if "order rejected" in r.getMessage()]
    print("FIRED:", fired, "ERROR LINES:", len(rejected))
    assert fired <= 1, f"the refused leg fired {fired} times in 5 ticks"


def test_an_order_racing_the_first_state_read_is_refused_as_unreadable(risk_store, monkeypatch):
    """Real threads (gthread, the dev server): while the first read of the kill switch's state is
    in flight, a second order sees the state as unknown and is refused, though the switch is off
    and the store reads fine."""
    import threading

    from sudarshan.guard import flag, gate, words
    from test_guard_gate import Book

    book = Book()
    fresh = gate.Gate(store=risk_store, read=book)
    monkeypatch.setattr(gate, "GATE", fresh)
    monkeypatch.setenv(flag.ENV, "on")
    reading, finish = threading.Event(), threading.Event()
    original = risk_store.state

    def slow_state():
        reading.set()
        assert finish.wait(10)
        return original()  # off

    monkeypatch.setattr(risk_store, "state", slow_state)
    first = threading.Thread(target=lambda: broker().place(order("BUY"), "token"))
    first.start()
    assert reading.wait(10)
    threading.Timer(0.3, finish.set).start()  # the first read ends while the second waits
    res, body, oid = broker().place(order("BUY"), "token")  # the switch is off
    finish.set()
    first.join(30)
    print("SECOND ORDER:", res.status if res else None, body)
    assert oid is not None, body
