"""Dividends (dividends.json, T8): the quantity a dividend is paid on, the expected amount, a
year by payer and the likely-TDS flag."""

from __future__ import annotations

import pytest
from tax_vector_tools import cases

from sudarshan.tax import dividends, rules


@pytest.mark.parametrize("case", cases("dividends.json", "entitled"))
def test_the_quantity_held_the_day_before_the_ex_date(case):
    changes = [tuple(change) for change in case["in"]["changes"]]
    assert dividends.entitled_qty_u(changes, case["in"]["ex_day"]) == case["out"]


@pytest.mark.parametrize("case", cases("dividends.json", "expected"))
def test_the_expected_amount(case):
    assert dividends.expected_paise(**case["in"]) == case["out"]


@pytest.mark.parametrize("case", cases("dividends.json", "year"))
def test_a_year_by_payer(case):
    got = dividends.year(case["in"]["lines"], case["in"]["fy"])
    for payer in got["payers"]:
        assert payer.pop("name") is None
        payer["lines"] = len(payer["lines"])
    assert got == case["out"]


def test_the_block_of_the_working_paper_adds_a_buybacks_receipt_and_drops_the_lines():
    lines = [{"isin": "INE000A01010", "fy": 2026, "state": "expected", "expected_paise": 165000}]
    of_year = dividends.year(lines, 2026, {"INE000A01010": "A Ltd"})
    got = dividends.block(of_year, buyback_paise=6_000_000)
    assert got == {
        "by_payer": [{"isin": "INE000A01010", "name": "A Ltd", "expected_paise": 165000,
                      "confirmed_paise": 0, "total_paise": 165000, "tds_likely": False}],
        "buyback_paise": 6_000_000, "total_paise": 6_165_000, "tds_expected_paise": 0,
        "unconfirmed_count": 1,
    }  # fmt: skip
    assert dividends.block(dividends.year([], 2026))["total_paise"] == 0


def test_the_threshold_is_a_dated_row_marked_to_verify():
    assert rules.at(rules.DIVIDEND_TDS, 20250331)["threshold"] == 500_000
    assert rules.at(rules.DIVIDEND_TDS, 20250401)["threshold"] == 1_000_000
    assert rules.row_at(rules.DIVIDEND_TDS, 20260401).verify
