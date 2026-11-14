"""Generator of business.json (T5): derivative pairing, expiry, turnover, income and flags.

Imports nothing from sudarshan. NIFTY lot 65. Rupees in the notes; the file holds paise,
quantities x 10^6 and prices x 10^4. Run: python3 make_business.py
"""

import json
from pathlib import Path

U = 10**6
P = 10**4
EXP = 20260827
CALL = ["NFO", "NIFTY", EXP, 245000000, "CE"]
PUT = ["NFO", "NIFTY", EXP, 240000000, "PE"]
FUT = ["NFO", "NIFTY", EXP, 0, "FUT"]
LAKH, CRORE = 100_000, 10_000_000
_SEQ = [0]


def t(day, side, qty, rupees, contract=CALL, account=1, **more):
    _SEQ[0] += 1
    row = {"taxpayer": 1, "account": account, "day": day, "seq": _SEQ[0], "side": side}
    row.update(segment="FO", instrument=contract, qty_u=qty * U, price_u=int(rupees * P))
    row.update(amount_paise=round(qty * rupees * 100))
    row.update(more)
    return row


def r(kind, opened, closed, qty, buy, sell, flag=None):
    """A closed piece: buy and sell values in paise; the profit is their difference."""
    return {"kind": kind, "open_day": opened, "close_day": closed, "qty_u": qty * U,
            "buy_paise": buy, "sell_paise": sell, "pnl_paise": sell - buy, "flag": flag}  # fmt: skip


def case(name, note, trades, realised, unknown=0, still_open=0, as_of=20260901, settlements=()):
    given = {"trades": trades, "as_of_day": as_of, "settlements": [list(s) for s in settlements]}
    out = {"realised": realised, "unknown_settlement": unknown, "open": still_open}
    return {"id": name, "note": note, "in": given, "out": out}


assert 65 * 130 - 65 * 100 == 1_950 and 65 * 40 - 65 * 80 == -2_600  # B1 T1, T2
assert 65 * 120 - 65 * 100 == 1_300 and 0 - 65 * 30 == -1_950  # B1 T3, T4
assert 1_950 - 2_600 + 1_300 - 1_950 == -1_300 and 1_950 + 2_600 + 1_300 + 1_950 == 7_800
assert 65 * 130 + 65 * 40 + 65 * 120 + 0 == 18_850 and 7_800 + 18_850 == 26_650
assert 65 * (24_100 - 24_000) == 6_500 and 65 * (24_200 - 24_250) == -3_250  # B2
B1 = [
    t(20260803, "BUY", 65, 100), t(20260804, "SELL", 65, 130),
    t(20260805, "BUY", 65, 80), t(20260806, "SELL", 65, 40),
    t(20260807, "SELL", 65, 120), t(20260810, "BUY", 65, 100),
    t(20260811, "BUY", 65, 30),
]  # fmt: skip
B1_OUT = [
    r("options", 20260803, 20260804, 65, 650_000, 845_000),
    r("options", 20260805, 20260806, 65, 520_000, 260_000),
    r("options", 20260807, 20260810, 65, 650_000, 780_000),
    r("options", 20260811, EXP, 65, 195_000, 0, "assumed_zero"),
]
B2 = [
    t(20260803, "BUY", 65, 24_000, FUT), t(20260804, "SELL", 65, 24_100, FUT),
    t(20260805, "SELL", 65, 24_200, FUT), t(20260806, "BUY", 65, 24_250, FUT),
]  # fmt: skip
B2_OUT = [
    r("futures", 20260803, 20260804, 65, 156_000_000, 156_650_000),
    r("futures", 20260805, 20260806, 65, 157_625_000, 157_300_000),
]
MARCH = ["NFO", "NIFTY", 20260428, 230000000, "PE"]

