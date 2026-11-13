"""Follow rows to contracts (M8.10, M8.11, W15): the rules of ``dynamic.py`` over the master, the
calendar's closes and the best price there is.

Every outside fact is an argument (the master's ``Index``, ``row_of`` for the underlying's own row,
the index underlyings the master lists, and the calendar and the feed as callables), so a test
hands in its own and nothing here has a clock, a thread or I/O of its own. The contract of a row is looked up in the master, never built
from a name; a side the master lacks is said (for a strike row, which one). The price is, in
order: the page's own tick while its socket is connected (however old), the page's snapshot, the
feed's last tick (asked once for every row that needs it), and for a closed market the last price
the page kept, said so; none means the row waits.
"""

from __future__ import annotations

from collections.abc import Callable, Collection
from typing import Any

from sudarshan.watchlists import dynamic, numfmt, view
from sudarshan.watchlists import expiry as stops
from sudarshan.watchlists.dynamic import Price
from sudarshan.watchlists.words import dynamic as dwords
from sudarshan.watchlists.words import rows as words

#: A row that has just rolled says so for this long (the page shows the line for a minute), and
#: its answer carries ``rolled`` true for the same time, so the page need not read the words.
ROLL_WINDOW_MS = 60_000


def fo_exchange(underlying_exchange: str) -> str:
    """The derivatives exchange of an underlying's exchange."""
    return "NFO" if underlying_exchange.startswith("NSE") else "BFO"


def cash_exchange(underlying_exchange: str) -> str:
    """The calendar's name for the exchange the underlying trades on."""
    return "NSE" if underlying_exchange.startswith("NSE") else "BSE"


def _page_price(item: dict, connected: bool) -> dict:
    """What the page sent, sorted into the slots of ``choose_price``."""
    value, at, source = item.get("price"), item.get("price_at"), item.get("source")
    out: dict[str, Any] = {"connected": connected}
    if value is None or isinstance(value, bool) or value <= 0:
        return out
    got = (float(value), at)
    if source == "tick" and connected:
        out["tick"] = got
    elif source in ("tick", "last"):
        out["last"] = got  # a tick on a socket that has dropped is the page's last price
    else:
        out["snapshot"] = got
    return out


class Book:
    """The master's reference maps for one request: the expiries and strikes an underlying lists on
    one derivatives exchange, found once per (underlying, exchange)."""

    def __init__(self, ix) -> None:
        self.ix = ix
        self._options: dict[tuple[str, str], dict[str, list]] = {}
        self._futures: dict[tuple[str, str], dict[str, Any]] = {}

    def options(self, name: str, fo: str) -> dict[str, list]:
        got = self._options.get((name, fo))
        if got is None:
            got = {}
            for iso, rows in self.ix.options.get(name, {}).items():
                keep = [r for r in rows if r.exchange == fo]
                if keep:
                    got[iso] = keep
            self._options[name, fo] = got
        return got

    def futures(self, name: str, fo: str) -> dict[str, Any]:
        got = self._futures.get((name, fo))
        if got is None:
            got = {iso: r for iso, r in self.ix.futures.get(name, ()) if r.exchange == fo}
            self._futures[name, fo] = got
        return got


def _centre(rule: dict, strikes: list[float], held) -> float | None:
    """The strike the ATM rule's band was held on: the strike last shown, taken back by the
    row's offset in strike steps. ``None`` when it is not on this expiry's ladder."""
    if rule["strike_rule"] != "atm" or held is None or held not in strikes:
        return None
    i = strikes.index(held) - int(rule["offset"] or 0)
    return strikes[i] if 0 <= i < len(strikes) else None


def _closes(
    expiries: list[str],
    now_ms: int,
    closes_of: Callable[[list[str]], dict[str, int]],
    *,
    index: bool,
    exchange: str,
) -> dict[str, int]:
    """The dated closes that matter: today's expiry (is it still open?) and the ones the rule
    picks (when does it end?), asked of the calendar in two steps, each once."""
    today = numfmt.ist_date(now_ms).isoformat()
    got = closes_of([today]) if today in expiries else {}
    picked = dynamic.pick_expiries(expiries, now_ms, got, index=index, exchange=exchange)
    more = [e for e in picked.values() if e and e not in got and e != today]
    return {**got, **(closes_of(more) if more else {})}


def _entry(rule_id: int, state: str, text: str, **more) -> dict:
    return {"id": rule_id, "state": state, "ce": None, "pe": None, "future": None,
            "strike": None, "band": None, "valid_until": None, "price": None,
            "price_at": None, "words": text, "rolled": False, **more}  # fmt: skip


