"""Generator of the PN2 golden vectors: reports.json, Upstox's report rows as integers (M7.6, M7.7).

The rows are Upstox's own documented shapes (upstox.com/developer/api-documentation, read 8 Nov
2026): a row of GET /v2/charges/historical-trades, a row of GET /v2/trade/profit-loss/data, the
body of GET /v2/trade/profit-loss/charges and of .../metadata. Each figure the phone receives is
worked out here by hand in the case's why and by Decimal arithmetic of this file's own, never by
the code under test: rupees x 100 as paise, quantity x 10^6 as qty_u, a price x 10^4 as price_u,
half away from zero; a date as YYYYMMDD; a charge Upstox left null stays null. Like the other
generators it imports nothing from sudarshan.

Run: python3 make_pn2.py
"""

from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from portfolio_vector_tools import write_all


def scaled(value, factor):
    if value is None:
        return None
    return int((Decimal(str(value)) * factor).to_integral_value(ROUND_HALF_UP))


def paise(value):
    return scaled(value, 100)


def qty_u(value):
    return scaled(value, 1_000_000)


def price_u(value):
    return scaled(value, 10_000)


def iso(text):
    return None if text is None else int(text[:4] + text[5:7] + text[8:10])


def dmy(text):
    return None if text is None else int(text[6:10] + text[3:5] + text[:2])


TRADES = []
PNL = []
CHARGES = []
META = []


def trade(ident, title, why, raw, **expect_more):
    expect = {
        "trade_id": raw["trade_id"],
        "trade_date": iso(raw["trade_date"]),
        "exchange": raw["exchange"],
        "segment": raw["segment"],
        "transaction_type": raw["transaction_type"],
        "scrip_name": raw["scrip_name"],
        "symbol": raw.get("symbol"),
        "isin": raw.get("isin"),
        "instrument_token": raw.get("instrument_token"),
        "option_type": raw.get("option_type"),
        "strike_price_u": price_u(raw.get("strike_price")) or None,
        "expiry": iso(raw.get("expiry")),
        "qty_u": qty_u(raw["quantity"]),
        "price_u": price_u(raw["price"]),
        "amount_paise": paise(raw["amount"]),
        **expect_more,
    }
    TRADES.append({"id": ident, "title": title, "why": why, "raw": raw, "expect": expect})


def pnl(ident, title, why, raw):
    bought, sold = paise(raw["buy_amount"]), paise(raw["sell_amount"])
    expect = {
        "scrip_name": raw["scrip_name"],
        "isin": raw.get("isin"),
        "trade_type": raw["trade_type"],
        "qty_u": qty_u(raw["quantity"]),
        "buy_date": dmy(raw.get("buy_date")),
        "sell_date": dmy(raw.get("sell_date")),
        "buy_average_u": price_u(raw["buy_average"]),
        "sell_average_u": price_u(raw["sell_average"]),
        "buy_amount_paise": bought,
        "sell_amount_paise": sold,
        "pnl_paise": sold - bought,
    }
    PNL.append({"id": ident, "title": title, "why": why, "raw": raw, "expect": expect})


def charges(ident, title, why, raw):
    tree = raw["charges_breakdown"]
    taxes, other = tree["taxes"], tree["charges"]
    expect = {
        "total_paise": paise(tree.get("total")),
        "brokerage_paise": paise(tree.get("brokerage")),
        "gst_paise": paise(taxes.get("gst")),
        "stt_paise": paise(taxes.get("stt")),
        "stamp_duty_paise": paise(taxes.get("stamp_duty")),
        "transaction_paise": paise(other.get("transaction")),
        "clearing_paise": paise(other.get("clearing")),
        "ipft_paise": paise(other.get("ipft")),
        "others_paise": paise(other.get("others")),
        "sebi_turnover_paise": paise(other.get("sebi_turnover")),
        "demat_transaction_paise": paise(other.get("demat_transaction")),
    }
    CHARGES.append({"id": ident, "title": title, "why": why, "raw": raw, "expect": expect})


trade(
    "T1",
    "An equity buy as the trade history lists it",
    "10 RELIANCE bought at 2,587.45 for 25,874.50 on 27 Oct 2026: quantity 10 is 10000000 qty_u, "
    "the price 25874500 price_u, the amount 2587450 paise, the date 20261027. No option type, "
    "strike or expiry (null), the ISIN, symbol and instrument token as given.",
    {
        "exchange": "NSE",
        "segment": "EQ",
        "option_type": None,
        "quantity": 10,
        "amount": 25874.50,
        "trade_id": "50001",
        "trade_date": "2026-10-27",
        "transaction_type": "BUY",
        "scrip_name": "RELIANCE INDUSTRIES",
        "strike_price": None,
        "expiry": None,
        "price": 2587.45,
        "isin": "INE002A01018",
        "symbol": "RELIANCE",
        "instrument_token": "NSE_EQ|INE002A01018",
    },  # fmt: skip
)

trade(
    "T2",
    "An option sale carries its strike and expiry; no ISIN or token",
    "65 NIFTY 25900 CE sold at 125.00 for 8,125.00 on 28 Oct 2026, expiring 3 Nov 2026: strike "
    "25900.0 is 259000000 price_u, expiry 20261103, option type CE. Upstox gives no ISIN and no "
    "instrument token for FO rows: both null.",
    {
        "exchange": "NFO",
        "segment": "FO",
        "option_type": "CE",
        "quantity": 65,
        "amount": 8125.00,
        "trade_id": "50002",
        "trade_date": "2026-10-28",
        "transaction_type": "SELL",
        "scrip_name": "NIFTY 25900 CE",
        "strike_price": 25900.0,
        "expiry": "2026-11-03",
        "price": 125.0,
        "isin": None,
        "symbol": "NIFTY26NOV25900CE",
        "instrument_token": None,
    },  # fmt: skip
)

