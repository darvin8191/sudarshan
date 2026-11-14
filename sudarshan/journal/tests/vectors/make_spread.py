"""Writes spread.json: the contract of ``core/spread.py`` (design p7 section 5, C1 to C4, and
the cases beside them). Run: python sudarshan/journal/tests/vectors/make_spread.py

Every expected figure below is worked by hand in the comment beside it; nothing here imports
the code under test. Money is paise.
"""

from __future__ import annotations

import json
from pathlib import Path

# The two orders of charges.json's C8 (an option bought and sold): the journal's estimate.
BUY = {"brokerage": 3000, "stt": 0, "exchange": 315, "sebi": 1, "stamp": 27, "gst": 597,
       "total": 3940}  # fmt: skip
SELL = {"brokerage": 3000, "stt": 625, "exchange": 350, "sebi": 1, "stamp": 0, "gst": 603,
        "total": 4579}  # fmt: skip


def order(key, estimate=None, turnover=0, dp_group=None):
    return {"key": key, "estimate": estimate or {}, "turnover": turnover, "dp_group": dp_group}


SPREAD = [
    {
        "id": "C1",
        "note": "one part, 153.20, three orders estimated at 40.00, 60.00 and 50.00",
        "actual": {"stt": 15320},
        "orders": [order("A", {"stt": 4000}), order("B", {"stt": 6000}), order("C", {"stt": 5000})],
        # 15320 x 4000 / 15000 = 4085.33; x 6000 = 6128.00; x 5000 = 5106.67.
        # Floors 4085 + 6128 + 5106 = 15319; the one paisa left goes to the largest fraction (.67).
        "expect": {"A": {"stt": 4085}, "B": {"stt": 6128}, "C": {"stt": 5107}},
        "missing": [],
        "partial": False,
    },
    {
        "id": "C1-tie",
        "note": "1.01 between two orders of equal weight: the paisa left goes to the lower key",
        "actual": {"brokerage": 101},
        "orders": [order("B", {"brokerage": 3000}), order("A", {"brokerage": 3000})],
        # 50.5 each; floors 50 + 50; the tie goes to A.
        "expect": {"A": {"brokerage": 51}, "B": {"brokerage": 50}},
        "missing": [],
        "partial": False,
    },
    {
        "id": "C1-day",
        "note": "every part of a day over a buy and a sell order",
        "actual": {
            "brokerage": 4000,
            "gst": 850,
            "stt": 625,
            "stamp": 27,
            "transaction": 665,
            "clearing": 10,
            "ipft": 3,
            "sebi": 2,
            "others": 7,
        },  # fmt: skip
        "orders": [order("P1", BUY, 900000), order("P2", SELL, 1000000)],
        "expect": {
            # brokerage 4000 on 3000 : 3000 -> 2000, 2000
            # gst 850 on 597 : 603 (1200): 422.875, 427.125 -> 422, 427, one left to .875 -> 423, 427
            # stt 625 on 0 : 625 -> 0, 625.  stamp 27 on 27 : 0 -> 27, 0
            # transaction 665 on 315 : 350 (665) -> 315, 350
            # clearing 10 on 315 : 350: 4.737, 5.263 -> 4, 5, one left to .737 -> 5, 5
            # ipft 3 on 315 : 350: 1.421, 1.579 -> 1, 1, one left to .579 -> 1, 2
            # sebi 2 on 1 : 1 -> 1, 1
            # others 7 on totals 3940 : 4579 (8519): 3.2375, 3.7625 -> 3, 3, one left -> 3, 4
            "P1": {
                "brokerage": 2000,
                "gst": 423,
                "stt": 0,
                "stamp": 27,
                "transaction": 315,
                "clearing": 5,
                "ipft": 1,
                "sebi": 1,
                "others": 3,
            },  # fmt: skip
            "P2": {
                "brokerage": 2000,
                "gst": 427,
                "stt": 625,
                "stamp": 0,
                "transaction": 350,
                "clearing": 5,
                "ipft": 2,
                "sebi": 1,
                "others": 4,
            },  # fmt: skip
        },
        # P1 2775 + P2 3414 = 6189 = 4000 + 850 + 625 + 27 + 665 + 10 + 3 + 2 + 7.
        "totals": {"P1": 2775, "P2": 3414},
        "missing": [],
        "partial": False,
    },
    {
        "id": "C3",
        "note": "every weight zero (two sell orders pay no stamp by the card): turnover weighs",
        "actual": {"stamp": 30},
        "orders": [order("A", SELL, 900000), order("B", SELL, 1000000)],
        # 30 x 9 / 19 = 14.21; 30 x 10 / 19 = 15.79; floors 14 + 15; one left to .79.
        "expect": {"A": {"stamp": 14}, "B": {"stamp": 16}},
        "missing": [],
        "partial": False,
    },
    {
        "id": "C3-unmodelled",
        "note": "a segment the card does not model (no estimate at all) still gets its actual",
        "actual": {"brokerage": 4000, "stt": 999},
        "orders": [order("A", None, 250000), order("B", None, 750000)],
        # brokerage 1000, 3000; stt 249.75, 749.25 -> 249, 749, one left to .75 -> 250, 749.
        "expect": {"A": {"brokerage": 1000, "stt": 250}, "B": {"brokerage": 3000, "stt": 749}},
        "missing": [],
        "partial": False,
    },
    {
        "id": "C3-nothing",
        "note": "no estimate and no turnover: the orders weigh the same",
        "actual": {"others": 5},
        "orders": [order("A"), order("B")],
        # 2.5 each; floors 2 + 2; the tie goes to A.
        "expect": {"A": {"others": 3}, "B": {"others": 2}},
        "missing": [],
        "partial": False,
    },
    {
        "id": "C4",
        "note": "a part Upstox left empty is skipped, never 0, and the day is partial",
        "actual": {"brokerage": 6000, "stt": None, "gst": 1080, "clearing": None},
        "orders": [order("P1", BUY, 900000), order("P2", SELL, 1000000)],
        # gst 1080 on 597 : 603: 537.3, 542.7 -> 537, 542, one left to .7 -> 537, 543.
        "expect": {"P1": {"brokerage": 3000, "gst": 537}, "P2": {"brokerage": 3000, "gst": 543}},
        "missing": ["stt", "clearing"],
        "partial": True,
    },
    {
        "id": "C4-extras",
        "note": "an empty clearing charge alone does not make the day partial, and the "
        "depository's charge is only missed on a day with a delivery sale",
        "actual": {"brokerage": 6000, "clearing": None, "dp": None},
        "orders": [order("P1", BUY, 900000), order("P2", SELL, 1000000)],
        "expect": {"P1": {"brokerage": 3000}, "P2": {"brokerage": 3000}},
        "missing": ["clearing", "dp"],
        "partial": False,
    },
    {
        "id": "C4-dp-missed",
        "note": "the depository's charge left empty on a day with a delivery sale: partial",
        "actual": {"brokerage": 3000, "dp": None},
        "orders": [order("S", SELL, 1000000, "INE000A01010")],
        "expect": {"S": {"brokerage": 3000}},
        "missing": ["dp"],
        "partial": True,
    },
    {
        "id": "C5-dp",
        "note": "the depository's charge: once a share sold, so two shares weigh the same and "
        "the two sell orders of one share halve its weight; a buy order takes none",
        "actual": {"dp": 3186},
        "orders": [
            order("A", {"brokerage": 3000}, 100, "INE000A01010"),
            order("B", {"brokerage": 3000}, 900000, "INE000A01010"),
            order("C", {"brokerage": 3000}, 50, "INE000B01018"),
            order("D", {"brokerage": 3000}, 5000000),
        ],
        # Weights 1/2, 1/2, 1, 0 (sum 2): 796.5, 796.5, 1593, 0; floors 796 + 796 + 1593 = 3185;
        # the tie for the paisa left goes to A.
        "expect": {"A": {"dp": 797}, "B": {"dp": 796}, "C": {"dp": 1593}, "D": {"dp": 0}},
        "missing": [],
        "partial": False,
    },
    {
        "id": "C6-negative",
        "note": "a part below zero (a reversal) is shared the same way and keeps its sign",
        "actual": {"others": -7, "unlisted": 5},
        "orders": [order("P1", BUY, 900000), order("P2", SELL, 1000000)],
        # others: 7 shared 3, 4 (as in C1-day), signed. unlisted 5 on 3940 : 4579: 2.31, 2.69
        # -> 2, 2, one left to .69 -> 2, 3.
        "expect": {"P1": {"others": -3, "unlisted": 2}, "P2": {"others": -4, "unlisted": 3}},
        "missing": [],
        "partial": False,
    },
]

