"""The journal's estimate of charges against Upstox's actual, month by month (P7 J4, M7.42;
design p7 4.6).

For a month and one of Upstox's segments: the estimate is the sum of the estimates of that
month's orders (``core/charges.py``), the actual is Upstox's own row for the month. The gap is
flagged when the two differ by more than 5% of the actual (``charges.estimate_gap``), and a
flagged row says which part is off most, in a sentence the owner can act on.

* ``diff_paise`` is the actual less the estimate: above zero means Upstox charged more than the
  journal estimated. ``diff_pct`` is that as a percentage of the actual.
* **Part by part**, only where both sides have the part: brokerage, STT, stamp, SEBI's fee and GST
  one for one; the estimate's exchange charge against Upstox's transaction charge with its
  clearing charge and investor-protection fund. A part Upstox left empty is not compared.
* What Upstox charged in parts the estimate has no figure for (the depository's charge, others,
  what its total holds beyond the named parts) is ``unestimated_paise``: in the total gap, and
  named by the sentence when it is the largest part of it.
* A segment the rate card does not cover at all is never flagged: there is no estimate to be
  wrong. A month whose row is not read yet has no actual and no flag; nor has a month the
  journal does not cover whole (``NOT_WHOLE``), whose caller passes no actual for it.

No I/O, no clock.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from . import charges, words

#: (the estimate's part, Upstox's parts it is held against, the label on a screen).
COMPARED = (
    ("brokerage", ("brokerage",), "Brokerage"),
    ("stt", ("stt",), "STT"),
    ("exchange", ("transaction", "clearing", "ipft"), "Exchange charges"),
    ("sebi", ("sebi",), "SEBI fee"),
    ("stamp", ("stamp",), "Stamp duty"),
    ("gst", ("gst",), "GST"),
)
UNESTIMATED = ("dp", "others", "unlisted")
SEGMENT_LABELS = {"EQ": "Shares", "FO": "Futures and options", "COM": "Commodity",
                  "CD": "Currency"}  # fmt: skip
ADVICE = {
    "brokerage": "check your plan under Reports > Settings",
    "stt": "the rate card's STT row may be out of date",
    "exchange": "the rate card's exchange rate may be out of date",
    "sebi": "the rate card's SEBI row may be out of date",
    "stamp": "the rate card's stamp duty row may be out of date",
    "gst": "it follows the brokerage and the exchange charges",
}
NOT_MODELLED = (
    "Charges on this exchange are not estimated, so there is nothing to compare; Upstox's own "
    "figure is shown."
)
NOT_READ = "Upstox's charges for this month are not read yet."
NOT_ENDED = "Upstox's charges for a month are read from the 3rd of the next month."
#: A month the journal does not cover whole: Upstox's figure is the whole account's month.
NOT_WHOLE = (
    "The journal does not cover the whole of this month, and Upstox's figure is for the whole "
    "month, so the two are not compared."
)


def _gap(estimate: int, actual: int) -> dict[str, Any]:
    found = charges.estimate_gap(estimate, actual)
    pct = found["diff_pct"]
    if pct is not None:  # the gap's own sign is estimate less actual: turned round here
        pct = pct[1:] if pct.startswith("-") else ("-" + pct if found["diff_paise"] else pct)
    return {"diff_paise": actual - estimate, "diff_pct": pct, "flagged": found["flagged"]}


def parts_compared(estimate: Mapping[str, int],
                   actual: Mapping[str, int | None]) -> list[dict[str, Any]]:  # fmt: skip
    """Each part both sides have, in the card's order."""
    out = []
    for name, theirs, label in COMPARED:
        # The first of Upstox's parts must be there; clearing and the fund add to it if given.
        if actual.get(theirs[0]) is None:
            continue
        got = sum(int(actual.get(t) or 0) for t in theirs)
        est = int(estimate.get(name) or 0)
        out.append({"part": name, "label": label, "estimate_paise": est, "actual_paise": got,
                    "diff_paise": got - est})  # fmt: skip
    return out


