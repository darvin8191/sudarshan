"""The year's capital-gains schedule against year.json (T4)."""

from __future__ import annotations

import pytest
from tax_vector_tools import cases

from sudarshan.tax import capgains
from sudarshan.tax.model import Carry, Disposal, Gain, Settings, build


def gain(row: dict) -> Gain:
    base = {"taxpayer": 1, "account": 1, "isin": "X", "acquired_day": None, "qty_u": 10**6,
            "receipt_paise": 0, "sale_paise": 0, "cost_paise": 0, "expenses_paise": 0}  # fmt: skip
    return Gain(**{**base, **row})


def schedule(given: dict) -> dict:
    settings = Settings(**given["settings"]) if "settings" in given else None
    carries = [build(Carry, row) for row in given.get("carries", ())]
    return capgains.year_schedule(
        [gain(row) for row in given["gains"]], given["fy"], carries, settings
    )


@pytest.mark.parametrize("case", cases("year.json", "schedule"))
def test_year_schedule(case):
    got, out = schedule(case["in"]), case["out"]
    assert got["taxable"] == out["taxable"]
    assert got["indicative_tax"]["tax_paise"] == out["tax_paise"]
    assert got["indicative_tax"]["cess_paise"] == out["cess_paise"]
    assert got["exemption"]["used_paise"] == out["exempt_used_paise"]
    assert got["carried"] == out["carried"] and got["lapsed"] == out["lapsed"]
    if "out_by_rate" in out:
        assert got["exemption"]["by_rate"] == out["out_by_rate"]
    for name, count in out.get("out_lists", {}).items():
        assert len(got[name]) == count, name
    assert got["buyback_dividend_paise"] == out.get("out_buyback_dividend_paise", 0)


@pytest.mark.parametrize("case", cases("year.json", "schedule"))
def test_every_rupee_of_gain_and_loss_is_accounted_for(case):
    """Gains = taxable + exemption + losses set off; losses = set off + carried on as this year's."""
    got = schedule(case["in"])
    used = sum(step["paise"] for step in got["set_off"])
    taxable = got["taxable"]["st_paise"] + got["taxable"]["lt_paise"]
    assert (
        got["st_gain_paise"] + got["lt_gain_paise"]
        == taxable + got["exemption"]["used_paise"] + used
    )
    own = sum(s["paise"] for s in got["set_off"] if s["step"] == "same_year")
    kept = sum(c["paise"] for c in got["carried"] if c["loss_fy"] == got["fy"])
    assert got["st_loss_paise"] + got["lt_loss_paise"] == own + kept
    assert got["exemption"]["order"] in ("highest_rate_first", "chronological")
    assert all(line["tax_paise"] >= 0 for line in got["indicative_tax"]["lines"])


def test_a_long_term_loss_never_meets_a_short_term_gain_and_the_exemption_is_long_term_only():
    for case in cases("year.json", "schedule"):
        got = schedule(case.values[0]["in"])
        assert not [s for s in got["set_off"] if s["bucket"] == "ltcl" and s["against"] == "short"]
        assert got["exemption"]["used_paise"] <= got["exemption"]["limit_paise"]


@pytest.mark.parametrize("case", cases("year.json", "allocate"))
def test_allocate_expenses(case):
    given = case["in"]
    sales = [Disposal(1, 1, "X", day, n, 10**6, value, 20250101, 0, kind_hint=hint)
             for n, (day, value, hint) in enumerate(given["sales"])]  # fmt: skip
    shares = capgains.allocate_expenses(sales, given["fy"], given["total_paise"])
    assert shares == case["out"] and sum(shares) == given["total_paise"]
