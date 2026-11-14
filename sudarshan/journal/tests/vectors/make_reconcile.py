"""Writes reconcile.json: the contract of ``core/reconcile.py`` (design p7 4.6, M7.42).
Run: python sudarshan/journal/tests/vectors/make_reconcile.py

Every expected figure is worked by hand in the comment beside it; nothing here imports the code
under test. Money is paise; a percentage is text with two decimals.
"""

from __future__ import annotations

import json
from pathlib import Path

# A month of two option orders (charges.json C8): the journal's estimate, part by part.
EST = {"brokerage": 6000, "stt": 625, "exchange": 665, "sebi": 2, "stamp": 27, "gst": 1200,
       "total": 8519}  # fmt: skip

SEGMENTS = [
    {
        "id": "R1",
        "note": "Upstox charged Rs 20 an order where the plan says Rs 30: flagged, brokerage named",
        "segment": "FO",
        "estimate": EST,
        "orders": 2,
        "modelled": True,
        "actual": {
            "brokerage": 4000,
            "gst": 850,
            "stt": 625,
            "stamp": 27,
            "transaction": 665,
            "clearing": 10,
            "ipft": 3,
            "sebi": 2,
            "dp": None,
            "others": 7,
        },
        "actual_total": 6189,
        # diff = 6189 - 8519 = -2330; -2330 / 6189 = -37.65%; above 5%: flagged.
        # brokerage 4000 - 6000 = -2000 (the largest); gst 850 - 1200 = -350;
        # exchange 665 + 10 + 3 = 678 against 665 = +13; unestimated: others 7.
        "expect": {
            "estimate_paise": 8519,
            "actual_paise": 6189,
            "diff_paise": -2330,
            "diff_pct": "-37.65",
            "flagged": True,
            "unestimated_paise": 7,
            "parts": [
                ["brokerage", 6000, 4000, -2000],
                ["stt", 625, 625, 0],
                ["exchange", 665, 678, 13],
                ["sebi", 2, 2, 0],
                ["stamp", 27, 27, 0],
                ["gst", 1200, 850, -350],
            ],
            "words": "Brokerage is ₹20.00 below the estimate: check your plan under Reports > "
            "Settings.",
        },
    },  # fmt: skip
    {
        "id": "R2-at-5",
        "note": "exactly 5% off is not flagged, and says nothing",
        "segment": "FO",
        "estimate": {"brokerage": 9500, "total": 9500},
        "orders": 3,
        "modelled": True,
        "actual": {"brokerage": 10000},
        "actual_total": 10000,
        # 10000 - 9500 = 500 = 5.00% of 10000: not above 5%.
        "expect": {
            "estimate_paise": 9500,
            "actual_paise": 10000,
            "diff_paise": 500,
            "diff_pct": "5.00",
            "flagged": False,
            "unestimated_paise": 0,
            "parts": [["brokerage", 9500, 10000, 500]],
            "words": None,
        },
    },  # fmt: skip
    {
        "id": "R2-over-5",
        "note": "one paisa more than 5%: flagged; Upstox charged more",
        "segment": "FO",
        "estimate": {"brokerage": 9499, "total": 9499},
        "orders": 3,
        "modelled": True,
        "actual": {"brokerage": 10000},
        "actual_total": 10000,
        # 501 / 10000 = 5.01%.
        "expect": {
            "estimate_paise": 9499,
            "actual_paise": 10000,
            "diff_paise": 501,
            "diff_pct": "5.01",
            "flagged": True,
            "unestimated_paise": 0,
            "parts": [["brokerage", 9499, 10000, 501]],
            "words": "Brokerage is ₹5.01 above the estimate: check your plan under "
            "Reports > Settings.",
        },
    },  # fmt: skip
    {
        "id": "R3-not-read",
        "note": "the month's row is not read yet: no actual, no flag",
        "segment": "FO",
        "estimate": EST,
        "orders": 2,
        "modelled": True,
        "actual": None,
        "actual_total": None,
        "waiting": "Upstox's charges for this month are not read yet.",
        "expect": {
            "estimate_paise": 8519,
            "actual_paise": None,
            "diff_paise": None,
            "diff_pct": None,
            "flagged": False,
            "unestimated_paise": 0,
            "parts": [],
            "words": "Upstox's charges for this month are not read yet.",
        },
    },  # fmt: skip
    {
        "id": "R4-not-modelled",
        "note": "a commodity month: nothing estimated, so never flagged",
        "segment": "COM",
        "estimate": {"total": 0},
        "orders": 4,
        "modelled": False,
        "actual": {"brokerage": 12000, "transaction": 800},
        "actual_total": 12800,
        "expect": {
            "estimate_paise": 0,
            "actual_paise": 12800,
            "diff_paise": None,
            "diff_pct": None,
            "flagged": False,
            "unestimated_paise": 0,
            "parts": [],
            "words": "Charges on this exchange are not estimated, so there is nothing "
            "to compare; Upstox's own figure is shown.",
        },
    },  # fmt: skip
    {
        "id": "R5-depository",
        "note": "delivery sales: every estimated part agrees and the depository's charge is "
        "the whole gap",
        "segment": "EQ",
        "estimate": {"brokerage": 6000, "stt": 2000, "gst": 1080, "total": 9080},
        "orders": 2,
        "modelled": True,
        "actual": {
            "brokerage": 6000,
            "stt": 2000,
            "gst": 1080,
            "dp": 3186,
            "others": None,
            "transaction": None,
        },
        "actual_total": 12266,
        # 12266 - 9080 = 3186; 3186 / 12266 = 25.97%. The exchange part is not compared:
        # Upstox left the transaction charge empty.
        "expect": {
            "estimate_paise": 9080,
            "actual_paise": 12266,
            "diff_paise": 3186,
            "diff_pct": "25.97",
            "flagged": True,
            "unestimated_paise": 3186,
            "parts": [["brokerage", 6000, 6000, 0], ["stt", 2000, 2000, 0], ["gst", 1080, 1080, 0]],
            "words": "₹31.86 of Upstox's charges are ones the estimate has no figure "
            "for (the depository's charge on shares sold from your demat account, and "
            "others).",
        },
    },  # fmt: skip
    {
        "id": "R6-stt",
        "note": "an STT row out of date: the sale paid 0.15% where the card said 0.1%",
        "segment": "FO",
        "estimate": {"brokerage": 3000, "stt": 1000, "total": 4000},
        "orders": 1,
        "modelled": True,
        "actual": {"brokerage": 3000, "stt": 1500},
        "actual_total": 4500,
        # 500 / 4500 = 11.11%.
        "expect": {
            "estimate_paise": 4000,
            "actual_paise": 4500,
            "diff_paise": 500,
            "diff_pct": "11.11",
            "flagged": True,
            "unestimated_paise": 0,
            "parts": [["brokerage", 3000, 3000, 0], ["stt", 1000, 1500, 500]],
            "words": "STT is ₹5.00 above the estimate: the rate card's STT row may be out of date.",
        },
    },  # fmt: skip
]

