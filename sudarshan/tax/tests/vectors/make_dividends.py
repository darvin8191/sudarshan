"""Generator of dividends.json (T8): expected dividends, the confirmed ones and the TDS flag.

Imports nothing from sudarshan. Every expected value is typed by hand from the arithmetic in the
case's note. Units: quantity x 10^6, rupees a share x 10^4, money in paise, days YYYYMMDD; a
financial year is named by the year it starts in (2026 is FY 2026-27).
Run: python3 make_dividends.py
"""

import json
from pathlib import Path

U = 10**6
P = 10**4
RUPEE = 100
A, B = "INE000A01010", "INE000B01018"


def held(case, note, changes, ex_day, out):
    changes = [[day, shares * U] for day, shares in changes]
    return {"id": case, "note": note, "in": {"changes": changes, "ex_day": ex_day}, "out": out * U}


ENTITLED = [
    held("D1-QTY", "D1: 300 bought in June are held at the close of 13 Aug; ex-date 14 Aug: 300",
         [(20260610, 300)], 20260814, 300),
    held("D2", "D2: bought on the ex-date itself: nothing held the day before: 0",
         [(20260814, 300)], 20260814, 0),
    held("D-EVE", "bought on the day before the ex-date: held at its close: 300",
         [(20260813, 300)], 20260814, 300),
    held("D-SOLD-EVE", "300 held, 100 sold the day before the ex-date: 300 - 100 = 200",
         [(20260610, 300), (20260813, -100)], 20260814, 200),
    held("D-SOLD-EX", "300 held, all sold on the ex-date: the sale is cum-dividend no more: 300",
         [(20260610, 300), (20260814, -300)], 20260814, 300),
    held("D-ADD-EX", "300 held, 200 more bought on the ex-date: only the 300 count",
         [(20260610, 300), (20260814, 200)], 20260814, 300),
    held("D-NONE", "nothing ever held: 0", [], 20260814, 0),
    held("D-OVERSOLD", "a sale with no purchase on record never gives a negative holding: 0",
         [(20260610, -50)], 20260814, 0),
]  # fmt: skip

EXPECTED = [
    {"id": "D1", "note": "D1: Rs 5.50 a share on 300 shares = Rs 1,650",
     "in": {"qty_u": 300 * U, "amount_u": 55000}, "out": 1650 * RUPEE},
    {"id": "D1-SECOND", "note": "the second dividend of D1: Rs 30 a share on 300 = Rs 9,000",
     "in": {"qty_u": 300 * U, "amount_u": 30 * P}, "out": 9000 * RUPEE},
    {"id": "D-FRACTION", "note": "Rs 0.0125 a unit on 2.5 units = Rs 0.03125 = 3.125 paise: 3",
     "in": {"qty_u": 2_500_000, "amount_u": 125}, "out": 3},
    {"id": "D-HALF", "note": "Rs 0.05 a unit on 0.1 unit = 0.5 paisa: half away from zero, 1",
     "in": {"qty_u": 100_000, "amount_u": 500}, "out": 1},
]  # fmt: skip


def line(isin, fy, expected, state="expected", credited=None, tds=None):
    return {"isin": isin, "fy": fy, "state": state, "expected_paise": expected * RUPEE,
            "credited_paise": None if credited is None else credited * RUPEE,
            "tds_paise": None if tds is None else tds * RUPEE}  # fmt: skip


def payer(isin, expected, confirmed, total, likely, tds, unconfirmed, lines):
    return {"isin": isin, "expected_paise": expected, "confirmed_paise": confirmed,
            "total_paise": total, "tds_likely": likely, "tds_expected_paise": tds,
            "unconfirmed_count": unconfirmed, "lines": lines}  # fmt: skip


def year(case, note, fy, lines, payers, total, tds, unconfirmed, threshold):
    out = {"payers": payers, "total_paise": total, "tds_expected_paise": tds,
           "unconfirmed_count": unconfirmed, "tds_threshold_paise": threshold}  # fmt: skip
    return {"id": case, "note": note, "in": {"fy": fy, "lines": lines}, "out": out}


YEAR = [
    year("D1-TDS", "D1: one payer, 1,650 + 9,000 = 10,650, above 10,000: TDS likely, 10% = 1,065",
         2026, [line(A, 2026, 1650), line(A, 2026, 9000)],
         [payer(A, 1065000, 0, 1065000, True, 106500, 2, 2)], 1065000, 106500, 2, 1000000),
    year("D-PER-PAYER", "two payers of 6,000 each: 12,000 in the year, but neither payer is "
         "above 10,000, so no TDS is flagged: the test is per payer",
         2026, [line(A, 2026, 6000), line(B, 2026, 6000)],
         [payer(A, 600000, 0, 600000, False, None, 1, 1),
          payer(B, 600000, 0, 600000, False, None, 1, 1)], 1200000, 0, 2, 1000000),
    year("D-AT-LIMIT", "exactly 10,000 from one payer is not above 10,000: no flag",
         2026, [line(A, 2026, 10000)],
         [payer(A, 1000000, 0, 1000000, False, None, 1, 1)], 1000000, 0, 1, 1000000),
    year("D-OLD-LIMIT", "FY 2024-25: the limit was 5,000; 6,000 from one payer is above it: "
         "10% of 6,000 = 600",
         2024, [line(A, 2024, 6000)],
         [payer(A, 600000, 0, 600000, True, 60000, 1, 1)], 600000, 60000, 1, 500000),
    year("D-CONFIRMED", "expected 9,000; confirmed with 8,640 credited and 960 deducted: the "
         "dividend is 8,640 + 960 = 9,600, which replaces the expected figure. A second line "
         "of 1,650 marked not received counts as nothing. Total 9,600, not above 10,000",
         2026, [line(A, 2026, 9000, "confirmed", 8640, 960), line(A, 2026, 1650, "not_received")],
         [payer(A, 1065000, 960000, 960000, False, None, 0, 2)], 960000, 0, 0, 1000000),
    year("D-OTHER-YEAR", "a line of another year is passed over: only the 2,000 of FY 2026-27",
         2026, [line(A, 2025, 50000), line(A, 2026, 2000)],
         [payer(A, 200000, 0, 200000, False, None, 1, 1)], 200000, 0, 1, 1000000),
    year("D-EMPTY", "no dividend lines: nothing, and no flag", 2026, [], [], 0, 0, 0, 1000000),
]  # fmt: skip

FILES = {"dividends.json": {"entitled": ENTITLED, "expected": EXPECTED, "year": YEAR}}

if __name__ == "__main__":
    for name, data in FILES.items():
        (Path(__file__).parent / name).write_text(json.dumps(data, indent=1) + "\n")
