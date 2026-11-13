"""The exchange's trading hours, regime by regime: the one table every reader takes them from.

Continuous trading in IST on a trading day, the end not included:

    from 3 Aug 2026 (SEBI's closing auction, NSE/CMTR/73362 and 75479):
      cash (NSE_EQ, BSE_EQ) and indices              09:15-15:30
      a stock with derivatives ("closes by auction")  09:15-15:15, then the closing auction: orders
                                                      from 15:20, a random close in 15:28-15:30
      F&O (NSE_FO, BSE_FO: OpenAlgo's NFO and BFO)    09:15-15:40, an expiring contract to 15:40
    from 7 Dec 2026 (SEBI HO/47/11/11(3)2025-MRD-POD2/I/23843/2026 of 15 Oct 2026, para 4.2):
      cash (every stock) and indices                 09:15-15:30; a stock with derivatives then
                                                      closes by auction 15:30-15:40, order entry
                                                      closing at a random moment in 15:38-15:40
      F&O                                            09:15-15:45; an expiring index contract stops
                                                      at 15:30 on its expiry day, a stock contract
                                                      trades to 15:45

The cash market's pre-open starts at 09:00 (``PRE_OPEN``) in both.

What SEBI's circular of 15 Oct 2026 states (para 4.2, read 13 Nov 2026): the auction 15:30-15:40
with its random close from 15:38, index derivatives to 15:45 and an expiring one to 15:30 on its
expiry day, stock derivatives to 15:45 on every day, the post-close session 15:50-16:00. It does not
say which stocks the auction covers. NSE's two circulars of 6 Nov 2026, as reported, keep it for
securities with derivatives contracts on at least one exchange, subject to exceptions (not on the
ex-date of a scheme of arrangement, which is not modelled here): so ``every_stock`` is False in
both regimes, and from 7 Dec a stock without derivatives ends at 15:30 with no auction.

Every reader takes its times from here, never from a constant of its own:

    sudarshan/alerts/market/sessions.py  each segment's session, a stock's closing auction and a
                                         contract's expiry stop: market alerts, the screener and
                                         the portfolio
    sudarshan/autologin/schedule.py      the day's session (``session_of``), around which auto-login,
                                         the feed, the alerts' window and the backup keep their
                                         margins
    database/market_calendar_db.py       OpenAlgo's NFO and BFO close on a date, for a row that holds
                                         a close OpenAlgo or SUDARSHAN shipped; the chart's hours
                                         read it from there
    sandbox/position_manager.py          when the sandbox settles an expiring NFO or BFO contract
    sudarshan/index_contracts.py         which contracts are an index's, from OpenAlgo's master
                                         (``index_underlyings``), for every reader inside OpenAlgo

``sudarshan/tests/regimes_vectors.json`` pins the table, the calendar the server derives from it and
the phone chart's hours built from the server's answer (frontend/src/lib/trading/sessionHours.ts).

Pure: dates in, minutes out. No I/O, no clock, no logging.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import date

#: The cash market's pre-open starts at 09:00, 15 minutes before continuous trading.
PRE_OPEN = 9 * 60


@dataclass(frozen=True, slots=True)
class Regime:
    """The exchange's timings from one day on, in IST minutes after midnight."""

    since: date
    cash: tuple[int, int]
    #: Continuous trading of a stock that closes by auction.
    auction: tuple[int, int]
    fo: tuple[int, int]
    #: An expiring index contract stops at this minute on its expiry day.
    index_expiry_end: int
    #: The auction's print may come until this many minutes after the cash close: five minutes
    #: after the auction's latest close in both regimes (15:35, and from 7 Dec 2026 15:45), so a
    #: print stamped a moment after a random close in its last second is still the auction's.
    print_after_cash: int
    #: The auction's price is expected this many minutes after the cash close (X6j).
    close_after_cash: int
    #: Every cash stock closes by auction, not only those with derivatives (False in both
    #: regimes: NSE keeps the auction for stocks with derivatives, circulars of 6 Nov 2026).
    every_stock: bool


