"""options.py against hand-computed vectors (vectors/options.json) and brute-force restatements.

Open interest and strikes are whole numbers and a ratio is an exact fraction, so a vector compares
with no tolerance except for the few figures that come from a float implied volatility. The
properties restate build-up, ATM, PCR and max pain the long way (fractions, a scan of every
possible settlement price) over small random chains where ties are common.
"""

from __future__ import annotations

import json
import random
from fractions import Fraction
from pathlib import Path

import pytest
from screener_helpers import assert_same

from sudarshan.screener import catalog as cat
from sudarshan.screener import options as opt
from sudarshan.screener import types as T
from sudarshan.screener.types import Why

VECTORS = json.loads((Path(__file__).parent / "vectors" / "options.json").read_text("utf-8"))
SECTIONS = "buildup oi_change atm pcr max_pain atm_iv iv_rank iv_change".split()
MIN_CASES = {
    "buildup": 14, "oi_change": 5, "atm": 8, "pcr": 6, "max_pain": 5, "atm_iv": 5, "iv_rank": 8,
    "iv_change": 5,
}  # fmt: skip


def ids(section: str) -> list[str]:
    return [case["id"] for case in VECTORS[section]]


def cases(section: str):
    return pytest.mark.parametrize("case", VECTORS[section], ids=ids(section))


def strike_of(row) -> opt.Strike:
    return opt.Strike(row) if isinstance(row, int) else opt.Strike(**row)


def chain_of(spot, rows, underlying: str = "NIFTY") -> opt.Chain:
    return opt.Chain(underlying, spot, tuple(strike_of(row) for row in rows))


# ---- the vectors file -------------------------------------------------------------------------
def test_the_vectors_are_what_the_tests_expect():
    every = []
    for section in SECTIONS:
        found = ids(section)
        assert len(found) == len(set(found)), f"two {section} vectors share an id"
        assert len(found) >= MIN_CASES[section], f"{section} lost vectors"
        every += found
    every += [case["id"] for case in VECTORS["view"]["reads"]]
    assert len(every) == len(set(every)), "two vectors share an id across sections"


def test_the_states_are_the_catalogues():
    assert (opt.LONG_BUILDUP, opt.SHORT_BUILDUP, opt.SHORT_COVERING, opt.LONG_UNWINDING) == (
        cat.BUILDUP_STATES[:4]
    )
    assert opt.NONE == cat.BUILDUP_STATES[4]
    # Every option figure the catalogue offers is one View answers (none falls through to a guess).
    assert set(cat.OPT) == {
        "pcr", "max_pain", "atm_iv", "iv_change", "straddle", "iv_rank", "iv_pct", "vix_rank",
        "vix_pct",
    }  # fmt: skip


# ---- open interest ------------------------------------------------------------------------------
@cases("buildup")
def test_a_buildup_state(case):
    (price_ref, price_now), (oi_ref, oi_now) = case["price"], case["oi"]
    got = opt.buildup(
        price_now,
        price_ref,
        oi_now,
        oi_ref,
        Fraction(case.get("min_price", "0")),
        Fraction(case.get("min_oi", "0")),
    )
    assert got == case["expect"]


def moved_ref(now: int, ref: int, minimum: Fraction) -> int:
    """The same rule said with a ratio: how far it moved, in percent of the reference."""
    if now == ref:
        return 0
    if ref and abs(Fraction(now - ref, ref)) * 100 < minimum:
        return 0
    return 1 if now > ref else -1


def test_buildup_agrees_with_its_restatement_over_every_pair_of_moves():
    rng = random.Random(17)
    states = {
        (1, 1): opt.LONG_BUILDUP, (-1, 1): opt.SHORT_BUILDUP,
        (1, -1): opt.SHORT_COVERING, (-1, -1): opt.LONG_UNWINDING,
    }  # fmt: skip
    seen = set()
    for _ in range(20000):
        p_ref, o_ref = rng.randint(0, 60), rng.randint(0, 60)
        p_now, o_now = rng.randint(0, 60), rng.randint(0, 60)
        min_p, min_o = Fraction(rng.choice((0, 1, 5, 10, 25))), Fraction(rng.choice((0, 1, 5, 10)))
        got = opt.buildup(p_now, p_ref, o_now, o_ref, min_p, min_o)
        price, oi = moved_ref(p_now, p_ref, min_p), moved_ref(o_now, o_ref, min_o)
        assert got == (states.get((price, oi)) if price and oi else opt.NONE), (p_ref, p_now)
        seen.add(got)
    assert seen == set(cat.BUILDUP_STATES)


@cases("oi_change")
def test_open_interest_change(case):
    ref, now = case["oi"]
    assert_same(opt.oi_change_pct(now, ref), case["expect"])


# ---- the strikes followed -----------------------------------------------------------------------
@cases("atm")
def test_the_at_the_money_strike(case):
    got = opt.atm(chain_of(case["spot"], case["strikes"]))
    if isinstance(case["expect"], dict):
        assert got == T.Why(case["expect"]["why"], tuple(case["expect"]["detail"]))
    else:
        assert got.strike == case["expect"]


@cases("pcr")
def test_put_call_ratio(case):
    assert_same(opt.pcr(chain_of(2587500, case["strikes"])), case["expect"])


