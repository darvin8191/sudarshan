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
    assert income_summary(ledger, case["valuation_day"]) == case["expect"]


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_the_twelve_months_add_up_to_the_months_the_report_lists(case):
    ledger = ledger_of(case["entries"])
    report = income_report(ledger, case["valuation_day"])
    got = income_summary(ledger, case["valuation_day"])
    assert got["last_12m_paise"] == sum(m["paise"] for m in report["by_month"])
    assert len(report["by_month"]) == 12


def test_the_vectors_cover_the_shapes_that_matter():
    ids = {case["id"] for case in CASES}
    assert {"Y1", "Y2", "Y3", "Y4", "Y5", "Y6", "Y7", "Y8", "Y9", "Y10", "Y11", "Y12"} <= ids
    assert {"Y5c", "Y13", "Y14", "Y15", "Y16", "Y17"} <= ids
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
    rows = [
        {"id": 1, "seq": 1, "kind": "BUY", "trade_date": 20260101, "account_id": 1,
         "asset_id": 1, "qty_u": 1_000_000, "amount_paise": 3},
        {"id": 2, "seq": 2, "kind": "DIVIDEND", "trade_date": 20260601, "account_id": 1,
         "asset_id": 1, "amount_paise": 1},
    ]  # fmt: skip
    # 1 paise on 3 paise: 33.3333...% -> "33.3333"; 2 on 3 -> "66.6667"
    assert income_summary(ledger_of(rows), 20261027)["yield_on_cost_pct"] == "33.3333"
    rows[1]["amount_paise"] = 2
    assert income_summary(ledger_of(rows), 20261027)["yield_on_cost_pct"] == "66.6667"


def test_the_yield_has_the_review_case_as_a_vector():
    """The case that was wrong: buy 100, Rs 5 a share, sell 90 is 5%, not 50%."""
    y8 = next(case for case in CASES if case["id"] == "Y8")
    assert y8["expect"]["yield_on_cost_pct"] == "5.0000"
    assert y8["expect"]["held_income_paise"] == 5_000 and y8["expect"]["held_cost_paise"] == 100_000


def test_the_summary_reads_no_clock_and_no_io():
    import inspect

    from sudarshan.portfolio.core import performance

    source = inspect.getsource(performance.income_summary)
    for word in ("time.", "datetime", "open(", "log", "import"):
        assert word not in source, word


# ---- through the report: the cost is the lots', not the value and not the tab's income ----------
NOW = 1_793_000_000_000  # 26 Oct 2026, mid-morning in India


@pytest.fixture
def world(tmp_path):
    from sudarshan.portfolio.book import Book
    from sudarshan.portfolio.store import DB_FILENAME, PortfolioStore, Scope
    from sudarshan.portfolio.views import Views

    owner = Scope.owner()
    store = PortfolioStore(tmp_path / "s" / DB_FILENAME, now=NOW)
    book = Book(store)
    me = book.members(owner)[0]

    class World:
        views, scope = Views(store), owner
        account = book.account_create(owner, "a-1", me["id"], "Zerodha", "demat", now=NOW)
        tab = book.tab_create(owner, "t-1", "Long term", "mixed", now=NOW)
        gold = book.asset_create(
            owner, "g-1", "manual", now=NOW, name="Gold coin", asset_class="gold",
            unit_label="g", first_value={"day": 20261001, "price_u": 600_000_000},
        )  # fmt: skip
        n = 0

        @classmethod
        def add(cls, **entry):
            cls.n += 1
            version = next(t for t in book.tabs(owner) if t["id"] == cls.tab["id"])["version"]
            return book.activity_create(
                owner, cls.tab["id"], version, {"account_id": cls.account["id"], **entry},
                f"e-{cls.n}", now=NOW,
            )  # fmt: skip

        @classmethod
        def income(cls):
            return cls.views.performance(owner, cls.tab["id"], now=NOW)["income"]

    from sudarshan.portfolio import store as store_module

    yield World
    store.close()
    store_module.stop()


def test_the_report_yield_after_a_part_sale_is_on_what_is_held_not_on_its_value(world):
    """Buy 10 g of gold for 10,000.00 which is now worth 60,000.00; a payment of 500.00 on all ten;
    sell 9. One gram is left costing 1,000.00 with 50.00 of the payment: 5.0000%. Divided by the
    value (6,000.00) it would be 0.8333%, and by the whole income (500.00) 50.0000%."""
    gold = world.gold["id"]
    world.add(kind="BUY", trade_date=20260110, asset_id=gold, qty_u=10_000_000,
              amount_paise=1_000_000)  # fmt: skip
    world.add(kind="DIVIDEND", trade_date=20260814, asset_id=gold, amount_paise=50_000)
    world.add(kind="SELL", trade_date=20261001, asset_id=gold, qty_u=9_000_000,
              amount_paise=900_000)  # fmt: skip
    got = world.income()
    assert got["yield_on_cost_pct"] == "5.0000"
    assert (got["held_income_paise"], got["held_cost_paise"]) == (5_000, 100_000)
    assert got["last_12m_paise"] == 50_000  # all that was received is still said


def test_the_report_yield_of_a_sold_payer_is_not_given_to_what_was_bought_after(world):
    gold = world.gold["id"]
    world.add(kind="BUY", trade_date=20260110, asset_id=gold, qty_u=10_000_000,
              amount_paise=1_000_000)  # fmt: skip
    world.add(kind="DIVIDEND", trade_date=20260814, asset_id=gold, amount_paise=80_000)
    world.add(kind="SELL", trade_date=20261001, asset_id=gold, qty_u=10_000_000,
              amount_paise=1_050_000)  # fmt: skip
    assert world.income()["yield_on_cost_pct"] is None  # nothing held at a cost
    world.add(kind="BUY", trade_date=20261002, asset_id=gold, qty_u=10_000_000,
              amount_paise=100_000)  # fmt: skip
    assert world.income()["yield_on_cost_pct"] is None  # the gold has earned nothing yet
