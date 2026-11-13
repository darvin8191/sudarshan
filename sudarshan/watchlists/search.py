"""The symbol search grammar (M8.18, W16): what a trader typed, understood, and the rows it means.

Pure: every input is an argument, no I/O, no clock, no logging. Rows are anything with the master's
fields (``symbol``, ``name``, ``exchange``, ``token``, ``expiry`` as ``24-NOV-26``, ``strike``,
``lotsize``, ``instrumenttype``, ``underlying``). The index keeps small reference maps whose values
are the rows themselves (never copies), so it is as small as the lists of references it holds and
is dropped with the generation of the master it was built from (``IndexBuilder.add`` takes rows in
slices, so the caller can yield between them).
"""

from __future__ import annotations

import re
from collections.abc import Collection
from typing import Any, NamedTuple

from sudarshan.watchlists import dynamic, expiry

MAX_HITS = 50
MAX_SUGGESTIONS = 3
ALIASES = {
    "banknifty": "BANKNIFTY", "bank nifty": "BANKNIFTY", "nifty bank": "BANKNIFTY",
    "finnifty": "FINNIFTY", "fin nifty": "FINNIFTY",
    "midcpnifty": "MIDCPNIFTY", "midcap nifty": "MIDCPNIFTY", "midcp nifty": "MIDCPNIFTY",
    "nifty 50": "NIFTY", "nifty50": "NIFTY", "vix": "INDIAVIX", "india vix": "INDIAVIX",
}  # fmt: skip
_MONTHS = {
    m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())
}
_LONG = "january february march april june july august september october november december"
_MONTH_NAMES = {
    **_MONTHS,
    **dict(zip(_LONG.split(), (1, 2, 3, 4, 6, 7, 8, 9, 10, 11, 12), strict=True)),
}
_SIDES = {"ce": "CE", "c": "CE", "call": "CE", "pe": "PE", "p": "PE", "put": "PE"}
_ISIN = re.compile(r"^IN[A-Z0-9]{9}\d$")
_GLUE = re.compile(r"(\d)(ce|pe|call|put|c|p|" + "|".join(_MONTH_NAMES) + r")\b")
_ATM = re.compile(r"^atm(?:([+-])(\d{1,2}))?$")
_NUM = re.compile(r"^\d+(?:\.\d+)?$")
_COMMODITY_EXCHANGES = ("MCX", "NCDEX", "NCO")


class Understood(NamedTuple):
    """The query as understood (the words are in ``words/search.py``)."""

    underlying: str | None = None
    expiry: tuple | None = None  # ("nearest"|"next"|"month"|"next_month"|"date"|"month_name", ...)
    expiries: tuple[str, ...] = ()
    strike: float | None = None
    atm: int | None = None
    side: str | None = None
    future: bool = False
    isin: str | None = None
    needs_price: bool = False
    options: bool = False


class Result(NamedTuple):
    """Hits are ``(group, row)``; ``underlying`` is set when options exist for Pick a strike."""

    hits: list
    understood: Understood
    underlying: str | None
    suggestions: tuple[str, ...]


def isin_of(token: str | None) -> str | None:
    """The ISIN in an Upstox instrument key (``NSE_EQ|INE002A01018``), or None."""
    if not token or "|" not in token:
        return None
    tail = token.split("|", 1)[1]
    return tail if _ISIN.match(tail) else None


def key_of(text: str) -> str:
    """A name as a lookup key: lower case, only letters, digits and ``&``."""
    return re.sub(r"[^a-z0-9&]", "", text.lower())


def expiry_iso(text: str | None) -> str | None:
    """``24-NOV-26`` or ``2026-11-24`` to ``2026-11-24``."""
    if not text:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return text
    m = re.match(r"^(\d{1,2})-([A-Za-z]{3})-(\d{2}|\d{4})$", text)
    if not m or m.group(2).lower() not in _MONTHS:
        return None
    y = int(m.group(3))
    y += 2000 if y < 100 else 0
    return f"{y:04d}-{_MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"


