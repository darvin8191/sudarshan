"""Generator of the PA3 golden vectors: valuation.json, freshness.json and daylines.json.

Value and Today (the design's VA1-VA8 and the cases around them), the freshness words (FR1-FR4 and
the edges of each rule), and a ledger turned into the lines a screen prices. Like make_pa1.py it
imports nothing from sudarshan: every number is worked out by hand in the case's ``why``, a small
evaluator below works the same case out again with exact fractions, and the generator refuses to
write a file in which the two disagree. The tests then hold the engine and the naive second
implementation to the file. valuation.json and freshness.json are read by the phone's tests too.

Run: python3 make_pa3.py
"""

from pathlib import Path

from portfolio_vector_tools import (
    DOT,
    U,
    business_days,
    e,
    epoch_ms,
    inr,
    pct4,
    rd,
    write_all,
)

PRICE = 10**4  # one rupee as price_u


# ---------------------------------------------------------------- rows, worked out again
def line(account, qty, opened, cost, inn=0, out=0, over=0):
    return {
        "account_id": account,
        "qty_u": qty,
        "open_qty_u": opened,
        "cost_paise": cost,
        "in_today_paise": inn,
        "out_today_paise": out,
        "over_year_qty_u": over,
    }


def row(mode, price, prev, *lines):
    """The parts of a HoldingRow that the sums read."""
    return {
        "today_mode": mode,
        "price": {"price_u": price, "prev_close_u": prev},
        "lines": list(lines),
    }


def fixed(realised=0, income=0, fees=0, money_in=0, money_out=0, closed=0, closed_base=0):
    return {
        "realised_paise": realised,
        "income_paise": income,
        "fees_paise": fees,
        "money_in_paise": money_in,
        "money_out_paise": money_out,
        "closed_today_paise": closed,
        "closed_today_base_paise": closed_base,
    }


def figure(state, paise=None, base=0, pct=None, counted=0, unknown=0, pending=0):
    return {
        "state": state,
        "paise": paise,
        "base_paise": base,
        "pct": pct,
        "counted": counted,
        "unknown": unknown,
        "pending": pending,
    }


def val(qty_u, price_u):
    return rd(qty_u * price_u, 10**8)


def held(r):
    return any(ln["qty_u"] for ln in r["lines"])


def today_of(r):
    """Today for one row, line by line."""
    mode = r["today_mode"]
    price, prev = r["price"]["price_u"], r["price"]["prev_close_u"]
    if mode == "none":
        return figure("none")
    if mode == "pending":
        return figure("pending", pending=1)
    paise = base = 0
    for ln in r["lines"]:
        if (ln["qty_u"] and price is None) or (ln["open_qty_u"] and prev is None):
            return figure("unknown", unknown=1)
        now = val(ln["qty_u"], price) if ln["qty_u"] else 0
        opened = val(ln["open_qty_u"], prev) if ln["open_qty_u"] else 0
        paise += now - (opened + ln["in_today_paise"] - ln["out_today_paise"])
        base += opened + ln["in_today_paise"]
    return figure("ok", paise, base, pct4(paise, base), counted=1)


def figures_of(r):
    cost = sum(ln["cost_paise"] for ln in r["lines"])
    price = r["price"]["price_u"]
    value = sum(val(ln["qty_u"], price) for ln in r["lines"]) if price is not None else cost
    return {
        "value_paise": value,
        "cost_paise": cost,
        "unrealised_paise": value - cost,
        "gain_pct": pct4(value - cost, cost) if price is not None else None,
        "priced": price is not None,
        "today": today_of(r),
    }


def totals_of(rows, fx):
    parts = [figures_of(r) for r in rows if held(r)]
    value = sum(p["value_paise"] for p in parts)
    cost = sum(p["cost_paise"] for p in parts)
    todays = [p["today"] for p in parts]
    ok = [t for t in todays if t["state"] == "ok"]
    closed, closed_base = fx["closed_today_paise"], fx["closed_today_base_paise"]
    with_closed = closed is not None and (closed != 0 or closed_base != 0)
    paise = sum(t["paise"] for t in ok) + (closed if with_closed else 0)
    base = sum(t["base_paise"] for t in ok) + (closed_base if with_closed else 0)
    unknown = sum(t["state"] == "unknown" for t in todays)
    pending = sum(t["state"] == "pending" for t in todays)
    if ok or with_closed:
        today = figure("ok", paise, base, pct4(paise, base), len(ok), unknown, pending)
    else:
        state = "unknown" if (unknown or closed is None) else ("pending" if pending else "none")
        today = figure(state, None, 0, None, 0, unknown, pending)
    put_in = fx["money_in_paise"] + fx["fees_paise"]
    unrealised = value - cost
    return {
        "value_paise": value,
        "cost_paise": cost,
        "unrealised_paise": unrealised,
        "overall_gain_paise": unrealised
        + fx["realised_paise"]
        + fx["income_paise"]
        - fx["fees_paise"],
        "absolute_return_pct": pct4(value + fx["money_out_paise"] - put_in, put_in),
        "today": today,
        "unpriced": sum(not p["priced"] for p in parts),
    }


def closed_of(rows):
    paise = base = 0
    for r in rows:
        if held(r):
            continue
        t = today_of(r)
        if t["state"] == "unknown":
            return None, 0
        if t["state"] == "ok":
            paise += t["paise"]
            base += t["base_paise"]
    return paise, base


def agrees(got, hand, where):
    """Every number typed by hand in a case is the evaluator's number too, or nothing is written."""
    for path, want in hand.items():
        node = got
        for key in path.split("."):
            node = node[key]
        assert node == want, (where, path, node, want)


# ---------------------------------------------------------------- valuation.json
ROWS, TABS, CLOSED, PREVIOUS, MODES, REPLAYS = [], [], [], [], [], []
SHARES = U  # one share
FUND_UNITS = 109_459_000  # 109.459 units


def case_row(cid, title, why, r, **hand):
    expect = figures_of(r)
    agrees(expect, hand, cid)
    ROWS.append({"id": cid, "title": title, "why": why, "row": r, "expect": expect})
    return r


def case_tab(cid, title, why, rows, fx, **hand):
    expect = totals_of(rows, fx)
    agrees(expect, hand, cid)
    TABS.append(
        {"id": cid, "title": title, "why": why, "rows": rows, "fixed": fx, "expect": expect}
    )


