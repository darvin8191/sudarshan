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

BUCKET_NOUNS = {
    "stcl": "short-term capital loss",
    "ltcl": "long-term capital loss",
    "business": "F&O (business) loss",
    "speculative": "intraday (speculative) loss",
}
TARGET_NOUNS = {
    "short": "short-term gain",
    "long": "long-term gain",
    "business": "F&O profit",
    "speculative": "intraday profit",
}
BUCKET_USE = {
    "stcl": "against capital gains",
    "ltcl": "against long-term capital gains only",
    "business": "against business income only",
    "speculative": "against intraday (speculative) profit only",
}


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def set_off_step(step: dict, amount: str, year: str) -> str:
    """One step of a set-off as a sentence; ``year`` is the label of the loss's own year."""
    whose = "This year's" if step["step"] == "same_year" else f"The {year}"
    noun = BUCKET_NOUNS[step["bucket"]]
    return f"{whose} {noun}: {amount} set against this year's {TARGET_NOUNS[step['against']]}."


def carried_on(row: dict, amount: str, year: str, last: str, years: int) -> str:
    noun, use = BUCKET_NOUNS[row["bucket"]], BUCKET_USE[row["bucket"]]
    return f"{_cap(noun)} of {year}: {amount} carried {years} years (to {last}), {use}."


def lapsed(row: dict, amount: str, year: str, last: str) -> str:
    noun = BUCKET_NOUNS[row["bucket"]]
    return f"{_cap(noun)} of {year}: {amount} could be used up to {last}. It has lapsed."


def own_figure(row: dict, amount: str, year: str) -> str:
    noun = BUCKET_NOUNS[row["bucket"]]
    return f"SUDARSHAN's own figure for the {noun} of {year} is {amount}; yours is used."


def other_heads(amount: str) -> str:
    return (
        f"Available against other income this year except salary: {amount}; what is not used "
        "is carried, against business income only."
    )


SPECULATIVE_STAYS = "An intraday (speculative) loss cannot reduce any other income."
SET_OFF_ORDER = (
    "Order used: this year's losses first, then earlier years' oldest first, an intraday "
    "(speculative) loss before a business loss. Verify with your CA."
)