#: Each regime from its first day; the first one also covers earlier days.
REGIMES: tuple[Regime, ...] = (
    Regime(date(2026, 8, 3), (555, 930), (555, 915), (555, 940), 940, 5, 0, False),
    Regime(date(2026, 12, 7), (555, 930), (555, 930), (555, 945), 930, 15, 10, False),
)
#: Underlyings of index derivatives every reader knows without the master contract: an OpenAlgo
#: derivative symbol is the underlying, then DDMMMYY. The master adds any other one
#: (``index_underlyings``), BSE's Focused IT under whatever name Upstox gives it among them.
INDEX_UNDERLYINGS = ("NIFTYNXT50", "MIDCPNIFTY", "BANKNIFTY", "FINNIFTY", "NIFTYFPI", "SENSEX50",
                     "BANKEX", "SENSEX", "NIFTY")  # fmt: skip
#: An OpenAlgo derivative symbol: <underlying><DDMMMYY>FUT or <underlying><DDMMMYY><strike><CE|PE>.
_DERIVATIVE = re.compile(
    r"(?P<under>[A-Z0-9&-]+?)\d{2}(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)\d{2}"
    r"(?:FUT|\d+(?:\.\d+)?(?:CE|PE))"
)
#: How a master contract marks its futures, where it tells an index's from a stock's.
_INDEX_FUTURES, _STOCK_FUTURES = frozenset({"FUTIDX", "OPTIDX"}), frozenset({"FUTSTK", "OPTSTK"})


def regime_on(day: date | None) -> Regime:
    """The regime in force on ``day``; the newest one when no day is given."""
    if day is None:
        return REGIMES[-1]
    found = REGIMES[0]
    for regime in REGIMES:
        if regime.since <= day:
            found = regime
    return found


def fo_close(day: date) -> int:
    """The F&O close on ``day`` in IST minutes: 15:40, from 7 Dec 2026 15:45."""
    return regime_on(day).fo[1]


def fo_changes() -> tuple[tuple[date, tuple[int, int]], ...]:
    """Each later regime's first day and F&O hours: the days hours written for the first regime
    (the NFO and BFO close OpenAlgo's market calendar ships, 15:40) change on by themselves."""
    return tuple((regime.since, regime.fo) for regime in REGIMES[1:])


def underlying_of(symbol: str) -> str | None:
    """The underlying of an OpenAlgo derivative symbol (NIFTYFPI for NIFTYFPI29DEC2618000CE,
    360ONE for 360ONE29DEC26FUT), or None for anything else (a cash symbol, an index)."""
    found = _DERIVATIVE.fullmatch(symbol.strip().upper())
    return found["under"] if found else None


def index_derivative(symbol: str, underlyings: Collection[str] = INDEX_UNDERLYINGS) -> bool:
    """True for an OpenAlgo derivative symbol on an index: NIFTY25NOV2625900CE, BANKNIFTY...FUT.
    ``underlyings`` are the index underlyings the master contract lists (``index_underlyings``);
    without it the ones every reader knows. The whole underlying is compared, so NIFTYFPI is not
    read as NIFTY nor BANKINDIA as an index."""
    underlying = underlying_of(symbol)
    return underlying is not None and underlying in underlyings


def index_underlyings(
    futures: Iterable[tuple[str, str]],
    *,
    cash: Collection[str] = (),
    indices: Collection[str] = (),
) -> frozenset[str]:
    """The underlyings of the index derivatives a master contract lists, from its F&O future rows
    as (symbol, instrument type), and the symbols it lists as cash stocks (``cash``: NSE, BSE)
    and as indices (``indices``: NSE_INDEX, BSE_INDEX). Every index with derivatives has futures.

    A master that marks its futures says it (FUTIDX an index's, FUTSTK a stock's). Upstox's marks
    every one FUT: an underlying listed as an index, or not listed as a cash stock, is an index's.
    A master holding no cash stock at all (read while it is being downloaded) makes no such guess.
    The underlyings every reader knows (INDEX_UNDERLYINGS) are always in."""
    found = set(INDEX_UNDERLYINGS)
    for symbol, kind in futures:
        underlying = underlying_of(symbol)
        mark = str(kind or "").upper()
        if underlying is None or mark in _STOCK_FUTURES:
            continue
        if mark in _INDEX_FUTURES or underlying in indices or (cash and underlying not in cash):
            found.add(underlying)
    return frozenset(found)


def expiry_stop(expiry: date, *, index: bool) -> int:
    """When an expiring F&O contract stops trading on its expiry day, in IST minutes: the F&O
    close (15:40), but from 7 Dec 2026 an expiring index contract stops at 15:30 and a stock
    contract trades to 15:45."""
    regime = regime_on(expiry)
    return regime.index_expiry_end if index else regime.fo[1]
