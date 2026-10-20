"""The Upstox books carry what the phone's book cards read (M6.9).

Golden rows in the shape Upstox's REST order book and position book return them, through
OpenAlgo's own transforms. Each added field is passed through only when Upstox sends it, so a
reader can tell "not sent" from zero: the alerts re-sync (`sudarshan/alerts/resync.py`) then takes
its fills from the trade book as before, and the cards leave the line out.
"""

from __future__ import annotations

import pytest

from broker.upstox.mapping.order_data import (
    transform_order_data,
    transform_positions_data,
)

#: An order Upstox refused, as its order book returns it.
REJECTED = {
    "exchange": "NFO",
    "product": "MIS",
    "price": 310.0,
    "quantity": 30,
    "status": "rejected",
    "tradingsymbol": "BANKNIFTY27OCT2658000CE",
    "order_type": "SL",
    "trigger_price": 305.0,
    "transaction_type": "BUY",
    "average_price": 0.0,
    "filled_quantity": 0,
    "pending_quantity": 0,
    "status_message": "RMS:Margin Exceeds,Required:14250.00, Available:9120.55 ",
    "order_id": "251020000301196",
    "order_timestamp": "2026-10-20 09:22:13",
}

#: A limit order part filled, still working.
PART_FILLED = {
    "exchange": "NFO",
    "product": "MIS",
    "price": 168.0,
    "quantity": 130,
    "status": "open",
    "tradingsymbol": "NIFTY27OCT2625900CE",
    "order_type": "LIMIT",
    "trigger_price": 0.0,
    "transaction_type": "SELL",
    "average_price": 168.05,
    "filled_quantity": 65,
    "pending_quantity": 65,
    "status_message": "",
    "order_id": "251020000598204",
    "order_timestamp": "2026-10-20 10:41:51",
}


def test_an_order_carries_its_fill_and_a_rejection_its_reason():
    rejected, part = transform_order_data([REJECTED, PART_FILLED])

    assert rejected["order_status"] == "rejected"
    assert rejected["rejection_reason"] == "RMS:Margin Exceeds,Required:14250.00, Available:9120.55"
    assert rejected["filled_quantity"] == 0

    assert part["order_status"] == "open"
    assert (part["filled_quantity"], part["pending_quantity"], part["average_price"]) == (
        65,
        65,
        168.05,
    )
    # A working order's message is not a reason: nothing was refused.
    assert "rejection_reason" not in part


def test_a_rejection_without_a_message_says_none_rather_than_guessing():
    (row,) = transform_order_data([{**REJECTED, "status_message": None}])
    assert row["rejection_reason"] == ""


def test_fields_upstox_does_not_send_are_left_out_not_zeroed():
    bare = {
        key: value
        for key, value in PART_FILLED.items()
        if key not in ("average_price", "filled_quantity", "pending_quantity", "status_message")
    }
    (row,) = transform_order_data([bare])
    assert not {"average_price", "filled_quantity", "pending_quantity"} & set(row)
    # Everything upstream already mapped is unchanged.
    assert row == {
        "symbol": "NIFTY27OCT2625900CE",
        "exchange": "NFO",
        "action": "SELL",
        "quantity": 130,
        "price": 168.0,
        "trigger_price": 0.0,
        "pricetype": "LIMIT",
        "product": "MIS",
        "orderid": "251020000598204",
        "order_status": "open",
        "timestamp": "2026-10-20 10:41:51",
    }


#: A position partly booked today: bought 130, sold 65; 65 still held.
POSITION = {
    "exchange": "NFO",
    "multiplier": 1.0,
    "value": -9100.0,
    "pnl": 1950.0,
    "product": "MIS",
    "instrument_token": "NSE_FO|49213",
    "average_price": 120.0,
    "buy_value": 15600.0,
    "overnight_quantity": 0,
    "day_buy_value": 15600.0,
    "day_buy_price": 120.0,
    "overnight_buy_amount": 0.0,
    "overnight_buy_quantity": 0,
    "day_buy_quantity": 130,
    "day_sell_value": 9100.0,
    "day_sell_price": 140.0,
    "overnight_sell_amount": 0.0,
    "overnight_sell_quantity": 0,
    "day_sell_quantity": 65,
    "quantity": 65,
    "last_price": 130.0,
    "unrealised": 650.0,
    "realised": 1300.0,
    "sell_value": 9100.0,
    "tradingsymbol": "NIFTY27OCT2625900CE",
    "close_price": 118.4,
    "buy_price": 120.0,
    "sell_price": 140.0,
}


def test_a_position_carries_what_its_sheet_shows():
    (row,) = transform_positions_data([POSITION])
    assert {key: row[key] for key in ("realised", "unrealised")} == {
        "realised": 1300.0,
        "unrealised": 650.0,
    }
    assert (row["day_buy_quantity"], row["day_buy_value"]) == (130, 15600.0)
    assert (row["day_sell_quantity"], row["day_sell_value"]) == (65, 9100.0)
    assert row["overnight_quantity"] == 0
    # Upstream's own fields are as they were.
    assert (row["quantity"], row["average_price"], row["pnl"], row["ltp"]) == (
        65,
        120.0,
        1950.0,
        130.0,
    )


@pytest.mark.parametrize("missing", ["realised", "unrealised", "overnight_quantity"])
def test_a_position_field_upstox_leaves_out_stays_out(missing):
    (row,) = transform_positions_data([{**POSITION, missing: None}])
    assert missing not in row
