"""What the TradingView import sheet says, for a trader (M8.12). Pure."""

from __future__ import annotations

from sudarshan.watchlists import contract, numfmt
from sudarshan.watchlists.tvimport import FOLLOW_ROWS, LIST_ITEMS, MAX_BYTES, Plan

NO_INDIAN = (
    "None of these symbols could be matched. TradingView lists need NSE, BSE or MCX symbols."
)
TOO_BIG = f"That file is larger than {MAX_BYTES // 1024} KiB. Export a shorter list and try again."
LISTS_FULL = "You have 50 lists, the most there can be. Delete one to import."
CHANGED = "The file changed since its preview. Preview it again."
CHANGED_ELSEWHERE = "Changed on another screen. The list is shown again."
NEED_VERSION = "Say which version of the list the import was decided on. Update the app."
BOTH = "Pick the open list or a new list, not both."
NAME_LONG = "List names can be up to 64 letters."
NOTHING_NEW = "Everything in this file is already in this list."
NAME_DEFAULT = "TradingView"
STALE = "stale"
LOST = "lost"

_NUMBERS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine"}


def _plural(n: int, one: str, many: str) -> str:
    return f"{numfmt.group(n)} {one if n == 1 else many}"


def _names(tokens: list[str]) -> str:
    return ", ".join(tokens)


def unchecked_line(futures: list[str], options: list[str]) -> str | None:
    """What waits for a real TradingView file (design fact W-5), counted and named, on the way in
    and out: ``Left out until their TradingView form is checked: 2 index futures (A, B) and 1 BSE
    option (C).`` None when nothing does."""
    parts = []
    if futures:
        parts.append(
            f"{_plural(len(futures), 'index future', 'index futures')} ({_names(futures)})"
        )
    if options:
        parts.append(f"{_plural(len(options), 'BSE option', 'BSE options')} ({_names(options)})")
    if not parts:
        return None
    its = "its" if len(futures) + len(options) == 1 else "their"
    return f"Left out until {its} TradingView form is checked: {' and '.join(parts)}."


def preview(plan: Plan) -> list[str]:
    """The sentences under the file picker, in order; empty when nothing matched is ``NO_INDIAN``."""
    if plan.matched == 0:
        return [NO_INDIAN]
    out = [
        f"Found {_plural(plan.found_sections, 'section', 'sections')} and "
        f"{_plural(plan.found_symbols, 'symbol', 'symbols')}. {numfmt.group(plan.matched)} match."
    ]
    by: dict[str, list[str]] = {}
    for c in plan.cuts:
        by.setdefault(c.reason, []).append(c.token)
    if "not_indian" in by:
        n = len(by["not_indian"])
        verb = "is" if n == 1 else "are"
        out.append(
            f"{numfmt.group(n)} {verb} not on Indian exchanges and {verb} left out: "
            f"{_names(by['not_indian'])}."
        )
    if "not_recognised" in by:
        out.append(
            f"Not recognised and left out: {_names(by['not_recognised'])}. Add them from search."
        )
    if "no_exchange" in by:
        out.append(f"Left out, no exchange in front of the name: {_names(by['no_exchange'])}.")
    if "no_futures" in by:
        out.append(
            f"No futures are listed for these, so they are left out: {_names(by['no_futures'])}."
        )
    if "mcx_follow" in by:
        out.append(
            f"Rows that follow a commodity future are not offered yet: {_names(by['mcx_follow'])}."
        )
    held = unchecked_line(by.get("unchecked_future", []), by.get("unchecked_option", []))
    if held:
        out.append(held)
    for sec in plan.sections:
        for it in sec.items:
            if it.kind == "follow":
                what, bang = ("current-month", "1!") if it.rule == "month" else ("next-month", "2!")
                out.append(f"{it.symbol}{bang} becomes a {what} FUT row that follows {it.symbol}.")
    if plan.not_rolling:
        out.append(
            f"{FOLLOW_ROWS} follow rows is the most a list holds: "
            f"{_plural(plan.not_rolling, 'future was', 'futures were')} added as the contract of "
            "today and will not roll."
        )
    if plan.repeats:
        out.append(f"{_plural(plan.repeats, 'repeat was', 'repeats were')} dropped.")
    return out


