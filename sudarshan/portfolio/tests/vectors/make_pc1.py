"""Generator of the PC1 golden vectors: corpactions.json, Upstox's corporate actions as integers.

The rows are built from the fields Upstox documents for
``GET /v2/fundamentals/{isin}/corporate-actions`` (``name``, ``expiry_date``, ``amount``,
``ratio``, ``event_details``); the first is the page's own sample, a final dividend of Rs 5.50
with the ex-date 14 Aug 2025 (the labels inside ``event_details`` are as the page describes them,
not copied). No row here came from the network, and no ISIN is a real company's.

Every expected value is typed by hand from the rule in the case's ``why``; nothing is computed.
Units: a day is YYYYMMDD, rupees a share are ``amount_u`` (x 10^4), and the ratio is kept in the
order Upstox wrote it. A reading is ``[num, den]`` in the ledger's terms: a bonus is ``num`` new
for every ``den`` held, a split is every ``den`` become ``num``.

Run: python3 make_pc1.py
"""

from pathlib import Path

from portfolio_vector_tools import write_all

ISIN = "INE000A01010"


def details(**pairs):
    return [{"name": name.replace("_", " "), "value": value} for name, value in pairs.items()]


SAMPLE = {
    "name": "Dividend",
    "expiry_date": "14 Aug 2025",
    "amount": 5.5,
    "ratio": None,
    "event_details": details(
        Announcement_date="25 Apr 2025",
        Ex_dividend_date="14 Aug 2025",
        Record_date="14 Aug 2025",
        Dividend_type="Final",
        Dividend_percentage="55.0",
        Details="Final dividend of Rs.5.50 per share",
    ),
}
SAMPLE_DETAILS = [
    {"name": "Announcement date", "value": "25 Apr 2025"},
    {"name": "Ex dividend date", "value": "14 Aug 2025"},
    {"name": "Record date", "value": "14 Aug 2025"},
    {"name": "Dividend type", "value": "Final"},
    {"name": "Dividend percentage", "value": "55.0"},
    {"name": "Details", "value": "Final dividend of Rs.5.50 per share"},
]


def event(kind, ex_day, amount_u=None, ratio=None, detail=()):
    return {
        "isin": ISIN, "kind": kind, "ex_day": ex_day, "amount_u": amount_u,
        "ratio_num": ratio[0] if ratio else None, "ratio_den": ratio[1] if ratio else None,
        "details": list(detail),
    }  # fmt: skip


def row(case, why, raw, expect=None, skip=None):
    return {"id": case, "why": why, "raw": raw, "expect": expect, "skip": skip}


ROWS = [
    row("R-SAMPLE", "the documented sample: Rs 5.50 a share is 55000 amount_u; no ratio",
        SAMPLE, event("dividend", 20250814, 55000, None, SAMPLE_DETAILS)),
    row("R-BONUS", "a bonus 1:1 on 10 Sep 2025; the ratio is kept as written, amount none",
        {"name": "Bonus", "expiry_date": "10 Sep 2025", "amount": None, "ratio": "1:1",
         "event_details": []},
        event("bonus", 20250910, None, (1, 1))),
    row("R-SPLIT", "a split written 1:5 on 5 Jan 2026 stays (1, 5); a zero amount is no amount",
        {"name": "Split", "expiry_date": "05 Jan 2026", "amount": 0, "ratio": "1:5",
         "event_details": []},
        event("split", 20260105, None, (1, 5))),
    row("R-RIGHTS", "a rights issue 3:10 with its price of Rs 120.25 a share: 1202500 amount_u",
        {"name": "Rights", "expiry_date": "2 Feb 2026", "amount": "120.25", "ratio": "3 : 10",
         "event_details": []},
        event("rights", 20260202, 1202500, (3, 10))),
    row("R-NAMES", "the kind is the word in the name, whatever stands beside it",
        {"name": "Stock Split", "expiry_date": "5 January 2026", "ratio": "2:10"},
        event("split", 20260105, None, (2, 10))),
    row("R-ROUND", "Rs 2.00005 a share: 20000.5 amount_u rounds half away from zero to 20001",
        {"name": "DIVIDEND", "expiry_date": "2026-08-14", "amount": "2.00005"},
        event("dividend", 20260814, 20001)),
    row("R-NO-RATIO", "a bonus whose ratio cannot be read is kept, with no ratio",
        {"name": "Bonus", "expiry_date": "10 Sep 2025", "ratio": "one for one"},
        event("bonus", 20250910)),
    row("R-ZERO-RATIO", "a ratio with a zero in it is no ratio",
        {"name": "Split", "expiry_date": "10 Sep 2025", "ratio": "0:5"},
        event("split", 20250910)),
    row("R-UNKNOWN", "a name SUDARSHAN does not know is left out, never guessed",
        {"name": "Demerger", "expiry_date": "10 Sep 2025", "ratio": "1:1"}, skip="kind"),
    row("R-TWO-KINDS", "a name with two kinds in it is not guessed either",
        {"name": "Bonus and Split", "expiry_date": "10 Sep 2025"}, skip="kind"),
    row("R-BAD-DATE", "a date that cannot be read leaves the row out",
        {"name": "Dividend", "expiry_date": "to be announced", "amount": 5}, skip="date"),
    row("R-NO-DAY", "31 Feb is no day", {"name": "Dividend", "expiry_date": "31 Feb 2026"},
        skip="date"),
    row("R-NO-DATE", "no date at all", {"name": "Dividend", "amount": 5}, skip="date"),
    row("R-NOT-A-ROW", "a row that is not an object", "Dividend", skip="row"),
]  # fmt: skip