MONTHS = [
    {
        "id": "M1",
        "note": "two segments, both read: R1's and R2-over-5's rows; the month is 8519 + 9499 "
        "= 18018 against 6189 + 10000 = 16189",
        "segments": ["R1", "R2-over-5"],
        # 16189 - 18018 = -1829; -1829 / 16189 = -11.30%: flagged; the sentence is the worse
        # segment's (R1: 2330 against 501).
        "expect": {
            "orders": 5,
            "estimate_paise": 18018,
            "actual_paise": 16189,
            "diff_paise": -1829,
            "diff_pct": "-11.30",
            "flagged": True,
            "words": "Brokerage is ₹20.00 below the estimate: check your plan under "
            "Reports > Settings.",
        },
    },  # fmt: skip
    {
        "id": "M2",
        "note": "one segment of the month is not read yet: the month has no actual",
        "segments": ["R1", "R3-not-read"],
        "expect": {
            "orders": 4,
            "estimate_paise": 17038,
            "actual_paise": None,
            "diff_paise": None,
            "diff_pct": None,
            "flagged": False,
            "words": "Upstox's charges for this month are not read yet.",
        },
    },  # fmt: skip
    {
        "id": "M3",
        "note": "a commodity segment beside an estimated one: the month's actual holds both, "
        "the flag and the percentage rest on the estimated one alone (R2-at-5: 5.00%, quiet)",
        "segments": ["R2-at-5", "R4-not-modelled"],
        # actual 10000 + 12800 = 22800; estimate 9500 + 0; the whole difference 13300.
        "expect": {
            "orders": 7,
            "estimate_paise": 9500,
            "actual_paise": 22800,
            "diff_paise": 13300,
            "diff_pct": "5.00",
            "flagged": False,
            "words": None,
        },
    },  # fmt: skip
    {
        "id": "M4",
        "note": "a month with no orders: nothing to compare",
        "segments": [],
        "expect": {
            "orders": 0,
            "estimate_paise": 0,
            "actual_paise": None,
            "diff_paise": None,
            "diff_pct": None,
            "flagged": False,
            "words": None,
        },
    },  # fmt: skip
]

# The year: the months that have an actual. M1 and M3: estimate 18018 + 9500 = 27518, actual
# 16189 + 22800 = 38989; 38989 - 27518 = 11471; 11471 / 38989 = 29.42%.
YEAR = {"months": ["M1", "M2", "M3", "M4"],
        "expect": {"months": 2, "estimate_paise": 27518, "actual_paise": 38989,
                   "diff_paise": 11471, "diff_pct": "29.42", "flagged": True}}  # fmt: skip

if __name__ == "__main__":
    out = {"note": "synthetic; estimate against Upstox's actual; paise",
           "segments": SEGMENTS, "months": MONTHS, "year": YEAR}  # fmt: skip
    Path(__file__).with_name("reconcile.json").write_text(
        json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
