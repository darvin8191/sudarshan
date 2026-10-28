"""Generator of the PA5 golden vectors: history.json.

The prices a day may use (a listed close of that very day, a fund's NAV no more than three business
days old, the newest value kept by hand), the daily series of a tab (its value on each complete day
with the net money put in, and the time-weighted return read from it, including the design's T4
from the entries up), and the same money put into an index (the design's B1 and B2 and the edges
around them). Like the other generators it imports nothing from sudarshan and works the other way
round from the code: the code replays the ledger once and walks the days with running counts; this
replays the entries again for every single day, looks every price up by reading the list from the
back, adds the money of each day up from scratch, keeps the index units as exact fractions and
finds a rate by walking out from zero in floats. Every case carries its hand derivation, and the
generator refuses to write a file in which what was typed by hand and what was computed disagree.

Run: python3 make_pa5.py
"""

from fractions import Fraction
from pathlib import Path

from portfolio_vector_tools import (
    U,
    business_days,
    day_text,
    days_apart,
    e,
    pct4,
    pct_text,
    plus_days,
    rd,
    weekday,
    write_all,
)

YEAR = 365
E = 2.718281828459045  # the base of natural logarithms, to a float
STEP = 0.01  # how far apart, in the log of the rate, the walk looks
REACH = 23.0
LABELS = {"nifty50": "NIFTY 50", "nifty500": "NIFTY 500"}
NAV_DAYS = 3
DIVIDENDS = "Dividends not included"
WAITING = "Daily history starts once every holding here has a closing price."
TWR_NEEDS = "TWR needs closing prices for at least two days."
COULD_NOT_PAY = "The index could not have paid every withdrawal; the comparison is approximate."


def series_from(day):
    return (
        f"Daily history from {day_text(day)}, the first day every holding here had a closing price."
    )


def index_starts(name, day):
    return f"{LABELS[name]} prices start {day_text(day)}; this comparison needs every entry after that."


def index_missing(name):
    return f"{LABELS[name]} prices are not available yet; the comparison appears when they are."


# ---------------------------------------------------------------- the prices a day may use
def price_for(kind, prices, day, holidays=()):
    """The price of ``day``: the newest not after it (the later of two for one day), looked for
    from the back of the list one price at a time; None when this kind of asset has none."""
    best = None
    for priced, price in prices:
        if price > 0 and priced <= day and (best is None or priced >= best[0]):
            best = (priced, price)
    if best is None:
        return None
    if kind == "listed":
        return best[1] if best[0] == day else None
    if kind == "mf":
        return best[1] if business_days(best[0], day, holidays) <= NAV_DAYS else None
    return best[1]


# ---------------------------------------------------------------- a small ledger, for the units
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


def lines_at(rows, day):
    """The units each (account, asset) holds at the end of ``day``: the entries up to it replayed
    in the day's order, only their quantities."""
    live = [r for r in rows if r["trade_date"] <= day and r.get("status", "posted") == "posted"]
    held = {}

    def add(account, asset, qty):
        held[(account, asset)] = held.get((account, asset), 0) + qty

    for r in sorted(live, key=lambda r: (r["trade_date"], rank(r), r["seq"], r["id"])):
        kind, asset = r["kind"], r.get("asset_id")
        if kind in ("BUY", "RIGHTS", "BONUS"):
            add(r["account_id"], asset, r["qty_u"])
        elif kind == "SELL":
            add(r["account_id"], asset, -r["qty_u"])
        elif kind == "SPLIT":
            for (account, other), qty in list(held.items()):
                if other == asset and qty and r.get("account_id") in (None, account):
                    assert qty * r["ratio_num"] % r["ratio_den"] == 0, r
                    held[(account, other)] = qty * r["ratio_num"] // r["ratio_den"]
        elif kind == "TRANSFER":
            if r.get("account_id") is not None:
                add(r["account_id"], asset, -r["qty_u"])
            if r.get("to_account_id") is not None:
                add(r["to_account_id"], asset, r["qty_u"])
    return {key: qty for key, qty in held.items() if qty}


def money_by_day(rows):
    """``{day: [in, out, fee]}``; a transfer out to outside always says its value in these cases."""
    days = {}
    for r in rows:
        if r.get("status", "posted") != "posted":
            continue
        kind, inn, out, fee = r["kind"], 0, 0, 0
        if kind in ("BUY", "RIGHTS"):
            inn = amount_of(r) + r.get("charges_paise", 0)
        elif kind == "SELL":
            out = amount_of(r) - r.get("charges_paise", 0)
        elif kind in ("DIVIDEND", "INTEREST"):
            out = r["amount_paise"]
        elif kind == "FEE":
            fee = r["amount_paise"]
        elif kind == "TRANSFER" and r.get("account_id") is None:
            inn = r["amount_paise"] if r.get("value_paise") is None else r["value_paise"]
        elif kind == "TRANSFER" and r.get("to_account_id") is None:
            out = r["value_paise"]
        if inn or out or fee:
            total = days.setdefault(r["trade_date"], [0, 0, 0])
            total[0] += inn
            total[1] += out
            total[2] += fee
    return days


# ---------------------------------------------------------------- the daily series, day by day
def pt(day, value, inn=0, out=0, fee=0):
    return {
        "day": day,
        "value_paise": value,
        "in_paise": inn,
        "out_paise": out,
        "fee_paise": fee,
    }


def history_of(rows, quotes, last_day, holidays=()):
    """Every day from the first with money to ``last_day``: its lines, its prices, its value."""
    money = {day: m for day, m in money_by_day(rows).items() if day <= last_day}
    if not money:
        return {"series": [], "points": [], "opening": None, "words": None}
    first = min(money)
    series, points = [], []
    net = 0
    for step in range(days_apart(first, last_day) + 1):
        day = plus_days(first, step)
        m = money.get(day, [0, 0, 0])
        net += m[0] + m[2] - m[1]
        value = 0
        for (_account, asset), qty in lines_at(rows, day).items():
            if asset not in quotes:
                value = None
                break
            kind, prices = quotes[asset]
            price = price_for(kind, prices, day, holidays)
            if price is None:
                value = None
                break
            value += rd(qty * price, 10**8)
        if value is not None:
            series.append({"day": day, "value_paise": value, "net_put_in_paise": net})
        if day in money:
            points.append(pt(day, value, *m))
        elif value is not None:
            points.append(pt(day, value))
    if not series:
        said = WAITING
    else:
        said = series_from(series[0]["day"]) if series[0]["day"] > first else None
    opening = 0 if series and series[0]["day"] == first else None
    return {"series": series, "points": points, "opening": opening, "words": said}


def twr_of(points, opening):
    """The time-weighted return of a daily series, in exact fractions, from the complete days;
    with the days it runs from and to, or None for no period."""
    complete = [i for i, p in enumerate(points) if p["value_paise"] is not None]
    periods = []
    previous, value, base_day = -1, opening, None
    for i in complete:
        window = points[previous + 1 : i + 1]
        inn = sum(p["in_paise"] for p in window)
        out = sum(p["out_paise"] for p in window)
        fee = sum(p["fee_paise"] for p in window)
        if value is not None and value + inn > 0:
            grown = max(0, points[i]["value_paise"] + out - fee)
            start = window[0]["day"] if base_day is None else base_day
            periods.append((start, points[i]["day"], value + inn, grown))
        previous, value, base_day = i, points[i]["value_paise"], points[i]["day"]
    if not periods:
        return {"start_day": None, "pct": None, "annualised_pct": None, "words": TWR_NEEDS}, None
    growth = Fraction(1)
    for _, _, base, grown in periods:
        growth *= Fraction(grown, base)
    assert days_apart(periods[0][0], periods[-1][1]) <= YEAR, "a longer series needs a power"
    figure = {
        "start_day": periods[0][0],
        "pct": pct4(growth.numerator - growth.denominator, growth.denominator),
        "annualised_pct": None,
        "words": f"TWR since {day_text(periods[0][0])}",
    }
    return figure, (periods[0][0], periods[-1][1])


