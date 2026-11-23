"""Generator of token_refusals.json (M13.12, v0.17.0 verification V1): which reads a 401 pauses.

Imports nothing from sudarshan; the sentences are written out again here, so a vector is not a
copy of the code's words.

The Analytics Token makes twelve kinds of read: the Upstox tab's four parts (holdings, positions,
MTF, funds), the day's orders and trades (the family view and the journal), the reports (the
trade history, the P&L's metadata, pages and charges, an order's history) and corporate actions.
Each case refuses one of them with a 401 and then makes every read once more.

* A 401 on holdings or positions is Upstox refusing the token itself: every read pauses, in its
  reader's words that the token was not accepted, and the token line is failed.
* A 401 on any other read may only mean that this token may not make that read: that read alone
  pauses, in its own words, which never say the token was refused; every other read asks, and
  the token line is what holdings and positions say (ok).
* Corporate actions keep no pause: a 401 there ends the night, and the next read asks again.

``then`` gives each read after the refusal: ``asks`` (Upstox is asked and answers), ``asks_again``
(Upstox is asked and refuses it again, in the words of ``first``), or the key in ``words`` of the
sentence it shows without asking. ``first`` is the refused read's own sentence.

A read refused alone is asked again (v0.17.0 review M2): the live book's parts say when, refused at
11:00 on Monday 26 Oct 2026 (the test's clock), 15 minutes later in market hours. A report's
sentence gets its time where the report is kept (``reports.again_note``), so its key here is the
sentence alone.

Run: python3 make_token_refusals.py
"""

from pathlib import Path

from portfolio_vector_tools import write_all

NOTE = (
    "M13.12, v0.17.0 verification V1, written out in make_token_refusals.py: after a 401 on one "
    "read, which reads still ask Upstox, which pause and in what words, and the token line."
)

TAB = ("holdings", "positions", "mtf", "funds", "orders", "day_trades")
REPORTS = ("trades", "pnl_meta", "pnl", "charges", "order_history")
READS = (*TAB, *REPORTS, "corporate_actions")
JUDGES = ("holdings", "positions")

LET = "Upstox did not let SUDARSHAN's read-only token read "
REST = "; the rest of the Upstox tab is not affected."
TAB_NOT = "; the Upstox tab is not affected."
AGAIN = " SUDARSHAN asks Upstox again after 11:15 on Mon 26 Oct."
WORDS = {
    "tab_token": (
        "Upstox did not accept SUDARSHAN's read-only token, so the Upstox tab cannot be read now. "
        "The token is shared with TRINETRA: do not create a new one in the Upstox app; check the "
        "server first."
    ),
    "reports_token": (
        "Upstox did not accept SUDARSHAN's read-only token, so the reports cannot be read now. "
        "The token is shared with TRINETRA: do not create a new one in the Upstox app; check the "
        "server first."
    ),
    "corporate_actions_token": "Upstox did not accept SUDARSHAN's read-only token.",
    "mtf": LET + "your MTF positions" + REST + AGAIN,
    "funds": LET + "your funds" + REST + AGAIN,
    "orders": LET + "the day's orders" + TAB_NOT + AGAIN,
    "day_trades": LET + "the day's trades" + TAB_NOT + AGAIN,
    "trades": LET + "your trade history" + REST,
    "pnl": LET + "your trade-wise P&L report" + REST,
    "order_history": LET + "this order's history" + TAB_NOT,
    "corporate_actions": LET + "the list of corporate actions" + TAB_NOT,
}
#: The words a read shows when it is refused alone, by read.
OWN = {"pnl_meta": "pnl", "charges": "pnl"}


def token_words(read: str) -> str:
    if read in TAB:
        return "tab_token"
    return "reports_token" if read in REPORTS else "corporate_actions_token"


def case(number: int, refused: str) -> dict:
    own = OWN.get(refused, refused)
    if refused in JUDGES:
        title = f"R{number} {refused} refused: the token itself, so every read pauses"
        then = {read: token_words(read) for read in READS}
        return {"id": title, "refused": refused, "first": "tab_token", "token_line": "failed",
                "then": then}  # fmt: skip
    if refused == "corporate_actions":
        title = f"R{number} corporate actions refused: the night ends, nothing pauses"
        then = {read: "asks_again" if read == refused else "asks" for read in READS}
    else:
        title = f"R{number} {refused} refused: that read alone pauses, in its own words"
        then = {read: own if read == refused else "asks" for read in READS}
    return {"id": title, "refused": refused, "first": own, "token_line": "ok", "then": then}


OUT = {
    "note": NOTE,
    "reads": list(READS),
    "words": WORDS,
    "cases": [case(number, read) for number, read in enumerate(READS, 1)],
}


def build() -> dict:
    return {"token_refusals.json": OUT}


def main() -> None:
    write_all(build(), Path(__file__).parent)


if __name__ == "__main__":
    main()