PAIRS = [
    case("B1", "T1 buy 65 at 100, sell at 130: +1,950. T2 buy at 80, sell at 40: -2,600. T3 "
         "sell at 120, buy back at 100: +1,300. T4 buy at 30 and held past expiry with no "
         "settlement price: taken as worthless, -1,950, flagged", B1, B1_OUT),
    case("B2", "futures: buy 65 at 24,000, sell 24,100: +6,500; sell 65 at 24,200, buy 24,250: "
         "-3,250", B2, B2_OUT),
    case("B3", "a short opened 30 Mar 2026 at 50 and bought back 2 Apr 2026 at 20: one piece of "
         "+1,950 that belongs to the year it was closed in, FY 2026-27",
         [t(20260330, "SELL", 65, 50, MARCH), t(20260402, "BUY", 65, 20, MARCH)],
         [r("options", 20260330, 20260402, 65, 130_000, 325_000)]),
    case("B4", "buy 130 at 100 (13,000), sell 65 at 110 and 65 the next day at 90: two pieces, "
         "each against half the cost 6,500: +650 and -650",
         [t(20260803, "BUY", 130, 100), t(20260804, "SELL", 65, 110), t(20260805, "SELL", 65, 90)],
         [r("options", 20260803, 20260804, 65, 650_000, 715_000),
          r("options", 20260803, 20260805, 65, 650_000, 585_000)]),
    case("B5", "a future bought and still open past expiry with no settlement price: its result "
         "is not known, so it is listed and left out, never closed at 0",
         [t(20260803, "BUY", 65, 24_000, FUT)], [], unknown=1),
    case("B5-settled", "the same future with the settlement price 24,150: closed on the expiry "
         "day, 65 x 150 = +9,750", [t(20260803, "BUY", 65, 24_000, FUT)],
         [r("futures", 20260803, EXP, 65, 156_000_000, 156_975_000)],
         settlements=[(FUT, 24_150 * P)]),
    case("B6-option-settled", "a call bought at 30 and settled at 12.50 on expiry: 65 x 12.50 = "
         "812.50 against 1,950: -1,137.50, not flagged", [t(20260811, "BUY", 65, 30)],
         [r("options", 20260811, EXP, 65, 195_000, 81_250)], settlements=[(CALL, 125_000)]),
    case("B7-short-expires", "a put sold at 40 and never bought back: taken as worthless, the "
         "whole premium 2,600 is profit, flagged", [t(20260811, "SELL", 65, 40, PUT)],
         [r("options", 20260811, EXP, 65, 0, 260_000, "assumed_zero")]),
    case("B8-flip", "long 65 at 100; sell 130 at 120 closes the 65 (+1,300) and opens a short "
         "of 65 at 120; buy 65 at 90 closes it (+1,950)",
         [t(20260803, "BUY", 65, 100), t(20260804, "SELL", 130, 120), t(20260805, "BUY", 65, 90)],
         [r("options", 20260803, 20260804, 65, 650_000, 780_000),
          r("options", 20260804, 20260805, 65, 585_000, 780_000)]),
    case("B9-not-expired", "bought and still open on the expiry day itself: not closed, not "
         "assumed worthless, in no total", [t(20260811, "BUY", 65, 30)], [], still_open=1,
         as_of=EXP),
    case("B10-first-in-first-out", "buy 65 at 100 then 65 at 110; sell 65 at 120: the first lot "
         "goes (+1,300), the second stays open before expiry",
         [t(20260803, "BUY", 65, 100), t(20260804, "BUY", 65, 110), t(20260805, "SELL", 65, 120)],
         [r("options", 20260803, 20260805, 65, 650_000, 780_000)], still_open=1, as_of=20260810),
    case("B11-per-account", "account 1 buys and account 2 sells the same contract: two open "
         "positions, nothing paired",
         [t(20260803, "BUY", 65, 100), t(20260804, "SELL", 65, 120, account=2)], [],
         still_open=2, as_of=20260810),
    case("B12-odd-paise", "3 units bought for 100.00 in all and sold one at a time for 40 each: "
         "costs 3,333, 3,334 and 3,333 paise, so the cost is used up exactly",
         [t(20260803, "BUY", 3, 33.3333, amount_paise=10_000), t(20260804, "SELL", 1, 40),
          t(20260805, "SELL", 1, 40), t(20260806, "SELL", 1, 40)],
         [r("options", 20260803, 20260804, 1, 3_333, 4_000),
          r("options", 20260803, 20260805, 1, 3_334, 4_000),
          r("options", 20260803, 20260806, 1, 3_333, 4_000)]),
    case("B13-shares-passed-over", "a share trade is not paired here",
         [dict(t(20260803, "BUY", 10, 100), segment="EQ", instrument="INE000A01010")], []),
]  # fmt: skip