DAYS = [
    {"id": "D1", "text": "14 Aug 2025", "day": 20250814},
    {"id": "D2", "text": "1 Jan 2026", "day": 20260101},
    {"id": "D3", "text": "14-Aug-2025", "day": 20250814},
    {"id": "D4", "text": "14 August 2025", "day": 20250814},
    {"id": "D5", "text": "2025-08-14", "day": 20250814},
    {"id": "D6", "text": "2025-08-14T00:00:00+05:30", "day": 20250814},
    {"id": "D7", "text": "14-08-2025", "day": 20250814},
    {"id": "D8", "text": " 29 Feb 2024 ", "day": 20240229},
    {"id": "D9", "text": "29 Feb 2025", "day": None},
    {"id": "D10", "text": "14 Augustus 2025", "day": None},
    {"id": "D11", "text": "Aug 2025", "day": None},
    {"id": "D12", "text": "", "day": None},
    {"id": "D13", "text": None, "day": None},
    {"id": "D14", "text": 20250814, "day": None},
]

#: [kind, first, second] as Upstox wrote the ratio; the readings, the proposed one first.
READINGS = [
    {"id": "W-BONUS", "why": "bonus 1:2 is proposed as 1 new for 2 held; the other way 2 for 1",
     "in": ["bonus", 1, 2], "out": [[1, 2], [2, 1]]},
    {"id": "W-SPLIT", "why": "split 1:5 is proposed as every 1 become 5; the other way 5 become 1",
     "in": ["split", 1, 5], "out": [[5, 1], [1, 5]]},
    {"id": "W-SPLIT-10-2", "why": "a split written 10:2 is proposed as every 10 become 2 (a "
     "consolidation); the owner turns it round when it was face value 10 to 2",
     "in": ["split", 10, 2], "out": [[2, 10], [10, 2]]},
    {"id": "W-SAME", "why": "1:1 reads one way only", "in": ["bonus", 1, 1], "out": [[1, 1]]},
    {"id": "W-NONE", "why": "no ratio, no reading", "in": ["bonus", None, None], "out": []},
    {"id": "W-DIVIDEND", "why": "a dividend changes no lot", "in": ["dividend", 1, 2], "out": []},
    {"id": "W-RIGHTS", "why": "a rights issue is the owner's own purchase, not a reading",
     "in": ["rights", 3, 10], "out": []},
]  # fmt: skip

#: Two events are one when this text is the same: isin|kind|ex_day|amount_u|ratio|dividend type.
IDENTITY = [
    {"id": "I-SAMPLE", "event": event("dividend", 20250814, 55000, None, SAMPLE_DETAILS),
     "text": "INE000A01010|dividend|20250814|55000||final"},
    {"id": "I-REWORDED", "why": "another wording of the details is still the same event",
     "event": event("dividend", 20250814, 55000, None,
                    [{"name": "Dividend Type", "value": "FINAL"},
                     {"name": "Details", "value": "Final dividend"}]),
     "text": "INE000A01010|dividend|20250814|55000||final"},
    {"id": "I-SPECIAL", "why": "a special dividend of the same day and amount is another event",
     "event": event("dividend", 20250814, 55000, None,
                    [{"name": "Dividend type", "value": "Special"}]),
     "text": "INE000A01010|dividend|20250814|55000||special"},
    {"id": "I-BONUS", "event": event("bonus", 20250910, None, (1, 2)),
     "text": "INE000A01010|bonus|20250910||1:2|"},
]  # fmt: skip

ANSWER = {
    "id": "A1",
    "why": "one answer: the sample twice (kept once), a bonus, an unknown name and a bad date",
    "payload": {
        "status": "success",
        "data": [
            SAMPLE,
            dict(SAMPLE),
            {"name": "Bonus", "expiry_date": "10 Sep 2025", "ratio": "1:1"},
            {"name": "Demerger", "expiry_date": "10 Sep 2025"},
            {"name": "Dividend", "expiry_date": "soon"},
        ],
    },
    "events": [
        event("dividend", 20250814, 55000, None, SAMPLE_DETAILS),
        event("bonus", 20250910, None, (1, 1)),
    ],
    "skipped": {"kind": 1, "date": 1, "row": 0},
}

FILE = {
    "about": (
        "Golden vectors for core/corpactions (PC1, M7.36): Upstox's corporate-action rows as "
        "integers, the dates it may write, the two readings of a ratio and what makes an event "
        "the same event. Typed by hand from Upstox's documented fields; nothing from the network."
    ),
    "isin": ISIN,
    "rows": ROWS,
    "days": DAYS,
    "readings": READINGS,
    "identity": IDENTITY,
    "answer": ANSWER,
}


def build() -> dict:
    return {"corpactions.json": FILE}


def main() -> None:
    write_all(build(), Path(__file__).parent)


if __name__ == "__main__":
    main()
