"""The charges estimate (JA6): vectors C1-C7, the rate card's shape, the 3-fill order."""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

import pytest

from sudarshan.journal.core import charges, kinds

DATA = json.loads((Path(__file__).parent / "vectors" / "charges.json").read_text(encoding="utf-8"))
VEC = {v["id"]: v for v in DATA["vectors"]}


@pytest.mark.parametrize("vec", DATA["vectors"], ids=[v["id"] for v in DATA["vectors"]])
def test_charges_vector(vec):
    got = charges.estimate(vec["fills"], vec["plan"])
    assert got["orders"] == vec["orders"]
    assert got["modelled"] is vec.get("modelled", True)
    assert got["plan"] == vec["plan"]
    assert set(got["parts"]) == {f.name for f in kinds.record("ChargeParts")}
    if vec.get("expect"):
        assert got["parts"] == vec["expect"]
    for key, want in (vec.get("parts_only") or {}).items():
        assert got["parts"][key] == want, key
    if "brokerage_paise" in vec:
        assert got["parts"]["brokerage_paise"] == vec["brokerage_paise"]


def test_c1_net_of_the_trip():
    """P1's gross of 812.50 less the estimate of 87.79 is 724.71."""
    assert 81250 - charges.estimate(VEC["C1"]["fills"])["parts"]["total_paise"] == 72471


def test_brokerage_is_per_order_not_per_fill():
    """Mutation (Y23): per fill, the 3-fill order would pay 3 x Rs 30."""
    got = charges.estimate(VEC["C4"]["fills"])
    assert got["parts"]["brokerage_paise"] == 3000
    one_fill = charges.estimate([VEC["C4"]["fills"][0]])
    assert one_fill["parts"]["brokerage_paise"] == 3000


def test_components_round_per_order_before_adding():
    """Mutation 16: summing the two orders' SEBI first (0.0138) would give 0.01, not 0.02."""
    got = charges.estimate(VEC["C1"]["fills"])["parts"]
    assert got["sebi_paise"] == 2
    turnover = 13812.5 * 10000
    summed_then_rounded = round(turnover * 1e-6 / 100)
    assert summed_then_rounded == 1 != got["sebi_paise"]


def test_the_api_brokerage_is_not_applied_to_an_upstox_app_order():
    """Mutation 17: on the standard plan an app order pays Rs 20 where an API order pays Rs 10."""
    assert charges.brokerage_paise("standard", "upstox_app", 20261109) == 2000
    assert charges.brokerage_paise("standard", "api", 20261109) == 1000
    assert charges.brokerage_paise("standard", "telegram", 20261109) == 1000
    assert charges.brokerage_paise("standard", None, 20261109) == 1000
    assert charges.brokerage_paise("standard", "api", 20261231) == 1000
    assert charges.brokerage_paise("standard", "api", 20270101) == 2000
    assert charges.brokerage_paise("plus", "upstox_app", 20261109) == 3000
    assert charges.brokerage_paise("plus", "api", 20261109) == 3000


def test_the_stt_row_boundary_is_1_april_2026():
    """Mutation 18: the new STT row must start on 1 Apr, not 31 Mar."""
    assert charges._statutory("options", "SELL", "stt", 20260331) == Fraction(1, 1000)
    assert charges._statutory("options", "SELL", "stt", 20260401) == Fraction(15, 10000)
    assert charges._statutory("futures", "SELL", "stt", 20260331) == Fraction(2, 10000)
    assert charges._statutory("futures", "SELL", "stt", 20260401) == Fraction(5, 10000)


def test_the_stt_rows_before_1_october_2024():
    """The card reaches back to 1 Apr 2024: a fill of April to September 2024 takes the lower
    F&O STT of that time, and the row changes on 1 Oct, not a day either side."""
    assert charges.CARD_START == 20240401
    assert charges._statutory("options", "SELL", "stt", 20240401) == Fraction(625, 1_000_000)
    assert charges._statutory("options", "SELL", "stt", 20240930) == Fraction(625, 1_000_000)
    assert charges._statutory("options", "SELL", "stt", 20241001) == Fraction(1, 1000)
    assert charges._statutory("futures", "SELL", "stt", 20240930) == Fraction(125, 1_000_000)
    assert charges._statutory("futures", "SELL", "stt", 20241001) == Fraction(2, 10000)
    # A fill older than the card takes its first row, as before.
    assert charges._statutory("options", "SELL", "stt", 20230105) == Fraction(625, 1_000_000)


