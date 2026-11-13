"""What the TradingView export says, for a trader (M8.12, W20). Pure."""

from __future__ import annotations

from sudarshan.watchlists import numfmt
from sudarshan.watchlists.tvexport import Export

NOTHING = "Nothing in {name} can be exported to TradingView."
NO_FILE_NAME = "TradingView"
NOT_LOADED_FOR = "The instrument list is not loaded yet. Try again in a moment."
REASONS = {
    "no_tv_name": "These indices have no name TradingView reads: {tokens}.",
    "no_form": "These are on exchanges TradingView lists cannot take here, so they are left out: "
    "{tokens}.",
    "dated_future": "These are futures of one month. TradingView lists take only the rolling "
    "contract (NAME1! and NAME2!), so they are left out: {tokens}.",
    "expired": "No longer listed by the exchange, so they are left out: {tokens}.",
    "waiting": "These rows had no price to work from, so they are left out: {tokens}.",
    "no_contract": "The exchange lists no contract for these today, so they are left out: "
    "{tokens}.",
}


def _plural(n: int, one: str, many: str) -> str:
    return f"{numfmt.group(n)} {one if n == 1 else many}"


def summary(name: str, out: Export) -> str:
    tail = f" in {_plural(out.sections, 'section', 'sections')}" if out.sections else ""
    return f"{name}: {_plural(out.symbols, 'symbol', 'symbols')}{tail}, ready for TradingView."


def commas(n: int) -> str:
    return (
        f"{_plural(n, 'section name', 'section names')} had a comma, which TradingView cannot "
        "read; it was left out."
    )


def follows(rows: tuple[tuple[str, str], ...]) -> list[str]:
    """``NSE:NIFTY1! is the current-month future of NIFTY, and follows it on TradingView.``"""
    out = []
    for token, rule in rows:
        what = "current-month" if rule == "month" else "next-month"
        under = token.partition(":")[2][:-2]
        out.append(f"{token} is the {what} future of {under}, and follows it on TradingView.")
    return out


def rolled(tokens: tuple[str, ...]) -> str:
    return (
        f"{', '.join(tokens)} {'was' if len(tokens) == 1 else 'were'} written as the rolling "
        "future, since the contract is the one for the current or next month."
    )


def contracts(tokens: tuple[str, ...]) -> str:
    n = len(tokens)
    return (
        f"{_plural(n, 'at-the-money or strike row was', 'at-the-money or strike rows were')} "
        f"written as the contract {'it shows' if n == 1 else 'they show'} today: "
        f"{', '.join(tokens)}. {'It' if n == 1 else 'They'} will not follow the price on "
        "TradingView."
    )


def repeats(n: int) -> str:
    return f"{_plural(n, 'repeat was', 'repeats were')} dropped."


def left_out(out: Export) -> list[str]:
    by: dict[str, list[str]] = {}
    for c in out.cuts:
        by.setdefault(c.reason, []).append(c.token)
    return [text.format(tokens=", ".join(by[r])) for r, text in REASONS.items() if r in by]


def all_words(name: str, out: Export) -> list[str]:
    """The sentences under the export, in order."""
    if out.symbols == 0:
        said = [NOTHING.format(name=name)]
    else:
        said = [summary(name, out)]
    if out.commas:
        said.append(commas(out.commas))
    said += follows(out.follows)
    if out.rolled:
        said.append(rolled(out.rolled))
    if out.contracts:
        said.append(contracts(out.contracts))
    said += left_out(out)
    if out.repeats:
        said.append(repeats(out.repeats))
    return said
