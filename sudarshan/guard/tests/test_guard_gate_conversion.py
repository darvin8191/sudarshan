"""The gate judges a position converted between products as the exits do (M5.13, X2).

A conversion in Upstox's app (MIS to NRML) moves quantity between the two rows with no order
behind it, so the gate corrects every row with the other products of its instrument
(sudarshan.books.fills), the same as the single exit and Exit all.
"""

from __future__ import annotations

from test_guard_gate import CE, TOKENS, Book

from sudarshan.guard import gate

TOKEN = TOKENS[(CE, "NFO")]
OWNER = "owner"


def _row(product, quantity, day_buy=0, day_sell=0):
    return {"instrument_token": TOKEN, "product": product, "quantity": quantity,
            "day_buy_quantity": day_buy, "day_sell_quantity": day_sell}  # fmt: skip


def _order(product, side, quantity):
    return {"instrument_token": TOKEN, "product": product, "transaction_type": side,
            "status": "complete", "filled_quantity": quantity, "quantity": quantity}  # fmt: skip


def _gate(risk_store, order_rows):
    book = Book()
    book.order_rows = order_rows
    return gate.Gate(store=risk_store, read=book)


def test_a_converted_position_is_corrected_with_its_other_product(risk_store):
    """Bought 65 MIS, converted to NRML: the D row carries a buy no D order explains. Corrected
    per product alone the gate could not judge it; across the instrument it reads 65 in D."""
    the_gate = _gate(risk_store, [_order("I", "BUY", 65)])
    rows = [_row("I", 0), _row("D", 65, day_buy=65)]
    corrected = the_gate._corrected(OWNER, rows, strict=True)
    assert [(each["product"], each["quantity"]) for each in corrected] == [("I", 0), ("D", 65)]


def test_a_conversion_whose_figures_do_not_add_up_is_not_judged_in_strict_mode(risk_store):
    """A sell the rows do not show yet, after a conversion, cannot be placed in a product."""
    the_gate = _gate(risk_store, [_order("I", "BUY", 65), _order("D", "SELL", 65)])
    rows = [_row("I", 0), _row("D", 65, day_buy=65)]
    assert the_gate._corrected(OWNER, rows, strict=True) is None
    assert the_gate._corrected(OWNER, rows) == rows  # not strict: as read
