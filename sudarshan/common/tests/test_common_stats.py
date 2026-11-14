"""R1: the shared statistics (M7.31, M7.32): vectors, the exact comparison, and purity (11.3)."""

from __future__ import annotations

import ast
import json
import sys
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest

from sudarshan.common import stats

DATA = json.loads((Path(__file__).parent / "vectors" / "stats.json").read_text(encoding="utf-8"))
SOURCE = Path(stats.__file__).read_text(encoding="utf-8")


def frac(pair):
    return None if pair is None else Fraction(*pair)


def series(pairs):
    return [Fraction(*pair) for pair in pairs]


def ids(group):
    return [case["id"] for case in DATA[group]]


@pytest.mark.parametrize("case", DATA["moments"], ids=ids("moments"))
def test_moments(case):
    r_p, r_m, want = series(case["r_p"]), series(case["r_m"]), case["out"]
    assert stats.mean(r_p) == frac(want["mean_p"]) and stats.mean(r_m) == frac(want["mean_m"])
    assert stats.variance(r_p) == frac(want["variance_p"])
    assert stats.variance(r_m) == frac(want["variance_m"])
    assert stats.covariance(r_p, r_m) == frac(want["covariance"])
    assert stats.covariance(r_m, r_p) == frac(want["covariance"])
    assert stats.beta(r_p, r_m) == frac(want["beta"])
    assert stats.downside_variance(r_p) == frac(want["downside_p"])
    got = stats.correlation(r_p, r_m)
    assert (None if got is None else stats.decimal_str(got, 30)) == want["correlation"]


@pytest.mark.parametrize("case", DATA["window"], ids=ids("window"))
def test_the_window_and_the_floor(case):
    got = stats.correlation_beta(
        series(case["r_p"]), series(case["r_m"]), case["min_days"], case["window"]
    )
    if got["correlation"] is not None:
        got["correlation"] = stats.decimal_str(got["correlation"], 30)
    assert got == {**case["out"], "beta": frac(case["out"]["beta"])}


def test_two_series_of_different_lengths_are_refused():
    with pytest.raises(ValueError):
        stats.correlation_beta([Fraction(1)], [], 1, 60)
    with pytest.raises(ValueError):
        stats.covariance([Fraction(1)], [Fraction(1), Fraction(2)])


def test_returns_are_level_over_the_level_before():
    assert stats.returns([100, 110, 99]) == [Fraction(1, 10), Fraction(-1, 10)]
    assert stats.returns([100]) == [] and stats.returns([]) == []


@pytest.mark.parametrize("case", DATA["drawdown"], ids=ids("drawdown"))
def test_the_largest_fall(case):
    got = stats.drawdown(case["start_day"], [tuple(step) for step in case["steps"]])
    assert got._asdict() == {**case["out"], "depth": frac(case["out"]["depth"])}


def test_the_largest_fall_of_a_long_series_is_still_exact():
    case = DATA["drawdown_long"]
    steps = [(day + 2, case["grown"], case["base"]) for day in range(case["repeat"])]
    got = stats.drawdown(case["start_day"], [*steps, tuple(case["then"])])
    assert got.depth == 1 - Fraction(case["grown"], case["base"]) ** case["repeat"]
    assert stats.fraction_str(got.depth, 30) == case["out"]["depth_text"]
    assert got[1:] == tuple(case["out"][key] for key in ("peak_day", "trough_day", "recovered_day"))


BIG = 10**1200  # some 4,000 bits: past what is multiplied out at once


@pytest.mark.parametrize(
    ("a", "b", "c", "d"),
    [
        (1, 2, 2, 3),
        (2, 3, 1, 2),
        (1, 2, 2, 4),
        (0, 5, 0, 7),
        (0, 5, 1, BIG),
        (BIG + 1, BIG, BIG + 2, BIG),  # apart only in the last digit: the leading bits cannot say
        (BIG + 2, BIG, BIG + 1, BIG),
        (BIG, BIG + 1, BIG, BIG + 1),
        (BIG, 3 * BIG, 1, 2),  # far apart: the leading bits settle it
        (BIG, 2 * BIG, 1, 3),
        (3, 7, 3 * BIG + 1, 7 * BIG),
        (3 * BIG - 1, 7 * BIG, 3, 7),
        (BIG * BIG, BIG, 2, 1),
        (1, BIG, 1, BIG * BIG),
    ],
    ids=lambda value: "n",
)
def test_less_is_the_exact_comparison(a, b, c, d):
    assert stats.less(a, b, c, d) is (Fraction(a, b) < Fraction(c, d))
    assert stats.less(c, d, a, b) is (Fraction(c, d) < Fraction(a, b))


def test_the_leading_bits_alone_settle_numbers_that_are_far_apart(monkeypatch):
    """The quick path is taken, and is right: with the exact products made to fail, a pair far
    apart still answers, and a pair one digit apart needs the products."""

    class Whole(int):
        def __mul__(self, other):
            if max(int(self).bit_length(), int(other).bit_length()) > 2048:
                raise AssertionError("multiplied out")
            return Whole(int(self) * int(other))

        def __rshift__(self, other):
            return Whole(int(self) >> other)

    far = [Whole(n) for n in (BIG, 3 * BIG, BIG, 2 * BIG)]
    assert stats.less(*far) is True
    near = [Whole(n) for n in (BIG + 1, BIG, BIG + 2, BIG)]
    with pytest.raises(AssertionError):
        stats.less(*near)


@pytest.mark.parametrize("case", DATA["strings"], ids=lambda case: case["out"])
def test_text_is_half_away_from_zero_and_never_minus_zero(case):
    assert stats.fraction_str(frac(case["value"]), case["places"]) == case["out"]
    if case["places"]:
        value = Decimal(case["value"][0]) / Decimal(case["value"][1])
        assert stats.decimal_str(value, case["places"]) == case["out"]


def test_the_square_root_keeps_sixty_digits():
    assert str(stats.sqrt_ratio(Fraction(1, 4))) == "0.5"
    got = stats.sqrt_ratio(Fraction(2))
    assert len(str(got)) == 61 and abs(got * got - 2) < Decimal(10) ** -58


# ---- pure: the standard library only, no clock, file, log or float (design 11.3) ----------------

IMPURE = {"os", "io", "sys", "time", "logging", "socket", "sqlite3", "subprocess", "pathlib",
          "datetime", "random", "urllib", "http", "threading", "asyncio", "shutil", "tempfile"}  # fmt: skip
CALLS = {"open", "print", "input", "exec", "eval", "__import__"}


def test_stats_imports_only_the_standard_library():
    seen = set()
    for node in ast.walk(ast.parse(SOURCE)):
        if isinstance(node, ast.Import):
            seen |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "a relative import"
            seen.add((node.module or "").split(".")[0])
    assert seen and seen <= set(sys.stdlib_module_names) - IMPURE, seen


def test_stats_reads_nothing_and_uses_no_float():
    for node in ast.walk(ast.parse(SOURCE)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in CALLS, node.func.id
        assert not isinstance(node, (ast.Global, ast.AsyncFunctionDef))
        assert not (isinstance(node, ast.Constant) and isinstance(node.value, float))
        assert not (isinstance(node, ast.Name) and node.id == "float")


def test_the_journal_uses_this_copy_and_keeps_none_of_its_own():
    from sudarshan.journal.core import benchmark

    assert benchmark._sqrt_ratio is stats.sqrt_ratio
    text = Path(benchmark.__file__).read_text(encoding="utf-8")
    assert "stats.correlation_beta(" in text
    assert "mean_p" not in text and ".sqrt()" not in text and "** 2" not in text
