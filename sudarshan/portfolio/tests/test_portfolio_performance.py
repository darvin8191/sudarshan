"""core/performance.py (PA4): money put in, absolute return, XIRR, TWR and the year's figures.

Four witnesses, as for the ledger. The golden vectors (vectors/performance.json) are worked out by
hand and by an evaluator in make_pa4.py that imports nothing from sudarshan and finds every rate by
another route (floats walked out from 0%, halved, then polished with Newton's method on Decimal
exponentials to 60 digits and checked by substituting). The naive oracle in portfolio_oracle.py
reads the flows from the entries one at a time, scans for every rate in floats and multiplies the
time-weighted periods as fractions. Both are run against the engine on random tabs and random
cash. Identities that must hold whatever the numbers are close the gaps: what a tab put in is what
its positions cost less what it earned, scaling the money or moving the dates changes no rate,
flat deposits change no time-weighted return, and the answer never depends on the arithmetic
context the process happens to be in.
"""

from __future__ import annotations

import ast
import json
import math
import random
import time
from datetime import date, timedelta
from decimal import ROUND_DOWN, Decimal, Inexact, localcontext
from fractions import Fraction
from pathlib import Path

import pytest
from portfolio_oracle import flows as oracle_flows
from portfolio_oracle import income_by_month, xirr_roots, year_figures
from portfolio_oracle import twr as oracle_twr
from portfolio_ts import interfaces, members
from test_portfolio_ledger import DECIMALS, random_rows, repaired
from test_portfolio_vectors import load

from sudarshan.portfolio.core import kinds, words
from sudarshan.portfolio.core.days import from_date
from sudarshan.portfolio.core.ledger import replay
from sudarshan.portfolio.core.model import Entry
from sudarshan.portfolio.core.money import pct_of_rate
from sudarshan.portfolio.core.performance import (
    Flow,
    Fy,
    Point,
    Twr,
    Xirr,
    absolute_return,
    by_financial_year,
    cashflows,
    first_flow_day,
    flows_of,
    income_report,
    periods,
    summary,
    totals,
    twr,
    xirr,
    xirr_rate,
)
from sudarshan.portfolio.core.valuation import Fixed, Line, Row, tab_totals

VECTORS = Path(__file__).parent / "vectors"
FILE = json.loads((VECTORS / "performance.json").read_text(encoding="utf-8"))
GEN = load("make_pa4.py")  # the independent evaluator
TYPES_TS = Path(__file__).resolve().parents[3] / "frontend/src/sudarshan/portfolio/types.ts"
U = kinds.QTY_SCALE
SOME_DAY = date(2025, 1, 1)


def ledger_of(rows, as_of=None):
    return replay([Entry.of(r) for r in rows], as_of=as_of, decimals=DECIMALS)


def flows_in(case):
    return [Flow.of(f) for f in case["flows"]]


def points_in(case):
    return [Point.of(p) for p in case["points"]]


