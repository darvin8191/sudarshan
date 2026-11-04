"""Generator of the PA8 golden vectors: import_zerodha.json.

What importing a Zerodha tradebook (equity segment) would do to a tab: the trade id is the
duplicate key, a trade is a purchase or a sale at its price with no charges, and a file that does
not have a tradebook's header is refused in words. Like the other generators it imports nothing
from sudarshan. Every case carries its hand derivation and the rows are synthetic, typed from the
column list (P21): the symbols, the ISINs and the ids are invented. The header names are the
ones the design expects; only two were seen in a third party's description, so a real download
is to be checked against them (see imports/zerodha.py).

Run: python3 make_pa8.py
"""

from pathlib import Path

from portfolio_vector_tools import write_all

S = 10**6  # one unit
R = 10**4  # one rupee as price_u
TODAY = 20261030
WORLD = {
    "today": TODAY,
    "accounts": {"1": "Zerodha", "2": "Account B"},
    "default_account": 1,
    "assets": {
        "1": {"name": "RELIANCE", "symbol": "RELIANCE", "isin": "INE000A00001"},
        "2": {"name": "TCS", "symbol": "TCS", "isin": "INE000A00002"},
    },
}
HEAD = (
    "symbol,isin,trade_date,exchange,segment,series,trade_type,auction,quantity,price,"
    "trade_id,order_id,order_execution_time\n"
)


def line(symbol, day, kind, qty, price, trade_id, segment="EQ", exchange="NSE", isin=""):
    return (
        f"{symbol},{isin},{day},{exchange},{segment},EQ,{kind},false,{qty},{price},"
        f"{trade_id},ORD{trade_id},{day}T09:20:05\n"
    )


def buy(day, asset, qty, price_u):
    return {
        "kind": "BUY",
        "trade_date": day,
        "account_id": 1,
        "asset_id": asset,
        "qty_u": qty * S,
        "price_u": price_u,
    }


def sell(day, asset, qty, price_u):
    return {**buy(day, asset, qty, price_u), "kind": "SELL"}


def key(trade_id):
    return f"zerodha:{trade_id}"


def row(n, status, entry=None, words=None, trade_id=None):
    return {
        "row": n,
        "status": status,
        "words": words,
        "entry": entry,
        "key": None if trade_id is None else key(trade_id),
    }


CASES = []


def case(cid, title, why, text, rows, summary, existing=(), keys=None, unused=()):
    CASES.append(
        {
            "id": cid,
            "title": title,
            "why": why,
            "text": text,
            "existing": list(existing),
            "keys": keys or {},
            "expect": {"summary": summary, "unused": list(unused), "rows": rows},
        }
    )


def held(i, asset, qty, day=20260101):
    return {
        "id": i,
        "seq": i,
        "kind": "BUY",
        "trade_date": day,
        "account_id": 1,
        "asset_id": asset,
        "qty_u": qty * S,
        "price_u": 100 * R,
    }