def test_an_exercise_is_its_own_segment_only_when_the_fill_says_so():
    """Mutation: the exercise rate applied to every option sale, or to the writer's side."""
    plain = dict(VEC["C9"]["fills"][0])
    del plain["exercise"]
    assert charges.estimate([plain])["parts"]["stt_paise"] == 1463  # 0.15% either way in Nov 2026
    early = {**VEC["C10"]["fills"][0]}
    assert charges.estimate([early])["parts"]["stt_paise"] == 1219
    del early["exercise"]  # an ordinary sale of March 2026 pays 0.1%, and exchange charges
    got = charges.estimate([early])["parts"]
    assert got["stt_paise"] == 975 and got["exchange_paise"] > 0
    assert charges._statutory("options_exercise", "BUY", "stt", 20261126) == 0
    assert charges._statutory("options_exercise", "SELL", "stt", 20260331) == Fraction(125, 100000)
    assert charges._statutory("options_exercise", "SELL", "stt", 20260401) == Fraction(15, 10000)
    share = {**plain, "exchange": "NSE", "symbol": "ACME", "product": "CNC", "exercise": True}
    assert charges.estimate([share])["parts"]["exchange_paise"] > 0  # never for a share


def test_options_buy_pays_no_stt_and_sell_pays_no_stamp():
    assert charges._statutory("options", "BUY", "stt", 20261109) == 0
    assert charges._statutory("options", "SELL", "stamp", 20261109) == 0
    assert charges._statutory("equity_delivery", "BUY", "stt", 20261109) == Fraction(1, 1000)
    assert charges._statutory("equity_delivery", "SELL", "stt", 20261109) == Fraction(1, 1000)


def test_the_card_is_dated_and_sorted_sensibly():
    for row in charges.RATE_CARD:
        assert row.segment in kinds.CHARGE_SEGMENTS and row.kind in (
            "stt", "exchange", "sebi", "stamp"
        )  # fmt: skip
        assert row.side in ("BUY", "SELL", "BOTH")
        assert row.from_day >= charges.CARD_START
    assert {r.plan for r in charges.BROKERAGE_CARD} == set(kinds.CHARGE_PLANS)
    assert charges.DEFAULT_PLAN == "plus"


def test_an_unknown_plan_is_refused():
    with pytest.raises(ValueError):
        charges.brokerage_paise("gold", "api", 20261109)


def test_segments():
    assert charges.segment_of("NFO", "NIFTY25NOV2625000CE", "MIS") == "options"
    assert charges.segment_of("NFO", "NIFTY25NOV26FUT", "NRML") == "futures"
    assert charges.segment_of("NSE", "SBIN", "CNC") == "equity_delivery"
    assert charges.segment_of("NSE", "SBIN", "MIS") == "equity_intraday"
    for ex in ("BFO", "BSE", "CDS", "MCX"):
        assert charges.segment_of(ex, "X", "MIS") == "not_modelled"


def test_a_modelled_order_beside_an_unmodelled_one_flags_the_whole_estimate():
    fills = [*VEC["C1"]["fills"], *VEC["C6"]["fills"]]
    got = charges.estimate(fills)
    assert got["modelled"] is False
    assert got["parts"]["total_paise"] == 8779


def test_the_estimate_gap_is_flagged_over_five_percent():
    for case in DATA["gap"]:
        got = charges.estimate_gap(case["estimate_paise"], case["actual_paise"])
        assert got["flagged"] is case["flagged"]
        assert got["diff_pct"] == case["diff_pct"]
    assert charges.estimate_gap(100, 0) == {"diff_paise": 100, "diff_pct": None, "flagged": False}