# ---------------------------------------------------------------- a rate, found by walking
def merge_cash(cash):
    by_day = {}
    for day, paise in cash:
        by_day[day] = by_day.get(day, 0) + paise
    return sorted((d, c) for d, c in by_day.items() if c)


def npv_float(x, items):
    return sum(c * E ** (-x * d / YEAR) for d, c in items)


def nearest_rate(cash):
    """The yearly rate nearest zero on the log scale (a tie to the gain), walking out from zero in
    floats, halving the first sign change on each side; None when there is no sign change."""
    cash = merge_cash(cash)
    if not (any(c < 0 for _, c in cash) and any(c > 0 for _, c in cash)):
        return None
    items = [(days_apart(cash[0][0], day), paise) for day, paise in cash]
    roots = []
    for side in (1, -1):
        before = npv_float(0.0, items)
        for k in range(1, round(REACH / STEP) + 1):
            x = side * k * STEP
            now = npv_float(x, items)
            if now == 0 or (now > 0) != (before > 0):
                low, high = sorted((side * (k - 1) * STEP, x))
                f_low = npv_float(low, items)
                for _ in range(200):
                    mid = (low + high) / 2
                    if (npv_float(mid, items) > 0) == (f_low > 0):
                        low = mid
                    else:
                        high = mid
                roots.append((low + high) / 2)
                break
            before = now
    if not roots:
        return None
    near = min(abs(x) for x in roots)
    x = next(x for x in roots if abs(x) <= near * (1 + 1e-12))
    return E**x - 1


def rate_text(rate):
    """A rate as a percentage of four decimals; refuses a percentage near a rounding tie."""
    scaled = rate * 100 * 10_000
    assert abs(abs(scaled) % 1 - 0.5) > 1e-4, scaled
    count = int(abs(scaled) + 0.5)
    return pct_text(count if scaled >= 0 else -count)


# ---------------------------------------------------------------- the index, the same money
def benchmark_of(name, flows, closes, vday, now_u=None, span=None):
    """The same money put into the index: units as fractions, a close on the day or the last
    before it, the rate found by walking, the index's own return from the start to the end."""
    out = {
        "benchmark": name,
        "label": LABELS[name],
        "value_paise": None,
        "xirr_pct": None,
        "twr_pct": None,
        "flagged": False,
        "words": [DIVIDENDS],
    }
    if not any(c > 0 for _, c in closes):
        out["flagged"] = True
        out["words"].append(index_missing(name))
        return out

    def close_on(day):
        """The newest close not after ``day``; of two for one day, the one later in the list."""
        best = None
        for d, c in closes:
            if c > 0 and d <= day and (best is None or d >= best[0]):
                best = (d, c)
        return None if best is None else best[1]

    live = sorted((f for f in flows if f["day"] <= vday), key=lambda f: f["day"])
    units, short, early = Fraction(0), False, False
    for f in live:
        net = f["in_paise"] + f["fee_paise"] - f["out_paise"]
        if not net:
            continue
        close = close_on(f["day"])
        if close is None:
            early = True
            break
        units += Fraction(net, close)
        short = short or units < 0
    level = close_on(vday) if now_u is None or now_u <= 0 else now_u
    if live and not early:
        value = rd(units.numerator * level, units.denominator)
        out["value_paise"] = value
        cash = [(f["day"], f["out_paise"] - f["in_paise"] - f["fee_paise"]) for f in live]
        cash.append((vday, value))
        merged = merge_cash(cash)
        if merged and days_apart(merged[0][0], vday) >= YEAR:
            rate = nearest_rate(cash)
            out["xirr_pct"] = None if rate is None else rate_text(rate)
    if span is not None:
        start, end = close_on(span[0]), close_on(span[1])
        out["twr_pct"] = pct4(end - start, start)
    if early:
        out["flagged"] = True
        out["words"].append(index_starts(name, min(d for d, c in closes if c > 0)))
    elif short:
        out["flagged"] = True
        out["words"].append(COULD_NOT_PAY)
    return out


def agrees(got, hand, where):
    """Every number typed by hand in a case is the evaluator's number too, or nothing is written."""
    for path, want in hand.items():
        node = got
        for key in path.split("."):
            node = node[int(key)] if isinstance(node, list) else node[key]
        assert node == want, (where, path, node, want)


# ================================================================ the prices a day may use
QUOTES = []


def case_quotes(cid, title, why, kind, prices, asks, holidays=()):
    """``asks`` are ``(day, price_u or None)`` typed by hand; the evaluator must say the same."""
    for day, hand in asks:
        got = price_for(kind, prices, day, holidays)
        assert got == hand, (cid, day, got, hand)
    case = {
        "id": cid,
        "title": title,
        "why": why,
        "kind": kind,
        "prices": [list(p) for p in prices],
        "asks": [list(a) for a in asks],
    }
    if holidays:
        case["holidays"] = list(holidays)
    QUOTES.append(case)


# 5 Jan 2026 is a Monday.
MON, TUE, WED, THU, FRI, SAT, SUN, MON2 = range(20260105, 20260113)
assert [weekday(d) for d in (MON, TUE, WED, THU, FRI, SAT, SUN, MON2)] == [0, 1, 2, 3, 4, 5, 6, 0]

