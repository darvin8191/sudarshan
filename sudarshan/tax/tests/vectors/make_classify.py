"""Generator of classify.json (T1): which head a trade, a day and a round trip belong to.

Imports nothing from sudarshan. Every expected value is typed from the rule as the design states
it, with the reason in the case's note. Run: python3 make_classify.py
"""

import json
from pathlib import Path

U = 10**6
FUT = ["NFO", "NIFTY", 20261126, 0, "FUT"]
CALL = ["NFO", "NIFTY", 20261126, 245000000, "CE"]


def trade(segment, instrument="INE000A01010", **more):
    row = {"taxpayer": 1, "account": 1, "day": 20260803, "seq": 1, "side": "BUY", "qty_u": U}
    row.update(segment=segment, instrument=instrument, price_u=1000000, amount_paise=10000)
    row.update(more)
    return row


def head(case, note, row, out):
    return {"id": case, "note": note, "in": row, "out": out}


HEAD_OF = [
    head("H-EQ", "a share trade alone is delivery; the day decides intraday", trade("EQ"),
         "delivery_equity"),
    head("H-EQ-PARTLY-PAID", "a company's partly paid equity share (IN9, kind 01) is a share",
         trade("EQ", "IN9000A01018"), "delivery_equity"),
    head("H-EQ-TAGGED", "a share tagged equity by its ledger is a share",
         trade("EQ", asset_class="equity"), "delivery_equity"),
    head("H-GOLD-ETF", "a gold ETF's unit (INF) trades in the cash segment and is no share",
         trade("EQ", "INF000B01019"), "not_computed"),
    head("H-LIQUID-ETF", "a liquid ETF's unit, often held as F&O margin, is taxed at slab",
         trade("EQ", "INF000C01017"), "not_computed"),
    head("H-EQUITY-ETF", "an equity ETF's unit is a fund unit; no list of them is kept here",
         trade("EQ", "INF000D01015"), "not_computed"),
    head("H-ETF-TAGGED", "a listed asset's class is equity until changed, so the class of an ETF "
         "unit cannot make it a share", trade("EQ", "INF000B01019", asset_class="equity"),
         "not_computed"),
    head("H-REIT", "a REIT's unit: a company's ISIN (INE) of another kind (25)",
         trade("EQ", "INE000R25014"), "not_computed"),
    head("H-INVIT", "an InvIT's unit: a company's ISIN of another kind (23)",
         trade("EQ", "INE000V23012"), "not_computed"),
    head("H-GOLD-BOND", "a sovereign gold bond is a government security (IN0)",
         trade("EQ", "IN0020200013"), "not_computed"),
    head("H-GSEC", "a government security or treasury bill", trade("EQ", "IN0020230010"),
         "not_computed"),
    head("H-DEBENTURE", "a company's debenture: kind 07", trade("EQ", "INE000A07017"),
         "not_computed"),
    head("H-PREFERENCE", "a company's preference share: kind 04", trade("EQ", "INE000A04014"),
         "not_computed"),
    head("H-UNKNOWN-ISIN", "an ISIN of no known shape is never assumed to be a share",
         trade("EQ", "XX0000000000"), "not_computed"),
    head("H-FOREIGN-ISIN", "another country's ISIN, though its later characters match",
         trade("EQ", "US000A010100"), "not_computed"),
    head("H-NAME", "a ledger asset with no ISIN goes by its name: unknown, so not computed",
         trade("EQ", "Some Company Ltd", asset_class="equity"), "not_computed"),
    head("H-SHORT", "a cut-off ISIN is unknown", trade("EQ", "INE000A0101"), "not_computed"),
    head("H-FO-FUT", "a future is business, not speculative", trade("FO", FUT), "derivative"),
    head("H-FO-OPT", "an option is business, not speculative", trade("FO", CALL), "derivative"),
    head("H-COM", "a commodity derivative", trade("COM", ["MCX", "GOLDM", 20261205, 0, "FUT"]),
         "derivative"),
    head("H-CD", "a currency derivative", trade("CD", ["CDS", "USDINR", 20261127, 0, "FUT"]),
         "derivative"),
    head("H-MF-EQ", "a fund unit tagged equity", trade("MF", "INF000A01011", asset_class="equity"),
         "equity_mf"),
    head("H-MF-NONE", "a fund unit with no tag is not assumed equity", trade("MF", "INF000A01011"),
         "not_computed"),
    head("H-MF-DEBT", "a debt fund is outside this engine",
         trade("MF", "INF000A01011", asset_class="debt"), "not_computed"),
    head("H-MANUAL", "a hand-kept asset is never computed, whatever its segment",
         trade("EQ", asset_class="manual"), "not_computed"),
    head("H-LIAB", "a liability is never computed", trade("EQ", asset_class="liability"),
         "not_computed"),
]  # fmt: skip


