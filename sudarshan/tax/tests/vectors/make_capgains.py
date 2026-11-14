"""Generator of capgains.json and buyback.json (T3): one disposal worked out by hand.

Imports nothing from sudarshan. The G cases are the 31 Jan 2018 rule, the H cases the 12-month
line by calendar dates, the R cases the rate row on the day of sale, the E cases expenses, and
buyback.json the three regimes by date. Rupees in the notes, paise in the rows.
Run: python3 make_capgains.py
"""

import json
from pathlib import Path

U = 10**6
P = 10**4
XYZ = "INE000A01010"


def sale(qty, receipt, acquired, cost, day=20260812, **more):
    row = {"taxpayer": 1, "account": 1, "isin": XYZ, "sale_day": day, "seq": 1}
    row.update(qty_u=qty * U, sale_paise=receipt * 100, acquired_day=acquired)
    row.update(cost_paise=None if cost is None else cost * 100, basis_qty_u=qty * U)
    row.update(more)
    return row


def case(name, note, disposal, out, fmv=None, **more):
    given = {"disposal": disposal, "fmv": {} if fmv is None else {XYZ: int(fmv * P)}, **more}
    return {"id": name, "note": note, "in": given, "out": out}


def ok(gain, term, rate, cost, **more):
    return {"status": "ok", "gain_paise": gain * 100, "term": term, "rate_bp": rate,
            "cost_paise": cost * 100, **more}  # fmt: skip


OLD = 20160310  # bought 10 Mar 2016: 100 at 200 = 20,000
assert max(20_000, min(35_000, 50_000)) == 35_000 and 50_000 - 35_000 == 15_000  # G1
assert max(20_000, min(35_000, 30_000)) == 30_000 and 30_000 - 30_000 == 0  # G2
assert max(20_000, min(35_000, 15_000)) == 20_000 and 15_000 - 20_000 == -5_000  # G3
assert max(20_000, min(15_000, 50_000)) == 20_000 and 50_000 - 20_000 == 30_000  # G4
assert max(10_000, min(350 * 50, 25_000)) == 17_500 and 25_000 - 17_500 == 7_500  # G8

