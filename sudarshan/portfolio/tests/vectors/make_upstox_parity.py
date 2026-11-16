"""Generator of upstox_parity.json (M7.15): the Upstox tab's bars and the return of its shares.

Imports nothing from sudarshan. Every case is small enough to work out on paper, and its id says
the arithmetic. Units: qty_u is shares x 1,000,000, price_u rupees x 10,000, money paise, so
10 shares at 110 rupees are 10,000,000 x 1,100,000 / 100,000,000 = 110,000 paise.

* allocation: a bar's share is its value over the whole, to four decimals, half away from zero,
  each on its own; largest first, equal bars by label. Lines are [key, value, class, sector].
* counted: trades are [day, isin, side, qty_u, amount]; held [isin, qty_u, price_u]; actions
  [day, isin, kind, num, den]; flows [day, in, out]. Units held from before the history are
  held less (bought less sold, through the bonuses and splits); a net sale uses them up first,
  and that part of its money, by quantity, is left out.
* points: a closing record's value, and the share trades of each day from the first record on;
  a day whose net quantity of some share changed has no value of its own.

Run: python3 make_upstox_parity.py
"""

from pathlib import Path

from portfolio_vector_tools import write_all

NOTE = (
    "M7.15, worked out by hand in make_upstox_parity.py. qty_u is shares x 1,000,000, price_u "
    "rupees x 10,000, money paise."
)

ALLOCATION = [
    {
        "id": "A1 two classified, one not: 60,000 + 30,000 + 10,000 paise",
        "lines": [
            ["k1", 60000, "equity", "IT"],
            ["k2", 30000, "equity", None],
            ["k3", 10000, None, None],
        ],
        "expect": {
            "class": [
                {
                    "key": "equity",
                    "label": "Equity",
                    "value_paise": 90000,
                    "pct": "90.0000",
                    "holdings": 2,
                },
                {
                    "key": "",
                    "label": "Not classified",
                    "value_paise": 10000,
                    "pct": "10.0000",
                    "holdings": 1,
                },
            ],
            "sector": [
                {"key": "IT", "label": "IT", "value_paise": 60000, "pct": "60.0000", "holdings": 1},
                {
                    "key": "",
                    "label": "No sector set",
                    "value_paise": 40000,
                    "pct": "40.0000",
                    "holdings": 2,
                },
            ],
        },
    },
    {
        "id": "A2 thirds round each on its own: 1 of 3 is 33.3333, and equal bars go by label",
        "lines": [
            ["k1", 100, "equity", "Banks"],
            ["k2", 100, "equity", "Autos"],
            ["k3", 100, "gold", "Chemicals"],
        ],
        "expect": {
            "class": [
                {
                    "key": "equity",
                    "label": "Equity",
                    "value_paise": 200,
                    "pct": "66.6667",
                    "holdings": 2,
                },
                {
                    "key": "gold",
                    "label": "Gold and silver",
                    "value_paise": 100,
                    "pct": "33.3333",
                    "holdings": 1,
                },
            ],
            "sector": [
                {
                    "key": "Autos",
                    "label": "Autos",
                    "value_paise": 100,
                    "pct": "33.3333",
                    "holdings": 1,
                },
                {
                    "key": "Banks",
                    "label": "Banks",
                    "value_paise": 100,
                    "pct": "33.3333",
                    "holdings": 1,
                },
                {
                    "key": "Chemicals",
                    "label": "Chemicals",
                    "value_paise": 100,
                    "pct": "33.3333",
                    "holdings": 1,
                },
            ],
        },
    },
    {"id": "A3 nothing held: no bar", "lines": [], "expect": {"class": [], "sector": []}},
]

