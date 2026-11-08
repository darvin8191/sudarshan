"""Release review A, L4: the kill switch when Upstox's order book cannot be read.

With no book the switch cancels nothing by id. It first uses Upstox's own cancel of every open
order, which needs no book, and then Exit all. If that fails too, no market exit is sent beside a
closing order that may still be working (it could close the position a second time): the step
tells the trader, in a sentence, to close from the Upstox app.
"""

from __future__ import annotations

import pytest
from test_guard_killswitch_upstox import CE, OWNER, Upstox, world_module

from sudarshan.guard import flag, gate, killswitch, readers, words
from sudarshan.guard.store import KillSwitchState


class Multi(Upstox):
    """The simulated Upstox, with its cancel of every open order."""

    def __init__(self, held, lag=0.0):
        super().__init__(held, lag)
        self.multi_answer = 200
        self.multi_body = None  # the body of an answer other than 200, when a test gives one
        self.multi_calls = 0

    def delete(self, url, headers=None, **kwargs):
        if not url.endswith("/v2/order/multi/cancel"):
            return super().delete(url, headers, **kwargs)
        with self.lock:
            self.multi_calls += 1
            if self.multi_answer != 200:
                body = self.multi_body or {"status": "error", "errors": [{"message": "down"}]}
                return world_module.FakeResponse(self.multi_answer, body)
            ids = [order["id"] for order in self.working]
            self.working = []
        return world_module.FakeResponse(
            200,
            {"status": "success", "data": {"order_ids": ids},
             "summary": {"total": len(ids), "success": len(ids), "error": 0}},
        )  # fmt: skip


@pytest.fixture
def upstox(monkeypatch, risk_store):
    import services.gtt_orderbook_service as gtt_book

    broker = Multi({CE: 65})
    world = world_module.install(monkeypatch, broker)
    monkeypatch.setattr(
        gtt_book,
        "get_gtt_orderbook_with_auth",
        lambda *args, **kwargs: (True, {"status": "success", "data": []}, 200),
    )
    monkeypatch.setattr(killswitch, "_tell", lambda owner, text: (True, "told", {}))
    monkeypatch.setattr(killswitch, "_announce", lambda state: None)
    monkeypatch.setattr(
        killswitch, "_engage_agent", lambda: (True, "agent", {"engaged_by_us": False})
    )
    monkeypatch.setattr(killswitch, "DRAIN_S", 0.2)
    monkeypatch.setattr(killswitch, "CANCELS_POLL_S", 0.05)
    monkeypatch.setattr(killswitch, "SECOND_SWEEP_AFTER_S", 0.0)
    monkeypatch.setattr(readers, "READ_LIMIT_S", 0.3)
    fresh = gate.Gate(store=risk_store)
    fresh.set_state(KillSwitchState())
    monkeypatch.setattr(gate, "GATE", fresh)
    monkeypatch.setenv(flag.ENV, "on")
    return world


def _steps(answer) -> dict:
    return {step["step"]: step for step in answer.state.report["steps"]}


def test_x5_with_the_book_unreadable_and_exit_all_asked_the_stops_are_kept(upstox):
    """Long 65 CE with its stop loss resting; the owner turns the switch on with Exit all while
    the order book hangs. Nothing is cancelled and nothing is sent: a cancel without the book
    would take the stop, and Exit all could not size an exit without the book, so the position
    would be left held with no stop and no exit (release review v0.4.1, X5). On 376dc58 the stop
    was cancelled and the position left open."""
    broker = upstox.broker
    broker.rest(CE, "SELL", 65)  # a resting stop beside the position
    broker.order_book_delay = 20.0  # the book hangs: every read is cut off
    answer = killswitch.activate(OWNER, exit_all=True, phrase="STOP")
    steps = _steps(answer)
    assert broker.multi_calls == 0  # Upstox's cancel of every open order was not sent
    assert broker.true_qty(CE) == 65 and len(broker.working) == 1  # the stop still protects it
    assert [o for o in broker.orders if o["symbol"] == CE] == []  # no market exit beside it
    assert steps["live_orders"]["words"] == words.KS_ORDERS_KEPT_UNREAD
    assert steps["live_orders"]["ok"] is False
    assert steps["live_orders_again"]["detail"]["kept_for_exit_all"] is True  # looked again
    assert steps["live_exit_all"]["words"] == words.KS_NO_EXIT_ORDERS_UNREAD
    assert steps["live_exit_all"]["ok"] is False
    assert "Upstox app" in steps["live_exit_all"]["words"]