@cases("max_pain")
def test_max_pain(case):
    assert_same(opt.max_pain(chain_of(2587500, case["strikes"])), case["expect"])


@cases("atm_iv")
def test_atm_iv_and_straddle(case):
    chain = chain_of(case["spot"], case["strikes"])
    assert_same(opt.atm_iv(chain), case["iv"])
    assert_same(opt.straddle(chain), case["straddle"])


def random_chain(rng: random.Random) -> opt.Chain:
    step = rng.choice((1, 5, 10))
    base = rng.randint(0, 30)
    count = rng.randint(1, 7)
    rows = []
    for k in range(count):
        gap = rng.random() < 0.15  # a strike that does not report one side
        rows.append(
            opt.Strike(
                base + k * step,
                None if gap and rng.random() < 0.5 else rng.randint(0, 9),
                None if gap and rng.random() < 0.5 else rng.randint(0, 9),
            )
        )
    return opt.Chain("T", rng.randint(base - 5, base + count * step + 5), tuple(rows))


def test_the_nearest_strike_wins_and_a_tie_goes_to_the_lower():
    rng = random.Random(23)
    for _ in range(3000):
        chain = random_chain(rng)
        got = opt.atm(chain)
        distance = min(abs(s.strike - chain.spot) for s in chain.strikes)
        nearest = [s.strike for s in chain.strikes if abs(s.strike - chain.spot) == distance]
        assert got.strike == min(nearest)


def test_pcr_and_max_pain_agree_with_a_scan_of_every_settlement_price():
    rng = random.Random(29)
    ties = 0
    for _ in range(4000):
        chain = random_chain(rng)
        live = [s for s in chain.strikes if s.ce_oi is not None and s.pe_oi is not None]
        got_pcr, got_pain = opt.pcr(chain), opt.max_pain(chain)
        if not live:
            assert got_pcr == got_pain == Why(T.NO_DATA, ("oi",))
            continue
        calls, puts = sum(s.ce_oi for s in live), sum(s.pe_oi for s in live)
        assert got_pcr == (Fraction(puts, calls) if calls else Why(T.ZERO))

        def pain(k: int, rows=live) -> int:
            return sum(
                s.ce_oi * max(0, k - s.strike) + s.pe_oi * max(0, s.strike - k) for s in rows
            )

        low, high = min(s.strike for s in live), max(s.strike for s in live)
        costs = {k: pain(k) for k in range(low, high + 1)}
        best = min(costs.values())
        lowest = min(k for k, cost in costs.items() if cost == best)
        assert got_pain == lowest
        ties += sum(1 for cost in costs.values() if cost == best) > 1
    assert ties > 100, "the sample should reach ties"


# ---- IV over time -------------------------------------------------------------------------------
@cases("iv_rank")
def test_iv_rank_and_percentile(case):
    assert_same(opt.iv_rank(case["history"], case["today"]), case["rank"])
    assert_same(opt.iv_percentile(case["history"], case["today"]), case["percentile"])


def test_rank_and_percentile_stay_in_range_and_never_fall_as_today_rises():
    rng = random.Random(31)
    for _ in range(300):
        history = [rng.randint(5, 40) / 2 for _ in range(rng.randint(19, 60))]
        last_rank, last_pct = -1.0, Fraction(-1)
        for today in sorted({rng.randint(0, 100) / 2 for _ in range(12)}):
            rank, pct = opt.iv_rank(history, today), opt.iv_percentile(history, today)
            if isinstance(rank, Why):
                assert rank.code == T.ZERO
                continue
            assert 0.0 <= rank <= 100.0 and 0 <= pct <= 100
            assert rank >= last_rank and pct >= last_pct
            assert pct == Fraction(100 * sum(1 for v in history if v < today), len(history))
            last_rank, last_pct = rank, pct


@cases("iv_change")
def test_iv_change(case):
    assert_same(opt.iv_change(case["now"], case["ago"]), case["expect"])


# ---- answering an operand -----------------------------------------------------------------------
def make_view() -> opt.View:
    raw = VECTORS["view"]
    chains = {
        name: chain_of(item["spot"], item["strikes"], name) for name, item in raw["chains"].items()
    }
    ago = {
        (name, int(tf)): value
        for name, table in raw["iv_ago"].items()
        for tf, value in table.items()
    }
    return opt.View(
        chains=chains,
        iv_history=raw["iv_history"],
        iv_ago=ago,
        vix_now=raw["vix_now"],
        vix_history=raw["vix_history"],
    )


@pytest.mark.parametrize(
    "case", VECTORS["view"]["reads"], ids=[c["id"] for c in VECTORS["view"]["reads"]]
)
def test_a_view_answers_an_option_operand(case):
    operand = T.Opt(case["what"], case["underlying"], case.get("tf"), cat.OPT[case["what"]].unit)
    assert_same(make_view()(operand, case.get("own")), case["expect"])


def test_a_view_with_nothing_in_it_says_what_is_missing():
    empty = opt.View()
    assert empty(T.Opt("pcr", "NIFTY", None, "num")) == Why(T.NO_OPTIONS, ("NIFTY",))
    assert empty(T.Opt("vix_rank", "NIFTY", None, "pct")) == Why(T.NO_DATA, ("India VIX",))
