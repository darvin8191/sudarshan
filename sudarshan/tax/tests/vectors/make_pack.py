"""Generator of pack.json (T7): PK1, one small synthetic year through the whole core.

Imports nothing from sudarshan. ``IN`` is what the adapter would hand over; ``OUT`` is the pack,
typed here to the contract's shape with every figure worked by hand in the comments below (the
figures were worked out before the code was run against them). The journal's renderer test and
the phone's fixture load this file. No name, account id or amount comes from anyone's books.
Run: python3 make_pack.py

FY 2026-27, taxpayer 1, as of 14 Nov 2026 (so the year is running).

Shares (account 1)
  AAA  1 Jun 2025 buy 100 at 1,000 = 1,00,000. Bonus 1:1 on 10 Sep 2025: 100 more at nil.
       20 Jun 2026 sell 150 at 700 = 1,05,000. First in, first out: 100 of 1 Jun 2025 take
       1,05,000 x 100/150 = 70,000 (held past 1 Jun 2026: long); 50 of the bonus take 35,000
       (held from 10 Sep 2025: short). 50 bonus shares stay at nil.
  BBB  3 Aug 2026 buy 50 at 200 = 10,000 and sell 50 at 210 = 10,500 the same day: intraday,
       +500, turnover 500. No lot, no disposal.
  CCC  an opening lot: 100 bought 5 Jan 2025 for 40,000. Buyback 20 Jul 2026 at 600 = 60,000:
       from 1 Apr 2026 an ordinary sale: 60,000 - 40,000 = 20,000, long (past 5 Jan 2026).
  Charges (Upstox, EQ, the year): total 300.00 of which STT 180.00.
       Traded value without the buyback: 1,05,000 + 10,000 + 10,500 = 1,25,500, of which
       intraday 20,500 and delivery 1,05,000.
       Total 30,000 paise: intraday 30,000 x 20,500/1,25,500 = 4,900.40, delivery 25,099.60;
       floors 4,900 + 25,099, the spare paisa to the .60: 4,900 and 25,100.
       STT 18,000 paise: 2,940.24 and 15,059.76: 2,940 and 15,060.
       Delivery without STT: 25,100 - 15,060 = 10,040 paise over the sales 70,000 : 35,000:
       6,693.33 and 3,346.67: 6,693 and 3,347.
  Gains  AAA long : 70,000.00 - 1,00,000.00 - 66.93 = -30,066.93
         AAA short: 35,000.00 - 0 - 33.47         = +34,966.53
         CCC long : 60,000.00 - 40,000.00 - 0     = +20,000.00
  Year   long gain 20,000.00, long loss 30,066.93, short gain 34,966.53.
         The year's long loss takes the long gain: 20,000 used, 10,066.93 left, carried to
         FY 2034-35. A short-term loss of FY 2024-25 of 10,000 brought forward takes short
         gain: 24,966.53 left. No long gain is left, so the exemption is not used.
         Tax: 24,966.53 x 20% = 4,993.306 -> 4,993.31. Cess 4% = 199.7324 -> 199.73.

Options (NIFTY, lot 65)
  call A: 3 Aug buy 65 at 100 = 6,500; 4 Aug sell at 130 = 8,450: +1,950
  put:    5 Aug sell 65 at 120 = 7,800; 10 Aug buy at 100 = 6,500: +1,300
  call B: 11 Aug buy 65 at 30 = 1,950; held past the 27 Aug expiry, no settlement record:
          taken as worthless: -1,950
  call C (26 Nov expiry): 1 Sep buy 65 at 80 = 5,200: still open, in no total
  Gross 1,950 + 1,300 - 1,950 = 1,300. Turnover 1,950 + 1,300 + 1,950 = 5,200.
  Charges (Upstox, FO): 450.00. Net 1,300 - 450 = 850.
Intraday: gross 500, charges 49.00 (above), net 451, turnover 500.
Turnover in all 5,700; net 1,301 is 22.8% of it: no sentence. Nothing to set off: taxable
business income 850 and 451.
"""

import json
from pathlib import Path

U = 10**6
P = 10**4
AAA, BBB, CCC = "INE000A01010", "INE000B01018", "INE000C01016"
CALL_A = ["NFO", "NIFTY", 20260827, 245000000, "CE"]
PUT = ["NFO", "NIFTY", 20260827, 240000000, "PE"]
CALL_B = ["NFO", "NIFTY", 20260827, 250000000, "CE"]
CALL_C = ["NFO", "NIFTY", 20261126, 260000000, "CE"]
_SEQ = [0]


def t(day, side, qty, rupees, instrument, segment="EQ", **more):
    _SEQ[0] += 1
    row = {"taxpayer": 1, "account": 1, "day": day, "seq": _SEQ[0], "side": side}
    row.update(segment=segment, instrument=instrument, qty_u=qty * U, price_u=rupees * P)
    row.update(amount_paise=qty * rupees * 100)
    row.update(more)
    return row


