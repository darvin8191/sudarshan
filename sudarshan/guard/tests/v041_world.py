"""The simulated Upstox, wired to the real services and the risk guard, for the v0.4.1 exit tests
(test_guard_v041_exits.py): the books world of sudarshan/books/tests with Upstox's cancel, the kill
switch's side effects stubbed, and the guard switched on (M5.13, M6.9)."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import restx_api  # noqa: F401 - first, as app.py does
from sudarshan.guard import flag, gate, killswitch, readers
from sudarshan.guard.store import KillSwitchState

REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "v041_books_world", REPO / "sudarshan" / "books" / "tests" / "books_upstox_world.py"
)
world_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(world_module)

CE = "NIFTY27OCT2625900CE"  # MIS in the world
SBIN = "SBIN"
ON = KillSwitchState(on=True, since=datetime(2026, 10, 27, 5, 12, tzinfo=UTC), surface="app",
                     activation_id="ks-review")  # fmt: skip
OWNER = killswitch.Owner("owner", "app", "owner", auth_token="token", broker="upstox")


def _order(symbol, action, quantity, **extra):
    token, exchange, _code, product = world_module.INSTRUMENTS[symbol]
    return {
        "apikey": "KEY",
        "strategy": "Test",
        "symbol": symbol,
        "exchange": exchange,
        "action": action,
        "quantity": str(quantity),
        "pricetype": "MARKET",
        "product": product,
        **extra,
    }


class Upstox(world_module.Broker):
    """The simulated Upstox, which also cancels (as test_guard_killswitch_upstox.py's)."""

    def delete(self, url, headers=None, **kwargs):
        from urllib.parse import parse_qs, urlparse

        orderid = parse_qs(urlparse(url).query)["order_id"][0]
        with self.lock:
            self.working = [order for order in self.working if order["id"] != orderid]
        return world_module.FakeResponse(200, {"status": "success", "data": {"order_id": orderid}})


def _world(monkeypatch, risk_store, held, state):
    import services.gtt_orderbook_service as gtt_book
    import services.place_order_service as place_service

    broker = Upstox(held, lag=0.0)
    world = world_module.install(monkeypatch, broker)
    monkeypatch.setattr(place_service, "get_analyze_mode", lambda: False)
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
    fresh.set_state(state)
    monkeypatch.setattr(gate, "GATE", fresh)
    monkeypatch.setenv(flag.ENV, "on")
    return world


def _steps(answer) -> dict:
    return {step["step"]: step for step in answer.state.report["steps"]}


