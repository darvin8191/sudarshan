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
        self.multi_calls = 0

    def delete(self, url, headers=None, **kwargs):
        if not url.endswith("/v2/order/multi/cancel"):
            return super().delete(url, headers, **kwargs)
        with self.lock:
            self.multi_calls += 1
            if self.multi_answer != 200:
                return world_module.FakeResponse(
                    self.multi_answer, {"status": "error", "errors": [{"message": "down"}]}
                )
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


def test_with_the_book_unreadable_it_cancels_every_order_without_it_then_exits(upstox):
    broker = upstox.broker
    broker.rest(CE, "SELL", 65)  # a resting stop beside the position
    broker.order_book_delay = 20.0  # the book hangs: every read is cut off
    answer = killswitch.activate(OWNER, exit_all=True, phrase="STOP")
    steps = _steps(answer)
    assert broker.multi_calls >= 1 and broker.working == []
    assert steps["live_orders"]["words"] == words.KS_ORDERS_CANCELLED_UNREAD
    # M6.9 (N2): what is held is worked out from the positions row and the order book's fills,
    # and the order book hangs, so no market exit is sent for it; the step says so.
    assert [o for o in broker.orders if o["symbol"] == CE] == []
    assert broker.true_qty(CE) == 65
    assert steps["live_exit_all"]["ok"] is False
    assert "could not be confirmed from Upstox's orders" in steps["live_exit_all"]["words"]


def test_if_that_fails_too_no_market_exit_is_sent_and_the_step_says_to_use_the_app(upstox):
    broker = upstox.broker
    broker.rest(CE, "SELL", 65)
    broker.order_book_delay = 20.0
    broker.multi_answer = 503
    answer = killswitch.activate(OWNER, exit_all=True, phrase="STOP")
    steps = _steps(answer)
    assert [o for o in broker.orders if o["symbol"] == CE] == []  # no market exit beside the stop
    assert broker.true_qty(CE) == 65 and len(broker.working) == 1  # as it was: the app can close it
    assert steps["live_exit_all"]["words"] == words.KS_NO_EXIT_ORDERS_UNREAD
    assert steps["live_exit_all"]["ok"] is False
    assert "Upstox app" in steps["live_exit_all"]["words"]
    # a 5xx may have come after Upstox acted: not confirmed, never "did not go through" (N5)
    assert steps["live_orders"]["words"] == words.ks_orders_not_cancelled_unread(None, None, True)
