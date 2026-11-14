"""One disposal against capgains.json and buyback.json (T3)."""

from __future__ import annotations

import pytest
from tax_vector_tools import cases, plain

from sudarshan.portfolio.core.days import held_over_year
from sudarshan.tax import capgains, lots
from sudarshan.tax.model import Disposal, Settings, Trade, build

ALL = cases("capgains.json", "gain") + cases("capgains.json", "term") + cases("buyback.json")


def gain_of(given: dict):
    settings = Settings(**given["settings"]) if "settings" in given else None
    return capgains.gain_of(
        build(Disposal, given["disposal"]), given["fmv"], settings, given.get("expenses_paise")
    )


@pytest.mark.parametrize("case", ALL)
def test_gain_of(case):
    gain = plain(gain_of(case["in"]))
    assert {key: gain[key] for key in case["out"]} == case["out"]


@pytest.mark.parametrize("case", ALL)
def test_what_is_left_out_has_no_gain_and_what_is_in_adds_up(case):
    gain = gain_of(case["in"])
    if gain.status != "ok":
        assert gain.gain_paise is None
    else:
        assert gain.gain_paise == gain.sale_paise - gain.cost_paise - gain.expenses_paise
        assert gain.sale_paise + gain.dividend_paise == gain.receipt_paise


@pytest.mark.parametrize("case", cases("capgains.json", "term"))
def test_the_default_line_is_the_portfolio_screen_s_line(case):
    """One vector, two readers: the tax term and the holdings filter's "held over a year"."""
    if "settings" not in case["in"]:
        row = case["in"]["disposal"]
        over = held_over_year(row["acquired_day"], row["sale_day"])
        assert over == (case["out"]["term"] == "long")
        assert capgains.term_of(row["acquired_day"], row["sale_day"]) == case["out"]["term"]


def test_stt_never_enters_a_cost_or_an_expense():
    trades = [
        Trade(1, 1, 20250602, 1, "BUY", "EQ", "X", 10**7, 10**6, 100_000, "ledger", 500, 999),
        Trade(1, 1, 20260812, 2, "SELL", "EQ", "X", 10**7, 10**6, 120_000, "ledger", 300, 777),
    ]
    gain = capgains.gain_of(lots.replay(trades).disposals[0], {})
    assert (gain.cost_paise, gain.expenses_paise, gain.gain_paise) == (100_500, 300, 19_200)
