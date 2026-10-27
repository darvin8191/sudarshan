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


def test_a_split_and_a_bonus_are_said_in_the_owners_words():
    buy = {"id": 1, "seq": 1, "kind": "BUY", "trade_date": 20250106, "account_id": 1}
    buy |= {"asset_id": 1, "qty_u": 10_000_000, "amount_paise": 100000}
    day = {"seq": 2, "trade_date": 20250310, "asset_id": 1}
    each = day | {"id": 2, "kind": "SPLIT", "ratio_num": 5, "ratio_den": 1}
    assert words.describe(_move([buy, each], 2)) == (
        f"Each share becomes 5{DOT}10 become 50{DOT}cost and dates kept"
    )
    part = day | {"id": 2, "kind": "SPLIT", "account_id": 1, "ratio_num": 1, "ratio_den": 10}
    fund = buy | {"qty_u": 15_000_000}
    with_decimals = replay([Entry.of(r) for r in [fund, part]], names=NAMES, decimals={1: 4})
    assert words.describe(with_decimals.journal[-1]) == (
        f"1 new for every 10 old{DOT}15 become 1.5{DOT}cost and dates kept"
    )
    bonus = day | {"id": 2, "kind": "BONUS", "account_id": 1, "qty_u": 2_500_000}
    assert words.describe(_move([buy, bonus], 2)) == "Bonus 2.5 at no cost"


def test_shares_moved_are_said_with_their_lots_and_shares_from_outside_with_their_cost():
    buy = {"id": 1, "seq": 1, "kind": "BUY", "trade_date": 20240105, "account_id": 1}
    buy |= {"asset_id": 1, "qty_u": 10_000_000, "amount_paise": 100000}
    move = {"id": 2, "seq": 2, "kind": "TRANSFER", "trade_date": 20250601, "asset_id": 1}
    move |= {"account_id": 1, "qty_u": 4_000_000}
    between = words.describe(_move([buy, move | {"to_account_id": 2}], 2))
    assert between == f"From 5 Jan 2024: 4 at {RUPEE}100.00{DOT}cost and dates kept"
    leaving = words.describe(_move([buy, move], 2))
    assert leaving == f"From 5 Jan 2024: 4 at {RUPEE}100.00{DOT}left at cost, no gain or loss"
    coming = move | {"account_id": None, "to_account_id": 1, "amount_paise": 40000}
    assert words.describe(_move([coming], 2)) == f"Cost {RUPEE}400.00"
    # The day it was first bought and a value equal to the cost say nothing new.
    same = coming | {"lot_date": 20250601, "value_paise": 40000}
    assert words.describe(_move([same], 2)) == f"Cost {RUPEE}400.00"
    told = coming | {"lot_date": 20230815, "value_paise": 55000}
    assert words.describe(_move([told], 2)) == (
        f"Cost {RUPEE}400.00{DOT}bought 15 Aug 2023{DOT}valued at {RUPEE}550.00"
    )


def test_a_split_or_bonus_on_nothing_held_says_who_holds_nothing():
    assert words.not_held(NAMES, "SPLIT", 1, 1, 20250312) == (
        "Zerodha holds no RELIANCE on 12 Mar 2025, so a split cannot be saved. "
        "Check the date, or add the missing purchase first."
    )
    assert words.not_held(NAMES, "SPLIT", None, 1, 20250312) == (
        "No account holds RELIANCE on 12 Mar 2025, so a split cannot be saved. "
        "Check the date, or add the missing purchase first."
    )
    assert words.not_held(Names(), "BONUS", 3, 4, 20250312).startswith(
        "The account holds no this holding on 12 Mar 2025, so a bonus cannot be saved."
    )


def test_a_split_that_would_leave_a_fraction_says_how_much_and_where():
    assert words.fraction_left(NAMES, 1, 1, 1_500_000) == (
        "This split would leave 1.5 shares in Zerodha. "
        "Record the sale of the fraction first, then the split."
    )
    grams = Names(accounts={1: "Locker"}, units={4: "grams"})
    assert words.fraction_left(grams, 1, 4, 12_340_000).startswith(
        "This split would leave 12.34 grams in Locker."
    )
