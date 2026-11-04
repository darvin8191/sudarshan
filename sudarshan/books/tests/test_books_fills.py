"""What the positions book does not show yet, from the order book's fills (M6.9, N2 and N4).

The golden vectors in fill_vectors.json are raw Upstox rows (a short-term positions row and the
day's order book rows) with the figures sudarshan.books.fills must work out from them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sudarshan.books import fills

VECTORS = json.loads((Path(__file__).parent / "fill_vectors.json").read_text())["cases"]


@pytest.mark.parametrize("case", VECTORS, ids=[case["name"] for case in VECTORS])
def test_golden_vectors(case):
    assert fills.held_by_product(case["positions"], case["orders"], case["token"]) == case["held"]


@pytest.mark.parametrize("case", VECTORS, ids=[case["name"] for case in VECTORS])
def test_held_for_and_held_each_agree_with_the_vectors(case):
    held = case["held"]
    for product in ("I", "D"):
        expected = None if held is None else held.get(product, 0)
        assert (
            fills.held_for(case["positions"], case["orders"], case["token"], product) == expected
        )
    each = fills.held_each(case["positions"], case["orders"])
    expected_each = [
        None if held is None else held.get(row["product"]) for row in case["positions"]
    ]
    assert each == expected_each


def test_no_clock_is_involved(monkeypatch):
    """The figure depends on the rows alone, however long the positions book has lagged."""
    import time

    case = VECTORS[1]
    monkeypatch.setattr(time, "monotonic", lambda: 1e9)
    assert fills.held_by_product(case["positions"], case["orders"], case["token"]) == case["held"]


def test_the_module_adds_no_field_to_what_the_api_returns():
    """It reads Upstox's raw rows inside the broker module and returns plain numbers."""
    case = VECTORS[1]
    before = json.dumps(case, sort_keys=True)
    fills.held_by_product(case["positions"], case["orders"], case["token"])
    assert json.dumps(case, sort_keys=True) == before


def test_a_single_product_whose_row_shows_more_than_the_order_book_is_never_nets_compared():
    """Nothing was converted with one product, so a row that shows more than the order book does
    is a book that does not add up, even when the nets happen to agree."""
    row = {"instrument_token": "T", "product": "I", "quantity": 55, "day_buy_quantity": 65,
           "day_sell_quantity": 10}  # fmt: skip
    orders = [
        {"instrument_token": "T", "product": "I", "transaction_type": "BUY", "filled_quantity": 60},
        {"instrument_token": "T", "product": "I", "transaction_type": "SELL", "filled_quantity": 5},
    ]
    assert fills.held_by_product([row], orders, "T") is None