VA3 = case_row(
    "VA3",
    "Today counts what was bought today from the price paid",
    "10 shares at the open, previous close 1,400.00, and 5 more bought today for 7,100.00 "
    "(710000 paise). Now 15 shares at 1,428.25: value 15 x 1,428.25 = 21,423.75 (2142375). The "
    "open was worth 10 x 1,400.00 = 14,000.00 (1400000) and 710000 went in, so the base is 2110000 "
    "and Today is 2142375 - 2110000 = 32375, +323.75, which is 1.5344% of the base. Cost: the 10 at "
    "1,300.00 (1300000) and the 5 at 710000 make 2010000, so the gain is 132375 (6.5858%).",
    row(
        "ok",
        14_282_500,
        14_000_000,
        line(1, 15 * SHARES, 10 * SHARES, 2_010_000, inn=710_000),
    ),
    **{
        "value_paise": 2_142_375,
        "today.paise": 32_375,
        "today.base_paise": 2_110_000,
        "today.pct": "1.5344",
        "unrealised_paise": 132_375,
        "gain_pct": "6.5858",
    },
)
VA5 = case_row(
    "VA5",
    "Today counts what was sold today from the price received",
    "10 shares at the open, previous close 100.00, and 4 sold today for 420.00 (42000). Now 6 at "
    "104.00: value 624.00 (62400). The open was worth 1,000.00 (100000) and 42000 came out, so "
    "Today is 62400 - (100000 - 42000) = 4400, +44.00, which is 4.4000% of 100000. The 6 left "
    "cost 540.00 (54000): gain 8400, 15.5556%.",
    row("ok", 1_040_000, 1_000_000, line(1, 6 * SHARES, 10 * SHARES, 54_000, out=42_000)),
    **{
        "value_paise": 62_400,
        "today.paise": 4_400,
        "today.pct": "4.4000",
        "unrealised_paise": 8_400,
        "gain_pct": "15.5556",
    },
)
VA6 = case_row(
    "VA6",
    "Unrealised is value less the cost, charges included",
    "10 shares that cost 1,020.00 (1000.00 and 20.00 charges, 102000) at 110.00 are worth 1,100.00 "
    "(110000): unrealised +80.00 (8000), 7.8431% of the cost. Previous close 109.00 and no trades "
    "today: Today 110000 - 109000 = 1000, +10.00, 0.9174% of 109000.",
    row("ok", 1_100_000, 1_090_000, line(1, 10 * SHARES, 10 * SHARES, 102_000)),
    **{
        "unrealised_paise": 8_000,
        "gain_pct": "7.8431",
        "today.paise": 1_000,
        "today.pct": "0.9174",
    },
)
VA7 = case_row(
    "VA7",
    "No previous close: Today is not known, never 0",
    "Units were held at the open but the close before today is not known, so what they gained today "
    "cannot be worked out: the state is unknown and there is no figure. The value is still 110000.",
    row("ok", 1_100_000, None, line(1, 10 * SHARES, 10 * SHARES, 102_000)),
    **{"value_paise": 110_000, "today.state": "unknown", "today.unknown": 1},
)
case_row(
    "VA7b",
    "Units bought today need no previous close",
    "5 shares bought today for 7,100.00 (710000) and nothing held at the open: Today needs only "
    "the price. Now 5 x 1,428.25 = 7,141.25 (714125); 714125 - 710000 = 4125, +41.25, 0.5810% of "
    "710000. The missing previous close does not matter, since nothing was held before today.",
    row("ok", 14_282_500, None, line(1, 5 * SHARES, 0, 710_000, inn=710_000)),
    **{
        "value_paise": 714_125,
        "today.paise": 4_125,
        "today.base_paise": 710_000,
        "today.pct": "0.5810",
    },
)
VA7C = case_row(
    "VA7c",
    "No price yet: counted at cost, Today not known",
    "10 shares are held but nothing has priced them. The row is counted at its cost, 102000, so the "
    "tab does not lose the whole holding from its value; there is no gain figure (it would be 0 by "
    "construction) and Today is unknown.",
    row("ok", None, 1_090_000, line(1, 10 * SHARES, 10 * SHARES, 102_000)),
    **{
        "value_paise": 102_000,
        "unrealised_paise": 0,
        "priced": False,
        "gain_pct": None,
        "today.state": "unknown",
    },
)
VA8_ROW = case_row(
    "VA8r",
    "A holding sold out today still has a Today, and needs no price",
    "10 shares at the open (previous close 100.00, 100000) all sold today for 1,050.00 (105000). "
    "Nothing is held now, so no price is needed: Today is 0 - (100000 - 105000) = +5000, +50.00, "
    "5.0000% of 100000.",
    row("ok", None, 1_000_000, line(1, 0, 10 * SHARES, 0, out=105_000)),
    **{"today.paise": 5_000, "today.base_paise": 100_000, "today.pct": "5.0000", "value_paise": 0},
)
FUND_ROW = case_row(
    "VA10",
    "A fund whose NAV is dated today has a Today, each value rounded on its own",
    "109.459 units at the NAV of 45.6789 are worth 4999.96671, 499997 paise (design VA2). At the "
    "open the NAV was 45.5000: 4980.3845, 498038 paise (the .45 rounds down). Today is "
    "499997 - 498038 = 1959, +19.59, where units x change would be 19.5822: each value is a whole "
    "number of paise before they are subtracted. 0.3933% of 498038. The units cost 481620.",
    row("ok", 456_789, 455_000, line(1, FUND_UNITS, FUND_UNITS, 481_620)),
    **{
        "value_paise": 499_997,
        "today.paise": 1_959,
        "today.base_paise": 498_038,
        "today.pct": "0.3933",
        "unrealised_paise": 18_377,
    },
)
PENDING_ROW = case_row(
    "VA11",
    "A fund whose NAV is not dated today waits",
    "The same units at the NAV of 45.5000 (498038), the newest AMFI has published, which is not "
    "today's: Today is pending, not 0. The value still counts.",
    row("pending", 455_000, 454_000, line(1, FUND_UNITS, FUND_UNITS, 481_620)),
    **{"value_paise": 498_038, "today.state": "pending", "today.pending": 1},
)
GOLD_ROW = case_row(
    "VA12",
    "A value kept by hand has no Today",
    "12.5 grams at 7,250.50 a gram are worth 90,631.25 (9063125); they cost 85,000.00 (8500000): "
    "gain 563125, 6.6250%. Nobody prices it each day, so Today is none.",
    row("none", 72_505_000, None, line(1, 12_500_000, 12_500_000, 8_500_000)),
    **{
        "value_paise": 9_063_125,
        "unrealised_paise": 563_125,
        "gain_pct": "6.6250",
        "today.state": "none",
    },
)
TWO_PENNIES = case_row(
    "VA13",
    "Lines are rounded one by one and then added",
    "Two accounts hold 3 units each of something worth 0.0017 a unit. Each line is 3 x 0.0017 = "
    "0.0051 rupees, 0.51 paise, which rounds to 1; the two lines are 2. (Rounding their sum, 1.02 "
    "paise, would give 1.) Every sum on a screen is a sum of figures on that screen.",
    row("ok", 17, 17, line(1, 3 * U, 3 * U, 1), line(2, 3 * U, 3 * U, 1)),
    **{
        "value_paise": 2,
        "cost_paise": 2,
        "unrealised_paise": 0,
        "today.paise": 0,
        "today.base_paise": 2,
    },
)
MOVED = case_row(
    "VA14",
    "Shares moved between two accounts today change nothing in the holding's Today",
    "Account 1 held 10 and gave 3 to account 2, which held 4: previous close 1,400.00, so 3 x "
    "1,400.00 = 4,200.00 (420000) goes out of the first line and into the second. Now 7 and 7 at "
    "1,428.25: 7 x 1,428.25 = 9,997.75 (999775) each. First line: 999775 - (1400000 - 420000) = "
    "19775. Second: 999775 - (560000 + 420000) = 19775. Together 39550, +395.50, which is "
    "14 shares x 28.25, what 14 shares would have made had nothing moved. 1.6618% of 2380000.",
    row(
        "ok",
        14_282_500,
        14_000_000,
        line(1, 7 * SHARES, 10 * SHARES, 910_000, out=420_000),
        line(2, 7 * SHARES, 4 * SHARES, 930_000, inn=420_000),
    ),
    **{
        "value_paise": 1_999_550,
        "today.paise": 39_550,
        "today.base_paise": 2_380_000,
        "today.pct": "1.6618",
    },
)

