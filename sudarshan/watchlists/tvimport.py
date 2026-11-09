"""Reading a TradingView watchlist file (M8.12, W18): sections, symbols, mapping and the split.

Pure: the text and a ``known(exchange, symbol)`` lookup of the master come in as arguments; no
I/O, no clock, no logging. The file is exchange-prefixed symbols separated by commas or line
ends; ``###Name`` opens a section. A symbol is matched against the master as written, then with
each ``_`` as ``-``, then as ``&`` (``BAJAJ_AUTO``, ``M_M``). ``NAME1!`` and ``NAME2!`` become
current-month and next-month future rows, up to the follow rows a list may hold.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from typing import NamedTuple

MAX_BYTES = 64 * 1024
SECTION_NAME = 40
LIST_ITEMS = 250
FOLLOW_ROWS = 20
INDEX_ALIASES = {
    "NIFTY": "NIFTY", "BANKNIFTY": "BANKNIFTY", "INDIAVIX": "INDIAVIX",
    "CNXFINANCE": "FINNIFTY", "NIFTY_MID_SELECT": "MIDCPNIFTY", "CNXIT": "NIFTYIT",
    "CNXAUTO": "NIFTYAUTO", "CNXPHARMA": "NIFTYPHARMA", "CNXFMCG": "NIFTYFMCG",
    "CNXMETAL": "NIFTYMETAL", "CNXREALTY": "NIFTYREALTY", "CNXPSUBANK": "NIFTYPSUBANK",
    "CNXMEDIA": "NIFTYMEDIA", "CNXENERGY": "NIFTYENERGY", "SENSEX": "SENSEX", "BANKEX": "BANKEX",
    "CNX500": "NIFTY500", "CNX100": "NIFTY100", "CNX200": "NIFTY200", "NIFTYJR": "NIFTYNXT50",
    "CNXSMALLCAP": "NIFTYSMLCAP100", "CNXMIDCAP": "NIFTYMIDCAP100",
}  # fmt: skip
_BSE_INDICES = ("SENSEX", "BANKEX")
_FOLLOW = re.compile(r"^(.+?)([12])!$")
_RULES = {"1": "month", "2": "next_month"}

Known = Callable[[str, str], bool]


class Item(NamedTuple):
    """``fixed``: a symbol. ``follow``: a row that follows ``symbol`` (the underlying), with its
    expiry rule in ``rule``. ``fixed_future``: the same, imported as today's contract only."""

    kind: str
    exchange: str
    symbol: str
    rule: str | None = None


class Cut(NamedTuple):
    """A token left out, and why: not_indian, no_exchange, not_recognised, mcx_follow, empty."""

    token: str
    reason: str


class Sec(NamedTuple):
    """A section (``None`` is the symbols before the first header) and what it holds."""

    name: str | None
    items: tuple[Item, ...]


class Plan(NamedTuple):
    sections: tuple[Sec, ...]
    cuts: tuple[Cut, ...]
    found_sections: int
    found_symbols: int
    repeats: int
    follow: int
    not_rolling: int

    @property
    def matched(self) -> int:
        return sum(len(s.items) for s in self.sections)


def too_big(text: str) -> bool:
    """True when the file is past what the sheet reads (64 KiB)."""
    return len(text.encode("utf-8")) > MAX_BYTES


