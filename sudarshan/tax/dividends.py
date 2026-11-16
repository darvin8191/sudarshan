"""Dividends (M7.13, M7.29): what a holding is expected to be paid, what it was paid, and when a
company is likely to deduct tax from it.

A dividend goes to whoever holds the share at the close of the day before the ex-date: a purchase
on the ex-date itself earns nothing. What is expected is that quantity times the rupees a share.
"Credited" is the owner's own word for it: he confirms a line with what reached his bank and the
tax deducted from it, and the dividend is the two together; a line he marks as not received
counts as nothing; until then the expected amount stands in.

A company deducts tax when what it pays one holder in a financial year is above a threshold
(``rules.DIVIDEND_TDS``, a dated row marked verify): the test is per payer, never on the year's
total over every payer. The flag says "likely" and an amount "about": the company decides on its
own records of the holder, which can differ from one account's.

The amounts are taxed at slab rates, which this package never works out; the working paper lists
the year's dividends and stops there.
"""

from __future__ import annotations

from . import rules
from .units import bp_of, fy_first, fy_of, value_paise

STATES = ("expected", "confirmed", "not_received")


def entitled_qty_u(changes, ex_day: int) -> int:
    """The quantity a dividend is paid on: what was held at the close of the day before the
    ex-date. ``changes`` are ``(day, qty_u change)`` of one holding, a purchase positive and a
    sale negative; a change on the ex-date or after it does not count. Never below zero."""
    return max(0, sum(change for day, change in changes if day < ex_day))


def expected_paise(qty_u: int, amount_u: int) -> int:
    """Quantity times rupees a share (``amount_u``, x 10^4), in paise, half away from zero."""
    return value_paise(qty_u, amount_u)


def counted_paise(line: dict) -> int:
    """What a line counts as income: the credit and the tax deducted from it once confirmed,
    nothing when marked as not received, and what was expected until the owner says."""
    if line["state"] == "confirmed":
        return (line.get("credited_paise") or 0) + (line.get("tds_paise") or 0)
    return 0 if line["state"] == "not_received" else line["expected_paise"]


#: Why a dividend could not be counted, as the owner reads it beside the company and the date.
NO_RECORD = (
    "No holdings record reaches the day before this ex-date, so the amount is not known: "
    "confirm what reached your bank on the Dividends card"
)
NO_AMOUNT = (
    "Upstox lists no amount for this dividend, so it is not known: confirm what reached your "
    "bank on the Dividends card"
)


def uncounted(line: dict) -> str | None:
    """The sentence of a line that counts for nothing because it cannot be worked out: still
    expected, with no expected amount. Without a quantity, no record says what was held;
    with one, the amount a share is missing. None for every line that counts. Once the owner
    confirms it (or says it was not received) it is a line like any other."""
    if line["state"] != "expected" or line["expected_paise"]:
        return None
    return NO_AMOUNT if line.get("qty_u") else NO_RECORD


def first_year() -> int:
    """The first financial year the threshold rule has a row for; none is worked out before."""
    return min(fy_of(row[0]) for row in rules.DIVIDEND_TDS)


def no_year() -> dict:
    """``year`` of a year before the first rule row: nothing, and no threshold."""
    return {"payers": [], "total_paise": 0, "tds_expected_paise": 0, "unconfirmed_count": 0,
            "tds_threshold_paise": None, "uncounted": [], "uncounted_count": 0}  # fmt: skip


def year(lines, fy: int, names: dict | None = None) -> dict:
    """One taxpayer's dividend lines of a financial year, by payer (the ISIN) in ISIN order, with
    the likely-TDS flag per payer and the year's totals. Lines of other years are passed over.
    A line that cannot be counted (``uncounted``) is in no payer and no total: it is listed
    apart with its sentence, and ``uncounted_count`` says how many there are."""
    rule = rules.at(rules.DIVIDEND_TDS, fy_first(fy))
    payers: dict[str, dict] = {}
    left_out = []
    for line in lines:
        if line["fy"] != fy:
            continue
        why = uncounted(line)
        if why is not None:
            left_out.append({**line, "name": (names or {}).get(line["isin"]), "words": why})
            continue
        payer = payers.setdefault(line["isin"], {
            "isin": line["isin"], "name": (names or {}).get(line["isin"]), "lines": [],
            "expected_paise": 0, "confirmed_paise": 0, "total_paise": 0, "unconfirmed_count": 0,
        })  # fmt: skip
        payer["lines"].append(line)
        payer["expected_paise"] += line["expected_paise"]
        payer["total_paise"] += counted_paise(line)
        if line["state"] == "confirmed":
            payer["confirmed_paise"] += counted_paise(line)
        payer["unconfirmed_count"] += line["state"] == "expected"
    for payer in payers.values():
        payer["tds_likely"] = payer["total_paise"] > rule["threshold"]
        payer["tds_expected_paise"] = (
            bp_of(payer["total_paise"], rule["rate_bp"]) if payer["tds_likely"] else None
        )
    found = [payers[isin] for isin in sorted(payers)]
    return {
        "payers": found,
        "total_paise": sum(p["total_paise"] for p in found),
        "tds_expected_paise": sum(p["tds_expected_paise"] or 0 for p in found),
        "unconfirmed_count": sum(p["unconfirmed_count"] for p in found),
        "tds_threshold_paise": rule["threshold"],
        "uncounted": sorted(left_out, key=lambda line: (line["ex_day"], line["isin"])),
        "uncounted_count": len(left_out),
    }


def block(of_year: dict, buyback_paise: int = 0) -> dict:
    """The working paper's dividends block from ``year``'s answer: the payers without their
    lines, and a buyback's receipt taxed as a dividend (the pack's own figure) beside them and
    inside the total. The dividends that could not be counted are listed with the company, the
    ex-date and why; ``note`` is for the caller (whether the list was read at all)."""
    keep = ("isin", "name", "expected_paise", "confirmed_paise", "total_paise", "tds_likely")
    return {
        "by_payer": [{key: payer[key] for key in keep} for payer in of_year["payers"]],
        "buyback_paise": buyback_paise,
        "total_paise": of_year["total_paise"] + buyback_paise,
        "tds_expected_paise": of_year["tds_expected_paise"],
        "unconfirmed_count": of_year["unconfirmed_count"],
        "uncounted": [
            {key: line.get(key) for key in ("isin", "name", "ex_day", "words")}
            for line in of_year.get("uncounted", ())
        ],
        "uncounted_count": of_year.get("uncounted_count", 0),
        "note": None,
    }