# ---- tabs
case_tab(
    "VA8",
    "A holding sold out today is hidden, and its Today still counts in the tab",
    "The only holding was sold out today (the VA8r row, +5000 on a base of 100000). The list has no "
    "row for it, but the tab's Today is the +50.00 it made: the figure comes from the fixed sums "
    "(closed_today). The row passed in anyway is skipped, not counted twice.",
    [VA8_ROW],
    fixed(realised=15_000, money_in=90_000, money_out=105_000, closed=5_000, closed_base=100_000),
    **{
        "value_paise": 0,
        "today.state": "ok",
        "today.paise": 5_000,
        "today.base_paise": 100_000,
        "today.pct": "5.0000",
        "today.counted": 0,
        "overall_gain_paise": 15_000,
        "absolute_return_pct": "16.6667",
    },
)
case_tab(
    "VA-mixed",
    "A stock, a fund waiting for its NAV and some gold",
    "Value is the three rows added: 2142375 + 498038 + 9063125 = 11703538. Cost 2010000 + 481620 + "
    "8500000 = 10991620, unrealised 711918. Overall gain adds realised 15000 and income 2500 and "
    "takes off fees 1000: 728418. Absolute return = (11703538 + 120000 - 11200000 - 1000) / "
    "(11200000 + 1000) = 622538 / 11201000 = 5.5579%. Only the stock has a Today (the fund waits, "
    "the gold has none): +32375 on 2110000, one row counted, one pending.",
    [VA3, PENDING_ROW, GOLD_ROW],
    fixed(realised=15_000, income=2_500, fees=1_000, money_in=11_200_000, money_out=120_000),
    **{
        "value_paise": 11_703_538,
        "cost_paise": 10_991_620,
        "unrealised_paise": 711_918,
        "overall_gain_paise": 728_418,
        "absolute_return_pct": "5.5579",
        "today.paise": 32_375,
        "today.counted": 1,
        "today.pending": 1,
    },
)
case_tab(
    "VA-unpriced",
    "A holding with no price yet is counted at cost and said so",
    "The stock (2142375) and a holding nobody has priced (counted at its cost, 102000): value "
    "2244375, cost 2112000, unrealised 132375 (the unpriced one adds none). Today counts the stock "
    "(+32375) and leaves the other out as not known yet. One holding is unpriced.",
    [VA3, VA7C],
    fixed(),
    **{
        "value_paise": 2_244_375,
        "cost_paise": 2_112_000,
        "unrealised_paise": 132_375,
        "today.state": "ok",
        "today.paise": 32_375,
        "today.counted": 1,
        "today.unknown": 1,
        "unpriced": 1,
    },
)
case_tab(
    "VA-unknown",
    "Every holding without a previous close: Today is not known yet",
    "The only holding has no previous close, so nothing is counted: the state is unknown with no "
    "figure and one holding left out.",
    [VA7],
    fixed(),
    **{"today.state": "unknown", "today.paise": None, "today.counted": 0, "today.unknown": 1},
)
case_tab(
    "VA-pending",
    "Only a fund waiting for its NAV: Today is pending",
    "Nothing is counted and nothing is unknown, one fund waits: the state is pending.",
    [PENDING_ROW],
    fixed(),
    **{"today.state": "pending", "today.paise": None, "today.pending": 1},
)
case_tab(
    "VA-none",
    "Only a value kept by hand: no Today",
    "Gold priced by hand has no Today, and neither has a tab made of it.",
    [GOLD_ROW],
    fixed(),
    **{"today.state": "none", "today.paise": None, "today.counted": 0},
)
case_tab(
    "VA-closed-unknown",
    "A sold-out holding with no previous close leaves a figure from the rest",
    "The stock counts (+32375 on 2110000). Something sold out today had no previous close, so the "
    "closed figure is None and contributes nothing: the tab's Today is the stock's alone, and the "
    "screen says a holding sold today is not in it.",
    [VA3],
    fixed(closed=None, closed_base=0),
    **{"today.state": "ok", "today.paise": 32_375, "today.base_paise": 2_110_000},
)
case_tab(
    "VA-closed-only-unknown",
    "Nothing else to count and a sold-out holding with no previous close",
    "With no listed holding in the list and the closed figure unknown the state is unknown.",
    [],
    fixed(closed=None, closed_base=0),
    **{"today.state": "unknown", "today.paise": None},
)
case_tab(
    "VA-closed-pending",
    "A closed holding counts even when the rest waits for a NAV",
    "The fund waits (pending) and a stock sold out today made +5000 on 100000: the state is ok "
    "with that figure, and the fund is still counted as pending.",
    [PENDING_ROW],
    fixed(closed=5_000, closed_base=100_000),
    **{"today.state": "ok", "today.paise": 5_000, "today.pending": 1, "today.counted": 0},
)
case_tab(
    "VA-closed-tiny",
    "A sold-out holding worth less than half a paisa at the open still counts what it sold for",
    "A millionth of a share at the open is worth next to nothing at a close of 1.00 (0 paise), so "
    "the closed holding's base is 0, but the 50 paise it was sold for are real money: the tab's "
    "Today is +50 with no percentage (there is nothing to divide by), not the 0 of a day with "
    "nothing sold.",
    [],
    fixed(closed=50, closed_base=0),
    **{
        "today.state": "ok",
        "today.paise": 50,
        "today.base_paise": 0,
        "today.pct": None,
        "today.counted": 0,
    },
)
case_tab(
    "VA-pennies",
    "The tab adds rows that were rounded one by one",
    "Two rows, each a line of 3 units worth 0.51 paise: 1 and 1, 2 in all. Rounding the 1.02 of "
    "their sum would give 1.",
    [row("ok", 17, 17, line(1, 3 * U, 3 * U, 1)), row("ok", 17, 17, line(1, 3 * U, 3 * U, 1))],
    fixed(),
    **{"value_paise": 2},
)

# ---- absolute return
HUNDRED = row("ok", 1_080_000, 1_080_000, line(1, 100 * SHARES, 100 * SHARES, 1_000_000))
case_tab(
    "X3-abs",
    "Absolute return before the first anniversary (design X3)",
    "10,000.00 put in (1000000) and 100 shares worth 10,800.00 (1080000): (1080000 - 1000000) / "
    "1000000 = 8.0000%.",
    [HUNDRED],
    fixed(money_in=1_000_000),
    **{"absolute_return_pct": "8.0000", "value_paise": 1_080_000},
)
case_tab(
    "X5-abs",
    "A tab that sold everything still has a return (design X5)",
    "10,000.00 in, 10,500.00 out, nothing left: (0 + 1050000 - 1000000) / 1000000 = 5.0000%.",
    [],
    fixed(money_in=1_000_000, money_out=1_050_000),
    **{"absolute_return_pct": "5.0000", "value_paise": 0},
)
case_tab(
    "abs-none",
    "Nothing put in: no absolute return",
    "With no money in and no fees there is nothing to divide by, so the return is absent.",
    [HUNDRED],
    fixed(),
    **{"absolute_return_pct": None},
)
case_tab(
    "abs-fees",
    "Fees count as money put in",
    "10,000.00 in and a 50.00 fee (5000): (1080000 + 0 - 1000000 - 5000) / (1000000 + 5000) = "
    "75000 / 1005000 = 7.4627%. The overall gain takes the fee off: unrealised 80000 - 5000 = 75000.",
    [HUNDRED],
    fixed(money_in=1_000_000, fees=5_000),
    **{"absolute_return_pct": "7.4627", "overall_gain_paise": 75_000},
)
case_tab(
    "abs-loss",
    "A loss has a plain minus",
    "The same 100 shares worth 9,000.00 (900000): (900000 - 1000000) / 1000000 = -10.0000%.",
    [row("ok", 900_000, 900_000, line(1, 100 * SHARES, 100 * SHARES, 1_000_000))],
    fixed(money_in=1_000_000),
    **{"absolute_return_pct": "-10.0000", "overall_gain_paise": -100_000},
)

