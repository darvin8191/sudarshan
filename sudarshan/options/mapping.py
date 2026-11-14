"""Upstox's option chain as rows of OpenAlgo symbols (M11.1, O1): the target and the mapping.

Pure: an answer, the master's reference maps and lookups come in as arguments; no I/O, no clock,
no logging.

**The target.** ``target_of`` names what one read asks Upstox for: the underlying's own instrument
key (the master's ``token`` of its index or cash row) and the listed contracts of the expiry as a
ladder ``{(strike, "CE" | "PE"): row}``. NFO and BFO only (Upstox offers no chain for MCX).

**The mapping.** Every leg Upstox names becomes a record carrying the OpenAlgo symbol the proxy
subscribes. A leg is matched by its ``instrument_key`` against the master's ``token``; when that
finds no row, or a row of another strike or side, it is matched by its strike and side on the
expiry's ladder instead, and counted (``by_strike``). Design fact 2 (that Upstox's key equals the
master's token) is not verified until the first live read: with it false every leg still maps, and
the count says so. A leg neither way finds is left out and counted; a strike with no leg left is
dropped.

**Figures.** A figure Upstox did not send, or sent as something that is not a finite number, is
absent from its leg, never 0. A 0 Upstox did send stays 0 (an untraded strike's price, a strike
with no open interest): the phone treats a price of 0 as no price. ``pcr`` is every mapped put's
open interest over every mapped call's, absent while no call carries any.

Design fact 2 also leaves ``prev_oi``'s meaning to the first live read (the previous day's closing
open interest, per Upstox's page): it is passed on under that name and nothing here depends on it.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any, NamedTuple

from sudarshan.watchlists import search

FO_EXCHANGES = ("NFO", "BFO")
#: Where the underlying of each F&O exchange is listed, the index first.
UNDERLYING_ON = {"NFO": ("NSE_INDEX", "NSE"), "BFO": ("BSE_INDEX", "BSE")}
SIDES = (("ce", "call_options", "CE"), ("pe", "put_options", "PE"))
#: Upstox's name of a figure -> the answer's.
MARKET = (("ltp", "ltp"), ("bid_price", "bid"), ("ask_price", "ask"), ("oi", "oi"),
          ("prev_oi", "prev_oi"), ("volume", "volume"), ("close_price", "close"))  # fmt: skip
GREEKS = ("iv", "delta", "gamma", "theta", "vega")
NO_ROWS_LIST, NO_LEGS = "no list of strikes", "no strike names a call or a put"


class Target(NamedTuple):
    underlying: str  # as the master lists it
    exchange: str  # NFO or BFO
    instrument_key: str  # the underlying's own, for Upstox
    underlying_exchange: str  # NSE_INDEX, NSE, BSE_INDEX or BSE
    expiry: str  # ISO
    ladder: dict  # (strike, "CE" | "PE") -> the master's row


class Mapped(NamedTuple):
    strikes: list[dict]
    spot: float | None
    pcr: float | None
    left_out: int  # legs Upstox named that today's master lacks
    by_strike: int  # legs matched by strike and side, their key not being the master's token


def number(value: Any) -> float | None:
    """A finite number, or a number sent as text; anything else is not a figure."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            return None
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def target_of(ix: search.Index, exchange: str, underlying: str, expiry: str) -> Target | None:
    """What to ask Upstox for, or ``None`` when the master lists no such chain on ``exchange``."""
    if exchange not in FO_EXCHANGES:
        return None
    name = ix.display.get(search.key_of(underlying), underlying)
    rows = [r for r in ix.options.get(name, {}).get(expiry, ()) if r.exchange == exchange]
    ladder = {(float(r.strike), r.instrumenttype): r for r in rows if r.strike is not None}
    if not ladder:
        return None
    cash = {r.exchange: r for r in ix.cash.get(search.key_of(name), ())}
    for home in UNDERLYING_ON[exchange]:
        row = cash.get(home)
        token = getattr(row, "token", None)
        if isinstance(token, str) and token:
            return Target(name, exchange, token, home, expiry, ladder)
    return None