def fo(day, side, rupees, contract):
    return t(day, side, 65, rupees, contract, "FO")


IN = {
    "fy": 2026, "taxpayer": 1, "as_of_day": 20261114, "as_of_ms": 1794673800000,
    "trades": [
        t(20250601, "BUY", 100, 1000, AAA), t(20260620, "SELL", 150, 700, AAA),
        t(20260803, "BUY", 50, 200, BBB), t(20260803, "SELL", 50, 210, BBB),
        t(20260720, "SELL", 100, 600, CCC, source="ledger", kind_hint="buyback"),
        fo(20260803, "BUY", 100, CALL_A), fo(20260804, "SELL", 130, CALL_A),
        fo(20260805, "SELL", 120, PUT), fo(20260810, "BUY", 100, PUT),
        fo(20260811, "BUY", 30, CALL_B), fo(20260901, "BUY", 80, CALL_C),
    ],
    "opening": [{"taxpayer": 1, "account": 1, "instrument": CCC, "acquired_day": 20250105,
                 "qty_u": 100 * U, "cost_paise": 4_000_000, "source": "owner"}],
    "actions": [{"kind": "BONUS", "instrument": AAA, "ex_day": 20250910, "num": 1, "den": 1}],
    "carries": [{"taxpayer": 1, "loss_fy": 2024, "bucket": "stcl", "paise": 1_000_000,
                 "source": "owner"}],
    "charges": {"EQ": {"total_paise": 30_000, "stt_paise": 18_000},
                "FO": {"total_paise": 45_000, "stt_paise": 21_000}},
    "upstox_pnl": {"NIFTY": 130_000},
    "sources": [{"kind": "trades", "fy": 2026, "segment": "EQ", "state": "ready",
                 "read_at": 1794670000000}],
}  # fmt: skip

assert 10_500_000 * 100 // 150 == 7_000_000 and 30_000 * 20_500 // 125_500 == 4_900
assert 18_000 * 20_500 // 125_500 == 2_940 and 25_100 - 15_060 == 10_040
assert 10_040 * 2 // 3 == 6_693 and 10_040 - 6_693 == 3_347
assert 7_000_000 - 10_000_000 - 6_693 == -3_006_693 and 3_500_000 - 3_347 == 3_496_653
assert 3_496_653 - 1_000_000 == 2_496_653 and 3_006_693 - 2_000_000 == 1_006_693
assert (2_496_653 * 2000 + 5_000) // 10_000 == 499_331 and (
    499_331 * 400 + 5_000
) // 10_000 == 19_973
assert (
    195_000 + 130_000 - 195_000 == 130_000
    and 130_000 - 45_000 == 85_000
    and 50_000 - 4_900 == 45_100
)


def gain(isin, acquired, day, qty, sale, cost, expenses, term, rate, **more):
    row = {"taxpayer": 1, "account": 1, "isin": isin, "acquired_day": acquired, "sale_day": day}
    row.update(qty_u=qty * U, receipt_paise=sale, sale_paise=sale, cost_paise=cost)
    row.update(expenses_paise=expenses, gain_paise=sale - cost - expenses, term=term, rate_bp=rate)
    row.update(status="ok", fmv_used=False, allocated=True, buyback=None, dividend_paise=0)
    row.update(more)
    return row


