"""Generator of the PA1 golden vectors: ledger.json and money.json, written next to this file.

It imports nothing from sudarshan, so the vectors cannot be a copy of what the code does. Every
rounding is computed twice (exact fractions, and Decimal with ROUND_HALF_UP) and the two must
agree. Each ledger case carries the numbers worked out by hand, with the working written in its
``why``; the tests hold the engine and a naive second implementation (tests/portfolio_oracle.py)
to them, and test_portfolio_vectors.py holds the JSON to this file, so a number is changed here
or not at all.

Run: python3 make_pa1.py
"""

import json
import re
from decimal import ROUND_HALF_UP, Decimal, getcontext
from fractions import Fraction
from pathlib import Path

getcontext().prec = 80
RUPEE = chr(0x20B9)
DOT = " " + chr(0xB7) + " "
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


# ---------------------------------------------------------------- exact rounding, two ways
def half_away_fraction(x: Fraction) -> int:
    s = -1 if x < 0 else 1
    return s * int((abs(x) + Fraction(1, 2)) // 1)


def half_away_decimal(n: int, d: int) -> int:
    q = (Decimal(n) / Decimal(d)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return int(q)


def rd(n: int, d: int) -> int:
    a, b = half_away_fraction(Fraction(n, d)), half_away_decimal(n, d)
    assert a == b, (n, d, a, b)
    return a


# ---------------------------------------------------------------- independent words
def group_in(n: int) -> str:
    s = str(abs(n))
    if len(s) > 3:
        head = re.sub(r"(\d)(?=(\d\d)+$)", r"\1,", s[:-3])
        s = head + "," + s[-3:]
    return ("-" if n < 0 else "") + s


def inr(paise: int) -> str:
    r, p = divmod(abs(paise), 100)
    return ("-" if paise < 0 else "") + RUPEE + group_in(r) + "." + f"{p:02d}"


def price_text(price_u: int) -> str:
    r, p = divmod(price_u, 10000)
    frac = f"{p:04d}".rstrip("0")
    frac = frac + "0" * (2 - len(frac)) if len(frac) < 2 else frac
    return RUPEE + group_in(r) + "." + frac


def qty_text(qty_u: int) -> str:
    r, p = divmod(qty_u, 10**6)
    frac = f"{p:06d}".rstrip("0")
    return group_in(r) + ("." + frac if frac else "")


def day_text(day: int) -> str:
    return f"{day % 100} {MONTHS[day // 100 % 100 - 1]} {day // 10000}"


def e(i, kind, day, account, asset, qty_u=None, amount=None, **more):
    row = {"id": i, "seq": i, "kind": kind, "trade_date": day, "account_id": account}
    if asset is not None:
        row["asset_id"] = asset
    if qty_u is not None:
        row["qty_u"] = qty_u
    if amount is not None:
        row["amount_paise"] = amount
    row.update(more)
    return row


U = 10**6  # one unit

NAMES = {"accounts": {"1": "Zerodha", "2": "Account B"}, "assets": {"1": "RELIANCE"}, "units": {}}
CASES = []


def case(cid, title, why, entries, expect, names=None, as_of=None, words=None):
    if words and "refused" not in expect:
        expect = {**expect, "words": {str(k): v for k, v in words.items()}}
    c = {"id": cid, "title": title, "why": why, "entries": entries, "expect": expect}
    if names:
        c["names"] = names
    if as_of:
        c["as_of"] = as_of
    CASES.append(c)


def mv(entry, qty_u, cost, gain, paise, used=()):
    return {
        "entry": entry,
        "qty_u": qty_u,
        "cost_paise": cost,
        "gain_paise": gain,
        "paise": paise,
        "used": [list(u) for u in used],
    }


def pos(qty_u, cost, lots, realised=0, income=0, fees=0):
    return {
        "qty_u": qty_u,
        "cost_paise": cost,
        "realised_paise": realised,
        "income_paise": income,
        "fees_paise": fees,
        "lots": [list(lot) for lot in lots],
    }


def totals(realised=0, income=0, fees=0):
    return {"realised_paise": realised, "income_paise": income, "fees_paise": fees}


# F1 ---------------------------------------------------------------------------------------
case(
    "F1",
    "Two purchases, then a sale across both lots",
    "Buy 10 at 100 on 10 Jan 2025 costs 1,000.00 (100000 paise); buy 10 at 120 on 10 Feb costs "
    "1,200.00 (120000). Sell 15 at 130 on 10 Mar for 1,950.00 (195000). FIFO takes the whole "
    "10 Jan lot (10 units, 100000) and 5 of the 10 Feb lot: 120000 x 5/10 = 60000. Cost "
    "100000 + 60000 = 160000; proceeds 195000; gain 35000 (350.00). Left: 5 at 120 = 60000.",
    [
        e(1, "BUY", 20250110, 1, 1, 10 * U, 100000),
        e(2, "BUY", 20250210, 1, 1, 10 * U, 120000),
        e(3, "SELL", 20250310, 1, 1, 15 * U, 195000),
    ],
    {
        "positions": {"1/1": pos(5 * U, 60000, [(20250210, 5 * U, 60000)], realised=35000)},
        "moves": [
            mv(1, 10 * U, 100000, 0, 100000),
            mv(2, 10 * U, 120000, 0, 120000),
            mv(
                3,
                -15 * U,
                -160000,
                35000,
                195000,
                [(20250110, 10 * U, 100000), (20250210, 5 * U, 60000)],
            ),
        ],
        "totals": totals(realised=35000),
    },
    names=NAMES,
    words={
        1: f"Cost {inr(100000)}",
        2: f"Cost {inr(120000)}",
        3: f"From {day_text(20250110)}: {qty_text(10 * U)} at {price_text(1_000_000)}"
        f"; from {day_text(20250210)}: {qty_text(5 * U)} at {price_text(1_200_000)}"
        f"{DOT}gain {inr(35000)}",
    },
)

# F2 ---------------------------------------------------------------------------------------
case(
    "F2",
    "Charges are part of the cost and come off the proceeds",
    "Buy 10 at 100 (100000 paise) with 20.00 charges (2000): the lot costs 102000. Sell 10 at "
    "110 (110000) with 15.00 charges (1500): net proceeds 108500. The lot goes whole at 102000. "
    "Gain 108500 - 102000 = 6500 (65.00).",
    [
        e(1, "BUY", 20250106, 1, 1, 10 * U, 100000, charges_paise=2000),
        e(2, "SELL", 20250210, 1, 1, 10 * U, 110000, charges_paise=1500),
    ],
    {
        "positions": {"1/1": pos(0, 0, [], realised=6500)},
        "moves": [
            mv(1, 10 * U, 102000, 0, 100000),
            mv(2, -10 * U, -102000, 6500, 110000, [(20250106, 10 * U, 102000)]),
        ],
        "totals": totals(realised=6500),
    },
    names=NAMES,
    words={
        1: f"Cost {inr(102000)} with {inr(2000)} charges",
        2: f"From {day_text(20250106)}: {qty_text(10 * U)} at {price_text(1_020_000)}"
        f"{DOT}gain {inr(6500)}",
    },
)

# F3 ---------------------------------------------------------------------------------------
# 3 units at 33.3333: price_u 333333; 3,000,000 x 333,333 / 10^8 = 9999.99 paise -> 10000
assert rd(3 * U * 333_333, 10**8) == 10000
case(
    "F3",
    "A lot's cost is used up exactly: 10,000 paise over three sales of one",
    "Buy 3 at 33.3333 with no amount typed: 3 x 33.3333 = 99.9999 rupees = 9999.99 paise, which "
    "rounds to 10000. Sale 1 of 1 unit of 3 takes 10000 x 1/3 = 3333.33 -> 3333, leaving 2 units "
    "and 6667. Sale 2 takes 6667 x 1/2 = 3333.5 -> 3334 (half away from zero), leaving 1 unit and "
    "3333. Sale 3 takes the last unit's 3333. Costs 3333 + 3334 + 3333 = 10000. At 40.00 a "
    "sale the gains are 667, 666, 667 = 2000 = 12000 - 10000. Allocating from the original lot "
    "instead would give 3333, 3333, 3334.",
    [
        e(1, "BUY", 20250106, 1, 1, 3 * U, None, price_u=333_333),
        e(2, "SELL", 20250210, 1, 1, 1 * U, 4000),
        e(3, "SELL", 20250211, 1, 1, 1 * U, 4000),
        e(4, "SELL", 20250212, 1, 1, 1 * U, 4000),
    ],
    {
        "positions": {"1/1": pos(0, 0, [], realised=2000)},
        "moves": [
            mv(1, 3 * U, 10000, 0, 10000),
            mv(2, -U, -3333, 667, 4000, [(20250106, U, 3333)]),
            mv(3, -U, -3334, 666, 4000, [(20250106, U, 3334)]),
            mv(4, -U, -3333, 667, 4000, [(20250106, U, 3333)]),
        ],
        "totals": totals(realised=2000),
    },
    names=NAMES,
    words={
        1: f"Cost {inr(10000)}",
        2: f"From {day_text(20250106)}: {qty_text(U)} at {price_text(333_300)}{DOT}gain {inr(667)}",
        3: f"From {day_text(20250106)}: {qty_text(U)} at {price_text(333_400)}{DOT}gain {inr(666)}",
    },
)

case(
    "F3b",
    "A typed amount beats quantity x price",
    "Buy 3 with a price of 40.0000 each (which would make 12000 paise) and an amount of 100.00 "
    "(10000): the amount is authoritative, so the lot costs 10000.",
    [e(1, "BUY", 20250106, 1, 1, 3 * U, 10000, price_u=400_000)],
    {
        "positions": {"1/1": pos(3 * U, 10000, [(20250106, 3 * U, 10000)])},
        "moves": [mv(1, 3 * U, 10000, 0, 10000)],
        "totals": totals(),
    },
    names=NAMES,
)

# F4 ---------------------------------------------------------------------------------------
MSG4 = "Zerodha holds 10 RELIANCE on 12 Mar 2025, so a sale of 12 cannot be saved. Add the missing purchase first."
case(
    "F4",
    "Selling more than the account holds is refused, in words",
    "Buy 10 on 10 Jan 2025; on 12 Mar 2025 the account holds 10 and a sale of 12 is 2 short. The "
    "sale is entry 2 and is the one named.",
    [e(1, "BUY", 20250110, 1, 1, 10 * U, 100000), e(2, "SELL", 20250312, 1, 1, 12 * U, 150000)],
    {"refused": {"entry": 2, "reason": "short", "message": MSG4}},
    names=NAMES,
)
case(
    "F4b",
    "A sale with nothing bought says so",
    "Nothing is held on 12 Mar 2025, so a sale of 5 is refused and the sentence says 'no'.",
    [e(1, "SELL", 20250312, 1, 1, 5 * U, 70000)],
    {
        "refused": {
            "entry": 1,
            "reason": "short",
            "message": "Zerodha holds no RELIANCE on 12 Mar 2025, so a sale of 5 cannot be saved. Add the missing purchase first.",
        }
    },
    names=NAMES,
)
case(
    "F4c",
    "The second sale of a day sees what the first left",
    "Buy 10; sell 6 on 12 Mar 2025 leaves 4; a second sale of 6 the same day is 2 short.",
    [
        e(1, "BUY", 20250110, 1, 1, 10 * U, 100000),
        e(2, "SELL", 20250312, 1, 1, 6 * U, 90000),
        e(3, "SELL", 20250312, 1, 1, 6 * U, 90000),
    ],
    {
        "refused": {
            "entry": 3,
            "reason": "short",
            "message": "Zerodha holds 4 RELIANCE on 12 Mar 2025, so a sale of 6 cannot be saved. Add the missing purchase first.",
        }
    },
    names=NAMES,
)

# F5 ---------------------------------------------------------------------------------------
case(
    "F5",
    "FIFO is per account",
    "Account A buys 10 at 100 (100000). Account B buys 10 at 200 (200000) and sells 10 at 150 "
    "(150000). B's sale uses B's lot only: gain 150000 - 200000 = -50000 (-500.00). A's lot is "
    "untouched. Taking A's cheaper lot (per asset instead of per account) would show +500.00.",
    [
        e(1, "BUY", 20250110, 1, 1, 10 * U, 100000),
        e(2, "BUY", 20250115, 2, 1, 10 * U, 200000),
        e(3, "SELL", 20250310, 2, 1, 10 * U, 150000),
    ],
    {
        "positions": {
            "1/1": pos(10 * U, 100000, [(20250110, 10 * U, 100000)]),
            "2/1": pos(0, 0, [], realised=-50000),
        },
        "moves": [
            mv(1, 10 * U, 100000, 0, 100000),
            mv(2, 10 * U, 200000, 0, 200000),
            mv(3, -10 * U, -200000, -50000, 150000, [(20250115, 10 * U, 200000)]),
        ],
        "totals": totals(realised=-50000),
    },
    names=NAMES,
    words={
        3: f"From {day_text(20250115)}: {qty_text(10 * U)} at {price_text(2_000_000)}{DOT}loss {inr(50000)}"
    },
)

# F6 ---------------------------------------------------------------------------------------
case(
    "F6",
    "On one day the purchase comes first, whatever was typed first",
    "Sell 5 for 550.00 (55000) typed before buy 10 for 1,000.00 (100000), both on 10 Jan 2025, "
    "nothing held before. The day applies purchases before sales: the lot costs 100000, the sale "
    "takes 100000 x 5/10 = 50000, gain 55000 - 50000 = 5000. Left: 5 units, cost 50000.",
    [e(1, "SELL", 20250110, 1, 1, 5 * U, 55000), e(2, "BUY", 20250110, 1, 1, 10 * U, 100000)],
    {
        "positions": {"1/1": pos(5 * U, 50000, [(20250110, 5 * U, 50000)], realised=5000)},
        "moves": [
            mv(2, 10 * U, 100000, 0, 100000),
            mv(1, -5 * U, -50000, 5000, 55000, [(20250110, 5 * U, 50000)]),
        ],
        "totals": totals(realised=5000),
    },
    names=NAMES,
)
case(
    "F6b",
    "A sale the day before the purchase is still refused",
    "The same two entries, but the sale is dated 9 Jan 2025, the day before the purchase: "
    "nothing is held on 9 Jan.",
    [e(1, "SELL", 20250109, 1, 1, 5 * U, 55000), e(2, "BUY", 20250110, 1, 1, 10 * U, 100000)],
    {
        "refused": {
            "entry": 1,
            "reason": "short",
            "message": "Zerodha holds no RELIANCE on 9 Jan 2025, so a sale of 5 cannot be saved. Add the missing purchase first.",
        }
    },
    names=NAMES,
)

# L1 ---------------------------------------------------------------------------------------
case(
    "L1",
    "A half paisa of cost goes to the sale (half away from zero)",
    "Buy 2 units for 100.05 (10005 paise). Sale 1 of 1 unit takes 10005 x 1/2 = 5002.5 -> 5003 "
    "(half away from zero; half to even would give 5002), leaving 1 unit and 5002. Sale 2 takes "
    "the last unit's 5002. Gains at 60.00 a sale: 6000 - 5003 = 997 and 6000 - 5002 = 998; "
    "997 + 998 = 1995 = 12000 - 10005.",
    [
        e(1, "BUY", 20250106, 1, 1, 2 * U, 10005),
        e(2, "SELL", 20250210, 1, 1, U, 6000),
        e(3, "SELL", 20250211, 1, 1, U, 6000),
    ],
    {
        "positions": {"1/1": pos(0, 0, [], realised=1995)},
        "moves": [
            mv(1, 2 * U, 10005, 0, 10005),
            mv(2, -U, -5003, 997, 6000, [(20250106, U, 5003)]),
            mv(3, -U, -5002, 998, 6000, [(20250106, U, 5002)]),
        ],
        "totals": totals(realised=1995),
    },
    names=NAMES,
)

# L2 ---------------------------------------------------------------------------------------
assert rd(109_459_000 * 456_789, 10**8) == 499_997
alloc = rd(499_997 * 50_250_000, 109_459_000)
assert alloc == 229_537
unit_u = rd(alloc * 10**8, 50_250_000)
case(
    "L2",
    "Fund units with four decimals, bought at a NAV",
    "109.459 units at NAV 45.6789: 109.459 x 45.6789 = 4999.96671 rupees = 499996.67 paise, "
    "which rounds to 499997. Selling 50.25 units takes 499997 x 50.25 / 109.459 = 229536.62 -> "
    "229537 of the cost; the amount received is 2,350.00 (235000), gain 235000 - 229537 = 5463. "
    "Left: 59.209 units costing 499997 - 229537 = 270460.",
    [
        e(1, "BUY", 20250106, 1, 1, 109_459_000, None, price_u=456_789),
        e(2, "SELL", 20250310, 1, 1, 50_250_000, 235000),
    ],
    {
        "positions": {
            "1/1": pos(59_209_000, 270460, [(20250106, 59_209_000, 270460)], realised=5463)
        },
        "moves": [
            mv(1, 109_459_000, 499997, 0, 499997),
            mv(2, -50_250_000, -229537, 5463, 235000, [(20250106, 50_250_000, 229537)]),
        ],
        "totals": totals(realised=5463),
    },
    names={"accounts": {"1": "Folio"}, "assets": {"1": "Example Fund"}, "units": {"1": "units"}},
    words={
        1: f"Cost {inr(499997)}",
        2: f"From {day_text(20250106)}: {qty_text(50_250_000)} at {price_text(unit_u)}{DOT}gain {inr(5463)}",
    },
)

# L3 ---------------------------------------------------------------------------------------
case(
    "L3",
    "A purchase and its sale on the same day",
    "Buy 10 for 1,000.00 and sell 10 for 1,050.00 on 6 Jan 2025: the sale takes the lot made "
    "that morning, 100000, gain 105000 - 100000 = 5000.",
    [e(1, "BUY", 20250106, 1, 1, 10 * U, 100000), e(2, "SELL", 20250106, 1, 1, 10 * U, 105000)],
    {
        "positions": {"1/1": pos(0, 0, [], realised=5000)},
        "moves": [
            mv(1, 10 * U, 100000, 0, 100000),
            mv(2, -10 * U, -100000, 5000, 105000, [(20250106, 10 * U, 100000)]),
        ],
        "totals": totals(realised=5000),
    },
    names=NAMES,
)

# L4 ---------------------------------------------------------------------------------------
L4 = [
    e(1, "BUY", 20250106, 1, 1, 10 * U, 100000),
    e(2, "SELL", 20250210, 1, 1, 4 * U, 44000),
    dict(e(3, "SELL", 20250211, 1, 1, 100 * U, 1000000), status="draft"),
    e(4, "BUY", 20250301, 1, 1, 5 * U, 60000),
]
case(
    "L4",
    "A draft counts nowhere",
    "Entry 3 is a draft sale of 100 units that would be refused if it counted; it is ignored. "
    "Buy 10 (100000); sell 4 takes 100000 x 4/10 = 40000, gain 44000 - 40000 = 4000; buy 5 for "
    "600.00. Left: 6 units costing 60000 (10 Jan) and 5 costing 60000 (1 Mar): 11 units, 120000.",
    L4,
    {
        "positions": {
            "1/1": pos(
                11 * U, 120000, [(20250106, 6 * U, 60000), (20250301, 5 * U, 60000)], realised=4000
            )
        },
        "moves": [
            mv(1, 10 * U, 100000, 0, 100000),
            mv(2, -4 * U, -40000, 4000, 44000, [(20250106, 4 * U, 40000)]),
            mv(4, 5 * U, 60000, 0, 60000),
        ],
        "totals": totals(realised=4000),
    },
    names=NAMES,
)
case(
    "L4b",
    "As of a day: the replay stops there",
    "The same entries replayed as of 15 Feb 2025: the 1 Mar purchase is not there yet. Left: "
    "6 units costing 60000.",
    L4,
    {
        "positions": {"1/1": pos(6 * U, 60000, [(20250106, 6 * U, 60000)], realised=4000)},
        "moves": [
            mv(1, 10 * U, 100000, 0, 100000),
            mv(2, -4 * U, -40000, 4000, 44000, [(20250106, 4 * U, 40000)]),
        ],
        "totals": totals(realised=4000),
    },
    names=NAMES,
    as_of=20250215,
)

# L5 ---------------------------------------------------------------------------------------
case(
    "L5",
    "Income and fees touch no lot",
    "Buy 10 (100000). A dividend of 10.00 gross with 1.00 TDS counts 1000 as income (gross). "
    "Interest 5.00 (500) with no asset, a fee of 3.00 (300) on the holding and a fee of 1.20 "
    "(120) with no asset. Income 1000 + 500 = 1500; fees 300 + 120 = 420.",
    [
        e(1, "BUY", 20250106, 1, 1, 10 * U, 100000),
        e(2, "DIVIDEND", 20250601, 1, 1, None, 1000, tds_paise=100),
        e(3, "INTEREST", 20250701, 1, None, None, 500),
        e(4, "FEE", 20250702, 1, 1, None, 300),
        e(5, "FEE", 20250703, 1, None, None, 120),
    ],
    {
        "positions": {
            "1/-": pos(0, 0, [], income=500, fees=120),
            "1/1": pos(10 * U, 100000, [(20250106, 10 * U, 100000)], income=1000, fees=300),
        },
        "moves": [
            mv(1, 10 * U, 100000, 0, 100000),
            mv(2, 0, 0, 0, 1000),
            mv(3, 0, 0, 0, 500),
            mv(4, 0, 0, 0, 300),
            mv(5, 0, 0, 0, 120),
        ],
        "totals": totals(income=1500, fees=420),
    },
    names=NAMES,
    words={
        2: f"Income {inr(1000)} before {inr(100)} TDS",
        3: f"Income {inr(500)}",
        4: f"Fee {inr(300)}",
        5: f"Fee {inr(120)}",
    },
)

# L6 ---------------------------------------------------------------------------------------
case(
    "L6",
    "FIFO follows the day a lot was bought, not the order it was typed",
    "The 10 Feb purchase (1,200.00) is typed first and the earlier 10 Jan purchase (1,000.00) "
    "second. A sale of 10 on 10 Mar takes the 10 Jan lot: cost 100000, gain 150000 - 100000 = "
    "50000. The 10 Feb lot is left whole.",
    [
        e(1, "BUY", 20250210, 1, 1, 10 * U, 120000),
        e(2, "BUY", 20250110, 1, 1, 10 * U, 100000),
        e(3, "SELL", 20250310, 1, 1, 10 * U, 150000),
    ],
    {
        "positions": {"1/1": pos(10 * U, 120000, [(20250210, 10 * U, 120000)], realised=50000)},
        "moves": [
            mv(2, 10 * U, 100000, 0, 100000),
            mv(1, 10 * U, 120000, 0, 120000),
            mv(3, -10 * U, -100000, 50000, 150000, [(20250110, 10 * U, 100000)]),
        ],
        "totals": totals(realised=50000),
    },
    names=NAMES,
)

# L7 ---------------------------------------------------------------------------------------
assert rd(3302 * 1, 3) == 1101
case(
    "L7",
    "Lots of one day go in the order they were made; a sale crosses the boundary",
    "Two purchases of 3 units on 6 Jan 2025: first 30.01 (3001), then 33.02 (3302). A sale of 4 "
    "takes all of the first (3001) and 1 of the second: 3302 x 1/3 = 1100.67 -> 1101. Cost "
    "3001 + 1101 = 4102; at 50.00 the gain is 5000 - 4102 = 898. Left: 2 units costing "
    "3302 - 1101 = 2201.",
    [
        e(1, "BUY", 20250106, 1, 1, 3 * U, 3001),
        e(2, "BUY", 20250106, 1, 1, 3 * U, 3302),
        e(3, "SELL", 20250210, 1, 1, 4 * U, 5000),
    ],
    {
        "positions": {"1/1": pos(2 * U, 2201, [(20250106, 2 * U, 2201)], realised=898)},
        "moves": [
            mv(1, 3 * U, 3001, 0, 3001),
            mv(2, 3 * U, 3302, 0, 3302),
            mv(3, -4 * U, -4102, 898, 5000, [(20250106, 3 * U, 3001), (20250106, U, 1101)]),
        ],
        "totals": totals(realised=898),
    },
    names=NAMES,
)


# D1, D2 -----------------------------------------------------------------------------------
case(
    "D1",
    "The design's first dry-run sentence (4.10)",
    "Buy 10 at 1,428.25 (1,428,250 paise) with 20.00 charges (2000): the lot costs 1,430,250 "
    "paise = 14,302.50 rupees. The sheet says: Cost 14,302.50 with 20.00 charges.",
    [e(1, "BUY", 20250110, 1, 1, 10 * U, 1_428_250, charges_paise=2000)],
    {
        "positions": {"1/1": pos(10 * U, 1_430_250, [(20250110, 10 * U, 1_430_250)])},
        "moves": [mv(1, 10 * U, 1_430_250, 0, 1_428_250)],
        "totals": totals(),
    },
    names=NAMES,
    words={1: f"Cost {inr(1_430_250)} with {inr(2000)} charges"},
)
assert inr(1_430_250) == RUPEE + "14,302.50"
case(
    "D2",
    "The design's second dry-run sentence (4.10)",
    "Buy 10 at 1,428.25 on 10 Jan 2025 (1,428,250) and 10 at 1,510.00 on 12 Feb 2025 "
    "(1,510,000). Sell 15 for 23,066.50 (2,306,650). The sale takes the 10 Jan lot whole "
    "(1,428,250) and 5 of the 10 Feb lot: 1,510,000 x 5/10 = 755,000. Cost 2,183,250; gain "
    "2,306,650 - 2,183,250 = 123,400 = 1,234.00. The sheet says: From 10 Jan 2025: 10 at "
    "1,428.25; from 12 Feb 2025: 5 at 1,510.00 · gain 1,234.00.",
    [
        e(1, "BUY", 20250110, 1, 1, 10 * U, 1_428_250),
        e(2, "BUY", 20250212, 1, 1, 10 * U, 1_510_000),
        e(3, "SELL", 20250310, 1, 1, 15 * U, 2_306_650),
    ],
    {
        "positions": {"1/1": pos(5 * U, 755_000, [(20250212, 5 * U, 755_000)], realised=123_400)},
        "moves": [
            mv(1, 10 * U, 1_428_250, 0, 1_428_250),
            mv(2, 10 * U, 1_510_000, 0, 1_510_000),
            mv(
                3,
                -15 * U,
                -2_183_250,
                123_400,
                2_306_650,
                [(20250110, 10 * U, 1_428_250), (20250212, 5 * U, 755_000)],
            ),
        ],
        "totals": totals(realised=123_400),
    },
    names=NAMES,
    words={
        3: f"From {day_text(20250110)}: {qty_text(10 * U)} at {price_text(14_282_500)}; "
        f"from {day_text(20250212)}: {qty_text(5 * U)} at {price_text(15_100_000)}"
        f"{DOT}gain {inr(123_400)}"
    },
)


# ---------------------------------------------------------------- money.json
def money():
    out = {
        "about": "Golden vectors for core/money.py (PA1). Every expected value is computed "
        "twice, with exact fractions and with Decimal ROUND_HALF_UP, by "
        "make_pa1.py, which imports nothing from sudarshan. Numbers stay below 2^53 so "
        "the phone's tests can read the same file."
    }
    ties = [
        (0, 7),
        (1, 2),
        (-1, 2),
        (3, 2),
        (-3, 2),
        (5, 10),
        (-5, 10),
        (4, 10),
        (-4, 10),
        (14, 10),
        (15, 10),
        (25, 10),
        (-25, 10),
        (35, 10),
        (7, 3),
        (-7, 3),
        (8, 3),
        (1, 3),
        (2, 3),
        (10005, 2),
        (-10005, 2),
        (10005, -2),
        (-10005, -2),
        (999_999_000_000, 10**8),
        (9_007_199_254_740_991, 2),
        (-9_007_199_254_740_991, 2),
    ]
    out["round_div"] = [{"n": n, "d": d, "expect": rd(n, d)} for n, d in ties]
    values = [
        ("VA1", 10 * U, 14_282_500, "10 x 1,428.25 = 14,282.50 rupees = 1,428,250 paise"),
        (
            "VA2",
            109_459_000,
            456_789,
            "109.459 x 45.6789 = 4999.96671 rupees = 499996.67 paise -> 499997",
        ),
        ("VA4", 500_000, 100, "0.5 x 0.01 rupees = 0.5 paise -> 1 (half away from zero)"),
        ("neg", -500_000, 100, "a loss of half a paisa mirrors the gain: -0.5 paise -> -1"),
        ("tiny", 1, 1, "10^-8 paise -> 0"),
        ("zero", 0, 123_456, "no units"),
        ("F3", 3 * U, 333_333, "3 x 33.3333 = 99.9999 rupees = 9999.99 paise -> 10000"),
        ("cent", U, 1, "1 unit at 0.0001 rupees = 0.01 paise -> 0"),
        ("half1", U, 50, "1 unit at 0.0050 rupees = 0.5 paise -> 1"),
        ("half2", 2 * U, 25, "2 units at 0.0025 rupees = 0.5 paise -> 1"),
        ("big", 1_000_000_000 * U // 1000, 14_282_500, "a million shares at 1,428.25"),
    ]
    out["value"] = [
        {"id": i, "qty_u": q, "price_u": p, "expect": rd(q * p, 10**8), "why": w}
        for i, q, p, w in values
    ]
    assert out["value"][2]["expect"] == 1 and out["value"][3]["expect"] == -1
    units = [(160000, 15 * U), (10000, 3 * U), (5002, U), (0, 5), (1, 3 * U), (999_999, 7 * U)]
    out["unit_price"] = [
        {"cost_paise": c, "qty_u": q, "expect": rd(c * 10**8, q)} for c, q in units
    ]
    out["unit_price"].append({"cost_paise": 1, "qty_u": 0, "expect": None})
    shares = [
        (10000, U, 3 * U),
        (6667, U, 2 * U),
        (3333, U, U),
        (120000, 5 * U, 10 * U),
        (10005, U, 2 * U),
        (0, U, 2 * U),
        (1, U, 3 * U),
        (2, U, 3 * U),
        (5, U, 2 * U),
        (499_997, 50_250_000, 109_459_000),
    ]
    out["share_of_cost"] = [
        {
            "cost_paise": c,
            "part_qty_u": p,
            "whole_qty_u": w,
            "expect": c if p == w else rd(c * p, w),
        }
        for c, p, w in shares
    ]
    pcts = [
        (800, 10000),
        (1, 3),
        (2, 3),
        (-1, 3),
        (0, 5),
        (5, 1),
        (1, 2_000_000),
        (-1, 2_000_000),
        (1, 4_000_000),
        (-1, 4_000_000),
        (-5, -100),
        (5, -100),
        (35, 400),
        (1, 8),
        (1, 16),
        (123_456_789, 1000),
        (-123_456_789, 1000),
    ]

    def pct_text(n, d):
        v = (Decimal(n) * 100 / Decimal(d)).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        text = format(v, "f")
        return "0.0000" if Decimal(text) == 0 else text

    out["pct"] = [{"n": n, "d": d, "expect": pct_text(n, d)} for n, d in pcts]
    out["pct"].append({"n": 7, "d": 0, "expect": None})
    rates = [
        "0.099714",
        "0.0997135905",
        "-0.00005",
        "0.0000005",
        "-0.0000005",
        "0.00000025",
        "-0.00000025",
        "0",
        "1.5",
        "-0.5",
    ]
    out["pct_of_rate"] = [{"rate": r, "expect": pct_text_rate(r)} for r in rates]
    prices = [1428.25, 249.98, 25900.0, 0.01, 100, 1, 0.05, 25903.65, 1e6]
    out["feed_price_u"] = [{"v": v, "expect": int(Decimal(repr(v)) * 100) * 100} for v in prices]
    for v in (0, -5, 0.004, 1e-9, None, True, "12", [1]):
        out["feed_price_u"].append({"v": v, "expect": None})
    return out


def pct_text_rate(rate: str) -> str:
    v = (Decimal(rate) * 100).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    text = format(v, "f")
    return "0.0000" if Decimal(text) == 0 else text


LEDGER_ABOUT = (
    "Golden vectors for the FIFO ledger (PA1: F1-F6, L1-L7, D1-D2). The numbers are derived by "
    "hand in each case's 'why'; make_pa1.py writes this file, imports nothing from sudarshan, and "
    "the tests hold the engine and a naive second implementation to it. Synthetic data only. "
    "Entries are the contract's ActivityInput names; a case's 'names' turn ids into the words of "
    "its sentences."
)


def build() -> dict:
    """The file name and contents of every vector file this generator writes."""
    ledger = {"about": LEDGER_ABOUT, "names": NAMES, "cases": CASES}
    return {"ledger.json": ledger, "money.json": money()}


def main() -> None:
    here = Path(__file__).parent
    for name, data in build().items():
        text = json.dumps(data, indent=1, ensure_ascii=False) + "\n"
        (here / name).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