def group_of(row: Any) -> str:
    """The group a row shows under: stocks, indices, futures, options or commodities."""
    if row.exchange in _COMMODITY_EXCHANGES:
        return "commodities"
    if row.exchange.endswith("_INDEX"):
        return "indices"
    return {"FUT": "futures", "CE": "options", "PE": "options"}.get(row.instrumenttype, "stocks")


class Index:
    """Reference maps over one generation of the master."""

    def __init__(self) -> None:
        self.cash: dict[str, list] = {}  # key of the symbol -> cash and index rows
        self.names: list[tuple[str, Any]] = []  # (key of the company name, cash row)
        self.futures: dict[str, list] = {}  # underlying -> (iso, row) sorted
        self.options: dict[str, dict[str, list]] = {}  # underlying -> iso -> rows by strike
        self.isin: dict[str, list] = {}
        self.display: dict[str, str] = {}  # key -> the symbol as listed


class IndexBuilder:
    """Builds an ``Index`` from rows given in slices."""

    def __init__(self) -> None:
        self.ix = Index()
        self._iso: dict[str, str | None] = {}

    def _date(self, text):
        if text not in self._iso:
            self._iso[text] = expiry_iso(text)
        return self._iso[text]

    def add(self, rows) -> None:
        ix = self.ix
        for r in rows:
            typ = r.instrumenttype
            if typ in ("CE", "PE", "FUT"):
                u, iso = r.underlying, self._date(r.expiry)
                if not u or not iso:
                    continue
                ix.display.setdefault(key_of(u), u)
                if typ == "FUT":
                    ix.futures.setdefault(u, []).append((iso, r))
                else:
                    ix.options.setdefault(u, {}).setdefault(iso, []).append(r)
                continue
            k = key_of(r.symbol)
            ix.cash.setdefault(k, []).append(r)
            ix.display.setdefault(k, r.symbol)
            if r.name:
                ix.names.append((key_of(r.name), r))
            isin = isin_of(r.token)
            if isin:
                ix.isin.setdefault(isin, []).append(r)

    def finish(self) -> Index:
        for lst in self.ix.futures.values():
            lst.sort(key=lambda t: (t[0], t[1].exchange))
        for by in self.ix.options.values():
            for lst in by.values():
                lst.sort(key=lambda r: (r.strike or 0, r.instrumenttype))
        return self.ix


def build_index(rows) -> Index:
    """Everything in one go (tests and small masters); a large one is added in slices."""
    b = IndexBuilder()
    b.add(rows)
    return b.finish()