def split_line(name: str, sizes: list[int]) -> str | None:
    """``This file has 312 symbols. A list holds 250, so they go into two lists: ...``."""
    if len(sizes) < 2:
        return None
    parts = " and ".join(f"{name} ({i}) with {n}" for i, n in enumerate(sizes, 1))
    return (
        f"This file has {numfmt.group(sum(sizes))} symbols. A list holds {LIST_ITEMS}, so they go "
        f"into {_NUMBERS.get(len(sizes), len(sizes))} lists: {parts}, split at a section."
    )


def room_line(name: str, room: int) -> str:
    """``Swing · room for 203``."""
    return f"{name} · room for {numfmt.group(room)}"


def done(name: str, symbols: int, sections: int) -> str:
    """The toast: ``Swing: 45 symbols in 2 sections.``"""
    tail = f" in {_plural(sections, 'section', 'sections')}" if sections else ""
    return f"{name}: {_plural(symbols, 'symbol', 'symbols')}{tail}."


def name_taken(name: str) -> str:
    return f"You already have a list called {name}. Pick another name."


def lists_short(need: int, room: int) -> str:
    """The file needs more lists than there is room for (under 50 in all)."""
    if room <= 0:
        return LISTS_FULL
    return (
        f"This file goes into {_NUMBERS.get(need, need)} lists and you have room for "
        f"{_NUMBERS.get(room, room)} more. Delete {need - room} to import."
    )


def no_room(name: str, room: int, symbols: int) -> str:
    """The open list cannot take the file's symbols."""
    more = "no more symbols" if room <= 0 else f"room for {numfmt.group(room)} more"
    return (
        f"{name} has {more}; this file has {_plural(symbols, 'symbol', 'symbols')}. "
        "Make room in it or import into a new list."
    )


def sections_full(name: str, have: int, adding: int) -> str:
    cap = contract.LIMITS["sections_per_list"]
    if have == 0:
        return f"This file has {adding} sections in {name}, and a list can have {cap}."
    return (
        f"{name} has {_plural(have, 'section', 'sections')} and this file adds "
        f"{adding}, and a list can have {cap}."
    )


def done_many(names: list[str], symbols: int, sections: int) -> str:
    """The toast for a file split into lists: ``Swing (1) and Swing (2): 312 symbols ...``."""
    tail = f" in {_plural(sections, 'section', 'sections')}" if sections else ""
    return f"{' and '.join(names)}: {_plural(symbols, 'symbol', 'symbols')}{tail}."


def part_new(made: list[str], got: int, total: int, rest: list[str]) -> str:
    """A new list was made, or several, and the next one was not."""
    if not made:
        return "Nothing was imported. Try again."
    return (
        f"Only {' and '.join(made)} {'was' if len(made) == 1 else 'were'} made, with {got} of the "
        f"file's {_plural(total, 'symbol', 'symbols')}. {' and '.join(rest)} "
        f"{'was' if len(rest) == 1 else 'were'} not made. Delete "
        f"{'it' if len(made) == 1 else 'them'} and import again."
    )


def part_open(done: int, total: int, name: str, placed: str | None = None) -> str:
    """The adds into the open list stopped part way."""
    if done == 0:
        return f"Nothing was added to {name}. Try again."
    where = ""
    if placed == STALE:
        where = ", in No section, because the list was changed on another screen meanwhile"
    elif placed == LOST:
        where = ", in No section, because the sections could not be set"
    return (
        f"Added {done} of {_plural(total, 'symbol', 'symbols')} to {name}{where}. "
        "The rest were not added; try again."
    )


def overlay_lost(name: str, n: int, stale: bool) -> str:
    """Every symbol is in the list, but its sections and follow rows were not set."""
    why = (
        "because the list was changed on another screen meanwhile. The list is shown again."
        if stale
        else "because its sections and follow rows could not be set."
    )
    return f"Added {_plural(n, 'symbol', 'symbols')} to {name}, in No section, {why}"
