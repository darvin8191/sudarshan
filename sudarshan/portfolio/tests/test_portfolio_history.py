"""core/history.py (PA5): the daily value of a tab, and the index its money is held against.

Four witnesses, as for the ledger and the performance figures. The golden vectors
(vectors/history.json) are worked out by hand and by an evaluator in make_pa5.py that imports
nothing from sudarshan: it replays the entries again for every single day, reads every price list
from the back one price at a time, keeps the index units as exact fractions and finds a rate by
walking out from zero. The naive oracle in portfolio_oracle.py reads the same rules a third way
(the ledger replayed once for each day the entries change, prices found by scanning, business days
counted one at a time). Both are run against the engine on random tabs with random closing prices.
Identities that must hold whatever the numbers are close the gaps: the last day of a series is
what the valuation module adds up, its money put in is what the performance module counts, an
index fund bought at the index's own close is worth what the same money in the index is worth,
and more prices only ever add days.
"""

from __future__ import annotations

import itertools
import json
import math
import random
import re
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import date
from decimal import ROUND_DOWN, Decimal, Inexact, localcontext
from fractions import Fraction
from pathlib import Path

import pytest
from portfolio_oracle import flows as oracle_flows
from portfolio_oracle import history as oracle_history
from portfolio_oracle import index_comparison, ordinal, price_of, shift
from portfolio_oracle import twr as oracle_twr
from portfolio_ts import interfaces, members
from test_portfolio_ledger import DECIMALS, random_rows, repaired
from test_portfolio_performance import lattice_step, spread
from test_portfolio_vectors import VECTORS, load

from sudarshan.portfolio.core import kinds, performance, words
from sudarshan.portfolio.core.days import from_date
from sudarshan.portfolio.core.history import (
    Benchmark,
    Daily,
    Quotes,
    SeriesPoint,
    benchmark,
    daily,
    price_on,
    report,
)
from sudarshan.portfolio.core.ledger import replay
from sudarshan.portfolio.core.model import Entry
from sudarshan.portfolio.core.performance import (
    Flow,
    Point,
    by_financial_year,
    flows_of,
    income_report,
    summary,
    totals,
    twr,
)
from sudarshan.portfolio.core.valuation import MODE_NONE, Fixed, Line, Row, tab_totals

FILE = json.loads((VECTORS / "history.json").read_text(encoding="utf-8"))
GEN = load("make_pa5.py")  # the independent evaluator
TYPES_TS = Path(__file__).resolve().parents[3] / "frontend/src/sudarshan/portfolio/types.ts"
U = kinds.QTY_SCALE
NIFTY = "nifty50"


def weekday(day: int) -> int:
    """0 for a Monday."""
    return date.fromordinal(ordinal(day)).weekday()


def decimals_in(case):
    return {int(asset): places for asset, places in case.get("decimals", {}).items()}


def replayed(rows, as_of=None, decimals=None):
    return replay(
        [Entry.of(row) for row in rows],
        as_of=as_of,
        decimals=DECIMALS if decimals is None else decimals,
    )


def book_in(case):
    """The prices of a case as the oracle and the evaluator read them: ``{asset: (kind, prices)}``."""
    return {
        quote["asset_id"]: (quote["kind"], [tuple(price) for price in quote["prices"]])
        for quote in case["quotes"]
    }


def quotes_of(book):
    """The same prices as the engine reads them."""
    return {
        asset: Quotes.of({"kind": kind, "prices": prices}) for asset, (kind, prices) in book.items()
    }


def closes_in(found):
    return [tuple(close) for close in found]


def span_in(case):
    return tuple(case["span"]) if "span" in case else None


def flows_in(case):
    return [Flow.of(flow) for flow in case["flows"]]


def points_as_dicts(points):
    return [asdict(point) for point in points]


# -- the prices a day may use -------------------------------------------------------------------------


def test_the_file_has_what_the_design_needs():
    wanted = {"quotes": 11, "series": 16, "benchmarks": 17, "reports": 3}
    assert {name: len(FILE[name]) for name in wanted} == wanted
    for section in wanted:
        cases = FILE[section]
        assert all(case["title"] and case["why"] for case in cases), section
        assert len({case["id"] for case in cases}) == len(cases), section
    assert [case["id"] for case in FILE["series"]] == [f"S{n}" for n in range(1, 17)]
    assert [case["id"] for case in FILE["benchmarks"]] == [f"B{n}" for n in range(1, 18)]
    assert [case["id"] for case in FILE["quotes"]] == [f"Q{n}" for n in range(1, 12)]
    assert [case["id"] for case in FILE["reports"]] == ["RP1", "RP2", "RP3"]


def test_the_design_numbers_are_in_the_file():
    series = {case["id"]: case["expect"] for case in FILE["series"]}
    assert series["S1"]["twr"]["pct"] == "5.0000"  # the design's T4, from the entries up
    assert [point["value_paise"] for point in series["S1"]["series"]] == [1_000_000, 1_155_000]
    bench = {case["id"]: case["expect"] for case in FILE["benchmarks"]}
    assert (bench["B1"]["value_paise"], bench["B1"]["xirr_pct"], bench["B1"]["twr_pct"]) == (
        8_389_985,
        "11.7347",
        "48.8673",
    )
    assert (bench["B2"]["value_paise"], bench["B2"]["flagged"]) == (-525_000, True)
    assert bench["B2"]["xirr_pct"] is None
    for name, shown in (("B1", False), ("B2", True), ("B3", True), ("B4", True), ("B5", True)):
        assert bench[name]["flagged"] is shown, name


@pytest.mark.parametrize("case", FILE["quotes"], ids=lambda case: case["id"])
def test_the_price_a_day_may_use_is_the_vectors(case):
    quotes, holidays = Quotes.of(case), case.get("holidays", ())
    for day, want in case["asks"]:
        assert price_on(quotes, day, holidays) == want, day


@pytest.mark.parametrize("case", FILE["quotes"], ids=lambda case: case["id"])
def test_the_scanning_oracle_and_the_evaluator_price_a_day_the_same(case):
    prices, holidays = [tuple(price) for price in case["prices"]], case.get("holidays", ())
    for day, want in case["asks"]:
        assert price_of(case["kind"], prices, day, holidays) == want, day
        assert GEN.price_for(case["kind"], prices, day, holidays) == want, day


def test_prices_may_come_in_any_order_and_the_later_of_two_for_a_day_wins():
    quotes = Quotes.of({"kind": "listed", "prices": [[20260106, 2], [20260105, 1], [20260105, 3]]})
    assert quotes.days == (20260105, 20260105, 20260106)
    assert quotes.prices_u == (1, 3, 2)
    assert price_on(quotes, 20260105) == 3
    assert price_on(quotes, 20260106) == 2