# ---- the holdings sold out today
for cid, title, why, rows, hand in [
    (
        "C-one",
        "One holding sold out today",
        "The VA8 holding: +5000 on 100000.",
        [VA8_ROW],
        (5_000, 100_000),
    ),
    (
        "C-held",
        "A holding still held is not a closed one",
        "The stock still held (VA3) is left to the list; only the sold-out holding counts.",
        [VA3, VA8_ROW],
        (5_000, 100_000),
    ),
    (
        "C-unknown",
        "A sold-out holding with units at the open and no previous close",
        "Its Today cannot be worked out, so there is no closed figure at all (None), not a sum "
        "with a holding missing from it.",
        [row("ok", None, None, line(1, 0, 10 * SHARES, 0, out=105_000))],
        (None, 0),
    ),
    (
        "C-fund",
        "A fund waiting for its NAV is left out",
        "The fund (pending) is not counted, as in the list; the stock sold out counts.",
        [row("pending", 455_000, 454_000, line(1, 0, FUND_UNITS, 0, out=500_000)), VA8_ROW],
        (5_000, 100_000),
    ),
    (
        "C-two",
        "Two holdings sold out add up",
        "The first +5000 on 100000. The second: 5 shares at the open (previous close 98.00, "
        "49000) sold for 515.00 (51500): 0 - (49000 - 51500) = +2500 on 49000. Together +7500 on "
        "149000.",
        [VA8_ROW, row("ok", None, 980_000, line(1, 0, 5 * SHARES, 0, out=51_500))],
        (7_500, 149_000),
    ),
    (
        "C-day-trade",
        "Bought and sold the same day: no previous close needed",
        "Nothing was held at the open: 100000 in, 101000 out, +1000 on a base of 100000, with no "
        "previous close at all.",
        [row("ok", None, None, line(1, 0, 0, 0, inn=100_000, out=101_000))],
        (1_000, 100_000),
    ),
    (
        "C-tiny",
        "A holding worth less than half a paisa at the open",
        "A millionth of a share at a close of 1.00 is 0 paise, so the base is 0, and it was sold "
        "for 50 paise: +50 on 0. The figure stays, with nothing to divide by.",
        [row("ok", None, 10_000, line(1, 0, 1, 0, out=50))],
        (50, 0),
    ),
    (
        "C-none",
        "Nothing sold out",
        "A tab with only holdings still held has no closed figure: 0 on 0.",
        [VA3],
        (0, 0),
    ),
]:
    got = closed_of(rows)
    assert got == hand, (cid, got, hand)
    CLOSED.append({"id": cid, "title": title, "why": why, "rows": rows, "expect": list(hand)})

# ---- the close Today is measured from
for cid, why, price, prev, price_day, today, want in [
    (
        "P-tick",
        "A price from today's session is measured from the close the tick carries.",
        14_282_500,
        14_000_000,
        20261027,
        20261027,
        14_000_000,
    ),
    (
        "P-earlier",
        "A price from an earlier session is itself the close: Today is only today's trades.",
        14_000_000,
        13_900_000,
        20261026,
        20261027,
        14_000_000,
    ),
    (
        "P-weekend",
        "On a Saturday, Friday's price is the previous close.",
        14_000_000,
        13_900_000,
        20261023,
        20261024,
        14_000_000,
    ),
    (
        "P-no-price",
        "With no price the close that was given is kept.",
        None,
        14_000_000,
        None,
        20261027,
        14_000_000,
    ),
    (
        "P-no-day",
        "A price with no day cannot be called earlier: the close given is kept.",
        14_000_000,
        13_900_000,
        None,
        20261027,
        13_900_000,
    ),
    (
        "P-earlier-no-close",
        "An earlier price with no close given is the close.",
        14_000_000,
        None,
        20261026,
        20261027,
        14_000_000,
    ),
    (
        "P-today-no-close",
        "Today's price with no close given leaves it unknown.",
        14_282_500,
        None,
        20261027,
        20261027,
        None,
    ),
]:
    PREVIOUS.append(
        {
            "id": cid,
            "why": why,
            "price_u": price,
            "prev_close_u": prev,
            "price_day": price_day,
            "today": today,
            "expect": want,
        }
    )

# ---- what Today means for a holding
for kind, basis, price_day, want, why in [
    ("listed", "last", 20261027, "ok", "A listed stock always has a Today."),
    ("listed", "close", 20261024, "ok", "Whatever the price is, a listed stock has a Today."),
    ("mf", "nav", 20261027, "ok", "A fund with the NAV of today has one."),
    (
        "mf",
        "nav",
        20261026,
        "pending",
        "A fund whose newest NAV is yesterday's waits for AMFI (around 9 PM).",
    ),
    ("mf", "manual", 20261027, "none", "A fund priced by hand has none, whatever its date."),
    ("manual", "manual", 20261027, "none", "A value kept by hand has none."),
    ("liability", "manual", 20261027, "none", "A loan's balance has none."),
]:
    MODES.append(
        {
            "asset_kind": kind,
            "basis": basis,
            "price_day": price_day,
            "today": 20261027,
            "expect": want,
            "why": why,
        }
    )

# ---- a morning's ticks, frame by frame (design R1-R3)


def replay(rid, title, why, rows, fx, frames):
    """Apply each frame's settings to the rows in turn; the expected totals come from the evaluator."""
    current = [dict(r, price=dict(r["price"])) for r in rows]
    out = []
    for note, sets in frames:
        for s in sets:
            target = current[s["row"]]
            for key in ("price_u", "prev_close_u"):
                if key in s:
                    target["price"][key] = s[key]
            if "today_mode" in s:
                target["today_mode"] = s["today_mode"]
        out.append({"note": note, "set": sets, "expect": totals_of(current, fx)})
    REPLAYS.append(
        {"id": rid, "title": title, "why": why, "rows": rows, "fixed": fx, "frames": out}
    )
    return out


R_FIXED = fixed(realised=6_000, money_in=2_100_000, money_out=42_000)
R1_ROWS = [
    row("ok", 14_000_000, 14_000_000, line(1, 15 * SHARES, 10 * SHARES, 2_010_000, inn=710_000)),
    row("ok", 1_000_000, 1_000_000, line(1, 6 * SHARES, 10 * SHARES, 54_000, out=42_000)),
]
R1 = replay(
    "R1",
    "A morning of ticks on two listed holdings",
    "Before the open the price on hand is the previous close, so Today is only the effect of the "
    "trades: the 5 shares bought at 1,420.00 are worth 1,400.00 each now, -100.00 (-10000), and "
    "the 4 shares sold for 420.00 against a close of 100.00 made +20.00 (+2000), together -80.00 "
    "(-8000). Each tick then moves one price and the frame is the whole tab again.",
    R1_ROWS,
    R_FIXED,
    [
        ("before the open", []),
        ("first ticks", [{"row": 0, "price_u": 14_100_000}, {"row": 1, "price_u": 1_010_000}]),
        ("a rise", [{"row": 0, "price_u": 14_282_500}]),
        ("the second stock", [{"row": 1, "price_u": 1_040_000}]),
        ("the same tick again", [{"row": 0, "price_u": 14_282_500}]),
    ],
)
assert R1[0]["expect"]["today"]["paise"] == -10_000 + 2_000, R1[0]["expect"]["today"]
assert R1[3]["expect"]["today"]["paise"] == 32_375 + 4_400
R2 = replay(
    "R2",
    "The previous close arrives with the first tick",
    "Two holdings have no previous close at first (Today unknown). The first tick of the first "
    "carries its close, the second's arrives later; each frame counts what is known.",
    [
        row("ok", 14_000_000, None, line(1, 10 * SHARES, 10 * SHARES, 1_300_000)),
        row("ok", 1_000_000, None, line(1, 10 * SHARES, 10 * SHARES, 90_000)),
    ],
    fixed(money_in=1_390_000),
    [
        ("nothing known", []),
        (
            "first tick with its close",
            [{"row": 0, "price_u": 14_100_000, "prev_close_u": 14_000_000}],
        ),
        ("a later tick", [{"row": 0, "price_u": 14_050_000}]),
        (
            "second tick with its close",
            [{"row": 1, "price_u": 1_020_000, "prev_close_u": 1_000_000}],
        ),
    ],
)
assert R2[0]["expect"]["today"]["state"] == "unknown"
assert R2[1]["expect"]["today"]["unknown"] == 1
R3 = replay(
    "R3",
    "A fund's NAV is published in the evening",
    "A stock, a fund waiting for its NAV and gold. At about 9 PM the fund's NAV for the day "
    "arrives: its mode becomes ok, its price the new NAV, its previous close the old one, and "
    "its Today joins the tab's.",
    [VA3, PENDING_ROW, GOLD_ROW],
    fixed(money_in=10_991_620),
    [
        ("evening, before the NAV", []),
        (
            "the NAV is published",
            [{"row": 1, "today_mode": "ok", "price_u": 456_789, "prev_close_u": 455_000}],
        ),
        ("the stock ticks", [{"row": 0, "price_u": 14_300_000}]),
    ],
)
assert R3[0]["expect"]["today"]["pending"] == 1 and R3[1]["expect"]["today"]["counted"] == 2


