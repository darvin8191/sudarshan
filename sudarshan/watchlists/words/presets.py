"""What the owner reads on a built-in list (M8.8, W12). Pure.

A built-in list is locked: the page offers no add, remove or arrange on it, and a request that
tries is refused in these words. Sort, flags, notes and copy work.
"""

from __future__ import annotations

from sudarshan.watchlists import contract

#: A change was asked of a built-in list (a move takes symbols out of it).
LOCKED = "This is a built-in list, locked. Make a copy of it to change it."
GONE = "That built-in list is not offered. Pick another list."
NO_SECTION = "A built-in list has no sections. Pick symbols from it, or copy the whole list."
NOT_LOADED = (
    "Today's contract list is not loaded yet. It loads after the day's login; "
    "open this list again then."
)
WINDOW = (
    f"Prices update for the {contract.LIMITS['window']} rows around what you see; "
    "the rest show their last price and its time."
)

#: The second line of each master-derived list: what it holds. The date is the master's own.
RULES = {
    "indices": "the indices SUDARSHAN follows",
    "fno_stocks": "every stock with futures",
    "index_futures": "the current future of each index, moving to the next month when its "
    "expiry day closes",
}
MORNING = "Updated each morning after the day's login."


def symbols(n: int) -> str:
    return f"{n} symbol" if n == 1 else f"{n} symbols"


def date_words(iso: str | None) -> str | None:
    """``2026-11-09`` to ``9 Nov 2026``; None when there is no date."""
    if not iso:
        return None
    y, m, d = (int(p) for p in iso[:10].split("-"))
    names = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
    return f"{d} {names[m - 1]} {y}"


def locked_line(name: str, n: int) -> str:
    return f"Built-in list, locked. {name}: {symbols(n)}."


def source_line(rule: str, as_of: str | None) -> str:
    """``From the Upstox contract list of 9 Nov 2026: every stock with futures. Updated ...``."""
    when = date_words(as_of)
    of = f"of {when}" if when else "(date not recorded)"
    return f"From the Upstox contract list {of}: {RULES[rule]}. {MORNING}"


def missing_line(names: list[str], review: bool) -> str:
    """The names a list could not find in today's master. ``review`` says the list is a typed one
    that is checked at each index review."""
    n = len(names)
    if review:
        count = f"{n} name in this list is" if n == 1 else f"{n} names in this list are"
        return (
            f"{count} not in today's contract list: {', '.join(names)}. "
            "The list is checked at each index review."
        )
    count = "1 stock has" if n == 1 else f"{n} stocks have"
    return f"{count} futures but no stock listing today: {', '.join(names)}."


def truncated(kept: int, total: int) -> str:
    return (
        f"Copied the first {kept} of {total}, in the order shown. "
        f"A list holds {contract.LIMITS['list_items']}."
    )


#: Why a name was left out of the sealed route's answer.
LEFT_OUT = "not in today's contract list"
