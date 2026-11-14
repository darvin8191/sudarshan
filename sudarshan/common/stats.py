"""Statistics of a series of returns, in exact fractions (M7.31, M7.32, M14.3).

One copy of the arithmetic for the journal (the week against the index, analytics) and the
portfolio (benchmark figures, risk). Pure: the standard library only, no clock, no file, no log.

* A return is a ``Fraction``; a mean, a variance and a covariance are fractions too, over the
  whole series (population figures: the series is all there is, not a sample of something).
* ``beta = cov(r_p, r_m) / var(r_m)``; correlation is ``cov / (sd_p x sd_m)``. Nothing is rounded
  on the way: the one square root a figure needs is taken at the end, in Decimal with 60 digits.
* A figure that cannot be worked out is None, never 0: no beta where the index did not move, no
  correlation where either side did not.
* The largest fall is read on an index that starts at 1 and is multiplied by each period's
  growth, so money put in or taken out is never a gain or a loss. It is ``(peak - value) / peak``
  at its deepest, with the day of the peak, the day of the low and the day the peak was reached
  again, if it was.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Context, Decimal, localcontext
from fractions import Fraction
from typing import Any, NamedTuple

SQRT_DIGITS = 60
#: The arithmetic of the one square root and of a figure's text, whatever the process was told
#: (its precision, its rounding, what it traps): 60 digits, and nothing raised for rounding.
_CONTEXT = Context(prec=SQRT_DIGITS, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999, traps=[])
#: Whole numbers up to this many bits are compared exactly at once; longer ones by their leading
#: bits first (``less``).
_EXACT_BITS = 2048
_LEAD_BITS = 128


def fraction_str(frac: Fraction, places: int) -> str:
    """An exact fraction as text with ``places`` decimals, half away from zero, never "-0.00"."""
    scaled = Fraction(frac) * 10**places
    whole, rest = divmod(abs(scaled.numerator), scaled.denominator)
    if 2 * rest >= scaled.denominator:
        whole += 1
    digits = str(whole).rjust(places + 1, "0")
    sign = "-" if scaled < 0 and whole else ""
    return sign + (digits if places == 0 else f"{digits[:-places]}.{digits[-places:]}")


def decimal_str(value: Decimal, places: int) -> str:
    """A Decimal as text with ``places`` decimals, half-up, never "-0.00"."""
    with localcontext(_CONTEXT):
        text = str(value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))
    return text[1:] if text.startswith("-") and not text.strip("-0.") else text


def sqrt_ratio(frac: Fraction) -> Decimal:
    """The square root of a fraction that is not below nothing, to 60 digits."""
    with localcontext(_CONTEXT):
        return (Decimal(frac.numerator) / Decimal(frac.denominator)).sqrt()


def returns(levels: Sequence[int]) -> list[Fraction]:
    """``level / the level before - 1`` for each level after the first (levels above nothing)."""
    return [Fraction(now, before) - 1 for before, now in zip(levels, levels[1:], strict=False)]


def mean(values: Sequence[Fraction]) -> Fraction:
    return sum(values, Fraction(0)) / len(values)


def covariance(xs: Sequence[Fraction], ys: Sequence[Fraction]) -> Fraction:
    """The population covariance of two series of one length."""
    mean_x, mean_y = mean(xs), mean(ys)
    total = sum(((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)), Fraction(0))
    return total / len(xs)


def variance(values: Sequence[Fraction]) -> Fraction:
    """The population variance."""
    return covariance(values, values)


def downside_variance(values: Sequence[Fraction], floor: Fraction = Fraction(0)) -> Fraction:
    """The mean, over every return, of the square of how far it fell below ``floor`` (a return
    at or above it counts as nothing, and still counts in the mean)."""
    return sum((min(v - floor, Fraction(0)) ** 2 for v in values), Fraction(0)) / len(values)


def beta(r_p: Sequence[Fraction], r_m: Sequence[Fraction]) -> Fraction | None:
    """``cov(r_p, r_m) / var(r_m)``; None where the index did not move."""
    var_m = variance(r_m)
    return None if var_m == 0 else covariance(r_p, r_m) / var_m


def correlation(r_p: Sequence[Fraction], r_m: Sequence[Fraction]) -> Decimal | None:
    """``cov / (sd_p x sd_m)`` with the sign of the covariance; None where either did not move."""
    cov, var_p, var_m = covariance(r_p, r_m), variance(r_p), variance(r_m)
    if var_p == 0 or var_m == 0:
        return None
    root = sqrt_ratio(cov * cov / (var_p * var_m))
    # copy_negate: a plain minus would round the 60 digits to the context's 28.
    return root.copy_negate() if cov < 0 else root


def correlation_beta(
    r_p: Sequence[Fraction], r_m: Sequence[Fraction], min_days: int, window: int
) -> dict[str, Any]:
    """Beta and correlation over the last ``window`` pairs of returns, from ``min_days`` of them.

    ``state`` is "ok", "short" (fewer than ``min_days``; nothing is worked out) or "no_variation"
    (the index did not move: neither figure); ``correlation_state`` is "no_variation" as well
    where only the first series did not move, and the beta stands.
    """
    if len(r_p) != len(r_m):
        raise ValueError("the two series are not of one length")
    r_p, r_m = list(r_p)[-window:], list(r_m)[-window:]
    out: dict[str, Any] = {"beta": None, "correlation": None, "state": "ok",
                           "correlation_state": "ok", "days": len(r_p), "need": min_days}  # fmt: skip
    if len(r_p) < min_days or not r_p:
        out["state"] = out["correlation_state"] = "short"
        return out
    out["beta"] = beta(r_p, r_m)
    if out["beta"] is None:
        out["state"] = out["correlation_state"] = "no_variation"
        return out
    out["correlation"] = correlation(r_p, r_m)
    if out["correlation"] is None:
        out["correlation_state"] = "no_variation"
    return out


def less(a: int, b: int, c: int, d: int) -> bool:
    """Whether ``a / b < c / d`` for whole numbers, ``a`` and ``c`` not below nothing, ``b`` and
    ``d`` above it. Exact. Numbers of thousands of digits (a product of years of daily growth) are
    told apart by their leading bits when those settle it, and multiplied out only when not."""
    if max(a.bit_length(), b.bit_length(), c.bit_length(), d.bit_length()) <= _EXACT_BITS:
        return a * d < c * b
    (a1, ea), (b1, eb), (c1, ec), (d1, ed) = (_lead(n) for n in (a, b, c, d))
    # n lies in [n1, n1 + 1) x 2^e, so a/b < (a1 + 1) / b1 x 2^(ea - eb) and so on.
    if _scaled_le((a1 + 1) * (d1 + 1), ea + ed, c1 * b1, ec + eb):
        return True
    if _scaled_le((c1 + 1) * (b1 + 1), ec + eb, a1 * d1, ea + ed):
        return False
    return a * d < c * b


def _lead(n: int) -> tuple[int, int]:
    shift = max(0, n.bit_length() - _LEAD_BITS)
    return n >> shift, shift


def _scaled_le(x: int, ex: int, y: int, ey: int) -> bool:
    """``x x 2^ex <= y x 2^ey``."""
    return x << (ex - ey) <= y if ex >= ey else x <= y << (ey - ex)


class Drawdown(NamedTuple):
    """The largest fall of an index: ``depth`` is ``(peak - low) / peak``, 0 with no days when
    it never fell; ``recovered_day`` is the first day back at the peak, None while below it."""

    depth: Fraction
    peak_day: int | None
    trough_day: int | None
    recovered_day: int | None


def drawdown(start_day: int, periods: Iterable[tuple[int, int, int]]) -> Drawdown:
    """The largest fall of the index that is 1 on ``start_day`` and is multiplied, on each
    ``(day, grown, base)`` in order, by ``grown / base`` (``base`` above nothing, ``grown`` not
    below it). Of two falls of one depth the earlier is given."""
    peak_day = start_day
    num = den = 1  # the index over its last peak
    low: tuple[int, int, int] | None = None  # the deepest of the fall under way: num, den, day
    best: tuple[int, int, int, int, int | None] | None = None  # num, den, peak, trough, recovered
    for day, grown, base in periods:
        num, den = num * grown, den * base
        if num >= den:
            if best is not None and best[2] == peak_day and best[4] is None:
                best = (*best[:4], day)
            peak_day, num, den, low = day, 1, 1, None
            continue
        if low is None or less(num, den, low[0], low[1]):
            low = (num, den, day)
            if best is None or less(num, den, best[0], best[1]):
                best = (num, den, peak_day, day, None)
    if best is None:
        return Drawdown(Fraction(0), None, None, None)
    return Drawdown(1 - Fraction(best[0], best[1]), best[2], best[3], best[4])