def test_x5_the_sweep_finds_the_book_and_the_switch_cancels_then_exits(upstox, monkeypatch):
    """The book hangs for the first look and answers for the sweep two seconds later: the stop
    is cancelled from the book and Exit all closes the position, as on a readable book."""
    broker = upstox.broker
    broker.rest(CE, "SELL", 65)
    broker.order_book_delay = 20.0
    real_sweep = killswitch._sweep_again

    def book_back(*args, **kwargs):
        broker.order_book_delay = 0.0  # by the sweep, Upstox answers again
        return real_sweep(*args, **kwargs)

    monkeypatch.setattr(killswitch, "_sweep_again", book_back)
    answer = killswitch.activate(OWNER, exit_all=True, phrase="STOP")
    steps = _steps(answer)
    assert broker.multi_calls == 0
    assert steps["live_orders"]["words"] == words.KS_ORDERS_KEPT_UNREAD
    assert steps["live_orders_again"]["ok"] is True
    assert broker.true_qty(CE) == 0 and broker.working == []
    assert steps["live_exit_all"]["ok"] is True


def test_without_exit_all_the_book_unreadable_still_cancels_every_order_without_it(upstox):
    """The switch alone (no Exit all) cancels every open order through Upstox's own cancel when
    the book cannot be read, as before: no exit follows that the stops would be needed for."""
    broker = upstox.broker
    broker.rest(CE, "SELL", 65)
    broker.order_book_delay = 20.0
    answer = killswitch.activate(OWNER, exit_all=False, phrase="STOP")
    steps = _steps(answer)
    assert broker.multi_calls >= 1 and broker.working == []
    assert steps["live_orders"]["words"] == words.KS_ORDERS_CANCELLED_UNREAD
    assert "live_exit_all" not in steps


def test_without_exit_all_a_cancel_that_fails_too_is_said_as_not_confirmed(upstox):
    broker = upstox.broker
    broker.rest(CE, "SELL", 65)
    broker.order_book_delay = 20.0
    broker.multi_answer = 503
    answer = killswitch.activate(OWNER, exit_all=False, phrase="STOP")
    steps = _steps(answer)
    assert broker.true_qty(CE) == 65 and len(broker.working) == 1  # as it was
    # a 5xx may have come after Upstox acted: not confirmed, never "did not go through" (N5)
    assert steps["live_orders"]["words"] == words.ks_orders_not_cancelled_unread(None, None, True)


def test_x5_exit_all_that_got_no_orders_in_time_is_sent_again_and_closes(upstox, monkeypatch):
    """The book answers the switch's reads, the stop is cancelled, and then Upstox does not send
    the orders in time for Exit all's own read: Exit all is tried once more, and closes."""
    broker = upstox.broker
    broker.rest(CE, "SELL", 65)
    reads = []
    real_order_book = broker._order_book

    def book(timeout=None):
        reads.append(timeout)
        if len(reads) == 5:  # Exit all's own read, after the switch's four
            broker.order_book_delay = 20.0
        else:
            broker.order_book_delay = 0.0
        return real_order_book(timeout)

    monkeypatch.setattr(broker, "_order_book", book)
    monkeypatch.setattr(killswitch, "EXIT_ALL_AGAIN_AFTER_S", 0.0)
    answer = killswitch.activate(OWNER, exit_all=True, phrase="STOP")
    steps = _steps(answer)
    assert broker.working == []
    assert broker.true_qty(CE) == 0, steps["live_exit_all"]
    assert steps["live_exit_all"]["detail"]["tried_again"] is True
    assert steps["live_exit_all"]["words"].startswith("Exit all, sent again")