def rows_of(answer: Any) -> list | None:
    """The list of strikes where Upstox's page puts it (``data``), or ``None``."""
    rows = answer.get("data") if isinstance(answer, dict) else None
    return rows if isinstance(rows, list) else None


def unusable(answer: Any) -> str | None:
    """Why an answer Upstox calls a success cannot be read as a chain, or ``None`` when it can.
    An empty list is a chain with no strikes, which is readable."""
    rows = rows_of(answer)
    if rows is None:
        return NO_ROWS_LIST
    legs = [r for r in rows if isinstance(r, dict)
            and any(isinstance(r.get(key), dict) for _, key, _ in SIDES)]  # fmt: skip
    return NO_LEGS if rows and not legs else None


def shape_of(answer: Any) -> dict:
    """The key names of an answer, its first row and that row's call: names only, never a value,
    for the one log line an unreadable answer gets."""

    def names(value: Any) -> list[str]:
        return sorted(str(k)[:40] for k in value)[:20] if isinstance(value, dict) else []

    rows = rows_of(answer)
    first = rows[0] if rows else None
    call = first.get("call_options") if isinstance(first, dict) else None
    return {"answer": names(answer), "row": names(first), "call": names(call)}


def _row_of(leg: dict, strike: float, side: str, target: Target,
            by_token: Callable[[str], Any]) -> tuple[Any, bool]:  # fmt: skip
    """``(the master's row, matched by strike)`` of one leg, or ``(None, False)``."""
    key = leg.get("instrument_key")
    row = by_token(key) if isinstance(key, str) and key else None
    if row is not None and target.ladder.get((strike, side)) is row:
        return row, False
    row = target.ladder.get((strike, side))
    return row, row is not None


def _leg(leg: dict, row: Any) -> dict:
    out: dict[str, Any] = {
        "symbol": row.symbol,
        "exchange": row.exchange,
        "lot": getattr(row, "lotsize", None) or None,
        "tick": getattr(row, "tick_size", None) or None,
    }
    market = leg.get("market_data")
    for theirs, ours in MARKET if isinstance(market, dict) else ():
        value = number(market.get(theirs))
        if value is not None:
            out[ours] = value
    greeks = leg.get("option_greeks")
    for name in GREEKS if isinstance(greeks, dict) else ():
        value = number(greeks.get(name))
        if value is not None:
            out[name] = value
    return out


def map_chain(answer: Any, target: Target, by_token: Callable[[str], Any]) -> Mapped:
    """The chain of ``target``'s expiry out of Upstox's answer, strikes ascending."""
    strikes: dict[float, dict] = {}
    spot, left_out, by_strike = None, 0, 0
    puts = calls = 0.0
    for raw in rows_of(answer) or ():
        if not isinstance(raw, dict):
            continue
        if spot is None:
            spot = number(raw.get("underlying_spot_price"))
        strike = number(raw.get("strike_price"))
        said = raw.get("expiry")
        other_expiry = isinstance(said, str) and said[:10] != target.expiry
        for ours, theirs, side in SIDES:
            leg = raw.get(theirs)
            if not isinstance(leg, dict):
                continue
            row, matched = (None, False) if strike is None or other_expiry else _row_of(
                leg, strike, side, target, by_token)  # fmt: skip
            if row is None:
                left_out += 1
                continue
            by_strike += matched
            record = _leg(leg, row)
            strikes.setdefault(strike, {"strike": strike})[ours] = record
            if ours == "pe":
                puts += record.get("oi", 0.0)
            else:
                calls += record.get("oi", 0.0)
    return Mapped(
        [strikes[s] for s in sorted(strikes)],
        spot,
        puts / calls if calls > 0 else None,
        left_out,
        by_strike,
    )
