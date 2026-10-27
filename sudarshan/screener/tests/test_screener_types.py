"""types.py: three-valued logic, number text, candle columns, run keys."""

from __future__ import annotations

import itertools
import random
from decimal import Decimal
from fractions import Fraction

import numpy as np
import pytest
from screener_helpers import candle, ladder

from sudarshan.screener import types as t
from sudarshan.screener.types import FALSE, TRUE, UNKNOWN, Tri

# Kleene's strong logic, written as numbers so the tables below do not lean on the code: false is
# 0, unknown 1, true 2; "and" is the smallest, "or" the largest, "not" is 2 minus.
RANK = {FALSE: 0, UNKNOWN: 1, TRUE: 2}
BACK = {0: FALSE, 1: UNKNOWN, 2: TRUE}


def test_a_three_valued_result_has_no_truth_value():
    for value in Tri:
        with pytest.raises(TypeError):
            bool(value)
        with pytest.raises(TypeError):
            _ = not value


@pytest.mark.parametrize("size", [1, 2, 3, 4])
def test_all_any_none_follow_kleene_for_every_combination(size):
    for combo in itertools.product(Tri, repeat=size):
        low, high = min(RANK[x] for x in combo), max(RANK[x] for x in combo)
        assert t.all_of(combo) is BACK[low], combo
        assert t.any_of(combo) is BACK[high], combo
        assert t.none_of(combo) is BACK[2 - high], combo
        assert t.all_of(iter(combo)) is BACK[low], "any iterable, consumed once"


def test_not():
    assert t.not_(TRUE) is FALSE
    assert t.not_(FALSE) is TRUE
    assert t.not_(UNKNOWN) is UNKNOWN


def test_de_morgan_double_negation_and_order_do_not_matter():
    for combo in itertools.product(Tri, repeat=3):
        assert t.not_(t.all_of(combo)) is t.any_of([t.not_(x) for x in combo])
        assert t.not_(t.any_of(combo)) is t.all_of([t.not_(x) for x in combo])
        assert all(t.not_(t.not_(x)) is x for x in combo)
        for shuffled in itertools.permutations(combo):
            assert t.all_of(shuffled) is t.all_of(combo)
            assert t.any_of(shuffled) is t.any_of(combo)


def test_nothing_decides_nothing():
    """An empty group is UNKNOWN (3.4), never a vacuous TRUE or FALSE."""
    assert t.all_of([]) is UNKNOWN
    assert t.any_of([]) is UNKNOWN
    assert t.none_of([]) is UNKNOWN


def test_a_decided_item_beats_an_unknown_one():
    assert t.all_of([UNKNOWN, FALSE, UNKNOWN]) is FALSE
    assert t.any_of([UNKNOWN, TRUE, UNKNOWN]) is TRUE
    assert t.all_of([TRUE, UNKNOWN]) is UNKNOWN
    assert t.any_of([FALSE, UNKNOWN]) is UNKNOWN


def test_tri_of_a_bool():
    assert t.tri(True) is TRUE
    assert t.tri(False) is FALSE


@pytest.mark.parametrize(
    "value, text",
    [
        (0, "0"), (3, "3"), (-3, "-3"), (100, "100"), (Fraction(5, 2), "2.5"),
        (Fraction(-5, 2), "-2.5"), (Fraction(1, 2), "0.5"), (Fraction(1, 8), "0.125"),
        (Fraction(7, 4), "1.75"), (Fraction(250003, 100), "2500.03"),
        (Fraction(12345, 1000), "12.345"), (Fraction(1, 10**6), "0.000001"),
        (Fraction(1, 10**7), "0.0000001"), (Fraction(1, 3), "0.333333"),
        (Fraction(-1, 3), "-0.333333"), (Fraction(2, 3), "0.666667"),
        (Fraction(-1, 3 * 10**7), "0"), (Fraction(10**12), "1000000000000"),
    ],
)  # fmt: skip
def test_plain_is_clean_decimal_text(value, text):
    assert t.plain(value) == text


