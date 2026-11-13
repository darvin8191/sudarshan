"""The exchange's trading hours, regime by regime: the one table every reader takes them from.

Continuous trading in IST on a trading day, the end not included:

    from 3 Aug 2026 (SEBI's closing auction, NSE/CMTR/73362 and 75479):
      cash (NSE_EQ, BSE_EQ) and indices              09:15-15:30
      a stock with derivatives ("closes by auction")  09:15-15:15, then the closing auction: orders
                                                      from 15:20, a random close in 15:28-15:30
      F&O (NSE_FO, BSE_FO: OpenAlgo's NFO and BFO)    09:15-15:40, an expiring contract to 15:40
    from 7 Dec 2026 (SEBI HO/47/11/11(3)2025-MRD-POD2/I/23843/2026 of 15 Oct 2026, para 4.2):
      cash, every stock, and indices                 09:15-15:30; every stock's closing auction
                                                      15:30-15:40, order entry closing at a random
                                                      moment in 15:38-15:40
      F&O                                            09:15-15:45; an expiring index contract stops
                                                      at 15:30 on its expiry day, a stock contract
                                                      trades to 15:45

The cash market's pre-open starts at 09:00 (``PRE_OPEN``) in both.

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

``sudarshan/tests/regimes_vectors.json`` pins the table, the calendar the server derives from it and
the phone chart's hours built from the server's answer (frontend/src/lib/trading/sessionHours.ts).

Pure: dates in, minutes out. No I/O, no clock, no logging.
"""

from __future__ import annotations

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
    #: The auction's print may come until this many minutes after the cash close.
    print_after_cash: int
    #: The auction's price is expected this many minutes after the cash close (X6j).
    close_after_cash: int
    #: Every cash stock closes by auction, not only those with derivatives.
    every_stock: bool


#: Each regime from its first day; the first one also covers earlier days.
REGIMES: tuple[Regime, ...] = (
    Regime(date(2026, 8, 3), (555, 930), (555, 915), (555, 940), 940, 5, 0, False),
    Regime(date(2026, 12, 7), (555, 930), (555, 930), (555, 945), 930, 10, 10, True),
)
#: Underlyings of index derivatives: an OpenAlgo derivative symbol starts with one, then DDMMMYY.
INDEX_UNDERLYINGS = ("NIFTYNXT50", "MIDCPNIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX50", "BANKEX",
                     "SENSEX", "NIFTY")  # fmt: skip


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


def index_derivative(symbol: str) -> bool:
    """True for an OpenAlgo derivative symbol on an index: NIFTY25NOV2625900CE, BANKNIFTY...FUT."""
    for name in INDEX_UNDERLYINGS:
        rest = symbol[len(name) :]
        if (
            symbol.startswith(name)
            and len(rest) >= 7
            and rest[:2].isdigit()
            and rest[2:5].isalpha()
        ):
            return rest[5:7].isdigit()
    return False


def expiry_stop(expiry: date, *, index: bool) -> int:
    """When an expiring F&O contract stops trading on its expiry day, in IST minutes: the F&O
    close (15:40), but from 7 Dec 2026 an expiring index contract stops at 15:30 and a stock
    contract trades to 15:45."""
    regime = regime_on(expiry)
    return regime.index_expiry_end if index else regime.fo[1]