TRIPS = [
    {
        "id": "C2",
        "note": "a flip: 130 sold against a long of 65, so the order's fills sit in two trades, "
        "65 each",
        "parts": {"brokerage": 3000, "stt": 1463},
        "holders": {"7": 65, "8": 65},
        # brokerage 1500 each; stt 731.5 each -> 731 + 731, the tie goes to the lower trade.
        "expect": {"7": {"brokerage": 1500, "stt": 732}, "8": {"brokerage": 1500, "stt": 731}},
    },
    {
        "id": "C2-uneven",
        "note": "100 of the order in one trade and 30 in the next",
        "parts": {"gst": 101},
        "holders": {"7": 100, "8": 30},
        # 101 x 100 / 130 = 77.69; x 30 = 23.31; floors 77 + 23; one left to .69.
        "expect": {"7": {"gst": 78}, "8": {"gst": 23}},
    },
    {
        "id": "C2-one",
        "note": "an order wholly in one trade gives it everything",
        "parts": {"brokerage": 2000, "others": -3},
        "holders": {"7": 65},
        "expect": {"7": {"brokerage": 2000, "others": -3}},
    },
]

if __name__ == "__main__":
    out = {
        "note": "synthetic; Upstox's actual charges shared among orders and trades; paise",
        "spread": SPREAD,
        "to_trips": TRIPS,
    }
    Path(__file__).with_name("spread.json").write_text(
        json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