def test_a_kind_that_holds_no_value_has_no_prices_to_read():
    for kind in kinds.HOLDING_KINDS:
        assert Quotes.of({"kind": kind, "prices": []}).kind == kind
    for kind in ("liability", "bond", "", None):
        with pytest.raises(ValueError, match="no prices are kept"):
            Quotes.of({"kind": kind, "prices": [[20260105, 1]]})


def test_nothing_is_priced_without_a_price_and_the_holidays_may_be_any_iterable():
    empty = Quotes.of({"kind": "mf", "prices": []})
    assert price_on(empty, 20260105) is None
    nav = Quotes.of({"kind": "mf", "prices": [[20260105, 450_000]]})
    # Fri 9 Jan is the 4th business day after Mon 5 Jan, the 3rd with Wed 7 Jan shut.
    assert price_on(nav, 20260109) is None
    assert price_on(nav, 20260109, [20260107]) == 450_000
    assert price_on(nav, 20260109, iter([20260107])) == 450_000
    assert price_on(nav, 20260109, (d for d in (20260107,))) == 450_000
    assert price_on(nav, 20260109, {20260107, 20260110, 20990101, 20200101}) == 450_000
    assert price_on(nav, 20260109, [20260110, 20260107, 20260107]) == 450_000


def test_the_holidays_may_be_given_in_any_order():
    # Every order of the holidays of every vector that has some gives that vector's answers.
    for case in FILE["quotes"]:
        holidays = case.get("holidays", ())
        if len(holidays) < 2:
            continue
        quotes = Quotes.of(case)
        for order in itertools.permutations(holidays):
            for day, want in case["asks"]:
                assert price_on(quotes, day, order) == want, (case["id"], order, day)
    # and a long list of them, shuffled, on a fund priced once a week
    rng = random.Random(20261109)
    days = [shift(20260105, n) for n in range(60)]
    nav = Quotes.of({"kind": "mf", "prices": [[days[7 * n], 450_000 + n] for n in range(8)]})
    shut = [day for n, day in enumerate(days) if weekday(day) < 5 and n % 3 == 1]
    base = [price_on(nav, day, sorted(shut)) for day in days]
    assert None in base and any(price is not None for price in base)
    for _ in range(40):
        rng.shuffle(shut)
        assert [price_on(nav, day, shut) for day in days] == base


# -- the daily series --------------------------------------------------------------------------------


def daily_of(case, as_of=True):
    last = case["last_day"]
    ledger = replayed(case["entries"], last if as_of else None, decimals_in(case))
    return daily(ledger, quotes_of(book_in(case)), last, case.get("holidays", ()))


@pytest.mark.parametrize("case", FILE["series"], ids=lambda case: case["id"])
def test_the_daily_series_of_a_tab_is_the_vectors(case):
    got, want = daily_of(case), case["expect"]
    assert [point.as_dict() for point in got.series] == want["series"]
    assert points_as_dicts(got.points) == want["points"]
    assert got.opening_paise == want["opening_paise"]
    assert got.words == want["words"]
    assert got.twr().as_dict() == want["twr"]
    assert got.span() == (None if want["span"] is None else tuple(want["span"]))


@pytest.mark.parametrize("case", FILE["series"], ids=lambda case: case["id"])
def test_the_whole_ledger_gives_the_same_days_as_the_ledger_to_the_day_asked_for(case):
    assert daily_of(case, as_of=False) == daily_of(case)


@pytest.mark.parametrize("case", FILE["series"], ids=lambda case: case["id"])
def test_the_naive_oracle_gives_the_same_days(case):
    got = oracle_history(
        case["entries"],
        book_in(case),
        case["last_day"],
        case.get("holidays", ()),
        decimals_in(case),
    )
    want = case["expect"]
    assert got["series"] == want["series"]
    assert got["points"] == want["points"]
    assert got["opening"] == want["opening_paise"]
    theirs = oracle_twr(got["points"], got["opening"])
    for key in ("start_day", "pct", "annualised_pct"):
        assert theirs["figure"][key] == want["twr"][key], key
    stretches = theirs["stretches"]
    span = (stretches[0][0], stretches[-1][1]) if stretches else None
    assert span == (None if want["span"] is None else tuple(want["span"]))


@pytest.mark.parametrize("case", FILE["series"], ids=lambda case: case["id"])
def test_the_evaluator_gives_the_same_days(case):
    got = GEN.history_of(case["entries"], book_in(case), case["last_day"], case.get("holidays", ()))
    want = case["expect"]
    assert got["series"] == want["series"]
    assert got["points"] == want["points"]
    assert got["opening"] == want["opening_paise"]
    assert got["words"] == want["words"]
    span = GEN.twr_of(got["points"], got["opening"])[1]
    assert (None if span is None else list(span)) == want["span"]


def test_a_day_is_complete_when_what_is_held_at_its_end_has_a_price_and_not_before():
    # S8: sold out on Wed 7 Jan, priced only on Mon and Tue. Wednesday and Thursday hold nothing,
    # so they need no price and are worth nothing; they are complete.
    case = next(case for case in FILE["series"] if case["id"] == "S8")
    got = daily_of(case)
    assert [point.day for point in got.series] == [20260105, 20260106, 20260107, 20260108]
    assert [point.value_paise for point in got.series] == [100_000, 105_000, 0, 0]
    # and S12: nothing priced at all
    case = next(case for case in FILE["series"] if case["id"] == "S12")
    got = daily_of(case)
    assert got.series == () and got.words == words.SERIES_WAITING
    assert got.opening_paise is None
    assert got.twr().words == words.TWR_NEEDS_PRICES


def test_a_tab_with_no_money_has_no_history_and_says_nothing_about_it():
    nothing = daily(replayed([]), {}, 20261027)
    assert nothing == Daily()
    assert nothing.span() is None
    assert nothing.twr().words == words.TWR_NEEDS_PRICES
    row = {"id": 1, "seq": 1, "kind": "BUY", "trade_date": 20261105, "account_id": 1}
    row |= {"asset_id": 1, "qty_u": U, "amount_paise": 100}
    early = daily(replayed([row]), quotes_of({1: ("manual", [(20261001, 5)])}), 20261104)
    assert early == Daily()  # asked for the day before the first money