trade(
    "T3",
    "A mutual-fund purchase with fractional units",
    "109.459 units bought at 45.6789 for 5,000.00: quantity 109.459 is 109459000 qty_u, the "
    "price 456789 price_u, the amount 500000 paise. Upstox gives an ISIN and a token for MF and "
    "no symbol.",
    {
        "exchange": "BMF",
        "segment": "MF",
        "option_type": None,
        "quantity": 109.459,
        "amount": 5000.00,
        "trade_id": "MF7",
        "trade_date": "2026-04-01",
        "transaction_type": "BUY",
        "scrip_name": "Some Flexi Cap Fund Direct Growth",
        "strike_price": 0,
        "expiry": None,
        "price": 45.6789,
        "isin": "INF000A01011",
        "symbol": None,
        "instrument_token": "MF|INF000A01011",
    },  # fmt: skip
)

trade(
    "T4",
    "A price with a half paise is rounded half away from zero",
    "3 shares at 100.005 for 300.015: the price 100.005 is exactly 1000050 price_u (four "
    "decimals hold it); the amount 300.015 rounds to 30002 paise, half away from zero. A strike "
    "of 0 is no strike (null).",
    {
        "exchange": "BSE",
        "segment": "EQ",
        "option_type": None,
        "quantity": 3,
        "amount": 300.015,
        "trade_id": "50004",
        "trade_date": "2026-05-12",
        "transaction_type": "BUY",
        "scrip_name": "SMALL CO",
        "strike_price": 0,
        "expiry": None,
        "price": 100.005,
        "isin": "INE000X01010",
        "symbol": "SMALLCO",
        "instrument_token": "BSE_EQ|INE000X01010",
    },  # fmt: skip
)

pnl(
    "P1",
    "Upstox's documented P&L row",
    "100 ZEE ENTER bought on 14-09-2021 at 12,345.67 and sold the same day at 12,345.67: both "
    "amounts 12,345.67 are 1234567 paise and the P&L is 0. Dates in dd-mm-yyyy become 20210914.",
    {
        "quantity": 100,
        "isin": "INE256A01028",
        "scrip_name": "ZEE ENTER",
        "trade_type": "EQ",
        "buy_date": "14-09-2021",
        "buy_average": 12345.67,
        "sell_date": "14-09-2021",
        "sell_average": 12345.67,
        "buy_amount": 12345.67,
        "sell_amount": 12345.67,
    },  # fmt: skip
)

pnl(
    "P2",
    "A gain: sold for more than bought",
    "100 bought at 250.25 (25,025.00) and sold at 262.75 (26,275.00): 2627500 - 2502500 = "
    "125000 paise, a gain of 1,250.00.",
    {
        "quantity": 100,
        "isin": "INE256A01028",
        "scrip_name": "ZEE ENTER",
        "trade_type": "EQ",
        "buy_date": "14-09-2026",
        "buy_average": 250.25,
        "sell_date": "15-09-2026",
        "sell_average": 262.75,
        "buy_amount": 25025.0,
        "sell_amount": 26275.0,
    },  # fmt: skip
)

pnl(
    "P3",
    "A loss on an option, with a fractional average",
    "130 units of an option bought at an average of 101.333 (13,173.29) and sold at 95.5 "
    "(12,415.00): the P&L is 1241500 - 1317329 = -75829 paise (a loss of 758.29). The average "
    "101.333 is 1013330 price_u. The trade type OPT is kept.",
    {
        "quantity": 130,
        "isin": None,
        "scrip_name": "NIFTY 25900 CE",
        "trade_type": "OPT",
        "buy_date": "20-10-2026",
        "buy_average": 101.333,
        "sell_date": "21-10-2026",
        "sell_average": 95.5,
        "buy_amount": 13173.29,
        "sell_amount": 12415.0,
    },  # fmt: skip
)

charges(
    "C1",
    "Upstox's documented charges body: a null charge stays null",
    "total 154.23 is 15423 paise, brokerage 97.23 is 9723, GST 20.93 is 2093, STT 15 is 1500, "
    "stamp duty 2 is 200, transaction 0.56 is 56, clearing 0 is 0, IPFT null stays null (not 0), "
    "others 0, SEBI turnover 0.01 is 1, demat transaction 18.5 is 1850.",
    {
        "charges_breakdown": {
            "total": 154.23,
            "brokerage": 97.23,
            "taxes": {"gst": 20.93, "stt": 15, "stamp_duty": 2},
            "charges": {
                "transaction": 0.56,
                "clearing": 0,
                "ipft": None,
                "others": 0,
                "sebi_turnover": 0.01,
                "demat_transaction": 18.5,
            },
        }
    },  # fmt: skip
)

META.append(
    {
        "id": "M1",
        "title": "Upstox's documented metadata body",
        "why": "trades_count 10 and page_size_limit 5000 are kept as the whole numbers they are.",
        "raw": {"trades_count": 10, "page_size_limit": 5000},
        "expect": {"trades_count": 10, "page_size_limit": 5000},
    }
)

REPORTS = {
    "about": (
        "Golden vectors for reports.py (PN2, M7.6 and M7.7): Upstox's documented rows of the trade "
        "history, the trade-wise P&L report, its metadata and the charges, and the integers the "
        "phone receives for each. Worked out by hand in each case's why; this file's arithmetic is "
        "Decimal, not the code's."
    ),
    "trades": TRADES,
    "pnl": PNL,
    "charges": CHARGES,
    "meta": META,
}


def build() -> dict:
    return {"reports.json": REPORTS}


def main() -> None:
    write_all(build(), Path(__file__).parent)


if __name__ == "__main__":
    main()
