"""Generator of lots.json (T2): FIFO lots, intraday first, opening lots, bonus and split.

Imports nothing from sudarshan. Amounts are paise, quantities x 10^6, prices x 10^4; every
expected figure is typed from the arithmetic in the case's note. Run: python3 make_lots.py
"""

import json
from pathlib import Path

U = 10**6
P = 10**4
XYZ, OLD, NEW, FUND = "INE000A01010", "INE000B01018", "INE000B01026", "INF000A01011"
_SEQ = [0]


def t(day, side, qty, rupees, isin=XYZ, account=1, **more):
    """A share trade of ``qty`` at ``rupees`` each; the amount is qty x price in paise."""
    _SEQ[0] += 1
    row = {"taxpayer": 1, "account": account, "day": day, "seq": _SEQ[0], "side": side}
    row.update(segment="EQ", instrument=isin, qty_u=qty * U, price_u=int(rupees * P))
    row.update(amount_paise=int(qty * rupees * 100))
    row.update(more)
    return row


def opening(day, qty, cost, isin=XYZ, source="owner", account=1):
    return {"taxpayer": 1, "account": account, "instrument": isin, "acquired_day": day,
            "qty_u": qty * U, "cost_paise": cost, "source": source}  # fmt: skip


def act(kind, day, num, den, isin=XYZ, **more):
    return {"kind": kind, "instrument": isin, "ex_day": day, "num": num, "den": den, **more}


def sold(day, qty, sale, acquired, cost, **more):
    return {"sale_day": day, "qty_u": int(qty * U), "sale_paise": sale, "acquired_day": acquired,
            "cost_paise": cost, **more}  # fmt: skip


def lot(day, qty, cost, **more):
    return {"acquired_day": day, "qty_u": int(qty * U), "cost_paise": cost, **more}


def intraday(day, qty, buy, sell):
    assert sell - buy == sell - buy
    return {"kind": "speculative", "open_day": day, "close_day": day, "qty_u": qty * U,
            "buy_paise": buy, "sell_paise": sell, "pnl_paise": sell - buy}  # fmt: skip


def case(
    name, note, trades, disposals=(), open_lots=(), intraday=(), notes=0, fractions=(), **more
):
    given = {"trades": trades, **more}
    out = {"disposals": list(disposals), "open_lots": list(open_lots), "intraday": list(intraday),
           "notes": notes, "fractions": list(fractions)}  # fmt: skip
    return {"id": name, "note": note, "in": given, "out": out}


L1_TRADES = [t(20250601, "BUY", 100, 1000), t(20260620, "SELL", 600, 150)]
L1_ACTIONS = [act("BONUS", 20250910, 1, 1), act("SPLIT", 20260105, 5, 1)]
assert 600 * 150 * 100 == 9_000_000 and 9_000_000 * 500 // 600 == 7_500_000
I_OPEN = [opening(20250512, 100, 1_800_000)]
I_DAY = [t(20260803, "BUY", 50, 200), t(20260803, "SELL", 80, 210)]
assert 1_680_000 * 50 // 80 == 1_050_000 and 1_800_000 * 30 // 100 == 540_000