case_quotes(
    "Q1",
    "A listed asset is valued at that day's own close and no other",
    "Closing prices of 100.00 and 105.00 (1000000 and 1050000 in price_u) on Mon 5 Jan 2026 and "
    "Wed 7 Jan. Monday and Wednesday have their prices. Tuesday and Thursday have none: a close of another day is "
    "not that day's. Sunday 4 Jan is before the first price.",
    "listed",
    [(MON, 1_000_000), (WED, 1_050_000)],
    [(20260104, None), (MON, 1_000_000), (TUE, None), (WED, 1_050_000), (THU, None)],
)
case_quotes(
    "Q2",
    "A fund's NAV serves for three business days",
    "One NAV of 45.0000 (450000 in price_u) on Mon 5 Jan. Business days after Monday: Tuesday 1, "
    "Wednesday 2, Thursday 3, Friday 4, Saturday and Sunday still 4, Monday 12 Jan 5. A NAV no "
    "more than 3 business days old is used: through Thursday.",
    "mf",
    [(MON, 450_000)],
    [
        (MON, 450_000),
        (TUE, 450_000),
        (WED, 450_000),
        (THU, 450_000),
        (FRI, None),
        (SAT, None),
        (MON2, None),
    ],
)
case_quotes(
    "Q3",
    "A holiday does not age a NAV",
    "The same NAV with Wed 7 Jan a holiday of the exchange: Tue 1, Thu 2, Fri 3 business days "
    "after Monday, so Friday is still good, and so are Saturday and Sunday (3). Monday 12 Jan is "
    "the 4th and is not.",
    "mf",
    [(MON, 450_000)],
    [(THU, 450_000), (FRI, 450_000), (SAT, 450_000), (SUN, 450_000), (MON2, None)],
    holidays=[WED],
)
case_quotes(
    "Q4",
    "A value kept by hand serves until the next one, however old",
    "A value of 500.00 on Sat 10 Jan and 520.00 on Thu 15 Jan 2026 (5000000 and 5200000 in "
    "price_u). Before the first there is none; from it each serves until the next is entered, "
    "and the newest serves for ever (it is the freshness notes that say how old it is).",
    "manual",
    [(20260110, 5_000_000), (20260115, 5_200_000)],
    [
        (20260109, None),
        (20260110, 5_000_000),
        (20260114, 5_000_000),
        (20260115, 5_200_000),
        (20270115, 5_200_000),
    ],
)
case_quotes(
    "Q5",
    "Two prices for one day: the later entered",
    "A price corrected on the same day: the list gives 100.00 and then 101.00 for Mon 5 Jan, "
    "and the second is the one used. The list may come in any order.",
    "listed",
    [(TUE, 1_020_000), (MON, 1_000_000), (MON, 1_010_000)],
    [(MON, 1_010_000), (TUE, 1_020_000)],
)
case_quotes(
    "Q6",
    "A price of nothing is a gap in the data, not a price",
    "A listed close of 0 on Tue 6 Jan 2026 between real ones of 100.00 and 102.00 on Monday and "
    "Wednesday: Tuesday has no close (a holding would otherwise show as lost that day).",
    "listed",
    [(MON, 1_000_000), (TUE, 0), (WED, 1_020_000)],
    [(MON, 1_000_000), (TUE, None), (WED, 1_020_000)],
)
case_quotes(
    "Q7",
    "A fund's NAV of nothing leaves the last real one to serve",
    "A NAV of 45.0000 on Mon 5 Jan 2026 and 0 on Tue 6 Jan: Tuesday takes Monday's, one business "
    "day old; Friday is still the 4th business day after Monday, so too old.",
    "mf",
    [(MON, 450_000), (TUE, 0)],
    [(MON, 450_000), (TUE, 450_000), (THU, 450_000), (FRI, None)],
)
case_quotes(
    "Q8",
    "A value kept by hand at nothing leaves the last real one to serve",
    "500.00 on Sat 10 Jan 2026 and 0 on Wed 14 Jan: Wednesday and every day after take the 500.00.",
    "manual",
    [(20260110, 5_000_000), (20260114, 0)],
    [(20260110, 5_000_000), (20260114, 5_000_000), (20270101, 5_000_000)],
)
case_quotes(
    "Q9",
    "A negative price is a gap in the data too",
    "A listed close of -102.00 (a bad row) on Tue 6 Jan 2026 between real ones of 100.00 on Monday "
    "and 102.00 on Wednesday: it is not a price, and Tuesday has no close.",
    "listed",
    [(MON, 1_000_000), (TUE, -1_020_000), (WED, 1_020_000)],
    [(MON, 1_000_000), (TUE, None), (WED, 1_020_000)],
)
case_quotes(
    "Q10",
    "A fund's negative NAV leaves the last real one to serve",
    "A NAV of 45.0000 on Mon 5 Jan 2026 and -45.0000 on Tue 6 Jan: Tuesday takes Monday's, one "
    "business day old, and so do Wednesday and Thursday (2 and 3); Friday is the 4th and is too old.",
    "mf",
    [(MON, 450_000), (TUE, -450_000)],
    [(MON, 450_000), (TUE, 450_000), (WED, 450_000), (THU, 450_000), (FRI, None)],
)
case_quotes(
    "Q11",
    "The holidays may be given in any order",
    "One NAV of 45.0000 on Mon 5 Jan 2026 and three holidays of the exchange, given out of order: "
    "Tue 6 Jan, Mon 26 Jan and Wed 7 Jan. Monday 12 Jan is the 5th business day after the NAV "
    "(Tuesday, Wednesday, Thursday, Friday, Monday) less the two holidays among them (Tuesday "
    "and Wednesday): the 3rd, so the NAV is still good. The holiday of 26 Jan is later than the "
    "day and counts for nothing. Tue 13 Jan is the 6th less two: the 4th, too old.",
    "mf",
    [(MON, 450_000)],
    [(FRI, 450_000), (MON2, 450_000), (20260113, None)],
    holidays=[TUE, 20260126, WED],
)


# ================================================================ the daily series
SERIES = []


def quotes_json(quotes):
    return [
        {"asset_id": asset, "kind": kind, "prices": [list(p) for p in prices]}
        for asset, (kind, prices) in sorted(quotes.items())
    ]


def case_series(cid, title, why, rows, quotes, last_day, holidays=(), decimals=None, **hand):
    got = history_of(rows, quotes, last_day, holidays)
    figure, span = twr_of(got["points"], got["opening"])
    expect = {
        "series": got["series"],
        "points": got["points"],
        "opening_paise": got["opening"],
        "words": got["words"],
        "twr": figure,
        "span": None if span is None else list(span),
    }
    agrees(expect, hand, cid)
    case = {"id": cid, "title": title, "why": why, "entries": rows}
    case["quotes"] = quotes_json(quotes)
    case["last_day"] = last_day
    if holidays:
        case["holidays"] = list(holidays)
    if decimals:
        case["decimals"] = {str(k): v for k, v in decimals.items()}
    case["expect"] = expect
    SERIES.append(case)
    return expect


LISTED, MF, MANUAL = "listed", "mf", "manual"
FUND = {2: 4}  # asset 2 counts its units to four decimals