def test_prices_of_assets_not_held_or_dated_after_the_day_change_nothing():
    case = next(case for case in FILE["series"] if case["id"] == "S1")
    book = book_in(case)
    base = daily_of(case)
    book[9] = ("listed", [(20260105, 7), (20260106, 8)])  # an asset nobody holds
    book[1] = (book[1][0], [*book[1][1], (20260108, 1_234_567), (20260109, 1)])  # after the day
    ledger = replayed(case["entries"], case["last_day"], decimals_in(case))
    assert daily(ledger, quotes_of(book), case["last_day"]) == base


# -- the index ---------------------------------------------------------------------------------------


def bench_of(case):
    return benchmark(
        case["benchmark"],
        flows_in(case),
        closes_in(case["closes"]),
        case["valuation_day"],
        case.get("now_u"),
        span_in(case),
    )


@pytest.mark.parametrize("case", FILE["benchmarks"], ids=lambda case: case["id"])
def test_the_same_money_put_into_an_index_is_the_vectors(case):
    assert bench_of(case).as_dict() == case["expect"]


@pytest.mark.parametrize("case", FILE["benchmarks"], ids=lambda case: case["id"])
def test_the_exact_oracle_gives_the_same_index_figures(case):
    closes = closes_in(case["closes"])
    got = index_comparison(
        case["flows"], closes, case["valuation_day"], case.get("now_u"), span_in(case)
    )
    want = case["expect"]
    assert got["value_paise"] == want["value_paise"]
    assert got["twr_pct"] == want["twr_pct"]
    assert (got["early"] or got["short"] or not any(c > 0 for _, c in closes)) == want["flagged"]
    if want["xirr_pct"] is None:
        assert not got["roots"]
    else:
        assert float(want["xirr_pct"]) / 100 == pytest.approx(got["roots"][0], abs=6e-7)


@pytest.mark.parametrize("case", FILE["benchmarks"], ids=lambda case: case["id"])
def test_the_evaluator_gives_the_same_index_figures(case):
    got = GEN.benchmark_of(
        case["benchmark"],
        case["flows"],
        closes_in(case["closes"]),
        case["valuation_day"],
        case.get("now_u"),
        span_in(case),
    )
    assert got == case["expect"]


def test_a_withdrawal_the_index_could_not_pay_is_kept_and_flagged():
    case = next(case for case in FILE["benchmarks"] if case["id"] == "B2")
    got = bench_of(case)
    assert got.value_paise < 0 and got.flagged
    assert got.words == [words.DIVIDENDS_NOT_INCLUDED, words.INDEX_COULD_NOT_PAY]
    ok = next(case for case in FILE["benchmarks"] if case["id"] == "B6")
    assert bench_of(ok).flagged is False


def test_an_index_without_prices_for_the_first_day_says_when_its_prices_start():
    got = bench_of(next(case for case in FILE["benchmarks"] if case["id"] == "B4"))
    assert got.value_paise is None and got.xirr_pct is None and got.flagged
    assert got.words[1] == words.index_starts("NIFTY 500", 20260102)
    got = bench_of(next(case for case in FILE["benchmarks"] if case["id"] == "B5"))
    assert got.value_paise is None and got.flagged
    assert got.words[1] == words.index_missing("NIFTY 50")


def test_money_that_did_not_move_buys_nothing_and_a_tab_with_none_has_no_comparison():
    closes = [(20260105, 200_000_000), (20260106, 210_000_000)]
    for flows in ([], [Flow(20260105)], [Flow(20260105), Flow(20260106)]):
        got = benchmark(NIFTY, flows, closes, 20260106)
        assert (got.value_paise, got.xirr_pct, got.flagged) == (None, None, False), flows
    got = benchmark(NIFTY, [Flow(20260105), Flow(20260106, in_paise=100_000)], closes, 20260106)
    assert got.value_paise == 100_000 and got.flagged is False


def test_a_name_that_is_not_an_index_is_refused():
    with pytest.raises(KeyError):
        benchmark("sensex", [], [(20260105, 1)], 20260105)


# -- the whole report --------------------------------------------------------------------------------


def report_of(case):
    day = case["valuation_day"]
    closes = {name: closes_in(found) for name, found in case["closes"].items()}
    return report(
        replayed(case["entries"], day),
        day,
        case["value_paise"],
        quotes_of(book_in(case)),
        closes,
        case.get("now_u"),
        case.get("holidays", ()),
    )


@pytest.mark.parametrize("case", FILE["reports"], ids=lambda case: case["id"])
def test_a_report_is_the_vectors(case):
    got = report_of(case)
    for key, want in case["expect"].items():
        assert got[key] == want, key
    day = case["valuation_day"]
    ledger = replayed(case["entries"], day)
    money = summary(flows_of(ledger), case["value_paise"], day)
    assert {
        key: got[key] for key in money
    } == money  # the money figures are the performance module's
    assert got["by_fy"] == [fy.as_dict() for fy in by_financial_year(ledger)]
    assert got["income"] == income_report(ledger, day)
    assert [each["benchmark"] for each in got["benchmarks"]] == list(kinds.BENCHMARKS)


def test_the_report_has_the_members_and_the_order_the_phone_reads():
    found = interfaces(TYPES_TS.read_text(encoding="utf-8"))
    case = FILE["reports"][0]
    assert list(report_of(case)) == members(found["PerformanceReport"])
    assert list(Benchmark("nifty50", "NIFTY 50", None, None, None, False, []).as_dict()) == members(
        found["BenchmarkFigure"]
    )
    assert list(SeriesPoint(20260105, 1, 2).as_dict()) == members(found["SeriesPoint"])
    assert list(SeriesPoint(20260105, 1, 2)._fields) == members(found["SeriesPoint"])


def no_float(value, where="report"):
    """Whole numbers, texts, booleans and nothing else carry a figure."""
    if isinstance(value, dict):
        for key, each in value.items():
            assert isinstance(key, str), (where, key)
            no_float(each, f"{where}.{key}")
    elif isinstance(value, (list, tuple)):
        for number, each in enumerate(value):
            no_float(each, f"{where}[{number}]")
    else:
        assert value is None or type(value) in (int, str, bool), (where, value)


def test_the_figures_hold_whole_numbers_and_texts_and_never_a_float():
    for case in FILE["reports"]:
        no_float(report_of(case))
    for case in FILE["benchmarks"]:
        no_float(bench_of(case).as_dict(), case["id"])
    for case in FILE["series"]:
        got = daily_of(case)
        no_float([point.as_dict() for point in got.series], case["id"])
        no_float(got.twr().as_dict(), case["id"])


def test_the_arithmetic_context_of_the_process_changes_nothing():
    cases = [case for case in FILE["benchmarks"] if case["expect"]["xirr_pct"] is not None]
    assert cases
    with localcontext() as ctx:
        ctx.prec, ctx.rounding = 4, ROUND_DOWN
        ctx.traps[Inexact] = True
        for case in cases:
            assert bench_of(case).as_dict() == case["expect"], case["id"]
        for case in FILE["reports"]:
            for key, want in case["expect"].items():
                assert report_of(case)[key] == want, (case["id"], key)


