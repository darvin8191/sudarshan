"""What the strike picker says, for a trader (M8.18, W17). Pure."""

from __future__ import annotations

from sudarshan.watchlists import numfmt

#: The chips of the search sheet, as the owner reads them.
SEGMENTS = {"stocks": "Stocks", "futures": "Futures", "options": "Options",
            "indices": "Indices", "commodities": "Commodities"}  # fmt: skip

QUERY_LONG = "That is too long to search for. Type the symbol or a few words of the name."
LIMIT_BAD = "Ask for between 1 and 50 results."
PRICE_BAD = "The price must be more than zero."
STRIKES_BAD = "Ask for between 0 and 25 strikes each side of the ATM."
EXPIRY_GONE = "That expiry is not in today's contract list. Pick another one."
EXCHANGE_BAD = "That underlying is not listed on that exchange."
NO_PRICE = (
    "The price is not known yet, so no strike is marked ATM. "
    "The middle of the listed strikes is shown."
)


def expiry_label(iso: str, first: bool, month: bool) -> str:
    """``Nearest 24 Nov``, ``1 Dec`` or ``29 Dec (month)``."""
    day = numfmt.short_day(iso)
    if first:
        return f"Nearest {day}"
    return f"{day} (month)" if month else day


def no_options(underlying: str) -> str:
    return f"{underlying} has no options in today's contract list."


def no_open_expiry(underlying: str) -> str:
    return f"{underlying} has no expiry still open in today's contract list."


def ends_today(close_ms: int) -> str:
    return f"This expiry ends today at {numfmt.hm(close_ms)}."


def missing_sides(missing: list[tuple[float, str]]) -> str:
    """``No PE in today's contract list for 25,850 and 25,900.`` one sentence per side."""
    out = []
    for side in ("CE", "PE"):
        strikes = [numfmt.num(s) for s, w in missing if w == side]
        if not strikes:
            continue
        shown = strikes[:3]
        more = len(strikes) - len(shown)
        names = ", ".join(shown[:-1]) + (" and " if len(shown) > 1 else "") + shown[-1]
        if more:
            names += f" and {more} more"
        out.append(f"No {side} in today's contract list for {names}.")
    return " ".join(out)


def segment_empty(segment: str, query: str) -> str:
    """The query has matches, only not under the chip that is on."""
    return f"Nothing for {query.strip().upper()} under {SEGMENTS[segment]}. Try All."
