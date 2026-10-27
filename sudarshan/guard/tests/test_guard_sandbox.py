"""The gate on the sandbox's own order calls (M5.13): the place decorator decides inside the
position's lock, on the sandbox's own book, and refuses in the sandbox's answer shape.

The real sandbox tables and OrderManager / GTTManager run on a throwaway database with a seeded
symbol master; only the quote is fixed. The stage is ``sandbox``, as the owner's Analyzer-mode
sessions S1 and S2 run it: the Live account is never checked.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from sudarshan.guard import flag, gate, readers, words
from sudarshan.guard.store import KillSwitchState

USER = "guard-sandbox-owner"
ON = KillSwitchState(on=True, since=datetime(2026, 10, 27, 5, 12, tzinfo=UTC), activation_id="ks-s")
ENTRY = (
    "New orders are refused: the kill switch is on since 10:42. Exits and cancels still go. "
    "To trade again, resume from Settings > Risk or send /killswitch in Telegram."
)
SYMBOLS = [
    {"symbol": "SBIN", "brsymbol": "SBIN-EQ", "name": "STATE BANK OF INDIA", "exchange": "NSE",
     "brexchange": "NSE", "token": "3045", "lotsize": 1, "instrumenttype": "EQ",
     "tick_size": 0.05, "contract_value": 1.0},
]  # fmt: skip

_prepared = False


def _prepare() -> None:
    global _prepared
    if _prepared:
        return
    from database.sandbox_db import init_db as init_sandbox
    from database.symbol import SymToken
    from database.symbol import db_session as symbols
    from database.symbol import init_db as init_symbols

    init_symbols()
    init_sandbox()
    for spec in SYMBOLS:
        if (
            SymToken.query.filter_by(symbol=spec["symbol"], exchange=spec["exchange"]).first()
            is None
        ):
            symbols.add(SymToken(**spec))
    symbols.commit()
    symbols.remove()
    _prepared = True


def _reset() -> None:
    from database.sandbox_db import (
        SandboxFunds,
        SandboxGTT,
        SandboxHoldings,
        SandboxOrders,
        SandboxPositions,
        SandboxTrades,
        db_session,
    )

    for model in (SandboxOrders, SandboxTrades, SandboxPositions, SandboxHoldings, SandboxGTT):
        model.query.filter_by(user_id=USER).delete()
    SandboxFunds.query.filter_by(user_id=USER).delete()
    capital = Decimal("10000000.00")
    db_session.add(
        SandboxFunds(
            user_id=USER,
            total_capital=capital,
            available_balance=capital,
            used_margin=Decimal("0"),
            realized_pnl=Decimal("0"),
            today_realized_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
            total_pnl=Decimal("0"),
            last_reset_date=datetime.now(),
            reset_count=0,
        )
    )
    db_session.commit()
    db_session.remove()


@pytest.fixture
def sandbox(monkeypatch, risk_store):
    _prepare()
    _reset()
    import sandbox.order_manager as order_manager
    from sandbox.execution_engine import ExecutionEngine

    class Morning(datetime):
        """11:00 in India today: MIS orders are refused after the sandbox's 15:15 square-off."""

        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz).replace(hour=11, minute=0, second=0, microsecond=0)

    monkeypatch.setattr(order_manager, "datetime", Morning)

    quote = {"ltp": 800.0, "bid": 799.95, "ask": 800.05, "high": 810.0, "low": 790.0}
    monkeypatch.setattr(ExecutionEngine, "_fetch_quote", lambda self, symbol, exchange: quote)
    fresh = gate.Gate(store=risk_store)
    fresh.set_state(KillSwitchState())
    monkeypatch.setattr(gate, "GATE", fresh)
    monkeypatch.setenv(flag.ENV, "sandbox")
    yield fresh
    from database.sandbox_db import db_session

    db_session.remove()


def _place(action, quantity, price_type="MARKET", price=0, product="MIS"):
    from sandbox.order_manager import OrderManager

    return OrderManager(USER).place_order(
        {
            "symbol": "SBIN",
            "exchange": "NSE",
            "action": action,
            "quantity": quantity,
            "price": price,
            "price_type": price_type,
            "product": product,
        }
    )


def _position() -> int:
    from database.sandbox_db import SandboxPositions, db_session

    db_session.remove()
    row = SandboxPositions.query.filter_by(user_id=USER, symbol="SBIN", product="MIS").first()
    return 0 if row is None else row.quantity