def test_the_sentences_a_trader_reads_are_in_words_py():
    assert (
        words.SERIES_WAITING == "Daily history starts once every holding here has a closing price."
    )
    assert words.series_from(20261103) == (
        "Daily history from 3 Nov 2026, the first day every holding here had a closing price."
    )
    assert words.DIVIDENDS_NOT_INCLUDED == "Dividends not included"
    assert words.INDEX_COULD_NOT_PAY == (
        "The index could not have paid every withdrawal; the comparison is approximate."
    )
    assert words.index_starts("NIFTY 50", 20230403) == (
        "NIFTY 50 prices start 3 Apr 2023; this comparison needs every entry after that."
    )
    assert words.index_missing("NIFTY 500") == (
        "NIFTY 500 prices are not available yet; the comparison appears when they are."
    )


#: Developer words and the feed's own words: none of these sentences says one.
NOT_HERE = re.compile(
    r"\b(arm|armed|arming|paper|virtual|live|real-time|http|exception|traceback|none|nan)\b",
    re.IGNORECASE,
)


def test_the_sentences_are_plain_words_with_no_developer_word_in_them():
    sentences = [
        words.SERIES_WAITING,
        words.series_from(20261103),
        words.DIVIDENDS_NOT_INCLUDED,
        words.INDEX_COULD_NOT_PAY,
        words.index_starts("NIFTY 50", 20230403),
        words.index_missing("NIFTY 500"),
    ]
    for sentence in sentences:
        assert sentence.isascii() and sentence == sentence.strip() and "  " not in sentence
        assert not NOT_HERE.search(sentence), sentence
        assert "{" not in sentence and "None" not in sentence and not sentence.endswith("..")


# -- random tabs, random prices ---------------------------------------------------------------------

KINDS = list(kinds.HOLDING_KINDS)


def dated_tab(rng, factor=1):
    """A random tab the ledger accepts, every transfer out to outside giving its value (the
    evaluator's reading of the money needs it said)."""
    rows = repaired(random_rows(rng, rng.randint(2, 40)))
    if factor != 1:
        rows = spread(rows, factor)
    for row in rows:
        if row["kind"] == "TRANSFER" and row.get("account_id") and not row.get("to_account_id"):
            row.setdefault("value_paise", rng.randint(100, 400_000))
    return rows


def quote_book(rng, rows, last_day):
    """Prices for assets 1 and 2: of any kind, on some of the days from just before the first
    entry to just after the last day, a few days twice, in any order, or none at all."""
    first = min(row["trade_date"] for row in rows)
    book = {}
    for asset in (1, 2):
        if rng.random() < 0.04:
            continue  # an asset with no price list at all
        kind = rng.choice(KINDS)
        chance = rng.choice([0.5, 0.8, 0.95, 1.0])
        begin = shift(first, rng.choice([-6, -1, 0, 0, 4, 20]))
        end = shift(last_day, rng.choice([0, 0, 3]))
        count = max(0, ordinal(end) - ordinal(begin))
        prices = []
        if kind == "manual":
            for _ in range(rng.randint(0, 6)):
                prices.append((shift(begin, rng.randint(0, count)), rng.randint(10_000, 5_000_000)))
        else:
            for step in range(count + 1):
                if rng.random() < (chance if kind == "listed" else chance / 2):
                    prices.append((shift(begin, step), rng.randint(10_000, 5_000_000)))
        if prices and rng.random() < 0.25:
            prices.append((rng.choice(prices)[0], rng.randint(10_000, 5_000_000)))
        if rng.random() < 0.08:
            prices = []
        if rng.random() < 0.5:
            rng.shuffle(prices)
        book[asset] = (kind, prices)
    return book


def random_case(rng, factor=1):
    """``(rows, book, last_day, holidays)``: a tab, its prices, the day asked for and the exchange's holidays."""
    rows = dated_tab(rng, factor)
    if not rows:
        return rows, {}, 20260105, []
    days = sorted(row["trade_date"] for row in rows)
    last_day = max(shift(days[-1], rng.choice([-40, -10, -3, 0, 0, 0, 6, 25])), shift(days[0], -2))
    book = quote_book(rng, rows, last_day)
    count = ordinal(last_day) - ordinal(days[0]) + 12
    holidays = [
        day
        for day in (shift(days[0], -6 + step) for step in range(max(count, 0)))
        if rng.random() < 0.06
    ]
    return rows, book, last_day, holidays


def test_the_daily_series_of_random_tabs_is_the_naive_oracles_and_the_evaluators():
    rng = random.Random(20261110)
    seen = Counter()
    for number in range(110):
        rows, book, last_day, holidays = random_case(rng, rng.choice([1, 1, 1, 1, 1, 2, 4]))
        engine = daily(replayed(rows, last_day), quotes_of(book), last_day, holidays)
        naive = oracle_history(rows, book, last_day, holidays, DECIMALS)
        assert [p.as_dict() for p in engine.series] == naive["series"]
        assert points_as_dicts(engine.points) == naive["points"]
        assert engine.opening_paise == naive["opening"]
        series, first = naive["series"], naive["first"]
        if first is None:
            said = None
        elif not series:
            said = words.SERIES_WAITING
        elif series[0]["day"] > first:
            said = words.series_from(series[0]["day"])
        else:
            said = None
        assert engine.words == said
        figure, theirs = engine.twr(), oracle_twr(naive["points"], naive["opening"])
        for key in ("start_day", "pct", "annualised_pct"):
            assert getattr(figure, key) == theirs["figure"][key], key
        assert (engine.span() is not None) == bool(theirs["stretches"])
        if theirs["stretches"]:
            assert engine.span() == (theirs["stretches"][0][0], theirs["stretches"][-1][1])
        if number % 3 == 0 and first is not None:  # the evaluator replays every day again
            mine = GEN.history_of(rows, book, last_day, holidays)
            assert mine["series"] == naive["series"]
            assert mine["points"] == naive["points"]
            assert mine["opening"] == naive["opening"]
            assert mine["words"] == said
            span = engine.span()
            if span is None or ordinal(span[1]) - ordinal(span[0]) <= 365:  # it has no power
                assert GEN.twr_of(mine["points"], mine["opening"])[0]["pct"] == figure.pct
        seen["no_money" if first is None else "waiting" if not series else "series"] += 1
        seen["later"] += bool(series and series[0]["day"] > (first or 0))
        seen["from_the_start"] += bool(series and series[0]["day"] == first)
        seen["gaps"] += any(point["value_paise"] is None for point in naive["points"])
        seen["annualised"] += figure.annualised_pct is not None
        seen["sold_out_days"] += any(point["value_paise"] == 0 for point in series)
        seen["has_a_fund"] += any(kind == "mf" for kind, _ in book.values())
    assert seen["series"] >= 60 and seen["waiting"] >= 3 and seen["no_money"] >= 1, seen
    assert seen["later"] >= 10 and seen["from_the_start"] >= 25 and seen["gaps"] >= 30, seen
    assert seen["annualised"] >= 2 and seen["sold_out_days"] >= 2 and seen["has_a_fund"] >= 25, seen


