"""What the owner reads when a follow row is refused (M8.10, M8.11). Pure."""

from __future__ import annotations

from sudarshan.watchlists import contract

ROWS_FULL = f"A list can have {contract.LIMITS['rows_per_list']} rows that follow the price."
UNDERLYING_EMPTY = "Pick the stock or index this row follows."
EXCHANGE_LATER = (
    "Rows that follow the price are for NSE and BSE options and futures. "
    "Commodity (MCX) rows will come later."
)
EXCHANGE_BAD = "Rows that follow the price are for NSE and BSE stocks and indices only."
RIGHT_NEEDED = "Pick CE or PE for this row."
OFFSET_BAD = "Pick a strike from 10 below the ATM to 10 above it."
STRIKE_NEEDED = "Give the strike this row stays on."
STRIKE_BAD = "That strike is not a price a strike can have."
NOTHING = "Give something to change."
ROW_GONE = "That row is no longer in this list."
NOT_LOADED = (
    "Today's contract list is not loaded yet, so this row cannot be checked. Try again in a minute."
)
TOO_MANY = "That is more rows than one look can follow. Open fewer at a time."


def not_listed(underlying: str) -> str:
    return f"{underlying} is not in today's contract list."


def no_options(underlying: str, exchange: str) -> str:
    return f"{underlying} has no options on {exchange}."


def no_futures(underlying: str, exchange: str) -> str:
    return f"{underlying} has no futures on {exchange}."


def same_row() -> str:
    return "This list already follows that."