case_series(
    "S1",
    "The design's T4 from the entries up: a day without a price joins the next",
    "Mon 5 Jan 2026: 100 shares for 10,000.00. Tue 6 Jan: 10 more for 1,000.00, but no closing "
    "price that day. Wed 7 Jan: closing price 105.00. Complete days: Monday (100 x 100.00 = "
    "10,000.00) and Wednesday (110 x 105.00 = 11,550.00); Tuesday has no value, so its 1,000.00 "
    "joins Wednesday's period. Net money put in: 10,000.00, then 11,000.00. Time-weighted: the "
    "tab began empty on a complete day, so Monday's own return counts (10,000.00 in at the start, "
    "10,000.00 at the close: 0%), and Wednesday's period is 11,550.00 over 10,000.00 + 1,000.00 = "
    "1.05: 5.0000%. The time-weighted return runs from Monday to Wednesday.",
    [e(1, "BUY", MON, 1, 1, 100 * U, 1_000_000), e(2, "BUY", TUE, 1, 1, 10 * U, 100_000)],
    {1: (LISTED, [(MON, 1_000_000), (WED, 1_050_000)])},
    WED,
    **{
        "series.0.day": MON,
        "series.0.value_paise": 1_000_000,
        "series.0.net_put_in_paise": 1_000_000,
        "series.1.day": WED,
        "series.1.value_paise": 1_155_000,
        "series.1.net_put_in_paise": 1_100_000,
        "points.1.day": TUE,
        "points.1.value_paise": None,
        "points.1.in_paise": 100_000,
        "opening_paise": 0,
        "words": None,
        "twr.start_day": MON,
        "twr.pct": "5.0000",
        "twr.words": "TWR since 5 Jan 2026",
        "span": [MON, WED],
    },
)
case_series(
    "S2",
    "A tab that began on a complete day counts its first day's own return",
    "10 shares for 1,000.00 on Mon 5 Jan 2026, closing prices 102.00, 104.04 and 104.04 on "
    "Monday, Tuesday and Wednesday. Values 1,020.00, 1,040.40, 1,040.40; net money put in 1,000.00 "
    "throughout. The tab was empty before Monday, so Monday's return counts, from the 1,000.00 "
    "paid in: 1.02; Tuesday 1,040.40 / 1,020.00 = 1.02; Wednesday 1. Product 1.0404: 4.0400%. It "
    "runs from Monday to Wednesday.",
    [e(1, "BUY", MON, 1, 1, 10 * U, 100_000)],
    {1: (LISTED, [(MON, 1_020_000), (TUE, 1_040_400), (WED, 1_040_400)])},
    WED,
    **{
        "series.0.value_paise": 102_000,
        "series.1.value_paise": 104_040,
        "series.2.value_paise": 104_040,
        "series.2.net_put_in_paise": 100_000,
        "opening_paise": 0,
        "twr.pct": "4.0400",
        "twr.start_day": MON,
        "span": [MON, WED],
    },
)
case_series(
    "S3",
    "A back-dated entry: the series begins when the prices do, and says so",
    "10 shares bought for 1,000.00 on Mon 1 Dec 2025, but the first closing prices are those of "
    "Mon 5 Jan 2026 (110.00), then 111.10 and 111.10. Every day before 5 Jan holds a share with "
    "no price, so the series begins on 5 Jan: values 1,100.00, 1,111.00, 1,111.00 against net "
    "money put in of 1,000.00. The words say where it begins. The tab was not empty before 5 "
    "Jan, so the time-weighted return begins at 5 Jan's close and nothing earlier is in it: "
    "1,111.00 / 1,100.00 = 1.01, then 1: 1.0000%. It runs from Monday 5 Jan to Wednesday 7 Jan.",
    [e(1, "BUY", 20251201, 1, 1, 10 * U, 100_000)],
    {1: (LISTED, [(MON, 1_100_000), (TUE, 1_111_000), (WED, 1_111_000)])},
    WED,
    **{
        "series.0.day": MON,
        "series.0.value_paise": 110_000,
        "series.0.net_put_in_paise": 100_000,
        "series.2.value_paise": 111_100,
        "words": "Daily history from 5 Jan 2026, the first day every holding here had a closing price.",
        "opening_paise": None,
        "twr.start_day": MON,
        "twr.pct": "1.0000",
        "points.0.day": 20251201,
        "points.0.value_paise": None,
        "span": [MON, WED],
    },
)
case_series(
    "S4",
    "A fund is complete every day, weekends too",
    "100.1234 units bought for 4,500.00 on Mon 5 Jan 2026. NAVs: 45.0000 on Mon 5 Jan, 46.0000 on "
    "Thu 8 Jan. Every day from Monday 5 to Monday 12 Jan has a NAV no more than 3 business days "
    "old (Saturday and Sunday use Thursday's, Monday 12th is 2 business days after it). Value "
    "= units x NAV: 100.1234 x 45 = 4,505.553, which is 450,555 paise; 100.1234 x 46 = 4,605.6764, "
    "which is 460,568 paise. Eight days. Time-weighted: 450,555 / 450,000 on the first day, 1 "
    "until Thursday's 460,568 / 450,555, then 1: 460,568 / 450,000 = 1.0234844: 2.3484%.",
    [e(1, "BUY", MON, 1, 2, 100_123_400, 450_000)],
    {2: (MF, [(MON, 450_000), (THU, 460_000)])},
    MON2,
    decimals=FUND,
    **{
        "series.0.value_paise": 450_555,
        "series.2.value_paise": 450_555,
        "series.3.day": THU,
        "series.3.value_paise": 460_568,
        "series.5.day": SAT,
        "series.7.day": MON2,
        "series.7.value_paise": 460_568,
        "twr.pct": "2.3484",
        "opening_paise": 0,
    },
)
case_series(
    "S5",
    "A NAV that is too old leaves the day out",
    "The same units with one NAV, 45.0000 of Mon 5 Jan. Tuesday, Wednesday and Thursday are 1, 2 "
    "and 3 business days after it and are complete; Friday is the 4th, so Friday, the weekend and "
    "Monday 12th are not. Four points, all 450,555 paise. Time-weighted: only Monday's own return, "
    "450,555 / 450,000 = 0.1233%.",
    [e(1, "BUY", MON, 1, 2, 100_123_400, 450_000)],
    {2: (MF, [(MON, 450_000)])},
    MON2,
    decimals=FUND,
    **{
        "series.3.day": THU,
        "twr.pct": "0.1233",
        "words": None,
    },
)
case_series(
    "S6",
    "A holiday keeps a NAV good for one more day",
    "As S5, with Wed 7 Jan a holiday of the exchange: Friday 9th is only the 3rd business day "
    "after the NAV (Tue, Thu, Fri), so it is complete, and so are Saturday and Sunday. Monday 12th "
    "is the 4th and is not. Seven points.",
    [e(1, "BUY", MON, 1, 2, 100_123_400, 450_000)],
    {2: (MF, [(MON, 450_000)])},
    MON2,
    holidays=(WED,),
    decimals=FUND,
    **{
        "series.6.day": SUN,
        "twr.pct": "0.1233",
    },
)
case_series(
    "S7",
    "A value kept by hand rides on from the day it was entered",
    "2 units of something kept by hand bought for 1,000.00 on Sat 10 Jan 2026, valued at 500.00 "
    "that day and at 520.00 from Thu 15 Jan. Every day from Saturday 10th to Friday 16th is "
    "complete: 2 x 500.00 = 1,000.00 (100,000 paise) until Wednesday 14th, 2 x 520.00 = 1,040.00 "
    "(104,000 paise) on Thursday and Friday. Time-weighted since Saturday: 1.04, 4.0000%.",
    [e(1, "BUY", SAT, 1, 3, 2 * U, 100_000)],
    {3: (MANUAL, [(SAT, 5_000_000), (20260115, 5_200_000)])},
    20260116,
    **{
        "series.0.value_paise": 100_000,
        "series.4.value_paise": 100_000,
        "series.5.day": 20260115,
        "series.5.value_paise": 104_000,
        "series.6.value_paise": 104_000,
        "twr.pct": "4.0000",
        "twr.start_day": SAT,
        "opening_paise": 0,
    },
)
case_series(
    "S8",
    "Once everything is sold the tab is worth nothing, and needs no price to say so",
    "10 shares for 1,000.00 on Mon 5 Jan 2026, all sold for 1,100.00 on Wed 7 Jan. Closing prices "
    "exist only for Monday (100.00) and Tuesday (105.00). Monday 1,000.00, Tuesday 1,050.00. On "
    "Wednesday and Thursday nothing is held, so both days are complete without a price, worth "
    "nothing, with net money put in of 1,000.00 - 1,100.00 = -100.00. Time-weighted: Monday "
    "1,000 / 1,000, Tuesday 1,050 / 1,000, Wednesday (0 + 1,100 out) / 1,050; Thursday has nothing "
    "at work and is skipped. 1.05 x 1,100 / 1,050 = 1.10: 10.0000%.",
    [e(1, "BUY", MON, 1, 1, 10 * U, 100_000), e(2, "SELL", WED, 1, 1, 10 * U, 110_000)],
    {1: (LISTED, [(MON, 1_000_000), (TUE, 1_050_000)])},
    THU,
    **{
        "series.1.value_paise": 105_000,
        "series.2.day": WED,
        "series.2.value_paise": 0,
        "series.2.net_put_in_paise": -10_000,
        "series.3.day": THU,
        "series.3.value_paise": 0,
        "series.3.net_put_in_paise": -10_000,
        "points.2.out_paise": 110_000,
        "twr.pct": "10.0000",
    },
)
case_series(
    "S9",
    "Each account's line is rounded on its own, as the screen's is",
    "Two accounts each hold 0.005 unit of a fund (5,000 in units of a millionth) bought for 1 "
    "paisa, and its NAV is 1.0000 (10,000 in price_u). One line is worth 0.005 x 1.00 rupees = "
    "0.5 paisa, which rounds half away from zero to 1 paisa. Two lines: 2 paise. (Rounding the "
    "whole holding, 0.01 x 1.00 = 1 paisa, would give 1.) Net money put in 2 paise. The tab began "
    "on this one complete day, so its own return counts and the return runs over that one day, "
    "from Monday to Monday.",
    [e(1, "BUY", MON, 1, 2, 5_000, 1), e(2, "BUY", MON, 2, 2, 5_000, 1)],
    {2: (MF, [(MON, 10_000)])},
    MON,
    decimals=FUND,
    **{
        "series.0.value_paise": 2,
        "series.0.net_put_in_paise": 2,
        "twr.pct": "0.0000",
        "span": [MON, MON],
    },
)
case_series(
    "S10",
    "A split changes the units on its day and the price with them",
    "10 shares for 10,000.00 on Mon 5 Jan 2026; on Wed 7 Jan each share becomes 5. Closing prices "
    "1,000.00 and 1,000.00 (Mon, Tue), then 200.00 and 210.00 after the split (Wed, Thu). Units "
    "10, 10, 50, 50: values 10,000.00, 10,000.00, 50 x 200.00 = 10,000.00 and 50 x 210.00 = "
    "10,500.00. Time-weighted: 1 x 1 x 1 x 1.05 = 5.0000%.",
    [
        e(1, "BUY", MON, 1, 1, 10 * U, 1_000_000),
        e(2, "SPLIT", WED, None, 1, ratio_num=5, ratio_den=1),
    ],
    {1: (LISTED, [(MON, 10_000_000), (TUE, 10_000_000), (WED, 2_000_000), (THU, 2_100_000)])},
    THU,
    **{
        "series.2.value_paise": 1_000_000,
        "series.3.value_paise": 1_050_000,
        "series.3.net_put_in_paise": 1_000_000,
        "twr.pct": "5.0000",
    },
)
case_series(
    "S11",
    "Shares moved between two accounts change nothing",
    "10 shares for 1,000.00 on Mon 5 Jan 2026 in the first account, moved to the second on Tue "
    "6 Jan. Closing prices 100.00, 101.00, 102.00. Values 1,000.00, 1,010.00, 1,020.00 whoever "
    "holds them; no money moved on Tuesday, so there is a point for the day but no money in it. "
    "Time-weighted 1.01 x (102 / 101) = 1.02: 2.0000%.",
    [
        e(1, "BUY", MON, 1, 1, 10 * U, 100_000),
        e(2, "TRANSFER", TUE, 1, 1, 10 * U, to_account_id=2),
    ],
    {1: (LISTED, [(MON, 1_000_000), (TUE, 1_010_000), (WED, 1_020_000)])},
    WED,
    **{
        "series.1.value_paise": 101_000,
        "series.1.net_put_in_paise": 100_000,
        "series.2.net_put_in_paise": 100_000,
        "points.1.day": TUE,
        "points.1.in_paise": 0,
        "points.1.out_paise": 0,
        "twr.pct": "2.0000",
    },
)
case_series(
    "S12",
    "No closing price at all: no series, and it says why",
    "10 shares bought on Mon 5 Jan 2026 and no closing price for them yet. No day is complete: "
    "no series, the sentence that says it starts once every holding has a closing price, and "
    "the time-weighted return waits for two days of prices.",
    [e(1, "BUY", MON, 1, 1, 10 * U, 100_000)],
    {},
    WED,
    **{
        "words": "Daily history starts once every holding here has a closing price.",
        "twr.words": "TWR needs closing prices for at least two days.",
        "twr.pct": None,
        "opening_paise": None,
        "points.0.value_paise": None,
        "points.0.in_paise": 100_000,
        "span": None,
    },
)
case_series(
    "S13",
    "A dividend on a day with no price joins the next day's period",
    "10 shares for 1,000.00 on Fri 9 Jan 2026, a dividend of 50.00 credited on Sat 10 Jan, closing "
    "prices of 100.00 on Friday and on Mon 12 Jan (a listed share has no close on a Saturday). "
    "Complete: Friday and Monday, both worth 1,000.00; net money put in 1,000.00, then 950.00 "
    "after the dividend. Saturday is not complete, so the 50.00 paid out is in Monday's period: "
    "(1,000 + 50) / 1,000 = 1.05, Friday's own return being 1: 5.0000%.",
    [e(1, "BUY", FRI, 1, 1, 10 * U, 100_000), e(2, "DIVIDEND", SAT, 1, 1, None, 5_000)],
    {1: (LISTED, [(FRI, 1_000_000), (MON2, 1_000_000)])},
    MON2,
    **{
        "series.1.day": MON2,
        "series.1.value_paise": 100_000,
        "series.1.net_put_in_paise": 95_000,
        "points.1.day": SAT,
        "points.1.value_paise": None,
        "points.1.out_paise": 5_000,
        "twr.pct": "5.0000",
        "twr.start_day": FRI,
    },
)
case_series(
    "S14",
    "Entries after the day asked for are not in it",
    "10 shares for 1,000.00 on Mon 5 Jan 2026 and 10 more on Wed 7 Jan, asked for Tue 6 Jan. "
    "Prices 100.00, 101.00, 102.00. Two points, 1,000.00 and 1,010.00, net money put in "
    "1,000.00; Wednesday's purchase is not seen. Time-weighted 1.01: 1.0000%.",
    [e(1, "BUY", MON, 1, 1, 10 * U, 100_000), e(2, "BUY", WED, 1, 1, 10 * U, 102_000)],
    {1: (LISTED, [(MON, 1_000_000), (TUE, 1_010_000), (WED, 1_020_000)])},
    TUE,
    **{
        "series.1.value_paise": 101_000,
        "series.1.net_put_in_paise": 100_000,
        "twr.pct": "1.0000",
    },
)
case_series(
    "S15",
    "A tab of three kinds is complete when each has its price",
    "On Mon 5 Jan 2026: 10 shares (100.00 each), 3 units kept by hand (100.00 each) and 10 fund "
    "units (NAV 20.0000), bought for 1,000.00, 300.00 and 200.00. The shares have a close on "
    "Monday (100.00) and Tuesday (110.00), none after; the value kept by hand and the NAV are "
    "entered once and ride on. Monday: 1,000 + 300 + 200 = 1,500.00; Tuesday: 1,100 + 300 + 200 = "
    "1,600.00. Wednesday and Thursday hold shares without a price and are not complete. "
    "Time-weighted 1,600 / 1,500 = 6.6667%.",
    [
        e(1, "BUY", MON, 1, 1, 10 * U, 100_000),
        e(2, "BUY", MON, 1, 3, 3 * U, 30_000),
        e(3, "BUY", MON, 1, 2, 10 * U, 20_000),
    ],
    {
        1: (LISTED, [(MON, 1_000_000), (TUE, 1_100_000)]),
        2: (MF, [(MON, 200_000)]),
        3: (MANUAL, [(MON, 1_000_000)]),
    },
    THU,
    decimals=FUND,
    **{
        "series.0.value_paise": 150_000,
        "series.0.net_put_in_paise": 150_000,
        "series.1.value_paise": 160_000,
        "twr.pct": "6.6667",
    },
)
case_series(
    "S16",
    "A fee and a dividend on a priced day",
    "10 shares for 1,000.00 on Mon 5 Jan 2026; on Tue 6 Jan a fee of 5.00 paid from outside and a "
    "dividend of 10.00. Closing price 100.00 both days: 1,000.00. Net money put in: 1,000.00, "
    "then 1,000.00 + 5.00 - 10.00 = 995.00. Tuesday's period: 1,000.00 at work, and "
    "(1,000.00 + 10.00 paid out - 5.00 fee) / 1,000.00 = 1.005: 0.5000%.",
    [
        e(1, "BUY", MON, 1, 1, 10 * U, 100_000),
        e(2, "FEE", TUE, 1, None, None, 500),
        e(3, "DIVIDEND", TUE, 1, 1, None, 1_000),
    ],
    {1: (LISTED, [(MON, 1_000_000), (TUE, 1_000_000)])},
    TUE,
    **{
        "series.1.net_put_in_paise": 99_500,
        "points.1.out_paise": 1_000,
        "points.1.fee_paise": 500,
        "twr.pct": "0.5000",
    },
)