def test_the_days_of_a_series_follow_from_the_entries_and_the_prices():
    rng = random.Random(20261111)
    for _ in range(120):
        rows, book, last_day, holidays = random_case(rng, rng.choice([1, 1, 2]))
        ledger = replayed(rows)  # all of it: the entries after the day asked for are not seen
        got = daily(ledger, quotes_of(book), last_day, holidays)
        assert got == daily(replayed(rows, last_day), quotes_of(book), last_day, holidays)
        flows = [flow for flow in flows_of(ledger) if flow.day <= last_day]
        days = [point.day for point in got.points]
        assert days == sorted(set(days))
        assert {flow.day for flow in flows} <= set(days)
        assert all(not flows or flows[0].day <= day <= last_day for day in days)
        complete = [point.day for point in got.series]
        assert complete == sorted(set(complete))
        by_day = {point.day: point for point in got.points}
        for flow in flows:  # every flow day carries its day's money, whether priced or not
            point = by_day[flow.day]
            assert (point.in_paise, point.out_paise, point.fee_paise) == (
                flow.in_paise,
                flow.out_paise,
                flow.fee_paise,
            )
        for point in got.points:
            assert (point.value_paise is None) == (point.day not in complete)
            assert point.day in complete or point.day in {flow.day for flow in flows}
        sums = totals(flows)
        assert sum(point.in_paise for point in got.points) == sums.in_paise
        assert sum(point.out_paise for point in got.points) == sums.out_paise
        assert sum(point.fee_paise for point in got.points) == sums.fee_paise
        for each in got.series:  # the money put in by each day is the performance module's
            assert each.net_put_in_paise == totals(flows, through=each.day).net_put_in
            assert each.value_paise >= 0
        if flows:
            begins = complete[0] if complete else None
            assert got.opening_paise == (0 if begins == flows[0].day else None)
            assert (got.words is None) == (begins == flows[0].day)
        else:
            assert got == Daily()


def test_the_last_day_of_a_series_is_what_the_valuation_module_adds_up():
    rng = random.Random(20261112)
    compared = holding = 0
    for _ in range(260):
        rows, book, last_day, holidays = random_case(rng)
        if not rows:
            continue
        quotes = quotes_of(book)
        ledger = replayed(rows, last_day)
        got = daily(ledger, quotes, last_day, holidays)
        if not got.series or got.series[-1].day != last_day:
            continue
        lines = defaultdict(list)
        for position in ledger.holdings():
            lines[position.asset_id].append(
                Line(
                    position.account_id,
                    position.qty_u,
                    position.qty_u,
                    position.cost_paise,
                    0,
                    0,
                )
            )
        shown = [
            Row(MODE_NONE, price_on(quotes[asset], last_day, holidays), None, tuple(each))
            for asset, each in lines.items()
        ]
        figures = tab_totals(shown, Fixed())
        assert figures.unpriced == 0
        assert figures.value_paise == got.series[-1].value_paise
        compared += 1
        holding += bool(lines)
    assert compared >= 80 and holding >= 50, (compared, holding)


def extra_prices(rng, rows, last_day):
    first = min(row["trade_date"] for row in rows)
    count = max(0, ordinal(last_day) - ordinal(first))
    return [
        (shift(first, rng.randint(-3, count)), rng.randint(10_000, 5_000_000))
        for _ in range(rng.randint(0, 40))
    ]


def test_more_prices_only_ever_add_days():
    rng = random.Random(20261113)
    added = same = 0
    for _ in range(120):
        rows, book, last_day, holidays = random_case(rng, rng.choice([1, 1, 2]))
        if not rows:
            continue
        ledger = replayed(rows, last_day)
        before = daily(ledger, quotes_of(book), last_day, holidays)
        more = {
            asset: (kind, [*prices, *extra_prices(rng, rows, last_day)])
            for asset, (kind, prices) in book.items()
        }
        after = daily(ledger, quotes_of(more), last_day, holidays)
        assert {p.day for p in before.series} <= {p.day for p in after.series}
        # prices dated after the day asked for are never looked at
        late = {
            asset: (kind, [*prices, (shift(last_day, 1), 1), (shift(last_day, 40), 2)])
            for asset, (kind, prices) in book.items()
        }
        assert daily(ledger, quotes_of(late), last_day, holidays) == before
        added += len(after.series) > len(before.series)
        same += len(after.series) == len(before.series)
    assert added >= 20 and same >= 10, (added, same)


def test_holidays_that_do_not_matter_change_nothing():
    rng = random.Random(20261114)
    for _ in range(80):
        rows, book, last_day, holidays = random_case(rng)
        if not rows:
            continue
        ledger = replayed(rows, last_day)
        quotes = quotes_of(book)
        base = daily(ledger, quotes, last_day, holidays)
        first = min(row["trade_date"] for row in rows)
        weekend = [day for day in (shift(first, step) for step in range(60)) if weekday(day) >= 5]
        outside = [20200101, 20990101, 20260230, 0]  # before, after, no such day, no day at all
        rng.shuffle(holidays)
        assert daily(ledger, quotes, last_day, [*holidays, *weekend, *outside]) == base
        assert daily(ledger, quotes, last_day, iter(holidays)) == base


def test_a_nav_that_is_too_old_leaves_exactly_the_days_the_rule_says():
    # One fund bought on Mon 5 Jan 2026 and one NAV on that day: complete on the day and the three
    # business days after it, then not again, whatever the weekend and the holidays do.
    row = {"id": 1, "seq": 1, "kind": "BUY", "trade_date": 20260105, "account_id": 1}
    row |= {"asset_id": 2, "qty_u": 1_000_000, "amount_paise": 100}
    ledger = replayed([row])
    nav = {2: Quotes.of({"kind": "mf", "prices": [[20260105, 100_000]]})}
    for holidays, last in (
        ((), [20260105, 20260106, 20260107, 20260108]),
        ([20260106], [20260105, 20260106, 20260107, 20260108, 20260109, 20260110, 20260111]),
        ([20260106, 20260107], [*range(20260105, 20260112), 20260112]),
        ([20260109], [*range(20260105, 20260112)]),  # a holiday on the day itself counts too
    ):
        got = daily(ledger, nav, 20260115, holidays)
        assert [p.day for p in got.series] == last, holidays


