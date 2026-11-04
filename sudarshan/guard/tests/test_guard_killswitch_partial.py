"""Release review A, H2: the kill switch's Exit all after it cancelled a part-filled exit.

Upstox's positions book can show a fill a minute late. The owner is long 130 and taps Exit; 65
fills and 65 rests. The switch cancels the 65 and its Exit all must sell what the order book
says is still held (130 less the 65 filled), never the 130 an old positions row still shows.
"""

from __future__ import annotations

import pytest
from test_guard_killswitch_upstox import CE, OWNER, Upstox, world_module

from sudarshan.guard import flag, gate, killswitch
from sudarshan.guard.store import KillSwitchState


def _world(monkeypatch, risk_store, lag):
    import services.gtt_orderbook_service as gtt_book

    broker = Upstox({CE: 130}, lag=lag)
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


@pytest.mark.parametrize("lag", [60.0, 0.0], ids=["book 60 s behind", "book up to date"])
def test_exit_all_sells_only_what_the_order_book_says_is_still_held(monkeypatch, risk_store, lag):
    world = _world(monkeypatch, risk_store, lag)
    broker = world.broker
    broker.part[CE] = 65  # the Exit of 130 fills 65; the other 65 keeps working
    code, _body = world.phone_exit(CE)
    assert code == 200
    del broker.part[CE]
    answer = killswitch.activate(OWNER, exit_all=True, phrase="STOP")
    assert answer.activated
    sold = [order["qty"] for order in broker.orders if order["symbol"] == CE]
    assert sold == [130, 65], sold  # never 130 twice
    assert broker.true_qty(CE) == 0
