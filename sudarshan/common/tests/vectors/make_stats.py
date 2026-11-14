"""Generator of stats.json (R1): the shared statistics, each case worked by hand below.

Imports nothing from sudarshan. Returns are whole percentages here and ``[numerator, denominator]``
pairs in the file; an expected figure is a pair too, or text of a stated number of decimals whose
square root this file takes with whole numbers (``math.isqrt``), not with the code under test.
Run: python3 make_stats.py
"""

import json
from fractions import Fraction as F
from math import isqrt
from pathlib import Path


def pair(x):
    x = F(x)
    return [x.numerator, x.denominator]


def pct(values):
    return [pair(F(v, 100)) for v in values]


def text(x, places):
    """A fraction as text, half away from zero."""
    scaled = F(x) * 10**places
    whole, rest = divmod(abs(scaled.numerator), scaled.denominator)
    whole += 2 * rest >= scaled.denominator
    digits = str(whole).rjust(places + 1, "0")
    return ("-" if scaled < 0 and whole else "") + f"{digits[:-places]}.{digits[-places:]}"


def root(x, places=40):
    """The square root of a fraction to ``places`` decimals, from a whole-number square root."""
    x = F(x)
    return F(isqrt(x.numerator * 10 ** (2 * places) // x.denominator), 10**places)


# ---- R1: the 5-day toy series ---------------------------------------------------------------
# index  r_m = +1, -2, +3, -1, +4 %   sum 5, mean 1
#   from the mean: 0, -3, +2, -2, +3   squares 0, 9, 4, 4, 9 = 26   variance 26/5 = 5.2 (%^2)
# tab    r_p = +2, -3, +5, -2, +3 %   sum 5, mean 1
#   from the mean: +1, -4, +4, -3, +2  squares 1, 16, 16, 9, 4 = 46  variance 46/5 = 9.2 (%^2)
# products of the two: 0x1 + (-3)(-4) + 2x4 + (-2)(-3) + 3x2 = 0 + 12 + 8 + 6 + 6 = 32
#   covariance 32/5 = 6.4 (%^2)
# beta = 6.4 / 5.2 = 16/13 = 1.2308
# correlation = 6.4 / sqrt(5.2 x 9.2) = 6.4 / sqrt(47.84) = 6.4 / 6.91665 = 0.9253
# downside of r_p (below 0): (-3)^2 + (-2)^2 = 13 over all 5 days = 2.6 (%^2)
R_M, R_P = [1, -2, 3, -1, 4], [2, -3, 5, -2, 3]
assert sum(R_M) == 5 and sum(R_P) == 5
assert 0 + 9 + 4 + 4 + 9 == 26 and 1 + 16 + 16 + 9 + 4 == 46 and 0 + 12 + 8 + 6 + 6 == 32
assert F(32, 5) / F(26, 5) == F(16, 13)
CORR = F(32, 5) / root(F(26, 5) * F(46, 5))
assert text(CORR, 4) == "0.9253" and text(F(16, 13), 4) == "1.2308"
P4 = 10**4  # a percentage squared is 1/10^4

MOMENTS = [
    {
        "id": "R1",
        "note": "the toy series of the header: means 1%, variances 5.2 and 9.2, "
        "covariance 6.4 (%^2), beta 16/13, correlation 6.4 / sqrt(47.84) = 0.9253, downside 2.6",
        "r_p": pct(R_P),
        "r_m": pct(R_M),
        "out": {
            "mean_p": pair(F(1, 100)),
            "mean_m": pair(F(1, 100)),
            "variance_p": pair(F(46, 5) / P4),
            "variance_m": pair(F(26, 5) / P4),
            "covariance": pair(F(32, 5) / P4),
            "beta": pair(F(16, 13)),
            "correlation": text(CORR, 30),
            "downside_p": pair(F(13, 5) / P4),
        },
    },
    # The tab moving against the index: every product changes sign, so covariance -6.4,
    # beta -16/13 and the correlation -0.9253; the variances and the downside (now of
    # -2, +3, -5, +2, -3: 4 + 25 + 9 = 38 over 5 = 7.6) follow the new series.
    {
        "id": "R2",
        "note": "the tab mirrored: covariance -6.4, beta -16/13, correlation -0.9253; "
        "downside (4 + 25 + 9) / 5 = 7.6",
        "r_p": pct([-v for v in R_P]),
        "r_m": pct(R_M),
        "out": {
            "mean_p": pair(F(-1, 100)),
            "mean_m": pair(F(1, 100)),
            "variance_p": pair(F(46, 5) / P4),
            "variance_m": pair(F(26, 5) / P4),
            "covariance": pair(F(-32, 5) / P4),
            "beta": pair(F(-16, 13)),
            "correlation": text(-CORR, 30),
            "downside_p": pair(F(38, 5) / P4),
        },
    },
    # r_m = +1, -1 (mean 0, variance 1); r_p = +3, -3 (mean 0, variance 9); covariance
    # (3 + 3) / 2 = 3; beta 3; correlation 3 / (1 x 3) = 1 exactly.
    {
        "id": "R3",
        "note": "three times the index: beta 3, correlation exactly 1",
        "r_p": pct([3, -3]),
        "r_m": pct([1, -1]),
        "out": {
            "mean_p": pair(0),
            "mean_m": pair(0),
            "variance_p": pair(F(9) / P4),
            "variance_m": pair(F(1) / P4),
            "covariance": pair(F(3) / P4),
            "beta": pair(3),
            "correlation": text(1, 30),
            "downside_p": pair(F(9, 2) / P4),
        },
    },
    {
        "id": "R4",
        "note": "the index did not move: no beta and no correlation (never 0)",
        "r_p": pct([1, -1, 2]),
        "r_m": pct([1, 1, 1]),
        "out": {
            "mean_p": pair(F(2, 300)),
            "mean_m": pair(F(1, 100)),
            # from the mean 2/3: +1/3, -5/3, +4/3; squares (1 + 25 + 16) / 9 = 42/9; over 3
            "variance_p": pair(F(42, 27) / P4),
            "variance_m": pair(0),
            "covariance": pair(0),
            "beta": None,
            "correlation": None,
            "downside_p": pair(F(1, 3) / P4),
        },
    },
    {
        "id": "R5",
        "note": "the tab did not move: beta 0 stands, no correlation",
        "r_p": pct([0, 0, 0]),
        "r_m": pct([1, -1, 3]),
        "out": {
            "mean_p": pair(0),
            "mean_m": pair(F(1, 100)),
            # from the mean 1: 0, -2, +2; squares 8 over 3
            "variance_p": pair(0),
            "variance_m": pair(F(8, 3) / P4),
            "covariance": pair(0),
            "beta": pair(0),
            "correlation": None,
            "downside_p": pair(0),
        },
    },
]
assert F(1 + 25 + 16, 9) / 3 == F(42, 27) and F(1 - 1 + 2, 3) == F(2, 3)

# ---- the window and the floor ---------------------------------------------------------------
WINDOW = [
    {
        "id": "W1",
        "note": "4 pairs where 5 are needed: short, nothing worked out",
        "r_p": pct(R_P[:4]),
        "r_m": pct(R_M[:4]),
        "min_days": 5,
        "window": 60,
        "out": {
            "state": "short",
            "correlation_state": "short",
            "days": 4,
            "need": 5,
            "beta": None,
            "correlation": None,
        },
    },
    {
        "id": "W2",
        "note": "5 pairs where 5 are needed: R1's figures",
        "r_p": pct(R_P),
        "r_m": pct(R_M),
        "min_days": 5,
        "window": 60,
        "out": {
            "state": "ok",
            "correlation_state": "ok",
            "days": 5,
            "need": 5,
            "beta": pair(F(16, 13)),
            "correlation": text(CORR, 30),
        },
    },
    {
        "id": "W3",
        "note": "a window of 2 reads only the last two pairs, R3's: beta 3, whatever came before",
        "r_p": pct([9, 9, 9, 3, -3]),
        "r_m": pct([5, -7, 2, 1, -1]),
        "min_days": 2,
        "window": 2,
        "out": {
            "state": "ok",
            "correlation_state": "ok",
            "days": 2,
            "need": 2,
            "beta": pair(3),
            "correlation": text(1, 30),
        },
    },
    {
        "id": "W4",
        "note": "R4 through the window: the index did not move",
        "r_p": pct([1, -1, 2]),
        "r_m": pct([1, 1, 1]),
        "min_days": 2,
        "window": 60,
        "out": {
            "state": "no_variation",
            "correlation_state": "no_variation",
            "days": 3,
            "need": 2,
            "beta": None,
            "correlation": None,
        },
    },
    {
        "id": "W5",
        "note": "R5 through the window: only the correlation is missing",
        "r_p": pct([0, 0, 0]),
        "r_m": pct([1, -1, 3]),
        "min_days": 2,
        "window": 60,
        "out": {
            "state": "ok",
            "correlation_state": "no_variation",
            "days": 3,
            "need": 2,
            "beta": pair(0),
            "correlation": None,
        },
    },
    {
        "id": "W6",
        "note": "no pairs at all, even with a floor of 0: short",
        "r_p": [],
        "r_m": [],
        "min_days": 0,
        "window": 60,
        "out": {
            "state": "short",
            "correlation_state": "short",
            "days": 0,
            "need": 0,
            "beta": None,
            "correlation": None,
        },
    },
]

# ---- the largest fall -----------------------------------------------------------------------
# Each step is [day, grown, base]: the index is multiplied by grown / base. It is 1 on day 1.
D = 20260100


def steps(*levels):
    """Steps that take the index through ``levels`` (hundredths) from 100 on day 1."""
    before, out = 100, []
    for at, level in enumerate(levels, start=2):
        out.append([D + at, level, before])
        before = level
    return out


FALLS = [
    # 1.00, 1.10, 0.99, 0.88, 1.10, 1.21, 1.089: from 1.10 (day 2) to 0.88 (day 4) is
    # 0.22 / 1.10 = 1/5; back at 1.10 on day 5. The later fall 1.21 to 1.089 is 1/10: smaller.
    {
        "id": "D1",
        "note": "1.10 to 0.88 is 0.22 / 1.10 = 20%, recovered at 1.10 on day 5; the "
        "later 10% fall does not replace it",
        "start_day": D + 1,
        "steps": steps(110, 99, 88, 110, 121) + [[D + 7, 1089, 1210]],
        "out": {
            "depth": pair(F(1, 5)),
            "peak_day": D + 2,
            "trough_day": D + 4,
            "recovered_day": D + 5,
        },
    },
    {
        "id": "D2",
        "note": "never below its last high: no fall, no days",
        "start_day": D + 1,
        "steps": steps(101, 105, 105, 130),
        "out": {"depth": pair(0), "peak_day": None, "trough_day": None, "recovered_day": None},
    },
    # 1.00, 1.20, 0.90, 1.00: 0.30 / 1.20 = 1/4; 1.00 is still under 1.20.
    {
        "id": "D3",
        "note": "1.20 to 0.90 is 25%; 1.00 after it is not a recovery",
        "start_day": D + 1,
        "steps": steps(120, 90, 100),
        "out": {
            "depth": pair(F(1, 4)),
            "peak_day": D + 2,
            "trough_day": D + 3,
            "recovered_day": None,
        },
    },
    {
        "id": "D4",
        "note": "two falls of 10%: the earlier is given, with its own recovery",
        "start_day": D + 1,
        "steps": steps(90, 100, 90),
        "out": {
            "depth": pair(F(1, 10)),
            "peak_day": D + 1,
            "trough_day": D + 2,
            "recovered_day": D + 3,
        },
    },
    {
        "id": "D5",
        "note": "everything lost: 100%, and nothing after it is a recovery",
        "start_day": D + 1,
        "steps": [[D + 2, 0, 100], [D + 3, 500, 100]],
        "out": {"depth": pair(1), "peak_day": D + 1, "trough_day": D + 2, "recovered_day": None},
    },
    # 1.00, 0.95, 1.00, 0.80: 5% recovered on day 3, then 20% from day 3's high.
    {
        "id": "D6",
        "note": "a later, deeper fall replaces the first: 20% from day 3, not recovered",
        "start_day": D + 1,
        "steps": steps(95, 100, 80),
        "out": {
            "depth": pair(F(1, 5)),
            "peak_day": D + 3,
            "trough_day": D + 4,
            "recovered_day": None,
        },
    },
    # 1.00, 0.90, 0.95, 0.85, 0.90: the low of one fall moves from day 2 to day 4: 15%.
    {
        "id": "D7",
        "note": "one fall with two lows: the deeper, later one is the trough (15%)",
        "start_day": D + 1,
        "steps": steps(90, 95, 85, 90),
        "out": {
            "depth": pair(F(3, 20)),
            "peak_day": D + 1,
            "trough_day": D + 4,
            "recovered_day": None,
        },
    },
    # A flat day at the high moves the high's day on: 1.00, 1.00, 0.50.
    {
        "id": "D8",
        "note": "a day level with the high is the high: the fall is counted from it",
        "start_day": D + 1,
        "steps": steps(100, 50),
        "out": {
            "depth": pair(F(1, 2)),
            "peak_day": D + 2,
            "trough_day": D + 3,
            "recovered_day": None,
        },
    },
]
assert F(110 - 88, 110) == F(1, 5) and F(121 * 10 - 1089, 1210) == F(1, 10)
assert F(120 - 90, 120) == F(1, 4) and F(100 - 85, 100) == F(3, 20)

# Twelve years of days, each 0.1% down, then one day back above the start: numbers of some
# 36,000 bits, which the code compares by their leading bits. 1 - 0.999^3000 = 95.03%.
LONG = {
    "id": "D9",
    "note": "3,000 days of -0.1% then a jump back: depth 1 - 0.999^3000 = "
    "95.03%, the low on the 3,000th day, recovered the day after",
    "start_day": 1,
    "repeat": 3000,
    "grown": 999,
    "base": 1000,
    "then": [3002, 25, 1],
    "out": {
        "depth_text": text(1 - F(999, 1000) ** 3000, 30),
        "peak_day": 1,
        "trough_day": 3001,
        "recovered_day": 3002,
    },
}
assert text(1 - F(999, 1000) ** 3000, 4) == "0.9503" and F(999, 1000) ** 3000 * 25 > 1

# ---- text -----------------------------------------------------------------------------------
STRINGS = [
    {"value": pair(F(16, 13)), "places": 4, "out": "1.2308"},
    {"value": pair(F(-16, 13)), "places": 4, "out": "-1.2308"},
    {"value": pair(F(5, 1000)), "places": 2, "out": "0.01"},
    {"value": pair(F(-5, 1000)), "places": 2, "out": "-0.01"},
    {"value": pair(F(-4, 1000)), "places": 2, "out": "0.00"},
    {"value": pair(F(4999, 1000000)), "places": 2, "out": "0.00"},
    {"value": pair(F(1995, 1000)), "places": 2, "out": "2.00"},
    {"value": pair(7), "places": 0, "out": "7"},
    {"value": pair(F(5, 2)), "places": 0, "out": "3"},
]

OUT = {
    "note": "synthetic; built by make_stats.py, which states the arithmetic of every case",
    "moments": MOMENTS,
    "window": WINDOW,
    "drawdown": FALLS,
    "drawdown_long": LONG,
    "strings": STRINGS,
}
Path(__file__).with_name("stats.json").write_text(json.dumps(OUT, indent=1) + "\n")
