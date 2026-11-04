"""Analyze mode follows Live's rule for a smart order to position size 0 (M6.9, Z1).

TradingView's strategy alert carries the size after the order, so its only size-0 alert is an
exit: from flat it sends nothing, in the sandbox as in Live. To open a position, give the size.
"""

from __future__ import annotations

import pytest

NOTHING_OPEN = "No open position found. Not placing exit order."
RELIANCE_LONG = {"symbol": "RELIANCE", "exchange": "NSE", "product": "CNC", "quantity": 10}


@pytest.fixture
def sandbox(monkeypatch):
    import services.sandbox_service as sandbox_service
    from sandbox.order_manager import OrderManager
    from sandbox.position_manager import PositionManager

    placed = []
    held = []

    class Holding(PositionManager):
        def get_open_positions(self, update_mtm=True):
            return True, {"status": "success", "data": [dict(row) for row in held]}, 200

    def place(self, data):
        placed.append((data["action"], data["quantity"]))
        return True, {"status": "success", "orderid": "S1", "mode": "analyze"}, 200

    monkeypatch.setattr(sandbox_service, "get_user_id_from_apikey", lambda key: "owner")
    monkeypatch.setattr(sandbox_service, "PositionManager", Holding)
    monkeypatch.setattr(OrderManager, "place_order", place)
    return sandbox_service, placed, held


def _order(**fields):
    return {
        "symbol": "RELIANCE",
        "exchange": "NSE",
        "product": "CNC",
        "action": "BUY",
        "pricetype": "MARKET",
        "quantity": "10",
        "position_size": "0",
        **fields,
    }


@pytest.mark.parametrize("action", ["BUY", "SELL"])
def test_z1_a_sandbox_size_0_order_from_flat_places_nothing(sandbox, action):
    service, placed, _held = sandbox
    ok, answer, code = service.sandbox_place_smart_order(_order(action=action), "key", {})
    assert (ok, code) == (True, 200)
    assert answer == {"status": "success", "message": NOTHING_OPEN, "mode": "analyze"}
    assert placed == []


def test_z1_a_sandbox_size_0_order_closes_a_position_that_is_open(sandbox):
    service, placed, held = sandbox
    held.append(RELIANCE_LONG)
    ok, answer, code = service.sandbox_place_smart_order(
        _order(action="SELL", quantity="10"), "key", {}
    )
    assert (ok, code) == (True, 200)
    assert placed == [("SELL", 10)]


@pytest.mark.parametrize(
    "action,size,sent",
    [("BUY", "10", ("BUY", 10)), ("SELL", "-10", ("SELL", 10))],
    ids=["long entry", "short entry"],
)
def test_z1_a_sandbox_entry_with_a_size_other_than_0_is_placed(sandbox, action, size, sent):
    """Control: TradingView's entry shape, the size after the order."""
    service, placed, _held = sandbox
    ok, answer, code = service.sandbox_place_smart_order(
        _order(action=action, position_size=size), "key", {}
    )
    assert (ok, code) == (True, 200)
    assert placed == [sent]