GAINS = [
    case("G1", "cost 20,000, 31 Jan 2018 value 35,000, sold for 50,000: max(20,000, min(35,000, "
         "50,000)) = 35,000; gain 15,000 long-term at 12.5%",
         sale(100, 50_000, OLD, 20_000), ok(15_000, "long", 1250, 35_000, fmv_used=True), 350),
    case("G2", "sold for 30,000: min(35,000, 30,000) = 30,000; max(20,000, 30,000) = 30,000; "
         "gain 0", sale(100, 30_000, OLD, 20_000), ok(0, "long", 1250, 30_000, fmv_used=True), 350),
    case("G3", "sold for 15,000: min(35,000, 15,000) = 15,000; max(20,000, 15,000) = 20,000; "
         "loss 5,000", sale(100, 15_000, OLD, 20_000), ok(-5_000, "long", 1250, 20_000,
                                                          fmv_used=False), 350),
    case("G4", "2018 value 15,000, sold for 50,000: min = 15,000; max(20,000, 15,000) = 20,000; "
         "gain 30,000", sale(100, 50_000, OLD, 20_000), ok(30_000, "long", 1250, 20_000,
                                                           fmv_used=False), 150),
    case("G5", "the 2018 price is not known and the sale is above cost: left out of every total, "
         "never worked out as if the price were 0 (which would give 30,000)",
         sale(100, 50_000, OLD, 20_000), {"status": "needs_fmv", "gain_paise": None,
                                          "term": "long"}),
    case("G5-below-cost", "the 2018 price is not known but the sale (15,000) is below cost "
         "(20,000): min(any value, 15,000) is at most 15,000, so the cost stays 20,000 whatever "
         "the price was: loss 5,000 without asking for it",
         sale(100, 15_000, OLD, 20_000), ok(-5_000, "long", 1250, 20_000, fmv_used=False)),
    case("G6", "bought on 1 Feb 2018 itself: the 2018 rule does not apply whatever the table "
         "holds; gain 50,000 - 20,000 = 30,000",
         sale(100, 50_000, 20180201, 20_000), ok(30_000, "long", 1250, 20_000, fmv_used=False),
         350),
    case("G7", "bought on 31 Jan 2018, the last day the rule covers: cost becomes 35,000, gain "
         "15,000", sale(100, 50_000, 20180131, 20_000), ok(15_000, "long", 1250, 35_000,
                                                           fmv_used=True), 350),
    case("G8-split-after-2018", "250 sold of a lot split 1 into 5 in 2020 (50 shares in 2018 "
         "terms), cost 10,000, sold for 25,000, 2018 price 350: value 350 x 50 = 17,500 (not "
         "350 x 250); max(10,000, min(17,500, 25,000)) = 17,500; gain 7,500",
         sale(250, 25_000, OLD, 10_000, basis_qty_u=50 * U), ok(7_500, "long", 1250, 17_500,
                                                                fmv_used=True), 350),
    case("G9-no-record", "no purchase record: no cost, no gain, no term; in no total",
         sale(100, 50_000, None, None), {"status": "unknown_cost", "gain_paise": None,
                                         "cost_paise": None, "term": None, "rate_bp": None}),
    case("E1-allocated", "G1 with 120.50 of the year's non-STT charges allocated to this sale: "
         "50,000 - 35,000 - 120.50 = 14,879.50", sale(100, 50_000, OLD, 20_000),
         {"status": "ok", "gain_paise": 1_487_950, "expenses_paise": 12_050, "allocated": True},
         350, expenses_paise=12_050),
    case("E2-own-charges", "a ledger sale that carries its own non-STT charges of 3.00: 440 - "
         "402 - 3 = 35", sale(4, 440, 20250602, 402, charges_paise=300),
         {"status": "ok", "gain_paise": 3_500, "expenses_paise": 300, "allocated": False}),
    case("E3-none-known", "no charges known and none allocated: expenses 0, the larger gain",
         sale(4, 440, 20250602, 402), {"status": "ok", "gain_paise": 3_800, "expenses_paise": 0}),
]  # fmt: skip


def term(name, note, acquired, day, out, rate, **more):
    return case(
        name, note, sale(10, 1_500, acquired, 1_000, day), ok(500, out, rate, 1_000), **more
    )


TERMS = [
    term("H1-anniversary", "bought 20 Jun 2025, sold 20 Jun 2026: exactly 12 months, not more: "
         "short-term at 20%", 20250620, 20260620, "short", 2000),
    term("H1-day-after", "sold 21 Jun 2026: more than 12 months: long-term at 12.5%", 20250620,
         20260621, "long", 1250),
    term("H1-day-before", "sold 19 Jun 2026: short-term", 20250620, 20260619, "short", 2000),
    term("H1-leap-28-feb", "bought 29 Feb 2024; 2025 has no 29 Feb, so the anniversary is 28 Feb "
         "2025: sold that day is short-term", 20240229, 20250228, "short", 2000),
    term("H1-leap-1-mar", "bought 29 Feb 2024, sold 1 Mar 2025: long-term", 20240229, 20250301,
         "long", 1250),
    term("H2-into-leap-28", "bought 28 Feb 2023, sold 28 Feb 2024: the anniversary: short-term "
         "at the 15% of that day", 20230228, 20240228, "short", 1500),
    term("H2-into-leap-29", "bought 28 Feb 2023, sold 29 Feb 2024: a day past the anniversary: "
         "long-term at the 10% of that day", 20230228, 20240229, "long", 1000),
    term("H3-year-end", "bought 1 Jan 2025, sold 31 Dec 2025: short-term", 20250101, 20251231,
         "short", 2000),
    term("H4-other-reading", "the rule row's other reading (on_anniversary): bought 20 Jun 2025, "
         "sold 20 Jun 2026 is already long-term", 20250620, 20260620, "long", 1250,
         settings={"lt_boundary": "on_anniversary"}),
    term("H4-other-reading-before", "on that reading 19 Jun 2026 is still short-term", 20250620,
         20260619, "short", 2000, settings={"lt_boundary": "on_anniversary"}),
    term("R1-last-old-day", "sold 22 Jul 2024, short-term: 15%", 20240101, 20240722, "short", 1500),
    term("R1-first-new-day", "sold 23 Jul 2024, short-term: 20%, though bought before the change",
         20240101, 20240723, "short", 2000),
    term("R2-last-old-day", "sold 22 Jul 2024, long-term: 10%", 20230101, 20240722, "long", 1000),
    term("R2-first-new-day", "sold 23 Jul 2024, long-term: 12.5%, though bought before the change",
         20230101, 20240723, "long", 1250),
]  # fmt: skip