def day(case, note, bought, sold, all_delivery, out):
    row = {"bought_u": bought * U, "sold_u": sold * U, "all_delivery": all_delivery}
    return {"id": case, "note": note, "in": row, "out": out * U}


INTRADAY = [
    day("D-I1", "I1: bought 50, sold 80: min(50, 80) = 50 intraday", 50, 80, False, 50),
    day("D-MORE-BUY", "bought 120, sold 70: min = 70 intraday, 50 delivered", 120, 70, False, 70),
    day("D-EQUAL", "a same-day round trip, CNC or not: min(100, 100) = 100", 100, 100, False, 100),
    day("D-BUY-ONLY", "nothing sold: min(40, 0) = 0", 40, 0, False, 0),
    day("D-SELL-ONLY", "nothing bought: min(0, 40) = 0", 0, 40, False, 0),
    day("D-I2", "I2: the owner marked the day as all delivery: 0", 50, 80, True, 0),
]


def trip(case, note, exchange, product, same_day, out, isin="INE000A01010"):
    row = {"exchange": exchange, "symbol": "XYZ", "product": product, "same_day": same_day}
    if isin is not None:
        row["isin"] = isin
    return {"id": case, "note": note, "in": row, "out": out}


PRODUCT = [
    trip("P-MIS", "MIS on a cash exchange is intraday", "NSE", "MIS", True, "intraday_equity"),
    trip("P-CNC", "CNC held overnight is delivery", "NSE", "CNC", False, "delivery_equity"),
    trip("P-CNC-SAME-DAY", "a CNC round trip inside one day delivered nothing: intraday", "BSE",
         "CNC", True, "intraday_equity"),
    trip("P-NFO-NRML", "an F&O trip is a derivative whatever the product", "NFO", "NRML", False,
         "derivative"),
    trip("P-NFO-MIS", "an intraday F&O trip is still not speculative", "NFO", "MIS", True,
         "derivative"),
    trip("P-BFO", "BSE derivatives", "BFO", "NRML", False, "derivative"),
    trip("P-MCX", "commodity derivatives", "MCX", "NRML", False, "derivative"),
    trip("P-CDS", "currency derivatives", "CDS", "MIS", True, "derivative"),
    trip("P-INDEX", "an index is not traded: not computed", "NSE_INDEX", "CNC", False,
         "not_computed"),
    trip("P-GOLD-ETF", "a gold ETF's trip held overnight is no share's: not computed", "NSE", "CNC",
         False, "not_computed", "INF000B01019"),
    trip("P-LIQUID-ETF-DAY", "a liquid ETF bought and sold in a day is no intraday share trade",
         "NSE", "MIS", True, "not_computed", "INF000C01017"),
    trip("P-REIT", "a REIT's unit", "NSE", "CNC", False, "not_computed", "INE000R25014"),
    trip("P-GOLD-BOND", "a gold bond", "NSE", "CNC", False, "not_computed", "IN0020200013"),
    trip("P-NO-ISIN", "a cash trip whose ISIN is not known is not assumed to be a share's", "BSE",
         "CNC", False, "not_computed", None),
    trip("P-NFO-NO-ISIN", "a derivative has no ISIN and needs none", "NFO", "NRML", False,
         "derivative", None),
    trip("P-NSE-NRML", "NRML is not a cash-market product: not computed", "NSE", "NRML", False,
         "not_computed"),
]  # fmt: skip

FILES = {
    "classify.json": {"head_of": HEAD_OF, "intraday_qty": INTRADAY, "head_of_product": PRODUCT}
}

if __name__ == "__main__":
    for name, data in FILES.items():
        (Path(__file__).parent / name).write_text(json.dumps(data, indent=1) + "\n")