T1 = line("RELIANCE", "2026-01-05", "buy", 10, "1400.50", 100001)
T2 = line("RELIANCE", "2026-01-05", "buy", 5, "1401.25", 100002)
T3 = line("RELIANCE", "2026-03-02", "sell", 12, "1500.00", 100003)
E1 = [
    buy(20260105, 1, 10, 14005000),
    buy(20260105, 1, 5, 14012500),
    sell(20260302, 1, 12, 15000000),
]
case(
    "Z1",
    "buys and a sale, trade ids as keys",
    "Two purchases and a sale. 1400.50 is 14,005,000 price_u; 1401.25 is 14,012,500. The 15 "
    "shares held after the purchases cover the sale of 12. Each key is zerodha: and the trade id. "
    "The account is the one chosen for the import (1), because a tradebook names none.",
    HEAD + T1 + T2 + T3,
    [row(2 + k, "new", E1[k], None, 100001 + k) for k in range(3)],
    "3 new",
)
case(
    "Z1b",
    "the same file again adds nothing",
    "The tab holds the three trade ids: every row is already here, with the day the first import "
    "was made (this year, so without the year).",
    HEAD + T1 + T2 + T3,
    [
        row(
            2 + k,
            "dup",
            E1[k],
            f"Row {2 + k} is already in this tab (imported 15 Oct).",
            100001 + k,
        )
        for k in range(3)
    ],
    "3 already in this tab",
    existing=[held(1, 1, 15), {**held(2, 1, 12), "kind": "SELL", "trade_date": 20260302}],
    keys={key(100001 + k): 20261015 for k in range(3)},
)
case(
    "Z1c",
    "a later file that overlaps the first",
    "A second download covers 2 Mar to 30 Jun and repeats the sale (trade id 100003); the "
    "purchase of 4 on 6 Jun is new. The 15 shares held cover the sale already in the tab.",
    HEAD + T3 + line("RELIANCE", "2026-06-06", "buy", 4, "1480.00", 100004),
    [
        row(2, "dup", E1[2], "Row 2 is already in this tab (imported 15 Oct).", 100003),
        row(3, "new", buy(20260606, 1, 4, 14800000), None, 100004),
    ],
    "1 new, 1 already in this tab",
    existing=[held(1, 1, 15), {**held(2, 1, 12), "kind": "SELL", "trade_date": 20260302}],
    keys={key(100003): 20261015},
)
case(
    "Z2",
    "the same trade id twice in one file",
    "A trade id names one trade. Row 3 repeats row 2's id, so it is the same trade as row 2 and is "
    "not imported twice, whatever else its cells say.",
    HEAD
    + line("TCS", "2026-01-05", "buy", 2, "3000.00", 200001)
    + line("TCS", "2026-01-05", "buy", 2, "3000.00", 200001),
    [
        row(2, "new", buy(20260105, 2, 2, 30000000), None, 200001),
        row(
            3,
            "dup",
            buy(20260105, 2, 2, 30000000),
            "Row 3 is the same trade as row 2, which is in this file too.",
            200001,
        ),
    ],
    "1 new, 1 already in this tab",
)
case(
    "Z3",
    "identical trades with different trade ids are two trades",
    "Zerodha splits one order into several fills at the same price; each has its own trade id, so "
    "both import (the key is the id, not the content).",
    HEAD
    + line("TCS", "2026-01-05", "buy", 2, "3000.00", 200001)
    + line("TCS", "2026-01-05", "buy", 2, "3000.00", 200002),
    [
        row(2, "new", buy(20260105, 2, 2, 30000000), None, 200001),
        row(3, "new", buy(20260105, 2, 2, 30000000), None, 200002),
    ],
    "2 new",
)
case(
    "Z4",
    "rows that cannot be used, in words",
    "Row 2 is a futures-and-options trade (segment FO): only EQ is imported. Row 3 has no trade "
    "id. Row 4's trade type is neither buy nor sell. Row 5 has no quantity. Row 6's symbol is not "
    "in the instrument list. Row 7 is dated after 30 Oct 2026. Row 8 is fine.",
    HEAD
    + line("RELIANCE", "2026-01-05", "buy", 10, "1400.00", 300001, segment="FO")
    + line("RELIANCE", "2026-01-05", "buy", 10, "1400.00", "")
    + line("RELIANCE", "2026-01-05", "hold", 10, "1400.00", 300003)
    + line("RELIANCE", "2026-01-05", "buy", "", "1400.00", 300004)
    + line("TATAMTR", "2026-01-05", "buy", 10, "1400.00", 300005)
    + line("RELIANCE", "2026-11-02", "buy", 10, "1400.00", 300006)
    + line("RELIANCE", "2026-01-05", "buy", 1, "1400.00", 300007),
    [
        row(
            2,
            "bad",
            None,
            "Row 2: This is a FO trade. Only equity trades (segment EQ) can be imported for now.",
        ),
        row(
            3,
            "bad",
            None,
            "Row 3: This trade has no trade id, so it cannot be told from another trade.",
        ),
        row(4, "bad", None, "Row 4: The trade type 'hold' is not buy or sell."),
        row(
            5,
            "bad",
            None,
            "Row 5: The quantity '' is not a number. Write it with digits and a decimal point only, without commas or a currency sign.",
        ),
        row(6, "bad", None, "Row 6: TATAMTR is not in today's instrument list. Check the symbol."),
        row(
            7,
            "bad",
            None,
            "Row 7: 2 Nov 2026 is after today. A date after today can be saved as a draft only.",
        ),
        row(8, "new", buy(20260105, 1, 1, 14000000), None, 300007),
    ],
    "1 new, 6 with problems",
)
case(
    "Z5",
    "a sale beyond what the tab holds",
    "The tab holds nothing of TCS; a sale of 3 on 5 Jan cannot be saved, with the ledger's own "
    "sentence. Its key is kept so that the same row is recognised when it is read again.",
    HEAD + line("TCS", "2026-01-05", "sell", 3, "3000.00", 400001),
    [
        row(
            2,
            "bad",
            sell(20260105, 2, 3, 30000000),
            "Row 2: Zerodha holds no TCS on 5 Jan 2026, so a sale of 3 cannot be saved. Add the missing purchase first.",
            400001,
        )
    ],
    "1 with a problem",
)
case(
    "Z6",
    "a header written as the spreadsheet shows it, no segment column, a column of no use",
    "Header cells are read without regard to case, with spaces as underscores ('Trade Date' is "
    "trade_date). A file with no segment column is taken as equity. A column the format has no "
    "name for ('Remarks') is named after the counts so that it is not a mystery. The ISIN "
    "finds the holding when the symbol is not the instrument list's.",
    "Symbol,ISIN,Trade Date,Exchange,Trade Type,Quantity,Price,Trade ID,Remarks\n"
    "OLDNAME,INE000A00002,2026-01-05,BSE,BUY,3,3000,500001,x\n",
    [row(2, "new", buy(20260105, 2, 3, 30000000), None, 500001)],
    "1 new",
    unused=["remarks"],
)
REFUSED = [
    {
        "id": "Z7",
        "title": "a file that is not a tradebook",
        "why": "The first row lacks price and trade_id, which a tradebook always has: the whole file "
        "is refused with a sentence that names them.",
        "text": "symbol,trade_date,trade_type,quantity\nTCS,2026-01-05,buy,1\n",
        "expect": "This does not look like a Zerodha tradebook. Its first row has no 'price' and "
        "'trade_id' columns. Download the tradebook from Zerodha Console as CSV and choose "
        "that file.",
    },
    {
        "id": "Z8",
        "title": "a generic CSV is not a tradebook",
        "why": "The generic format's header has no trade_id, trade_type or trade_date.",
        "text": "date,action,symbol,quantity,price\n2026-01-05,BUY,TCS,1,3000\n",
        "expect": "This does not look like a Zerodha tradebook. Its first row has no 'trade_date', "
        "'trade_type' and 'trade_id' columns. Download the tradebook from Zerodha Console "
        "as CSV and choose that file.",
    },
]

ZERODHA = {
    "about": (
        "Golden vectors for imports/zerodha.py (PA8, Z1 and its neighbours): a tradebook's equity "
        "rows with the trade id as the duplicate key, rows that cannot be used in words, and a "
        "file that is not a tradebook. The rows are synthetic."
    ),
    "columns": [
        "symbol",
        "isin",
        "trade_date",
        "exchange",
        "segment",
        "series",
        "trade_type",
        "auction",
        "quantity",
        "price",
        "trade_id",
        "order_id",
        "order_execution_time",
    ],
    "world": WORLD,
    "previews": CASES,
    "refused": REFUSED,
}


def build() -> dict:
    """The file name and contents of every vector file this generator writes."""
    return {"import_zerodha.json": ZERODHA}


def main() -> None:
    write_all(build(), Path(__file__).parent)


if __name__ == "__main__":
    main()
