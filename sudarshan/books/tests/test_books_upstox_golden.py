"""Golden vectors: raw Upstox order book rows to the rows the phone's order cards read (M6.9).

Each row is in the shape Upstox's GET /v2/order/retrieve-all returns it (instrument_token, the
broker's own trading symbol, product codes I and D, lower-case statuses), and goes through the
same chain as POST /api/v1/orderbook: map_order_data -> calculate_order_statistics ->
transform_order_data -> format_order_data. The expected rows are written out in full, so any
field that appears, disappears or changes its value fails here first.

No fill fields reach the book (see test_books_upstox_mapping.py for why), so a part fill, a
complete order's average and the part filled before a cancel are absent here; the cards show
them only from the order stream.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask
from flask_restx import Api

from broker.upstox.mapping.order_data import (
    calculate_order_statistics,
    map_order_data,
    transform_order_data,
)
from limiter import limiter
from services.orderbook_service import format_order_data

ROOT = Path(__file__).resolve().parents[3]

#: OpenAlgo's symbol for each instrument key, as the master contract maps it.
SYMBOLS = {
    ("NSE_FO|49213", "NFO"): "NIFTY27OCT2625900CE",
    ("NSE_FO|49214", "NFO"): "NIFTY27OCT2625900PE",
    ("NSE_FO|51077", "NFO"): "BANKNIFTY27OCT2658000CE",
    ("NSE_EQ|INE002A01018", "NSE"): "RELIANCE",
}


def _raw(order_id: str, **fields) -> dict:
    """One Upstox order book row; every field Upstox documents, then this order's own."""
    row = {
        "exchange": "NFO",
        "product": "I",
        "price": 0.0,
        "quantity": 65,
        "status": "open",
        "guid": None,
        "tag": None,
        "instrument_token": "NSE_FO|49213",
        "placed_by": "TEST01",
        "tradingsymbol": "NIFTY 25900 CE 27 OCT 26",
        "trading_symbol": "NIFTY 25900 CE 27 OCT 26",
        "order_type": "LIMIT",
        "validity": "DAY",
        "trigger_price": 0.0,
        "disclosed_quantity": 0,
        "transaction_type": "BUY",
        "average_price": 0.0,
        "filled_quantity": 0,
        "pending_quantity": 65,
        "status_message": None,
        "status_message_raw": None,
        "exchange_order_id": "1100000000000001",
        "parent_order_id": None,
        "order_id": order_id,
        "variety": "SIMPLE",
        "order_timestamp": "2026-10-21 10:41:51",
        "exchange_timestamp": "2026-10-21 10:41:51",
        "is_amo": False,
        "order_request_id": "1",
        "order_ref_id": "",
    }
    row.update(fields)
    return row


def _row(order_id: str, **fields) -> dict:
    """The row OpenAlgo's order book answers for it: upstream's fields, nothing more."""
    row = {
        "symbol": "NIFTY27OCT2625900CE",
        "exchange": "NFO",
        "action": "BUY",
        "quantity": 65,
        "price": 0.0,
        "trigger_price": 0.0,
        "pricetype": "LIMIT",
        "product": "MIS",
        "orderid": order_id,
        "order_status": "open",
        "timestamp": "2026-10-21 10:41:51",
    }
    row.update(fields)
    return row


VECTORS = {
    "open": (
        _raw("251021000000101", price=142.5),
        _row("251021000000101", price=142.5),
    ),
    "part-filled": (
        _raw(
            "251021000000102",
            transaction_type="SELL",
            quantity=130,
            price=168.0,
            average_price=168.05,
            filled_quantity=65,
            pending_quantity=65,
            status_message="",
        ),
        _row("251021000000102", action="SELL", quantity=130, price=168.0),
    ),
    "complete": (
        _raw(
            "251021000000103",
            exchange="NSE",
            product="D",
            instrument_token="NSE_EQ|INE002A01018",
            tradingsymbol="RELIANCE",
            trading_symbol="RELIANCE",
            quantity=40,
            order_type="MARKET",
            status="complete",
            average_price=1452.35,
            filled_quantity=40,
            pending_quantity=0,
        ),
        _row(
            "251021000000103",
            symbol="RELIANCE",
            exchange="NSE",
            quantity=40,
            pricetype="MARKET",
            product="CNC",
            order_status="complete",
        ),
    ),
    "cancelled after a part fill": (
        # Two lots of a 65-lot NIFTY option, one of them filled: fills come in whole lots.
        _raw(
            "251021000000104",
            product="D",
            price=140.0,
            quantity=130,
            status="cancelled",
            average_price=139.95,
            filled_quantity=65,
            pending_quantity=0,
        ),
        _row(
            "251021000000104",
            price=140.0,
            quantity=130,
            product="NRML",
            order_status="cancelled",
        ),
    ),
    "trigger pending": (
        _raw(
            "251021000000105",
            instrument_token="NSE_FO|49214",
            tradingsymbol="NIFTY 25900 PE 27 OCT 26",
            trading_symbol="NIFTY 25900 PE 27 OCT 26",
            transaction_type="SELL",
            order_type="SL",
            price=119.5,
            trigger_price=120.0,
            status="trigger pending",
        ),
        _row(
            "251021000000105",
            symbol="NIFTY27OCT2625900PE",
            action="SELL",
            price=119.5,
            trigger_price=120.0,
            pricetype="SL",
        ),
    ),
    "rejected with a message": (
        _raw(
            "251021000000106",
            instrument_token="NSE_FO|51077",
            tradingsymbol="BANKNIFTY 58000 CE 27 OCT 26",
            trading_symbol="BANKNIFTY 58000 CE 27 OCT 26",
            quantity=30,
            order_type="SL",
            price=310.0,
            trigger_price=305.0,
            status="rejected",
            pending_quantity=0,
            status_message="RMS:Margin Exceeds,Required:14250.00, Available:9120.55 ",
            status_message_raw="RMS:Margin Exceeds,Required:14250.00, Available:9120.55 ",
        ),
        _row(
            "251021000000106",
            symbol="BANKNIFTY27OCT2658000CE",
            quantity=30,
            price=310.0,
            trigger_price=305.0,
            pricetype="SL",
            order_status="rejected",
            rejection_reason="RMS:Margin Exceeds,Required:14250.00, Available:9120.55",
        ),
    ),
    "rejected without a message": (
        _raw("251021000000107", price=150.0, status="rejected", pending_quantity=0),
        _row("251021000000107", price=150.0, order_status="rejected", rejection_reason=""),
    ),
    "cancelled with a message": (
        _raw(
            "251021000000108",
            price=141.0,
            validity="IOC",
            status="cancelled",
            pending_quantity=0,
            status_message="IOC order cancelled at the exchange",
        ),
        # A cancelled order's message is not a rejection's reason: no rejection_reason at all.
        _row("251021000000108", price=141.0, order_status="cancelled"),
    ),
}


