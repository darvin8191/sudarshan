"""price.py and patterns.py against hand-computed vectors (vectors/price.json) and properties.

The vectors were worked out by hand from the definitions in the design, not read off the code. A
price is paise, a ratio is an exact fraction in percent units, so a vector compares whole numbers
and fractions with no tolerance at all. The properties restate each definition a second way
(fractions instead of cross-multiplication, brute force instead of windows) over small random
candles, where ties and one-paisa differences are common, and check what no vector can: that a
shape does not change when the whole chart is shifted, scaled or turned upside down.
"""

from __future__ import annotations

import json
import math
import random
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest
from screener_helpers import candle, day_key, ms, series

from sudarshan.screener import catalog as cat
from sudarshan.screener import patterns, price
from sudarshan.screener import types as T
from sudarshan.screener.types import Series, Why

VECTORS = json.loads((Path(__file__).parent / "vectors" / "price.json").read_text("utf-8"))
SECTIONS = (
    "turnover fields change_pct session pivot orb unverified extremum extremum_array rs patterns"
).split()
MIN_CASES = {
    "turnover": 4, "fields": 13, "change_pct": 13, "session": 31, "pivot": 9, "orb": 17,
    "unverified": 7, "extremum": 12, "extremum_array": 4, "rs": 9, "patterns": 60,
}  # fmt: skip
WHY_CODES = {
    T.WARMING, T.NO_CANDLE, T.NO_DATA, T.ZERO, T.NOT_A_NUMBER, T.PAUSED, T.NO_AUCTION,
    T.NO_OPTIONS, T.SHORT_STORE, T.WINDOW_OPEN, T.OTHER_DAY, T.NO_FEED,
}  # fmt: skip


#: Fewest times a random sample must reach a shape for the sample to count as covering it.
MIN_SEEN = 40


def seed_of(name: str) -> int:
    """A seed that is the same in every run (``hash`` of a string is not)."""
    return sum(name.encode()) + 1


# ---- reading the vectors ----------------------------------------------------------------------
def build(name: str) -> Series:
    item = VECTORS["series"][name]
    rows = []
    for day, start, minutes, o, h, low, c, v, *extra in item["rows"]:
        rows.append(candle(day, start, minutes, o, h, low, c, v, **(extra[0] if extra else {})))
    return series(item["tf"], rows)


def days_of(name: str | None) -> Series | None:
    return build(name) if name else None


def want(raw):
    """A vector's expected value: a Why, a Fraction (text), an int or None."""
    if isinstance(raw, dict) and "why" in raw:
        detail = tuple(ms(*item) if isinstance(item, list) else item for item in raw["detail"])
        return Why(raw["why"], detail)
    if isinstance(raw, str):
        return Fraction(raw)
    return raw


def assert_same(got, raw) -> None:
    """Equal in value and in kind: a percent is a Fraction, a price an int, never a float."""
    expected = want(raw)
    if isinstance(expected, Why):
        assert got == expected
    elif isinstance(raw, str):
        assert type(got) is Fraction, f"{got!r} is not an exact fraction"
        assert got == expected
    elif expected is None:
        assert got is None
    else:
        assert type(got) is int, f"{got!r} is not a whole number"
        assert got == expected


def ids(section: str) -> list[str]:
    return [case["id"] for case in VECTORS[section]]


def cases(section: str):
    return pytest.mark.parametrize("case", VECTORS[section], ids=ids(section))


# ---- the vectors file -------------------------------------------------------------------------
def test_the_vectors_are_what_the_tests_expect():
    for section in SECTIONS:
        found = ids(section)
        assert len(found) == len(set(found)), f"two {section} vectors share an id"
        assert len(found) >= MIN_CASES[section], f"{section} lost vectors"
    every = [case["id"] for section in SECTIONS for case in VECTORS[section]]
    assert len(every) == len(set(every)), "two vectors share an id across sections"
    for name in VECTORS["series"]:
        assert build(name).tf in T.TIMEFRAMES