def test_plain_reads_back_as_the_same_number():
    rng = random.Random(7)
    for _ in range(2000):
        places = rng.randint(0, 6)
        digits = rng.randint(-(10**9), 10**9)
        value = Fraction(Decimal(digits).scaleb(-places))
        text = t.plain(value)
        assert Fraction(Decimal(text)) == value
        assert not text.endswith(".") and "e" not in text.lower()
        assert not (("." in text) and text.endswith("0")), "no trailing zeros"
        assert text != "-0"


def test_timeframes_and_units():
    assert t.TIMEFRAMES == (1, 3, 5, 15, 30, 60, "D", "W")
    assert t.MINUTE_TFS == (1, 3, 5, 15, 30, 60)
    assert set(t.UNITS) == {"paise", "qty", "pct", "rupees", "num", "state"}
    assert t.MAX_AGO == 100


def test_series_holds_read_only_integer_columns():
    candles = ladder("2026-10-28", "09:15:00", 15, [100, 101, 99, 105], v=7)
    series = t.Series.of(15, candles)
    assert len(series) == 4 and series.tf == 15 and series.forming is False
    for name in ("start", "end", "session", "o", "h", "l", "c", "v", "oi", "atp", "flags"):
        column = series.column(name)
        assert column.dtype == np.int64 and not column.flags.writeable
        with pytest.raises(ValueError):
            column[0] = 1
    assert [int(x) for x in series.c] == [100, 101, 99, 105]
    assert series.candle(2) == candles[2]
    assert series.candle(-1) == candles[3]


def test_series_finds_a_candle_by_its_start():
    candles = ladder("2026-10-28", "09:15:00", 15, [100, 101, 99])
    series = t.Series.of(15, candles, forming=True)
    assert series.forming is True
    assert series.index_of_start(candles[1].start) == 1
    assert series.index_of_start(candles[1].start + 1) is None
    assert series.index_of_start(candles[0].start - 1) is None
    assert series.index_of_start(candles[2].start + 10**9) is None


def test_an_empty_series_is_fine():
    series = t.Series.of(5, [])
    assert len(series) == 0
    assert series.index_of_start(1) is None
    assert series.c.dtype == np.int64


def test_a_candle_defaults_mean_no_open_interest_and_no_vwap():
    one = candle("2026-10-28", "09:15:00", 1, 100, 101, 99, 100, 5)
    assert one.oi == -1 and one.atp == 0 and one.flags == 0
    assert one.end - one.start == 60_000
    assert one.session == 20261028


def test_candle_flags_are_distinct_bits():
    flags = [t.UNVERIFIED, t.REPAIRED, t.LATE, t.FROM_I1]
    assert len(set(flags)) == 4
    assert all(flag & (flag - 1) == 0 for flag in flags)


def test_run_key_round_trips():
    key = t.RunKey(12, 3, 20261028, "15", 7)
    assert str(key) == "12:v3:20261028:15:7"
    assert t.RunKey.parse(str(key)) == key
    after = t.RunKey(12, 3, 20261028, "after_close", 0)
    assert t.RunKey.parse(str(after)) == after


@pytest.mark.parametrize(
    "text", ["", "1:2:3:4:5", "1:v2:3:4", "1:v2:3:4:5:6", "a:v2:3:4:5", "1:v2:x:4:5"]
)
def test_a_bad_run_key_is_refused(text):
    with pytest.raises(ValueError):
        t.RunKey.parse(text)


def test_compiled_operands_are_hashable_values():
    """An indicator instance is computed once however many times it is named."""
    a = t.Ind("ema", (("length", 20), ("source", "close")), "value", 15, 0, "paise")
    b = t.Ind("ema", (("length", 20), ("source", "close")), "value", 15, 3, "paise")
    assert a != b and a.instance == b.instance == ("ema", (("length", 20), ("source", "close")), 15)
    assert len({a, b, a}) == 2
    assert t.Num(Fraction(5), "5") == t.Num(Fraction(10, 2), "5")