def piece(kind, rupees_buy, rupees_sell, closed=20260804):
    return r(kind, 20260803, closed, 65, rupees_buy * 100, rupees_sell * 100)


def income(name, note, realised, non_spec, spec, turnover, flags, fy=2026, **more):
    """non_spec and spec are (gross, charges, net, turnover) in paise."""
    keys = ("gross_paise", "charges_paise", "net_paise", "turnover_paise")
    out = {"non_speculative": dict(zip(keys, non_spec, strict=True)), "speculative": dict(zip(keys, spec, strict=True)),
           "turnover_paise": turnover, "flags": flags}  # fmt: skip
    return {"id": name, "note": note, "in": {"fy": fy, "realised": realised, **more}, "out": out}


def big(profit, loss):
    """Two futures pieces: one that made ``profit`` rupees and one that lost ``loss``."""
    return [piece("futures", 0, profit), piece("futures", loss, 0)]


SPEC = [r("speculative", 20260803, 20260803, 50, 1_000_000, 1_050_000),
        r("speculative", 20260804, 20260804, 100, 500_000, 470_000)]  # fmt: skip
assert 500 + 300 == 800 and 500 - 300 == 200  # the two intraday days: +500 and -300
assert (6 * CRORE + 5 * CRORE, 6 * CRORE - 5 * CRORE) == (11 * CRORE, CRORE)
assert (53 - 47) * 10_000 == 600 * (53 + 47) and (50 - 47) * 10_000 < 600 * 97