# ================================================================ the index, the same money
BENCH = []


def fl(day, inn=0, out=0, fee=0):
    return {"day": day, "in_paise": inn, "out_paise": out, "fee_paise": fee}


def case_bench(cid, title, why, name, flows, closes, vday, now_u=None, span=None, **hand):
    got = benchmark_of(name, flows, closes, vday, now_u, span)
    agrees(got, hand, cid)
    case = {
        "id": cid,
        "title": title,
        "why": why,
        "benchmark": name,
        "flows": flows,
        "closes": [list(c) for c in closes],
        "valuation_day": vday,
    }
    if now_u is not None:
        case["now_u"] = now_u
    if span is not None:
        case["span"] = list(span)
    case["expect"] = got
    BENCH.append(case)
    return got


X2_FLOWS = [
    fl(20230403, inn=5_000_000),
    fl(20231016, inn=2_500_000),
    fl(20240614, out=120_000),
    fl(20250328, out=2_000_000),
]
X2_INDEX = [
    (20230331, 170_000_000),
    (20230403, 173_980_500),
    (20230404, 175_000_000),
    (20231013, 196_000_000),
    (20231016, 197_317_500),
    (20240613, 233_000_000),
    (20240614, 234_656_000),
    (20250327, 230_000_000),
    (20250328, 235_193_500),
    (20261027, 259_000_000),
]
case_bench(
    "B1",
    "The design's B1: X2's money put into NIFTY 50",
    "X2's flows (50,000.00 and 25,000.00 in; 1,200.00 and 20,000.00 out) with the index at "
    "17,398.05, 19,731.75, 23,465.60 and 23,519.35 on those days and 25,900.00 on 27 Oct 2026; the "
    "other closes given are other days' and are not used. Index units (money in rupees over the "
    "level): 50,000 / 17,398.05 + 25,000 / 19,731.75 - 1,200 / 23,465.60 - 20,000 / 23,519.35 = "
    "2.8739... + 1.2670... - 0.0511... - 0.8504... = 3.2393765. Worth 3.2393765 x 25,900.00 = "
    "83,899.85. The XIRR of the same flows with 83,899.85 at the end: 11.7347%. The index's own "
    "return from 3 Apr 2023 to 27 Oct 2026: 25,900.00 / 17,398.05 - 1 = 48.8673%.",
    "nifty50",
    X2_FLOWS,
    X2_INDEX,
    20261027,
    span=(20230403, 20261027),
    **{
        "value_paise": 8_389_985,
        "xirr_pct": "11.7347",
        "twr_pct": "48.8673",
        "flagged": False,
        "words": ["Dividends not included"],
        "label": "NIFTY 50",
    },
)
case_bench(
    "B2",
    "The design's B2: withdrawals the index could not have paid",
    "10,000.00 put in on Mon 5 Jan 2026 and 15,000.00 taken out on Tue 6 Jan, the index at "
    "20,000.00 both days and 21,000.00 on Wed 7 Jan. Units: 10,000 / 20,000 - 15,000 / 20,000 = "
    "0.5 - 0.75 = -0.25 of the index. Kept as they are: worth -0.25 x 21,000.00 = -5,250.00. "
    "Flagged, with the sentence. Less than a year old, so no rate.",
    "nifty50",
    [fl(MON, inn=1_000_000), fl(TUE, out=1_500_000)],
    [(MON, 200_000_000), (TUE, 200_000_000), (WED, 210_000_000)],
    WED,
    **{
        "value_paise": -525_000,
        "xirr_pct": None,
        "flagged": True,
        "words": [DIVIDENDS, COULD_NOT_PAY],
    },
)
case_bench(
    "B3",
    "Negative units still have a rate when the money is old enough",
    "As B2 a year apart: 10,000.00 in on 5 Jan 2025, 15,000.00 out on 5 Jan 2026, the index at "
    "20,000.00 on both days and 21,000.00 on 5 Jan 2027, so -5,250.00 at the end. With y = 1 / "
    "(1 + r) the cash is -10,000 + 15,000 y - 5,250 y^2 = 0, so y = (15 +- sqrt 15) / 10.5 = "
    "1.05972 or 1.79743 and r = -5.6351% or -44.3649%. The one nearest zero is shown.",
    "nifty50",
    [fl(20250105, inn=1_000_000), fl(20260105, out=1_500_000)],
    [(20250105, 200_000_000), (20260105, 200_000_000), (20270105, 210_000_000)],
    20270105,
    **{
        "value_paise": -525_000,
        "xirr_pct": "-5.6351",
        "flagged": True,
    },
)
case_bench(
    "B4",
    "Money that went in before the index prices start",
    "1,000.00 put in on Thu 1 Jan 2026, and the index prices we have begin on Fri 2 Jan: there "
    "is no close for the first day, so there is no honest comparison. No value, no rate, flagged "
    "with the day the prices start.",
    "nifty500",
    [fl(20260101, inn=100_000)],
    [(20260102, 200_000_000), (MON, 205_000_000)],
    MON,
    **{
        "value_paise": None,
        "xirr_pct": None,
        "flagged": True,
        "words": [
            DIVIDENDS,
            "NIFTY 500 prices start 2 Jan 2026; this comparison needs every entry after that.",
        ],
        "label": "NIFTY 500",
    },
)
case_bench(
    "B5",
    "No index prices at all",
    "The index has not been downloaded yet: nothing to compare with, and the sentence says it "
    "will appear.",
    "nifty50",
    [fl(MON, inn=100_000)],
    [],
    WED,
    **{
        "value_paise": None,
        "flagged": True,
        "words": [
            DIVIDENDS,
            "NIFTY 50 prices are not available yet; the comparison appears when they are.",
        ],
    },
)
case_bench(
    "B6",
    "Money on a day the market was shut buys at the last close",
    "10,000.00 put in on Sat 10 Jan 2026, when there is no close: the last one before it is "
    "Friday's, 20,000.00 (the Monday close of 22,000.00 is after the day and is not used). Units "
    "10,000 / 20,000 = 0.5; at Monday 12 Jan's close of 22,000.00 they are worth 11,000.00.",
    "nifty50",
    [fl(SAT, inn=1_000_000)],
    [(FRI, 200_000_000), (MON2, 220_000_000)],
    MON2,
    **{"value_paise": 1_100_000, "flagged": False},
)
case_bench(
    "B7",
    "The index can be valued at a level given, not the last close",
    "As B6 with the index now at 23,000.00 (a live level): 0.5 units are worth 11,500.00.",
    "nifty50",
    [fl(SAT, inn=1_000_000)],
    [(FRI, 200_000_000), (MON2, 220_000_000)],
    MON2,
    now_u=230_000_000,
    **{"value_paise": 1_150_000},
)
case_bench(
    "B8",
    "A fee is money put in",
    "10,000.00 bought and a fee of 200.00 on Mon 5 Jan 2026: 10,200.00 went into the index at "
    "20,000.00, and at 21,000.00 on Wed 7 Jan it is worth 10,200 x 21,000 / 20,000 = 10,710.00.",
    "nifty50",
    [fl(MON, inn=1_000_000, fee=20_000)],
    [(MON, 200_000_000), (TUE, 205_000_000), (WED, 210_000_000)],
    WED,
    **{"value_paise": 1_071_000, "flagged": False},
)
case_bench(
    "B9",
    "Units that dip below nothing and recover are still flagged",
    "10,000.00 in on Mon 5 Jan 2026, 15,000.00 out on Tue 6 Jan, 20,000.00 in on Wed 7 Jan, the "
    "index at 20,000.00 on all three days and 21,000.00 on Thu 8 Jan. Units: 0.5, then -0.25, "
    "then 0.75. Worth 0.75 x 21,000.00 = 15,750.00 in the end, but the index could not have paid "
    "the withdrawal on Tuesday.",
    "nifty50",
    [fl(MON, inn=1_000_000), fl(TUE, out=1_500_000), fl(WED, inn=2_000_000)],
    [(MON, 200_000_000), (TUE, 200_000_000), (WED, 200_000_000), (THU, 210_000_000)],
    THU,
    **{"value_paise": 1_575_000, "flagged": True, "words": [DIVIDENDS, COULD_NOT_PAY]},
)
case_bench(
    "B10",
    "Money after the valuation day is not in it",
    "B1 with a further 100,000.00 put in on 28 Oct 2026, the day after the day asked for, and "
    "an index close for that day: neither is seen.",
    "nifty50",
    X2_FLOWS + [fl(20261028, inn=10_000_000)],
    X2_INDEX + [(20261028, 300_000_000)],
    20261027,
    **{"value_paise": 8_389_985, "xirr_pct": "11.7347"},
)
case_bench(
    "B11",
    "The index's own return over the days of the time-weighted return",
    "The index at 20,000.00 on Mon 5 Jan 2026 and 22,000.00 on Mon 12 Jan, over the days the "
    "tab's time-weighted return runs: 22,000 / 20,000 - 1 = 10.0000%.",
    "nifty50",
    [fl(MON, inn=1_000_000)],
    [(MON, 200_000_000), (WED, 210_000_000), (MON2, 220_000_000)],
    MON2,
    span=(MON, MON2),
    **{"twr_pct": "10.0000", "value_paise": 1_100_000},
)
case_bench(
    "B12",
    "No money, nothing to compare",
    "A tab with no flows: nothing was put into the index, so there is no value and no rate, "
    "and nothing to flag.",
    "nifty50",
    [],
    [(MON, 200_000_000)],
    MON,
    **{"value_paise": None, "xirr_pct": None, "flagged": False, "words": [DIVIDENDS]},
)
case_bench(
    "B13",
    "Money in and out on one day that cancel buys nothing",
    "5,000.00 in and 5,000.00 out on Mon 5 Jan 2026: no net money, no units, so no close is needed "
    "even though the index prices start on Tue 6 Jan. Worth nothing.",
    "nifty50",
    [fl(MON, inn=500_000, out=500_000)],
    [(TUE, 200_000_000)],
    TUE,
    **{"value_paise": 0, "flagged": False, "words": [DIVIDENDS]},
)
case_bench(
    "B14",
    "Two closes for one day: the later in the list",
    "The index is given at 20,000.00 and then 20,500.00 for Mon 5 Jan 2026 (a corrected close) "
    "and 21,000.00 for Wed 7 Jan, in any order. 10,000.00 put in on Monday buys at the later "
    "one, 20,500.00: 10,000 / 20,500 = 0.487805... of the index, worth 0.487805... x 21,000.00 = "
    "10,243.90 on Wednesday. The index's own return from Monday: 21,000 / 20,500 - 1 = 2.4390%.",
    "nifty50",
    [fl(MON, inn=1_000_000)],
    [(WED, 210_000_000), (MON, 200_000_000), (MON, 205_000_000)],
    WED,
    span=(MON, WED),
    **{"value_paise": 1_024_390, "twr_pct": "2.4390", "flagged": False},
)
case_bench(
    "B15",
    "A close of nothing is a gap in the data",
    "The index is given at 20,000.00 on Mon 5 Jan 2026, 0 on Tue 6 Jan and 21,000.00 on Wed 7 Jan. "
    "10,000.00 put in on Tuesday buys at Monday's close (the 0 is no close): 0.5 of the index, "
    "worth 0.5 x 21,000.00 = 10,500.00 on Wednesday. The index's own return from Tue to Wed is "
    "taken from Monday's close: 21,000 / 20,000 - 1 = 5.0000%. A level of nothing given as the "
    "level now is no level: the Wednesday close is used.",
    "nifty50",
    [fl(TUE, inn=1_000_000)],
    [(MON, 200_000_000), (TUE, 0), (WED, 210_000_000)],
    WED,
    now_u=0,
    span=(TUE, WED),
    **{"value_paise": 1_050_000, "twr_pct": "5.0000", "flagged": False},
)
case_bench(
    "B16",
    "Only closes of nothing is no index at all",
    "Every close given is 0: the index might as well not have been downloaded.",
    "nifty500",
    [fl(MON, inn=100_000)],
    [(MON, 0), (TUE, 0)],
    WED,
    **{
        "value_paise": None,
        "flagged": True,
        "words": [
            DIVIDENDS,
            "NIFTY 500 prices are not available yet; the comparison appears when they are.",
        ],
    },
)
case_bench(
    "B17",
    "Two closes for one day: the later in the list, even when it is lower",
    "The index is given at 20,500.00 and then 20,000.00 for Mon 5 Jan 2026 (a close corrected "
    "downwards) and 21,000.00 for Wed 7 Jan. 10,000.00 put in on Monday buys at the later one, "
    "20,000.00: 0.5 of the index, worth 0.5 x 21,000.00 = 10,500.00 on Wednesday. The index's own "
    "return from Monday: 21,000 / 20,000 - 1 = 5.0000%.",
    "nifty50",
    [fl(MON, inn=1_000_000)],
    [(WED, 210_000_000), (MON, 205_000_000), (MON, 200_000_000)],
    WED,
    span=(MON, WED),
    **{"value_paise": 1_050_000, "twr_pct": "5.0000", "flagged": False},
)