# -- the index, on random money --------------------------------------------------------------------


def random_index(rng, first, last):
    """An index's closes from a random start (sometimes after the first money) to a random end."""
    begin = shift(first, rng.choice([-40, -20, -5, -5, -1, 0, 3, 60]))
    end = shift(last, rng.choice([0, 0, 0, 20]))
    level, closes, day = rng.randint(80_000_000, 300_000_000), [], begin
    while day <= end:
        if weekday(day) < 5 and rng.random() < 0.9:
            level = max(1_000_000, int(level * rng.uniform(0.985, 1.016)))
            closes.append((day, level))
        day = shift(day, 1)
    if closes and rng.random() < 0.1:
        closes.append((rng.choice(closes)[0], rng.randint(80_000_000, 300_000_000)))
    if rng.random() < 0.5:
        rng.shuffle(closes)
    return closes


def index_case(rng):
    """``(flows, flow_dicts, closes, valuation_day, now_u, span)`` on a random tab."""
    rows = dated_tab(rng, rng.choice([1, 4, 9, 20]))
    engine = flows_of(replayed(rows))
    if not engine:
        return None
    dicts = oracle_flows(rows, None, DECIMALS)
    first, last = engine[0].day, engine[-1].day
    if rng.random() < 0.15:
        vday = engine[rng.randrange(len(engine))].day  # some money comes after the day asked for
    else:
        vday = shift(last, rng.choice([0, 0, 1, 40, 200, 400]))
    closes = random_index(rng, first, vday)
    now_u = rng.choice([None, None, None, rng.randint(80_000_000, 300_000_000)])
    if closes and rng.random() < 0.5:
        a = shift(first, rng.randint(-10, max(0, ordinal(vday) - ordinal(first))))
        span = (a, shift(a, rng.randint(0, max(0, ordinal(vday) - ordinal(a)))))
    else:
        span = None
    return engine, dicts, closes, vday, now_u, span


def x_of_pct(text):
    """ln(1 + rate) of a percentage text; None for a total loss, which has no log."""
    rate = float(text) / 100
    return None if rate <= -1 else math.log1p(rate)


def test_the_index_figures_of_random_money_are_the_naive_oracles():
    rng = random.Random(20261115)
    seen = Counter()
    for _ in range(180):
        made = index_case(rng)
        if made is None:
            seen["no_money"] += 1
            continue
        engine, dicts, closes, vday, now_u, span = made
        got = benchmark(NIFTY, engine, closes, vday, now_u, span)
        naive = index_comparison(dicts, closes, vday, now_u, span)
        assert got.benchmark == NIFTY and got.label == "NIFTY 50"
        assert got.value_paise == naive["value_paise"]
        assert got.twr_pct == naive["twr_pct"]
        assert got.flagged == (naive["early"] or naive["short"] or not closes)
        expected_words = [words.DIVIDENDS_NOT_INCLUDED]
        if not closes:
            expected_words.append(words.index_missing("NIFTY 50"))
        elif naive["early"]:
            expected_words.append(words.index_starts("NIFTY 50", min(day for day, _ in closes)))
        elif naive["short"]:
            expected_words.append(words.INDEX_COULD_NOT_PAY)
        assert got.words == expected_words
        seen["early"] += bool(naive["early"])
        seen["short"] += bool(naive["short"] and not naive["early"])
        seen["value"] += got.value_paise is not None
        seen["twr"] += got.twr_pct is not None
        seen["no_closes"] += not closes
        roots = naive["roots"]
        if roots:
            xs = [math.log1p(rate) for rate in roots]
            tied = len(xs) > 1 and abs(abs(xs[0]) - abs(xs[1])) < 1e-6
            crowded = any(
                abs(xs[0] - other) < 2 * lattice_step(naive["cash"], xs[0]) for other in xs[1:]
            )
            if tied or crowded or abs(xs[0]) > 20:
                seen["skipped"] += 1
                continue
            assert got.xirr_pct is not None, naive["cash"]
            assert float(got.xirr_pct) / 100 == pytest.approx(roots[0], rel=1e-9, abs=6e-7)
            seen["rate"] += 1
        elif got.xirr_pct is not None:  # a rate beyond what the oracle looks for
            seen["beyond"] += 1
            x = x_of_pct(got.xirr_pct)
            assert x is None or abs(x) > 20, got.xirr_pct
        else:
            seen["no_rate"] += 1
    assert seen["value"] >= 80 and seen["rate"] >= 40 and seen["twr"] >= 50, seen
    assert seen["early"] >= 5 and seen["short"] >= 5 and seen["no_closes"] >= 2, seen
    assert seen["skipped"] < 60, seen


def distinct_days(closes):
    """One close for each day, the later in the list where there were two."""
    return list({day: (day, close) for day, close in closes}.values())


def test_the_order_of_the_flows_and_of_the_closes_changes_nothing():
    rng = random.Random(20261116)
    checked = 0
    for _ in range(80):
        made = index_case(rng)
        if made is None:
            continue
        engine, _, closes, vday, now_u, span = made
        distinct = distinct_days(closes)
        base = benchmark(NIFTY, engine, sorted(distinct), vday, now_u, span)
        flows = list(engine)
        rng.shuffle(flows)
        rng.shuffle(distinct)
        assert benchmark(NIFTY, flows, distinct, vday, now_u, span) == base
        assert benchmark(NIFTY, iter(flows), iter(distinct), vday, now_u, span) == base
        checked += base.value_paise is not None
    assert checked >= 40


def test_money_and_closes_after_the_day_asked_for_are_not_seen():
    rng = random.Random(20261117)
    checked = 0
    for _ in range(120):
        made = index_case(rng)
        if made is None:
            continue
        engine, _, closes, vday, now_u, span = made
        if not any(day <= vday for day, _ in closes):
            continue  # an index whose prices all come later says when they start: that changes
        if span is not None:
            span = (min(span[0], vday), min(span[1], vday))
        base = benchmark(NIFTY, engine, closes, vday, now_u, span)
        later = [
            Flow(shift(vday, 1), in_paise=5_000_000),
            Flow(shift(vday, 9), out_paise=9_000_000, fee_paise=100),
        ]
        extra = [(shift(vday, 1), 1_000_000), (shift(vday, 5), 900_000_000)]
        assert benchmark(NIFTY, [*engine, *later], [*closes, *extra], vday, now_u, span) == base
        checked += base.value_paise is not None
    assert checked >= 50


