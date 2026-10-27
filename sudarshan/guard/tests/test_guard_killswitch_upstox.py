"""The kill switch over a simulated Upstox, through OpenAlgo's real services and Upstox module
(M5.13, with M6.9 round ten's Exit all).

Exit all leaves out a position whose closing order still shows working (exit_guard.order_working),
and Upstox's order book shows an order just cancelled as "cancel pending", which reads as
working, for a moment. The kill switch cancels, then exits, so a position with a stop loss is
exactly the one it would leave open: these tests show it waits for its cancels to show and
closes it, that it goes through the one claim every Exit all takes, and that its report carries
what became of each position.
"""

from __future__ import annotations

import importlib.util
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

import restx_api  # noqa: F401 - first, as app.py does: services.place_order_service imports it back
from sudarshan.guard import flag, gate, killswitch
from sudarshan.guard.store import KillSwitchState

REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "guard_ks_books_world", REPO / "sudarshan" / "books" / "tests" / "books_upstox_world.py"
)
world_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(world_module)

CE = "NIFTY27OCT2625900CE"  # MIS in the world
OWNER = killswitch.Owner("owner", "app", "owner", auth_token="token", broker="upstox")


class Upstox(world_module.Broker):
    """The simulated Upstox, which also cancels: a cancelled order reads "cancel pending" for
    the next ``pending_reads`` order book reads, then is gone from the working orders."""

    def __init__(self, held, lag=0.0):
        super().__init__(held, lag)
        self.pending_reads = 0
        self.cancelling: dict[str, int] = {}

    def delete(self, url, headers=None, **kwargs):
        orderid = parse_qs(urlparse(url).query)["order_id"][0]
        with self.lock:
            assert any(order["id"] == orderid for order in self.working), orderid
            self.cancelling[orderid] = self.pending_reads
        return world_module.FakeResponse(200, {"status": "success", "data": {"order_id": orderid}})

    def _order_book(self, timeout=None):
        with self.lock:
            for orderid, left in list(self.cancelling.items()):
                if left <= 0:
                    self.working = [order for order in self.working if order["id"] != orderid]
                    del self.cancelling[orderid]
        response = super()._order_book(timeout)
        with self.lock:
            for row in response.json()["data"]:
                if row["order_id"] in self.cancelling:
                    row["status"] = "cancel pending"
                    self.cancelling[row["order_id"]] -= 1
        return response


@pytest.fixture
def upstox(monkeypatch, risk_store):
    import services.gtt_orderbook_service as gtt_book

    broker = Upstox({CE: 65})
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
    fresh = gate.Gate(store=risk_store)
    fresh.set_state(KillSwitchState())
    monkeypatch.setattr(gate, "GATE", fresh)
    monkeypatch.setenv(flag.ENV, "on")
    return world


def _steps(answer) -> dict:
    return {step["step"]: step for step in answer.state.report["steps"]}


def test_exit_all_closes_a_position_whose_stop_loss_it_has_just_cancelled(upstox):
    upstox.broker.rest(CE, "SELL", 65)  # the stop loss protecting the long
    upstox.broker.pending_reads = 2  # its cancel shows on the third read of the order book
    answer = killswitch.activate(OWNER, exit_all=True, phrase="STOP")
    assert answer.status == 200
    assert upstox.broker.true_qty(CE) == 0  # flat
    assert upstox.broker.working == []
    steps = _steps(answer)
    assert steps["live_orders"]["ok"] and steps["live_exit_all"]["ok"], steps
    assert steps["live_exit_all"]["detail"]["cancels_shown"] is True
    each = steps["live_exit_all"]["detail"]["positions"]
    assert [(item["symbol"], item["outcome"]) for item in each] == [(CE, "sent")]
    assert steps["live_exit_all"]["detail"]["counts"] == {"sent": 1}


def test_without_the_wait_exit_all_would_leave_that_position_open(upstox, monkeypatch):
    """The defect the wait exists for: straight after the cancel, the stop loss still reads
    working, and Exit all sends nothing for its position."""
    monkeypatch.setattr(killswitch, "CANCELS_SHOW_WITHIN_S", 0.0)
    upstox.broker.rest(CE, "SELL", 65)
    upstox.broker.pending_reads = 5
    answer = killswitch.activate(OWNER, exit_all=True, phrase="STOP")
    assert upstox.broker.true_qty(CE) == 65  # still held
    step = _steps(answer)["live_exit_all"]
    assert step["ok"] is False and step["detail"]["cancels_shown"] is False
    assert [item["outcome"] for item in step["detail"]["positions"]] == ["not_sent"]
    assert "working to close it" in step["detail"]["positions"][0]["reason"]


def test_the_kill_switch_s_exit_all_takes_the_one_claim_every_exit_all_takes(upstox):
    """While its Exit all is being placed, an Exit from the phone for the same position is
    answered 409, as against any other Exit all: there is no second exit path."""
    from sudarshan.books import exit_guard

    upstox.broker.hang[CE] = 1.0  # the exit's place call takes a second
    running, out = upstox.background(
        lambda: killswitch.activate(OWNER, exit_all=True, phrase="STOP")
    )
    deadline = time.monotonic() + 10
    while not exit_guard.exit_all_held("live"):
        assert time.monotonic() < deadline, "Exit all never took its claim"
        time.sleep(0.01)
    code, body = upstox.phone_exit(CE)
    assert code == 409, body
    running.join(15)
    assert upstox.broker.true_qty(CE) == 0
    assert [order["symbol"] for order in upstox.broker.orders] == [CE]  # one exit, not two
    assert _steps(out["result"])["live_exit_all"]["ok"]


def test_a_new_order_racing_the_activation_is_refused_and_the_exit_still_goes(upstox, monkeypatch):
    """Once on, an entry from any surface is refused by the gate while the switch's own Exit
    all passes it: an exit always goes."""
    import services.place_order_service as place_service
    from services.place_order_service import place_order

    monkeypatch.setattr(place_service, "get_analyze_mode", lambda: False)
    upstox.broker.hang[CE] = 0.5
    running, out = upstox.background(
        lambda: killswitch.activate(OWNER, exit_all=True, phrase="STOP")
    )
    deadline = time.monotonic() + 10
    while not (gate.GATE.state() and gate.GATE.state().on):
        assert time.monotonic() < deadline
        time.sleep(0.01)
    entry = {"apikey": "KEY", "strategy": "Test", "symbol": "SBIN", "exchange": "NSE",
             "action": "BUY", "quantity": "1", "pricetype": "MARKET", "product": "MIS"}  # fmt: skip
    ok, body, code = place_order(entry, auth_token="token", broker="upstox")
    assert (ok, code) == (False, 403) and body["message"].startswith("New orders are refused")
    running.join(15)
    assert upstox.broker.true_qty(CE) == 0
    assert all(order["symbol"] == CE for order in upstox.broker.orders)
