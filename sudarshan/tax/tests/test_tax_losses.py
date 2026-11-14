"""The business buckets after set-off, and what is carried, against losses.json (T6)."""

from __future__ import annotations

import pytest
from tax_vector_tools import cases

from sudarshan.tax import losses
from sudarshan.tax.model import Carry, build


def setoff(given: dict) -> dict:
    carries = [build(Carry, row) for row in given["carries"]]
    return losses.business_setoff(
        given["non_speculative_net"], given["speculative_net"], carries, given["fy"]
    )


@pytest.mark.parametrize("case", cases("losses.json"))
def test_business_setoff(case):
    got, out = setoff(case["in"]), case["out"]
    for key in ("taxable", "available_other_heads_paise", "carried", "lapsed", "own_figures"):
        assert got[key] == out[key], key
    said = losses.sentences(got, case["in"]["fy"])
    assert all(sentence in said for sentence in out["says"]), said
    assert "section" not in " ".join(said).lower()


@pytest.mark.parametrize("case", cases("losses.json"))
def test_nothing_is_made_or_lost(case):
    """Profit = taxable + what losses took; each loss = what it gave + what is carried or lapsed."""
    given, got = case["in"], setoff(case["in"])
    profit = max(given["non_speculative_net"], 0) + max(given["speculative_net"], 0)
    used = sum(step["paise"] for step in got["steps"])
    assert profit == sum(got["taxable"].values()) + used
    business = [c for c in given["carries"] if c["bucket"] in losses.BUSINESS]
    offered = sum(c["paise"] for c in business) - sum(r["paise"] for r in got["own_figures"])
    offered += max(-given["non_speculative_net"], 0) + max(-given["speculative_net"], 0)
    assert offered == used + sum(r["paise"] for r in got["carried"] + got["lapsed"])


def test_a_speculative_loss_never_meets_derivative_profit():
    for case in cases("losses.json"):
        steps = setoff(case.values[0]["in"])["steps"]
        assert not [s for s in steps if s["bucket"] == "speculative" and s["against"] == "business"]


def test_the_last_usable_year_comes_from_the_rule_row():
    assert [losses.last_fy(2026, b) for b in ("stcl", "ltcl", "business", "speculative")] == [
        2034,
        2034,
        2034,
        2030,
    ]