def test_a_level_given_equal_to_the_last_close_is_the_same_as_none():
    rng = random.Random(20261118)
    checked = 0
    for _ in range(100):
        made = index_case(rng)
        if made is None:
            continue
        engine, _, closes, vday, _, span = made
        closes = distinct_days(closes)
        before = [(day, close) for day, close in closes if day <= vday]
        if not before:
            continue
        level = max(before)[1]
        assert benchmark(NIFTY, engine, closes, vday, level, span) == benchmark(
            NIFTY, engine, closes, vday, None, span
        )
        checked += 1
    assert checked >= 60


def test_moving_every_date_together_changes_no_figure():
    rng = random.Random(20261119)
    moved = 0
    for _ in range(100):
        made = index_case(rng)
        if made is None:
            continue
        engine, _, closes, vday, now_u, span = made
        base = benchmark(NIFTY, engine, closes, vday, now_u, span)
        n = rng.choice([1, 17, 365, 4000])
        there = benchmark(
            NIFTY,
            [Flow(shift(f.day, n), f.in_paise, f.out_paise, f.fee_paise) for f in engine],
            [(shift(day, n), close) for day, close in closes],
            shift(vday, n),
            now_u,
            None if span is None else (shift(span[0], n), shift(span[1], n)),
        )
        for key in ("value_paise", "xirr_pct", "twr_pct", "flagged"):
            assert getattr(there, key) == getattr(base, key), key
        assert len(there.words) == len(base.words)
        moved += base.value_paise is not None
    assert moved >= 40


def test_putting_in_more_money_buys_proportionally_more_of_the_index():
    rng = random.Random(20261120)
    checked = 0
    for _ in range(80):
        made = index_case(rng)
        if made is None:
            continue
        engine, _, closes, vday, now_u, span = made
        base = benchmark(NIFTY, engine, closes, vday, now_u, span)
        if base.value_paise is None:
            continue
        k = rng.choice([2, 3, 10, 1000])
        bigger = benchmark(
            NIFTY,
            [Flow(f.day, f.in_paise * k, f.out_paise * k, f.fee_paise * k) for f in engine],
            closes,
            vday,
            now_u,
            span,
        )
        assert abs(bigger.value_paise - k * base.value_paise) <= (k + 1) // 2
        assert (bigger.flagged, bigger.twr_pct, bigger.words) == (
            base.flagged,
            base.twr_pct,
            base.words,
        )
        checked += 1
    assert checked >= 40


def test_the_money_of_two_tabs_buys_what_each_bought_within_a_paisa():
    rng = random.Random(20261121)
    checked = 0
    for _ in range(100):
        made = index_case(rng)
        if made is None:
            continue
        engine, _, closes, vday, now_u, _ = made
        mine = [flow for flow in engine if rng.random() < 0.5]
        yours = [flow for flow in engine if flow not in mine]
        together = benchmark(NIFTY, engine, closes, vday, now_u)
        a, b = (benchmark(NIFTY, part, closes, vday, now_u) for part in (mine, yours))
        if None in (together.value_paise, a.value_paise, b.value_paise):
            continue
        assert abs(together.value_paise - a.value_paise - b.value_paise) <= 1
        checked += 1
    assert checked >= 40


def test_units_that_dip_below_nothing_are_flagged_and_those_that_never_do_are_not():
    rng = random.Random(20261122)
    seen = Counter()
    for _ in range(300):
        closes = [
            (shift(20260105, n), 100_000_000 + rng.randint(0, 9) * 10_000_000) for n in range(40)
        ]
        close_on = dict(closes)
        flows = []
        for n in sorted(rng.sample(range(40), rng.randint(1, 6))):
            money = rng.randint(1_000, 900_000)
            day = shift(20260105, n)
            flows.append(
                Flow(day, in_paise=money) if rng.random() < 0.55 else Flow(day, out_paise=money)
            )
        got = benchmark(NIFTY, flows, closes, shift(20260105, 45))
        units, dipped = Fraction(0), False
        for flow in flows:
            units += Fraction(flow.in_paise - flow.out_paise, close_on[flow.day])
            dipped = dipped or units < 0
        assert got.flagged == dipped
        assert (words.INDEX_COULD_NOT_PAY in got.words) == dipped
        assert got.value_paise == round_half_away(units * closes[-1][1])
        seen[dipped] += 1
    assert seen[True] >= 30 and seen[False] >= 30, seen


def round_half_away(x: Fraction) -> int:
    whole = int(abs(x) + Fraction(1, 2))
    return whole if x >= 0 else -whole


# -- an index fund against the index ----------------------------------------------------------------


def tracker_tab(rng, trades="many"):
    """A fund that follows the index, bought and sold in whole units at the index's own close
    (whole rupees, so that every amount is exact): ``(rows, closes)``."""
    day, closes, level = 20260105, [], rng.randint(8_000, 30_000)
    for _ in range(rng.randint(8, 70)):
        while weekday(day) >= 5:
            day = shift(day, 1)
        level = max(100, level + rng.randint(-300, 320))
        closes.append((day, level * 10_000))
        day = shift(day, 1)
    rows, held = [], 0
    for number, (when, close) in enumerate(closes):
        if number == 0 or (trades == "many" and rng.random() < 0.35):
            row = {"id": len(rows) + 1, "seq": len(rows) + 1, "trade_date": when}
            row |= {"account_id": 1, "asset_id": 1, "price_u": close}
            if held and rng.random() < 0.4:
                qty = rng.randint(1, held)
                rows.append(row | {"kind": "SELL", "qty_u": qty * U})
                held -= qty
            else:
                qty = rng.randint(1, 20)
                rows.append(row | {"kind": "BUY", "qty_u": qty * U})
                held += qty
    return rows, closes


def test_a_fund_bought_at_the_index_close_is_worth_what_the_same_money_in_the_index_is():
    rng = random.Random(20261123)
    compared = sold_out = 0
    for _ in range(80):
        rows, closes = tracker_tab(rng)
        ledger = replayed(rows)
        flows = flows_of(ledger)
        last = closes[-1][0]
        got = daily(ledger, quotes_of({1: ("listed", closes)}), last)
        shut = {day for day, _ in closes}
        for point in got.series:
            assert (
                point.day in shut or point.value_paise == 0
            )  # a weekend is priced only if sold out
            figure = benchmark(NIFTY, flows, closes, point.day)
            assert figure.value_paise == point.value_paise, point.day
            assert figure.flagged is False
            compared += 1
            sold_out += point.value_paise == 0
    assert compared >= 500 and sold_out >= 1, (compared, sold_out)