def _distance_one(a: str, b: str) -> bool:
    """True when ``a`` and ``b`` are one edit apart, a swap of two letters counting as one."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        d = [i for i in range(len(a)) if a[i] != b[i]]
        return len(d) == 1 or (len(d) == 2 and d[1] == d[0] + 1 and a[d[0]] == b[d[1]]
                               and a[d[1]] == b[d[0]])  # fmt: skip
    s, long = (a, b) if len(a) < len(b) else (b, a)
    return any(long[:i] + long[i + 1 :] == s for i in range(len(long)))


def suggest(word: str, vocabulary) -> tuple[str, ...]:
    """Names one edit away from a word of four or more letters, at most three."""
    k = key_of(word)
    if len(k) < 4:
        return ()
    found = sorted(
        (v for v in vocabulary if _distance_one(k, key_of(v))), key=lambda v: (len(v), v)
    )
    return tuple(found[:MAX_SUGGESTIONS])


def parse(query: str, price: float | None = None) -> dict:
    """The grammar: aliases, expiry words, strikes, sides, ATM and what is left as the name."""
    raw = query.strip()
    if _ISIN.match(raw.upper()):
        return {"isin": raw.upper(), "words": []}
    q = " " + " ".join(re.sub(r"(?<=\d),(?=\d{3})", "", raw.lower()).split()) + " "
    out: dict[str, Any] = {"words": [], "alias": None}
    for phrase, name in sorted(ALIASES.items(), key=lambda kv: -len(kv[0])):
        if f" {phrase} " in q:
            out["alias"] = name
            q = q.replace(f" {phrase} ", " ", 1)
            break
    for phrase, spec in (("next month", ("next_month",)), ("this month", ("month",)),
                         ("next week", ("next",)), ("this week", ("nearest",))):  # fmt: skip
        if f" {phrase} " in q:
            out["expiry"] = spec
            q = q.replace(f" {phrase} ", " ", 1)
    q = _GLUE.sub(r"\1 \2", q)
    toks = q.split()
    strike = side = None
    consumed: set[int] = set()
    for i, t in enumerate(toks):
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        if re.match(r"^\d{4}-\d{2}-\d{2}$", t):
            out["expiry"] = ("date", t)
            consumed.add(i)
        elif _NUM.match(t) and nxt in _MONTH_NAMES and 1 <= float(t) <= 31:
            out["expiry"] = ("day", _MONTH_NAMES[nxt], int(float(t)))
            consumed |= {i, i + 1}
    for i, t in enumerate(toks):
        if i in consumed:
            continue
        m = _ATM.match(t)
        if m:
            out["atm"] = int(m.group(2) or 0) * (-1 if m.group(1) == "-" else 1)
            consumed.add(i)
        elif t in _MONTH_NAMES:
            out.setdefault("expiry", ("month_name", _MONTH_NAMES[t]))
            consumed.add(i)
        elif t == "week":
            out.setdefault("expiry", ("nearest",))
            consumed.add(i)
        elif t == "month":
            out.setdefault("expiry", ("month",))
            consumed.add(i)
        elif t in ("fut", "future", "futures"):
            out["future"] = True
            consumed.add(i)
        elif t in ("ce", "pe", "call", "put"):
            side = _SIDES[t]
            consumed.add(i)
    for i, t in enumerate(toks):
        if i in consumed or not _NUM.match(t):
            continue
        prev = toks[i - 1] if i else ""
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        if nxt in _SIDES or prev in _SIDES or float(t) >= 100:
            strike = float(t)
            consumed.add(i)
            for j in (i - 1, i + 1):
                if 0 <= j < len(toks) and toks[j] in ("c", "p") and j not in consumed:
                    side = _SIDES[toks[j]]
                    consumed.add(j)
    out["strike"], out["side"] = strike, side
    out["words"] = [t for i, t in enumerate(toks) if i not in consumed]
    return out


def _spec_expiries(
    spec, expiries: list[str], now_ms: int, closes, index: bool = False
) -> list[str]:
    live = sorted(e for e in set(expiries) if dynamic.close_ms(e, closes, index=index) > now_ms)
    if spec is None:
        return live
    kind = spec[0]
    if kind in ("nearest", "next", "month", "next_month"):
        got = dynamic.pick_expiries(expiries, now_ms, closes, index=index)[kind]
        return [got] if got else []
    if kind == "date":
        return [e for e in live if e == spec[1]]
    if kind == "day":
        return [e for e in live if (int(e[5:7]), int(e[8:10])) == (spec[1], spec[2])]
    return [e for e in live if int(e[5:7]) == spec[1]]


def _order_key(price):
    return (
        (lambda r: (abs((r.strike or 0) - price), r.strike or 0, r.instrumenttype != "CE"))
        if (price is not None)
        else (lambda r: (r.strike or 0, r.instrumenttype != "CE"))
    )


def search(
    index: Index, query: str, *, price: float | None = None, now_ms: int, segment: str = "all",
    limit: int = MAX_HITS, closes: dict[str, int] | None = None, indices: Collection[str],
) -> Result:  # fmt: skip
    """Rows for a query, grouped and ordered as the design says; at most ``limit`` (50).
    ``indices`` are the index underlyings the master lists (review M1): which expiries are still
    open on an expiry day depends on it from 7 Dec 2026."""
    p = parse(query)
    limit = min(limit, MAX_HITS)
    if "isin" in p:
        rows = sorted(index.isin.get(p["isin"], []), key=lambda r: r.exchange != "NSE")
        return _done(rows, Understood(isin=p["isin"]), None, (), segment, limit)
    words = p["words"]
    name = p["alias"] or None
    if name is None and words:
        for n in range(len(words), 0, -1):
            k = key_of("".join(words[:n]))
            if k in index.display:
                name, words = index.display[k], words[n:]
                break
    wants_options = bool(p["side"] or p["strike"] is not None or "atm" in p or segment == "options")
    if name is None:
        return _by_name(index, " ".join(words), segment, limit, now_ms, closes, indices)
    k = key_of(name)
    cash = sorted(index.cash.get(k, []), key=lambda r: (r.exchange.endswith("BSE"), r.exchange))
    u = index.display.get(k, name)
    ix = expiry.is_index(u, indices)
    opt_by = index.options.get(u, {})
    spec = p.get("expiry")
    rows: list = []
    exps: list[str] = []
    atm = p.get("atm")
    needs_price = False
    if p.get("future") and not wants_options:
        iso_rows = index.futures.get(u, [])
        allowed = set(_spec_expiries(spec, [i for i, _ in iso_rows], now_ms, closes, ix))
        rows = [r for i, r in iso_rows if i in allowed]
        exps = sorted(allowed)
    elif wants_options:
        pool = list(opt_by)
        exps = _spec_expiries(spec, pool, now_ms, closes, ix)
        if spec is None and p["strike"] is None:
            exps = exps[:1]
        for e in exps:
            lst = opt_by[e]
            if atm is not None:
                if price is None:
                    needs_price = True
                    continue
                strikes = sorted({r.strike for r in lst})
                centre = dynamic.atm_strike(price, strikes)
                tgt = dynamic.offset_strike(strikes, centre, atm) if centre is not None else None
                lst = [r for r in lst if r.strike == tgt]
            elif p["strike"] is not None:
                lst = [r for r in lst if r.strike == p["strike"]]
            else:
                lst = sorted(lst, key=_order_key(price))
            if p["side"]:
                lst = [r for r in lst if r.instrumenttype == p["side"]]
            rows += lst
    else:
        rows = list(cash)
        iso_rows = index.futures.get(u, [])
        allowed = set(_spec_expiries(spec, [i for i, _ in iso_rows], now_ms, closes, ix))
        rows += [r for i, r in iso_rows if i in allowed]
        exps = sorted(allowed) if spec else []
    und = Understood(
        u, spec, tuple(exps), p["strike"], atm, p["side"], bool(p.get("future")), None,
        needs_price, wants_options,
    )  # fmt: skip
    pick = u if opt_by else None
    return _done(rows, und, pick, (), segment, limit)


def _done(rows, und, pick, sug, segment, limit) -> Result:
    hits = [(group_of(r), r) for r in rows]
    if segment != "all":
        hits = [h for h in hits if h[0] == segment]
    return Result(hits[:limit], und, pick, tuple(sug))


def _first_live(futures, now_ms: int, closes, index: bool = False) -> list:
    """The nearest future still open, for an underlying that has no cash listing."""
    live = [r for iso, r in futures if dynamic.close_ms(iso, closes, index=index) > now_ms]
    return live[:1]


def _by_name(
    index: Index, text: str, segment: str, limit: int, now_ms: int, closes, indices
) -> Result:
    """No underlying was named: exact symbol, then prefix, then substring, then company name."""
    k = key_of(text)
    und = Understood()
    if not k:
        return Result([], und, None, ())
    rank: dict[int, tuple[int, Any]] = {}
    for sym, shown in index.display.items():
        tier = 0 if sym == k else 1 if sym.startswith(k) else 2 if k in sym else None
        rows = index.cash.get(sym) or _first_live(
            index.futures.get(shown, []), now_ms, closes, expiry.is_index(shown, indices)
        )
        for r in rows if tier is not None else ():
            rank[id(r)] = (tier, r)
    for nk, r in index.names:
        if k in nk and id(r) not in rank:
            rank[id(r)] = (3, r)
    ordered = sorted(rank.values(), key=lambda t: (t[0], len(t[1].symbol), t[1].symbol,
                                                   t[1].exchange))  # fmt: skip
    rows = [r for _, r in ordered]
    sug = () if rows else suggest(text, index.display.values())
    return _done(rows, und, None, sug, segment, limit)
