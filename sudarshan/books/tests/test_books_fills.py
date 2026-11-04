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


ORDER_FIELDS = {
    "order_id", "exchange_order_id", "instrument_token", "exchange", "tradingsymbol", "product",
    "transaction_type", "order_type", "status", "status_message", "quantity", "filled_quantity",
    "pending_quantity", "price", "trigger_price", "average_price", "validity", "is_amo",
    "order_timestamp",
}  # fmt: skip


def test_every_positions_row_is_one_upstox_could_show():
    """quantity = overnight + day buys - day sells, as of the moment the row was taken (L5)."""
    rows = [row for case in VECTORS for row in case["positions"]]
    assert rows
    for row in rows:
        assert row["quantity"] == (
            row["overnight_quantity"] + row["day_buy_quantity"] - row["day_sell_quantity"]
        ), row


def test_every_order_row_carries_upstox_s_raw_fields():
    orders = [order for case in VECTORS for order in case["orders"] or []]
    assert orders
    for order in orders:
        assert ORDER_FIELDS <= set(order), (ORDER_FIELDS - set(order), order)


def test_the_vectors_cover_the_shapes_the_review_asked_for():
    names = " | ".join(case["name"] for case in VECTORS)
    for shape in ("carried overnight", "CNC", "short", "MCX", "after-market", "converted"):
        assert shape in names, shape
    products = {row["product"] for case in VECTORS for row in case["positions"]}
    assert products == {"I", "D"}
    assert any(row["overnight_quantity"] for case in VECTORS for row in case["positions"])


def _row(**fields):
    return {"instrument_token": "T", "product": "I", "quantity": 65, "day_buy_quantity": 0,
            "day_sell_quantity": 0, **fields}  # fmt: skip


def _order(**fields):
    return {"instrument_token": "T", "product": "I", "transaction_type": "SELL",
            "filled_quantity": 0, **fields}  # fmt: skip


@pytest.mark.parametrize(
    "positions,orders",
    [
        ([{k: v for k, v in _row().items() if k != "day_buy_quantity"}], []),
        ([{k: v for k, v in _row().items() if k != "day_sell_quantity"}], []),
        ([_row(day_buy_quantity="x")], []),
        ([_row(day_sell_quantity=-1)], []),
        ([_row(quantity=None)], []),
        ([_row(product="")], []),
        ([_row(), _row()], []),  # two rows for one product
        ([_row()], [{k: v for k, v in _order().items() if k != "filled_quantity"}]),
        ([_row()], [_order(transaction_type="")]),
        ([_row()], [_order(product="")]),
    ],
    ids=["no day buys", "no day sells", "day buys not a number", "negative day sells",
         "no quantity", "no product", "two rows", "order without filled quantity",
         "order without side", "order without product"],
)  # fmt: skip
def test_malformed_rows_cannot_be_worked_out(positions, orders):
    assert fills.held_by_product(positions, orders, "T") is None


def test_quantities_sent_as_text_are_read():
    row = _row(quantity="130")
    order = _order(filled_quantity="65")
    assert fills.held_by_product([row], [order], "T") == {"I": 65}


def test_an_order_of_another_instrument_without_a_filled_quantity_is_ignored():
    order = {"instrument_token": "OTHER", "product": "I", "transaction_type": "SELL"}
    assert fills.held_by_product([_row()], [order], "T") == {"I": 65}


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
