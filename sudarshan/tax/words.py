"""The sentences the tax core says (M15.5). No section number of any Act appears in any of them."""

from __future__ import annotations

#: The label on every output. The journal imports this as its FY note.
WORKING_PAPER = (
    "A working paper: verify with your CA. The broker's own report is the filing source."
)

ACTION_NOUNS = {"BONUS": "bonus", "SPLIT": "split"}


def action_unconfirmed(kind: str, isin: str, day_text: str) -> str:
    return (
        f"A {ACTION_NOUNS.get(kind, 'corporate action')} of {isin} on {day_text} is not "
        "confirmed, so the lots were left as they were. Confirm it and the figures follow."
    )


def fraction_left(kind: str, isin: str, day_text: str) -> str:
    return (
        f"The {ACTION_NOUNS.get(kind, 'corporate action')} of {isin} on {day_text} leaves part "
        "of a share. It is kept as a fraction, not rounded away: enter what the company paid for it."
    )


def audit_above_digital(limit: str) -> str:
    return (
        f"Turnover is above {limit}: a tax audit applies to exchange trading above this. "
        "Ask your CA."
    )


def audit_above_base(base: str, limit: str) -> str:
    return (
        f"Above {base}. The {limit} limit applies when cash dealings are 5% or less, which is "
        "so for exchange trading."
    )


LOW_MARGIN = "A loss or a low margin can need books and an audit in some cases. Ask your CA."
TURNOVER_METHODS = {
    "abs_pnl": "Sum of profits and losses, ICAI method",
    "abs_pnl_plus_sale_premium": "Sum of profits and losses plus premium on options sold",
}
