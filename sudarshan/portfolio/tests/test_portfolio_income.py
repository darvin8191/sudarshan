"""Yield on cost and the income by financial year (M7.29; core/performance.income_summary), pinned
by income.json (make_pn1.py, worked out by hand) and held to the words the owner reads."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from test_portfolio_performance import DECIMALS, ledger_of

from sudarshan.portfolio.core.performance import income_report, income_summary

FILE = json.loads((Path(__file__).parent / "vectors" / "income.json").read_text(encoding="utf-8"))
CASES = FILE["cases"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_the_income_summary_is_the_vectors(case):
    ledger = ledger_of(case["entries"])
    assert income_summary(ledger, case["valuation_day"], case["cost_paise"]) == case["expect"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_the_twelve_months_add_up_to_the_months_the_report_lists(case):
    ledger = ledger_of(case["entries"])
    report = income_report(ledger, case["valuation_day"])
    got = income_summary(ledger, case["valuation_day"], case["cost_paise"])
    assert got["last_12m_paise"] == sum(m["paise"] for m in report["by_month"])
    assert len(report["by_month"]) == 12


def test_the_vectors_cover_the_shapes_that_matter():
    ids = {case["id"] for case in CASES}
    assert {"Y1", "Y2", "Y3", "Y4", "Y5", "Y6", "Y7"} <= ids
    expects = [case["expect"] for case in CASES]
    assert any(e["yield_on_cost_pct"] is None for e in expects)  # nothing held at a cost
    assert any(e["by_fy"] == [] for e in expects)  # no income at all
    assert any(len(e["by_fy"]) == 2 for e in expects)  # two years
    for e in expects:
        pct = e["yield_on_cost_pct"]
        assert pct is None or re.fullmatch(r"-?\d+\.\d{4}", pct), pct
        assert pct != "-0.0000"
        for row in e["by_fy"]:
            assert re.fullmatch(r"FY \d{4}-\d{2}", row["label"])
            assert row["paise"] != 0


def test_a_yield_is_never_computed_from_a_float():
    ledger = ledger_of(
        [
            {"id": 1, "seq": 1, "kind": "DIVIDEND", "trade_date": 20260601, "account_id": 1,
             "asset_id": 1, "amount_paise": 1},
        ]
    )  # fmt: skip
    # 1 paise on 3 paise: 33.3333...% -> "33.3333"; 2 on 3 -> "66.6667"
    assert income_summary(ledger, 20261027, 3)["yield_on_cost_pct"] == "33.3333"
    assert (
        income_summary(ledger, 20261027, -5)["yield_on_cost_pct"] is None
    )  # never a negative cost


def test_the_summary_reads_no_clock_and_no_io():
    import inspect

    from sudarshan.portfolio.core import performance

    source = inspect.getsource(performance.income_summary)
    for word in ("time.", "datetime", "open(", "log", "import"):
        assert word not in source, word
