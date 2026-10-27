"""Generator of the PA4 golden vectors: performance.json.

The money flows of a replayed tab (the table of design 4.7), the XIRR with its three states (X1 to
X5 and the edges around them), the rates of dated cash on its own, the time-weighted return (T1 to
T5 and the edges), net money put in (N1) and the figures by financial year. Like the other
generators it imports nothing from sudarshan, and it finds every rate by another route than the
code does: the code solves a polynomial in q = (1 + r) ^ (-1 / 365) with whole-number powers; this
walks out from zero along the log of the rate in floats, bisects the first sign change, polishes it
with Newton's method on Decimal exponentials to 60 digits, and then checks the answer a third way,
by substituting it into the sum with a Decimal power. Every case carries its hand derivation and
the generator refuses to write a file in which what was typed by hand and what was computed
disagree, or in which a percentage sits close enough to a rounding tie to be a coin toss.

Run: python3 make_pa4.py
"""

from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from pathlib import Path

from portfolio_vector_tools import (
    U,
    day_text,
    days_apart,
    e,
    pct4,
    pct_text,
    plus_days,
    rd,
    write_all,
)

YEAR = 365
E = 2.718281828459045  # the base of natural logarithms, to a float
STEP = 0.01  # how far apart, in the log of the rate, the walk looks
REACH = 23.0  # and how far out: a loss of 99.99999999% or a gain of ten billion times
NEEDS = "Return needs at least one purchase and a value."
NO_RATE = "These purchases and sales do not give a single yearly return."
WAITING = "TWR needs closing prices for at least two days."


# ---------------------------------------------------------------- the rate, found another way
def merge_cash(cash):
    by_day = {}
    for day, paise in cash:
        by_day[day] = by_day.get(day, 0) + paise
    return sorted((d, c) for d, c in by_day.items() if c)


def both_ways(cash):
    return any(c < 0 for _, c in cash) and any(c > 0 for _, c in cash)


def npv_float(x, items):
    """The sum of the cash at the log rate x, in floats; items are (days from the first, paise)."""
    return sum(c * E ** (-x * d / YEAR) for d, c in items)


def first_change(items, side):
    """Walking out from zero on one side: the first step at which the sum changes sign."""
    before = npv_float(0.0, items)
    for k in range(1, round(REACH / STEP) + 1):
        x = side * k * STEP
        now = npv_float(x, items)
        if now == 0 or (now > 0) != (before > 0):
            return side * (k - 1) * STEP, x
        before = now
    return None


def bisect_float(items, low, high):
    f_low = npv_float(low, items)
    for _ in range(200):
        mid = (low + high) / 2
        f_mid = npv_float(mid, items)
        if f_mid == 0:
            return mid
        if (f_mid > 0) == (f_low > 0):
            low, f_low = mid, f_mid
        else:
            high = mid
    return (low + high) / 2


def newton(x, items):
    """Polish a log rate to far more digits than any float has: Newton on Decimal exponentials."""
    x = Decimal(repr(x))
    for _ in range(4):
        total = slope = Decimal(0)
        for d, c in items:
            term = (-x * d / YEAR).exp()
            total += c * term
            slope -= Decimal(c) * d / YEAR * term
        x -= total / slope
    return x


def substitute(rate, items):
    """The sum of the cash at a yearly rate, with a Decimal power: the third way."""
    return sum(Decimal(c) * (1 + rate) ** (Decimal(-d) / YEAR) for d, c in items)


def items_of(cash):
    cash = merge_cash(cash)
    return [(days_apart(cash[0][0], day), paise) for day, paise in cash]


def check_root(rate, items, where):
    assert abs(substitute(rate, items)) < Decimal("1e-30"), (where, substitute(rate, items))


def roots_walk(cash, where="rate"):
    """Every rate of the cash found by walking the whole log scale, nearest zero first."""
    cash = merge_cash(cash)
    if not both_ways(cash):
        return []
    items = items_of(cash)
    found = []
    if sum(c for _, c in items) == 0:
        found.append(Decimal(0))
    x = -REACH
    before = npv_float(x, items)
    while x < REACH - 1e-9:
        x += STEP
        now = npv_float(x, items)
        if now != 0 and (now > 0) != (before > 0):
            root = newton(bisect_float(items, x - STEP, x), items)
            if all(abs(root - other) > Decimal("1e-9") for other in found):
                found.append(root)  # (the exact zero is also seen by the walk: once is enough)
        before = now
    rates = sorted((x.exp() - 1 if x != 0 else x) for x in found)
    for rate in rates:
        check_root(rate, items, where)
    return sorted(rates, key=lambda r: abs((1 + r).ln()))


def nearest_rate(cash, where="rate"):
    """The rate nearest zero on the log scale (walking out from zero, as the rule says), or None."""
    cash = merge_cash(cash)
    if not both_ways(cash):
        return None
    items = items_of(cash)
    if sum(c for _, c in items) == 0:
        return Decimal(0)
    roots = []
    for side in (1, -1):
        bracket = first_change(items, side)
        if bracket is not None:
            x = newton(bisect_float(items, *sorted(bracket)), items)
            roots.append(x)
    if not roots:
        return None
    # Nearest zero on the log scale; two equally near (mirror images, as when the cash reads the
    # same backwards) go to the gain, which is the first of them: the two polishings differ in the
    # last digits, so "equally near" is judged to a part in 1e12.
    near = min(abs(x) for x in roots)
    x = next(x for x in roots if abs(x) <= near * (1 + Decimal("1e-12")))
    rate = x.exp() - 1
    check_root(rate, items, where)
    return rate


