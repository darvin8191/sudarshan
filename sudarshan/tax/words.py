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


CAPITAL_ORDER = (
    "Order used: this year's losses first, then earlier years' oldest first, a short-term "
    "loss before a long-term one of the same year. Verify with your CA."
)
SPECULATIVE_STAYS = "An intraday (speculative) loss cannot reduce any other income."
SET_OFF_ORDER = (
    "Order used: this year's losses first, then earlier years' oldest first, an intraday "
    "(speculative) loss before a business loss. Verify with your CA."
)

#: What the working paper never works out, each as one line.
NOT_COMPUTED = (
    "Tax at slab rates, surcharge, rebate and the choice of regime.",
    "The unused basic exemption against these gains.",
    "Set-off of a business loss against other income, and of any loss across heads.",
    "The presumptive scheme, and whether books or an audit are needed.",
    "Other business expenses (data, software, internet, an office).",
    "Rules against wash sales and bonus or dividend stripping.",
    "The extra tax on a promoter in a buyback.",
    "Debt funds, unlisted shares, property, gold, crypto and foreign assets.",
    "Dividends other than a buyback's receipt (they come with corporate actions).",
)
CESS_LABEL = "cess at 4%, before surcharge"
EXEMPT_ORDERS = {
    "highest_rate_first": "against the gains taxed at the higher rate first",
    "chronological": "against the earlier gains first",
}


def exempt_order(order: str) -> str:
    return (
        "This year has long-term gains at two rates. The exemption and losses were used "
        f"{EXEMPT_ORDERS[order]}. Verify the order with your CA."
    )


def count_of(count: int, one: str, many: str) -> str:
    return f"{count} {one if count == 1 else many}"


def unknown_cost(count: int) -> str:
    return count_of(count, "sale has", "sales have") + " no purchase record and is in no total."


def needs_fmv(count: int) -> str:
    return (
        count_of(count, "sale needs", "sales need")
        + " the price of 31 January 2018. Add it under Inputs; until then it is in no total."
    )


def assumed_zero(count: int) -> str:
    return (
        count_of(count, "option was", "options were")
        + " open past expiry with no settlement record and taken as expired worthless."
    )


def unknown_settlement(count: int) -> str:
    return (
        count_of(count, "future was", "futures were")
        + " open past expiry with no settlement price. It is listed and in no total."
    )


def not_computed_sales(count: int, amount: str) -> str:
    return (
        count_of(count, "sale", "sales")
        + f" of assets outside this working paper (sale value {amount}) are not computed."
    )


EXPENSES_ALLOCATED = (
    "Share-market charges are Upstox's total for the year, shared between intraday and "
    "delivery by traded value and over delivery sales by sale value (allocated). STT is "
    "left out of capital gains and kept in business charges."
)
EXPENSES_LEFT_OUT = (
    "Charges are not taken off share gains here, so the gains shown are slightly larger."
)
CHARGES_UNKNOWN = "Upstox's charges for {segment} are not read yet; none are taken off."
BUYBACK_NOTE = (
    "A buyback is worked out by the rule of its date; from 1 April 2026 as an ordinary sale "
    "of a listed share. Verify with your CA."
)
BUSINESS_LOSS_AND_GAINS = (
    "A business loss of this year can also reduce these capital gains; that sum is your CA's "
    "and is not taken here."
)
