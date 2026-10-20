"""The Upstox books carry what the phone's book cards read (M6.9).

Golden rows in the shape Upstox's REST order book and position book return them, through
OpenAlgo's own transforms. A rejected order carries the broker's reason. An order carries no fill
fields, on purpose: /orderstatus answers from this same book and replaces the average price of any
order that is not complete with 0.0, so a filled quantity here would reach the strategy module's
reconciliation as a part fill with no price (`test_orderstatus_answers_a_part_fill_as_before`).
The cards take fills from the order stream, and the alerts re-sync (`sudarshan/alerts/resync.py`)
from the trade book. A position field is passed through only when Upstox sends it, so a reader can
tell "not sent" from zero.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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

#: What OpenAlgo's order book row for PART_FILLED was before M6.9, and still is.
PART_FILLED_ROW = {
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

FILL_FIELDS = {"filled_quantity", "pending_quantity", "average_price"}


def test_a_rejection_carries_the_brokers_reason_and_no_fill():
    (rejected,) = transform_order_data([REJECTED])
    assert rejected["order_status"] == "rejected"
    assert rejected["rejection_reason"] == "RMS:Margin Exceeds,Required:14250.00, Available:9120.55"
    assert not FILL_FIELDS & set(rejected)


def test_a_rejection_without_a_message_says_none_rather_than_guessing():
    (row,) = transform_order_data([{**REJECTED, "status_message": None}])
    assert row["rejection_reason"] == ""


def test_a_part_fill_reaches_the_order_book_without_its_fill():
    (row,) = transform_order_data([PART_FILLED])
    # Exactly the row upstream maps: no fill fields, and a working order's message is not a
    # reason, since nothing was refused.
    assert row == PART_FILLED_ROW


def _orderstatus(raw: dict) -> dict:
    """What /api/v1/orderstatus answers for one raw Upstox row, through the real services."""
    from services import orderstatus_service

    with (
        patch(
            "broker.upstox.api.order_api.get_order_book",
            return_value={"status": "success", "data": [dict(raw)]},
        ),
        patch("broker.upstox.mapping.order_data.get_symbol", return_value=raw["tradingsymbol"]),
        patch.object(orderstatus_service, "get_analyze_mode", return_value=False),
        patch("database.settings_db.get_analyze_mode", return_value=False),
        patch.object(orderstatus_service, "log_executor", MagicMock()),
    ):
        ok, answer, status = orderstatus_service.get_order_status_with_auth(
            {"orderid": raw["order_id"]}, "test-token", "upstox", {}
        )
    assert (ok, status) == (True, 200), answer
    return answer["data"]


def test_orderstatus_answers_a_part_fill_as_before():
    """The strategy module folds /orderstatus as a broker fact (order_events.apply_order_snapshot).

    /orderstatus replaces the average of any order that is not complete with 0.0, so with a
    filled quantity beside it a part fill would be recorded unpriced, and the priced push for the
    same fill then changes nothing: the leg would have no entry price, so no stop and no target.
    Without one, the reconciliation sees no fill and the priced push is the one applied.
    """
    raw = {**PART_FILLED, "instrument_token": "NSE_FO|49213", "product": "I"}
    assert _orderstatus(raw) == {**PART_FILLED_ROW, "average_price": 0.0}


#: A position partly booked today: bought 130 at 120, sold 65 at 140; 65 still held, last 130.
#: Its figures hold together: value = sell value - buy value (9,100 - 15,600), and the net pnl =
#: value + quantity x last price = realised 1,300 + unrealised 650.
POSITION = {
    "exchange": "NFO",
    "multiplier": 1.0,
    "value": -6500.0,
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


#: A position carried in from yesterday: 65 bought at 100, closed yesterday at 118.40, last 130,
#: nothing traded today. Upstox documents realised and unrealised as the DAY's P&L
#: (65 x (130 - 118.40) = 754) and pnl as the position's net (65 x (130 - 100) = 1,950).
CARRIED = {
    "exchange": "NFO",
    "multiplier": 1.0,
    "value": -6500.0,
    "pnl": 1950.0,
    "product": "D",
    "instrument_token": "NSE_FO|49213",
    "average_price": 100.0,
    "buy_value": 6500.0,
    "overnight_quantity": 65,
    "day_buy_value": 0.0,
    "day_buy_price": 0.0,
    "overnight_buy_amount": 6500.0,
    "overnight_buy_quantity": 65,
    "day_buy_quantity": 0,
    "day_sell_value": 0.0,
    "day_sell_price": 0.0,
    "overnight_sell_amount": 0.0,
    "overnight_sell_quantity": 0,
    "day_sell_quantity": 0,
    "quantity": 65,
    "last_price": 130.0,
    "unrealised": 754.0,
    "realised": 0.0,
    "sell_value": 0.0,
    "tradingsymbol": "NIFTY27OCT2625900CE",
    "close_price": 118.4,
    "buy_price": 100.0,
    "sell_price": 0.0,
}


def _adds_up(row: dict) -> bool:
    """The rule the phone's Positions cards apply before showing booked and open apart."""
    return abs(row["realised"] + row["unrealised"] - row["pnl"]) <= 1.0


def test_the_vectors_hold_together():
    for raw in (POSITION, CARRIED):
        assert raw["value"] == raw["sell_value"] - raw["buy_value"]
        assert raw["pnl"] == raw["value"] + raw["quantity"] * raw["last_price"] * raw["multiplier"]
    # Today's P&L on the part carried in counts from yesterday's close.
    assert CARRIED["unrealised"] == pytest.approx(65 * (130.0 - 118.4))


def test_a_carried_position_s_day_split_need_not_add_up_to_its_net_pnl():
    intraday, carried = transform_positions_data([POSITION, CARRIED])
    # Passed through as Upstox sends them, for the sheet to judge.
    assert (carried["realised"], carried["unrealised"], carried["pnl"]) == (0.0, 754.0, 1950.0)
    assert carried["overnight_quantity"] == 65
    # A position opened today adds up; the carried one does not, so its cards show the total alone.
    assert _adds_up(intraday)
    assert not _adds_up(carried)