def check_roots(cash, roots, where):
    """``roots`` lists every rate that fits as an exact fraction ("1/20"), the smallest first. With
    every gap a whole number of years each one makes the cash add up to exactly nothing in
    fractions, and the walk of the whole log scale finds these and no others."""
    items = items_of(cash)
    assert all(d % YEAR == 0 for d, _ in items), where
    rates = [Fraction(text) for text in roots]
    assert rates == sorted(rates), where
    for rate in rates:
        assert sum(c * (1 + rate) ** (-(d // YEAR)) for d, c in items) == 0, (where, rate)
    walked = sorted(roots_walk(cash, where))
    assert len(walked) == len(rates), (where, walked)
    for got, want in zip(walked, rates, strict=True):
        assert abs(got - Decimal(want.numerator) / Decimal(want.denominator)) < Decimal("1e-25")


def rate_text(rate, where="rate"):
    """A rate as a percentage with four decimals, half away from zero; refuses a near tie."""
    scaled = rate * 100 * 10_000
    margin = abs(abs(scaled) % 1 - Decimal("0.5"))
    # The code's rate is good to about 4e-15 of itself (a bracket of 1e-17 of q, to the 365th
    # power): a percentage nearer than that to a rounding tie could go either way.
    assert margin > max(Decimal("1e-5"), abs(scaled) * Decimal("1e-14")), (where, scaled)
    return pct_text(int(scaled.to_integral_value(ROUND_HALF_UP)))


def rate_digits(rate):
    return format(rate.quantize(Decimal("1e-12")), "f")


# ---------------------------------------------------------------- flows, the figures of a tab
def fl(day, inn=0, out=0, fee=0):
    return {"day": day, "in_paise": inn, "out_paise": out, "fee_paise": fee}


def cash_of(flows, value, vday):
    cash = {}
    for f in flows:
        if f["day"] <= vday:
            cash[f["day"]] = cash.get(f["day"], 0) + f["out_paise"] - f["in_paise"] - f["fee_paise"]
    if value:
        cash[vday] = cash.get(vday, 0) + value
    return sorted((d, c) for d, c in cash.items() if c)


def xirr_of(flows, value, vday, where):
    live = [f for f in flows if f["day"] <= vday]
    cash = cash_of(live, value, vday)
    if not both_ways(cash):
        return {"state": "none", "pct": None, "from_day": None, "words": NEEDS}
    if days_apart(cash[0][0], vday) < YEAR:
        appears = plus_days(cash[0][0], YEAR)
        words = f"XIRR appears after one year (from {day_text(appears)})."
        return {"state": "later", "pct": None, "from_day": appears, "words": words}
    rate = nearest_rate(cash, where)
    if rate is None:
        return {"state": "none", "pct": None, "from_day": None, "words": NO_RATE}
    return {"state": "shown", "pct": rate_text(rate, where), "from_day": None, "words": None}


def hidden_rate(flows, value, vday):
    """What the XIRR would be, for a case where it is not shown yet."""
    return nearest_rate(cash_of(flows, value, vday))


def summary_of(flows, value, vday, where):
    live = [f for f in flows if f["day"] <= vday]
    inn = sum(f["in_paise"] for f in live)
    out = sum(f["out_paise"] for f in live)
    fee = sum(f["fee_paise"] for f in live)
    moved = [f["day"] for f in live if f["in_paise"] or f["out_paise"] or f["fee_paise"]]
    net = inn + fee - out
    return {
        "valuation_day": vday,
        "first_flow_day": min(moved) if moved else None,
        "value_paise": value,
        "money_in_paise": inn,
        "money_out_paise": out,
        "fee_paise": fee,
        "net_put_in_paise": net,
        "total_gain_paise": value - net,
        "absolute_return_pct": pct4(value - net, inn + fee),
        "xirr": xirr_of(live, value, vday, where),
    }


def agrees(got, hand, where):
    """Every number typed by hand in a case is the evaluator's number too, or nothing is written."""
    for path, want in hand.items():
        node = got
        for key in path.split("."):
            node = node[int(key)] if isinstance(node, list) else node[key]
        assert node == want, (where, path, node, want)


# ---------------------------------------------------------------- the time-weighted return
def pt(day, value, inn=0, out=0, fee=0):
    return {
        "day": day,
        "value_paise": value,
        "in_paise": inn,
        "out_paise": out,
        "fee_paise": fee,
    }


def merge_points(points):
    merged = {}
    for p in sorted(points, key=lambda p: p["day"]):
        old = merged.get(p["day"])
        if old is None:
            merged[p["day"]] = dict(p)
            continue
        for key in ("in_paise", "out_paise", "fee_paise"):
            old[key] += p[key]
        if p["value_paise"] is not None:
            old["value_paise"] = p["value_paise"]
    return list(merged.values())


def twr_of(points, opening, where):
    """The periods and the figure of a daily series, worked out from the complete days outward."""
    pts = merge_points(points)
    complete = [i for i, p in enumerate(pts) if p["value_paise"] is not None]
    periods = []
    previous, value, day = -1, opening, None
    for i in complete:
        window = pts[previous + 1 : i + 1]
        inn = sum(p["in_paise"] for p in window)
        out = sum(p["out_paise"] for p in window)
        fee = sum(p["fee_paise"] for p in window)
        if value is not None and value + inn > 0:
            grown = max(0, pts[i]["value_paise"] + out - fee)
            start = window[0]["day"] if day is None else day
            periods.append(
                {
                    "start": start,
                    "day": pts[i]["day"],
                    "base": value + inn,
                    "grown": grown,
                    "pct": pct4(grown - (value + inn), value + inn),
                }
            )
        previous, value, day = i, pts[i]["value_paise"], pts[i]["day"]
    if not periods:
        return periods, {"start_day": None, "pct": None, "annualised_pct": None, "words": WAITING}
    growth = Fraction(1)
    for p in periods:
        growth *= Fraction(p["grown"], p["base"])
    span = days_apart(periods[0]["start"], periods[-1]["day"])
    annual = None
    if span > YEAR:
        ratio = Decimal(growth.numerator) / Decimal(growth.denominator)
        annual = rate_text(ratio ** (Decimal(YEAR) / span) - 1, where)
    figure = {
        "start_day": periods[0]["start"],
        "pct": pct4(growth.numerator - growth.denominator, growth.denominator),
        "annualised_pct": annual,
        "words": f"TWR since {day_text(periods[0]['start'])}",
    }
    return periods, figure


# ---------------------------------------------------------------- a small ledger, for the flows
def amount_of(row):
    if row.get("amount_paise") is not None:
        return row["amount_paise"]
    return rd(row["qty_u"] * row["price_u"], 10**8)


def rank(row):
    kind = row["kind"]
    if kind in ("SPLIT", "BONUS"):
        return 0
    if kind in ("BUY", "RIGHTS"):
        return 1
    if kind == "TRANSFER":
        if row.get("account_id") is None:
            return 1
        return 3 if row.get("to_account_id") is None else 2
    return 3 if kind == "SELL" else 4


class Book:
    """Lots by account and asset, oldest first; the cost of part of a lot is rounded half away."""

    def __init__(self):
        self.lots = {}
        self.made = 0

    def add(self, account, asset, day, qty, cost):
        self.made += 1
        lots = self.lots.setdefault((account, asset), [])
        lots.append([day, self.made, qty, cost])
        lots.sort(key=lambda lot: (lot[0], lot[1]))

    def take(self, account, asset, qty):
        lots, taken, need = self.lots[(account, asset)], [], qty
        assert sum(lot[2] for lot in lots) >= qty, "the case sells what it does not hold"
        while need:
            lot = lots[0]
            part = min(need, lot[2])
            cost = lot[3] if part == lot[2] else rd(lot[3] * part, lot[2])
            taken.append((lot[0], part, cost))
            lot[2] -= part
            lot[3] -= cost
            need -= part
            if not lot[2]:
                lots.pop(0)
        return taken


def walk(rows, as_of=None):
    """Replay the rows and say what moved: the flows by day, the gains, the income and the fees."""
    live = [
        r
        for r in rows
        if r.get("status", "posted") == "posted" and (as_of is None or r["trade_date"] <= as_of)
    ]
    live.sort(key=lambda r: (r["trade_date"], rank(r), r["seq"], r["id"]))
    book, days = Book(), {}
    gains, income, fees = [], [], []

    def flow(day):
        return days.setdefault(day, {"in_paise": 0, "out_paise": 0, "fee_paise": 0})

    for r in live:
        kind, day = r["kind"], r["trade_date"]
        account, asset = r.get("account_id"), r.get("asset_id")
        if kind in ("BUY", "RIGHTS"):
            cost = amount_of(r) + r.get("charges_paise", 0)
            book.add(account, asset, day, r["qty_u"], cost)
            flow(day)["in_paise"] += cost
        elif kind == "SELL":
            cost = sum(c for _, _, c in book.take(account, asset, r["qty_u"]))
            net = amount_of(r) - r.get("charges_paise", 0)
            gains.append((day, net - cost))
            flow(day)["out_paise"] += net
        elif kind in ("DIVIDEND", "INTEREST"):
            income.append((day, r["amount_paise"]))
            flow(day)["out_paise"] += r["amount_paise"]
        elif kind == "FEE":
            fees.append((day, r["amount_paise"]))
            flow(day)["fee_paise"] += r["amount_paise"]
        elif kind == "BONUS":
            book.add(account, asset, day, r["qty_u"], 0)
        elif kind == "SPLIT":
            num, den = r["ratio_num"], r["ratio_den"]
            for (_, lot_asset), lots in book.lots.items():
                if lot_asset == asset and account in (None, _):
                    for lot in lots:
                        assert lot[2] * num % den == 0
                        lot[2] = lot[2] * num // den
        else:  # TRANSFER
            target, qty = r.get("to_account_id"), r["qty_u"]
            if account is None:
                value = r["amount_paise"] if r.get("value_paise") is None else r["value_paise"]
                book.add(target, asset, r.get("lot_date") or day, qty, r["amount_paise"])
                flow(day)["in_paise"] += value
                continue
            taken = book.take(account, asset, qty)
            if target is None:
                cost = sum(c for _, _, c in taken)
                flow(day)["out_paise"] += cost if r.get("value_paise") is None else r["value_paise"]
            else:
                for lot_day, part, cost in taken:
                    book.add(target, asset, lot_day, part, cost)
    flows = [{"day": d, **v} for d, v in sorted(days.items()) if any(v.values())]
    return {"flows": flows, "gains": gains, "income": income, "fees": fees}


def fy_of(day):
    return day // 10000 if day // 100 % 100 >= 4 else day // 10000 - 1


def fy_figures(walked):
    years = {}
    for key, entries in (
        ("realised", walked["gains"]),
        ("income", walked["income"]),
        ("fees", walked["fees"]),
    ):
        for day, paise in entries:
            years.setdefault(fy_of(day), {"realised": 0, "income": 0, "fees": 0})[key] += paise
    return [
        {
            "fy": fy,
            "label": f"FY {fy}-{(fy + 1) % 100:02d}",
            "realised_paise": v["realised"],
            "income_paise": v["income"],
            "fees_paise": v["fees"],
        }
        for fy, v in sorted(years.items())
        if any(v.values())
    ]


def income_figures(walked, vday):
    year, month = vday // 10000, vday // 100 % 100
    months = {}
    for back in range(11, -1, -1):
        n = year * 12 + month - 1 - back
        months[n // 12 * 100 + n % 12 + 1] = 0
    this_fy = 0
    for day, paise in walked["income"]:
        if day > vday:
            continue
        if fy_of(day) == fy_of(vday):
            this_fy += paise
        if day // 100 in months:
            months[day // 100] += paise
    return {
        "this_fy_paise": this_fy,
        "by_month": [{"month": m, "paise": p} for m, p in months.items()],
    }


# ---------------------------------------------------------------- the flows of a ledger
FLOWS = []
SHARE = U  # one share


def case_flows(cid, title, why, rows, expect, as_of=None, **hand):
    walked = walk(rows, as_of)
    got = {"flows": walked["flows"], "totals": totals_of(walked["flows"])}
    assert got["flows"] == expect, (cid, got["flows"], expect)
    agrees(got, hand, cid)
    case = {"id": cid, "title": title, "why": why, "entries": rows, "expect": got}
    if as_of is not None:
        case["as_of"] = as_of
    FLOWS.append(case)


def totals_of(flows):
    return {
        "in_paise": sum(f["in_paise"] for f in flows),
        "out_paise": sum(f["out_paise"] for f in flows),
        "fee_paise": sum(f["fee_paise"] for f in flows),
    }


case_flows(
    "FL1",
    "A purchase is money in, its charges with it",
    "10 shares for 1,000.00 (100000 paise) with 20.00 (2000) of charges: 102000 paise went out of "
    "the owner's pocket on 10 Jan 2025.",
    [e(1, "BUY", 20250110, 1, 1, 10 * SHARE, 100_000, charges_paise=2_000)],
    [fl(20250110, inn=102_000)],
    **{"totals.in_paise": 102_000},
)
case_flows(
    "FL2",
    "A sale is money out, net of its charges",
    "The purchase of FL1, then all 10 sold on 10 Feb for 1,100.00 (110000) with 15.00 (1500) of "
    "charges: 108500 came in. Nothing is counted twice: the purchase is money in and the sale is "
    "money out, and the gain is not a flow.",
    [
        e(1, "BUY", 20250110, 1, 1, 10 * SHARE, 100_000, charges_paise=2_000),
        e(2, "SELL", 20250210, 1, 1, 10 * SHARE, 110_000, charges_paise=1_500),
    ],
    [fl(20250110, inn=102_000), fl(20250210, out=108_500)],
    **{"totals.out_paise": 108_500},
)
case_flows(
    "FL3",
    "A rights issue is a purchase",
    "2 shares taken up for 160.00 (16000) with 1.00 (100) of charges: 16100 in, on the day paid.",
    [e(1, "RIGHTS", 20250305, 1, 1, 2 * SHARE, 16_000, charges_paise=100)],
    [fl(20250305, inn=16_100)],
)
case_flows(
    "FL4",
    "A dividend is its gross amount out; the tax withheld is not a loss",
    "A dividend of 50.00 (5000) with 5.00 (500) of TDS withheld: the flow is the gross 5000, as "
    "the tab's income is. (TDS is tax the owner paid, not money lost.)",
    [e(1, "DIVIDEND", 20250601, 1, 1, amount=5_000, tds_paise=500)],
    [fl(20250601, out=5_000)],
)
case_flows(
    "FL5",
    "Interest is money out, with or without a holding",
    "Interest of 12.00 (1200) credited to the account with no holding named, and of 30.00 (3000) "
    "on a deposit: both out, on their days.",
    [
        e(1, "INTEREST", 20250701, 1, None, amount=1_200),
        e(2, "INTEREST", 20250702, 1, 1, amount=3_000),
    ],
    [fl(20250701, out=1_200), fl(20250702, out=3_000)],
)
case_flows(
    "FL6",
    "A fee on its own is a fee, neither in nor out",
    "A fee of 3.00 (300) charged on its own: a cost paid from outside the holdings, so it is "
    "neither money in nor money out but its own term.",
    [e(1, "FEE", 20250801, 1, None, amount=300)],
    [fl(20250801, fee=300)],
    **{"totals.fee_paise": 300},
)
case_flows(
    "FL7",
    "Shares brought in from outside are money in on the day of the transfer, at the value entered",
    "10 shares that cost 10,000.00 (1000000) when bought on 15 Jan 2023 were moved into the tab on "
    "1 Jun 2025: the value counted is the cost, since none was given, and the day is the day of the "
    "transfer, not the day of the first purchase. 5 more, costing 5,000.00, moved in on 2 Jun with "
    "a value of 7,500.00 (750000): that is the money in.",
    [
        e(
            1,
            "TRANSFER",
            20250601,
            None,
            1,
            10 * SHARE,
            1_000_000,
            to_account_id=1,
            lot_date=20230115,
        ),
        e(
            2,
            "TRANSFER",
            20250602,
            None,
            1,
            5 * SHARE,
            500_000,
            to_account_id=1,
            value_paise=750_000,
        ),
    ],
    [fl(20250601, inn=1_000_000), fl(20250602, inn=750_000)],
)
case_flows(
    "FL8",
    "Shares taken out to outside are money out at the value entered, else at cost",
    "10 shares bought for 1,000.00 (100000). 4 are moved out with no value: they leave at their "
    "cost, 400.00 (40000), and that is the money out. 2 more are moved out with a value of 300.00 "
    "(30000): that is the money out, whatever they cost (20000).",
    [
        e(1, "BUY", 20250110, 1, 1, 10 * SHARE, 100_000),
        e(2, "TRANSFER", 20250301, 1, 1, 4 * SHARE),
        e(3, "TRANSFER", 20250302, 1, 1, 2 * SHARE, value_paise=30_000),
    ],
    [fl(20250110, inn=100_000), fl(20250301, out=40_000), fl(20250302, out=30_000)],
)
case_flows(
    "FL9",
    "Shares moved between accounts of the tab move no money (T5)",
    "10 shares bought in account 1 for 1,000.00 (100000) and moved to account 2 on 5 Mar. The "
    "transfer is neither money in nor out: only the purchase is a flow.",
    [
        e(1, "BUY", 20250110, 1, 1, 10 * SHARE, 100_000),
        e(2, "TRANSFER", 20250305, 1, 1, 10 * SHARE, to_account_id=2),
    ],
    [fl(20250110, inn=100_000)],
)
case_flows(
    "FL10",
    "Splits and bonus shares move no money",
    "10 shares for 1,000.00 (100000), each becoming 5 on 1 Mar, and 10 bonus shares on 1 Apr: "
    "nothing but the purchase is a flow.",
    [
        e(1, "BUY", 20250110, 1, 1, 10 * SHARE, 100_000),
        e(2, "SPLIT", 20250301, None, 1, ratio_num=5, ratio_den=1),
        e(3, "BONUS", 20250401, 1, 1, 10 * SHARE),
    ],
    [fl(20250110, inn=100_000)],
)
case_flows(
    "FL11",
    "Everything on one day is one flow",
    "On 10 Mar: 5 shares for 500.00 (50000), 5 for 600.00 (60000) with 1.00 (100) of charges, 4 "
    "sold for 480.00 (48000) less 0.50 (50) of charges, a dividend of 10.00 (1000) and a fee of "
    "2.00 (200). In: 50000 + 60100 = 110100. Out: 47950 + 1000 = 48950. Fee: 200. One flow.",
    [
        e(1, "BUY", 20250310, 1, 1, 5 * SHARE, 50_000),
        e(2, "BUY", 20250310, 1, 1, 5 * SHARE, 60_000, charges_paise=100),
        e(3, "SELL", 20250310, 1, 1, 4 * SHARE, 48_000, charges_paise=50),
        e(4, "DIVIDEND", 20250310, 1, 1, amount=1_000),
        e(5, "FEE", 20250310, 1, None, amount=200),
    ],
    [fl(20250310, inn=110_100, out=48_950, fee=200)],
)
case_flows(
    "FL12",
    "A draft and a day after the day asked for count nowhere",
    "The purchase of 1,000.00 on 10 Jan counts. The draft of 2,000.00 on 5 Feb counts nowhere. "
    "The purchase of 500.00 on 10 Mar is after the 1 Mar the tab is read up to.",
    [
        e(1, "BUY", 20250110, 1, 1, 10 * SHARE, 100_000),
        e(2, "BUY", 20250205, 1, 1, 10 * SHARE, 200_000, status="draft"),
        e(3, "BUY", 20250310, 1, 1, 5 * SHARE, 50_000),
    ],
    [fl(20250110, inn=100_000)],
    as_of=20250301,
)
case_flows(
    "FL13",
    "A sale whose charges are above its amount is money in",
    "1 share for 100.00 (10000), sold for 10.00 (1000) with 15.00 (1500) of charges: 1000 - 1500 "
    "is -500, which is money in (the owner paid 5.00 to sell). Nothing is clamped.",
    [
        e(1, "BUY", 20250110, 1, 1, SHARE, 10_000),
        e(2, "SELL", 20250210, 1, 1, SHARE, 1_000, charges_paise=1_500),
    ],
    [fl(20250110, inn=10_000), fl(20250210, out=-500)],
)
case_flows(
    "FL14",
    "An entry that moves no money is no flow",
    "Free shares entered as a purchase with an amount of nothing, and a bonus: no flow at all.",
    [
        e(1, "BUY", 20250110, 1, 1, 10 * SHARE, 0),
        e(2, "BONUS", 20250401, 1, 1, 10 * SHARE),
    ],
    [],
)
case_flows(
    "FL15",
    "A valuable added by hand is a purchase",
    "12.5 grams of gold for 85,000.00 (8500000) entered as a purchase of a holding kept by hand: "
    "8500000 in.",
    [e(1, "BUY", 20250110, 1, 3, 12_500_000, 8_500_000)],
    [fl(20250110, inn=8_500_000)],
)
case_flows(
    "FL16",
    "Flows come out oldest day first, whatever order the entries were made in",
    "Three entries made out of date order: the flows are on 10 Jan, 10 Feb and 10 Mar, in that order.",
    [
        e(1, "SELL", 20250210, 1, 1, 5 * SHARE, 60_000),
        e(2, "DIVIDEND", 20250310, 1, 1, amount=1_000),
        e(3, "BUY", 20250110, 1, 1, 10 * SHARE, 100_000),
    ],
    [fl(20250110, inn=100_000), fl(20250210, out=60_000), fl(20250310, out=1_000)],
)


# ---------------------------------------------------------------- the XIRR of a tab
XIRR = []
RATES = []


def case_xirr(cid, title, why, flows, value, vday, closed=None, **hand):
    expect = summary_of(flows, value, vday, cid)
    agrees(expect, hand, cid)
    if closed is not None:  # a formula that gives the rate without solving anything
        assert expect["xirr"]["pct"] == rate_text(closed, cid), (cid, closed)
    XIRR.append(
        {
            "id": cid,
            "title": title,
            "why": why,
            "flows": flows,
            "value_paise": value,
            "valuation_day": vday,
            "expect": expect,
        }
    )
    return expect


ONE_BUY = [fl(20240101, inn=1_000_000)]
D = Decimal
case_xirr(
    "X1",
    "Ten thousand to eleven thousand over 366 days",
    "10,000.00 paid in on 1 Jan 2024 is worth 11,000.00 on 1 Jan 2025, 366 days later (2024 is a "
    "leap year). With Act/365 the rate r solves 10000 (1 + r) ^ (366 / 365) = 11000, so "
    "1 + r = 1.1 ^ (365 / 366) = 1.099714, 9.9714%. The absolute return is 10.0000%.",
    ONE_BUY,
    1_100_000,
    20250101,
    closed=D("1.1") ** (D(365) / 366) - 1,
    **{"xirr.pct": "9.9714", "absolute_return_pct": "10.0000", "xirr.state": "shown"},
)
X2_FLOWS = [
    fl(20230403, inn=5_000_000),
    fl(20231016, inn=2_500_000),
    fl(20240614, out=120_000),
    fl(20250328, out=2_000_000),
]
case_xirr(
    "X2",
    "Two purchases, a dividend, a sale and a value",
    "50,000.00 paid in on 3 Apr 2023 and 25,000.00 on 16 Oct 2023; 1,200.00 of dividend on 14 Jun "
    "2024 and 20,000.00 from a sale on 28 Mar 2025; worth 82,000.00 on 27 Oct 2026. No formula "
    "gives the rate; it is the one at which those cash flows (negative paid, positive received) "
    "add up to nothing: 11.0863%. Absolute return: (82000 + 21200 - 75000) / 75000 = 37.6000%.",
    X2_FLOWS,
    8_200_000,
    20261027,
    **{"xirr.pct": "11.0863", "absolute_return_pct": "37.6000"},
)
X3 = case_xirr(
    "X3",
    "Under a year: absolute return, and the date the XIRR appears",
    "10,000.00 paid in on 1 May 2026, worth 10,800.00 on 27 Oct 2026, 179 days later. The "
    "absolute return is 8.0000%. The XIRR would be 1.08 ^ (365 / 179) - 1, about 16.99%, but over "
    "so short a time it only says how the gain would compound: it is hidden until the first flow "
    "is 365 days old, which is 1 May 2027.",
    [fl(20260501, inn=1_000_000)],
    1_080_000,
    20261027,
    **{
        "xirr.state": "later",
        "xirr.pct": None,
        "xirr.from_day": 20270501,
        "xirr.words": "XIRR appears after one year (from 1 May 2027).",
        "absolute_return_pct": "8.0000",
    },
)
assert days_apart(20260501, 20261027) == 179
assert rate_text(hidden_rate([fl(20260501, inn=1_000_000)], 1_080_000, 20261027)) == "16.9916"
case_xirr(
    "X3b",
    "Exactly 365 days: the XIRR is shown",
    "The same payment and value, read on 1 May 2027, exactly 365 days after the first flow: the "
    "rule is 'at least 365 days', so the XIRR is shown, and with one year to the day it is the "
    "absolute return, 8.0000%.",
    [fl(20260501, inn=1_000_000)],
    1_080_000,
    20270501,
    closed=D("0.08"),
    **{"xirr.state": "shown", "xirr.pct": "8.0000"},
)
case_xirr(
    "X3c",
    "364 days: not yet",
    "The same, read on 30 Apr 2027, 364 days after the first flow: still hidden, and it still "
    "says it appears on 1 May 2027.",
    [fl(20260501, inn=1_000_000)],
    1_080_000,
    20270430,
    **{"xirr.state": "later", "xirr.from_day": 20270501},
)
assert days_apart(20260501, 20270501) == 365
case_xirr(
    "X3d",
    "The year is counted in days: across a 29 February it ends the day before the anniversary",
    "First flow 1 Mar 2027, read on 28 Feb 2028: 364 days (2028 is a leap year, so 1 Mar 2028 is "
    "366 days on). The XIRR appears on 29 Feb 2028, 365 days after the first flow, not on the "
    "calendar anniversary.",
    [fl(20270301, inn=1_000_000)],
    1_050_000,
    20280228,
    **{"xirr.state": "later", "xirr.from_day": 20280229},
)
assert days_apart(20270301, 20280229) == 365
case_xirr(
    "X3e",
    "... and it is shown on that day",
    "The same, read on 29 Feb 2028: 365 days, so shown. 10,500.00 on 10,000.00 over 365 days is "
    "5.0000%.",
    [fl(20270301, inn=1_000_000)],
    1_050_000,
    20280229,
    closed=D("0.05"),
    **{"xirr.state": "shown", "xirr.pct": "5.0000"},
)
case_xirr(
    "X4",
    "Only money in and nothing left: no return to show",
    "10,000.00 paid in and nothing now worth anything: the money was all lost and there is no "
    "yearly rate for that. The absolute return is -100.0000%.",
    ONE_BUY,
    0,
    20260101,
    **{"xirr.state": "none", "xirr.words": NEEDS, "absolute_return_pct": "-100.0000"},
)
case_xirr(
    "X4b",
    "Nothing at all",
    "No flows and no value: no XIRR and no absolute return.",
    [],
    0,
    20260101,
    **{"xirr.state": "none", "absolute_return_pct": None, "first_flow_day": None},
)
case_xirr(
    "X4c",
    "A value and no purchase",
    "A value of 5,000.00 with no purchase recorded: nothing to compare it with, so no figure.",
    [],
    500_000,
    20260101,
    **{"xirr.state": "none", "absolute_return_pct": None, "total_gain_paise": 500_000},
)
case_xirr(
    "X4d",
    "Under a year and nothing left: none, not 'later'",
    "10,000.00 paid in on 1 May 2026 and nothing worth anything on 27 Oct 2026. A year from now "
    "there would still be no XIRR, so it must not promise one: 'none', with the same sentence.",
    [fl(20260501, inn=1_000_000)],
    0,
    20261027,
    **{"xirr.state": "none", "xirr.words": NEEDS},
)
case_xirr(
    "X5",
    "Everything sold: an XIRR from the flows alone",
    "10,000.00 paid in on 1 Jan 2025 and 10,500.00 received on 1 Jul 2025 (181 days), nothing "
    "held on 27 Oct 2026. The absolute return is 5.0000%. The XIRR does not need a final value: "
    "1.05 ^ (365 / 181) - 1 = 10.3392%.",
    [fl(20250101, inn=1_000_000), fl(20250701, out=1_050_000)],
    0,
    20261027,
    closed=D("1.05") ** (D(365) / 181) - 1,
    **{"xirr.pct": "10.3392", "absolute_return_pct": "5.0000"},
)
assert days_apart(20250101, 20250701) == 181
case_xirr(
    "X5b",
    "Everything sold within a year: absolute return and the date",
    "The same flows read on 1 Aug 2025, 212 days after the first: the absolute return, 5.0000%, "
    "and the XIRR appears from 1 Jan 2026.",
    [fl(20250101, inn=1_000_000), fl(20250701, out=1_050_000)],
    0,
    20250801,
    **{"xirr.state": "later", "xirr.from_day": 20260101, "absolute_return_pct": "5.0000"},
)
case_xirr(
    "XA",
    "A loss over two years",
    "10,000.00 paid in on 1 Jan 2025 is worth 8,000.00 on 1 Jan 2027, 730 days (2 x 365) later: "
    "(1 + r) ^ 2 = 0.8, so r = sqrt(0.8) - 1 = -10.5573%.",
    [fl(20250101, inn=1_000_000)],
    800_000,
    20270101,
    closed=D("0.8").sqrt() - 1,
    **{"xirr.pct": "-10.5573", "absolute_return_pct": "-20.0000"},
)
case_xirr(
    "XB",
    "Four times the money in two years is 100% a year",
    "10,000.00 becomes 40,000.00 in 730 days: (1 + r) ^ 2 = 4, r = 100.0000% exactly.",
    [fl(20250101, inn=1_000_000)],
    4_000_000,
    20270101,
    **{"xirr.pct": "100.0000", "absolute_return_pct": "300.0000"},
)
case_xirr(
    "XC",
    "No gain at all is 0.0000%, never -0.0000%",
    "10,000.00 in, 10,000.00 two years later: the rate is 0, and is written 0.0000.",
    [fl(20250101, inn=1_000_000)],
    1_000_000,
    20270101,
    **{"xirr.pct": "0.0000", "absolute_return_pct": "0.0000"},
)
case_xirr(
    "XD",
    "A fee is a cost, not a flow of its own kind",
    "10,000.00 bought on 1 Jan 2025 with a fee of 200.00 (20000) the same day, worth 11,200.00 "
    "exactly 365 days later. The fee is paid in like the purchase: 10,200.00 became 11,200.00, so "
    "r = 11200 / 10200 - 1 = 9.8039%. The absolute return is the same: (11200 - 10200) / 10200.",
    [fl(20250101, inn=1_000_000, fee=20_000)],
    1_120_000,
    20260101,
    closed=D(11_200) / 10_200 - 1,
    **{
        "xirr.pct": "9.8039",
        "absolute_return_pct": "9.8039",
        "net_put_in_paise": 1_020_000,
        "total_gain_paise": 100_000,
    },
)
case_xirr(
    "XE",
    "Two purchases on one day are one flow",
    "6,000.00 and 4,000.00 bought on 1 Jan 2025 are 10,000.00 in on that day: the same as one "
    "purchase of 10,000.00, worth 11,000.00 on 1 Jan 2026 (365 days): 10.0000%.",
    [fl(20250101, inn=600_000), fl(20250101, inn=400_000)],
    1_100_000,
    20260101,
    closed=D("0.1"),
    **{"xirr.pct": "10.0000", "money_in_paise": 1_000_000},
)
case_xirr(
    "XF",
    "A flow after the valuation day is not part of it",
    "10,000.00 in on 1 Jan 2025, worth 11,000.00 on 1 Jan 2026. A sale of 5,000.00 dated 1 Jan "
    "2027 is after the day the tab is read on, so it counts for nothing: 10.0000%, and no money "
    "out.",
    [fl(20250101, inn=1_000_000), fl(20270101, out=500_000)],
    1_100_000,
    20260101,
    closed=D("0.1"),
    **{"xirr.pct": "10.0000", "money_out_paise": 0},
)
case_xirr(
    "XG",
    "Buying and selling the same amount on one day leaves no flow",
    "10,000.00 bought and 10,000.00 sold on 1 Jan 2025, nothing held two years on: the day's cash "
    "is nothing, and there is nothing to find a rate for. The flows still show money in and out.",
    [fl(20250101, inn=1_000_000, out=1_000_000)],
    0,
    20270101,
    **{"xirr.state": "none", "xirr.words": NEEDS, "absolute_return_pct": "0.0000"},
)
case_xirr(
    "XL",
    "A round trip on the first day starts nothing",
    "On 1 Jan 2025 10,000.00 is bought and 10,000.00 sold: the day's cash is nothing and it is no "
    "start. On 1 Feb 2026 1,000.00 is bought and on 2 Feb sold for 1,100.00; nothing is held on "
    "1 Mar 2026. The cash spans one day and the year is counted from 1 Feb 2026, so the XIRR "
    "(1.1 ^ 365 - 1, a number with 18 digits) is not shown and appears from 1 Feb 2027. The "
    "absolute return counts the round trip too: in 10,000.00 + 1,000.00 = 11,000.00, out 10,000.00 "
    "+ 1,100.00 = 11,100.00, so (0 + 11,100.00 - 11,000.00) / 11,000.00 = 0.9091%.",
    [
        fl(20250101, inn=1_000_000, out=1_000_000),
        fl(20260201, inn=100_000),
        fl(20260202, out=110_000),
    ],
    0,
    20260301,
    **{
        "xirr.state": "later",
        "xirr.from_day": 20270201,
        "first_flow_day": 20250101,
        "absolute_return_pct": "0.9091",
    },
)
SIP_FLOWS = [
    fl(d, inn=500_000)
    for d in (
        20250101,
        20250201,
        20250301,
        20250401,
        20250501,
        20250601,
        20250701,
        20250801,
        20250901,
        20251001,
        20251101,
        20251201,
        20260101,
    )
]
case_xirr(
    "XH",
    "Thirteen monthly purchases and a value after a year",
    "5,000.00 paid in on the 1st of each month from 1 Jan 2025 to 1 Jan 2026 (thirteen payments, "
    "65,000.00), worth 70,000.00 on 1 Jan 2026, 365 days after the first. The rate weights each "
    "payment by how long it has worked, about half a year on average, so it is about double the "
    "absolute return of 7.6923%: 15.6698%.",
    SIP_FLOWS,
    7_000_000,
    20260101,
    **{"absolute_return_pct": "7.6923", "xirr.pct": "15.6698"},
)
case_xirr(
    "XI",
    "Ten thousand times the money in 400 days",
    "1.00 (100 paise) becomes 10,00,000.00 (100000000) in 400 days: r = 1000000 ^ (365 / 400) - "
    "1, a rate in the hundreds of thousands of percent, written in full.",
    [fl(20250101, inn=100)],
    100_000_000,
    plus_days(20250101, 400),
    closed=D(1_000_000) ** (D(365) / 400) - 1,
    **{"xirr.state": "shown"},
)
case_xirr(
    "XJ",
    "Nearly everything lost",
    "10,00,000.00 (100000000) becomes 1.00 (100 paise) in 400 days: r = 1e-6 ^ (365 / 400) - 1, "
    "about -99.9997%, which is a loss and not an error.",
    [fl(20250101, inn=100_000_000)],
    100,
    plus_days(20250101, 400),
    closed=D("1e-6") ** (D(365) / 400) - 1,
    **{"xirr.state": "shown"},
)
case_xirr(
    "XK",
    "Money in, money out at a profit, then a loss of everything: no single rate",
    "10,000.00 in on 1 Jan 2025, 12,000.00 out on 1 Jan 2026, 15,000.00 in on 1 Jan 2027, "
    "nothing held on 1 Jan 2027. With y = 1 / (1 + r) the cash is -10000 + 12000 y - 15000 y^2, "
    "whose discriminant 12000^2 - 4 x 10000 x 15000 is negative: no rate makes it add up to "
    "nothing. The absolute return is (12000 - 25000) / 25000 = -52.0000%.",
    [fl(20250101, inn=1_000_000), fl(20260101, out=1_200_000), fl(20270101, inn=1_500_000)],
    0,
    20270101,
    **{"xirr.state": "none", "xirr.words": NO_RATE, "absolute_return_pct": "-52.0000"},
)
assert roots_walk([(20250101, -1_000_000), (20260101, 1_200_000), (20270101, -1_500_000)]) == []

CHAIN = []
X2_ROWS = [
    e(1, "BUY", 20230403, 1, 1, 500 * SHARE, 5_000_000),
    e(2, "BUY", 20231016, 1, 1, 250 * SHARE, 2_500_000),
    e(3, "DIVIDEND", 20240614, 1, 1, amount=120_000),
    e(4, "SELL", 20250328, 1, 1, 200 * SHARE, 2_000_000),
]
_walked = walk(X2_ROWS, 20261027)
assert _walked["flows"] == X2_FLOWS
CHAIN.append(
    {
        "id": "CH1",
        "title": "X2 from the entries of a ledger",
        "why": "The entries behind X2 (two purchases, a dividend and a sale of 200 of the 750 "
        "shares), replayed, give X2's flows; with a value of 82,000.00 on 27 Oct 2026 the report "
        "is X2's.",
        "entries": X2_ROWS,
        "value_paise": 8_200_000,
        "valuation_day": 20261027,
        "expect": summary_of(_walked["flows"], 8_200_000, 20261027, "CH1"),
    }
)
assert CHAIN[0]["expect"]["xirr"]["pct"] == "11.0863"


def case_rate(cid, title, why, cash, pct, **more):
    got = nearest_rate(cash, cid)
    assert (None if got is None else rate_text(got, cid)) == pct, (cid, got, pct)
    case = {
        "id": cid,
        "title": title,
        "why": why,
        "cash": [list(c) for c in cash],
        "expect": {"pct": pct, "rate": None if got is None else rate_digits(got)},
    }
    case.update(more)
    if "roots" in more:
        check_roots(cash, more["roots"], cid)
    RATES.append(case)


D1, D2, D3 = 20250101, 20260101, 20270101  # 365 and 730 days apart
assert days_apart(D1, D2) == 365 and days_apart(D1, D3) == 730
case_rate(
    "R1",
    "Three payments with an exact rate",
    "-10,000.00 on 1 Jan 2025, -10,000.00 on 1 Jan 2026 and +23,100.00 on 1 Jan 2027, in paise "
    "-1000000, -1000000 and 2310000. With y = 1 / (1 + r) the sum is -1000000 - 1000000 y + "
    "2310000 y^2, and it is nothing at y = (1000000 + sqrt(1000000^2 + 4 x 2310000 x 1000000)) / "
    "(2 x 2310000) = (1000000 + 3200000) / 4620000 = 10 / 11, so 1 + r = 1.1 and r = 10.0000%. "
    "(The quadratic's other root is negative and is no rate.)",
    [(D1, -1_000_000), (D2, -1_000_000), (D3, 2_310_000)],
    "10.0000",
)
case_rate(
    "R2",
    "Two rates fit and one of them is 0%: zero",
    "+10,000.00, -25,000.00, +15,000.00 on three yearly dates: with y = 1 / (1 + r), "
    "1000000 - 2500000 y + 1500000 y^2 = 0 gives y = 1 or y = 2/3, so r = 0% or 50%. The sum of "
    "the cash is 0, so the rate nearest zero is 0 itself: 0.0000.",
    [(D1, 1_000_000), (D2, -2_500_000), (D3, 1_500_000)],
    "0.0000",
    roots=["0", "1/2"],
)
case_rate(
    "R3",
    "Two rates fit, both gains: the smaller",
    "+4,000.00, -13,000.00, +10,000.00 on three yearly dates: 400000 - 1300000 y + 1000000 y^2 "
    "= 0, so y = (13 +- 3) / 20 = 0.8 or 0.5 and r = 25% or 100%. The one nearest zero, 25.0000.",
    [(D1, 400_000), (D2, -1_300_000), (D3, 1_000_000)],
    "25.0000",
    roots=["1/4", "1"],
)
case_rate(
    "R4",
    "Two rates fit, a loss and a gain: the loss is nearer zero",
    "+5,000.00, -14,000.00, +8,000.00 on three yearly dates: 500000 - 1400000 y + 800000 y^2 = "
    "0, so y = (14 +- 6) / 16 = 1.25 or 0.5, r = -20% or +100%. Nearest zero: -20.0000.",
    [(D1, 500_000), (D2, -1_400_000), (D3, 800_000)],
    "-20.0000",
    roots=["-1/5", "1"],
)
case_rate(
    "R5",
    "Nearest zero is measured on the log scale, not in percent",
    "+1,000.00, -2,010.00, +989.00 on three yearly dates (100000, -201000, 98900 paise): "
    "100000 - 201000 y + 98900 y^2 = 0, so y = (2010 +- 290) / 1978, 1 + r = 0.86 or 1.15: "
    "r = -14% or +15%. In percent -14% is nearer zero; on the log scale ln 1.15 = 0.1398 is "
    "nearer than |ln 0.86| = 0.1508, and that is the rule: +15.0000. (Gains and losses are "
    "weighed alike: a rate of +x and its mirror 1 / (1 + x) - 1 are equally far.)",
    [(D1, 100_000), (D2, -201_000), (D3, 98_900)],
    "15.0000",
    roots=["-7/50", "3/20"],
)
case_rate(
    "R6",
    "Cash that changes sign twice but has no rate",
    "-10,000.00, +12,000.00, -15,000.00 on three yearly dates: -1000000 + 1200000 y - 1500000 "
    "y^2 has a negative discriminant, so no yearly rate makes it add up to nothing.",
    [(D1, -1_000_000), (D2, 1_200_000), (D3, -1_500_000)],
    None,
)
case_rate(
    "R7",
    "One payment alone has no rate",
    "A single payment, however large, cannot be compared with anything.",
    [(D1, -1_000_000)],
    None,
)
case_rate(
    "R8",
    "Payments only, or receipts only, have no rate",
    "Cash that is all paid out (or all received) never adds up to nothing at any rate.",
    [(D1, -1_000_000), (D2, -500_000)],
    None,
)
case_rate(
    "R9",
    "One day apart: the yearly rate of a day's gain",
    "-10,000.00 and +10,100.00 one day later: 1% a day, (1.01) ^ 365 - 1 = 3,678.3434% a year.",
    [(20250101, -1_000_000), (20250102, 1_010_000)],
    "3678.3434",
)
case_rate(
    "R10",
    "Cash given out of order, a day twice, and a day that nets to nothing",
    "The payments of R1 given latest first, the first split in two, and a day that pays and "
    "receives the same 777.00: the rate is R1's.",
    [
        (D3, 2_310_000),
        (D2, -1_000_000),
        (D1, -400_000),
        (D1, -600_000),
        (D2, 77_700),
        (D2, -77_700),
    ],
    "10.0000",
)
case_rate(
    "R11",
    "A rate that rounds the other way if it is computed in floats",
    "-10,000.00 and +10,000.01 a year (365 days) later: 0.0001% a year, 1 paisa on 10000.00 is "
    "(1000001 / 1000000) - 1 = 0.0001%.",
    [(D1, -1_000_000), (D2, 1_000_001)],
    "0.0001",
)
case_rate(
    "R12",
    "A loss too small to be a number of its own",
    "-10,000.00 and +9,999.99 a year later: -0.0001%.",
    [(D1, -1_000_000), (D2, 999_999)],
    "-0.0001",
)
case_rate(
    "R13",
    "Two rates equally near zero, found in the same step: the gain",
    "+4,200.00, -8,410.00, +4,200.00 on three yearly dates (420000, -841000, 420000 paise): with "
    "y = 1 / (1 + r) the sum is 420000 - 841000 y + 420000 y^2, and y = (841 +- 41) / 840 = 1.05 "
    "or 20/21, so r = -1/21 = -4.7619% or r = +1/20 = +5.0000%. The cash reads the same "
    "backwards, so the two rates are mirror images, ln(21/20) from zero on either side, and both "
    "lie in the first step the search takes. Equally near goes to the gain: 5.0000.",
    [(D1, 420_000), (D2, -841_000), (D3, 420_000)],
    "5.0000",
    roots=["-1/21", "1/20"],
)


# ---------------------------------------------------------------- the time-weighted return
TWR = []


def case_twr(cid, title, why, points, opening, **hand):
    periods, figure = twr_of(points, opening, cid)
    got = {"periods": periods, "twr": figure}
    agrees(got, hand, cid)
    TWR.append(
        {
            "id": cid,
            "title": title,
            "why": why,
            "opening_paise": opening,
            "points": points,
            "expect": got,
        }
    )


T1_POINTS = [
    pt(20261103, 1_050_000, inn=1_000_000),
    pt(20261104, 1_029_000),
    pt(20261105, 2_079_000, inn=1_000_000),
    pt(20261106, 1_600_000, out=500_000),
]
case_twr(
    "T1",
    "Four days with money in on the first and third and money out on the fourth",
    "Amounts in paise. Day 1: 10,000.00 in, closing value 10,500.00: r = (1050000 - 0 - 1000000) "
    "/ (0 + 1000000) = 5.0000%. Day 2: value 10,290.00, nothing moved: (1029000 - 1050000) / "
    "1050000 = -2.0000%. Day 3: 10,000.00 in, value 20,790.00: (2079000 - 1029000 - 1000000) / "
    "(1029000 + 1000000) = 50000 / 2029000 = 2.4643%. Day 4: 5,000.00 out, value 16,000.00: "
    "(1600000 - 2079000 + 500000) / 2079000 = 21000 / 2079000 = 1.0101%. Money in works from the "
    "start of its day and money out leaves at the end of it. The product is 1.05 x 0.98 x "
    "(2079000 / 2029000) x (2100000 / 2079000) = 1.029 x 2100000 / 2029000 = 1.0650074: 6.5007%.",
    T1_POINTS,
    0,
    **{
        "periods.0.pct": "5.0000",
        "periods.1.pct": "-2.0000",
        "periods.2.pct": "2.4643",
        "periods.3.pct": "1.0101",
        "twr.pct": "6.5007",
        "twr.start_day": 20261103,
        "twr.words": "TWR since 3 Nov 2026",
    },
)
case_twr(
    "T2",
    "A dividend paid out is part of the return",
    "Day 1: 10,000.00 in, value 10,000.00: 0.0000%. Day 2: a dividend of 100.00 paid out, value "
    "still 10,000.00: (1000000 - 1000000 + 10000) / 1000000 = 1.0000%.",
    [pt(20261103, 1_000_000, inn=1_000_000), pt(20261104, 1_000_000, out=10_000)],
    0,
    **{"periods.0.pct": "0.0000", "periods.1.pct": "1.0000", "twr.pct": "1.0000"},
)
case_twr(
    "T3",
    "A fee paid from outside lowers the return",
    "Day 1: 10,000.00 in, value 10,000.00. Day 2: a fee of 50.00 paid from outside, value still "
    "10,000.00: (1000000 - 1000000 - 5000) / 1000000 = -0.5000%.",
    [pt(20261103, 1_000_000, inn=1_000_000), pt(20261104, 1_000_000, fee=5_000)],
    0,
    **{"periods.1.pct": "-0.5000", "twr.pct": "-0.5000"},
)
case_twr(
    "T4",
    "An incomplete day joins the next complete day's period",
    "Day 1 closes at 10,000.00. Day 2 is not complete (a holding has no closing price) and "
    "10,00.00 comes in on it. Day 3 closes at 11,550.00. One period from the close of day 1 to "
    "the close of day 3, the money in counted from its start: (1155000 - 1000000 - 100000) / "
    "(1000000 + 100000) = 55000 / 1100000 = 5.0000%. With no opening value the first complete "
    "day is where the return begins, so day 1's own return (and its money in) is not part of it.",
    [
        pt(20261103, 1_000_000, inn=1_000_000),
        pt(20261104, None, inn=100_000),
        pt(20261105, 1_155_000),
    ],
    None,
    **{
        "periods.0.start": 20261103,
        "periods.0.day": 20261105,
        "periods.0.base": 1_100_000,
        "periods.0.grown": 1_155_000,
        "periods.0.pct": "5.0000",
        "twr.pct": "5.0000",
        "twr.start_day": 20261103,
    },
)
case_twr(
    "T5",
    "A transfer between accounts changes nothing",
    "FL9's tab: 10 shares bought on day 1 for 1,000.00 and moved to another account on day 2. "
    "The transfer is no flow and no change in value, so day 2's return is 0.0000% and the TWR is "
    "day 1's.",
    [pt(20250110, 100_000, inn=100_000), pt(20250305, 100_000)],
    0,
    **{"periods.1.pct": "0.0000", "twr.pct": "0.0000"},
)
case_twr(
    "T6",
    "The first day's own return counts for a tab that was empty before it",
    "Day 1: 10,000.00 in, value 11,000.00; day 2 no change. With an opening value of 0 the first "
    "day's 10.0000% counts: TWR 10.0000% since day 1. With no opening value day 1 is the base "
    "and only day 2 is a period: 0.0000%, measured from the close of day 1.",
    [pt(20261103, 1_100_000, inn=1_000_000), pt(20261104, 1_100_000)],
    0,
    **{"periods.0.pct": "10.0000", "periods.0.start": 20261103, "twr.pct": "10.0000"},
)
case_twr(
    "T6b",
    "... and not for a tab with an unknown value before it",
    "The same two days with no opening value: day 1 is the base, day 2 the only period, "
    "1100000 / 1100000 = 0.0000%, which began at the close of 3 Nov.",
    [pt(20261103, 1_100_000, inn=1_000_000), pt(20261104, 1_100_000)],
    None,
    **{"periods.0.pct": "0.0000", "periods.0.start": 20261103, "twr.pct": "0.0000"},
)
case_twr(
    "T7",
    "A day with nothing at work is skipped",
    "Days 1 and 2 have a closing value of nothing and nothing moves: there is no money at work, "
    "so no return can be worked out and they are skipped. Day 3: 5,000.00 in, value 5,050.00: "
    "1.0000%. Day 4: value 5,000.00: (500000 - 505000) / 505000 = -0.9901%. The first period "
    "began at the close of day 2.",
    [
        pt(20261103, 0),
        pt(20261104, 0),
        pt(20261105, 505_000, inn=500_000),
        pt(20261106, 500_000),
    ],
    0,
    **{
        "periods.0.start": 20261104,
        "periods.0.pct": "1.0000",
        "periods.1.pct": "-0.9901",
        "twr.start_day": 20261104,
    },
)
case_twr(
    "T8",
    "Sold out and bought again",
    "Day 1: 10,000.00 in, value 11,000.00 (10.0000%). Day 2: everything sold for 11,500.00, value "
    "0: (0 - 1100000 + 1150000) / 1100000 = 4.5455%. Day 3: nothing held, nothing at work: "
    "skipped. Day 4: 5,000.00 in, value 5,100.00: 2.0000%. Product 1.1 x (1150000 / 1100000) x "
    "1.02 = 1.17300: 17.3000%.",
    [
        pt(20261103, 1_100_000, inn=1_000_000),
        pt(20261104, 0, out=1_150_000),
        pt(20261105, 0),
        pt(20261106, 510_000, inn=500_000),
    ],
    0,
    **{
        "periods.1.pct": "4.5455",
        "periods.2.pct": "2.0000",
        "twr.pct": "17.3000",
    },
)
case_twr(
    "T9",
    "Several incomplete days, with money in, out and a fee, join one period",
    "Day 1 closes at 10,000.00. Days 2 and 3 are not complete: 1,000.00 in on day 2, and on day 3 "
    "200.00 out and a fee of 10.00. Day 4 closes at 11,500.00. One period: base 1000000 + 100000 "
    "= 1100000; grown 1150000 + 20000 - 1000 = 1169000; 69000 / 1100000 = 6.2727%.",
    [
        pt(20261103, 1_000_000),
        pt(20261104, None, inn=100_000),
        pt(20261105, None, out=20_000, fee=1_000),
        pt(20261106, 1_150_000),
    ],
    None,
    **{"periods.0.base": 1_100_000, "periods.0.grown": 1_169_000, "twr.pct": "6.2727"},
)
case_twr(
    "T10",
    "Days after the last complete day are not part of it",
    "Days 1 and 2 are complete, day 3 is not and has 500.00 in: there is no closing value for it, "
    "so the return ends at day 2. Day 2: (1030000 - 1000000) / 1000000 = 3.0000%.",
    [pt(20261103, 1_000_000), pt(20261104, 1_030_000), pt(20261105, None, inn=50_000)],
    None,
    **{"twr.pct": "3.0000"},
)
case_twr(
    "T11",
    "Days before the first complete day: with an empty tab they join its period",
    "Day 1 is not complete and 10,000.00 is paid in on it. Day 2 closes at 10,100.00. Opening "
    "value 0: one period from day 1, base 1000000, 1.0000%. With no opening value there is "
    "nothing to measure from, and no period at all.",
    [pt(20261103, None, inn=1_000_000), pt(20261104, 1_010_000)],
    0,
    **{"periods.0.start": 20261103, "periods.0.pct": "1.0000", "twr.pct": "1.0000"},
)
case_twr(
    "T11b",
    "... and with no opening value there is no return yet",
    "The same two days, no opening value: day 2 is the first complete day and so only the base. "
    "No period: no figure and the sentence that says why.",
    [pt(20261103, None, inn=1_000_000), pt(20261104, 1_010_000)],
    None,
    **{"twr.pct": None, "twr.start_day": None, "twr.words": WAITING},
)
case_twr(
    "T12",
    "A year and a day: annualised",
    "10,000.00 in on 1 Jan 2025, worth 10,000.00 that day and 11,000.00 on 2 Jan 2026. The first "
    "period began on 1 Jan 2025 and the last ends 366 days later: beyond 365, so the TWR of "
    "10.0000% is also given as a yearly rate, 1.1 ^ (365 / 366) - 1 = 9.9714%.",
    [pt(20250101, 1_000_000, inn=1_000_000), pt(20260102, 1_100_000)],
    0,
    **{"twr.pct": "10.0000", "twr.annualised_pct": "9.9714"},
)
case_twr(
    "T12b",
    "Exactly 365 days is not beyond a year",
    "The same series ending on 1 Jan 2026, 365 days after it began: not beyond 365 days, so no "
    "annualised figure.",
    [pt(20250101, 1_000_000, inn=1_000_000), pt(20260101, 1_100_000)],
    0,
    **{"twr.pct": "10.0000", "twr.annualised_pct": None},
)
case_twr(
    "T13",
    "A holding that goes to nothing loses everything, for good",
    "Day 1: 10,000.00 in, value 10,000.00. Day 2: the value is nothing and nothing was taken out: "
    "(0 - 1000000) / 1000000 = -100.0000%. Day 3: 1,000.00 in, value 1,100.00 is 10.0000%, but "
    "the product stays 0: a time-weighted return cannot come back from losing everything.",
    [
        pt(20261103, 1_000_000, inn=1_000_000),
        pt(20261104, 0),
        pt(20261105, 110_000, inn=100_000),
    ],
    0,
    **{"periods.1.pct": "-100.0000", "periods.2.pct": "10.0000", "twr.pct": "-100.0000"},
)
case_twr(
    "T14",
    "A fee larger than everything at work cannot lose more than everything",
    "Day 1: 100.00 in, value 100.00. Day 2: a fee of 250.00 from outside, value still 100.00: "
    "the formula gives (10000 - 10000 - 25000) / 10000 = -250%, but a period cannot lose more "
    "than all it began with, so it counts as -100.0000%.",
    [pt(20261103, 10_000, inn=10_000), pt(20261104, 10_000, fee=25_000)],
    0,
    **{"periods.1.grown": 0, "periods.1.pct": "-100.0000", "twr.pct": "-100.0000"},
)
case_twr(
    "T15",
    "Days out of order and a day twice",
    "T1's days given latest first, with day 3 split in two points (the money in on one, the "
    "value on the other): the same periods and the same 6.5007%.",
    [
        pt(20261106, 1_600_000, out=500_000),
        pt(20261105, None, inn=1_000_000),
        pt(20261103, 1_050_000, inn=1_000_000),
        pt(20261105, 2_079_000),
        pt(20261104, 1_029_000),
    ],
    0,
    **{"periods.2.pct": "2.4643", "twr.pct": "6.5007"},
)
case_twr(
    "T16",
    "A loss too small to show is 0.0000%, never -0.0000%",
    "1,00,00,000.00 (1000000000 paise) at work and a day's result of one paisa less: "
    "-1 / 1000000000 = -0.0000001%, which rounds to 0.0000.",
    [pt(20261103, 1_000_000_000, inn=1_000_000_000), pt(20261104, 999_999_999)],
    0,
    **{"periods.1.pct": "0.0000", "twr.pct": "0.0000"},
)
case_twr(
    "T17",
    "Nothing to measure",
    "No points, or only days with no closing value: no return, and the sentence that says so.",
    [],
    0,
    **{"twr.pct": None, "twr.start_day": None, "twr.words": WAITING},
)
case_twr(
    "T18",
    "Points far apart: 400 days, annualised",
    "10,000.00 in on 1 Jan 2025 and worth the same that day (0.0000%), 10,500.00 200 days later "
    "(5.0000%) and 11,000.00 200 days after that (4.7619%): a TWR of 10.0000% over 400 days, "
    "beyond a year, so also yearly: 1.1 ^ (365 / 400) - 1 = 9.0865%.",
    [
        pt(20250101, 1_000_000, inn=1_000_000),
        pt(plus_days(20250101, 200), 1_050_000),
        pt(plus_days(20250101, 400), 1_100_000),
    ],
    0,
    **{"twr.pct": "10.0000", "twr.annualised_pct": "9.0865"},
)

# ---------------------------------------------------------------- net money put in
NET = []
N1_FLOWS = [
    fl(20261103, inn=1_000_000),
    fl(20261104, out=10_000, fee=5_000),
    fl(20261105, inn=1_000_000),
    fl(20261106, out=500_000),
]
_n1 = summary_of(N1_FLOWS, 1_600_000, 20261106, "N1")
agrees(
    _n1,
    {
        "money_in_paise": 2_000_000,
        "money_out_paise": 510_000,
        "fee_paise": 5_000,
        "net_put_in_paise": 1_495_000,
        "total_gain_paise": 105_000,
        "absolute_return_pct": "5.2369",
    },
    "N1",
)
NET.append(
    {
        "id": "N1",
        "title": "T1 with a dividend and a fee: the money still put in",
        "why": "T1's flows plus a dividend of 100.00 (10000) paid out and a fee of 50.00 (5000) on "
        "4 Nov. Put in: 2000000 in + 5000 fee - 510000 out (the 500000 sale and the dividend) = "
        "1495000, 14,950.00, and the tab is worth 16,000.00, so the gain is 1,050.00 (105000). "
        "The absolute return is the same gain over what was paid in, 2005000: 105000 / 2005000 = "
        "5.2369%. Net money put in after each day is listed.",
        "flows": N1_FLOWS,
        "value_paise": 1_600_000,
        "valuation_day": 20261106,
        "expect": _n1,
        "running": [
            {"day": 20261103, "net_put_in_paise": 1_000_000},
            {"day": 20261104, "net_put_in_paise": 995_000},
            {"day": 20261105, "net_put_in_paise": 1_995_000},
            {"day": 20261106, "net_put_in_paise": 1_495_000},
        ],
    }
)

# ---------------------------------------------------------------- the year's figures
YEARS = []


def case_year(cid, title, why, rows, vday, by_fy, income, as_of=None, **hand):
    walked = walk(rows, as_of if as_of is not None else vday)
    expect = {"by_fy": fy_figures(walked), "income": income_figures(walked, vday)}
    assert expect["by_fy"] == by_fy, (cid, expect["by_fy"], by_fy)
    assert expect["income"] == income, (cid, expect["income"], income)
    agrees(expect, hand, cid)
    YEARS.append(
        {
            "id": cid,
            "title": title,
            "why": why,
            "entries": rows,
            "as_of": as_of if as_of is not None else vday,
            "valuation_day": vday,
            "expect": expect,
        }
    )


def fyrow(fy, realised=0, income=0, fees=0):
    return {
        "fy": fy,
        "label": f"FY {fy}-{(fy + 1) % 100:02d}",
        "realised_paise": realised,
        "income_paise": income,
        "fees_paise": fees,
    }


def months(vday, paise=None):
    year, month = vday // 10000, vday // 100 % 100
    out = []
    for back in range(11, -1, -1):
        n = year * 12 + month - 1 - back
        key = n // 12 * 100 + n % 12 + 1
        out.append({"month": key, "paise": (paise or {}).get(key, 0)})
    return out


case_year(
    "FY1",
    "31 March and 1 April fall in different financial years",
    "10 shares bought for 1,000.00 (100000). 5 sold on 31 Mar 2025 for 700.00 (70000), cost "
    "500.00: gain 200.00 (20000), FY 2024-25. 5 sold on 1 Apr 2025 for 450.00 (45000): loss "
    "50.00 (-5000), FY 2025-26. Dividends of 10.00 on 31 Mar and 20.00 on 1 Apr 2025; fees of "
    "3.00 and 4.00 on the same two days; interest of 1.50 on 31 Mar 2026 (FY 2025-26) and 2.50 "
    "on 1 Apr 2026 (FY 2026-27). Income is gross, and the sale's charges would be in its gain, "
    "not in the fees. Read on 27 Oct 2026 the last twelve months run from Nov 2025, so the two "
    "interest credits are in March and April 2026 and the 2025 income is in no month.",
    [
        e(1, "BUY", 20250310, 1, 1, 10 * SHARE, 100_000),
        e(2, "SELL", 20250331, 1, 1, 5 * SHARE, 70_000),
        e(3, "SELL", 20250401, 1, 1, 5 * SHARE, 45_000),
        e(4, "DIVIDEND", 20250331, 1, 1, amount=1_000),
        e(5, "DIVIDEND", 20250401, 1, 1, amount=2_000),
        e(6, "FEE", 20250331, 1, None, amount=300),
        e(7, "FEE", 20250401, 1, None, amount=400),
        e(8, "INTEREST", 20260331, 1, None, amount=150),
        e(9, "INTEREST", 20260401, 1, None, amount=250),
    ],
    20261027,
    [
        fyrow(2024, realised=20_000, income=1_000, fees=300),
        fyrow(2025, realised=-5_000, income=2_150, fees=400),
        fyrow(2026, income=250),
    ],
    {"this_fy_paise": 250, "by_month": months(20261027, {202603: 150, 202604: 250})},
)
case_year(
    "FY2",
    "Income this financial year and in each of the last twelve months",
    "Read on 27 Oct 2026, the twelve months are Nov 2025 to Oct 2026. Income of 1,000.00 (31 Oct "
    "2025, a month too early), 2,000.00 (1 Nov 2025, the first month), 3,000.00 (15 Mar 2026, "
    "last financial year), 4,000.00 (1 Apr 2026, the first day of this one), 5,000.00 (30 Sep "
    "2026), 6,000.00 (27 Oct 2026, the day itself) and 7,000.00 (28 Oct 2026, after it). The "
    "ledger is replayed to 28 Oct here, on purpose: the year by year figures add up whatever "
    "was replayed (FY 2026-27: 4000 + 5000 + 6000 + 7000 = 22,000.00), while the income figures "
    "stop at the day asked for. This financial year (from 1 Apr 2026): 4000 + 5000 + 6000 = "
    "15,000.00. Months with none are 0.",
    [
        e(1, "DIVIDEND", 20251031, 1, 1, amount=1_000_00),
        e(2, "DIVIDEND", 20251101, 1, 1, amount=2_000_00),
        e(3, "INTEREST", 20260315, 1, None, amount=3_000_00),
        e(4, "DIVIDEND", 20260401, 1, 1, amount=4_000_00),
        e(5, "DIVIDEND", 20260930, 1, 1, amount=5_000_00),
        e(6, "DIVIDEND", 20261027, 1, 1, amount=6_000_00),
        e(7, "DIVIDEND", 20261028, 1, 1, amount=7_000_00),
    ],
    20261027,
    [
        fyrow(2025, income=600_000),
        fyrow(2026, income=2_200_000),
    ],
    {
        "this_fy_paise": 1_500_000,
        "by_month": months(
            20261027,
            {202511: 200_000, 202603: 300_000, 202604: 400_000, 202609: 500_000, 202610: 600_000},
        ),
    },
    as_of=20261028,
)
case_year(
    "FY3",
    "A financial year with nothing in it has no row",
    "A dividend in FY 2022-23 and another in FY 2024-25: FY 2023-24 has nothing, and a sale at "
    "exactly its cost (no gain, no loss) in FY 2025-26 has nothing either.",
    [
        e(1, "BUY", 20220401, 1, 1, 10 * SHARE, 100_000),
        e(2, "DIVIDEND", 20230115, 1, 1, amount=1_000),
        e(3, "DIVIDEND", 20250215, 1, 1, amount=2_000),
        e(4, "SELL", 20250601, 1, 1, 10 * SHARE, 100_000),
    ],
    20261027,
    [fyrow(2022, income=1_000), fyrow(2024, income=2_000)],
    {"this_fy_paise": 0, "by_month": months(20261027)},
)
case_year(
    "FY4",
    "Charges are in a sale's gain, TDS is not taken off, a loss is a loss",
    "10 shares for 1,000.00 (100000) with 20.00 of charges (cost 102000). Sold for 900.00 "
    "(90000) with 15.00 of charges: 90000 - 1500 - 102000 = -13500, a realised loss of 135.00 "
    "in FY 2025-26. A dividend of 50.00 with 5.00 of TDS is income of 5000, gross.",
    [
        e(1, "BUY", 20250110, 1, 1, 10 * SHARE, 100_000, charges_paise=2_000),
        e(2, "SELL", 20250910, 1, 1, 10 * SHARE, 90_000, charges_paise=1_500),
        e(3, "DIVIDEND", 20250915, 1, 1, amount=5_000, tds_paise=500),
    ],
    20261027,
    [fyrow(2025, realised=-13_500, income=5_000)],
    {"this_fy_paise": 0, "by_month": months(20261027)},
)
case_year(
    "FY5",
    "Transfers, splits and bonus shares have no gain, income or fee",
    "Shares moved between accounts, taken out at a value, split and given as bonus: none of it "
    "is a gain, income or a fee, so there is no row at all.",
    [
        e(1, "BUY", 20250110, 1, 1, 10 * SHARE, 100_000),
        e(2, "TRANSFER", 20250205, 1, 1, 4 * SHARE, to_account_id=2),
        e(3, "TRANSFER", 20250206, 2, 1, 2 * SHARE, value_paise=30_000),
        e(4, "SPLIT", 20250301, None, 1, ratio_num=2, ratio_den=1),
        e(5, "BONUS", 20250401, 1, 1, 3 * SHARE),
    ],
    20261027,
    [],
    {"this_fy_paise": 0, "by_month": months(20261027)},
)

case_year(
    "FY6",
    "Read in January: the twelve months and the financial year do not line up",
    "Read on 31 Jan 2026 the twelve months are Feb 2025 to Jan 2026, and the financial year is the "
    "one that began on 1 Apr 2025. Income of 1,000.00 (31 Jan 2025, a month too early), 2,000.00 "
    "(1 Feb 2025, the first month, and last financial year), 3,000.00 (31 Mar 2025, the last day "
    "of last financial year), 4,000.00 (1 Apr 2025, the first day of this one) and 5,000.00 (31 Jan "
    "2026, the day itself). This financial year: 4000 + 5000 = 9,000.00; last year's three are "
    "6,000.00 in the year by year figures.",
    [
        e(1, "DIVIDEND", 20250131, 1, 1, amount=100_000),
        e(2, "DIVIDEND", 20250201, 1, 1, amount=200_000),
        e(3, "INTEREST", 20250331, 1, None, amount=300_000),
        e(4, "DIVIDEND", 20250401, 1, 1, amount=400_000),
        e(5, "DIVIDEND", 20260131, 1, 1, amount=500_000),
    ],
    20260131,
    [fyrow(2024, income=600_000), fyrow(2025, income=900_000)],
    {
        "this_fy_paise": 900_000,
        "by_month": months(
            20260131, {202502: 200_000, 202503: 300_000, 202504: 400_000, 202601: 500_000}
        ),
    },
)

case_year(
    "FY7",
    "The label writes the next year in two digits, to the end of the century",
    "Income on 15 Jun 2008 is in FY 2008-09 (not 2008-9) and on 15 Jun 2099 in FY 2099-00: the "
    "second part of the label is the next year's last two digits, with its zero.",
    [
        e(1, "DIVIDEND", 20080615, 1, 1, amount=1_000),
        e(2, "DIVIDEND", 20990615, 1, 1, amount=2_000),
    ],
    20991231,
    [fyrow(2008, income=1_000), fyrow(2099, income=2_000)],
    {"this_fy_paise": 2_000, "by_month": months(20991231, {209906: 2_000})},
    **{"by_fy.0.label": "FY 2008-09", "by_fy.1.label": "FY 2099-00"},
)

PERFORMANCE = {
    "about": (
        "Golden vectors for core/performance.py (PA4): the money flows of a replayed tab, the "
        "XIRR and its three states, the rate of dated cash on its own, the time-weighted return, "
        "net money put in and the figures by financial year. Rates are found by another route "
        "than the code's (see make_pa4.py) and every number is worked out by hand in the case's "
        "why. Python only: the phone is sent the figures."
    ),
    "flows": FLOWS,
    "xirr": XIRR,
    "chain": CHAIN,
    "rates": RATES,
    "twr": TWR,
    "net": NET,
    "years": YEARS,
}


def build() -> dict:
    """The file name and contents of every vector file this generator writes."""
    return {"performance.json": PERFORMANCE}


def main() -> None:
    write_all(build(), Path(__file__).parent)


if __name__ == "__main__":
    main()
