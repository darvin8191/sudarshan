"""Independent review of feat/risk-guard (d628ff3): reproductions of findings in the gate.

Each test asserts what the invariant requires ("an exit always goes", "a split is judged as the
one order it was asked as"). A test that FAILS here reproduces a finding. Not for merging.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest

import restx_api  # noqa: F401 - first, as app.py does
from sudarshan.guard import flag, gate, killswitch, readers
from sudarshan.guard.store import KillSwitchState

REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "review_books_world", REPO / "sudarshan" / "books" / "tests" / "books_upstox_world.py"
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


# -- H1: claims outlive the orders they stand for -------------------------------------------------


def test_h1a_kill_switch_exit_all_after_a_part_exit_that_already_filled(monkeypatch, risk_store):
    """The owner sold half of 130 (filled, the book shows 65), then turns the kill switch on with
    Exit all within 15 s. Exit all must close the 65 left."""
    from services.place_order_service import place_order

    world = _world(monkeypatch, risk_store, {CE: 130}, KillSwitchState())
    ok, body, code = place_order(_order(CE, "SELL", 65), auth_token="token", broker="upstox")
    assert ok and world.broker.true_qty(CE) == 65  # filled, and the book shows it (no lag)
    answer = killswitch.activate(OWNER, exit_all=True, phrase="STOP")
    step = _steps(answer)["live_exit_all"]
    print("EXIT ALL STEP:", step)
    assert world.broker.true_qty(CE) == 0, step["words"]  # the invariant: Exit all flattens


def test_h1b_kill_switch_exit_all_after_its_own_cancel_of_a_resting_stop(monkeypatch, risk_store):
    """A stop loss placed through SUDARSHAN a few seconds ago rests at Upstox; the kill switch
    cancels it, then its Exit all must close the position the stop protected."""
    from services.place_order_service import place_order

    world = _world(monkeypatch, risk_store, {CE: 65}, KillSwitchState())
    broker = world.broker
    broker.rejected_later.add(CE)  # accepted with an id and rests (no fill)
    ok, body, code = place_order(
        _order(CE, "SELL", 65, pricetype="SL-M", trigger_price="98.5"),
        auth_token="token",
        broker="upstox",
    )
    assert ok, body
    with broker.lock:  # it shows working in Upstox's order book, as a resting stop does
        broker.working.append({"symbol": CE, "side": "SELL", "qty": 65, "kind": "SL-M",
                               "trigger": 98.5, "id": body["orderid"],
                               "at": __import__("time").time()})  # fmt: skip
    broker.rejected_later.discard(CE)  # from now on Upstox fills market orders again
    answer = killswitch.activate(OWNER, exit_all=True, phrase="STOP")
    steps = _steps(answer)
    print("CANCEL STEP:", steps["live_orders"])
    print("EXIT ALL STEP:", steps["live_exit_all"])
    assert broker.working == []  # the stop was cancelled
    assert broker.true_qty(CE) == 0, steps["live_exit_all"]["words"]  # and the position closed


def test_h1c_phone_exit_under_the_switch_after_a_part_exit_that_filled(monkeypatch, risk_store):
    """The same claim refuses the phone's Exit of what is left."""
    from services.place_order_service import place_order

    world = _world(monkeypatch, risk_store, {CE: 130}, KillSwitchState())
    assert place_order(_order(CE, "SELL", 65), auth_token="token", broker="upstox")[0]
    gate.GATE.set_state(ON)
    code, body = world.phone_exit(CE)
    print("PHONE EXIT:", code, body)
    assert code == 200 and world.broker.true_qty(CE) == 0, body


# -- H2: a flat book kept 5 s refuses the exit of a position opened since -----------------------


def test_h2_exit_all_of_a_position_opened_after_the_gate_s_last_flat_read(monkeypatch, risk_store):
    """Kill switch on. A refused entry made the gate read the book (CE flat). A resting order
    placed earlier then fills at Upstox (or a GTT fires there): CE is +65. Exit all reads it
    and sends the exit within 5 s of the gate's read."""
    from broker.upstox.api import order_api
    from services.place_order_service import place_order

    world = _world(monkeypatch, risk_store, {CE: 0, SBIN: 0}, ON)
    ok, body, code = place_order(_order(CE, "BUY", 65), auth_token="token", broker="upstox")
    assert code == 403  # refused, after reading the book (flat)
    import time

    with world.broker.lock:
        world.broker.fills.append((time.monotonic(), CE, 65))  # filled at Upstox meanwhile
    order_api._POSITION_BOOK.invalidate("token")  # the module's own 1 s cache has expired
    code, body = world.app_exit_all()
    print("EXIT ALL:", code, body)
    assert world.broker.true_qty(CE) == 0, body