VALUATION = {
    "about": (
        "Golden vectors for core/valuation.py (PA3). A row is the part of a HoldingRow that the sums "
        "read; every figure is computed with exact fractions by make_pa3.py, which imports nothing "
        "from sudarshan, and equals the number worked out by hand in the case's why. Rows, tabs, "
        "closed, previous_close and modes are one function each; replays are the phone's ticks "
        "frame by frame."
    ),
    "rows": ROWS,
    "tabs": TABS,
    "closed": CLOSED,
    "previous_close": PREVIOUS,
    "modes": MODES,
    "replays": REPLAYS,
}


# ---------------------------------------------------------------- freshness.json
TODAY = 20261027  # a Tuesday
PRICES, BUSINESS, INSTANTS = [], [], []


def priced(price_u, day, at, source, basis, late=False, prev=None):
    return {
        "price_u": price_u,
        "prev_close_u": prev,
        "day": day,
        "at": at,
        "source": source,
        "basis": basis,
        "late": late,
    }


def fresh_case(cid, title, why, price, state, words, today=TODAY, last=None, **context):
    ctx = {"today": today, "last_session_day": last or today, **context}
    PRICES.append(
        {
            "id": cid,
            "title": title,
            "why": why,
            "price": price,
            "context": ctx,
            "expect": {"state": state, "words": words},
        }
    )


def nav_case(cid, title, why, nav_day, behind, state, words, last=TODAY, holidays=()):
    assert business_days(nav_day, last, holidays) == behind, (cid, behind)
    extra = {"holidays": list(holidays)} if holidays else {}
    fresh_case(
        cid,
        title,
        why,
        priced(456_789, nav_day, None, "amfi", "nav", prev=455_000),
        state,
        words,
        last=last,
        **extra,
    )


OLD_NAV = "; AMFI has not published a newer one."
nav_case(
    "FR1",
    "A NAV of the last trading day is fresh",
    "Tuesday 27 Oct is the latest session and the NAV is dated that day: 0 business days behind.",
    20261027,
    0,
    "fresh",
    "NAV of 27 Oct",
)
nav_case(
    "FR1b",
    "Yesterday's NAV is fresh",
    "Monday 26 Oct against Tuesday 27 Oct: 1 business day behind.",
    20261026,
    1,
    "fresh",
    "NAV of 26 Oct",
)
nav_case(
    "FR2",
    "A NAV 4 business days behind is old",
    "Wednesday 21 Oct against Tuesday 27 Oct: Thu 22, Fri 23, Mon 26 and Tue 27 are 4 business "
    "days, more than 3.",
    20261021,
    4,
    "old",
    "NAV of 21 Oct" + OLD_NAV,
)
nav_case(
    "FR2b",
    "3 business days behind is still fresh",
    "Thursday 22 Oct: Fri 23, Mon 26, Tue 27 are 3 business days, not more than 3.",
    20261022,
    3,
    "fresh",
    "NAV of 22 Oct",
)
nav_case(
    "FR2c",
    "A weekend is not business days",
    "Against Monday 26 Oct the Thursday 22 Oct NAV is only 2 business days behind (Fri 23, Mon 26) "
    "though it is 4 calendar days.",
    20261022,
    2,
    "fresh",
    "NAV of 22 Oct",
    last=20261026,
)
nav_case(
    "FR2d",
    "A holiday is not a business day",
    "Wednesday 21 Oct against Tuesday 27 Oct is 4 business days, but Thursday 22 Oct was a holiday: "
    "3, so fresh.",
    20261021,
    3,
    "fresh",
    "NAV of 21 Oct",
    holidays=[20261022],
)
nav_case(
    "FR2e",
    "A holiday on a weekend changes nothing",
    "The same NAV with only Saturday 24 Oct a holiday: still 4 business days, old.",
    20261021,
    4,
    "old",
    "NAV of 21 Oct" + OLD_NAV,
    holidays=[20261024],
)
nav_case(
    "FR2f",
    "A NAV from last year says the year",
    "30 Dec 2025 against 27 Oct 2026 is far more than 3 business days: old, and the year is said "
    "because it is not this year's.",
    20251230,
    business_days(20251230, 20261027),
    "old",
    "NAV of 30 Dec 2025" + OLD_NAV,
)
nav_case(
    "FR2g",
    "A NAV dated after the last session is not behind",
    "The NAV of Tuesday 27 Oct seen when the latest session is Monday 26 Oct: 0 days behind.",
    20261027,
    0,
    "fresh",
    "NAV of 27 Oct",
    last=20261026,
)


def manual_case(cid, title, why, price_day, age, state, words, old_days=30, noun=None, today=TODAY):
    extra = {"old_days": old_days}
    if noun:
        extra["noun"] = noun
    fresh_case(
        cid,
        title,
        why,
        priced(72_505_000, price_day, None, "manual", "manual"),
        state,
        words,
        today=today,
        **extra,
    )


manual_case(
    "FR3",
    "A value 45 days old is old past 30 days",
    "12 Sep to 27 Oct is 18 + 27 = 45 days; the tab's limit is 30.",
    20260912,
    45,
    "old",
    f"Value from 12 Sep{DOT}45 days old",
)
manual_case(
    "FR4",
    "A value 10 days old is fresh",
    "17 Oct to 27 Oct is 10 days.",
    20261017,
    10,
    "fresh",
    f"Value from 17 Oct{DOT}10 days old",
)
manual_case(
    "FR4b",
    "Exactly the limit is not past it",
    "27 Sep to 27 Oct is 30 days, which is not more than 30.",
    20260927,
    30,
    "fresh",
    f"Value from 27 Sep{DOT}30 days old",
)
manual_case(
    "FR4c",
    "One day past the limit is old",
    "26 Sep to 27 Oct is 31 days.",
    20260926,
    31,
    "old",
    f"Value from 26 Sep{DOT}31 days old",
)
manual_case(
    "FR4d",
    "Entered today",
    "The age is 0 and the words say today.",
    20261027,
    0,
    "fresh",
    f"Value from 27 Oct{DOT}today",
)
manual_case(
    "FR4e",
    "One day old",
    "Singular: 1 day old.",
    20261026,
    1,
    "fresh",
    f"Value from 26 Oct{DOT}1 day old",
)
manual_case(
    "FR4f",
    "A limit under a week is read as a week",
    "The limit is 7 to 365 days: 3 is read as 7. 20 Oct is 7 days old, not past 7.",
    20261020,
    7,
    "fresh",
    f"Value from 20 Oct{DOT}7 days old",
    old_days=3,
)
manual_case(
    "FR4g",
    "...and 8 days is past it",
    "The same limit of 3 read as 7: 19 Oct is 8 days old.",
    20261019,
    8,
    "old",
    f"Value from 19 Oct{DOT}8 days old",
    old_days=3,
)
manual_case(
    "FR4h",
    "A limit over a year is read as a year",
    "1000 is read as 365: 27 Oct 2025 is 365 days old (no leap day since), not past 365, and the "
    "year is said.",
    20251027,
    365,
    "fresh",
    f"Value from 27 Oct 2025{DOT}365 days old",
    old_days=1000,
)
manual_case(
    "FR4i",
    "...and 366 days is past it",
    "26 Oct 2025 is 366 days before 27 Oct 2026.",
    20251026,
    366,
    "old",
    f"Value from 26 Oct 2025{DOT}366 days old",
    old_days=1000,
)
manual_case(
    "FR4j",
    "A tab that keeps values 90 days",
    "45 days old against a limit of 90 is fresh.",
    20260912,
    45,
    "fresh",
    f"Value from 12 Sep{DOT}45 days old",
    old_days=90,
)
manual_case(
    "FR4k",
    "A loan's balance says Balance",
    "The same 45 days on a liability: Balance, not Value.",
    20260912,
    45,
    "old",
    f"Balance from 12 Sep{DOT}45 days old",
    noun="Balance",
)
manual_case(
    "FR4l",
    "A value dated after today is read as today's",
    "28 Oct seen on 27 Oct has no negative age: it says today.",
    20261028,
    0,
    "fresh",
    f"Value from 28 Oct{DOT}today",
)
manual_case(
    "FR4m",
    "A value from last year says the year",
    "10 Dec 2025 seen on 15 Jan 2026: 31 + 5 = 36 days, past 30.",
    20251210,
    36,
    "old",
    f"Value from 10 Dec 2025{DOT}36 days old",
    today=20260115,
)

