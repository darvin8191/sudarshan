"""Release review v0.4.1, X3: the gate reads Upstox's order book no second time for an exit whose
caller has just read it (M5.13, before the guard is switched on for Live).

Under the kill switch (or with any limit set) every exit is judged, and judging corrects the
positions row with the order book's fills. Exit all and the smart order had just read that book
to size the exit; the gate read it once more per exit, inside Exit all's sending window, so on a
slow Upstox the last positions ran out of time and stayed open. Now the caller hands the rows it
read (``gate.order_book_read``, from the Upstox module's ``_read_order_rows``) and the gate uses
them for the rest of that request; it reads the book itself only when nothing was handed.
"""

from __future__ import annotations

import time

from guard_world import make_app
from v041_world import CE, ON, OWNER, SBIN, _order, _steps, _world

from sudarshan.books import exit_guard
from sudarshan.guard import gate, killswitch
from sudarshan.guard.store import KillSwitchState

PE = "BANKNIFTY27OCT2657500PE"  # NRML in the world


def _held(broker, *symbols):
    return {symbol: broker.true_qty(symbol) for symbol in symbols}


# -- the finding: Exit all under the switch with a slow order book ----------------------------


def test_x3_the_switchs_exit_all_sends_every_exit_when_the_order_book_is_slow(
    monkeypatch, risk_store
):
    """Three positions, an order book that takes 2 s (under its limits) and, scaled down, a 5 s
    sending window. On 376dc58 the gate read the book once per exit and SBIN ran out of time."""
    world = _world(monkeypatch, risk_store, {CE: 65, PE: -30, SBIN: 10}, KillSwitchState())
    broker = world.broker
    monkeypatch.setattr(exit_guard, "EXIT_ALL_SENDS_FOR_S", 5.0)
    broker.order_book_delay = 2.0
    reads_before = []
    real_exit_all = killswitch._exit_all_live

    def counted(*args, **kwargs):
        reads_before.append(broker.order_book_reads)
        return real_exit_all(*args, **kwargs)

    monkeypatch.setattr(killswitch, "_exit_all_live", counted)
    answer = killswitch.activate(OWNER, exit_all=True, phrase="STOP")
    steps = _steps(answer)
    assert _held(broker, CE, PE, SBIN) == {CE: 0, PE: 0, SBIN: 0}, steps["live_exit_all"]
    assert broker.order_book_reads - reads_before[0] == 1  # Exit all's own, and no other


def test_x3_a_smart_exit_under_the_switch_reads_the_order_book_once(monkeypatch, risk_store):
    """The smart order sizes its exit from one order book read; the gate judges it on the same
    rows (on 376dc58 it read the book a second time, up to 4 s more before the send)."""
    world = _world(monkeypatch, risk_store, {CE: 65}, ON)
    broker = world.broker
    before = broker.order_book_reads
    world.tv_smart_exit(CE, quantity="65")
    assert broker.true_qty(CE) == 0
    assert broker.order_book_reads - before == 1


def test_x3_a_plain_order_nothing_handed_still_reads_the_book_itself(monkeypatch, risk_store):
    """A script's plain exit sizes nothing, so nothing is handed: the gate corrects its positions
    with a read of its own, as before (the control)."""
    from broker.upstox.api import order_api

    world = _world(monkeypatch, risk_store, {CE: 65}, ON)
    broker = world.broker
    before = broker.order_book_reads
    _res, _body, orderid = order_api.place_order_api(_order(CE, "SELL", 65), "token")
    assert orderid is not None
    assert broker.order_book_reads - before == 1


# -- the handing itself -----------------------------------------------------------------------


def test_handed_rows_serve_their_owner_only_and_age_out(monkeypatch):
    rows = [{"order_id": "1"}]
    gate.order_book_read("token", rows)
    assert gate.handed_order_rows("token") == rows
    assert gate.handed_order_rows("another") is None
    real = time.monotonic
    monkeypatch.setattr(time, "monotonic", lambda: real() + gate.HANDED_ORDERS_WITHIN_S + 1)
    assert gate.handed_order_rows("token") is None


def test_the_handed_rows_are_a_copy_the_caller_cannot_change_after():
    rows = [{"order_id": "1"}]
    gate.order_book_read("token", rows)
    rows.clear()
    assert gate.handed_order_rows("token") == [{"order_id": "1"}]


def test_forgetting_ends_the_handing():
    gate.order_book_read("token", [{"order_id": "1"}])
    gate.forget_handed_orders()
    assert gate.handed_order_rows("token") is None


def test_a_block_kept_apart_neither_sees_what_came_before_nor_leaves_anything():
    gate.order_book_read("token", [{"order_id": "before"}])
    with gate.handed_orders_kept_apart():
        assert gate.handed_order_rows("token") is None
        gate.order_book_read("token", [{"order_id": "inside"}])
    assert gate.handed_order_rows("token") == [{"order_id": "before"}]


def test_the_handing_lasts_the_window_exit_all_sends_in():
    assert gate.HANDED_ORDERS_WITHIN_S >= exit_guard.EXIT_ALL_SENDS_FOR_S + (
        exit_guard.EXIT_ALL_ORDERS_READ_S
    )


def test_every_request_forgets_what_its_caller_handed(monkeypatch, risk_store):
    """A gthread pool thread serves one request after another in the same context: the rows one
    exit handed must not correct the gate for the next request's order."""
    from blueprints import sudarshan_risk

    monkeypatch.setattr(sudarshan_risk, "find_user_by_username", lambda: None)
    app = make_app()
    seen_inside = []

    @app.route("/hand-for-tests")
    def hand():
        gate.order_book_read("token", [{"order_id": "1"}])
        seen_inside.append(gate.handed_order_rows("token"))
        return {"ok": True}

    with gate.handed_orders_kept_apart():
        with app.test_client() as client:
            assert client.get("/hand-for-tests").status_code == 200
        assert seen_inside == [[{"order_id": "1"}]]
        assert gate.handed_order_rows("token") is None