def test_x5_a_position_left_open_after_its_stop_went_is_said_with_the_upstox_app(
    upstox, monkeypatch
):
    """The stop is cancelled from the book, then the order book stays unreadable for both of Exit
    all's tries: the step says the position is open with its stop cancelled."""
    broker = upstox.broker
    broker.rest(CE, "SELL", 65)
    reads = []
    real_order_book = broker._order_book

    def book(timeout=None):
        reads.append(timeout)
        broker.order_book_delay = 20.0 if len(reads) >= 5 else 0.0
        return real_order_book(timeout)

    monkeypatch.setattr(broker, "_order_book", book)
    monkeypatch.setattr(killswitch, "EXIT_ALL_AGAIN_AFTER_S", 0.0)
    answer = killswitch.activate(OWNER, exit_all=True, phrase="STOP")
    steps = _steps(answer)
    assert broker.working == [] and broker.true_qty(CE) == 65
    said = steps["live_exit_all"]["words"]
    assert said.endswith(words.ks_left_without_stops(1))
    assert "stop-loss order cancelled" in said and "Upstox app" in said
    assert steps["live_exit_all"]["detail"]["left_without_stops"] == 1


# -- UDAPI1109: Upstox's answer to a cancel of every open order when none is open (L4) ---------


def _nothing_open_body():
    return {
        "status": "error",
        "errors": [
            {
                "errorCode": "UDAPI1109",
                "message": "No open or pending orders available",
                "propertyPath": None,
                "invalidValue": None,
                "error_code": "UDAPI1109",
                "property_path": None,
                "invalid_value": None,
            }
        ],
    }


def test_udapi1109_is_nothing_to_cancel_not_a_failure(monkeypatch):
    from broker.upstox.api import order_api

    monkeypatch.setattr(order_api, "get_api_response", lambda *a, **k: _nothing_open_body())
    result = order_api.cancel_every_open_order_detail("token")
    assert result["cancelled"] is True and result["nothing_open"] is True
    assert result["not_confirmed"] is False and result["ids"] == []
    assert (result["done"], result["total"]) == (0, 0)
    assert order_api.cancel_every_open_order_api("token") == (True, False)


@pytest.mark.parametrize(
    "body",
    [
        {"status": "error", "errors": [{"errorCode": "UDAPI1110", "message": "too many"}]},
        {"status": "error", "errors": [{"message": "No open or pending orders available"}]},
        {"status": "error", "errors": "UDAPI1109"},
        {"status": "error", "message": "UDAPI1109"},
    ],
    ids=["another code", "no code", "errors not a list", "no errors list"],
)
def test_only_the_code_itself_reads_as_nothing_open(monkeypatch, body):
    from broker.upstox.api import order_api

    monkeypatch.setattr(order_api, "get_api_response", lambda *a, **k: dict(body))
    result = order_api.cancel_every_open_order_detail("token")
    assert result["cancelled"] is False and result["nothing_open"] is False


def test_the_switch_says_there_was_nothing_to_cancel(upstox):
    """The book hangs, no order is open, the switch alone: Upstox's cancel of every open order
    answers UDAPI1109. The step says nothing was open, never that the cancel did not go through."""
    broker = upstox.broker
    broker.order_book_delay = 20.0
    broker.multi_answer = 400
    broker.multi_body = _nothing_open_body()
    answer = killswitch.activate(OWNER, exit_all=False, phrase="STOP")
    steps = _steps(answer)
    assert broker.multi_calls >= 1
    assert steps["live_orders"]["ok"] is True
    assert steps["live_orders"]["words"] == words.KS_NOTHING_TO_CANCEL_UNREAD
    assert "did not go through" not in steps["live_orders"]["words"]
    assert steps["live_orders"]["detail"]["nothing_open"] is True
    assert "live_orders_again" not in steps  # nothing to look at again