def tender(name, note, acquired, day, out, cost=40_000, **more):
    row = sale(100, 60_000, acquired, cost, day, kind_hint="buyback")
    return case(name, note, row, out, **more)


def exempt():
    return {"status": "exempt_buyback", "gain_paise": None, "buyback": "exempt",
            "receipt_paise": 6_000_000, "cost_paise": 4_000_000, "dividend_paise": 0}  # fmt: skip


def as_dividend(term_, rate, cost=40_000):
    return {"status": "ok", "gain_paise": -cost * 100, "term": term_, "rate_bp": rate,
            "buyback": "dividend_and_loss", "sale_paise": 0, "receipt_paise": 6_000_000,
            "dividend_paise": 6_000_000}  # fmt: skip


def as_gain(gain, term_, rate):
    return {"status": "ok", "gain_paise": gain * 100, "term": term_, "rate_bp": rate,
            "buyback": "capital_gain", "sale_paise": 6_000_000, "dividend_paise": 0}  # fmt: skip


BUYBACKS = [
    tender("K1-exempt", "100 shares, cost 40,000, tendered at 600 = 60,000 on 15 Sep 2024: the "
           "company paid the tax; listed with receipt and cost, in no total", 20230105, 20240915,
           exempt()),
    tender("K1-last-exempt-day", "30 Sep 2024 is still the first regime", 20230105, 20240930,
           exempt()),
    tender("K2-first-dividend-day", "1 Oct 2024: the whole 60,000 is a dividend and the sale "
           "value is nil: a loss of the cost 40,000, long-term (bought 5 Jan 2023)", 20230105,
           20241001, as_dividend("long", 1250)),
    tender("K2-dividend-and-loss", "bought 5 Jan 2025, tendered 10 Feb 2026: dividend 60,000; "
           "0 - 40,000 = long-term loss 40,000 (held past 5 Jan 2026)", 20250105, 20260210,
           as_dividend("long", 1250)),
    tender("K2-short", "bought 5 Jun 2025, tendered 10 Feb 2026: the same loss is short-term",
           20250605, 20260210, as_dividend("short", 2000)),
    tender("K2-last-dividend-day", "31 Mar 2026 is still the second regime", 20250105, 20260331,
           as_dividend("long", 1250)),
    tender("K2-old-lot", "bought 2016 for 20,000, 2018 value 35,000, tendered 10 Jun 2025: the "
           "sale value is nil, so min(35,000, 0) = 0 and the loss is the cost 20,000, not 35,000",
           20160310, 20250610, as_dividend("long", 1250, 20_000), 20_000, fmv=350),
    tender("K3-first-gain-day", "1 Apr 2026: an ordinary disposal: 60,000 - 40,000 = 20,000 "
           "long-term", 20250105, 20260401, as_gain(20_000, "long", 1250)),
    tender("K3-capital-gain", "bought 5 Jan 2025, tendered 20 Jul 2026: long-term gain 20,000",
           20250105, 20260720, as_gain(20_000, "long", 1250)),
    tender("K3-short", "bought 5 Jan 2026, tendered 20 Jul 2026: short-term gain 20,000 at 20%",
           20260105, 20260720, as_gain(20_000, "short", 2000)),
]  # fmt: skip

FILES = {"capgains.json": {"gain": GAINS, "term": TERMS}, "buyback.json": BUYBACKS}

if __name__ == "__main__":
    for name, data in FILES.items():
        (Path(__file__).parent / name).write_text(json.dumps(data, indent=1) + "\n")