def test_a_fund_bought_once_has_the_return_of_the_index_over_the_same_days():
    rng = random.Random(20261124)
    for _ in range(60):
        rows, closes = tracker_tab(rng, trades="once")
        assert len(rows) == 1
        ledger = replayed(rows)
        got = daily(ledger, quotes_of({1: ("listed", closes)}), closes[-1][0])
        span = got.span()
        assert span == (closes[0][0], closes[-1][0]) or len(closes) == 1
        figure = benchmark(NIFTY, flows_of(ledger), closes, closes[-1][0], None, span)
        assert got.twr().pct == figure.twr_pct
        assert figure.value_paise == got.series[-1].value_paise


# -- the report on random tabs ---------------------------------------------------------------------


def test_the_report_of_a_random_tab_is_its_parts():
    rng = random.Random(20261125)
    checked = 0
    for _ in range(60):
        rows, book, last_day, holidays = random_case(rng, rng.choice([1, 1, 4]))
        if not rows:
            continue
        ledger = replayed(rows, last_day)
        flows = flows_of(ledger)
        first = flows[0].day if flows else last_day
        index = {
            NIFTY: random_index(rng, first, last_day),
            "nifty500": random_index(rng, first, last_day) if rng.random() < 0.5 else [],
        }
        now = {NIFTY: rng.randint(80_000_000, 300_000_000)} if rng.random() < 0.3 else None
        value = rng.randint(0, 50_000_000)
        quotes = quotes_of(book)
        got = report(ledger, last_day, value, quotes, index, now, holidays)
        days = daily(ledger, quotes, last_day, holidays)
        assert {key: got[key] for key in summary([], 0, last_day)} == summary(
            flows, value, last_day
        )
        assert got["twr"] == days.twr().as_dict()
        assert got["series"] == [point.as_dict() for point in days.series]
        assert got["series_words"] == days.words
        assert got["by_fy"] == [fy.as_dict() for fy in by_financial_year(ledger)]
        assert got["income"] == income_report(ledger, last_day)
        assert [each["benchmark"] for each in got["benchmarks"]] == list(kinds.BENCHMARKS)
        for each in got["benchmarks"]:
            name = each["benchmark"]
            assert (
                each
                == benchmark(
                    name, flows, index.get(name, ()), last_day, (now or {}).get(name), days.span()
                ).as_dict()
            )
        no_float(got)
        checked += bool(flows)
    assert checked >= 40


def test_a_report_without_a_closes_list_for_an_index_still_names_both_indices():
    case = FILE["reports"][1]
    day = case["valuation_day"]
    got = report(
        replayed(case["entries"], day), day, case["value_paise"], quotes_of(book_in(case)), {}
    )
    assert [each["benchmark"] for each in got["benchmarks"]] == ["nifty50", "nifty500"]
    assert all(each["flagged"] and each["value_paise"] is None for each in got["benchmarks"])
    assert got["benchmarks"][0]["words"] == [
        words.DIVIDENDS_NOT_INCLUDED,
        words.index_missing("NIFTY 50"),
    ]


# -- a long history is quick -------------------------------------------------------------------------


def long_tab(years, funds=6, shares=8):
    """A tab with ``shares`` listed stocks and ``funds`` funds, priced every day for ``years``."""
    rng = random.Random(77)
    start = date(2011, 1, 3)
    days = [date.fromordinal(start.toordinal() + n) for n in range(0, 365 * years)]
    holidays = [from_date(d) for d in days if d.weekday() < 5 and rng.random() < 0.04]
    rows, book = [], {}
    for asset in range(1, shares + funds + 1):
        kind = "listed" if asset <= shares else "mf"
        price, prices = rng.randint(100, 900) * 10_000, []
        for d in days:
            if kind == "listed" and d.weekday() >= 5:
                continue
            price = max(10_000, int(price * (1 + rng.uniform(-0.02, 0.021))))
            prices.append((from_date(d), price))
        book[asset] = (kind, prices)
        for k in range(6):
            number = len(rows) + 1
            when = days[rng.randint(0, len(days) - 1)]
            rows.append(
                {"id": number, "seq": number, "kind": "BUY", "trade_date": from_date(when)}
                | {"account_id": 1 + k % 2, "asset_id": asset}
                | {"qty_u": rng.randint(1, 50) * U, "amount_paise": rng.randint(10_000, 900_000)}
            )
    return rows, book, holidays, from_date(days[-1])


def test_fifteen_years_of_daily_prices_are_worked_through_in_seconds():
    rows, book, holidays, last = long_tab(15)
    started = time.perf_counter()
    got = daily(replayed(rows), quotes_of(book), last, holidays)
    figure = got.twr()
    elapsed = time.perf_counter() - started
    assert len(got.series) > 3000 and figure.annualised_pct is not None
    assert elapsed < 20, elapsed  # about a second; the bound only catches a method gone quadratic


def test_the_ratio_of_two_huge_whole_numbers_is_the_decimal_quotient():
    rng = random.Random(20261126)
    for _ in range(300):
        bottom = rng.randint(1, 10 ** rng.randint(1, 1500))
        top = rng.choice([0, rng.randint(0, 10 ** rng.randint(1, 1500))])
        with localcontext(performance._CONTEXT):
            assert performance._ratio(top, bottom) == Decimal(top) / Decimal(bottom), (top, bottom)


def random_walk(count, seed):
    """Points for ``count`` days after a first day with money, the value changing by up to 1%."""
    rng = random.Random(seed)
    points = [Point(20100104, 1_000_000, 1_000_000)]
    value, day = 1_000_000, 20100104
    for _ in range(count):
        day = shift(day, 1)
        value = max(1, value * rng.randint(990, 1012) // 1000)
        points.append(Point(day, value))
    return points


def test_a_return_over_thousands_of_days_is_annualised_quickly():
    points = random_walk(9000, 5)  # the exact product has tens of thousands of digits
    started = time.perf_counter()
    figure = twr(points, 0)
    assert time.perf_counter() - started < 20
    assert figure.annualised_pct is not None and figure.pct is not None


def test_a_return_over_hundreds_of_days_is_the_naive_oracles():
    for seed in range(6):
        points = random_walk(400 + seed * 60, seed)
        figure = twr(points, 0)
        want = oracle_twr([asdict(p) for p in points], 0)["figure"]
        assert (figure.start_day, figure.pct, figure.annualised_pct) == (
            want["start_day"],
            want["pct"],
            want["annualised_pct"],
        )
        assert figure.annualised_pct is not None