COUNTED = [
    {
        "id": "C1 bought in the history, all counted: 10 at 100 becomes 10 at 110 a year on, 10% a year",
        "trades": [[20250401, "INE000A00001", "BUY", 10000000, 100000]],
        "held": [["INE000A00001", 10000000, 1100000]],
        "actions": [],
        "through": 20260401,
        "expect": {
            "flows": [[20250401, 100000, 0]],
            "value_paise": 110000,
            "uncounted": [],
            "gone": [],
            "left_out_paise": 0,
            "unmatched": 0,
        },
        "summary": {
            "valuation_day": 20260401,
            "xirr_state": "shown",
            "xirr_pct": "10.0000",
            "absolute_return_pct": "10.0000",
            "total_gain_paise": 10000,
        },
    },
    {
        "id": "C2 30 held, 10 bought in the history: the other 20 are not counted, never cost 0",
        "trades": [[20250601, "INE000A00001", "BUY", 10000000, 40000]],
        "held": [["INE000A00001", 30000000, 500000]],
        "actions": [],
        "through": 20251001,
        "expect": {
            "flows": [[20250601, 40000, 0]],
            "value_paise": 50000,
            "uncounted": [["INE000A00001", 20000000, 100000]],
            "gone": [],
            "left_out_paise": 0,
            "unmatched": 0,
        },
        "summary": {
            "valuation_day": 20251001,
            "xirr_state": "later",
            "xirr_pct": None,
            "absolute_return_pct": "25.0000",
            "total_gain_paise": 10000,
        },
    },
    {
        "id": "C3 a sale of shares held from before is not money out: sold 10 old, then bought 5",
        "trades": [
            [20250701, "INE000A00001", "SELL", 10000000, 150000],
            [20250801, "INE000A00001", "BUY", 5000000, 90000],
        ],
        "held": [["INE000A00001", 5000000, 2000000]],
        "actions": [],
        "through": 20251001,
        "expect": {
            "flows": [[20250801, 90000, 0]],
            "value_paise": 100000,
            "uncounted": [],
            "gone": [],
            "left_out_paise": 150000,
            "unmatched": 0,
        },
    },
    {
        "id": "C4 a sale that uses up the old 6 and 2 of the 6 bought: a quarter of its money counts",
        "trades": [
            [20250501, "INE000A00001", "BUY", 6000000, 60000],
            [20250901, "INE000A00001", "SELL", 8000000, 120000],
        ],
        "held": [["INE000A00001", 4000000, 1500000]],
        "actions": [],
        "through": 20251001,
        "expect": {
            "flows": [[20250501, 60000, 0], [20250901, 0, 30000]],
            "value_paise": 60000,
            "uncounted": [],
            "gone": [],
            "left_out_paise": 90000,
            "unmatched": 0,
        },
    },
    {
        "id": "C5 a split the owner applied: 10 bought become 20, all counted",
        "trades": [[20250501, "INE000A00001", "BUY", 10000000, 1000000]],
        "held": [["INE000A00001", 20000000, 6000000]],
        "actions": [[20251001, "INE000A00001", "SPLIT", 2, 1]],
        "through": 20251101,
        "expect": {
            "flows": [[20250501, 1000000, 0]],
            "value_paise": 1200000,
            "uncounted": [],
            "gone": [],
            "left_out_paise": 0,
            "unmatched": 0,
        },
    },
    {
        "id": "C6 the same split not applied: the 10 new shares are not counted, and said so",
        "trades": [[20250501, "INE000A00001", "BUY", 10000000, 1000000]],
        "held": [["INE000A00001", 20000000, 6000000]],
        "actions": [],
        "through": 20251101,
        "expect": {
            "flows": [[20250501, 1000000, 0]],
            "value_paise": 600000,
            "uncounted": [["INE000A00001", 10000000, 600000]],
            "gone": [],
            "left_out_paise": 0,
            "unmatched": 0,
        },
    },
    {
        "id": "C7 a bonus of 1 for 2 applied to 5 old and 10 bought: 7.5 old and 15 counted",
        "trades": [[20250501, "INE000A00001", "BUY", 10000000, 100000]],
        "held": [["INE000A00001", 22500000, 100000]],
        "actions": [[20250901, "INE000A00001", "BONUS", 1, 2]],
        "through": 20251101,
        "expect": {
            "flows": [[20250501, 100000, 0]],
            "value_paise": 15000,
            "uncounted": [["INE000A00001", 7500000, 7500]],
            "gone": [],
            "left_out_paise": 0,
            "unmatched": 0,
        },
    },
    {
        "id": "C8 more bought than held and no sale: 6 are gone, only the 4 held are valued",
        "trades": [[20250501, "INE000A00001", "BUY", 10000000, 100000]],
        "held": [["INE000A00001", 4000000, 1000000]],
        "actions": [],
        "through": 20251101,
        "expect": {
            "flows": [[20250501, 100000, 0]],
            "value_paise": 40000,
            "uncounted": [],
            "gone": [["INE000A00001", 6000000]],
            "left_out_paise": 0,
            "unmatched": 0,
        },
    },
    {
        "id": "C9 a round trip in a day counts both ways, a trade without an ISIN is left out, a later "
        "trade waits",
        "trades": [
            [20250601, "INE000B00002", "BUY", 5000000, 50000],
            [20250601, "INE000B00002", "SELL", 5000000, 52000],
            [20250602, None, "BUY", 1000000, 9999],
            [20251102, "INE000B00002", "BUY", 1000000, 11000],
        ],
        "held": [],
        "actions": [],
        "through": 20251101,
        "expect": {
            "flows": [[20250601, 50000, 52000]],
            "value_paise": 0,
            "uncounted": [],
            "gone": [],
            "left_out_paise": 0,
            "unmatched": 1,
        },
    },
    {
        "id": "C10 two shares: one bought and sold out, one held from before with nothing bought",
        "trades": [
            [20250410, "INE000A00001", "BUY", 2000000, 20000],
            [20250510, "INE000A00001", "SELL", 2000000, 26000],
        ],
        "held": [["INE000C00003", 3000000, 700000]],
        "actions": [],
        "through": 20251101,
        "expect": {
            "flows": [[20250410, 20000, 0], [20250510, 0, 26000]],
            "value_paise": 0,
            "uncounted": [["INE000C00003", 3000000, 21000]],
            "gone": [],
            "left_out_paise": 0,
            "unmatched": 0,
        },
    },
]