def resolve_rows(
    rules: dict[int, dict],
    items: dict[int, dict],
    *,
    now_ms: int,
    connected: bool,
    ix,
    row_of: Callable[[str, str], Any],
    isin: Callable[[Any], str | None],
    closes_of: Callable[[str, list[str]], dict[str, int]],
    is_open: Callable[[str, int], bool],
    feed: Callable[[list[str]], dict[str, tuple[float, int | None]]],
    indices: Collection[str],
) -> list[dict]:
    """One ``Resolved`` per id of ``items`` (in its order). ``rules`` is the stored rule of each id
    (a missing one is a row that is gone); ``items`` is what the page sent. ``indices`` are the
    index underlyings the master lists (``sessions.index_underlyings``, review M1): an index's
    contract stops at 15:30 on its expiry day from 7 Dec 2026, a stock's at 15:45."""
    book, out = Book(ix), []
    pages = {i: _page_price(items[i], connected) for i in items}
    # one asking of the feed for every option row that has no price of the page's to use
    need = {}
    for i, rule in rules.items():
        page = pages.get(i, {})
        if rule["kind"] == "month_future" or "tick" in page or "snapshot" in page:
            continue
        under = row_of(rule["underlying_exchange"], rule["underlying"])
        if under is not None and getattr(under, "token", None):
            need[i] = under.token
    found = feed(sorted(set(need.values()))) if need else {}
    opened: dict[str, bool] = {}
    for i in items:
        rule = rules.get(i)
        if rule is None:
            out.append(_entry(i, "missing", words.ROW_GONE))
            continue
        name, ux = rule["underlying"], rule["underlying_exchange"]
        fo, cash = fo_exchange(ux), cash_exchange(ux)
        if cash not in opened:
            opened[cash] = is_open(cash, now_ms)
        page = pages[i]
        price = dynamic.choose_price(
            tick=page.get("tick"), connected=connected, snapshot=page.get("snapshot"),
            feed=found.get(need.get(i)), last=page.get("last"), closed=not opened[cash],
        )  # fmt: skip
        futures = rule["kind"] == "month_future"
        listed = book.futures(name, fo) if futures else book.options(name, fo)
        expiries = sorted(listed)
        regime = {"index": stops.is_index(name, indices), "exchange": fo}
        closes = _closes(expiries, now_ms, lambda d, fo=fo: closes_of(fo, d), **regime)
        expiry = dynamic.pick_expiries(expiries, now_ms, closes, **regime)[rule["expiry_rule"]]
        rows = listed.get(expiry, []) if not futures and expiry else []
        strikes = sorted({r.strike for r in rows if r.strike is not None})
        sides: dict[tuple[str, float], tuple[str, ...]] = {}
        for r in rows:
            if r.strike is not None:
                sides[expiry, r.strike] = (*sides.get((expiry, r.strike), ()), r.instrumenttype)
        res = dynamic.resolve(
            rule, expiries, {expiry: strikes} if expiry else {}, now_ms, price,
            _centre(rule, strikes, items[i].get("held")), closes, sides, **regime,
        )  # fmt: skip
        text = dwords.describe(res, name, rule, now_ms)
        got: dict[str, Any] = {}
        rolled = False
        if res.state in ("ok", "closed"):
            if futures:
                got["future"] = _contract(listed[res.expiry], isin)
            else:
                sides_wanted = (
                    ("CE", "PE") if rule["kind"] == "strike_pair" else (rule["opt_right"],)
                )
                for r in rows:
                    if r.strike == res.strike and r.instrumenttype in sides_wanted:
                        got[r.instrumenttype.lower()] = _contract(r, isin)
            before = dynamic.pick_expiries(expiries, now_ms - ROLL_WINDOW_MS, closes, **regime)
            old = before[rule["expiry_rule"]]
            if old and old != res.expiry:
                text = dwords.rolled(res.expiry, dynamic.close_ms(old, closes, **regime))
                rolled = True
        out.append(_entry(
            i, res.state, text, strike=res.strike,
            band=None if res.band is None else {"low": res.band[0], "high": res.band[1]},
            valid_until=res.valid_until, price=None if futures else res.price.value,
            price_at=None if futures else res.price.at_ms, rolled=rolled, **got,
        ))  # fmt: skip
    return out


def _contract(row, isin) -> dict:
    return view.contract(row.exchange, row.symbol, row, isin(row))