# -- the review of this branch, HIGH-2: the stop gone on another path, and the words ------------


def _never_reassures(broker, steps):
    """The X5 outcome reached another way: the stop is gone, nothing was sent, and the words say
    so. They never say the stop-loss orders still work, or that nothing was cancelled."""
    said = steps["live_exit_all"]["words"]
    assert broker.working == [] and broker.true_qty(CE) == 65
    assert "stop-loss orders still work" not in said and "still stand" not in said, said
    assert "cancel" in said.lower() and "Upstox app" in said, said
    assert "open with no stop" in said or "may have been cancelled" in said, said


def test_h2a_cancel_all_takes_the_stop_then_the_book_hangs(upstox, monkeypatch):
    """Upstox's Cancel all reads the book and cancels the resting stop; the book hangs from the
    next read on (Upstox struggling in a crisis): the rest, the sweep, Exit all."""
    broker = upstox.broker
    broker.rest(CE, "SELL", 65)
    real_rest = killswitch._cancel_the_rest

    def hangs_after_cancel_all(*args, **kwargs):
        broker.order_book_delay = 20.0
        return real_rest(*args, **kwargs)

    monkeypatch.setattr(killswitch, "_cancel_the_rest", hangs_after_cancel_all)
    steps = _steps(killswitch.activate(OWNER, exit_all=True, phrase="STOP"))
    _never_reassures(broker, steps)
    assert steps["live_orders"]["words"] == words.ks_orders_unchecked(1)
    assert steps["live_orders"]["detail"]["stops_cancelled"] == 1  # counted on this pass too
    assert steps["live_orders_again"]["detail"]["after_cancels"] is True
    assert "1 order was cancelled, 1 stop-loss order among them" in steps["live_exit_all"]["words"]
    assert steps["live_exit_all"]["words"] == words.ks_no_exit_orders_unread(1, 1, 0, 0)


def test_h2c_a_full_first_pass_then_the_sweep_meets_a_hung_book(upstox, monkeypatch):
    broker = upstox.broker
    broker.rest(CE, "SELL", 65)
    monkeypatch.setattr(
        killswitch,
        "_drain",
        lambda tokens: (False, "One order was still waiting.", {"sent": 1, "waiting": 1}),
    )
    real_sweep = killswitch._sweep_again

    def book_hangs(*args, **kwargs):
        broker.order_book_delay = 20.0
        return real_sweep(*args, **kwargs)

    monkeypatch.setattr(killswitch, "_sweep_again", book_hangs)
    steps = _steps(killswitch.activate(OWNER, exit_all=True, phrase="STOP"))
    assert steps["live_orders"]["detail"].get("stops_cancelled") == 1
    _never_reassures(broker, steps)
    assert steps["live_orders_again"]["words"] == words.ks_swept_again(
        words.ks_orders_unread_after_cancels(1, 1, 0, 0)
    )


def test_h2g_with_the_book_unreadable_a_gtt_stop_is_kept_too(upstox, monkeypatch):
    """The main X5 case with a single-leg GTT as the stop (how a carried NRML position keeps one
    overnight): nothing is cancelled, the GTT included, and the step says so."""
    import services.cancel_gtt_order_service as cancel_gtt
    import services.gtt_orderbook_service as gtt_book

    broker = upstox.broker
    broker.order_book_delay = 20.0
    gtts = [{"trigger_id": "GTT-STOP-1", "symbol": CE, "action": "SELL", "quantity": 65}]
    monkeypatch.setattr(
        gtt_book,
        "get_gtt_orderbook_with_auth",
        lambda *args, **kwargs: (True, {"status": "success", "data": list(gtts)}, 200),
    )
    cancelled_gtts = []

    def cancel(trigger_id, *args, **kwargs):
        cancelled_gtts.append(trigger_id)
        gtts.clear()
        return True, {"status": "success"}, 200

    monkeypatch.setattr(cancel_gtt, "cancel_gtt_order_with_auth", cancel)
    steps = _steps(killswitch.activate(OWNER, exit_all=True, phrase="STOP"))
    assert broker.true_qty(CE) == 65 and cancelled_gtts == []
    assert steps["live_gtts"]["words"] == words.KS_GTTS_KEPT_UNREAD
    assert steps["live_exit_all"]["words"] == words.KS_NO_EXIT_ORDERS_UNREAD
    assert "still stand" in steps["live_exit_all"]["words"]  # true here: nothing was cancelled