def test_h2b_plain_exit_of_a_position_opened_after_the_gate_s_last_flat_read(
    monkeypatch, risk_store
):
    """The same through a plain exit (TradingView placeorder, scalping close, strategy module)."""
    from broker.upstox.api import order_api
    from services.place_order_service import place_order

    world = _world(monkeypatch, risk_store, {CE: 0, SBIN: 0}, ON)
    assert place_order(_order(SBIN, "BUY", 1), auth_token="token", broker="upstox")[2] == 403
    import time

    with world.broker.lock:
        world.broker.fills.append((time.monotonic(), CE, 65))
    order_api._POSITION_BOOK.invalidate("token")
    ok, body, code = place_order(_order(CE, "SELL", 65), auth_token="token", broker="upstox")
    print("PLAIN EXIT:", ok, code, body)
    assert ok and world.broker.true_qty(CE) == 0, body


# -- M: a reversal's closing part is refused with it -----------------------------------------


def test_m_tradingview_flip_under_the_switch_closes_nothing(monkeypatch, risk_store):
    """placesmartorder position_size -65 on +65 (a TradingView strategy flipping): refused whole,
    so the long it meant to close stays open. Documents the design's choice."""
    from services.place_smart_order_service import place_smart_order

    world = _world(monkeypatch, risk_store, {CE: 65}, ON)
    ok, body, code = place_smart_order(
        _order(CE, "SELL", 65, position_size="-65"), auth_token="token", broker="upstox"
    )
    print("FLIP:", ok, code, body)
    assert world.broker.true_qty(CE) == 0, body  # at least the long is closed


# -- M: an options order split into slices is not held whole --------------------------------------


def test_m_optionsorder_split_is_held_to_the_size_limit_whole(monkeypatch, risk_store):
    """/api/v1/optionsorder with splitsize: 10 lots in 1-lot slices against a 2-lot cap."""
    import services.place_options_order_service as options_service

    world = _world(monkeypatch, risk_store, {CE: 0}, KillSwitchState())
    risk_store.save_settings("owner", size_on=True, max_lots_fo=2)
    monkeypatch.setattr(readers, "lot_size", lambda symbol, exchange: 65.0)
    monkeypatch.setattr(options_service, "get_order_rate_limit", lambda: 0)
    monkeypatch.setattr(options_service, "get_analyze_mode", lambda: False)
    monkeypatch.setattr(
        options_service,
        "get_option_symbol",
        lambda **kwargs: (True, {"symbol": CE, "exchange": "NFO", "underlying_ltp": 25900.0}, 200),
    )
    ok, body, code = options_service.place_options_order(
        {
            "apikey": "KEY",
            "strategy": "Test",
            "underlying": "NIFTY",
            "exchange": "NSE_INDEX",
            "expiry_date": "27OCT26",
            "offset": "ATM",
            "option_type": "CE",
            "action": "BUY",
            "quantity": 650,
            "splitsize": 65,
            "pricetype": "MARKET",
            "product": "MIS",
        },
        api_key="KEY",
        auth_token="token",
        broker="upstox",
    )
    print("OPTIONSORDER:", ok, code, body.get("results") and len(body["results"]))
    sent = sum(order["qty"] for order in world.broker.orders)
    assert sent <= 130, f"{sent // 65} lots sent against a 2-lot cap"


def test_contrast_splitorder_is_held_whole(monkeypatch, risk_store):
    import services.split_order_service as split_service

    world = _world(monkeypatch, risk_store, {CE: 0}, KillSwitchState())
    risk_store.save_settings("owner", size_on=True, max_lots_fo=2)
    monkeypatch.setattr(readers, "lot_size", lambda symbol, exchange: 65.0)
    monkeypatch.setattr(split_service, "get_order_rate_limit", lambda: 0)
    split = {**_order(CE, "BUY", 650), "splitsize": "65"}
    ok, body, code = split_service.split_order_with_auth(split, "token", "upstox", split)
    assert code == 403 and world.broker.orders == []