FRI_RECORD = epoch_ms(20261023, 15, 29, 58)
fresh_case(
    "FR5",
    "A tick says nothing: the feed line speaks",
    "A last trade of today from the feed has no sentence of its own.",
    priced(14_282_500, TODAY, epoch_ms(TODAY, 10, 41, 5), "feed", "last", prev=14_000_000),
    "fresh",
    None,
)
fresh_case(
    "FR5b",
    "No price yet",
    "Nothing has priced it: old, and the sentence says so.",
    priced(None, None, None, None, None),
    "old",
    "No price yet",
)
fresh_case(
    "FR5c",
    "A quote says so, with its time",
    "A quote taken at 10:41:05 IST today (the instant is that wall-clock time on 27 Oct 2026).",
    priced(14_282_500, TODAY, epoch_ms(TODAY, 10, 41, 5), "quote", "last", prev=14_000_000),
    "fresh",
    "Last price 10:41:05 (quote)",
)
fresh_case(
    "FR5d",
    "A closing record read on the weekend says so, with the day",
    "Friday's last trade at 15:29:58, seen on Saturday 24 Oct (Friday was the latest session).",
    priced(14_000_000, 20261023, FRI_RECORD, "feed", "last"),
    "fresh",
    "Last price 15:29:58, 23 Oct (closing record)",
    today=20261024,
    last=20261023,
)
fresh_case(
    "FR5e",
    "A record taken late says so",
    "Today's closing record, taken at 17:00 after the service was down at 15:55, still holds the "
    "last trade of 15:29:58.",
    priced(14_000_000, TODAY, epoch_ms(TODAY, 15, 29, 58), "feed", "last", late=True),
    "fresh",
    "Last price 15:29:58 (closing record, taken late)",
)
fresh_case(
    "FR5f",
    "The official close",
    "Friday's price settled to the official close, read on Saturday.",
    priced(14_000_000, 20261023, FRI_RECORD, "feed", "close"),
    "fresh",
    "Close of 23 Oct",
    today=20261024,
    last=20261023,
)
fresh_case(
    "FR5g",
    "A price from before the latest session is old",
    "A price of Thursday 22 Oct when Friday 23 Oct has been traded.",
    priced(14_000_000, 20261022, epoch_ms(20261022, 15, 29, 58), "feed", "last"),
    "old",
    "Price of 22 Oct; no newer price yet.",
    today=20261024,
    last=20261023,
)
fresh_case(
    "FR5h",
    "Before the open on a Monday Friday's record is fresh",
    "Monday 26 Oct before 09:15: the latest session is still Friday 23 Oct, so Friday's last "
    "trade is the price to show.",
    priced(14_000_000, 20261023, FRI_RECORD, "feed", "last"),
    "fresh",
    "Last price 15:29:58, 23 Oct (closing record)",
    today=20261026,
    last=20261023,
)
fresh_case(
    "FR5i",
    "A quote with no time",
    "No time was kept for the quote: the sentence leaves the time out.",
    priced(14_282_500, TODAY, None, "quote", "last"),
    "fresh",
    "Last price (quote)",
)
fresh_case(
    "FR5j",
    "A quote from the last session, no time",
    "A quote of Friday seen on Saturday, with no time: only the day.",
    priced(14_000_000, 20261023, None, "quote", "last"),
    "fresh",
    "Last price 23 Oct (quote)",
    today=20261024,
    last=20261023,
)
fresh_case(
    "FR5k",
    "An old price from last year says the year",
    "31 Dec 2025 when 2 Jan 2026 has traded.",
    priced(14_000_000, 20251231, epoch_ms(20251231, 15, 29, 58), "feed", "last"),
    "old",
    "Price of 31 Dec 2025; no newer price yet.",
    today=20260102,
    last=20260102,
)
fresh_case(
    "FR5l",
    "A fund with no NAV yet",
    "A fund that has no NAV: the same sentence as any holding with no price.",
    priced(None, None, None, "amfi", "nav"),
    "old",
    "No price yet",
)

for start, end, holidays, want, why in [
    (20261027, 20261027, [], 0, "The same day is 0 days behind."),
    (20261026, 20261027, [], 1, "Monday to Tuesday."),
    (20261023, 20261026, [], 1, "Friday to Monday is one business day."),
    (20261024, 20261026, [], 1, "From a Saturday: only Monday counts."),
    (20261025, 20261026, [], 1, "From a Sunday: only Monday counts."),
    (20261023, 20261025, [], 0, "Friday to Sunday: nothing in between is a business day."),
    (20261024, 20261025, [], 0, "Saturday to Sunday."),
    (20261022, 20261027, [], 3, "Thursday to the next Tuesday."),
    (20261021, 20261027, [], 4, "Wednesday to the next Tuesday."),
    (20261021, 20261027, [20261022], 3, "A weekday holiday inside the range is left out."),
    (20261021, 20261027, [20261024], 4, "A holiday on a Saturday changes nothing."),
    (20261021, 20261027, [20261021], 4, "The start day itself is not counted, holiday or not."),
    (20261021, 20261027, [20261027], 3, "A holiday on the end day takes it off."),
    (20261021, 20261027, [20261110], 4, "A holiday after the range changes nothing."),
    (20261021, 20261027, [20261022, 20261022], 3, "A holiday listed twice is one holiday."),
    (20261027, 20261021, [], 0, "End before start is 0, not negative."),
    (20260101, 20261231, [], 260, "The 364 days after 1 Jan 2026 are exactly 52 weeks: 260."),
    (20240228, 20240301, [], 2, "Over 29 February 2024 (Wed 28 Feb to Fri 1 Mar: Thu, Fri)."),
]:
    assert business_days(start, end, holidays) == want, (start, end, want)
    BUSINESS.append({"start": start, "end": end, "holidays": holidays, "expect": want, "why": why})