def test_h2g_a_gtt_is_cancelled_when_the_book_was_read_and_counted_for_the_words(
    upstox, monkeypatch
):
    """The book answers for the cancels and the GTT step runs; then Exit all's own reads fail:
    the position left open is said to have had its stop cancelled, maybe (the GTT)."""
    import services.cancel_gtt_order_service as cancel_gtt
    import services.gtt_orderbook_service as gtt_book

    broker = upstox.broker
    gtts = [{"trigger_id": "GTT-STOP-1", "symbol": CE, "action": "SELL", "quantity": 65}]
    monkeypatch.setattr(
        gtt_book,
        "get_gtt_orderbook_with_auth",
        lambda *args, **kwargs: (True, {"status": "success", "data": list(gtts)}, 200),
    )
    monkeypatch.setattr(
        cancel_gtt,
        "cancel_gtt_order_with_auth",
        lambda *args, **kwargs: (gtts.clear(), (True, {"status": "success"}, 200))[1],
    )
    reads = []
    real_order_book = broker._order_book

    def book(timeout=None):
        reads.append(timeout)
        broker.order_book_delay = 20.0 if len(reads) >= 4 else 0.0  # Exit all's reads fail
        return real_order_book(timeout)

    monkeypatch.setattr(broker, "_order_book", book)
    monkeypatch.setattr(killswitch, "EXIT_ALL_AGAIN_AFTER_S", 0.0)
    steps = _steps(killswitch.activate(OWNER, exit_all=True, phrase="STOP"))
    assert gtts == [] and broker.true_qty(CE) == 65
    said = steps["live_exit_all"]["words"]
    assert said.endswith(words.ks_left_without_stops(0, 1)), said
    assert "may have been cancelled" in said


def test_h2n_a_stop_cancel_that_timed_out_is_not_called_nothing_cancelled(upstox, monkeypatch):
    """Cancel all reads the book and sends the stop's cancel; Upstox cancels it but the answer
    times out (not confirmed); the book then hangs. Anything that may have reached the broker
    reads "not confirmed", never "nothing was cancelled"."""
    from broker.upstox.api import order_api

    broker = upstox.broker
    broker.rest(CE, "SELL", 65)

    def cancel_times_out(orderid, auth):
        with broker.lock:
            broker.working = [o for o in broker.working if o["id"] != orderid]  # it went
        broker.order_book_delay = 20.0  # and the book hangs from here on
        return {"status": "error", "message": "The read operation timed out",
                "not_confirmed": True}, 500  # fmt: skip

    monkeypatch.setattr(order_api, "cancel_order", cancel_times_out)
    steps = _steps(killswitch.activate(OWNER, exit_all=True, phrase="STOP"))
    first = steps["live_orders"]["words"]
    assert first == words.ks_cancels_not_confirmed(0, 1), first
    assert "nothing was cancelled" not in first and "could not be cancelled" not in first
    _never_reassures(broker, steps)
    assert "1 cancel is not confirmed and may have gone through" in steps["live_exit_all"]["words"]


def test_the_kept_words_claim_no_stop_exists():
    for said in (words.KS_ORDERS_KEPT_UNREAD, words.KS_NO_EXIT_ORDERS_UNREAD):
        assert "their stop-loss orders" not in said and "the stop losses" not in said
    assert "any stop-loss orders you placed still stand" in words.KS_NO_EXIT_ORDERS_UNREAD
