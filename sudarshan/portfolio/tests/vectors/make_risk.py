"""Generator of risk.json (P7 lane R, M7.32): the risk figures, each worked by hand below.

Imports nothing from sudarshan. A period is [start, day, base, grown] (performance.Period); a
point is [day, value, in, out, fee] in paise. Square roots are taken here with whole numbers
(``math.isqrt``), not with the code under test. November 2026: the 2nd is a Monday.
Run: python3 make_risk.py
"""

from datetime import date, timedelta
from fractions import Fraction as F
from math import isqrt
from pathlib import Path

from portfolio_vector_tools import write_all


def key(d):
    return d.year * 10000 + d.month * 100 + d.day


def weekdays(first, count):
    out, d = [], first
    while len(out) < count:
        if d.weekday() < 5:
            out.append(key(d))
        d += timedelta(days=1)
    return out


def text(x, places=4):
    scaled = F(x) * 10**places
    whole, rest = divmod(abs(scaled.numerator), scaled.denominator)
    whole += 2 * rest >= scaled.denominator
    digits = str(whole).rjust(places + 1, "0")
    return ("-" if scaled < 0 and whole else "") + f"{digits[:-places]}.{digits[-places:]}"


def root(x, places=30):
    x = F(x)
    return F(isqrt(x.numerator * 10 ** (2 * places) // x.denominator), 10**places)


# ---- the series: the toy returns of make_stats.py, twelve times over (60 days) ---------------
# day returns +2, -3, +5, -2, +3 %: mean 1, variance 46/5 = 9.2 (%^2), downside (9 + 4) / 5 = 2.6;
# repeating the block changes neither (each is an average).
#   volatility = sqrt(9.2 x 252) = sqrt(2318.4) = 48.1498 %
#   downside deviation, yearly = sqrt(2.6 x 252) = sqrt(655.2) = 25.5969 %
# The index: 1, 1.02, 0.9894 (x0.97), 1.038870 (x1.05), 1.0180926 (x0.98), 1.048635378 (x1.03).
#   from 1.02 down to 0.9894 is 3%, recovered next day (1.03887 > 1.02); from 1.03887 to
#   1.0180926 is 2%, recovered next day. Every block repeats both, so the largest fall is 3%, and
#   of equal falls the first is given: peak day 2, low day 3, recovered day 4 of the series.
# With an annualised TWR of 20% and a risk-free rate of 6.50%:
#   Sharpe  = (20 - 6.5) / 48.1498 = 0.2804      Sortino = 13.5 / 25.5969 = 0.5274
#   Calmar  = 20 / 3 = 6.6667
# With 4% a year: Sharpe = (4 - 6.5) / 48.1498 = -0.0519, Sortino = -0.0977, Calmar 1.3333.
BLOCK = [2, -3, 5, -2, 3]
VOL, DOWN = root(F(46, 5) * 252), root(F(13, 5) * 252)
assert text(VOL) == "48.1498" and text(DOWN) == "25.5969"
assert text(F(27, 2) / VOL) == "0.2804" and text(F(27, 2) / DOWN) == "0.5274"
assert text(F(-5, 2) / VOL) == "-0.0519" and text(F(-5, 2) / DOWN) == "-0.0977"
assert F(102 * 97, 100 * 100) == F(9894, 10000) and 1 - F(97, 100) == F(3, 100)
assert F(102 * 97 * 105, 10**6) > F(102, 100)  # recovered on the third day


def periods_of(days, rates):
    """One period a day: [start, day, base 100, grown 100 + r]."""
    return [[a, b, 100, 100 + r] for a, b, r in zip(days[:-1], days[1:], rates, strict=True)]


D61 = weekdays(date(2026, 8, 3), 61)  # a Monday
P60 = periods_of(D61, BLOCK * 12)
P59 = P60[:59]
WAIT1 = "Shown after 60 trading days of closing values: 1 more to go."
YEAR = "Not yet: needs a year of daily history."


def stat(value=None, note=None):
    return {"value": value, "note": note}


def fell(pct, peak, trough, back, note=None):
    return {"pct": pct, "peak_day": peak, "trough_day": trough, "recovered_day": back,
            "note": note}  # fmt: skip


FALL60 = fell("3.0000", D61[1], D61[2], D61[3])
# S5: sixty days of +1% and one of -50% in a week with a Wednesday missing. The fall counts the
# period that spans the missing day (it is kept in the largest fall); the deviation does not.
UP = weekdays(date(2026, 8, 3), 64)
P_UP = periods_of(UP[:61], [1] * 60) + [[UP[60], UP[62], 100, 50], [UP[62], UP[63], 100, 101]]
assert len(P_UP) == 62 and date(2026, 8, 3).weekday() == 0
# 61 one-day returns of +1%: variance 0, volatility 0; no down day. The fall is 50% from the
# day before the gap to the day after it, not recovered (0.5 x 1.01 < 1).

# S7: five louder days before the sixty: +10, -10, +10, -10, 0 %, then the block twelve times.
#   65 returns: sum 0 + 12 x 5 = 60, mean 60/65 = 12/13
#   squares: 4 x 100 + 12 x (4 + 9 + 25 + 4 + 9 = 51) = 400 + 612 = 1012
#   variance = 1012/65 - (12/13)^2 = (1012 x 13 - 144 x 5) / 845 = 12436/845 = 14.7172 (%^2)
#   volatility = sqrt(12436/845 x 252) = 60.8993 %  (of all 65 days, not of the last sixty: 48.15)
#   downside: (100 + 100 + 12 x 13) / 65 = 356/65; yearly sqrt(356/65 x 252) = 37.1508 %
#   index: 1.1, 0.99, 1.089, 0.9801, 0.9801, then x1.02 x0.97 = 0.96971094 (the low, day 7):
#   fall = 1 - 0.96971094 / 1.1 = 1 - 0.891 x 0.9894 = 11.8445 %, from day 1; back above 1.1 on
#   the 3rd day of the 3rd block (day 18): 0.9801 x 1.048635378^2 x 1.02 x 0.97 x 1.05 = 1.1196
D66 = weekdays(date(2026, 7, 27), 66)
LOUD = [10, -10, 10, -10, 0] + BLOCK * 12
P65 = periods_of(D66, LOUD)
VAR65 = F(1012, 65) - F(12, 13) ** 2
assert VAR65 == F(12436, 845) and sum(LOUD) == 60 and sum(r * r for r in LOUD) == 1012
VOL65, DOWN65 = root(VAR65 * 252), root(F(356, 65) * 252)
assert text(VOL65) == "60.8993" and text(DOWN65) == "37.1508"
LOW65 = F(11, 10) * F(9, 10) * F(11, 10) * F(9, 10) * F(102, 100) * F(97, 100)
assert text((1 - LOW65 / F(11, 10)) * 100) == "11.8445"
BACK = F(9801, 10000) * F(1048635378, 10**9) ** 2 * F(102 * 97 * 105, 10**6)
assert BACK >= F(11, 10) > BACK / F(105, 100)
FALL65 = F(11, 10) - LOW65

SERIES = [
    {"id": "S7", "note": "65 days, the first five louder: the deviation is of all 65 "
     "(variance 12436/845, volatility 60.8993%), the fall 11.8445% from day 1 to day 7, "
     "recovered on day 18; Sharpe 13.5 / 60.8993, Sortino 13.5 / 37.1508, Calmar 20 / 11.8445",
     "periods": P65, "annualised_pct": "20.0000", "risk_free_bp": 650, "holidays": [],
     "expect": {"days": 65,
                "drawdown": {"pct": "11.8445", "peak_day": D66[1], "trough_day": D66[7],
                             "recovered_day": D66[18], "note": None},
                "volatility": {"value": "60.8993", "note": None},
                "sharpe": {"value": text(F(27, 2) / VOL65), "note": None},
                "sortino": {"value": text(F(27, 2) / DOWN65), "note": None},
                "calmar": {"value": text(F(1, 5) / (FALL65 / F(11, 10))), "note": None}}},
    {"id": "S1", "note": "60 days, annualised TWR 20%, risk-free 6.50%: volatility 48.1498%, "
     "Sharpe 13.5 / 48.1498 = 0.2804, Sortino 13.5 / 25.5969 = 0.5274, fall 3%, Calmar 20 / 3",
     "periods": P60, "annualised_pct": "20.0000", "risk_free_bp": 650, "holidays": [],
     "expect": {"days": 60, "drawdown": FALL60, "volatility": stat("48.1498"),
                "sharpe": stat("0.2804"), "sortino": stat("0.5274"), "calmar": stat("6.6667")}},
    {"id": "S2", "note": "the same earning 4% a year, below the risk-free 6.50%: Sharpe and "
     "Sortino are negative (-2.5 over each deviation); Calmar 4 / 3 = 1.3333",
     "periods": P60, "annualised_pct": "4.0000", "risk_free_bp": 650, "holidays": [],
     "expect": {"days": 60, "drawdown": FALL60, "volatility": stat("48.1498"),
                "sharpe": stat("-0.0519"), "sortino": stat("-0.0977"), "calmar": stat("1.3333")}},
    {"id": "S3", "note": "59 days: every figure withheld, with how many more it needs",
     "periods": P59, "annualised_pct": "20.0000", "risk_free_bp": 650, "holidays": [],
     "expect": {"days": 59, "drawdown": fell(None, None, None, None, WAIT1),
                "volatility": stat(note=WAIT1), "sharpe": stat(note=WAIT1),
                "sortino": stat(note=WAIT1), "calmar": stat(note=WAIT1)}},
    {"id": "S4", "note": "60 days but under a year (no annualised TWR): the fall and the "
     "volatility are shown; the three ratios wait for a year",
     "periods": P60, "annualised_pct": None, "risk_free_bp": 650, "holidays": [],
     "expect": {"days": 60, "drawdown": FALL60, "volatility": stat("48.1498"),
                "sharpe": stat(note=YEAR), "sortino": stat(note=YEAR), "calmar": stat(note=YEAR)}},
    {"id": "S5", "note": "61 one-day returns of +1% and a 50% fall across a day with no "
     "complete value: the fall is counted (50%, not recovered), the deviation is of the 61 days "
     "(0), so no Sharpe and no Sortino; Calmar 10 / 50 = 0.2",
     "periods": P_UP, "annualised_pct": "10.0000", "risk_free_bp": 650, "holidays": [],
     "expect": {"days": 61, "drawdown": fell("50.0000", UP[60], UP[62], None),
                "volatility": stat("0.0000"),
                "sharpe": stat(note="The value did not move on these days, so there is no ratio yet."),
                "sortino": stat(note="No losing day yet, so there is no Sortino ratio."),
                "calmar": stat("0.2000")}},
    {"id": "S6", "note": "60 days that only rise: no fall, so no Calmar; no down day, so no "
     "Sortino; the deviation of a constant +1% is 0, so no Sharpe",
     "periods": periods_of(D61, [1] * 60), "annualised_pct": "10.0000", "risk_free_bp": 650,
     "holidays": [],
     "expect": {"days": 60, "drawdown": fell("0.0000", None, None, None, "No fall from a high yet."),
                "volatility": stat("0.0000"),
                "sharpe": stat(note="The value did not move on these days, so there is no ratio yet."),
                "sortino": stat(note="No losing day yet, so there is no Sortino ratio."),
                "calmar": stat(note="No fall from a high yet.")}},
]

# ---- the fall is of the index, not of the value: a deposit on the low ------------------------
# Mon: 1,000 put in, worth 1,000.  Tue: 1,100 (index 1.10).  Wed: 1,000 (index 1.00).
# Thu: 1,000 more put in; the 2,000 at work close at 1,800: x 0.9, index 0.90 (the low).
# Fri: 1,890: x 1.05, index 0.945, still under 1.10.
# Fall = (1.10 - 0.90) / 1.10 = 2/11 = 18.1818%, from Tuesday to Thursday, not recovered.
# On the raw value the high would be 1,100, the low 1,000 (9.09%), and Thursday's 1,800 a
# recovery that never happened.
MON = 20261102
DEPOSIT = {"id": "F1", "note": "a deposit on the low is no recovery: 2/11 = 18.1818% from "
           "Tuesday to Thursday, not recovered; on the raw value it would read 9.09% and recovered",
           "points": [[MON, 100_000, 100_000, 0, 0], [MON + 1, 110_000, 0, 0, 0],
                      [MON + 2, 100_000, 0, 0, 0], [MON + 3, 180_000, 100_000, 0, 0],
                      [MON + 4, 189_000, 0, 0, 0]],
           "opening_paise": 0,
           "expect": {"depth": [2, 11], "peak_day": MON + 1, "trough_day": MON + 3,
                      "recovered_day": None}}
assert F(110 - 90, 110) == F(2, 11) and text(F(200, 11)) == "18.1818" and F(1800, 2000) == F(9, 10)
# A withdrawal is no fall: 1,000, then 600 taken out leaving 400 (x1), then 440 (x1.1).
WITHDRAW = {"id": "F2", "note": "money taken out is no fall: the index never drops",
            "points": [[MON, 100_000, 100_000, 0, 0], [MON + 1, 40_000, 0, 60_000, 0],
                       [MON + 2, 44_000, 0, 0, 0]],
            "opening_paise": 0,
            "expect": {"depth": [0, 1], "peak_day": None, "trough_day": None,
                       "recovered_day": None}}

# ---- concentration ---------------------------------------------------------------------------
# Rupees: A 3,000 (Banks), B 2,500 (Banks), C 1,500 (IT), D 1,400, E 900 (IT), F 700 = 10,000.
#   A 30%, B 25%, C 15% (at the 15% limit: not above it), D 14%, E 9%, F 7%
#   Banks 55% (above 35%), IT 24%; untagged D + F = 21%
def holding(k, rupees, sector=None, label=None):
    return [k, label or k, rupees * 100, sector]


BOOK = [holding("A", 3000, "Banks"), holding("B", 2500, "Banks"), holding("C", 1500, "IT"),
        holding("D", 1400), holding("E", 900, "IT"), holding("F", 700)]  # fmt: skip
assert 3000 + 2500 + 1500 + 1400 + 900 + 700 == 10_000 and 3000 + 2500 == 5500


def bar(label, weight, over=False):
    return {"label": label, "weight_pct": weight, "over_limit": over}


CONCENTRATION = [
    {"id": "N1", "note": "A 30% and B 25% are above 15%; C at exactly 15% is not; the five "
     "largest are listed; Banks 55% is above 35%; D and F (21%) have no sector tag",
     "holdings": BOOK, "max_holding_pct": "15.0000", "max_sector_pct": "35.0000",
     "expect": {"holdings": [bar("A", "30.0000", True), bar("B", "25.0000", True),
                             bar("C", "15.0000"), bar("D", "14.0000"), bar("E", "9.0000")],
                "sectors": [bar("Banks", "55.0000", True), bar("IT", "24.0000")],
                "untagged_pct": "21.0000",
                "flags": ["A is 30% of what is held here, above your 15% limit.",
                          "B is 25% of what is held here, above your 15% limit.",
                          "The sector Banks is 55% of what is held here, above your 35% limit.",
                          "Untagged: 21%; tag them to check sectors."]}},
    # Limits of 5% and 20%: all six holdings are above 5% and all six are listed (a share over
    # its limit is always listed); Banks and IT are both above 20%.
    {"id": "N2", "note": "limits of 5% and 20%: the sixth holding is over its limit, so it is "
     "listed past the five", "holdings": BOOK, "max_holding_pct": "5.0000",
     "max_sector_pct": "20.0000",
     "expect": {"holdings": [bar("A", "30.0000", True), bar("B", "25.0000", True),
                             bar("C", "15.0000", True), bar("D", "14.0000", True),
                             bar("E", "9.0000", True), bar("F", "7.0000", True)],
                "sectors": [bar("Banks", "55.0000", True), bar("IT", "24.0000", True)],
                "untagged_pct": "21.0000",
                "flags": [f"{k} is {w}% of what is held here, above your 5% limit."
                          for k, w in (("A", 30), ("B", 25), ("C", 15), ("D", 14), ("E", 9), ("F", 7))]
                + ["The sector Banks is 55% of what is held here, above your 20% limit.",
                   "The sector IT is 24% of what is held here, above your 20% limit.",
                   "Untagged: 21%; tag them to check sectors."]}},
    # One instrument in two accounts is one holding: 1,000 + 2,000 of X against 1,000 of Y is
    # 75% and 25%; a loan (a value below nothing) and an empty line are no holdings.
    # 2,256.50 of 10,000.00 would be 22.565%: the sentence says 22.57 (N4).
    {"id": "N3", "note": "X in two accounts is one holding of 3,000 (75%); what is owed and an "
     "empty line are left out; everything tagged, so untagged is 0 and says nothing",
     "holdings": [holding("X", 1000, "Pharma"), holding("Y", 1000, "Pharma"),
                  holding("X", 2000, "Pharma"), holding("LOAN", -5000), holding("Z", 0)],
     "max_holding_pct": "80.0000", "max_sector_pct": "100.0000",
     "expect": {"holdings": [bar("X", "75.0000"), bar("Y", "25.0000")],
                "sectors": [bar("Pharma", "100.0000")], "untagged_pct": "0.0000", "flags": []}},
    {"id": "N4", "note": "2,256.50 of 10,000 is 22.5650%: the sentence says 22.57 (half up); "
     "a limit of 22.56% is crossed",
     "holdings": [["P", "P", 225_650, "Auto"], ["Q", "Q", 774_350, "Auto"]],
     "max_holding_pct": "22.5600", "max_sector_pct": "100.0000",
     "expect": {"holdings": [bar("Q", "77.4350", True), bar("P", "22.5650", True)],
                "sectors": [bar("Auto", "100.0000")], "untagged_pct": "0.0000",
                "flags": ["Q is 77.44% of what is held here, above your 22.56% limit.",
                          "P is 22.57% of what is held here, above your 22.56% limit."]}},
    {"id": "N5", "note": "nothing held: no bars, no figure, no flag", "holdings": [],
     "max_holding_pct": "15.0000", "max_sector_pct": "35.0000",
     "expect": {"holdings": [], "sectors": [], "untagged_pct": None, "flags": []}},
]
assert 225_650 + 774_350 == 1_000_000

# ---- concentration from a tab's rows (the phone's live bars) -------------------------------------
# The risk op sends the rows its concentration was worked out from; the phone prices them from
# ticks and applies the rule above. A row is a holding as the tab's view gives it (HoldingRow, the
# keys the rule reads): the instrument is the asset id, the label its name, the value each line's
# units at the price (qty_u x price_u / 10^8, in paise) or its cost while there is no price.
#   Alpha Ltd (asset 1, Banks) is in two tabs: 12 and 8 shares at 250.00 = 3,000 + 2,000 = 5,000
#   Beta Fund (asset 2, no sector) has no price: its cost, 3,000
#   Gamma Ltd (asset 3, IT) in two accounts: 4 + 6 shares at 150.00 = 1,500
#   a loan and a row of no units are no holdings.               total 9,500
# "holdings" is what the rows map to, one per row in their order (those worth something).
def row(asset_id, name, kind, sector, price, lines, symbol=None):
    return {"asset": {"id": asset_id, "name": name, "kind": kind, "sector": sector,
                      "exchange": "NSE" if symbol else None, "symbol": symbol},
            "price": {"price_u": None if price is None else price * 10_000, "prev_close_u": None},
            "lines": [{"qty_u": qty * 1_000_000, "cost_paise": cost * 100} for qty, cost in lines]}  # fmt: skip


def rows_at(alpha):
    return [row(1, "Alpha Ltd", "listed", "Banks", alpha, [(12, 2400)], "ALPHA"),
            row(2, "Beta Fund", "mf", None, None, [(100, 3000)]),
            row(3, "Gamma Ltd", "listed", "IT", 150, [(4, 500), (6, 800)], "GAMMA"),
            row(9, "Home loan", "liability", None, None, [(1, 500_000)]),
            row(4, "Sold Ltd", "listed", "IT", 90, [(0, 0)], "SOLD"),
            row(1, "Alpha Ltd", "listed", "Banks", alpha, [(8, 1700)], "ALPHA")]  # fmt: skip


assert 12 * 250 + 8 * 250 == 5000 and 4 * 150 + 6 * 150 == 1500 and 5000 + 3000 + 1500 == 9500
assert [text(F(v * 100, 9500)) for v in (5000, 3000, 1500)] == ["52.6316", "31.5789", "15.7895"]
assert 20 * 500 + 3000 + 1500 == 14_500
assert [text(F(v * 100, 14_500)) for v in (10_000, 3000, 1500)] == ["68.9655", "20.6897", "10.3448"]
ROWS = [
    {"id": "RW1", "note": "the same asset in two tabs is one holding (Alpha 5,000 of 9,500); a "
     "fund with no price counts at its cost; two accounts of one row add up; a loan and a row of "
     "no units are left out",
     "rows": rows_at(250), "max_holding_pct": "15.0000", "max_sector_pct": "35.0000",
     "holdings": [["1", "Alpha Ltd", 300_000, "Banks"], ["2", "Beta Fund", 300_000, None],
                  ["3", "Gamma Ltd", 150_000, "IT"], ["1", "Alpha Ltd", 200_000, "Banks"]],
     "expect": {"holdings": [bar("Alpha Ltd", "52.6316", True), bar("Beta Fund", "31.5789", True),
                             bar("Gamma Ltd", "15.7895", True)],
                "sectors": [bar("Banks", "52.6316", True), bar("IT", "15.7895")],
                "untagged_pct": "31.5789",
                "flags": ["Alpha Ltd is 52.63% of what is held here, above your 15% limit.",
                          "Beta Fund is 31.58% of what is held here, above your 15% limit.",
                          "Gamma Ltd is 15.79% of what is held here, above your 15% limit.",
                          "The sector Banks is 52.63% of what is held here, above your 35% limit.",
                          "Untagged: 31.58%; tag them to check sectors."]}},
    {"id": "RW2", "note": "a tick takes Alpha to 500.00: 10,000 of 14,500; every weight moves, "
     "and Gamma (10.34%) is no longer above 15%",
     "rows": rows_at(500), "max_holding_pct": "15.0000", "max_sector_pct": "35.0000",
     "holdings": [["1", "Alpha Ltd", 600_000, "Banks"], ["2", "Beta Fund", 300_000, None],
                  ["3", "Gamma Ltd", 150_000, "IT"], ["1", "Alpha Ltd", 400_000, "Banks"]],
     "expect": {"holdings": [bar("Alpha Ltd", "68.9655", True), bar("Beta Fund", "20.6897", True),
                             bar("Gamma Ltd", "10.3448")],
                "sectors": [bar("Banks", "68.9655", True), bar("IT", "10.3448")],
                "untagged_pct": "20.6897",
                "flags": ["Alpha Ltd is 68.97% of what is held here, above your 15% limit.",
                          "Beta Fund is 20.69% of what is held here, above your 15% limit.",
                          "The sector Banks is 68.97% of what is held here, above your 35% limit.",
                          "Untagged: 20.69%; tag them to check sectors."]}},
]  # fmt: skip

# ---- overlap -----------------------------------------------------------------------------------
# Tab 1: A 50%, B 30%, C 20%.   Tab 2: A 20%, B 40%, D 40%.
#   min(50, 20) + min(30, 40) = 20 + 30 = 50%; B (30) before A (20). C and D are in one tab only.
OVERLAP = [
    {"id": "O1", "note": "A: min(50, 20) = 20; B: min(30, 40) = 30; overlap 50%, B named first",
     "tab_id": 2,
     "mine": [holding("A", 500), holding("B", 300), holding("C", 200)],
     "theirs": [holding("A", 1000), holding("B", 2000), holding("D", 2000)],
     "expect": {"tab_id": 2, "weight_pct": "50.0000", "names": ["B", "A"]}},
    {"id": "O2", "note": "nothing in common: 0%", "tab_id": 3,
     "mine": [holding("A", 500)], "theirs": [holding("D", 100)],
     "expect": {"tab_id": 3, "weight_pct": "0.0000", "names": []}},
    {"id": "O3", "note": "the same holdings in the same shares, whatever the sizes: 100%",
     "tab_id": 4, "mine": [holding("A", 100), holding("B", 300)],
     "theirs": [holding("A", 5), holding("B", 15)],
     "expect": {"tab_id": 4, "weight_pct": "100.0000", "names": ["B", "A"]}},
    {"id": "O4", "note": "the other tab holds nothing", "tab_id": 5,
     "mine": [holding("A", 100)], "theirs": [],
     "expect": {"tab_id": 5, "weight_pct": "0.0000", "names": []}},
    # 1/3 each of A, B, C against A alone: min(1/3, 1) = 33.3333%
    {"id": "O5", "note": "a third of one tab is all of the other: 33.3333%", "tab_id": 6,
     "mine": [holding("A", 100), holding("B", 100), holding("C", 100)],
     "theirs": [holding("A", 7)],
     "expect": {"tab_id": 6, "weight_pct": "33.3333", "names": ["A"]}},
]

# ---- the emergency fund ------------------------------------------------------------------------
# 3,00,000 liquid over 40,000 a month = 7.5 months (target 6: enough); 1,00,000 = 2.5 months.
EMERGENCY = [
    {"id": "E1", "note": "3,00,000 / 40,000 = 7.5 months, target 6", "liquid_paise": 30_000_000,
     "monthly_expenses_paise": 4_000_000, "months_target": 6,
     "expect": {"months": "7.5000", "target_months": 6,
                "note": "Covers 7.50 months of your expenses: at or above your target of 6."}},
    {"id": "E2", "note": "1,00,000 / 40,000 = 2.5 months, target 6", "liquid_paise": 10_000_000,
     "monthly_expenses_paise": 4_000_000, "months_target": 6,
     "expect": {"months": "2.5000", "target_months": 6,
                "note": "Covers 2.50 months of your expenses; your target is 6."}},
    {"id": "E3", "note": "exactly 6 months is enough", "liquid_paise": 24_000_000,
     "monthly_expenses_paise": 4_000_000, "months_target": 6,
     "expect": {"months": "6.0000", "target_months": 6,
                "note": "Covers 6 months of your expenses: at or above your target of 6."}},
    {"id": "E4", "note": "expenses not set: no figure, and it says what to do",
     "liquid_paise": 24_000_000, "monthly_expenses_paise": None, "months_target": 6,
     "expect": {"months": None, "target_months": 6,
                "note": "Set your monthly expenses to check this"}},
    {"id": "E5", "note": "nothing liquid: 0 months", "liquid_paise": 0,
     "monthly_expenses_paise": 4_000_000, "months_target": 3,
     "expect": {"months": "0.0000", "target_months": 3,
                "note": "Covers 0 months of your expenses; your target is 3."}},
]

# ---- several tabs as one -----------------------------------------------------------------------
# Tab 1 from Monday, tab 2 from Wednesday. Tuesday has only tab 1; on Thursday tab 2 has no
# complete value (no point), so the day has none; Friday both.
TOGETHER = [
    {"id": "T1", "note": "values and money add up; a tab adds nothing before it begins; a day "
     "one begun tab cannot value has no value",
     "tabs": [[[MON, 1000, 1000, 0, 0], [MON + 1, 1010, 0, 0, 0], [MON + 2, 1020, 0, 0, 0],
               [MON + 3, 1030, 0, 0, 0], [MON + 4, 1040, 0, 0, 0]],
              [[MON + 2, 500, 500, 0, 0], [MON + 4, 520, 0, 10, 5]]],
     "expect": {"points": [[MON, 1000, 1000, 0, 0], [MON + 1, 1010, 0, 0, 0],
                           [MON + 2, 1520, 500, 0, 0], [MON + 3, None, 0, 0, 0],
                           [MON + 4, 1560, 0, 10, 5]], "opening_paise": 0}},
    {"id": "T2", "note": "the first day has no complete value (money, no price): the value "
     "before it is not known, so the return starts at the first complete day",
     "tabs": [[[MON, None, 1000, 0, 0], [MON + 1, 1010, 0, 0, 0]], []],
     "expect": {"points": [[MON, None, 1000, 0, 0], [MON + 1, 1010, 0, 0, 0]],
                "opening_paise": None}},
    {"id": "T3", "note": "no tab has a point", "tabs": [[], []],
     "expect": {"points": [], "opening_paise": None}},
]

OUT = {"note": "synthetic; built by make_risk.py, which states the arithmetic of every case",
       "series": SERIES, "falls": [DEPOSIT, WITHDRAW], "concentration": CONCENTRATION,
       "rows": ROWS, "overlap": OVERLAP, "emergency": EMERGENCY, "together": TOGETHER}  # fmt: skip


def build() -> dict:
    return {"risk.json": OUT}


def main() -> None:
    write_all(build(), Path(__file__).parent)


if __name__ == "__main__":
    main()
