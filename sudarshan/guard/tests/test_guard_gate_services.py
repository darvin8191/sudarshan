"""The gate through OpenAlgo's own services and the real Upstox module (M5.13).

Over the simulated Upstox of sudarshan/books/tests/books_upstox_world.py (instant fills, a
position book that can lag): with the kill switch on, every way a new order is sent answers 403
with the gate's sentence and sends nothing, while the app's Exit, Exit all, a smart order to size
0 and a plain exit all go. With the guard off, everything goes as before.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest

import restx_api  # noqa: F401 - first, as app.py does: services.place_order_service imports it back
from sudarshan.guard import flag, gate
from sudarshan.guard.store import KillSwitchState

REPO = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "guard_books_world", REPO / "sudarshan" / "books" / "tests" / "books_upstox_world.py"
)
world_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(world_module)

CE = "NIFTY27OCT2625900CE"  # MIS in the world
PE = "BANKNIFTY27OCT2657500PE"  # NRML
SBIN = "SBIN"  # MIS
ON = KillSwitchState(on=True, since=datetime(2026, 10, 27, 5, 12, tzinfo=UTC), surface="app")
ENTRY = (
    "New orders are refused: the kill switch is on since 10:42. Exits and cancels still go. "
    "To trade again, resume from Settings > Risk or send /killswitch in Telegram."
)


@pytest.fixture
def upstox(monkeypatch, risk_store):
    import services.basket_order_service as basket_service
    import services.place_order_service as place_service
    import services.split_order_service as split_service

    broker = world_module.Broker({CE: 65, PE: -30}, lag=0.0)
    world = world_module.install(monkeypatch, broker)
    for service in (place_service, basket_service, split_service):
        monkeypatch.setattr(service, "get_analyze_mode", lambda: False)
    fresh = gate.Gate(store=risk_store)
    fresh.set_state(ON)
    monkeypatch.setattr(gate, "GATE", fresh)
    monkeypatch.setenv(flag.ENV, "on")
    return world


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


def test_placeorder_refuses_an_entry_with_403_and_the_sentence(upstox):
    from services.place_order_service import place_order

    answer = place_order(_order(SBIN, "BUY", 1), auth_token="token", broker="upstox")
    assert answer == (False, {"status": "error", "message": ENTRY}, 403)
    assert upstox.broker.orders == []


def test_placeorder_lets_an_exit_go(upstox):
    from services.place_order_service import place_order

    ok, body, code = place_order(_order(CE, "SELL", 65), auth_token="token", broker="upstox")
    assert (ok, code) == (True, 200) and body["orderid"]
    assert upstox.broker.history(CE) == [65, 0]


def test_a_smart_order_to_size_0_exits_and_one_that_opens_is_refused(upstox):
    from services.place_smart_order_service import place_smart_order

    code, body = upstox.tv_smart_exit(CE)
    assert code == 200 and body["status"] == "success"
    ok, body, code = place_smart_order(
        _order(SBIN, "BUY", 1, position_size="1"), auth_token="token", broker="upstox"
    )
    assert (ok, code, body["message"]) == (False, 403, ENTRY)
    assert [o["symbol"] for o in upstox.broker.orders] == [CE]


def test_the_app_s_exit_and_exit_all_go(upstox):
    code, body = upstox.phone_exit(PE)
    assert code == 200, body
    assert upstox.broker.history(PE) == [-30, 0]
    code, body = upstox.app_exit_all()
    assert code == 200, body
    assert upstox.broker.true_qty(CE) == 0


def test_a_basket_sends_its_exit_and_refuses_its_entry(upstox):
    from services.basket_order_service import process_basket_order_with_auth

    basket = {
        "apikey": "KEY",
        "strategy": "Test",
        "orders": [_order(CE, "SELL", 65), _order(SBIN, "BUY", 1)],
    }
    ok, body, code = process_basket_order_with_auth(basket, "token", "upstox", basket)
    results = {r["symbol"]: r for r in body["results"]}
    assert results[CE]["status"] == "success"
    assert results[SBIN] == {"symbol": SBIN, "status": "error", "message": ENTRY}
    assert [o["symbol"] for o in upstox.broker.orders] == [CE]


def test_a_split_entry_sends_no_slice(upstox, monkeypatch):
    import services.split_order_service as split_service

    monkeypatch.setattr(split_service, "get_order_rate_limit", lambda: 0)
    split = {**_order(SBIN, "BUY", 3), "splitsize": "1"}
    ok, body, code = split_service.split_order_with_auth(split, "token", "upstox", split)
    assert [r["message"] for r in body["results"]] == [ENTRY] * 3
    assert upstox.broker.orders == []


def test_a_sell_beyond_the_position_names_the_exit_that_fits(upstox):
    from services.place_order_service import place_order

    ok, body, code = place_order(_order(CE, "SELL", 130), auth_token="token", broker="upstox")
    assert code == 403
    assert (
        "would close NIFTY 25900 CE and open it the other way; to exit, sell 65."
        in (body["message"])
    )
    assert upstox.broker.orders == []


def test_with_the_guard_off_an_entry_goes_as_before(upstox, monkeypatch):
    from services.place_order_service import place_order

    monkeypatch.delenv(flag.ENV)
    ok, body, code = place_order(_order(SBIN, "BUY", 1), auth_token="token", broker="upstox")
    assert (ok, code) == (True, 200)


def test_with_the_guard_off_the_order_api_reads_nothing_more(upstox, monkeypatch):
    """The real place_order_api: one place call to Upstox, no positions read, the same answer."""
    from broker.upstox.api import order_api

    monkeypatch.delenv(flag.ENV)
    reads_before = upstox.broker.reads
    res, body, orderid = order_api.place_order_api(_order(SBIN, "BUY", 1), "token")
    res2, body2, orderid2 = order_api.place_order_api.__wrapped__(_order(SBIN, "BUY", 1), "token")
    assert (res.status, body["status"]) == (res2.status, body2["status"]) == (200, "success")
    assert upstox.broker.reads == reads_before
    assert len(upstox.broker.orders) == 2