def test_every_why_a_vector_names_is_a_code_the_engine_knows():
    def walk(node):
        if isinstance(node, dict):
            if "why" in node:
                yield node["why"]
            for value in node.values():
                yield from walk(value)
        elif isinstance(node, list):
            for value in node:
                yield from walk(value)

    named = set(walk(VECTORS))
    assert named and named <= WHY_CODES


def test_the_functions_cover_what_the_catalogue_offers():
    # Every level of the session the catalogue names is a level price.py can read, and no other.
    levels = {name for name, spec in cat.FIELDS.items() if not spec.candle}
    assert set(price.SESSION_NAMES) == levels
    # Every candle field is read by candle_field, change_pct by its own function.
    one = build("fields")
    for name, spec in cat.FIELDS.items():
        if spec.candle and name != "change_pct":
            price.candle_field(one, 1, name)
    # The pivot outputs are the levels of each kind, no more and no fewer.
    for kind, outs in cat.PIVOT_OUTS.items():
        assert set(price.pivot_levels(kind, 200, 100, 150)) == set(outs)
    # Every pattern has a test, a warm-up and a verdict.
    assert set(cat.PATTERNS) == set(patterns._TESTS) == set(cat.PATTERN_NEEDS)


# ---- one candle ---------------------------------------------------------------------------------
@cases("turnover")
def test_turnover(case):
    assert_same(price.turnover(case["close"], case["volume"]), case["expect"])


@cases("fields")
def test_a_candle_field(case):
    got = price.candle_field(build(case["series"]), case["index"], case["name"])
    assert_same(got, case["expect"])


def test_an_unknown_field_is_a_mistake_not_a_gap():
    with pytest.raises(KeyError):
        price.candle_field(build("fields"), 0, "volume_weighted")


@cases("change_pct")
def test_change_pct(case):
    got = price.change_pct(
        build(case["series"]),
        case["index"],
        days_of(case["days"]),
        day_key(case["today"]),
        case["cp"],
    )
    assert_same(got, case["expect"])


def test_a_percent_threshold_is_decided_in_whole_paise():
    # change_pct >= 2 must agree with close * 100 >= previous * 102 for every pair, with no float
    # in between: 10 paise on 5.00 is exactly 2 percent, and a paisa less is not.
    rng = random.Random(11)
    for _ in range(4000):
        previous = rng.randint(1, 400)
        close = rng.randint(0, 900)
        got = price.percent_from(close, previous)
        assert type(got) is Fraction
        assert (got >= 2) == (close * 100 >= previous * 102)
        assert (got <= -2) == (close * 100 <= previous * 98)
        assert (got == 0) == (close == previous)


# ---- the session --------------------------------------------------------------------------------
@cases("session")
def test_a_level_of_the_session(case):
    year = tuple(case["year"]) if case["year"] else None
    got = price.session_level(
        case["name"], days_of(case["days"]), day_key(case["session"]), case["cp"], year
    )
    assert_same(got, case["expect"])


@cases("pivot")
def test_pivot_levels(case):
    days, session = days_of(case["days"]), day_key(case["session"])
    expect = case["expect"]
    if "all" in expect:
        for out in cat.PIVOT_OUTS[case["kind"]]:
            assert_same(price.pivot(case["kind"], out, days, session), expect["all"])
        return
    assert set(expect) == set(cat.PIVOT_OUTS[case["kind"]]), "a vector must pin every level"
    for out, raw in expect.items():
        assert_same(price.pivot(case["kind"], out, days, session), raw)


