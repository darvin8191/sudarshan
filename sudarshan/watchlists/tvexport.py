"""Writing a TradingView watchlist file (M8.12, W20): W18's mapping in reverse.

Pure: the rows of a list come in as ``Block`` records (what the op read from the list and the
master), the text and what was left out go out. No I/O, no clock, no logging. The text is what
TradingView itself writes and what ``tvimport.parse`` reads: exchange-prefixed symbols separated
by commas, ``###Name`` opening a section. A comma inside a section name would split it in
TradingView's import, so it is dropped from the name.

What each row becomes:

* an NSE, BSE or (as the import reads it) MCX plain symbol: ``NSE:INFY``; ``-`` and ``&`` become ``_`` (``BAJAJ_AUTO``, ``M_M``),
  which the import tries back as ``-`` and ``&``;
* an index: its TradingView name through the import's alias table in reverse (``FINNIFTY`` is
  ``CNXFINANCE``); an index the table has no name for is left out;
* a row that follows a future: ``NSE:NIFTY1!`` (current month) or ``NSE:NIFTY2!`` (next month);
* a dated future that is today's current-month or next-month contract: the same ``1!`` or ``2!``,
  so it follows on TradingView (``Entry.cont`` says which); any other dated future has no form
  TradingView reads and is left out;
* an option contract, which is what an at-the-money or strike row shows today (``Entry.today``):
  ``NSE:NIFTY261124C25800`` (underlying, expiry as YYMMDD, C or P, strike). The row does not follow
  the price on TradingView, and the answer says so. The form is TradingView's as far as the
  owner's files show; the import does not read it back, so it is left out on the way in.

Until one real TradingView file confirms the form (design fact W-5), the futures of an index
other than NIFTY and BANKNIFTY (written with the index's chart name they would read
``NSE:CNXFINANCE1!``, which TradingView most likely does not), every BSE future and every BSE option
are left out, counted and named in the answer's words (``unchecked_future``, ``unchecked_option``;
v0.9.0 review M3). Which futures are an index's is the master's answer, passed in ``Entry.index``.
NSE stock and index options and stock futures keep their forms.
"""

from __future__ import annotations

from typing import NamedTuple

from sudarshan.watchlists.tvimport import (  # noqa: F401
    CHECKED_INDEX_FUTURES,
    INDEX_ALIASES,
    SECTION_NAME,
    UNCHECKED_FUTURE,
    UNCHECKED_OPTION,
)

#: The TradingView name of each index of the master (the alias table of the import, reversed).
TV_INDEX = {target: alias for alias, target in INDEX_ALIASES.items()}
_FO = {"NFO": "NSE", "BFO": "BSE"}
_INDEX_EXCHANGE = {"NSE_INDEX": "NSE", "BSE_INDEX": "BSE"}
_RULE_BANG = {"month": "1!", "next_month": "2!"}
SECTION_DEFAULT = "Section"


class Entry(NamedTuple):
    """One row. ``kind`` is ``follow`` (a row that follows the future of ``symbol``, an
    underlying on ``exchange``, with its expiry rule in ``rule``) or ``row`` (a contract or symbol
    of the master: ``instrument`` CE, PE, FUT or None, ``expiry`` as ``2026-11-24``, ``strike``,
    ``underlying`` its name). ``cont`` is ``month`` or ``next_month`` for a future that is that
    contract today; ``today`` is true for a contract an at-the-money or strike row shows now;
    ``index`` is true for a contract whose underlying the master lists as an index's."""

    kind: str
    exchange: str
    symbol: str
    rule: str | None = None
    instrument: str | None = None
    expiry: str | None = None
    strike: float | None = None
    underlying: str | None = None
    cont: str | None = None
    today: bool = False
    index: bool = False


class Block(NamedTuple):
    """A section (``None`` is the rows in no section) and its entries, plus the cuts the op made
    before the entries (a row it could not turn into one), as ``(token, reason)``."""

    name: str | None
    entries: tuple[Entry, ...]
    cuts: tuple[tuple[str, str], ...] = ()


class Cut(NamedTuple):
    """A row left out and why: no_tv_name, no_form, dated_future, expired, waiting, no_contract,
    unchecked_future, unchecked_option."""

    token: str
    reason: str


