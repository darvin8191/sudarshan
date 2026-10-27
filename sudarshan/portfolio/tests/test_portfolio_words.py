"""core/words.py (PA1): the owner's words for dates, money, quantities and the ledger's refusals."""

from __future__ import annotations

import pytest

from sudarshan.portfolio.core import kinds, words
from sudarshan.portfolio.core.ledger import replay
from sudarshan.portfolio.core.model import FIELDS, INT_FIELDS, Entry
from sudarshan.portfolio.core.words import Names

RUPEE = chr(0x20B9)
DOT = " " + chr(0xB7) + " "


@pytest.mark.parametrize(
    ("number", "text"),
    [
        (0, "0"),
        (7, "7"),
        (999, "999"),
        (1000, "1,000"),
        (12345, "12,345"),
        (123456, "1,23,456"),
        (1428250, "14,28,250"),
        (12345678, "1,23,45,678"),
        (1000000000, "1,00,00,00,000"),
        (-1428250, "-14,28,250"),
    ],
)
def test_digits_are_grouped_the_indian_way(number, text):
    assert words.group_in(number) == text


def test_dates_are_said_as_the_owner_says_them():
    assert words.date(20250312) == "12 Mar 2025"
    assert words.date(20250109) == "9 Jan 2025"
    assert words.date(20261231) == "31 Dec 2026"


@pytest.mark.parametrize(
    ("paise", "text"),
    [
        (142825, RUPEE + "1,428.25"),
        (1430250, RUPEE + "14,302.50"),
        (2000, RUPEE + "20.00"),
        (5, RUPEE + "0.05"),
        (0, RUPEE + "0.00"),
        (-50000, "-" + RUPEE + "500.00"),
        (12345678900, RUPEE + "12,34,56,789.00"),
    ],
)
def test_money_is_said_in_rupees(paise, text):
    assert words.rupees(paise) == text


@pytest.mark.parametrize(
    ("price_u", "text"),
    [
        (14_282_500, RUPEE + "1,428.25"),
        (15_100_000, RUPEE + "1,510.00"),
        (456_789, RUPEE + "45.6789"),
        (456_790, RUPEE + "45.679"),
        (333_300, RUPEE + "33.33"),
        (500, RUPEE + "0.05"),
        (1, RUPEE + "0.0001"),
    ],
)
def test_a_price_has_two_decimals_or_the_four_it_needs(price_u, text):
    assert words.price(price_u) == text


@pytest.mark.parametrize(
    ("qty_u", "text"),
    [
        (10_000_000, "10"),
        (109_459_000, "109.459"),
        (1_500_000, "1.5"),
        (1, "0.000001"),
        (100_000_000_000, "1,00,000"),
    ],
)
def test_a_quantity_has_the_decimals_it_needs(qty_u, text):
    assert words.quantity(qty_u) == text


NAMES = Names(accounts={1: "Zerodha"}, assets={1: "RELIANCE"})


def test_the_refusal_sentence_is_the_designs_word_for_word():
    text = words.short_of_units(NAMES, "SELL", 1, 1, 20250312, 10_000_000, 12_000_000)
    assert text == (
        "Zerodha holds 10 RELIANCE on 12 Mar 2025, so a sale of 12 cannot be saved. "
        "Add the missing purchase first."
    )
    nothing = words.short_of_units(NAMES, "SELL", 1, 1, 20250312, 0, 5_000_000)
    assert "holds no RELIANCE" in nothing
    assert "a transfer of 5 cannot" in words.short_of_units(
        NAMES, "TRANSFER", 1, 1, 20250312, 0, 5_000_000
    )


def test_names_the_owner_has_not_given_still_make_a_sentence():
    text = words.short_of_units(Names(), "SELL", 3, 4, 20250312, 1_000_000, 2_000_000)
    assert text.startswith("The account holds 1 this holding on 12 Mar 2025")
    assert Names().unit(1) == "shares"
    assert Names(units={1: "grams"}).unit(1) == "grams"


def test_a_knock_on_refusal_names_what_it_would_break():
    assert words.knock_on("delete", "BUY", "SELL", 20250312, "shares") == (
        "Deleting this purchase would leave the sale on 12 Mar 2025 without shares. "
        "Delete or change that sale first."
    )
    assert words.knock_on("change", "BUY", "SELL", 20250312, "units") == (
        "Changing this purchase would leave the sale on 12 Mar 2025 without units. "
        "Change or delete that sale first."
    )
    assert words.knock_on("add", "SELL", "SELL", 20250315, "shares").startswith(
        "Saving this sale would leave the sale on 15 Mar 2025 without shares."
    )


def test_every_kind_has_a_noun_and_every_field_a_label():
    assert set(words.NOUNS) == set(kinds.ACTIVITY_KINDS)
    assert set(FIELDS) == set(kinds.ACTIVITY_KINDS)
    named = {name for needs, may in FIELDS.values() for name in (*needs, *may)}
    assert named <= set(words.LABELS)
    assert set(INT_FIELDS[2:]) <= set(words.LABELS)


def _move(rows, entry_id):
    ledger = replay([Entry.of(row) for row in rows], names=NAMES)
    return next(move for move in ledger.journal if move.entry.id == entry_id)


def test_a_loss_and_a_flat_sale_are_said_as_such():
    buy = {"id": 1, "seq": 1, "kind": "BUY", "trade_date": 20250106, "account_id": 1}
    buy |= {"asset_id": 1, "qty_u": 10_000_000, "amount_paise": 100000}
    sell = {"id": 2, "seq": 2, "kind": "SELL", "trade_date": 20250210, "account_id": 1}
    sell |= {"asset_id": 1, "qty_u": 10_000_000}
    loss = words.describe(_move([buy, sell | {"amount_paise": 90000}], 2))
    assert loss == f"From 6 Jan 2025: 10 at {RUPEE}100.00{DOT}loss {RUPEE}100.00"
    flat = words.describe(_move([buy, sell | {"amount_paise": 100000}], 2))
    assert flat.endswith(f"{DOT}no gain or loss")


def test_an_income_a_fee_and_a_purchase_without_charges_are_said_plainly():
    base = {"seq": 1, "trade_date": 20250601, "account_id": 1}
    dividend = base | {"id": 1, "kind": "DIVIDEND", "asset_id": 1, "amount_paise": 1000}
    assert words.describe(_move([dividend], 1)) == f"Income {RUPEE}10.00"
    taxed = dividend | {"tds_paise": 100}
    assert words.describe(_move([taxed], 1)) == f"Income {RUPEE}10.00 before {RUPEE}1.00 TDS"
    fee = base | {"id": 1, "kind": "FEE", "amount_paise": 300}
    assert words.describe(_move([fee], 1)) == f"Fee {RUPEE}3.00"
    buy = base | {"id": 1, "kind": "BUY", "asset_id": 1, "qty_u": 1_000_000, "amount_paise": 5000}
    assert words.describe(_move([buy], 1)) == f"Cost {RUPEE}50.00"