def test_the_classic_levels_keep_their_order_and_their_balance():
    # Identities that follow from the definitions without restating them: the levels in pairs are
    # one, two and three ranges apart, and the pairs balance about the pivot.
    rng = random.Random(5)
    for _ in range(2000):
        low = rng.randint(1, 300)
        high = low + rng.randint(0, 40)
        close = rng.randint(low, high)
        spread = high - low
        level = price.pivot_levels("classic", high, low, close)
        p = level["P"]
        assert p == Fraction(high + low + close, 3)
        assert level["R1"] - level["S1"] == spread
        assert level["R2"] - level["S2"] == 2 * spread
        assert level["R3"] - level["S3"] == 3 * spread
        assert level["R2"] + level["S2"] == 2 * p
        assert level["R1"] + level["S1"] == level["R3"] + level["S3"] == 4 * p - (high + low)
        if spread:
            chain = [level[k] for k in ("S3", "S2", "S1", "P", "R1", "R2", "R3")]
            assert all(a < b for a, b in zip(chain, chain[1:], strict=False)), (high, low, close)
        else:
            assert len(set(level.values())) == 1
        cpr = price.pivot_levels("cpr", high, low, close)
        assert cpr["P"] == p and cpr["BC"] == Fraction(high + low, 2)
        assert cpr["TC"] - p == p - cpr["BC"], "TC and BC sit at equal distances from P"


def test_a_pivot_reads_the_day_before_the_session_whatever_follows():
    rng = random.Random(8)
    for _ in range(60):
        rows = []
        for day in range(20, 29):
            low = rng.randint(1, 500)
            high = low + rng.randint(0, 50)
            close = rng.randint(low, high)
            rows.append(candle(f"2026-10-{day:02d}", "09:15", 375, low, high, low, close, 1))
        days = series("D", rows)
        for upto in range(1, len(rows)):
            session = day_key(f"2026-10-{20 + upto:02d}")
            before = rows[upto - 1]
            levels = price.pivot_levels("classic", before.h, before.low, before.c)
            for out, level in levels.items():
                assert price.pivot("classic", out, days, session) == level


# ---- ranges -------------------------------------------------------------------------------------
def opens_of(case: dict):
    table = {day_key(day): ms(day, clock) for day, clock in case["opens"].items()}
    return table.get


@cases("orb")
def test_an_opening_range(case):
    one, now = build(case["series"]), ms(*case["now"])
    args = (one, case["index"], case["minutes"], opens_of(case), now)
    span = price.orb_span(*args)
    if isinstance(case["span"], dict):
        assert span == want(case["span"])
    else:
        assert span == tuple(case["span"])
    assert_same(price.orb(*args, "high"), case["high"])
    assert_same(price.orb(*args, "low"), case["low"])