def _symbol(token: str, exchange: str) -> str | None:
    return SYMBOLS.get((token, exchange))


def _through_the_book(raws: list[dict]) -> tuple[list[dict], dict]:
    """The chain services/orderbook_service.get_orderbook_with_auth runs, in its order."""
    with patch("broker.upstox.mapping.order_data.get_symbol", side_effect=_symbol):
        mapped = map_order_data({"status": "success", "data": [dict(raw) for raw in raws]})
    stats = calculate_order_statistics(mapped)
    return format_order_data(transform_order_data(mapped)), stats


@pytest.mark.parametrize("name", list(VECTORS))
def test_a_raw_upstox_row_reaches_the_order_book_as_written(name):
    raw, expected = VECTORS[name]
    (row,) = _through_the_book([raw])[0]
    assert row == expected


def test_no_fill_field_reaches_the_order_book():
    rows, _ = _through_the_book([raw for raw, _ in VECTORS.values()])
    for row in rows:
        assert not {"filled_quantity", "pending_quantity", "average_price"} & set(row), row


def test_the_statistics_count_trigger_pending_as_open():
    _, stats = _through_the_book([raw for raw, _ in VECTORS.values()])
    assert stats == {
        "total_buy_orders": 6,
        "total_sell_orders": 2,
        "total_completed_orders": 1,
        "total_open_orders": 3,
        "total_rejected_orders": 2,
    }


def _orderbook_resource() -> types.ModuleType:
    """restx_api/orderbook.py, loaded without the restx_api package.

    The package's __init__ imports every namespace, and some of those start threads and open a
    database at import. A stand-in parent lets the module's one relative import
    (`.account_schema`) resolve from the same directory; it is removed again afterwards.
    """
    names = ("restx_api", "restx_api.account_schema", "restx_api.orderbook")
    saved = {name: sys.modules.get(name) for name in names}
    parent = types.ModuleType("restx_api")
    parent.__path__ = [str(ROOT / "restx_api")]
    sys.modules["restx_api"] = parent
    try:
        spec = importlib.util.spec_from_file_location(
            "restx_api.orderbook", ROOT / "restx_api" / "orderbook.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        for name, before in saved.items():
            if before is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = before
    return module


@pytest.fixture
def client(monkeypatch):
    app = Flask(__name__)
    app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    monkeypatch.setattr(limiter, "enabled", False)
    Api(app).add_namespace(_orderbook_resource().api, path="/api/v1/orderbook")
    return app.test_client()


def test_post_orderbook_answers_the_golden_rows(client):
    raws = [VECTORS["part-filled"][0], VECTORS["rejected with a message"][0]]
    with (
        patch(
            "services.orderbook_service.get_auth_token_broker",
            return_value=("test-token", "upstox"),
        ),
        patch("database.settings_db.get_analyze_mode", return_value=False),
        patch(
            "broker.upstox.api.order_api.get_order_book",
            return_value={"status": "success", "data": raws},
        ),
        patch("broker.upstox.mapping.order_data.get_symbol", side_effect=_symbol),
    ):
        response = client.post("/api/v1/orderbook", json={"apikey": "test-key"})

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "success",
        "data": {
            "orders": [VECTORS["part-filled"][1], VECTORS["rejected with a message"][1]],
            "statistics": {
                "total_buy_orders": 1,
                "total_sell_orders": 1,
                "total_completed_orders": 0,
                "total_open_orders": 1,
                "total_rejected_orders": 1,
            },
        },
    }
