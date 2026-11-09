"""What the TradingView import sheet says, for a trader (M8.12). Pure."""

from __future__ import annotations

from sudarshan.watchlists import numfmt
from sudarshan.watchlists.tvimport import FOLLOW_ROWS, LIST_ITEMS, MAX_BYTES, Plan

NO_INDIAN = (
    "None of these symbols could be matched. TradingView lists need NSE, BSE or MCX symbols."
)
TOO_BIG = f"That file is larger than {MAX_BYTES // 1024} KiB. Export a shorter list and try again."
LISTS_FULL = "You have 50 lists, the most there can be. Delete one to import."


_NUMBERS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine"}


def _plural(n: int, one: str, many: str) -> str:
    return f"{numfmt.group(n)} {one if n == 1 else many}"


def _names(tokens: list[str]) -> str:
    return ", ".join(tokens)


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
    if "mcx_follow" in by:
        out.append(
            f"Rows that follow a commodity future are not offered yet: {_names(by['mcx_follow'])}."
        )
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