CASES = [
    case("L1", "buy 100 at 1,000 (cost 1,00,000); bonus 1:1 adds lot B 100 at nil on 10 Sep 2025; "
         "split 1 into 5 makes A 500 and B 500, cost and day kept; sell 600 at 150 = 90,000: 500 "
         "from A take 90,000 x 500/600 = 75,000 against 1,00,000, 100 from B take 15,000 against "
         "0; B keeps 400 at nil", L1_TRADES,
         [sold(20260620, 500, 7_500_000, 20250601, 10_000_000, lot_source="upstox_history"),
          sold(20260620, 100, 1_500_000, 20250910, 0, lot_source="bonus")],
         [lot(20250910, 400, 0, source="bonus")], actions=L1_ACTIONS),
    case("L1-not-accepted", "the same with neither action accepted: only lot A 100 exists, so of "
         "the 600 sold 100 take 90,000 x 100/600 = 15,000 against 1,00,000 and 500 (75,000) have "
         "no purchase record: no cost at all, never nil; one note per unconfirmed action",
         L1_TRADES,
         [sold(20260620, 100, 1_500_000, 20250601, 10_000_000),
          sold(20260620, 500, 7_500_000, None, None, lot_source=None)],
         actions=[dict(a, accepted=False) for a in L1_ACTIONS], notes=2),
    case("L2", "5 + 5 shares consolidated 1 for 10: running total 5/10 = 0.5 then 10/10 = 1, so "
         "two lots of half a share that make one share; cost and days kept; reported as a fraction",
         [t(20250602, "BUY", 5, 100), t(20250603, "BUY", 5, 110)], [],
         [lot(20250602, 0.5, 50_000), lot(20250603, 0.5, 55_000)],
         actions=[act("SPLIT", 20260105, 1, 10)], notes=1,
         fractions=[[1, 1, XYZ, 20260105, "SPLIT", U]]),
    case("F1-last-takes-the-rest", "3 shares for 100.00: three sales of 1 cost 10,000/3 = 3,333, "
         "then 6,667/2 = 3,333.5 up to 3,334, then the rest 3,333: the lot is used up exactly",
         [t(20250602, "BUY", 3, 33.3333, amount_paise=10_000), t(20250701, "SELL", 1, 40),
          t(20250702, "SELL", 1, 40), t(20250703, "SELL", 1, 40)],
         [sold(20250701, 1, 4_000, 20250602, 3_333), sold(20250702, 1, 4_000, 20250602, 3_334),
          sold(20250703, 1, 4_000, 20250602, 3_333)]),
    case("F2-oldest-first", "10 at 100 then 10 at 120; sell 15 for 2,000.01: the oldest 10 take "
         "2,00,001 x 10/15 = 1,33,334 paise against 1,00,000, the next 5 the rest 66,667 against "
         "1,20,000 x 5/10 = 60,000; 5 stay at 60,000",
         [t(20250602, "BUY", 10, 100), t(20250710, "BUY", 10, 120),
          t(20250901, "SELL", 15, 133.334, amount_paise=200_001)],
         [sold(20250901, 10, 133_334, 20250602, 100_000),
          sold(20250901, 5, 66_667, 20250710, 60_000)],
         [lot(20250710, 5, 60_000)]),
    case("C1-charges", "a ledger buy of 10 at 100 with charges 5.00: lot cost 1,00,000 + 500 = "
         "1,00,500 paise; a sale of 4 with charges 3.00 takes 1,00,500 x 4/10 = 40,200 and "
         "carries its own 300",
         [t(20250602, "BUY", 10, 100, source="ledger", charges_ex_stt_paise=500),
          t(20250901, "SELL", 4, 110, source="ledger", charges_ex_stt_paise=300)],
         [sold(20250901, 4, 44_000, 20250602, 40_200, charges_paise=300)],
         [lot(20250602, 6, 60_300)]),
    case("O1-opening-lot", "100 bought 10 Mar 2015 for 20,000, before the records: the owner's "
         "opening lot is what the sale of 12 Aug 2026 takes, with its own day",
         [t(20260812, "SELL", 100, 500)],
         [sold(20260812, 100, 5_000_000, 20150310, 2_000_000, lot_source="owner",
               basis_qty_u=100 * U)],
         opening=[opening(20150310, 100, 2_000_000)]),
    case("O2-split-after-2018", "100 of 2016 split 1 into 5 in 2020: 500 held, but the 31 Jan "
         "2018 price is for the 100 of that day; selling 250 takes half the cost (10,000) and "
         "half the 2018 quantity (50)",
         [t(20260812, "SELL", 250, 100)],
         [sold(20260812, 250, 2_500_000, 20160310, 1_000_000, basis_qty_u=50 * U)],
         [lot(20160310, 250, 1_000_000, basis_qty_u=50 * U)],
         opening=[opening(20160310, 100, 2_000_000)], actions=[act("SPLIT", 20200615, 5, 1)]),
    case("O3-split-before-2018", "the same split in June 2017: on 31 Jan 2018 there were already "
         "500, so the 2018 quantity of the 250 sold is 250",
         [t(20260812, "SELL", 250, 100)],
         [sold(20260812, 250, 2_500_000, 20160310, 1_000_000, basis_qty_u=250 * U)],
         [lot(20160310, 250, 1_000_000, basis_qty_u=250 * U)],
         opening=[opening(20160310, 100, 2_000_000)], actions=[act("SPLIT", 20170615, 5, 1)]),
    case("U1-no-purchase-record", "40 sold with nothing held and no opening lot: listed with the "
         "sale value 8,000 and no cost", [t(20260812, "SELL", 40, 200)],
         [sold(20260812, 40, 800_000, None, None)]),
    case("A1-new-isin", "10 bought under the old ISIN, split 1 into 2 with a new ISIN, 20 sold "
         "under the new one: one chain of lots, the whole cost 1,000 against the sale",
         [t(20250602, "BUY", 10, 100, isin=OLD), t(20260812, "SELL", 20, 60, isin=NEW)],
         [sold(20260812, 20, 120_000, 20250602, 100_000, isin=NEW)],
         actions=[act("SPLIT", 20260105, 2, 1, isin=OLD, new_isin=NEW)]),
    case("B1-bonus-fraction", "5 held, bonus 1 for 2: 2.5 shares at nil on the ex-date, kept as "
         "a fraction and reported", [t(20250602, "BUY", 5, 100)], [],
         [lot(20250602, 5, 50_000), lot(20250910, 2.5, 0, source="bonus")],
         actions=[act("BONUS", 20250910, 1, 2)], notes=1,
         fractions=[[1, 1, XYZ, 20250910, "BONUS", 7_500_000]]),
    case("I1", "holding 100 (cost 18,000); one day buy 50 at 200, sell 80 at 210: intraday "
         "min(50, 80) = 50, bought 10,000, sold 16,800 x 50/80 = 10,500, +500; the other 30 are "
         "a delivery sale of 6,300 against 18,000 x 30/100 = 5,400; no lot opens that day",
         I_DAY, [sold(20260803, 30, 630_000, 20250512, 540_000)],
         [lot(20250512, 70, 1_260_000)], [intraday(20260803, 50, 1_000_000, 1_050_000)],
         opening=I_OPEN),
    case("I2", "the same day marked all delivery: the buy opens a lot of 50 at 10,000 and the 80 "
         "sold (16,800) come from the old lot at 18,000 x 80/100 = 14,400", I_DAY,
         [sold(20260803, 80, 1_680_000, 20250512, 1_440_000)],
         [lot(20250512, 20, 360_000), lot(20260803, 50, 1_000_000)], opening=I_OPEN,
         all_delivery=[[1, 1, XYZ, 20260803]]),
    case("I3-earliest-first", "buy 30 at 100, sell 40 at 105, buy 30 at 102: intraday 40 = the "
         "first 30 (3,000) and 10 of the second buy (3,060 x 10/30 = 1,020) = 4,020 against "
         "4,200: +180; the other 20 open a lot at 2,040",
         [t(20260803, "BUY", 30, 100), t(20260803, "SELL", 40, 105), t(20260803, "BUY", 30, 102)],
         [], [lot(20260803, 20, 204_000)], [intraday(20260803, 40, 402_000, 420_000)]),
    case("I4-same-day-cnc-round-trip", "100 bought at 50 and sold at 51 on one day, whatever the "
         "product: nothing delivered, all intraday, +100; no lot and no disposal",
         [t(20260803, "BUY", 100, 50), t(20260803, "SELL", 100, 51)], [], [],
         [intraday(20260803, 100, 500_000, 510_000)]),
    case("I5-sell-then-buy", "holding 100; 40 sold at 210 before 40 bought back at 205 the same "
         "day: still intraday min(40, 40), +200, and the old lot is untouched",
         [t(20260803, "SELL", 40, 210), t(20260803, "BUY", 40, 205)], [],
         [lot(20250512, 100, 1_800_000)], [intraday(20260803, 40, 820_000, 840_000)],
         opening=I_OPEN),
    case("I6-buyback-is-never-intraday", "50 tendered in a buyback on a day 50 were also bought: "
         "the tender is a disposal of the old lot (18,000 x 50/100 = 9,000), the buy opens a lot",
         [t(20260803, "BUY", 50, 200), t(20260803, "SELL", 50, 300, kind_hint="buyback")],
         [sold(20260803, 50, 1_500_000, 20250512, 900_000, kind_hint="buyback")],
         [lot(20250512, 50, 900_000), lot(20260803, 50, 1_000_000)], opening=I_OPEN),
    case("I7-per-account", "account 1 buys 50 and account 2 sells 50 of its own on one day: no "
         "intraday, the rule is per account",
         [t(20250602, "BUY", 50, 100, account=2), t(20260803, "BUY", 50, 200),
          t(20260803, "SELL", 50, 210, account=2)],
         [sold(20260803, 50, 1_050_000, 20250602, 500_000, account=2)],
         [lot(20260803, 50, 1_000_000, account=1)]),
    case("I8-fund-units", "equity fund units bought and sold on one day are not intraday: 10 "
         "units of 2025 go first in, first out",
         [t(20250602, "BUY", 10, 50, isin=FUND, segment="MF", asset_class="equity"),
          t(20260803, "BUY", 10, 60, isin=FUND, segment="MF", asset_class="equity"),
          t(20260803, "SELL", 10, 61, isin=FUND, segment="MF", asset_class="equity")],
         [sold(20260803, 10, 61_000, 20250602, 50_000)], [lot(20260803, 10, 60_000)]),
    case("X1-other-heads-passed-over", "a future and an untagged fund are not lots: nothing out",
         [t(20260803, "BUY", 65, 24000, isin=["NFO", "NIFTY", 20260827, 0, "FUT"], segment="FO"),
          t(20260803, "BUY", 10, 60, isin=FUND, segment="MF")]),
    case("S1-bonus-of-one-account", "account 1 (Upstox) and account 2 (a tab) each bought 10 at "
         "100 (1,000.00); Upstox's bonus 1:1 of 10 Sep 2025, accepted for account 1 only, adds 10 "
         "at nil there; the tab's own bonus entry is its opening lot of 10 at nil on that day, "
         "and the Upstox bonus adds nothing to it (unscoped it would add 20 more, the 10 bought "
         "and the 10 already given)",
         [t(20250602, "BUY", 10, 100), t(20250602, "BUY", 10, 100, account=2)], [],
         [lot(20250602, 10, 100_000, account=1), lot(20250910, 10, 0, account=1, source="bonus"),
          lot(20250602, 10, 100_000, account=2), lot(20250910, 10, 0, account=2, source="bonus")],
         opening=[opening(20250910, 10, 0, source="bonus", account=2)],
         actions=[act("BONUS", 20250910, 1, 1, account=1)]),
    case("S2-split-of-one-account", "the same two purchases; the tab's split 1 into 5 of 5 Jan "
         "2026, for account 2 only, makes its lot 50 at 1,000.00; account 1's 10 are untouched, "
         "so its sale of 10 at 120 (1,200.00) on 12 Aug 2026 takes the whole lot at 1,000.00 "
         "(unscoped, its lot would be 50 and the 10 sold would cost a fifth of it, 200.00)",
         [t(20250602, "BUY", 10, 100), t(20250602, "BUY", 10, 100, account=2),
          t(20260812, "SELL", 10, 120)],
         [sold(20260812, 10, 120_000, 20250602, 100_000, account=1)],
         [lot(20250602, 50, 100_000, account=2)],
         actions=[act("SPLIT", 20260105, 5, 1, account=2)]),
]  # fmt: skip

FILES = {"lots.json": CASES}

if __name__ == "__main__":
    for name, data in FILES.items():
        (Path(__file__).parent / name).write_text(json.dumps(data, indent=1) + "\n")
