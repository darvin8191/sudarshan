"""Trading sessions per segment: the one table every part of market alerts reads (M10).

Continuous trading in IST on a trading day, the end not included, in two regimes:

    from 3 Aug 2026 (SEBI's closing auction, NSE/CMTR/73362 and 75479):
      NSE_EQ, BSE_EQ, NSE_INDEX, BSE_INDEX           09:15-15:30
      a stock with derivatives ("closes by auction")  09:15-15:15, then the closing auction:
                                                      orders 15:20-15:30, matched 15:30-15:35
      NSE_FO, BSE_FO                                 09:15-15:40
    from 7 Dec 2026 (SEBI HO/47/11/11(3)2025-MRD-POD2/I/23843/2026 of 15 Oct 2026):
      cash, every stock, and indices                 09:15-15:30; every stock's auction
                                                      15:30-15:40
      NSE_FO, BSE_FO                                 09:15-15:45; an expiring index contract
                                                      stops at 15:30 on its expiry day

A stock that closes by auction (a CAS key, cross-design X6) has its own session, key
``NSE_EQ+auction`` (``auction=True``): its continuous trading ends 15 minutes before the cash
close, and after that its quotes are the auction's order book, never judged. The auction's print
is no trade of that session: it may come from the continuous end until the auction is matched
(``Session.auction_until``: the cash close plus 5 minutes, 15:35; from 7 Dec 2026 the auction's
end, 15:40), and clock.auction judges it on its own terms. It is the official close: it belongs
to the day's last candle on the cash session's grid (the 15:29 minute, the 15:15-15:30 candle,
the day). Which stocks close by auction is the runtime's to say each morning
(``closes_by_auction``: a stock with futures in the master contract; from 7 Dec 2026 every stock).
Trades of the post-close session (15:50-16:00, at the closing price) are refused by the gate.

It decides when a value is inside the session (the gate), where the last candle of a day ends,
what "today" means for a % move or a day high, when a contract's alerts stop on its expiry day
and what "Market closed" says.

A special day (a Muhurat session) gives its own window in the extra-days file, as the exchange
announces the whole day: from the start of its pre-open to the F&O close, for example
``2026-11-08 14:05-15:40``. That day each segment starts with normal trading, 15 minutes after the
window opens, and ends as long before the window's end as it usually ends before the F&O close:
cash and indices 10 minutes before it, a stock that closes by auction 25, F&O at it. So 8 Nov 2026
trades 14:20-15:30 in cash (14:20-15:15 for such stocks, NSE/CMTR/76941, their auction matched by
15:35) and 14:20-15:40 in F&O. From 7 Dec 2026 the derivatives close 5 minutes after the
10-minute auction: cash 15 minutes before the window's end.

Pure: whether a day trades and its own window are arguments. The runtime reads both through
sudarshan.autologin.schedule (``TradingCalendar.is_trading_day`` and ``day_windows``); nothing
here opens a file or reads the clock.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sudarshan.autologin.schedule import IST

#: Continuous trading per segment, in IST minutes after midnight: (start, end), as from 3 Aug 2026
#: (the regimes below refine it by day; this is what an alert's form checks its times against).
SEGMENTS: dict[str, tuple[int, int]] = {
    "NSE_EQ": (555, 930),
    "BSE_EQ": (555, 930),
    "NSE_INDEX": (555, 930),
    "BSE_INDEX": (555, 930),
    "NSE_FO": (555, 940),
    "BSE_FO": (555, 940),
}
INDEX_SEGMENTS = frozenset({"NSE_INDEX", "BSE_INDEX"})
FO_SEGMENTS = frozenset({"NSE_FO", "BSE_FO"})
#: Cash segments where a stock with derivatives closes by auction; its session key's suffix.
AUCTION_SEGMENTS = frozenset({"NSE_EQ", "BSE_EQ"})
AUCTION = "+auction"
#: A special day's window starts with its pre-open; normal trading starts this much later.
PRE_OPEN_MIN = 15


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
    #: Every cash stock closes by auction, not only those with derivatives.
    every_stock: bool


#: Each regime from its first day; the first one also covers earlier days.
REGIMES: tuple[Regime, ...] = (
    Regime(date(2026, 8, 3), (555, 930), (555, 915), (555, 940), 940, 5, False),
    Regime(date(2026, 12, 7), (555, 930), (555, 930), (555, 945), 930, 10, True),
)
#: Underlyings of index derivatives: an OpenAlgo derivative symbol starts with one, then DDMMMYY.
INDEX_UNDERLYINGS = ("NIFTYNXT50", "MIDCPNIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX50", "BANKEX",
                     "SENSEX", "NIFTY")  # fmt: skip


@dataclass(frozen=True, slots=True)
class Session:
    segment: str
    #: The trading day as YYYYMMDD (20261108), the key every per-session record uses.
    key: int
    #: Epoch milliseconds; ``end`` is not part of the session.
    start: int
    end: int
    #: A stock that closes by auction: its print may come from ``end`` up to this (epoch ms).
    auction_until: int | None = None


def segment_of(instrument_key: str) -> str:
    """'NSE_EQ|INE002A01018' -> 'NSE_EQ'."""
    return instrument_key.partition("|")[0]


def day_key(day: date) -> int:
    return day.year * 10000 + day.month * 100 + day.day


def key_day(key: int) -> date:
    return date(key // 10000, key // 100 % 100, key % 100)


def epoch_ms(day: date, minute: int) -> int:
    """``minute`` minutes after midnight IST on ``day``, in epoch milliseconds."""
    midnight = datetime(day.year, day.month, day.day, tzinfo=IST)
    return int((midnight + timedelta(minutes=minute)).timestamp()) * 1000


def _regime(day: date | None) -> Regime:
    """The regime in force on ``day``; the newest one when no day is given."""
    if day is None:
        return REGIMES[-1]
    found = REGIMES[0]
    for regime in REGIMES:
        if regime.since <= day:
            found = regime
    return found


def hours(
    segment: str,
    window: tuple[int, int] | None = None,
    *,
    day: date | None = None,
    auction: bool = False,
) -> tuple[int, int] | None:
    """The segment's continuous session in IST minutes on ``day``: its usual hours, or its share
    of a special window. ``auction``: a cash stock that closes by auction.

    None for a segment market alerts do not cover, and for a window too short to hold a session.
    """
    if segment not in SEGMENTS:
        return None
    regime = _regime(day)
    usual = regime.fo if segment in FO_SEGMENTS else regime.cash
    if auction and segment in AUCTION_SEGMENTS:
        usual = regime.auction
    if window is None:
        return usual
    opens, closes = window
    start = opens + PRE_OPEN_MIN
    end = min(closes, closes - (regime.fo[1] - usual[1]))  # never past the window's end
    return (start, end) if start < end else None


def session(
    segment: str,
    day: date,
    *,
    trading_day: bool,
    window: tuple[int, int] | None = None,
    auction: bool = False,
) -> Session | None:
    """The segment's session on ``day``, or None when it does not trade that day. A stock that
    closes by auction gets the key ``<segment>+auction`` and the end of its auction's print."""
    span = hours(segment, window, day=day, auction=auction) if trading_day else None
    if span is None:
        return None
    start, end = epoch_ms(day, span[0]), epoch_ms(day, span[1])
    if not (auction and segment in AUCTION_SEGMENTS):
        return Session(segment, day_key(day), start, end)
    cash = hours(segment, window, day=day)
    until = epoch_ms(day, cash[1] + _regime(day).print_after_cash)
    return Session(segment + AUCTION, day_key(day), start, end, until)


