"""M7.32 (P7 lane R): the risk core. Vectors: vectors/risk.json (make_risk.py states the sums)."""

from __future__ import annotations

import json
from fractions import Fraction
from pathlib import Path

import pytest

from sudarshan.portfolio.core import kinds, risk, words
from sudarshan.portfolio.core.performance import Period, Point, periods
from sudarshan.portfolio.core.risk import Holding

FILE = json.loads((Path(__file__).parent / "vectors" / "risk.json").read_text(encoding="utf-8"))
SERIES = {case["id"]: case for case in FILE["series"]}


def by_id(group):
    return pytest.mark.parametrize("case", FILE[group], ids=lambda case: case["id"])


def figures(case, **changes):
    case = {**case, **changes}
    return risk.series_figures(
        [Period(*row) for row in case["periods"]],
        case["annualised_pct"],
        case["risk_free_bp"],
        case["holidays"],
    )


def held(rows):
    return [Holding(*row) for row in rows]


@by_id("series")
def test_the_series_figures_are_the_vectors(case):
    assert figures(case) == case["expect"]


@by_id("falls")
def test_the_fall_is_of_the_index_not_of_the_value(case):
    points = [Point(*row) for row in case["points"]]
    got = risk.fall(periods(points, case["opening_paise"]))
    want = case["expect"]
    assert got._asdict() == {**want, "depth": Fraction(*want["depth"])}


def test_a_deposit_on_the_low_would_read_as_a_recovery_on_the_raw_value():
    """Mutation 16: the fall computed on the raw value. The vector F1 is built so that the two
    readings differ in depth, in the low's day and in whether it recovered."""
    case = FILE["falls"][0]
    values = [row[1] for row in case["points"]]
    high = max(values[:3])
    raw_depth = Fraction(high - min(values[1:3]), high)
    assert raw_depth == Fraction(1, 11) and values[3] > high  # 9.09%, then "recovered"
    got = risk.fall(periods([Point(*row) for row in case["points"]], 0))
    assert got.depth == Fraction(2, 11) and got.recovered_day is None
    assert got.trough_day == case["points"][3][0]


def test_fifty_nine_days_show_no_figure_and_sixty_do():
    """Mutation 16: volatility from fewer than the stated days."""
    assert risk.MIN_DAYS == kinds.STAT_DAYS == 60
    short = figures(SERIES["S3"])
    assert short["days"] == 59 and short["volatility"]["value"] is None
    assert short["drawdown"]["pct"] is None and short["drawdown"]["note"]
    assert figures(SERIES["S1"])["volatility"]["value"] is not None


def test_a_higher_risk_free_rate_lowers_the_ratios_and_nothing_else():
    low, high = figures(SERIES["S1"], risk_free_bp=0), figures(SERIES["S1"], risk_free_bp=2000)
    assert Fraction(low["sharpe"]["value"]) > 0 and Fraction(high["sharpe"]["value"]) == 0
    assert Fraction(high["sortino"]["value"]) == 0
    for name in ("days", "drawdown", "volatility", "calmar"):
        assert low[name] == high[name]


def test_a_holiday_named_keeps_the_day_after_it_in_the_count():
    """Monday 10 Aug 2026 shut: without the holiday named, Friday to Tuesday is two days' move
    and is left out; named, it is one day's."""
    case = SERIES["S1"]
    rows = [row for row in case["periods"] if row[1] != 20260810]
    at = next(i for i, row in enumerate(rows) if row[0] == 20260810)
    rows[at] = [20260807, *rows[at][1:]]
    assert figures(case, periods=rows)["days"] == 58
    assert figures(case, periods=rows, holidays=[20260810])["days"] == 59


def test_no_period_gives_no_fall():
    assert risk.fall([]).depth == 0
    assert risk.series_figures([], None, 650)["days"] == 0


@by_id("concentration")
def test_concentration_is_the_vectors(case):
    got = risk.concentration(
        held(case["holdings"]), case["max_holding_pct"], case["max_sector_pct"]
    )
    assert got == case["expect"]


def test_the_default_limits_are_the_owners_decision():
    assert risk.DEFAULTS == {
        "risk_free_bp": 650,
        "max_holding_pct": "15.0000",
        "max_sector_pct": "35.0000",
        "monthly_expenses_paise": None,
        "months_target": 6,
    }
    assert set(risk.DEFAULTS) == set(kinds.SETTING_KEYS)
    case = FILE["concentration"][0]
    assert (
        risk.concentration(
            held(case["holdings"]),
            risk.DEFAULTS["max_holding_pct"],
            risk.DEFAULTS["max_sector_pct"],
        )
        == case["expect"]
    )


@by_id("overlap")
def test_overlap_is_the_vectors(case):
    got = risk.overlap(case["tab_id"], held(case["mine"]), held(case["theirs"]))
    assert got == case["expect"]
    back = risk.overlap(case["tab_id"], held(case["theirs"]), held(case["mine"]))
    assert back["weight_pct"] == got["weight_pct"] and set(back["names"]) == set(got["names"])


@by_id("emergency")
def test_the_emergency_fund_is_the_vectors(case):
    got = risk.emergency(
        case["liquid_paise"], case["monthly_expenses_paise"], case["months_target"]
    )
    assert got == case["expect"]


@by_id("together")
def test_several_tabs_as_one_are_the_vectors(case):
    points, opening = risk.together([[Point(*row) for row in tab] for tab in case["tabs"]])
    assert points == [Point(*row) for row in case["expect"]["points"]]
    assert opening == case["expect"]["opening_paise"]


def test_one_tab_together_is_itself():
    case = FILE["together"][0]
    one = [Point(*row) for row in case["tabs"][0]]
    assert risk.together([one]) == (one, 0)


def test_the_sentences_say_two_decimals_half_up_and_whole_numbers_whole():
    assert words.risk_untagged("42.0000") == "Untagged: 42%; tag them to check sectors."
    assert words.risk_untagged("0.0050") == "Untagged: 0.01%; tag them to check sectors."
    assert words.risk_untagged("0.0049") == "Untagged: 0%; tag them to check sectors."
    assert words.risk_basis(20261113) == "On closing values up to 13 Nov 2026."
    assert words.risk_days_more(60, 18).endswith("42 more to go.")