class Export(NamedTuple):
    text: str
    symbols: int
    sections: int
    cuts: tuple[Cut, ...]
    commas: int  # section names that had a comma, now without
    repeats: int
    follows: tuple[
        tuple[str, str], ...
    ]  # (token, ``month`` or ``next_month``) of rows written 1!/2!
    contracts: tuple[str, ...]  # tokens of the contracts written as they show today
    rolled: tuple[str, ...]  # tokens of dated futures written as 1!/2!


def _under(name: str, index: bool) -> str:
    if index:
        return TV_INDEX.get(name, name)
    return name.replace("-", "_").replace("&", "_")


def _strike(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))


def _cash(e: Entry) -> tuple[str | None, str | None]:
    if e.exchange in ("NSE", "BSE") or (e.exchange == "MCX" and e.instrument in (None, "EQ")):
        return f"{e.exchange}:{_under(e.symbol, False)}", None
    return None, "no_form"


def tv_symbol(e: Entry) -> tuple[str | None, str | None]:
    """``(TradingView symbol, None)`` or ``(None, reason it has none)``."""
    if e.kind == "follow":
        bang = _RULE_BANG.get(e.rule or "")
        index = e.exchange in _INDEX_EXCHANGE
        ex = _INDEX_EXCHANGE.get(e.exchange, e.exchange)
        if bang is None or ex not in ("NSE", "BSE"):
            return None, "no_form"
        if ex == "BSE" or (index and e.symbol not in CHECKED_INDEX_FUTURES):
            return None, UNCHECKED_FUTURE
        return f"{ex}:{_under(e.symbol, False)}{bang}", None
    if e.exchange in _INDEX_EXCHANGE:
        if e.symbol not in TV_INDEX:
            return None, "no_tv_name"
        return f"{_INDEX_EXCHANGE[e.exchange]}:{TV_INDEX[e.symbol]}", None
    if e.exchange in _FO:
        ex, name = _FO[e.exchange], e.underlying or ""
        if e.instrument == "FUT":
            if e.cont not in _RULE_BANG:
                return None, "dated_future"
            index = e.index or name in TV_INDEX
            if ex == "BSE" or (index and name not in CHECKED_INDEX_FUTURES):
                return None, UNCHECKED_FUTURE
            return f"{ex}:{_under(name, False)}{_RULE_BANG[e.cont]}", None
        if e.instrument in ("CE", "PE") and e.expiry and e.strike is not None and name:
            if ex == "BSE":
                return None, UNCHECKED_OPTION
            y, m, d = e.expiry.split("-")
            return (
                f"{ex}:{_under(name, False)}{y[2:]}{m}{d}{e.instrument[0]}{_strike(e.strike)}",
                None,
            )
        return None, "no_form"
    return _cash(e)


def section_name(raw: str) -> tuple[str, bool]:
    """The name without its commas (and line ends), and whether it had a comma."""
    had = "," in raw
    clean = " ".join(raw.replace(",", "").split())
    return (clean or SECTION_DEFAULT), had


def build(blocks: list[Block]) -> Export:
    """The file for ``blocks``, in their order. Repeats (the same TradingView symbol twice) are
    dropped, as the import drops them."""
    parts: list[str] = []
    seen: set[str] = set()
    cuts: list[Cut] = []
    follows: list[tuple[str, str]] = []
    contracts: list[str] = []
    rolled: list[str] = []
    symbols = sections = commas = repeats = 0
    for block in blocks:
        cuts += [Cut(t, r) for t, r in block.cuts]
        if block.name is not None:
            name, had = section_name(block.name)
            commas += had
            sections += 1
            parts.append("###" + name)
        for e in block.entries:
            sym, why = tv_symbol(e)
            if sym is None:
                cuts.append(
                    Cut(
                        f"{e.exchange}:{e.symbol}" if e.kind == "row" else e.symbol,
                        why or "no_form",
                    )
                )
                continue
            if sym in seen:
                repeats += 1
                continue
            seen.add(sym)
            parts.append(sym)
            symbols += 1
            if e.kind == "follow":
                follows.append((sym, e.rule or ""))
            elif e.today:
                contracts.append(sym)
            elif e.instrument == "FUT":
                rolled.append(sym)
    return Export(
        ",".join(parts), symbols, sections, tuple(cuts), commas, repeats,
        tuple(follows), tuple(contracts), tuple(rolled),
    )  # fmt: skip