def text_hash(text: str) -> str:
    """What a preview and its import agree on: the same text gives the same hash."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def tokens(text: str) -> list[str]:
    """Split on commas and line ends; strip spaces and a byte-order mark; drop blanks."""
    clean = text.replace("﻿", "").replace("\r", "\n")
    return [t.strip() for part in clean.split("\n") for t in part.split(",") if t.strip()]


def section_names(raw: list[str]) -> list[str]:
    """Names cut to 40 letters, a repeat getting `` (2)``, `` (3)`` and so on."""
    seen: dict[str, int] = {}
    out = []
    for name in raw:
        base = (name.strip() or "Section")[:SECTION_NAME]
        n = seen.get(base.lower(), 0) + 1
        seen[base.lower()] = n
        out.append(base if n == 1 else f"{base[: SECTION_NAME - len(f' ({n})')]} ({n})")
    return out


def _symbol(exchange: str, name: str, known: Known) -> Item | Cut | None:
    if name in INDEX_ALIASES:
        target = INDEX_ALIASES[name]
        ex = "BSE_INDEX" if target in _BSE_INDICES else "NSE_INDEX"
        return Item("fixed", ex, target) if known(ex, target) else None
    tries = [name]
    if "_" in name:
        tries += [name.replace("_", "-"), name.replace("_", "&")]
    for cand in tries:
        if known(exchange, cand):
            return Item("fixed", exchange, cand)
    return None


def _follow(exchange: str, name: str, rule: str, known: Known) -> Item | Cut | None:
    if exchange == "MCX":
        return Cut("", "mcx_follow")
    if name in INDEX_ALIASES:
        target = INDEX_ALIASES[name]
        ex = "BSE_INDEX" if target in _BSE_INDICES else "NSE_INDEX"
        return Item("follow", ex, target, rule) if known(ex, target) else None
    for cand in (name, name.replace("_", "-"), name.replace("_", "&")):
        if known(exchange, cand):
            return Item("follow", exchange, cand, rule)
    return None


def parse(text: str, known: Known, follow_room: int = FOLLOW_ROWS) -> Plan:
    """The file as sections of mapped items, with what was left out and why."""
    raw_sections: list[list] = [[None, []]]
    headers: list[str] = []
    for tok in tokens(text):
        if tok.startswith("###"):
            headers.append(tok[3:])
            raw_sections.append([len(headers) - 1, []])
        else:
            raw_sections[-1][1].append(tok)
    names = section_names(headers)
    seen: set[tuple[str, str, str | None]] = set()
    cuts: list[Cut] = []
    out: list[Sec] = []
    found = repeats = follow = rolling_off = 0
    for idx, toks in raw_sections:
        items: list[Item] = []
        for tok in toks:
            found += 1
            ex, sep, rest = tok.upper().partition(":")
            if not sep or not rest:
                cuts.append(Cut(tok, "no_exchange"))
                continue
            if ex not in ("NSE", "BSE", "MCX"):
                cuts.append(Cut(tok, "not_indian"))
                continue
            m = _FOLLOW.match(rest)
            got = (
                _follow(ex, m.group(1), _RULES[m.group(2)], known)
                if m
                else _symbol(ex, rest, known)
            )
            if isinstance(got, Cut):
                cuts.append(Cut(tok, got.reason))
                continue
            if got is None:
                cuts.append(Cut(tok, "not_recognised"))
                continue
            key = (got.kind, got.exchange + ":" + got.symbol, got.rule)
            if key in seen:
                repeats += 1
                continue
            seen.add(key)
            if got.kind == "follow":
                if follow < follow_room:
                    follow += 1
                else:
                    got = got._replace(kind="fixed_future")
                    rolling_off += 1
            items.append(got)
        out.append(Sec(None if idx is None else names[idx], tuple(items)))
    if out and out[0].name is None and not out[0].items:
        out = out[1:] if len(out) > 1 else out
    return Plan(tuple(out), tuple(cuts), len(headers), found, repeats, follow, rolling_off)


def _fixed(sec: Sec) -> list[Item]:
    return [i for i in sec.items if i.kind != "follow"]


def split(plan: Plan, size: int = LIST_ITEMS) -> list[tuple[Sec, ...]]:
    """Lists of at most ``size`` symbols, cut at section boundaries; inside a section only when
    that one section alone is over ``size``. Follow rows do not count against the size."""
    chunks: list[list[Sec]] = []
    cur: list[Sec] = []
    count = 0
    for sec in plan.sections:
        fixed = _fixed(sec)
        n = len(fixed)
        if n > size:
            if cur:
                chunks.append(cur)
            follows = [i for i in sec.items if i.kind == "follow"]
            pieces = [fixed[i : i + size] for i in range(0, n, size)]
            pieces[0] = follows + pieces[0]
            for piece in pieces[:-1]:
                chunks.append([Sec(sec.name, tuple(piece))])
            cur = [Sec(sec.name, tuple(pieces[-1]))]
            count = len(fixed) - size * (len(pieces) - 1)
            continue
        if count + n > size:
            chunks.append(cur)
            cur, count = [], 0
        cur.append(sec)
        count += n
    if cur or not chunks:
        chunks.append(cur)
    return [tuple(c) for c in chunks]