for day, hour, minute, second, millis, why in [
    (20261027, 10, 41, 5, 0, "An ordinary morning."),
    (20261027, 23, 59, 59, 999, "The last millisecond of the IST day; the millisecond is dropped."),
    (20261028, 0, 0, 0, 0, "Midnight IST is 18:30 UTC the evening before."),
    (20261027, 0, 0, 0, 0, "The first instant of an IST day."),
    (20261027, 5, 29, 59, 999, "Just before 00:00 UTC, still the same IST day."),
    (20280229, 12, 0, 0, 0, "A leap day."),
    (20261231, 23, 59, 59, 0, "The last second of a year."),
    (20270101, 0, 0, 0, 1, "The first millisecond of a year."),
    (19700101, 5, 30, 0, 0, "The epoch itself is 05:30:00 in IST."),
]:
    INSTANTS.append(
        {
            "at": epoch_ms(day, hour, minute, second, millis),
            "day": day,
            "clock": f"{hour:02d}:{minute:02d}:{second:02d}",
            "why": why,
        }
    )

FRESHNESS = {
    "about": (
        "Golden vectors for core/freshness.py and the calendar helpers under it (PA3). prices are "
        "of_price(price, today, last_session_day, old_days, holidays, noun); business_days and "
        "instants are the days.py helpers the phone repeats. Computed by make_pa3.py, which "
        "imports nothing from sudarshan; the instants are built from IST wall-clock times with its "
        "own calendar arithmetic."
    ),
    "prices": PRICES,
    "business_days": BUSINESS,
    "instants": INSTANTS,
}


# ---------------------------------------------------------------- daylines.json
DAY_CASES = []


def day_case(cid, title, why, entries, lines, prices=None, prev=None, today=TODAY, **hand):
    """A ledger and the lines it gives on ``today``; with prices, the figures of each row too."""
    prev = prev or {}
    expect = {"lines": {str(a): ls for a, ls in lines.items()}}
    if prices:
        rows = {}
        for asset, price in prices.items():
            r = row("ok", price, prev.get(asset), *lines[asset])
            rows[str(asset)] = figures_of(r)
        expect["rows"] = rows
    agrees(expect, hand, cid)
    case = {"id": cid, "title": title, "why": why, "today": today, "entries": entries}
    if prev:
        case["prev_close_u"] = {str(a): v for a, v in prev.items()}
    if prices:
        case["price_u"] = {str(a): v for a, v in prices.items()}
    case["expect"] = expect
    DAY_CASES.append(case)


