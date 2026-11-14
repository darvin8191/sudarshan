"""Generator of index_stats.json (P7 lane R, M7.31): day returns, beta, correlation, excess.

Imports nothing from sudarshan. The statistics repeat the 5-day toy series of
sudarshan/common/tests/vectors/make_stats.py:

    index +1, -2, +3, -1, +4 %  (mean 1, variance 26/5)    tab +2, -3, +5, -2, +3 %  (variance 46/5)
    covariance 32/5, so beta = 32/26 = 16/13 = 1.2308 and
    correlation = 6.4 / sqrt(5.2 x 9.2) = 6.4 / 6.91665 = 0.9253

A series that repeats those five days any whole number of times has the same mean, variance and
covariance (each is an average, and every block adds the same sums), so 20 days (4 blocks) and 60
days (12 blocks) give the same two figures. Index levels are whole numbers only because the first
is 10^130: each day multiplies it by (100 + r) / 100. No index is that high; the arithmetic is
what is pinned. November 2026: the 2nd is a Monday.
Run: python3 make_index_stats.py
"""

import json
from datetime import date, timedelta
from fractions import Fraction as F
from math import isqrt
from pathlib import Path

R_M, R_P = [1, -2, 3, -1, 4], [2, -3, 5, -2, 3]
assert F(32, 5) / F(26, 5) == F(16, 13) and round(16 / 13 * 10**4) == 12308
ROOT = F(isqrt(F(26 * 46, 25).numerator * 10**40 // 25), 10**20)  # sqrt(5.2 x 9.2)
assert round(F(32, 5) / ROOT * 10**4) == 9253
BETA, CORR = "1.2308", "0.9253"
START = 10**130


def key(d):
    return d.year * 10000 + d.month * 100 + d.day


def weekdays(first, count):
    """``count`` weekdays from ``first`` on, as days."""
    out, d = [], first
    while len(out) < count:
        if d.weekday() < 5:
            out.append(key(d))
        d += timedelta(days=1)
    return out


def said(day):
    months = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return f"{day % 100} {months[day // 100 % 100 - 1]} {day // 10000}"


def market(days, r_p, r_m):
    """The tab's returns as [start, day, grown, base] and the index's closes as [day, level]."""
    assert len(days) == len(r_p) + 1 == len(r_m) + 1
    level, closes, moves = START, [[days[0], START]], []
    for before, day, p, m in zip(days[:-1], days[1:], r_p, r_m, strict=True):
        assert level * (100 + m) % 100 == 0
        level = level * (100 + m) // 100
        closes.append([day, level])
        moves.append([before, day, 100 + p, 100])
    return moves, closes


def case(name, note, moves, closes, as_of, out, twr=None, index_twr=None, holidays=()):
    return {"id": name, "note": note, "benchmark": "nifty50", "returns": moves, "closes": closes,
            "as_of_day": as_of, "twr_pct": twr, "index_twr_pct": index_twr,
            "holidays": list(holidays), "expect": out}  # fmt: skip


def out(beta, corr, excess, days, note):
    return {"beta": beta, "correlation": corr, "excess_pct": excess, "stat_days": days,
            "stat_note": note}  # fmt: skip


OCT5 = date(2026, 10, 5)  # a Monday
D21 = weekdays(OCT5, 21)
assert D21[0] == 20261005 and D21[-1] == 20261102 and D21[-2] == 20261030
M20, C20 = market(D21, R_P * 4, R_M * 4)
M19, C19 = market(D21[:20], (R_P * 4)[:19], (R_M * 4)[:19])
D66 = weekdays(date(2026, 7, 6), 66)
M65, C65 = market(D66, [-50, 90, -50, 90, -50] + R_P * 12, [50, -40, 50, -40, 50] + R_M * 12)
# X4: 23 days; the index has no close on the 11th. The tab's return of that day has no index
# close, and the next day's index move would be of two days: both are left out, 20 pairs remain.
D23 = weekdays(OCT5, 23)
M22, C22 = market(D23, (R_P * 2) + [7, -7] + (R_P * 2), (R_M * 2) + [9, 9] + (R_M * 2))
C22_GAP = [row for row in C22 if row[0] != D23[11]]
assert len(C22_GAP) == 22 and M22[10][1] == D23[11] and M22[11][0] == D23[11]
FLAT_M, FLAT_C = market(D21, R_P * 4, [0] * 20)
STILL_M, STILL_C = market(D21, [0] * 20, R_M * 4)
# 12.5000 - 10.2500 = 2.2500; 3.1000 - 3.1049 = -0.0049; 5.0000 - 5.0000 = 0.0000 (no minus)
assert 125000 - 102500 == 22500 and 31000 - 31049 == -49

STATS = [
    case("X1", "20 days, the toy series four times: beta 16/13 = 1.2308, correlation 0.9253; the "
         "tab's TWR 12.50% less the index's 10.25% = +2.25%",
         M20, C20, D21[-1], out(BETA, CORR, "2.2500", 20, f"On 20 trading days to {said(D21[-1])}"),
         "12.5000", "10.2500"),
    case("X2", "19 days: nothing shown; one more trading day after Fri 30 Oct is Mon 2 Nov. The "
         "excess return needs no days: 3.1000 - 3.1049 = -0.0049",
         M19, C19, D21[-2],
         out(None, None, "-0.0049", 19, "Shown after 20 trading days (from 2 Nov 2026)"),
         "3.1000", "3.1049"),
    case("X2h", "the same with Mon 2 Nov a holiday: from Tue 3 Nov", M19, C19, D21[-2],
         out(None, None, None, 19, "Shown after 20 trading days (from 3 Nov 2026)"),
         holidays=[20261102]),
    case("X3", "65 days: only the last 60 count (the toy series twelve times), so five wild days "
         "before them change nothing", M65, C65, D66[-1],
         out(BETA, CORR, "0.0000", 60, f"On 60 trading days to {said(D66[-1])}"),
         "5.0000", "5.0000"),
    case("X4", "the index has no close on one day: that day and the day after it are left out "
         "(the second would be a two-day move of the index); the 20 days left are the toy series",
         M22, C22_GAP, D23[-1], out(BETA, CORR, None, 20, f"On 20 trading days to {said(D23[-1])}")),
    case("X5", "the index closed level every day: no beta and no correlation, never 0",
         FLAT_M, FLAT_C, D21[-1],
         out(None, None, None, 20, "NIFTY 50 did not move on these days, so there is no beta yet.")),
    case("X6", "the tab did not move: beta 0 stands, no correlation", STILL_M, STILL_C, D21[-1],
         out("0.0000", None, None, 20,
             "This tab's value did not move on these days, so there is no correlation yet.")),
    case("X7", "no index closes at all: no days; 20 trading days after Mon 2 Nov is Mon 30 Nov",
         M20, [], D21[-1], out(None, None, None, 0, "Shown after 20 trading days (from 30 Nov 2026)")),
    case("X8", "X1 with a close of 0 (a gap in the data, ignored) and a wrong close for one day "
         "listed before the right one (the later wins)",
         M20, [[D21[3], 12345], *C20, [D21[5], 0]], D21[-1],
         out(BETA, CORR, None, 20, f"On 20 trading days to {said(D21[-1])}")),
]
assert key(date(2026, 11, 2) + timedelta(days=28)) == 20261130  # 20 weekdays on: four weeks

# ---- day returns from periods: [start, day, base, grown] as performance.Period ----------------
MON, TUE, WED, THU, FRI, SAT, SUN, MON2 = (20261102 + n for n in range(8))


def moves(*rows):
    return [list(row) for row in rows]


RETURNS = [
    {"id": "DR1", "note": "a week of complete days and the Monday after: five returns, Friday "
     "to Monday being one trading day", "holidays": [],
     "periods": moves((MON, TUE, 100, 101), (TUE, WED, 101, 99), (WED, THU, 99, 99),
                      (THU, FRI, 99, 110), (FRI, MON2, 110, 121)),
     "expect": moves((MON, TUE, 101, 100), (TUE, WED, 99, 101), (WED, THU, 99, 99),
                     (THU, FRI, 110, 99), (FRI, MON2, 121, 110))},
    {"id": "DR2", "note": "Wednesday had no complete value: Tuesday to Thursday is two days' "
     "move and is left out; the days on either side stand", "holidays": [],
     "periods": moves((MON, TUE, 100, 101), (TUE, THU, 101, 99), (THU, FRI, 99, 110)),
     "expect": moves((MON, TUE, 101, 100), (THU, FRI, 110, 99))},
    # 100 -> 100 (Sat) -> 100 (Sun) -> 103 (Mon): 100/100 x 100/100 x 103/100
    {"id": "DR3", "note": "a fund valued on Saturday and Sunday: one return, Friday to Monday, "
     "the product of the three", "holidays": [],
     "periods": moves((FRI, SAT, 100, 100), (SAT, SUN, 100, 100), (SUN, MON2, 100, 103)),
     "expect": moves((FRI, MON2, 100 * 100 * 103, 100 * 100 * 100))},
    {"id": "DR4", "note": "Thursday a named holiday: Wednesday to Friday is one trading day",
     "holidays": [THU], "periods": moves((WED, FRI, 100, 104)),
     "expect": moves((WED, FRI, 104, 100))},
    {"id": "DR4b", "note": "the same without the holiday named: two days, left out",
     "holidays": [], "periods": moves((WED, FRI, 100, 104)), "expect": []},
    # 200 -> 202 on the holiday -> 198: 202/200 x 198/202
    {"id": "DR5", "note": "a value on the holiday itself joins Friday's return",
     "holidays": [THU], "periods": moves((WED, THU, 200, 202), (THU, FRI, 202, 198)),
     "expect": moves((WED, FRI, 202 * 198, 200 * 202))},
    {"id": "DR6", "note": "the day the series begins on (bought and valued on Monday) is no "
     "day's move: nothing for it, then Tuesday's", "holidays": [],
     "periods": moves((MON, MON, 100, 98), (MON, TUE, 98, 99)),
     "expect": moves((MON, TUE, 99, 98))},
    {"id": "DR7", "note": "nothing was held from Tuesday to Wednesday (no period): the returns "
     "on either side are each one day's", "holidays": [],
     "periods": moves((MON, TUE, 100, 105), (WED, THU, 50, 51)),
     "expect": moves((MON, TUE, 105, 100), (WED, THU, 51, 50))},
    {"id": "DR8", "note": "a return that only reaches Saturday is not finished: nothing yet",
     "holidays": [], "periods": moves((FRI, SAT, 100, 100)), "expect": []},
    {"id": "DR9", "note": "held to Saturday, nothing at work until money came on Sunday (no "
     "period between): Monday's return is of Sunday's money only, not joined to Friday's",
     "holidays": [], "periods": moves((FRI, SAT, 100, 90), (SUN, MON2, 50, 51)),
     "expect": moves((SUN, MON2, 51, 50))},
]

OUT = {"note": "synthetic; built by make_index_stats.py, which states the arithmetic",
       "returns": RETURNS, "stats": STATS}  # fmt: skip
Path(__file__).with_name("index_stats.json").write_text(json.dumps(OUT, indent=1) + "\n")