INCOMES = [
    income("B1-income", "B1 in FY 2026-27: net -1,300; turnover 1,950 + 2,600 + 1,300 + 1,950 = "
           "7,800; charges 250.75 (all parts, STT too) make the net -1,550.75; a loss, so the "
           "low-margin sentence", B1_OUT, (-130_000, 25_075, -155_075, 780_000), (0, 0, 0, 0),
           780_000, ["low"], charges_paise=25_075),
    income("B1-plus-premium", "the older method adds the premium of options sold: 8,450 + 2,600 "
           "+ 7,800 + 0 = 18,850; turnover 7,800 + 18,850 = 26,650; the income is the same",
           B1_OUT, (-130_000, 0, -130_000, 2_665_000), (0, 0, 0, 0), 2_665_000, ["low"],
           method="abs_pnl_plus_sale_premium"),
    income("B2-income", "B2: net 6,500 - 3,250 = 3,250; turnover 9,750; 33% of turnover, no flag",
           B2_OUT, (325_000, 0, 325_000, 975_000), (0, 0, 0, 0), 975_000, []),
    income("B2-plus-premium", "futures have no premium: 9,750 under the older method too",
           B2_OUT, (325_000, 0, 325_000, 975_000), (0, 0, 0, 0), 975_000, [],
           method="abs_pnl_plus_sale_premium"),
    income("B3-earlier-year", "the short of 30 Mar closed 2 Apr 2026 is not in FY 2025-26",
           [r("options", 20260330, 20260402, 65, 130_000, 325_000)], (0, 0, 0, 0), (0, 0, 0, 0),
           0, [], fy=2025),
    income("B3-later-year", "it is in FY 2026-27: +1,950, turnover 1,950",
           [r("options", 20260330, 20260402, 65, 130_000, 325_000)],
           (195_000, 0, 195_000, 195_000), (0, 0, 0, 0), 195_000, []),
    income("S1-two-buckets", "intraday shares +500 and -300 (speculative: gross 200, turnover "
           "800, charges 40) beside B2 (charges 100): the buckets never mix; turnover 9,750 + "
           "800 = 10,550; net 3,150 + 160 = 3,310, well above 6%", B2_OUT + SPEC,
           (325_000, 10_000, 315_000, 975_000), (20_000, 4_000, 16_000, 80_000), 1_055_000, [],
           charges_paise=10_000, speculative_charges_paise=4_000),
    income("F1-above-10-crore", "+6 crore and -5 crore: turnover 11 crore, above 10 crore; net 1 "
           "crore is 9%, no low-margin sentence", big(6 * CRORE, 5 * CRORE),
           (CRORE * 100, 0, CRORE * 100, 11 * CRORE * 100), (0, 0, 0, 0), 11 * CRORE * 100,
           ["digital"]),
    income("F1-exactly-10-crore", "+5.5 crore and -4.5 crore: turnover exactly 10 crore is not "
           "above it: the between-the-limits sentence; net 1 crore is 10%",
           big(55 * LAKH * 10, 45 * LAKH * 10), (CRORE * 100, 0, CRORE * 100, 10 * CRORE * 100),
           (0, 0, 0, 0), 10 * CRORE * 100, ["base"]),
    income("F2-between", "+80 lakh and -70 lakh: turnover 1.5 crore, between the limits; net 10 "
           "lakh is 6.67%", big(80 * LAKH, 70 * LAKH),
           (10 * LAKH * 100, 0, 10 * LAKH * 100, 150 * LAKH * 100), (0, 0, 0, 0),
           150 * LAKH * 100, ["base"]),
    income("F3-low-margin", "+50 lakh and -47 lakh: turnover 97 lakh, under 1 crore; net 3 lakh "
           "is 3.09%, under 6%", big(50 * LAKH, 47 * LAKH),
           (3 * LAKH * 100, 0, 3 * LAKH * 100, 97 * LAKH * 100), (0, 0, 0, 0), 97 * LAKH * 100,
           ["low"]),
    income("F4-exactly-6-percent", "+53 lakh and -47 lakh: turnover exactly 1 crore (not above "
           "it) and net exactly 6% (not below it): no sentence", big(53 * LAKH, 47 * LAKH),
           (6 * LAKH * 100, 0, 6 * LAKH * 100, CRORE * 100), (0, 0, 0, 0), CRORE * 100, []),
    income("F5-charges-make-it-low", "the same with charges of 1,000: net falls under 6%",
           big(53 * LAKH, 47 * LAKH), (6 * LAKH * 100, 100_000, 6 * LAKH * 100 - 100_000,
                                       CRORE * 100), (0, 0, 0, 0), CRORE * 100, ["low"],
           charges_paise=100_000),
]  # fmt: skip

assert 100_001 * 3 // 4 == 75_000 and 100_001 - 75_000 - 25_000 == 1  # 75,000.75 and 25,000.25
OTHER = [
    {"id": "X1-share-charges", "note": "share charges of 1,000.01 split by traded value 3 : 1 "
     "between intraday and delivery: 75,000.75 and 25,000.25 paise; the spare paisa to the .75",
     "in": {"split": [100_001, 300_000, 100_000]}, "out": [75_001, 25_000]},
    {"id": "X2-differs", "note": "own NIFTY result -1,300 against Upstox's -1,300.50 is within a "
     "rupee; own BANKNIFTY 0 against Upstox's 250 is listed with both; a scrip only here is "
     "listed against nothing",
     "in": {"fy": 2026, "realised": B1_OUT + [dict(piece("futures", 100, 400), scrip="FINNIFTY")],
            "upstox": {"NIFTY": -130_050, "BANKNIFTY": 25_000}},
     "out": [{"scrip": "BANKNIFTY", "own_paise": None, "upstox_paise": 25_000},
             {"scrip": "FINNIFTY", "own_paise": 30_000, "upstox_paise": None}]},
]  # fmt: skip

FILES = {"business.json": {"pair": PAIRS, "income": INCOMES, "other": OTHER}}

if __name__ == "__main__":
    for name, data in FILES.items():
        (Path(__file__).parent / name).write_text(json.dumps(data, indent=1) + "\n")