CAPITAL = {
    "fy": 2026,
    "schedule": [
        gain(AAA, 20250601, 20260620, 100, 7_000_000, 10_000_000, 6_693, "long", 1250),
        gain(AAA, 20250910, 20260620, 50, 3_500_000, 0, 3_347, "short", 2000),
        gain(CCC, 20250105, 20260720, 100, 6_000_000, 4_000_000, 0, "long", 1250,
             allocated=False, buyback="capital_gain"),
    ],
    "by_bucket": [
        {"term": "short", "rate_bp": 2000, "gain_paise": 3_496_653, "loss_paise": 0},
        {"term": "long", "rate_bp": 1250, "gain_paise": 2_000_000, "loss_paise": 3_006_693},
    ],
    "st_gain_paise": 3_496_653, "st_loss_paise": 0,
    "lt_gain_paise": 2_000_000, "lt_loss_paise": 3_006_693,
    "set_off": [
        {"step": "same_year", "bucket": "ltcl", "loss_fy": 2026, "against": "long",
         "rate_bp": 1250, "paise": 2_000_000},
        {"step": "brought_forward", "bucket": "stcl", "loss_fy": 2024, "against": "short",
         "rate_bp": 2000, "paise": 1_000_000},
    ],
    "exemption": {"limit_paise": 12_500_000, "used_paise": 0, "order": "highest_rate_first",
                  "by_rate": []},
    "taxable": {"st_paise": 2_496_653, "lt_paise": 0},
    "indicative_tax": {
        "lines": [
            {"term": "short", "rate_bp": 2000, "taxable_paise": 2_496_653, "tax_paise": 499_331},
            {"term": "long", "rate_bp": 1250, "taxable_paise": 0, "tax_paise": 0},
        ],
        "tax_paise": 499_331, "cess_bp": 400, "cess_paise": 19_973,
        "cess_label": "cess at 4%, before surcharge",
    },
    "carried": [{"loss_fy": 2026, "bucket": "ltcl", "paise": 1_006_693, "source": "computed",
                 "last_fy": 2034}],
    "lapsed": [], "own_figures": [], "needs_fmv": [], "unknown_cost": [], "exempt_buybacks": [],
    "set_off_words": [
        "This year's long-term capital loss: Rs 20,000 set against this year's long-term gain.",
        "The FY 2024-25 short-term capital loss: Rs 10,000 set against this year's short-term "
        "gain.",
        "Long-term capital loss of FY 2026-27: Rs 10,066.93 carried 8 years (to FY 2034-35), "
        "against long-term capital gains only.",
        "Order used: this year's losses first, then earlier years' oldest first, a short-term "
        "loss before a long-term one of the same year. Verify with your CA.",
    ],
    "expenses_allocated": True,
}  # fmt: skip

BUSINESS = {
    "non_speculative": {
        "gross_paise": 130_000, "charges_paise": 45_000, "turnover_paise": 520_000,
        "by_kind": {"options": {"gross_paise": 130_000, "turnover_paise": 520_000, "count": 3}},
        "net_paise": 85_000,
    },
    "speculative": {
        "gross_paise": 50_000, "charges_paise": 4_900, "turnover_paise": 50_000,
        "by_kind": {"speculative": {"gross_paise": 50_000, "turnover_paise": 50_000, "count": 1}},
        "net_paise": 45_100,
    },
    "turnover_method": "abs_pnl", "turnover_paise": 570_000, "flags": [],
    "turnover_method_label": "Sum of profits and losses, ICAI method",
    "taxable": {"non_speculative_paise": 85_000, "speculative_paise": 45_100},
    "available_other_heads_paise": 0,
    "set_off": [], "set_off_words": [], "carried": [], "lapsed": [], "own_figures": [],
    "differs": [], "unknown_settlement": [],
    "open": [[1, 1, CALL_C, "BUY", 20260901, 65 * U, 520_000]],
    "assumed_zero_count": 1,
}  # fmt: skip

OUT = {
    "fy": 2026, "fy_label": "FY 2026-27", "taxpayer": 1, "as_of_ms": 1794673800000,
    "as_of_day": 20261114, "running": True,
    "note": "A working paper: verify with your CA. The broker's own report is the filing source.",
    "rules_version": "2026.11.14-1",
    "inputs": {"sources": IN["sources"], "missing": []},
    "capital_gains": CAPITAL,
    "business": BUSINESS,
    "dividends": {"by_payer": [], "buyback_paise": 0, "total_paise": 0,
                  "tds_expected_paise": None, "unconfirmed_count": 0},
    "charges": {"by_segment": IN["charges"], "state": "ready"},
    "not_computed": [
        "Tax at slab rates, surcharge, rebate and the choice of regime.",
        "The unused basic exemption against these gains.",
        "Set-off of a business loss against other income, and of any loss across heads.",
        "The presumptive scheme, and whether books or an audit are needed.",
        "Other business expenses (data, software, internet, an office).",
        "Rules against wash sales and bonus or dividend stripping.",
        "The extra tax on a promoter in a buyback.",
        "Debt funds, unlisted shares, property, gold, crypto and foreign assets.",
        "Dividends other than a buyback's receipt (they come with corporate actions).",
    ],
    "not_computed_sales": [],
    "notes": [
        "Share-market charges are Upstox's total for the year, shared between intraday and "
        "delivery by traded value and over delivery sales by sale value (allocated). STT is "
        "left out of capital gains and kept in business charges.",
        "1 option was open past expiry with no settlement record and taken as expired worthless.",
        "A buyback is worked out by the rule of its date; from 1 April 2026 as an ordinary sale "
        "of a listed share. Verify with your CA.",
    ],
}  # fmt: skip

FILES = {"pack.json": [{"id": "PK1", "note": "one synthetic year end to end; the arithmetic is "
                        "in make_pack.py's own header", "in": IN, "out": OUT}]}  # fmt: skip

if __name__ == "__main__":
    for name, data in FILES.items():
        (Path(__file__).parent / name).write_text(json.dumps(data, indent=1) + "\n")
