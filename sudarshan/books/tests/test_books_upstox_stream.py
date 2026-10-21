"""A rejection pushed by Upstox's order stream carries Upstox's own reason, or none (M6.9).

The stream adapter (`broker/upstox/streaming/upstox_order_adapter.py`, the SUDARSHAN block)
used to fall back to the status word when Upstox sent no status_message, so the phone's order
sheet and its alerts said "Broker says: rejected": a word that reads as a reason and says
nothing. With no words of Upstox's own, the reason is empty and the screens say none was given.
"""

from __future__ import annotations

import json

import pytest

from broker.upstox.streaming.upstox_order_adapter import UpstoxOrderUpdateAdapter


def _rejected(**fields) -> dict:
    frame = {
        "update_type": "order",
        "order_id": "251020000301196",
        "trading_symbol": "BANKNIFTY27OCT2658000CE",
        "exchange": "NFO",
        "transaction_type": "BUY",
        "quantity": 30,
        "price": 310,
        "trigger_price": 305,
        "order_type": "SL",
        "product": "I",
        "status": "rejected",
        "filled_quantity": 0,
        "pending_quantity": 0,
        "average_price": 0,
    }
    frame.update(fields)
    return UpstoxOrderUpdateAdapter(user_id="u", access_token="t").normalize(json.dumps(frame))


def test_a_rejection_carries_upstox_s_own_words():
    pushed = _rejected(status_message=" RMS:Margin Exceeds,Required:14250.00, Available:9120.55 ")
    assert pushed["order_status"] == "rejected"
    assert pushed["rejection_reason"] == "RMS:Margin Exceeds,Required:14250.00, Available:9120.55"


@pytest.mark.parametrize("message", [None, "", "  ", "rejected", "REJECTED", "Rejected "])
def test_a_rejection_without_words_of_upstox_s_own_has_no_reason(message):
    fields = {} if message is None else {"status_message": message}
    assert _rejected(**fields)["rejection_reason"] == ""


def test_only_a_rejection_carries_a_reason():
    assert _rejected(status="complete", status_message="done")["rejection_reason"] == ""