def sessions_of(
    day: date, *, trading_day: bool, window: tuple[int, int] | None = None
) -> dict[str, Session]:
    """Every session key's session on ``day``: each segment, and each cash segment's stocks that
    close by auction (``NSE_EQ+auction``). What clock.Market carries."""
    found = {}
    for segment in SEGMENTS:
        span = session(segment, day, trading_day=trading_day, window=window)
        if span is not None:
            found[segment] = span
        if segment in AUCTION_SEGMENTS:
            span = session(segment, day, trading_day=trading_day, window=window, auction=True)
            if span is not None:
                found[segment + AUCTION] = span
    return found


def closes_by_auction(segment: str, day: date, *, derivatives: bool) -> bool:
    """Whether a cash stock closes by auction on ``day`` (a CAS key): one with derivatives in
    the master contract since 3 Aug 2026, every stock from 7 Dec 2026. BSE's list is taken to be
    NSE's until a check of BSE's says otherwise."""
    return segment in AUCTION_SEGMENTS and (derivatives or _regime(day).every_stock)


def auction_window(
    segment: str, day: date, *, trading_day: bool, window: tuple[int, int] | None = None
) -> tuple[int, int] | None:
    """A CAS key's closing auction on ``day``, (from, until) in epoch ms: from its continuous
    end until its print may come. What every row the phone gets for such a key carries
    (``auction_from``, ``auction_until``); the phone never computes sessions."""
    found = session(segment, day, trading_day=trading_day, window=window, auction=True)
    if found is None or found.auction_until is None:
        return None
    return found.end, found.auction_until


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


def expiry_close(segment: str, expiry: date, *, index: bool = False) -> int | None:
    """Epoch ms a contract stops trading on its expiry day: the derivatives' close, but from
    7 Dec 2026 an expiring index contract stops at 15:30."""
    span = hours(segment, day=expiry)
    if span is None:
        return None
    end = _regime(expiry).index_expiry_end if index and segment in FO_SEGMENTS else span[1]
    return epoch_ms(expiry, end)


def previous_trading_day(
    day: date, is_trading_day: Callable[[date], bool], horizon: int = 15
) -> date | None:
    """The trading day before ``day`` (the session an opening gap compares with)."""
    for back in range(1, horizon + 1):
        earlier = day - timedelta(days=back)
        if is_trading_day(earlier):
            return earlier
    return None


def next_session(
    segment: str,
    after_ms: int,
    *,
    is_trading_day: Callable[[date], bool],
    window_of: Callable[[date], tuple[int, int] | None],
    horizon: int = 15,
    auction: bool = False,
) -> Session | None:
    """The segment's first session starting after ``after_ms``: "checks from Mon 09:15"."""
    first = datetime.fromtimestamp(after_ms / 1000, IST).date()
    for offset in range(horizon + 1):
        day = first + timedelta(days=offset)
        found = session(segment, day, trading_day=is_trading_day(day), window=window_of(day),
                        auction=auction)  # fmt: skip
        if found is not None and found.start > after_ms:
            return found
    return None