OPEN_BUY = e(1, "BUY", 20260105, 1, 1, 10 * U, 1_300_000)
day_case(
    "DL1",
    "Today's purchase is money in at its gross amount, the charges are in the cost",
    "10 shares bought on 5 Jan for 1,300.00 each-way total 13,000.00 (1300000), and 5 more today "
    "for 7,100.00 (710000) with 20.00 (2000) charges. Now 15 held, 10 at the open. The new lot "
    "costs 712000 (the charges are part of the cost) but the money in today is 710000, the "
    "gross trade amount. Cost 1300000 + 712000 = 2012000. At 1,428.25 against a close of 1,400.00 "
    "this is VA3: value 2142375, Today 32375 on 2110000, unrealised 130375.",
    [OPEN_BUY, e(2, "BUY", TODAY, 1, 1, 5 * U, 710_000, charges_paise=2_000)],
    {1: [line(1, 15 * U, 10 * U, 2_012_000, inn=710_000)]},
    prices={1: 14_282_500},
    prev={1: 14_000_000},
    **{
        "rows.1.value_paise": 2_142_375,
        "rows.1.today.paise": 32_375,
        "rows.1.unrealised_paise": 130_375,
    },
)
day_case(
    "DL2",
    "Today's sale is money out at its gross amount",
    "10 shares for 900.00 (90000), then 4 sold today for 420.00 (42000) with 15.00 charges. The "
    "4 take 4/10 of the cost, 36000, leaving 54000. Money out is the gross 42000. At 104.00 against "
    "100.00 this is VA5: value 62400, Today 4400.",
    [
        e(1, "BUY", 20260105, 1, 1, 10 * U, 90_000),
        e(2, "SELL", TODAY, 1, 1, 4 * U, 42_000, charges_paise=1_500),
    ],
    {1: [line(1, 6 * U, 10 * U, 54_000, out=42_000)]},
    prices={1: 1_040_000},
    prev={1: 1_000_000},
    **{"rows.1.value_paise": 62_400, "rows.1.today.paise": 4_400},
)
day_case(
    "DL3",
    "A holding sold out today keeps its line",
    "10 shares sold today for 1,050.00 (105000): nothing is held now, 10 were at the open, 105000 "
    "went out. The line stays (the list hides it, the tab's Today needs it). At a previous close "
    "of 100.00 this is VA8: Today 5000 on 100000.",
    [e(1, "BUY", 20260105, 1, 1, 10 * U, 90_000), e(2, "SELL", TODAY, 1, 1, 10 * U, 105_000)],
    {1: [line(1, 0, 10 * U, 0, out=105_000)]},
    prices={1: 1_050_000},
    prev={1: 1_000_000},
    **{"rows.1.today.paise": 5_000, "rows.1.today.base_paise": 100_000},
)
day_case(
    "DL4",
    "Bought and sold the same day, the sale entered first",
    "Nothing held before today. The sale of 10 for 1,010.00 (101000) is typed before the purchase "
    "for 1,000.00 (100000) but the day applies purchases first, so both stand: nothing at the "
    "open, nothing now, 100000 in and 101000 out. Today is +1000 on 100000 and needs no previous "
    "close.",
    [e(1, "SELL", TODAY, 1, 1, 10 * U, 101_000), e(2, "BUY", TODAY, 1, 1, 10 * U, 100_000)],
    {1: [line(1, 0, 0, 0, inn=100_000, out=101_000)]},
    prices={1: 1_010_000},
    **{"rows.1.today.paise": 1_000, "rows.1.today.base_paise": 100_000},
)
day_case(
    "DL5",
    "A split and a bonus today are in the open; the purchase is not",
    "10 shares cost 10,000.00 (1000000). Today they split 5 for 1 (50 shares), a bonus of 10 is "
    "allotted and 5 more are bought for 1,025.00 (102500). The split and the bonus take effect "
    "before trading: 60 at the open, 65 now, and the exchange's previous close is already "
    "adjusted (200.00). Cost 1000000 + 0 + 102500 = 1102500. At 210.00: value 65 x 210.00 = "
    "13,650.00 (1365000); the open was 60 x 200.00 = 12,000.00 (1200000); Today = 1365000 - "
    "(1200000 + 102500) = 62500 (60 x 10.00 and 5 x 5.00).",
    [
        e(1, "BUY", 20260105, 1, 1, 10 * U, 1_000_000),
        e(2, "SPLIT", TODAY, None, 1, ratio_num=5, ratio_den=1),
        e(3, "BONUS", TODAY, 1, 1, 10 * U),
        e(4, "BUY", TODAY, 1, 1, 5 * U, 102_500),
    ],
    {1: [line(1, 65 * U, 60 * U, 1_102_500, inn=102_500)]},
    prices={1: 2_100_000},
    prev={1: 2_000_000},
    **{
        "rows.1.value_paise": 1_365_000,
        "rows.1.today.paise": 62_500,
        "rows.1.today.base_paise": 1_302_500,
    },
)
TRANSFER_ENTRIES = [
    e(1, "BUY", 20260105, 1, 1, 10 * U, 1_300_000),
    e(2, "BUY", 20260105, 2, 1, 4 * U, 540_000),
    e(3, "TRANSFER", TODAY, 1, 1, 3 * U, to_account_id=2),
]
day_case(
    "DL6",
    "Shares moved between accounts today count at the previous close on both sides",
    "Account 1 holds 10 (cost 1300000), account 2 holds 4 (540000). Today 3 move from 1 to 2: "
    "the lot goes with its cost (390000), so the accounts now hold 7 for 910000 and 7 for "
    "540000 + 390000 = 930000. At a previous close of 1,400.00 the 3 shares are 4,200.00 "
    "(420000): out of the first line, into the second. This is VA14: Today 39550 on 2380000, "
    "what 14 shares would have made.",
    TRANSFER_ENTRIES,
    {
        1: [
            line(1, 7 * U, 10 * U, 910_000, out=420_000),
            line(2, 7 * U, 4 * U, 930_000, inn=420_000),
        ]
    },
    prices={1: 14_282_500},
    prev={1: 14_000_000},
    **{"rows.1.today.paise": 39_550, "rows.1.today.base_paise": 2_380_000},
)
day_case(
    "DL6b",
    "With no previous close a transfer between accounts counts as nothing, and Today is unknown",
    "The same entries without a previous close: the 3 shares cannot be valued, so no money moves "
    "on either line, and the holding's Today is not known anyway (units were held at the open).",
    TRANSFER_ENTRIES,
    {1: [line(1, 7 * U, 10 * U, 910_000), line(2, 7 * U, 4 * U, 930_000)]},
    prices={1: 14_282_500},
    **{"rows.1.today.state": "unknown"},
)
day_case(
    "DL7",
    "Shares brought in or taken out count at the value entered",
    "Asset 1: 10 shares brought in today from outside at a cost of 10,000.00 (1000000), first "
    "bought 1 Mar 2025 (so over a year old), valued 14,000.00 (1400000) today: 1400000 goes in, "
    "nothing was held at the open. At 1,428.25 Today is 1428250 - 1400000 = 28250. Asset 2: 5 "
    "shares for 4,000.00 (400000); today 2 are taken out to outside, valued 1,700.00 (170000): "
    "they leave at their cost 160000 (no gain), 3 remain for 240000, and 170000 counts as money "
    "out. At 800.00 against 790.00: value 240000, open 395000, Today 240000 - (395000 - 170000) = "
    "15000 (3 x 10.00 and 2 x 60.00).",
    [
        e(
            1,
            "TRANSFER",
            TODAY,
            None,
            1,
            10 * U,
            1_000_000,
            to_account_id=1,
            lot_date=20250301,
            value_paise=1_400_000,
        ),
        e(2, "BUY", 20260105, 1, 2, 5 * U, 400_000),
        e(3, "TRANSFER", TODAY, 1, 2, 2 * U, value_paise=170_000),
    ],
    {
        1: [line(1, 10 * U, 0, 1_000_000, inn=1_400_000, over=10 * U)],
        2: [line(1, 3 * U, 5 * U, 240_000, out=170_000)],
    },
    prices={1: 14_282_500, 2: 8_000_000},
    prev={1: 14_000_000, 2: 7_900_000},
    **{"rows.1.today.paise": 28_250, "rows.2.today.paise": 15_000, "rows.2.value_paise": 240_000},
)
day_case(
    "DL8",
    "Income and fees today do not count",
    "A dividend of 50.00 and a fee of 3.00 today touch no units and are not money in or out of "
    "the holding: the line is the 10 shares as they were.",
    [
        OPEN_BUY,
        e(2, "DIVIDEND", TODAY, 1, 1, amount=5_000),
        e(3, "FEE", TODAY, 1, 1, amount=300),
    ],
    {1: [line(1, 10 * U, 10 * U, 1_300_000)]},
)
day_case(
    "DL9",
    "Sold out earlier, drafts and tomorrow's entries leave no line",
    "Asset 1 was sold out on 1 Mar. Asset 2 has only a draft today and a purchase tomorrow, "
    "neither of which counts. Asset 3 was bought on 20 Oct and is held: its line is the only one.",
    [
        OPEN_BUY,
        e(2, "SELL", 20260301, 1, 1, 10 * U, 1_400_000),
        e(3, "BUY", TODAY, 1, 2, 5 * U, 400_000, status="draft"),
        e(4, "BUY", 20261028, 1, 2, 5 * U, 400_000),
        e(5, "BUY", 20261020, 1, 3, 2 * U, 160_000),
    ],
    {3: [line(1, 2 * U, 2 * U, 160_000)]},
)
day_case(
    "DL10",
    "Units held for more than a year, lot by lot",
    "Today is 27 Oct 2026. The lot of 5 bought 1 Oct 2025 passed its anniversary on 1 Oct 2026: "
    "over a year. The lot of 3 bought 27 Oct 2025 is exactly a year old today: not yet. The lot "
    "of 5 bought 1 Feb 2026 is not. 5 units are held for more than a year.",
    [
        e(1, "BUY", 20251001, 1, 1, 5 * U, 500_000),
        e(2, "BUY", 20260201, 1, 1, 5 * U, 600_000),
        e(3, "BUY", 20251027, 1, 1, 3 * U, 330_000),
    ],
    {1: [line(1, 13 * U, 13 * U, 1_430_000, over=5 * U)]},
)
day_case(
    "DL11",
    "A rights issue paid for today is money in",
    "2 shares taken up today for 1,600.00 (160000): a new lot costing 160000, in today, not "
    "held at the open: 12 now, 10 at the open, cost 1300000 + 160000.",
    [OPEN_BUY, e(2, "RIGHTS", TODAY, 1, 1, 2 * U, 160_000)],
    {1: [line(1, 12 * U, 10 * U, 1_460_000, inn=160_000)]},
)
day_case(
    "DL12",
    "Assets and accounts come out in order",
    "Four holdings entered in a scrambled order: the assets are numbered 1 and 2 and each has "
    "its lines lowest account first.",
    [
        e(1, "BUY", 20261020, 2, 2, 1 * U, 20_000),
        e(2, "BUY", 20261020, 2, 1, 1 * U, 10_000),
        e(3, "BUY", 20261020, 1, 1, 1 * U, 10_000),
        e(4, "BUY", 20261020, 1, 2, 1 * U, 20_000),
    ],
    {
        1: [line(1, U, U, 10_000), line(2, U, U, 10_000)],
        2: [line(1, U, U, 20_000), line(2, U, U, 20_000)],
    },
)
day_case(
    "DL13",
    "A bonus today lifts the open and is not money in",
    "A bonus of 2 shares allotted today, with no purchase today: they are in the open (12 held "
    "at the open and now), cost nothing and are no money in.",
    [OPEN_BUY, e(2, "BONUS", TODAY, 1, 1, 2 * U)],
    {1: [line(1, 12 * U, 12 * U, 1_300_000)]},
)

day_case(
    "DL14",
    "Assets are listed in their own order, whichever account holds them first",
    "Account 1 holds only the asset numbered 2 and account 2 only the asset numbered 1. The "
    "answer lists asset 1 first (and each asset's lines lowest account first), not the asset the "
    "lowest account happens to hold.",
    [
        e(1, "BUY", 20261020, 1, 2, 1 * U, 20_000),
        e(2, "BUY", 20261020, 2, 1, 1 * U, 10_000),
    ],
    {
        1: [line(2, U, U, 10_000)],
        2: [line(1, U, U, 20_000)],
    },
)

DAYLINES = {
    "about": (
        "Golden vectors for core/daylines.py (PA3): a tab's entries replayed up to 'today' and "
        "turned into the lines the screen prices, with the figures of each priced row. The numbers "
        "are worked out by hand in each case's why; make_pa3.py writes this file and imports "
        "nothing from sudarshan. Python only: the phone is sent the lines."
    ),
    "cases": DAY_CASES,
}


def build() -> dict:
    """The file name and contents of every vector file this generator writes."""
    return {"valuation.json": VALUATION, "freshness.json": FRESHNESS, "daylines.json": DAYLINES}


def main() -> None:
    write_all(build(), Path(__file__).parent)


if __name__ == "__main__":
    main()