POINTS = [
    {
        "id": "P1 a delivery purchase day has no value of its own: 126,000 on 100,000 + 20,000 is 5%",
        "records": [[20261102, 100000], [20261103, 103000], [20261104, 126000]],
        "trades": [[20261103, "INE000A00001", "BUY", 1000000, 20000]],
        "expect": [[20261102, 100000, 0, 0], [20261103, None, 20000, 0], [20261104, 126000, 0, 0]],
        "twr_pct": "5.0000",
        "twr_start": 20261102,
    },
    {
        "id": "P2 a round trip in a day leaves the record its value; a day not read has none; trades "
        "before the first record are not part of it",
        "records": [[20261102, 100000], [20261103, 110000], [20261104, None], [20261105, 121000]],
        "trades": [
            [20261030, "INE000A00001", "BUY", 1000000, 5000],
            [20261103, "INE000B00002", "BUY", 1000000, 10000],
            [20261103, "INE000B00002", "SELL", 1000000, 11000],
        ],
        "expect": [
            [20261102, 100000, 0, 0],
            [20261103, 110000, 10000, 11000],
            [20261104, None, 0, 0],
            [20261105, 121000, 0, 0],
        ],
        "twr_pct": "21.0000",
        "twr_start": 20261102,
    },
]

OUT = {"note": NOTE, "allocation": ALLOCATION, "counted": COUNTED, "points": POINTS}


def build() -> dict:
    return {"upstox_parity.json": OUT}


def main() -> None:
    write_all(build(), Path(__file__).parent)


if __name__ == "__main__":
    main()