# ================================================================ the whole report
REPORTS = []


def case_report(
    cid, title, why, rows, quotes, closes, vday, value_now, now_u=None, holidays=(), **hand
):
    got = history_of(rows, quotes, vday, holidays)
    figure, span = twr_of(got["points"], got["opening"])
    flows = [fl(day, *m) for day, m in sorted(money_by_day(rows).items())]
    benchmarks = [
        benchmark_of(name, flows, closes.get(name, []), vday, (now_u or {}).get(name), span)
        for name in LABELS
    ]
    expect = {
        "twr": figure,
        "benchmarks": benchmarks,
        "series": got["series"],
        "series_words": got["words"],
    }
    agrees(expect, hand, cid)
    case = {"id": cid, "title": title, "why": why, "entries": rows}
    case["quotes"] = quotes_json(quotes)
    case["closes"] = {name: [list(c) for c in found] for name, found in closes.items()}
    case["valuation_day"] = vday
    case["value_paise"] = value_now
    if now_u:
        case["now_u"] = now_u
    if holidays:
        case["holidays"] = list(holidays)
    case["expect"] = expect
    REPORTS.append(case)


X2_ROWS = [
    e(1, "BUY", 20230403, 1, 1, 500 * U, 5_000_000),
    e(2, "BUY", 20231016, 1, 1, 250 * U, 2_500_000),
    e(3, "DIVIDEND", 20240614, 1, 1, None, 120_000),
    e(4, "SELL", 20250328, 1, 1, 200 * U, 2_000_000),
]
case_report(
    "RP1",
    "X2's tab, three closing records old, against NIFTY 50",
    "The entries of X2 (two purchases, a dividend and a sale of 200 of the 750 shares: 550 are "
    "left) on 27 Oct 2026, with closing prices of the share only from Fri 23 Oct (149.00), Mon "
    "26 Oct (149.50) and Tue 27 Oct (149.09): the first three complete days, the weekend between "
    "them not. Values 550 x 149.00 = 81,950.00, 550 x 149.50 = 82,225.00 and 550 x 149.09 = "
    "81,999.50, against net money put in of 50,000 + 25,000 - 1,200 - 20,000 = 53,800.00. The "
    "tab was not empty before the 23rd, so its time-weighted return begins at that close: "
    "(82,225.00 / 81,950.00) x (81,999.50 / 82,225.00) = 81,999.50 / 81,950.00 = 0.0604%, since "
    "23 Oct 2026. The words say where the history begins. NIFTY 50 is B1's: 83,899.85 and "
    "11.7347%, and its own return over the same days 25,900 / 25,700 - 1 = 0.7782%. NIFTY 500 has "
    "no prices yet.",
    X2_ROWS,
    {1: (LISTED, [(20261023, 1_490_000), (20261026, 1_495_000), (20261027, 1_490_900)])},
    {"nifty50": X2_INDEX + [(20261023, 257_000_000), (20261026, 258_500_000)]},
    20261027,
    8_200_000,
    **{
        "series.0.day": 20261023,
        "series.0.value_paise": 8_195_000,
        "series.0.net_put_in_paise": 5_380_000,
        "series.1.value_paise": 8_222_500,
        "series.2.value_paise": 8_199_950,
        "series_words": (
            "Daily history from 23 Oct 2026, the first day every holding here had a closing price."
        ),
        "twr.start_day": 20261023,
        "twr.pct": "0.0604",
        "benchmarks.0.value_paise": 8_389_985,
        "benchmarks.0.xirr_pct": "11.7347",
        "benchmarks.0.twr_pct": "0.7782",
        "benchmarks.0.flagged": False,
        "benchmarks.1.value_paise": None,
        "benchmarks.1.flagged": True,
        "benchmarks.1.words": [
            DIVIDENDS,
            "NIFTY 500 prices are not available yet; the comparison appears when they are.",
        ],
    },
)
case_report(
    "RP2",
    "A tab that began on a complete day, against a live level of NIFTY 500",
    "S2's tab (10 shares for 1,000.00 on Mon 5 Jan 2026, closes 102.00, 104.04, 104.04) asked "
    "for Wed 7 Jan with 1,040.40 as its value now. NIFTY 500 closed at 20,000.00, 20,100.00 and "
    "20,200.00 and stands at 20,250.00 now: the same 1,000.00 bought at 20,000.00 is worth "
    "1,000 x 20,250 / 20,000 = 1,012.50. Its own return over the tab's days: 20,200 / 20,000 - 1 "
    "= 1.0000%. The tab is a few days old, so neither has a yearly rate. NIFTY 50 has no "
    "prices.",
    [e(1, "BUY", MON, 1, 1, 10 * U, 100_000)],
    {1: (LISTED, [(MON, 1_020_000), (TUE, 1_040_400), (WED, 1_040_400)])},
    {"nifty500": [(MON, 200_000_000), (TUE, 201_000_000), (WED, 202_000_000)]},
    WED,
    104_040,
    now_u={"nifty500": 202_500_000},
    **{
        "twr.pct": "4.0400",
        "series.2.value_paise": 104_040,
        "series_words": None,
        "benchmarks.0.flagged": True,
        "benchmarks.1.value_paise": 101_250,
        "benchmarks.1.xirr_pct": None,
        "benchmarks.1.twr_pct": "1.0000",
        "benchmarks.1.flagged": False,
        "benchmarks.1.label": "NIFTY 500",
    },
)
case_report(
    "RP3",
    "A tab bought today: the index has not had a day to move yet",
    "10 shares for 1,000.00 on Mon 5 Jan 2026, closing at 101.00 that day, so 1,010.00 now. The tab "
    "began on a complete day, so its own return counts: 1,010.00 over 1,000.00, 1.0000%, over the "
    "one day, Monday to Monday. NIFTY 50 closed at 20,000.00 and the same 1,000.00 is worth "
    "1,000.00 at the same level. Its own return over that one day is 20,000 / 20,000 - 1 = "
    "0.0000%: the index money is bought at the close, so what the index did during the day is not "
    "counted. NIFTY 500 has no prices yet.",
    [e(1, "BUY", MON, 1, 1, 10 * U, 100_000)],
    {1: (LISTED, [(MON, 1_010_000)])},
    {"nifty50": [(MON, 200_000_000)]},
    MON,
    101_000,
    **{
        "twr.start_day": MON,
        "twr.pct": "1.0000",
        "series.0.value_paise": 101_000,
        "series.0.net_put_in_paise": 100_000,
        "series_words": None,
        "benchmarks.0.value_paise": 100_000,
        "benchmarks.0.xirr_pct": None,
        "benchmarks.0.twr_pct": "0.0000",
        "benchmarks.0.flagged": False,
        "benchmarks.1.flagged": True,
    },
)


# ================================================================ the file
HISTORY = {
    "about": (
        "Golden vectors for core/history.py (PA5): the price a day may use, a tab's daily series "
        "and time-weighted return from its entries and closing prices (the design's T4 is S1), "
        "the same money put into an index (the design's B1 and B2 are B1 and B2) and whole "
        "reports. The evaluator in make_pa5.py replays the entries for every day and shares no "
        "method with the code; every number is worked out by hand in the case's why. Python "
        "only: the phone is sent the figures."
    ),
    "quotes": QUOTES,
    "series": SERIES,
    "benchmarks": BENCH,
    "reports": REPORTS,
}


def build() -> dict:
    """The file name and contents of every vector file this generator writes."""
    return {"history.json": HISTORY}


def main() -> None:
    write_all(build(), Path(__file__).parent)


if __name__ == "__main__":
    main()