def test_with_the_switch_on_a_sandbox_entry_is_refused_and_nothing_is_stored(sandbox):
    sandbox.set_state(ON)
    assert _place("BUY", 10) == (
        False,
        {"status": "error", "message": ENTRY, "mode": "analyze"},
        403,
    )
    from database.sandbox_db import SandboxOrders

    assert SandboxOrders.query.filter_by(user_id=USER).count() == 0


def test_a_sandbox_exit_goes_and_closes_the_position(sandbox):
    ok, body, status = _place("BUY", 10)
    assert ok, body
    assert _position() == 10
    sandbox.set_state(ON)
    ok, body, status = _place("SELL", 10)
    assert ok and status == 200, body
    assert _position() == 0
    assert _place("SELL", 10)[2] == 403  # flat now: a sell would open a short


def test_the_sandbox_s_own_exits_and_square_off_pass(sandbox):
    from sandbox.position_manager import PositionManager

    assert _place("BUY", 10)[0]
    sandbox.set_state(ON)
    ok, body, status = PositionManager(USER).close_position("SBIN", "NSE", "MIS")
    assert ok, body
    assert _position() == 0


def test_the_place_decorator_decides_inside_the_position_s_lock(sandbox, monkeypatch):
    from sandbox import position_locks

    seen = []
    original = readers.sandbox_position

    def watch(*args):
        seen.append(position_locks.held_position_count())
        return original(*args)

    monkeypatch.setattr(readers, "sandbox_position", watch)
    sandbox.set_state(ON)
    _place("BUY", 10)
    assert seen == [1]


def test_a_sandbox_modify_is_judged_as_the_order_the_sandbox_has(sandbox):
    from sandbox.order_manager import OrderManager

    ok, body, _ = _place("BUY", 10, "LIMIT", 700)  # rests: the market is at 800
    assert ok, body
    orderid = body["orderid"]
    sandbox.set_state(ON)
    manager = OrderManager(USER)
    ok, answer, status = manager.modify_order(orderid, {"quantity": 5, "price": 690})
    assert ok and status == 200, answer  # less risk: fewer, and further from the market
    ok, answer, status = manager.modify_order(orderid, {"quantity": 5, "price": 750})
    assert (ok, status) == (False, 403) and answer["message"] == ENTRY


def test_sandbox_gtts_two_legs_refused_a_single_stop_on_a_held_position_passes(sandbox):
    from sandbox.gtt_manager import GTTManager

    assert _place("BUY", 10)[0]
    sandbox.set_state(ON)
    base = {"symbol": "SBIN", "exchange": "NSE", "action": "SELL", "quantity": 10,
            "product": "MIS", "pricetype": "LIMIT", "price": 780}  # fmt: skip
    ok, body, status = GTTManager(USER).place_gtt(
        {**base, "trigger_type": "OCO", "triggerprice_sl": 790, "triggerprice_tg": 850}, 800
    )
    assert (ok, status) == (False, 403)
    assert "A two-leg GTT opens a position at Upstox" in body["message"]
    ok, body, status = GTTManager(USER).place_gtt(
        {**base, "trigger_type": "SINGLE", "triggerprice_sl": 790}, 800
    )
    assert ok, body


def test_with_the_stage_off_the_sandbox_is_not_checked(sandbox, monkeypatch):
    monkeypatch.setenv(flag.ENV, "off")
    sandbox.set_state(ON)
    assert _place("BUY", 10)[0]


def test_a_sandbox_book_that_cannot_be_read_refuses_while_the_switch_is_on(sandbox, monkeypatch):
    def broken(*args):
        raise readers.Unreadable("sandbox positions")

    monkeypatch.setattr(readers, "sandbox_position", broken)
    sandbox.set_state(ON)
    ok, body, status = _place("SELL", 10)
    assert (ok, status) == (False, 403)
    assert body["message"] == words.book_unreadable("sandbox", "10:42")


def test_a_sandbox_modify_that_raises_the_quantity_is_refused(sandbox):
    from sandbox.order_manager import OrderManager

    ok, body, _ = _place("BUY", 10, "LIMIT", 700)
    assert ok, body
    sandbox.set_state(ON)
    ok, answer, status = OrderManager(USER).modify_order(body["orderid"], {"quantity": 20})
    assert (ok, status) == (False, 403) and answer["message"] == ENTRY