def session_candles(rng: random.Random, tf: int, day: str = "2026-10-28") -> list:
    rows, begin = [], 9 * 60 + 15
    for k in range(375 // tf):
        at = begin + k * tf
        o = rng.randint(100, 140)
        c = rng.randint(100, 140)
        high = max(o, c) + rng.randint(0, 9)
        low = min(o, c) - rng.randint(0, 9)
        rows.append(candle(day, f"{at // 60:02d}:{at % 60:02d}", tf, o, high, low, c, 1))
    return rows


@pytest.mark.parametrize("tf", [1, 3, 5, 15])
def test_the_opening_range_is_what_a_person_would_read_off_the_chart(tf):
    rng = random.Random(100 + tf)
    day = "2026-10-28"
    start = ms(day, "09:15")
    for _ in range(6):
        rows = session_candles(rng, tf)
        one = series(tf, rows)
        for minutes in range(5, 61, 5):
            stop = start + minutes * 60_000
            inside = [r for r in rows if r.start < stop]
            for index in range(min(len(rows), minutes // tf + 4)):
                now = rows[index].end + 5_000
                high = price.orb(one, index, minutes, {day_key(day): start}.get, now, "high")
                low = price.orb(one, index, minutes, {day_key(day): start}.get, now, "low")
                if rows[index].end < stop:
                    expected = (Why(T.WINDOW_OPEN, (minutes,)),) * 2
                elif minutes % tf:  # candles that do not divide the range: the spec refuses it
                    expected = (Why(T.NO_DATA, ("opening range",)),) * 2
                else:
                    expected = (max(r.h for r in inside), min(r.low for r in inside))
                assert (high, low) == expected, (tf, minutes, index)


def test_the_range_is_known_from_the_second_it_ends_and_not_before():
    one = build("m5_28")
    start = ms("2026-10-28", "09:15")
    for second in range(15 * 60):
        assert isinstance(
            price.orb_span(one, 2, 15, {20261028: start}.get, start + second * 1000), Why
        )
    assert price.orb_span(one, 2, 15, {20261028: start}.get, start + 15 * 60_000) == (0, 3)


@cases("unverified")
def test_a_candle_built_while_the_feed_paused(case):
    got = price.unverified_in(build(case["series"]), case["first"], case["after"])
    assert (got is None) if case["expect"] is None else (got == want(case["expect"]))


@cases("extremum")
def test_a_window_extremum(case):
    got = price.extremum_at(
        build(case["series"]), case["side"], case["n"], case["source"], case["index"]
    )
    assert_same(got, case["expect"])


@cases("extremum_array")
def test_a_window_extremum_for_every_candle(case):
    got = price.extremum_array(build(case["series"]), case["side"], case["n"], case["source"])
    expected = [math.nan if item is None else item for item in case["expect"]]
    np.testing.assert_array_equal(got, np.array(expected, dtype=np.float64))


def test_extremum_at_and_extremum_array_agree_with_a_plain_loop():
    rng = random.Random(21)
    attribute = {"o": "o", "h": "h", "l": "low", "c": "c", "v": "v"}
    for _ in range(40):
        size = rng.randint(1, 45)
        rows = []
        for k in range(size):
            at = 9 * 60 + 15 + k
            o, c = rng.randint(0, 40), rng.randint(0, 40)
            high, low = max(o, c) + rng.randint(0, 5), min(o, c) - rng.randint(0, 5)
            volume = rng.randint(0, 99)
            rows.append(
                candle("2026-10-28", f"{at // 60:02d}:{at % 60:02d}", 1, o, high, low, c, volume)
            )
        one = series(1, rows)
        for source, key in price.COLUMNS.items():
            values = [getattr(row, attribute[key]) for row in rows]
            for side, pick in (("high", max), ("low", min)):
                for n in {1, 2, 3, 7, size, size + 1}:
                    array = price.extremum_array(one, side, n, source)
                    for index in range(size):
                        got = price.extremum_at(one, side, n, source, index)
                        if index + 1 < n:
                            assert got == Why(T.WARMING, (n, index + 1))
                            assert math.isnan(array[index])
                        else:
                            expected = pick(values[index - n + 1 : index + 1])
                            assert got == expected and type(got) is int
                            assert array[index] == expected


def test_a_window_of_nothing_is_a_mistake():
    with pytest.raises(ValueError):
        price.extremum_at(build("m5_28"), "high", 0, "high", 3)


# ---- against the index --------------------------------------------------------------------------
@cases("rs")
def test_relative_strength(case):
    got = price.relative_strength(
        build(case["stock"]), days_of(case["bench"]), case["index"], case["n"]
    )
    assert_same(got, case["expect"])


def test_a_stock_that_moves_with_the_index_is_neither_ahead_nor_behind():
    rng = random.Random(3)
    for _ in range(100):
        bench, stock = [], []
        for k in range(12):
            level = rng.randint(1000, 30000)
            at = f"{9 + (15 + 5 * k) // 60:02d}:{(15 + 5 * k) % 60:02d}"
            bench.append(candle("2026-10-28", at, 5, level, level, level, level))
            stock.append(candle("2026-10-28", at, 5, level * 3, level * 3, level * 3, level * 3))
        for n in (1, 4, 11):
            got = price.relative_strength(series(5, stock), series(5, bench), 11, n)
            assert got == 0 and type(got) is Fraction


def test_relative_strength_is_a_ratio_of_returns_not_a_difference():
    # +10% against +5% is 4.76% ahead (1.10 / 1.05 - 1), not 5 points.
    def flat(clock: str, level: int):
        return candle("2026-10-28", clock, 5, level, level, level, level)

    stock = series(5, [flat("09:15", 100), flat("09:20", 110)])
    bench = series(5, [flat("09:15", 200), flat("09:20", 210)])
    assert price.relative_strength(stock, bench, 1, 1) == Fraction(100, 21)


# ---- candle patterns ----------------------------------------------------------------------------
def clock_of(k: int) -> str:
    at = 9 * 60 + 15 + 5 * k
    return f"{at // 60:02d}:{at % 60:02d}"


def pattern_series(quads: list) -> Series:
    rows = [
        candle("2026-10-28", clock_of(k), 5, o, h, low, c, 1)
        for k, (o, h, low, c) in enumerate(quads)
    ]
    return series(5, rows)


def quads_of(case: dict) -> list:
    if "ranges" in case:  # a candle of the given range: open at the low, close at the high
        return [[1000, 1000 + r, 1000, 1000 + r] for r in case["ranges"]]
    return case["candles"]


@cases("patterns")
def test_a_candle_pattern(case):
    quads = quads_of(case)
    got = patterns.holds(case["pattern"], pattern_series(quads), case.get("at", len(quads) - 1))
    if isinstance(case["expect"], dict):
        assert got == want(case["expect"])
    else:
        assert got is case["expect"], f"{case['pattern']}: {got!r}"


def random_quad(rng: random.Random, top: int = 14, wick: int = 6) -> list:
    o, c = rng.randint(0, top), rng.randint(0, top)
    return [o, max(o, c) + rng.randint(0, wick), min(o, c) - rng.randint(0, wick), c]


def verdict(name: str, quads: list) -> bool | Why:
    return patterns.holds(name, pattern_series(quads), len(quads) - 1)


# The same definitions, said another way: fractions instead of cross-multiplication.
def doji_ref(o, h, low, c):
    return h - low > 0 and Fraction(abs(c - o), h - low) <= Fraction(1, 10)


def hammer_ref(o, h, low, c):
    body, rng = abs(c - o), h - low
    if rng == 0 or body == 0:
        return False
    return Fraction(min(o, c) - low, body) >= 2 and Fraction(h - max(o, c), rng) <= Fraction(1, 10)


def star_ref(o, h, low, c):
    body, rng = abs(c - o), h - low
    if rng == 0 or body == 0:
        return False
    return Fraction(h - max(o, c), body) >= 2 and Fraction(min(o, c) - low, rng) <= Fraction(1, 10)


def bullish_ref(p, n):
    p_low, p_high = sorted((p[0], p[3]))
    n_low, n_high = sorted((n[0], n[3]))
    return (
        p[3] < p[0] and n[3] > n[0] and n_low <= p_low and n_high >= p_high
        and n_high - n_low > p_high - p_low
    )  # fmt: skip


def bearish_ref(p, n):
    p_low, p_high = sorted((p[0], p[3]))
    n_low, n_high = sorted((n[0], n[3]))
    return (
        p[3] > p[0] and n[3] < n[0] and n_low <= p_low and n_high >= p_high
        and n_high - n_low > p_high - p_low
    )  # fmt: skip


def test_the_single_candle_shapes_agree_with_their_restatement():
    rng = random.Random(41)
    seen = {"doji": 0, "hammer": 0, "shooting_star": 0}
    for _ in range(12000):
        quad = random_quad(rng, 10, 8)
        for name, ref in (("doji", doji_ref), ("hammer", hammer_ref), ("shooting_star", star_ref)):
            got = verdict(name, [quad])
            assert got is ref(*quad), (name, quad)
            seen[name] += got
    assert all(count > MIN_SEEN for count in seen.values()), seen  # the sample reaches every shape


def test_the_two_candle_shapes_agree_with_their_restatement():
    rng = random.Random(42)
    seen = {"bullish": 0, "bearish": 0, "inside": 0}
    for _ in range(12000):
        first, second = random_quad(rng, 12, 4), random_quad(rng, 12, 4)
        bull = verdict("bullish_engulfing", [first, second])
        bear = verdict("bearish_engulfing", [first, second])
        inside = verdict("inside_bar", [first, second])
        assert bull is bullish_ref(first, second), (first, second)
        assert bear is bearish_ref(first, second), (first, second)
        assert inside is (second[1] < first[1] and second[2] > first[2]), (first, second)
        assert not (bull and bear), "one pair cannot engulf both ways"
        seen["bullish"] += bull
        seen["bearish"] += bear
        seen["inside"] += inside
    assert all(count > MIN_SEEN for count in seen.values()), seen


def test_nr7_agrees_with_its_restatement():
    rng = random.Random(43)
    hits = 0
    for _ in range(4000):
        ranges = [rng.randint(0, 6) for _ in range(rng.randint(1, 10))]
        quads = [[0, r, 0, r] for r in ranges]
        got = verdict("nr7", quads)
        if len(ranges) < 7:
            assert got == Why(T.WARMING, (7, len(ranges)))
        else:
            last, before = ranges[-1], ranges[-7:-1]
            assert got is (last > 0 and all(last <= other for other in before)), ranges
            hits += got
    assert hits > MIN_SEEN


def moved(quad, shift=0, scale=1):
    return [value * scale + shift for value in quad]


def mirrored(quad, top):
    o, h, low, c = quad
    return [top - o, top - low, top - h, top - c]


ALL_PATTERNS = sorted(cat.PATTERNS)
ONE_CANDLE = {"doji", "hammer", "shooting_star"}
MIRROR = {
    "bullish_engulfing": "bearish_engulfing", "bearish_engulfing": "bullish_engulfing",
    "hammer": "shooting_star", "shooting_star": "hammer",
    "doji": "doji", "inside_bar": "inside_bar", "nr7": "nr7",
}  # fmt: skip


def random_run(rng: random.Random, name: str) -> list:
    size = 8 if name == "nr7" else (1 if name in ONE_CANDLE else 2)
    return [random_quad(rng, 12, 4) for _ in range(size)]


@pytest.mark.parametrize("name", ALL_PATTERNS)
def test_a_shape_does_not_change_when_the_chart_is_shifted_or_scaled(name):
    rng = random.Random(seed_of(name))
    for _ in range(1500):
        run = random_run(rng, name)
        base = verdict(name, run)
        for shift, scale in ((0, 3), (500, 1), (37, 2), (1000, 7)):
            assert verdict(name, [moved(q, shift, scale) for q in run]) == base, (name, run)


@pytest.mark.parametrize("name", ALL_PATTERNS)
def test_turning_the_chart_upside_down_turns_each_shape_into_its_opposite(name):
    rng = random.Random(seed_of(name) * 3)
    for _ in range(1500):
        run = random_run(rng, name)
        flipped = [mirrored(q, 60) for q in run]
        assert verdict(name, run) == verdict(MIRROR[name], flipped), (name, run)


@pytest.mark.parametrize("name", ALL_PATTERNS)
def test_a_shape_never_looks_ahead(name):
    rng = random.Random(seed_of(name) * 5)
    for _ in range(300):
        run = [random_quad(rng, 12, 4) for _ in range(11)]
        whole = pattern_series(run)
        for index in range(len(run)):
            cut = pattern_series(run[: index + 1])
            assert patterns.holds(name, whole, index) == patterns.holds(name, cut, index)


@pytest.mark.parametrize("name", ALL_PATTERNS)
def test_a_pattern_waits_for_exactly_the_candles_it_needs(name):
    need = cat.PATTERN_NEEDS[name]
    rng = random.Random(9)
    run = pattern_series([random_quad(rng) for _ in range(need + 2)])
    for index in range(need - 1):
        assert patterns.holds(name, run, index) == Why(T.WARMING, (need, index + 1))
    assert not isinstance(patterns.holds(name, run, need - 1), Why)


def test_patterns_answer_with_plain_booleans():
    # The engine turns the answer into TRUE or FALSE by identity, so a numpy bool would be lost.
    rng = random.Random(1)
    for name in ALL_PATTERNS:
        for _ in range(50):
            assert type(verdict(name, random_run(rng, name))) is bool
