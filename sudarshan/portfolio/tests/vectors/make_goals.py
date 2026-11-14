"""Generator of goals.json (P7 lane R, M7.34): the amount to add each month, worked by hand.

Imports nothing from sudarshan and no decimal arithmetic: roots are whole-number roots of scaled
whole numbers (Newton's steps on integers), powers are exact fractions. Rupees below; the file
holds paise and basis points.
Run: python3 make_goals.py
"""

import json
from fractions import Fraction as F
from pathlib import Path

DIGITS = 30


def root12(x):
    """The 12th root of a fraction, to 30 decimals, as a fraction (rounded down)."""
    n = F(x).numerator * 10 ** (12 * DIGITS) // F(x).denominator
    guess = 1 << (n.bit_length() // 12 + 1)
    while True:
        better = (11 * guess + n // guess**11) // 12
        if better >= guess:
            break
        guess = better
    assert guess**12 <= n < (guess + 1) ** 12
    return F(guess, 10**DIGITS)


def up(x):
    """Rounded up to the whole rupee."""
    return -(-F(x).numerator // F(x).denominator)


def monthly(target, now, months, rate_bp):
    """The design's formula, step by step, in fractions."""
    i = root12(1 + F(rate_bp, 10_000)) - 1 if rate_bp else F(0)
    g = (1 + i) ** months
    need = target - now * g
    if need <= 0:
        return 0, i, g, need
    return up(need * i / (g - 1) if i else need / months), i, g, need


def rs(x, places=2):
    return f"{float(x):.{places}f}"


# GL1 (the design's): target 10,00,000 in 60 months, 2,00,000 now, 12% a year.
#   i = 1.12^(1/12) - 1 = 0.0094887929        g = 1.12^5 = 1.7623416832
#   2,00,000 grows to 3,52,468.34; need = 10,00,000 - 3,52,468.34 = 6,47,531.66
#   monthly = 6,47,531.66 x 0.0094887929 / 0.7623416832 = 8,059.76, rounded up: 8,060
got, i, g, need = monthly(1_000_000, 200_000, 60, 1200)
assert f"{float(i):.10f}" == "0.0094887929" and f"{float(g):.10f}" == "1.7623416832"
assert rs(200_000 * g) == "352468.34" and rs(need) == "647531.66" and got == 8060
assert rs(need * i / (g - 1)) == "8059.76"
# GL4: one month left, 50,000 now, 12%: 50,000 x 1.0094887929 = 50,474.44; need 49,525.56 in one
#   payment (i / (g - 1) = 1): 49,526.
assert monthly(100_000, 50_000, 1, 1200)[0] == 49_526 and rs(50_000 * (1 + i)) == "50474.44"
# GL6: 1,00,00,000 in 120 months, 10,00,000 now, 10% (the default):
#   i = 1.1^(1/12) - 1 = 0.0079741404; g = 1.1^10 = 2.5937424601; 10,00,000 grows to 25,93,742.46
#   need 74,06,257.54; monthly = 74,06,257.54 x 0.0079741404 / 1.5937424601 = 37,056.51 -> 37,057
got6, i6, g6, need6 = monthly(10_000_000, 1_000_000, 120, 1000)
assert f"{float(i6):.10f}" == "0.0079741404" and f"{float(g6):.10f}" == "2.5937424601"
assert rs(need6) == "7406257.54" and rs(need6 * i6 / (g6 - 1)) == "37056.51" and got6 == 37_057
# GL7: on track: 6,00,000 now at 12% for 60 months is 6,00,000 x 1.7623416832 = 10,57,405.01,
#   above 10,00,000: nothing to add (and never a negative amount).
assert rs(600_000 * g) == "1057405.01" and monthly(1_000_000, 600_000, 60, 1200)[0] == 0
# GL8: the smallest value now that is on track at 12% over 60 months is 10,00,000 / g =
#   5,67,426.86: 5,67,427 is on track; 5,67,426 misses by 0.86 x g = 1.51 rupees, spread over 60
#   months: under a rupee a month, rounded up to 1.
assert rs(1_000_000 / g) == "567426.86"
assert monthly(1_000_000, 567_427, 60, 1200)[0] == 0 and monthly(1_000_000, 567_426, 60, 1200)[0] == 1


def case(name, note, target, now, months, rate_bp, want):
    return {"id": name, "note": note, "target_paise": target * 100, "value_now_paise": now * 100,
            "months": months, "rate_bp": rate_bp, "required_monthly_paise": want * 100}  # fmt: skip


MONTHLY = [
    case("GL1", "the design's: 10,00,000 in 60 months, 2,00,000 now, 12%: i 0.0094887929, "
         "g 1.7623416832, need 6,47,531.66, monthly 8,059.76, shown 8,060",
         1_000_000, 200_000, 60, 1200, 8060),
    case("GL2", "no return assumed: 1,20,000 in 12 months from nothing is 10,000 a month "
         "exactly (a whole rupee is not rounded further up)", 120_000, 0, 12, 0, 10_000),
    case("GL3", "no return, 1,00,000 in 7 months: 14,285.71, rounded up to 14,286",
         100_000, 0, 7, 0, 14_286),
    case("GL4", "one month left, 50,000 now, 12%: need 49,525.56, all of it at once: 49,526",
         100_000, 50_000, 1, 1200, 49_526),
    case("GL5", "months of 0 (due this month, or overdue) count as 1: GL4's answer",
         100_000, 50_000, 0, 1200, 49_526),
    case("GL5b", "months below 0 count as 1 too", 100_000, 50_000, -7, 1200, 49_526),
    case("GL6", "the default 10%: 1,00,00,000 in 120 months, 10,00,000 now: need 74,06,257.54, "
         "monthly 37,056.51, shown 37,057", 10_000_000, 1_000_000, 120, 1000, 37_057),
    case("GL7", "on track: 6,00,000 at 12% for 60 months is 10,57,405.01: nothing to add, "
         "never a negative amount", 1_000_000, 600_000, 60, 1200, 0),
    case("GL8", "just on track: 5,67,427 x 1.7623416832 is past 10,00,000",
         1_000_000, 567_427, 60, 1200, 0),
    case("GL8b", "a rupee less misses by 1.51 rupees in all: 1 rupee a month, never 0",
         1_000_000, 567_426, 60, 1200, 1),
    case("GL9", "already there, no return: target met exactly, nothing to add",
         100_000, 100_000, 12, 0, 0),
    case("GL10", "nothing saved, one month, no return: the whole target", 100_000, 0, 1, 0, 100_000),
    case("GL11", "a rupee short with no return over 12 months: 0.0833 a month, rounded up to 1",
         100_000, 99_999, 12, 0, 1),
]
for row in MONTHLY:  # every expected amount is what the fractions above give
    assert row["required_monthly_paise"] == 100 * monthly(
        F(row["target_paise"], 100), F(row["value_now_paise"], 100), max(1, row["months"]),
        row["rate_bp"])[0], row["id"]  # fmt: skip

# ---- whole months --------------------------------------------------------------------------------
# (2031 - 2026) x 12 + (3 - 11) = 52, and the 31st is not before the 14th.
MONTHS = [
    {"id": "MO1", "note": "14 Nov 2026 to 31 Mar 2031: 60 - 8 = 52 (the design's card)",
     "as_of_day": 20261114, "target_day": 20310331, "months": 52},
    {"id": "MO2", "note": "the same day of the month is a whole month",
     "as_of_day": 20261114, "target_day": 20261214, "months": 1},
    {"id": "MO3", "note": "a day short of it is not", "as_of_day": 20261114,
     "target_day": 20261213, "months": 0},
    {"id": "MO4", "note": "across the year end: 14 Nov to 13 Jan is 1, to 14 Jan is 2",
     "as_of_day": 20261114, "target_day": 20270113, "months": 1},
    {"id": "MO5", "note": "14 Nov to 14 Jan: 2", "as_of_day": 20261114, "target_day": 20270114,
     "months": 2},
    {"id": "MO6", "note": "the target day itself: 0", "as_of_day": 20261114,
     "target_day": 20261114, "months": 0},
    {"id": "MO7", "note": "overdue: 0, never below", "as_of_day": 20261114,
     "target_day": 20250101, "months": 0},
    {"id": "MO8", "note": "31 Jan to 28 Feb: the 28th is before the 31st, so 0; to 31 Mar: 2",
     "as_of_day": 20270131, "target_day": 20270228, "months": 0},
    {"id": "MO9", "note": "31 Jan to 31 Mar: 2", "as_of_day": 20270131, "target_day": 20270331,
     "months": 2},
    {"id": "MO10", "note": "ten years to the day: 120", "as_of_day": 20261114,
     "target_day": 20361114, "months": 120},
]

# ---- the card --------------------------------------------------------------------------------
# 2,00,000 of 10,00,000 is 20%; 14 Nov 2026 to 14 Nov 2031 is 60 months: GL1's 8,060.
# 12,50,000 of 10,00,000 is 125% (not capped here) and on track.
CARDS = [
    {"id": "GC1", "note": "GL1 as a card: 20% there, 60 months left, 8,060 a month",
     "target_paise": 100_000_000, "value_now_paise": 20_000_000, "as_of_day": 20261114,
     "target_day": 20311114, "rate_bp": 1200,
     "expect": {"value_now_paise": 20_000_000, "progress_pct": "20.0000",
                "required_monthly_paise": 806_000, "months_left": 60, "on_track": False}},
    {"id": "GC2", "note": "past the target: 125%, not capped; on track",
     "target_paise": 100_000_000, "value_now_paise": 125_000_000, "as_of_day": 20261114,
     "target_day": 20311114, "rate_bp": 1000,
     "expect": {"value_now_paise": 125_000_000, "progress_pct": "125.0000",
                "required_monthly_paise": 0, "months_left": 60, "on_track": True}},
    {"id": "GC3", "note": "overdue and short: 0 months left, and all of what is missing asked "
     "for at once (GL4: 49,526)",
     "target_paise": 10_000_000, "value_now_paise": 5_000_000, "as_of_day": 20261114,
     "target_day": 20261101, "rate_bp": 1200,
     "expect": {"value_now_paise": 5_000_000, "progress_pct": "50.0000",
                "required_monthly_paise": 4_952_600, "months_left": 0, "on_track": False}},
    {"id": "GC4", "note": "nothing saved yet: 0%", "target_paise": 12_000_000,
     "value_now_paise": 0, "as_of_day": 20261114, "target_day": 20271114, "rate_bp": 0,
     "expect": {"value_now_paise": 0, "progress_pct": "0.0000",
                "required_monthly_paise": 1_000_000, "months_left": 12, "on_track": False}},
    {"id": "GC5", "note": "GL7 as a card: 60% there and on track, because what is there grows "
     "past the target by the day (on track is not the same as already there)",
     "target_paise": 100_000_000, "value_now_paise": 60_000_000, "as_of_day": 20261114,
     "target_day": 20311114, "rate_bp": 1200,
     "expect": {"value_now_paise": 60_000_000, "progress_pct": "60.0000",
                "required_monthly_paise": 0, "months_left": 60, "on_track": True}},
]

OUT = {"note": "synthetic; built by make_goals.py, which states the arithmetic of every case",
       "monthly": MONTHLY, "months": MONTHS, "cards": CARDS}  # fmt: skip
Path(__file__).with_name("goals.json").write_text(json.dumps(OUT, indent=1) + "\n")