def day_plus(day: int, n: int) -> int:
    start = date(day // 10000, day // 100 % 100, day % 100)
    return from_date(start + timedelta(days=n))


def ordinal(day: int) -> int:
    return date(day // 10000, day // 100 % 100, day % 100).toordinal()


def fit_error(rate: Decimal, cash) -> Decimal:
    """How far the cash is from adding up to nothing at a yearly rate, as a share of the size of
    its terms there: the sum is taken with a Decimal power, a third way to see a rate is a fit."""
    with localcontext() as ctx:
        ctx.prec = 80
        terms = [
            Decimal(paise) * (1 + rate) ** (Decimal(ordinal(cash[0][0]) - ordinal(day)) / 365)
            for day, paise in cash
        ]
        return abs(sum(terms)) / sum(abs(term) for term in terms)


# -- the golden vectors ---------------------------------------------------------------------------


def test_the_file_has_what_the_design_needs():
    assert {"X1", "X2", "X3", "X3b", "X4", "X5"} <= {case["id"] for case in FILE["xirr"]}
    assert {"T1", "T2", "T3", "T4", "T5"} <= {case["id"] for case in FILE["twr"]}
    assert {case["id"] for case in FILE["net"]} == {"N1"}
    for section in ("flows", "xirr", "chain", "rates", "twr", "net", "years"):
        assert FILE[section], section
        assert all(case["title"] and case["why"] for case in FILE[section]), section
        assert len({case["id"] for case in FILE[section]}) == len(FILE[section]), section


@pytest.mark.parametrize("case", FILE["flows"], ids=lambda case: case["id"])
def test_the_flows_of_a_ledger_are_the_vectors(case):
    got = flows_of(ledger_of(case["entries"], case.get("as_of")))
    assert [flow.as_dict() for flow in got] == case["expect"]["flows"]
    assert totals(got)._asdict() == case["expect"]["totals"]
    assert all(flow.moved for flow in got)


@pytest.mark.parametrize("case", FILE["flows"], ids=lambda case: case["id"])
def test_the_naive_oracle_reads_the_same_flows(case):
    got = oracle_flows(case["entries"], case.get("as_of"), DECIMALS)
    assert got == case["expect"]["flows"]


@pytest.mark.parametrize("case", FILE["xirr"], ids=lambda case: case["id"])
def test_the_summary_of_a_tab_is_the_vectors(case):
    got = summary(flows_in(case), case["value_paise"], case["valuation_day"])
    assert got == case["expect"]
    figure = xirr(flows_in(case), case["value_paise"], case["valuation_day"])
    assert figure.as_dict() == case["expect"]["xirr"]


@pytest.mark.parametrize("case", FILE["xirr"], ids=lambda case: case["id"])
def test_the_summary_adds_up(case):
    got = summary(flows_in(case), case["value_paise"], case["valuation_day"])
    assert got["net_put_in_paise"] == (
        got["money_in_paise"] + got["fee_paise"] - got["money_out_paise"]
    )
    assert got["total_gain_paise"] == got["value_paise"] - got["net_put_in_paise"]


@pytest.mark.parametrize("case", FILE["chain"], ids=lambda case: case["id"])
def test_a_ledger_replayed_gives_the_vectors_report(case):
    ledger = ledger_of(case["entries"], case["valuation_day"])
    got = summary(flows_of(ledger), case["value_paise"], case["valuation_day"])
    assert got == case["expect"]


@pytest.mark.parametrize("case", FILE["rates"], ids=lambda case: case["id"])
def test_the_rate_of_dated_cash_is_the_vectors(case):
    cash = [tuple(each) for each in case["cash"]]
    rate = xirr_rate(cash)
    want = case["expect"]
    if want["pct"] is None:
        assert rate is None
        return
    assert pct_of_rate(rate) == want["pct"]
    assert abs(rate - Decimal(want["rate"])) < Decimal("1e-10")


@pytest.mark.parametrize("case", FILE["rates"], ids=lambda case: case["id"])
def test_the_scanning_oracle_finds_the_same_rate(case):
    cash = [tuple(each) for each in case["cash"]]
    found = xirr_roots(cash)
    want = case["expect"]
    if want["pct"] is None:
        assert found == []
    else:
        assert found[0] == pytest.approx(float(want["rate"]), abs=1e-9)
    if "roots" in case:  # every rate that fits, and no other
        assert sorted(found) == pytest.approx(
            sorted(float(Fraction(text)) for text in case["roots"]), abs=1e-9
        )


@pytest.mark.parametrize(
    "case", [case for case in FILE["rates"] if "roots" in case], ids=lambda case: case["id"]
)
def test_every_rate_a_case_lists_makes_the_cash_add_up_to_nothing(case):
    # Exactly: the rates are fractions, every gap is whole years, and Fractions have no rounding.
    cash = [(ordinal(day) - ordinal(case["cash"][0][0]), paise) for day, paise in case["cash"]]
    assert all(gap % 365 == 0 for gap, _ in cash)
    rates = [Fraction(text) for text in case["roots"]]
    assert rates == sorted(rates)
    for rate in rates:
        assert sum(paise * (1 + rate) ** -(gap // 365) for gap, paise in cash) == 0, rate


@pytest.mark.parametrize("case", FILE["twr"], ids=lambda case: case["id"])
def test_the_periods_and_the_figure_of_a_series_are_the_vectors(case):
    found = periods(points_in(case), case["opening_paise"])
    assert [
        {"start": p.start, "day": p.day, "base": p.base, "grown": p.grown, "pct": p.pct}
        for p in found
    ] == case["expect"]["periods"]
    assert twr(points_in(case), case["opening_paise"]).as_dict() == case["expect"]["twr"]


@pytest.mark.parametrize("case", FILE["twr"], ids=lambda case: case["id"])
def test_the_exact_oracle_gives_the_same_series(case):
    got = oracle_twr(case["points"], case["opening_paise"])
    want = case["expect"]
    assert [{"start": s, "day": d, "base": b, "grown": g} for s, d, b, g in got["stretches"]] == [
        {key: p[key] for key in ("start", "day", "base", "grown")} for p in want["periods"]
    ]
    for key in ("start_day", "pct", "annualised_pct"):
        assert got["figure"][key] == want["twr"][key]


@pytest.mark.parametrize("case", FILE["net"], ids=lambda case: case["id"])
def test_net_money_put_in_is_the_vectors(case):
    got = summary(flows_in(case), case["value_paise"], case["valuation_day"])
    assert got == case["expect"]
    for row in case["running"]:
        assert totals(flows_in(case), through=row["day"]).net_put_in == row["net_put_in_paise"]


@pytest.mark.parametrize("case", FILE["years"], ids=lambda case: case["id"])
def test_the_year_figures_are_the_vectors(case):
    ledger = ledger_of(case["entries"], case["as_of"])
    assert [fy.as_dict() for fy in by_financial_year(ledger)] == case["expect"]["by_fy"]
    assert income_report(ledger, case["valuation_day"]) == case["expect"]["income"]


@pytest.mark.parametrize("case", FILE["years"], ids=lambda case: case["id"])
def test_the_naive_oracle_gives_the_same_year_figures(case):
    assert year_figures(case["entries"], case["as_of"], DECIMALS) == case["expect"]["by_fy"]
    got = income_by_month(case["entries"], case["valuation_day"], DECIMALS)
    assert got == case["expect"]["income"]


# -- what the vectors do not say in words -----------------------------------------------------------


def test_the_design_numbers_are_in_the_file():
    by_id = {case["id"]: case for case in FILE["xirr"]}
    assert {case["expect"]["xirr"]["state"] for case in FILE["xirr"]} == set(kinds.XIRR_STATES)
    for name, rate in (("X1", "9.9714"), ("X2", "11.0863"), ("X3b", "8.0000"), ("X5", "10.3392")):
        assert by_id[name]["expect"]["xirr"]["pct"] == rate
    for name, shown in (("X3", "later"), ("X3c", "later"), ("X3b", "shown"), ("X4", "none")):
        assert by_id[name]["expect"]["xirr"]["state"] == shown
    assert (
        by_id["X3"]["expect"]["xirr"]["words"] == "XIRR appears after one year (from 1 May 2027)."
    )
    assert (
        by_id["X4"]["expect"]["xirr"]["words"] == "Return needs at least one purchase and a value."
    )


def test_the_year_is_counted_in_days_from_the_first_cash():
    one_buy = [Flow(20260501, in_paise=1_000_000)]
    for last, state in ((20270429, "later"), (20270430, "later"), (20270501, "shown")):
        assert xirr(one_buy, 1_080_000, last).state == state, last
    assert xirr(one_buy, 1_080_000, 20270430).from_day == 20270501
    leap = [Flow(20270301, in_paise=1_000_000)]  # 2028 has a 29 February
    assert xirr(leap, 1_050_000, 20280228).from_day == 20280229
    assert xirr(leap, 1_050_000, 20280229).state == "shown"
    assert kinds.XIRR_MIN_DAYS == 365


def test_a_value_alone_or_a_purchase_alone_has_no_rate():
    buy, sale = Flow(20250101, in_paise=1_000), Flow(20250102, out_paise=1_000)
    assert xirr([buy], 0, 20270101).state == "none"
    assert xirr([], 1_000, 20270101).state == "none"
    assert xirr([sale], 0, 20270101).state == "none"
    assert xirr([], 0, 20270101).state == "none"
    even = xirr([buy, sale], 0, 20270101)  # bought and sold for the same: it earned nothing
    assert (even.state, even.pct) == ("shown", "0.0000")


def test_a_flow_after_the_valuation_day_counts_for_nothing():
    flows = [Flow(20250101, in_paise=1_000_000), Flow(20260601, out_paise=500_000)]
    early = summary(flows, 1_100_000, 20260101)
    alone = summary(flows[:1], 1_100_000, 20260101)
    assert early == alone
    assert early["money_out_paise"] == 0
    assert totals(flows, through=20260601).out_paise == 500_000
    assert totals(flows, through=20260531).out_paise == 0
    nothing_yet = summary(flows, 0, 20241231)  # read before the first of them
    assert nothing_yet["first_flow_day"] is None
    assert nothing_yet["absolute_return_pct"] is None
    assert nothing_yet["xirr"] == Xirr("none", words=words.XIRR_NEEDS_A_PURCHASE).as_dict()


def test_the_cash_of_a_day_is_out_less_in_and_fees_and_the_value_comes_last():
    flows = [Flow(20250101, in_paise=100, fee_paise=5), Flow(20250101, out_paise=30)]
    assert cashflows(flows, 0, 20250201) == [(20250101, -75)]
    assert cashflows(flows, 200, 20250101) == [(20250101, 125)]
    assert cashflows(flows, 200, 20250201) == [(20250101, -75), (20250201, 200)]
    assert cashflows([Flow(20250101, in_paise=7, out_paise=7)], 0, 20250201) == []
    assert cashflows([Flow(20250301, in_paise=7)], 5, 20250201) == [(20250201, 5)]


def test_the_first_flow_is_the_first_day_money_moved():
    assert first_flow_day([]) is None
    assert first_flow_day([Flow(20250105), Flow(20250103, fee_paise=1), Flow(20250101)]) == 20250103


def test_a_flow_is_in_out_or_a_fee():
    flow = Flow(20250101, in_paise=100, out_paise=30, fee_paise=5)
    assert flow.cash_paise == -75
    assert flow.moved and not Flow(20250101).moved
    assert Flow.of(flow.as_dict()) == flow
    assert list(flow.as_dict()) == ["day", "in_paise", "out_paise", "fee_paise"]


def test_the_absolute_return_is_the_valuation_modules_too():
    rng = random.Random(7)
    for _ in range(300):
        flows = [
            Flow(
                20250101,
                rng.randint(0, 9_000_000),
                rng.randint(0, 4_000_000),
                rng.randint(0, 50_000),
            )
        ]
        value = rng.randint(0, 12_000_000)
        sums = totals(flows)
        row = Row("ok", value * 100, None, (Line(1, U, U, 0, 0, 0, 0),))  # one share worth `value`
        fixed = Fixed(0, 0, sums.fee_paise, sums.in_paise, sums.out_paise, 0, 0)
        assert tab_totals([row], fixed).absolute_return_pct == absolute_return(value, sums)
    assert absolute_return(5, totals([])) is None


# -- the engine and the oracle on random tabs ---------------------------------------------------------


def spread(rows, factor=9):
    """The same entries with their days stretched out, so that a tab runs through several years."""
    stretched = []
    for row in rows:
        row = dict(row)
        day = row["trade_date"]
        offset = (date(day // 10000, day // 100 % 100, day % 100) - SOME_DAY).days
        row["trade_date"] = from_date(SOME_DAY + timedelta(days=offset * factor))
        stretched.append(row)
    return stretched


def random_tab(rng):
    return spread(repaired(random_rows(rng, rng.randint(2, 45))), rng.choice([1, 4, 9, 20]))


def test_the_flows_of_random_tabs_are_the_naive_oracles():
    rng = random.Random(20261028)
    seen = {"in": 0, "out": 0, "fee": 0, "outside_in": 0, "outside_out": 0, "later": 0}
    for _ in range(220):
        rows = random_tab(rng)
        days = sorted({row["trade_date"] for row in rows})
        as_of = rng.choice([None, rng.choice(days)]) if days else None
        got = [flow.as_dict() for flow in flows_of(ledger_of(rows, as_of))]
        assert got == oracle_flows(rows, as_of, DECIMALS)
        assert [flow["day"] for flow in got] == sorted({flow["day"] for flow in got})
        for flow in got:
            seen["in"] += bool(flow["in_paise"])
            seen["out"] += bool(flow["out_paise"])
            seen["fee"] += bool(flow["fee_paise"])
        for row in rows:
            seen["outside_in"] += row["kind"] == "TRANSFER" and row.get("account_id") is None
            seen["outside_out"] += (
                row["kind"] == "TRANSFER"
                and row.get("account_id") is not None
                and row.get("to_account_id") is None
            )
        seen["later"] += as_of is not None
    assert all(count >= 20 for count in seen.values()), seen


def test_what_a_tab_put_in_is_what_it_costs_less_what_it_earned():
    # In + fees - out = cost held - realised - income + fees. A purchase puts its cost in, a sale
    # takes out its gain and the cost it sold, income and fees are themselves flows, and shares
    # moved in or out of the tab at no stated value are put in or taken out at cost.
    rng = random.Random(20261029)
    checked = 0
    for _ in range(220):
        rows = [
            {key: value for key, value in row.items() if key != "value_paise"}
            for row in random_tab(rng)
        ]
        ledger = ledger_of(rows)
        sums = totals(flows_of(ledger))
        held = sum(position.cost_paise for position in ledger.positions.values())
        assert sums.net_put_in == (
            held - ledger.realised_paise - ledger.income_paise + ledger.fees_paise
        )
        assert sums.fee_paise == ledger.fees_paise
        checked += bool(sums.in_paise)
    assert checked > 150


def test_the_year_figures_of_random_tabs_are_the_naive_oracles():
    rng = random.Random(20261030)
    years: dict[int, int] = {}
    for _ in range(220):
        rows = random_tab(rng)
        ledger = ledger_of(rows)
        got = [fy.as_dict() for fy in by_financial_year(ledger)]
        assert got == year_figures(rows, None, DECIMALS)
        assert [fy["fy"] for fy in got] == sorted({fy["fy"] for fy in got})
        for fy in got:
            years[fy["fy"]] = years.get(fy["fy"], 0) + 1
        if got:
            assert sum(fy["realised_paise"] for fy in got) == ledger.realised_paise
            assert sum(fy["income_paise"] for fy in got) == ledger.income_paise
            assert sum(fy["fees_paise"] for fy in got) == ledger.fees_paise
        days = sorted({row["trade_date"] for row in rows}) or [20260101]
        day = rng.choice(days + [day_plus(days[-1], 200)])
        later = income_report(ledger, day)
        assert later == income_by_month(rows, day, DECIMALS)
        assert len(later["by_month"]) == 12
        months = [each["month"] for each in later["by_month"]]
        assert months == sorted(months) and months[-1] == day // 100
    assert len(years) >= 4, years


# -- the rate, on random cash ---------------------------------------------------------------------------


def random_cash(rng, shape):
    start = date(rng.randint(2010, 2022), rng.randint(1, 12), rng.randint(1, 28))
    span = rng.choice([365, 500, 900, 1500, 2500, 5000])
    count = rng.randint(2, 12)
    if shape == "investor":
        # Everything paid in at least 90 days before the value, which is between 0.4 and 3 times
        # what was paid: exactly one rate fits (the signs change once) and it is a plain one.
        offsets = sorted({0, *(rng.randint(1, span - 90) for _ in range(count - 2))})
        paid = [rng.randint(1_000, 5_000_000) for _ in offsets]
        final = round(sum(paid) * rng.uniform(0.4, 3.0))
        return [
            *(
                (from_date(start + timedelta(days=off)), -amount)
                for off, amount in zip(offsets, paid, strict=True)
            ),
            (from_date(start + timedelta(days=span)), final),
        ]
    if shape == "pair":
        # Three equal gaps and a pair of z = (1 + r) ^ (-gap / 365) that are roots, as made:
        # top * (z - z1) * (z - z2), so two rates fit (rounding to paise moves them a little).
        gap = rng.choice([91, 182, 365, 730])
        z1, z2 = rng.uniform(0.55, 1.6), rng.uniform(0.55, 1.6)
        top, sign = rng.randint(100_000, 5_000_000), rng.choice([-1, 1])
        made = [round(top * z1 * z2), -round(top * (z1 + z2)), top]
        return [
            (from_date(start + timedelta(days=gap * i)), sign * amount)
            for i, amount in enumerate(made)
        ]
    offsets = sorted({0, span, *(rng.randint(1, span) for _ in range(count - 2))})
    return [
        (
            from_date(start + timedelta(days=off)),
            rng.choice([-1, 1]) * rng.randint(1_000, 5_000_000),
        )
        for off in offsets
    ]


def x_of(rate) -> float:
    return math.log1p(float(rate))


def lattice_step(cash, x: float) -> float:
    """How far apart the solver looks, in the log of the rate, near ``x``: 36.5 / (days spanned) at
    first and then 3% of the distance from zero. Two rates closer than that can be taken for none."""
    return max(36.5 / (ordinal(cash[-1][0]) - ordinal(cash[0][0])), 0.03 * abs(x))


def test_the_rate_of_random_cash_is_the_scanning_oracles_and_the_generators():
    rng = random.Random(20261031)
    counts = {"none": 0, "one": 0, "several": 0, "skipped": 0, "compared": 0}
    for number in range(300):
        cash = random_cash(rng, ("investor", "mixed", "pair")[number % 3])
        roots = xirr_roots(cash)
        got = xirr_rate(cash)
        counts["none" if not roots else "one" if len(roots) == 1 else "several"] += 1
        if got is not None:
            assert fit_error(got, cash) < Decimal("1e-9")
        if not roots:
            if got is not None:  # a rate the oracle's reach does not extend to
                assert abs(x_of(got)) > 20
            continue
        xs = [math.log1p(rate) for rate in roots]
        tied = len(xs) > 1 and abs(abs(xs[0]) - abs(xs[1])) < 1e-6
        crowded = any(abs(xs[0] - other) < 2 * lattice_step(cash, xs[0]) for other in xs[1:])
        if tied or crowded or abs(xs[0]) > 20:
            counts["skipped"] += 1
            continue
        counts["compared"] += 1
        assert got is not None, cash
        assert float(got) == pytest.approx(roots[0], rel=1e-9, abs=1e-9)
        if number % 4 == 0:  # the generator's rate, polished to 60 digits
            exact = GEN.nearest_rate(cash)
            assert abs(got - exact) <= Decimal("1e-12") * max(1, abs(exact))
    assert counts["none"] >= 5 and counts["one"] >= 60 and counts["several"] >= 60, counts
    assert counts["compared"] >= 200, counts


def single_root_cash(rng):
    return random_cash(rng, "investor")


def test_scaling_the_money_or_moving_the_dates_changes_no_rate():
    rng = random.Random(20261101)
    for _ in range(60):
        cash = single_root_cash(rng)
        base = xirr_rate(cash)
        assert base is not None
        assert xirr_rate([(day, paise * 1000) for day, paise in cash]) == base
        assert xirr_rate([(day, -paise) for day, paise in cash]) == base
        assert xirr_rate([(day_plus(day, 4000), paise) for day, paise in cash]) == base
        shuffled = list(cash)
        rng.shuffle(shuffled)
        assert xirr_rate(shuffled) == base
        split = [(day, paise // 2) for day, paise in cash] + [
            (day, paise - paise // 2) for day, paise in cash
        ]
        assert xirr_rate(split) == base


def test_a_higher_final_value_never_gives_a_lower_rate():
    rng = random.Random(20261102)
    for _ in range(60):
        cash = single_root_cash(rng)
        *paid, (last_day, last) = cash
        rates = [xirr_rate([*paid, (last_day, last + extra)]) for extra in (0, 1, 1_000, 10**6)]
        assert all(rate is not None for rate in rates)
        assert rates == sorted(rates)


def test_reading_time_backwards_turns_the_rate_into_its_mirror():
    # Reversing the order of the days replaces q by 1 / q, so 1 + r becomes 1 / (1 + r).
    rng = random.Random(20261103)
    for _ in range(60):
        cash = single_root_cash(rng)
        base = xirr_rate(cash)
        first, last = cash[0][0], cash[-1][0]
        span = ordinal(last) - ordinal(first)
        backwards = [
            (day_plus(first, span - (ordinal(day) - ordinal(first))), paise) for day, paise in cash
        ]
        mirror = xirr_rate(backwards)
        assert mirror is not None
        assert abs((1 + base) * (1 + mirror) - 1) < Decimal("1e-9") * (1 + base) * (1 + mirror)


def test_a_dozen_known_rates_come_back_exactly():
    # Money paid in once and received once, n years apart: (1 + r) ^ n = received / paid.
    for years, ratio, want in ((1, 2, "100.0000"), (2, 4, "100.0000"), (3, 8, "100.0000")):
        cash = [(20250101, -1_000_000), (day_plus(20250101, 365 * years), 1_000_000 * ratio)]
        assert pct_of_rate(xirr_rate(cash)) == want
    for ratio, want in ((1, "0.0000"), (1.5, "50.0000"), (0.5, "-50.0000"), (1.1, "10.0000")):
        cash = [(20250101, -1_000_000), (20260101, round(1_000_000 * ratio))]
        assert pct_of_rate(xirr_rate(cash)) == want, ratio
    # borrowed money has the same rate as lent money: 10,000.00 received, 11,000.00 paid back
    assert pct_of_rate(xirr_rate([(20250101, 1_000_000), (20260101, -1_100_000)])) == "10.0000"
    assert pct_of_rate(xirr_rate([(20250101, 1_100_000), (20260101, -1_000_000)])) == "-9.0909"


def test_cash_that_adds_up_to_nothing_has_exactly_the_rate_zero():
    # Nothing was gained or lost: not a rate near 0%, which the search would also say to within a
    # hair, but 0% itself, found without a search.
    for cash in (
        [(20250101, -1_000_000), (20260101, 1_000_000)],
        [(20250101, -1_000_000), (20250102, 400_000), (20260101, 600_000)],
        [(20250101, 1_000_000), (20260101, -2_500_000), (20270101, 1_500_000)],  # (0% and 50%)
        [(20250101, 7), (20250601, -3), (20260101, -4)],
    ):
        assert xirr_rate(cash) == 0
        assert xirr_rate(cash[::-1]) == 0
        assert xirr_rate(cash) == Decimal(0) and not xirr_rate(cash).is_signed()
    flows = [Flow(20240101, in_paise=1_000_000), Flow(20250101, out_paise=300_000)]
    assert xirr_rate(cashflows(flows, 700_000, 20260101)) == 0
    assert xirr(flows, 700_000, 20260101) == Xirr("shown", pct="0.0000")


def test_two_rates_equally_near_zero_give_the_gain_every_time():
    # +1,000.00 -2,500.00 +1,000.00 at a year's gap: (1 + r) is 2 or 1/2, +100% or -50%.
    cash = [(20250101, 100_000), (20260101, -250_000), (20270101, 100_000)]
    for order in (cash, cash[::-1], [cash[1], cash[2], cash[0]]):
        assert pct_of_rate(xirr_rate(order)) == "100.0000"
    assert sorted(round(rate, 6) for rate in xirr_roots(cash)) == [-0.5, 1.0]


def test_cash_with_no_rate_says_so_quickly():
    # (x - 1)^2 (x - 2)^2 + 1 in x = 1 / (1 + r), a year apart: it never reaches zero.
    cash = [(day_plus(20200101, 365 * i), c * 1000) for i, c in enumerate([5, -12, 13, -6, 1])]
    began = time.perf_counter()
    assert xirr_rate(cash) is None
    assert xirr_rate([(day, -paise) for day, paise in cash]) is None
    assert xirr_rate([]) is None
    assert xirr_rate([(20250101, 0), (20260101, 0)]) is None
    assert xirr([Flow(20200101, in_paise=5000)], 0, 20230101).state == "none"
    assert time.perf_counter() - began < 10


def test_a_long_tab_is_fast_and_its_rate_fits():
    rng = random.Random(20261104)
    start, cash, paid = date(2006, 1, 1), [], 0
    for month in range(240):  # twenty years of a monthly investment, then the value
        amount = rng.randint(400_000, 600_000)
        paid += amount
        cash.append((from_date(start + timedelta(days=month * 30)), -amount))
    cash.append((from_date(start + timedelta(days=240 * 30)), paid * 3))
    began = time.perf_counter()
    rate = xirr_rate(cash)
    assert time.perf_counter() - began < 10
    assert fit_error(rate, cash) < Decimal("1e-9")
    assert Decimal("0.05") < rate < Decimal("0.5")


def test_the_arithmetic_context_of_the_process_changes_nothing():
    case = {item["id"]: item for item in FILE["xirr"]}["X2"]
    flows = flows_in(case)
    want = summary(flows, case["value_paise"], case["valuation_day"])
    rate = xirr_rate([(20230403, -5_000_000), (20250101, 9_000_000)])
    for prec, rounding in ((3, ROUND_DOWN), (7, ROUND_DOWN), (100, ROUND_DOWN), (28, "ROUND_UP")):
        with localcontext() as ctx:
            ctx.prec, ctx.rounding = prec, rounding
            ctx.traps[Inexact] = True
            assert summary(flows, case["value_paise"], case["valuation_day"]) == want
            assert xirr_rate([(20230403, -5_000_000), (20250101, 9_000_000)]) == rate
            assert twr(
                [Point(20261103, 1_050_000, 1_000_000), Point(20261104, 1_029_000)], 0
            ).as_dict() == {
                "start_day": 20261103,
                "pct": "2.9000",
                "annualised_pct": None,
                "words": "TWR since 3 Nov 2026",
            }


def test_the_figures_hold_whole_numbers_and_texts_and_never_a_float():
    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                assert isinstance(key, str)
                yield from walk(value)
        elif isinstance(node, list | tuple):
            for value in node:
                yield from walk(value)
        else:
            yield node

    for case in FILE["xirr"]:
        for leaf in walk(summary(flows_in(case), case["value_paise"], case["valuation_day"])):
            assert leaf is None or type(leaf) in (int, str), leaf
    for case in FILE["twr"]:
        found = periods(points_in(case), case["opening_paise"])
        leaves = [*walk(twr(points_in(case), case["opening_paise"]).as_dict())]
        leaves += [leaf for period in found for leaf in (*period, period.pct)]
        assert all(leaf is None or type(leaf) in (int, str) for leaf in leaves)
    source = (Path(__file__).parents[1] / "core" / "performance.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id == "float"]
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and type(n.value) is float]
    assert "import math" not in source


# -- the time-weighted return, on random series ----------------------------------------------------


def random_series(rng):
    """A daily series, as the vectors have it: some days incomplete, money moving on some, a few
    days given twice, and the order scrambled."""
    day, points = 20261001, []
    for _ in range(rng.randint(1, 14)):
        day = day_plus(day, rng.choice([1, 1, 1, 2, 3, 40, 200]))
        value = (
            None
            if rng.random() < 0.25
            else (0 if rng.random() < 0.1 else rng.randint(1, 3_000_000))
        )
        point = {"day": day, "value_paise": value, "in_paise": 0, "out_paise": 0, "fee_paise": 0}
        for key in ("in_paise", "out_paise", "fee_paise"):
            if rng.random() < 0.3:
                point[key] = rng.randint(1, 1_500_000)
        points.append(point)
        if rng.random() < 0.1:  # the same day again, with other money
            points.append(
                {
                    **point,
                    "value_paise": None,
                    "in_paise": rng.randint(1, 900),
                    "out_paise": 0,
                    "fee_paise": 0,
                }
            )
    rng.shuffle(points)
    return points


def test_the_time_weighted_return_of_random_series_is_the_exact_oracles_and_the_generators():
    rng = random.Random(20261105)
    shown = long_ones = 0
    for _ in range(400):
        points = random_series(rng)
        opening = rng.choice([None, 0, 0, 1_000_000])
        engine = [Point.of(p) for p in points]
        found = periods(engine, opening)
        got = oracle_twr(points, opening)
        assert [(p.start, p.day, p.base, p.grown) for p in found] == got["stretches"]
        figure = twr(engine, opening)
        for key in ("start_day", "pct", "annualised_pct"):
            assert getattr(figure, key) == got["figure"][key], key
        mine, theirs = GEN.twr_of(points, opening, "random")
        assert [(p["start"], p["day"], p["base"], p["grown"]) for p in mine] == got["stretches"]
        assert theirs["pct"] == figure.pct and theirs["annualised_pct"] == figure.annualised_pct
        if found:
            assert figure.words == words.twr_since(found[0].start)
            assert all(p.base > 0 and 0 <= p.grown for p in found)
            assert all(a.day <= b.day for a, b in zip(found, found[1:], strict=False))
        else:
            assert figure == Twr(words=words.TWR_NEEDS_PRICES)
        shown += bool(found)
        long_ones += figure.annualised_pct is not None
    assert shown > 200 and long_ones >= 20, (shown, long_ones)


def clean_series(rng, count=8):
    """Closing values that are all there and above nothing, with money moving on some days."""
    day, points = 20261001, []
    value = rng.randint(100_000, 2_000_000)
    for _ in range(count):
        day = day_plus(day, rng.choice([1, 1, 2, 5]))
        value = max(1, value + rng.randint(-value // 4, value // 3))
        points.append(Point(day, value, rng.choice([0, 0, rng.randint(1, 500_000)])))
    return points


def test_the_order_and_the_splitting_of_the_points_change_nothing():
    rng = random.Random(20261106)
    for _ in range(100):
        points = clean_series(rng)
        base = twr(points, 0)
        shuffled = list(points)
        rng.shuffle(shuffled)
        assert twr(shuffled, 0) == base
        halves = []
        for point in points:  # the money on one point of the day, the value on another
            halves.append(Point(point.day, None, point.in_paise, point.out_paise, point.fee_paise))
            halves.append(Point(point.day, point.value_paise))
        rng.shuffle(halves)
        assert twr(halves, 0) == base
        assert periods(halves, 0) == periods(points, 0)


def test_deposits_and_withdrawals_in_proportion_change_no_time_weighted_return():
    # Put half the value in at the start of a day and the whole portfolio, new money included,
    # earns what it would have earned anyway; take a third of it out at the close and what is left
    # earns what it would have earned. The time-weighted return is the one number that money
    # moving about cannot change, and here the closing values are built so that it is exact.
    rng = random.Random(20261107)
    for _ in range(120):
        base = [rng.randint(1, 3_000) * 2**10 * 3**10 for _ in range(rng.randint(3, 9))]
        plain = [Point(day_plus(20261001, i), value) for i, value in enumerate(base)]
        scale, moved = Fraction(1), [plain[0]]
        for i in range(1, len(base)):
            before = scale * base[i - 1]
            deposit = before / 2 if rng.random() < 0.4 else Fraction(0)
            scale *= 1 + deposit / before
            close = scale * base[i]
            taken = close / 3 if rng.random() < 0.4 else Fraction(0)
            scale *= 1 - taken / close
            point = (close - taken, deposit, taken)
            assert all(each.denominator == 1 for each in point)
            moved.append(Point(plain[i].day, int(point[0]), int(point[1]), int(point[2])))
        figure = twr(plain)
        assert twr(moved) == figure
        assert [p.pct for p in periods(moved)] == [p.pct for p in periods(plain)]


def test_a_series_with_no_money_moving_grows_as_its_values_do():
    rng = random.Random(20261108)
    for _ in range(100):
        values = [rng.randint(1_000, 9_000_000) for _ in range(rng.randint(2, 12))]
        points = [Point(day_plus(20261001, i), v) for i, v in enumerate(values)]
        figure = twr(points)  # nothing before the first close: it is where the return starts
        assert figure.start_day == 20261001
        assert figure.pct == GEN.pct4(values[-1] - values[0], values[0])
        assert twr(points, values[0]).pct == figure.pct
        scaled = [Point(p.day, p.value_paise * 37) for p in points]
        assert twr(scaled) == figure


def test_the_money_can_be_counted_in_any_unit():
    rng = random.Random(20261109)
    for _ in range(80):
        points = clean_series(rng)
        scaled = [
            Point(p.day, p.value_paise * 53, p.in_paise * 53, p.out_paise * 53, p.fee_paise * 53)
            for p in points
        ]
        assert twr(scaled, 0) == twr(points, 0)
        assert [p.pct for p in periods(scaled, 0)] == [p.pct for p in periods(points, 0)]


def test_a_very_long_series_does_not_turn_a_huge_whole_number_into_text():
    # 3,000 periods multiply into a whole number of tens of thousands of digits; nothing may turn
    # it into text (Python refuses past 4,300 digits) and the answer is still exact.
    points = [Point(day_plus(20100101, i), 1_000_000 + (i * 7919) % 100_000) for i in range(3000)]
    found = periods(points)
    product = 1
    for period in found:
        product *= period.grown
    assert product.bit_length() > 4_300 * 3.33
    figure = twr(points)
    assert figure.start_day == 20100101
    series = [
        {"day": p.day, "value_paise": p.value_paise, "in_paise": 0, "out_paise": 0, "fee_paise": 0}
        for p in points
    ]
    got = oracle_twr(series)["figure"]
    assert (figure.pct, figure.annualised_pct) == (got["pct"], got["annualised_pct"])
    assert figure.annualised_pct is not None


def test_a_return_that_lost_everything_stays_lost_even_beyond_a_year():
    points = [
        Point(20250101, 1_000_000, 1_000_000),
        Point(20250102, 0),
        Point(20270101, 5_000, 5_000),
    ]
    figure = twr(points, 0)
    assert figure.pct == "-100.0000"
    assert figure.annualised_pct == "-100.0000"


def test_a_fee_cannot_lose_more_than_everything_at_work():
    found = periods([Point(20261103, 10_000, 10_000), Point(20261104, 10_000, 0, 0, 25_000)], 0)
    assert [(p.base, p.grown, p.pct) for p in found] == [
        (10_000, 10_000, "0.0000"),
        (10_000, 0, "-100.0000"),
    ]


def test_a_period_is_what_it_ended_with_over_what_it_began_with():
    found = periods([Point(20261103, 1_050_000, 1_000_000), Point(20261104, 1_029_000)], 0)
    assert [tuple(p) for p in found] == [
        (20261103, 20261103, 1_000_000, 1_050_000),
        (20261103, 20261104, 1_050_000, 1_029_000),
    ]
    assert [p.pct for p in found] == ["5.0000", "-2.0000"]
    assert Twr().as_dict() == {
        "start_day": None,
        "pct": None,
        "annualised_pct": None,
        "words": None,
    }


# -- the shapes the phone reads -----------------------------------------------------------------------


def test_the_figures_have_the_members_the_phone_reads():
    text = TYPES_TS.read_text(encoding="utf-8")
    found = interfaces(text)
    assert list(Xirr("none").as_dict()) == members(found["XirrFigure"])
    assert list(Twr().as_dict()) == members(found["TwrFigure"])
    assert list(Fy(2025, "FY 2025-26", 0, 0, 0).as_dict()) == members(found["FyFigure"])
    report = members(found["PerformanceReport"])
    keys = list(summary([], 0, 20261027))
    assert keys == report[: len(keys)]
    assert set(report) - set(keys) == {
        "twr",
        "benchmarks",
        "series",
        "by_fy",
        "income",
        "series_words",
    }
    income = income_report(ledger_of([]), 20261027)
    assert list(income["by_month"][0]) == members(found["MonthIncome"])
    assert list(income) == ["this_fy_paise", "by_month"]


def test_the_sentences_a_trader_reads_are_in_words_py():
    assert words.XIRR_NEEDS_A_PURCHASE == "Return needs at least one purchase and a value."
    assert words.XIRR_NO_RATE == "These purchases and sales do not give a single yearly return."
    assert words.TWR_NEEDS_PRICES == "TWR needs closing prices for at least two days."
    assert words.xirr_later(20270501) == "XIRR appears after one year (from 1 May 2027)."
    assert words.twr_since(20261103) == "TWR since 3 Nov 2026"