def _sentence(parts: Sequence[Mapping[str, Any]], unestimated: int) -> str | None:
    """Which part is off most, for a flagged row."""
    worst = max(parts, key=lambda p: abs(p["diff_paise"]), default=None)
    if worst is None or abs(unestimated) > abs(worst["diff_paise"]):
        if not unestimated:
            return None
        return (
            f"{words.rupees(abs(unestimated))} of Upstox's charges are ones the estimate has no "
            "figure for (the depository's charge on shares sold from your demat account, and "
            "others)."
        )
    if not worst["diff_paise"]:
        return None
    way = "above" if worst["diff_paise"] > 0 else "below"
    return (
        f"{worst['label']} is {words.rupees(abs(worst['diff_paise']))} {way} the estimate: "
        f"{ADVICE[worst['part']]}."
    )


def segment_row(segment: str, estimate: Mapping[str, int], orders: int, modelled: bool,
                actual: Mapping[str, int | None] | None, actual_total: int | None,
                waiting: str | None = None) -> dict[str, Any]:  # fmt: skip
    """One segment of one month. ``estimate`` is the sum of the orders' estimated parts (with
    ``total``); ``actual`` Upstox's parts for the month (None while not read) and
    ``actual_total`` their sum as the journal keeps it; ``waiting`` the sentence for a month
    with no actual yet."""
    row: dict[str, Any] = {
        "segment": segment, "label": SEGMENT_LABELS.get(segment, segment), "orders": orders,
        "estimate_paise": int(estimate.get("total") or 0), "modelled": modelled,
        "actual_paise": None, "diff_paise": None, "diff_pct": None, "flagged": False,
        "unestimated_paise": 0, "parts": [], "words": waiting,
    }  # fmt: skip
    if actual is None or actual_total is None:
        return row
    row["actual_paise"] = int(actual_total)
    if not modelled:
        row["words"] = NOT_MODELLED
        return row
    row.update(_gap(row["estimate_paise"], row["actual_paise"]))
    row["parts"] = parts_compared(estimate, actual)
    row["unestimated_paise"] = sum(int(actual.get(p) or 0) for p in UNESTIMATED)
    row["words"] = _sentence(row["parts"], row["unestimated_paise"]) if row["flagged"] else None
    return row


def month_row(month: int, label: str, segments: Sequence[Mapping[str, Any]],
              waiting: str | None = None) -> dict[str, Any]:  # fmt: skip
    """A month over its segments: it has an actual only when every segment with orders has
    one, and it is compared only over the segments the card covers."""
    traded = [s for s in segments if s["orders"]]
    row: dict[str, Any] = {
        "month": month, "label": label, "orders": sum(s["orders"] for s in segments),
        "estimate_paise": sum(s["estimate_paise"] for s in segments), "actual_paise": None,
        "diff_paise": None, "diff_pct": None, "flagged": False, "words": None,
        "segments": list(segments),
    }  # fmt: skip
    if not traded:
        return row
    if any(s["actual_paise"] is None for s in traded):
        row["words"] = waiting or NOT_READ
        return row
    row["actual_paise"] = sum(s["actual_paise"] for s in traded)
    covered = [s for s in traded if s["modelled"]]
    if covered:
        row.update(_gap(sum(s["estimate_paise"] for s in covered),
                        sum(s["actual_paise"] for s in covered)))  # fmt: skip
        if len(covered) < len(traded):  # the whole month's difference, the flag from the covered
            row["diff_paise"] = row["actual_paise"] - row["estimate_paise"]
    flagged = [s for s in covered if s["flagged"]]
    if row["flagged"] and flagged:
        worst = max(flagged, key=lambda s: abs(s["diff_paise"]))
        row["words"] = worst["words"]
    return row


def year_row(months: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The months that have an actual, added up: the year's estimate and actual so far."""
    done = [m for m in months if m["actual_paise"] is not None]
    out = {"months": len(done), "estimate_paise": sum(m["estimate_paise"] for m in done),
           "actual_paise": sum(m["actual_paise"] for m in done) if done else None,
           "diff_paise": None, "diff_pct": None, "flagged": False}  # fmt: skip
    if done:
        out.update(_gap(out["estimate_paise"], out["actual_paise"]))
    return out
