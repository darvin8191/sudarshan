"""Heads (classify.json) and the dated rule rows at their boundaries (T1)."""

from __future__ import annotations

import pytest
from tax_vector_tools import cases

from sudarshan.tax import classify, rules, units
from sudarshan.tax.model import Trade, build


@pytest.mark.parametrize("case", cases("classify.json", "head_of"))
def test_head_of(case):
    assert classify.head_of(build(Trade, case["in"])) == case["out"]


@pytest.mark.parametrize("case", cases("classify.json", "intraday_qty"))
def test_intraday_qty(case):
    assert classify.intraday_qty(**case["in"]) == case["out"]


@pytest.mark.parametrize("case", cases("classify.json", "head_of_product"))
def test_head_of_product(case):
    assert classify.head_of_product(**case["in"]) == case["out"]


def test_a_contract_is_an_option_or_a_future():
    assert classify.kind_of_contract(("NFO", "NIFTY", 20261126, 245000000, "PE")) == "options"
    assert classify.kind_of_contract(("NFO", "NIFTY", 20261126, 0, "FUT")) == "futures"


#: (table, day, value): each rate on the last day of the old row and the first of the new.
BOUNDARIES = [
    (rules.STCG_RATE, 20240722, 1500),
    (rules.STCG_RATE, 20240723, 2000),
    (rules.LTCG_RATE, 20180331, 0),
    (rules.LTCG_RATE, 20180401, 1000),
    (rules.LTCG_RATE, 20240722, 1000),
    (rules.LTCG_RATE, 20240723, 1250),
    (rules.LTCG_EXEMPT, 20230401, 10_000_000),
    (rules.LTCG_EXEMPT, 20240401, 12_500_000),
    (rules.LTCG_EXEMPT, 20260401, 12_500_000),
    (rules.BUYBACK, 20240930, "exempt"),
    (rules.BUYBACK, 20241001, "dividend_and_loss"),
    (rules.BUYBACK, 20260331, "dividend_and_loss"),
    (rules.BUYBACK, 20260401, "capital_gain"),
    (rules.ACT, 20260331, "1961"),
    (rules.ACT, 20260401, "2025"),
    (rules.CESS_BP, 20260401, 400),
    (rules.LT_MONTHS, 20260401, 12),
]


@pytest.mark.parametrize(("table", "day", "value"), BOUNDARIES)
def test_the_row_in_force(table, day, value):
    assert rules.at(table, day) == value


def test_tables_are_in_date_order_with_a_source_and_a_check_date():
    tables = [value for value in vars(rules).values() if isinstance(value, tuple) and value]
    tables = [table for table in tables if isinstance(table[0], rules.Row)]
    assert len(tables) >= 14
    for table in tables:
        assert [row.from_day for row in table] == sorted({row.from_day for row in table})
        assert all(row.ref and row.checked == rules.CHECKED for row in table)


def test_a_day_before_a_table_starts_has_no_row():
    with pytest.raises(LookupError):
        rules.at(rules.LTCG_EXEMPT, 20180331)


def test_an_open_rule_offers_its_other_reading_and_nothing_else():
    assert rules.choice(rules.EXEMPT_ORDER, 20240401) == "highest_rate_first"
    assert rules.choice(rules.EXEMPT_ORDER, 20240401, "chronological") == "chronological"
    assert rules.choice(rules.EXEMPT_ORDER, 20240401, "anything") == "highest_rate_first"
    assert rules.choice(rules.TURNOVER_METHOD, 20260401, "abs_pnl_plus_sale_premium") != "abs_pnl"
    assert rules.choice(rules.LT_BOUNDARY, 20260401, "on_anniversary") == "on_anniversary"
    for table in (rules.EXEMPT_ORDER, rules.TURNOVER_METHOD, rules.LT_BOUNDARY):
        assert table[0].verify and table[0].alternatives


def test_units_round_half_away_and_share_exactly():
    assert [units.round_div(n, 2) for n in (1, -1, 3, -3)] == [1, -1, 2, -2]
    assert units.value_paise(65_000_000, 1_000_000) == 650_000  # 65 x Rs 100
    assert [units.share_of(10_000, 1, 3), units.share_of(6_667, 1, 2)] == [3_333, 3_334]
    # 15,320 by 4,000 : 6,000 : 5,000 = 4,085.33 / 6,128 / 5,106.67: the spare paisa to the .67
    assert units.allocate(15_320, [4_000, 6_000, 5_000]) == [4_085, 6_128, 5_107]
    assert units.allocate(10, [1, 1, 1]) == [4, 3, 3] and units.allocate(7, [0, 0]) == [0, 0]
    assert [units.add_years(20240229), units.add_years(20230228)] == [20250228, 20240228]
    assert [units.fy_of(20260331), units.fy_of(20260401)] == [2025, 2026]
    assert (units.fy_first(2026), units.fy_last(2026)) == (20260401, 20270331)
    assert units.fy_label(2026) == "FY 2026-27" and units.fy_label(2099) == "FY 2099-00"
    assert units.day_text(20260803) == "3 Aug 2026"
    assert units.rupees(15_500_000) == "Rs 1,55,000" and units.rupees(-123_456) == "-Rs 1,234.56"
