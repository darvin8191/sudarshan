"""Which F&O contracts are an index's, from OpenAlgo's master contract (v0.8.1 review L1).

From 7 Dec 2026 an expiring index contract stops at 15:30 on its expiry day and a stock contract
trades to 15:45 (``regimes.expiry_stop``), so whatever inside OpenAlgo needs a contract's stop asks
here: the sandbox's settlement and its orders, the scalping terminal's expiry and the chart's
session facts. The names come from ``regimes.index_underlyings`` over the master's future rows,
the symbols it lists as cash stocks and those it lists as indices, so NIFTYFPI, BSE's Focused IT
under whatever name Upstox gives it, and any index derivative added later read as an index's.

The master is read at most once every ten minutes. A read that fails, or finds no futures (the
table is emptied and filled again by the morning's download), answers the names every reader knows
(``regimes.INDEX_UNDERLYINGS``) and is not kept, so the next question reads again.

An index underlying the master gives beyond those names is named in the log once a day, in
words (the v0.8.1 second review's decision 3): a stock whose F&O and cash names differ for a while,
during a rename say, would read as an index's and stop 15 minutes early on its expiry day, and the
line is where that shows. BSE's Focused IT (FOCIT in Upstox's master) is expected in it.

Runs inside OpenAlgo: ORM reads on OpenAlgo's own symbol session (released by the request's
teardown, or by the caller's session cleanup off a request), no thread, no file; the clock only
for the day of that log line.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import NamedTuple
from zoneinfo import ZoneInfo

from sudarshan import regimes
from utils.logging import get_logger
from utils.thread_safe_cache import LockedTTLCache

logger = get_logger(__name__)

FO_EXCHANGES = ("NFO", "BFO")
CASH_EXCHANGES = ("NSE", "BSE")
INDEX_EXCHANGES = ("NSE_INDEX", "BSE_INDEX")
#: How long one read of the master stands.
KEEP_S = 600
_KEY = "master"
_cache = LockedTTLCache(maxsize=1, ttl=KEEP_S)
IST = ZoneInfo("Asia/Kolkata")
#: The day the index underlyings beyond the known names were last named in the log, and which.
_named: tuple[date, frozenset[str]] | None = None


class Underlyings(NamedTuple):
    #: The underlyings of index derivatives.
    indices: frozenset[str]
    #: The cash stocks with futures: the stocks that close by auction (sessions.closes_by_auction).
    stocks: frozenset[str]


#: What stands in while the master cannot be read: the known index names, no stock.
KNOWN = Underlyings(frozenset(regimes.INDEX_UNDERLYINGS), frozenset())


def underlyings() -> Underlyings:
    """The master's index underlyings and stocks with futures, read at most every ten minutes."""
    found = _cache.get(_KEY)
    if found is not None:
        return found
    try:
        found = _read()
    except Exception:
        logger.exception("The master contract could not be read for index contracts")
        return KNOWN
    if found is None:
        return KNOWN
    _cache[_KEY] = found
    return found


def is_index_contract(symbol: str) -> bool:
    """Whether an F&O contract is an index's: its underlying, read from the master."""
    return regimes.index_derivative(symbol, underlyings().indices)


def has_futures(symbol: str) -> bool:
    """Whether a cash stock has futures in the master (it closes by auction)."""
    return symbol.strip().upper() in underlyings().stocks


def forget() -> None:
    """Read the master again at the next question. For tests."""
    _cache.clear()


def _today() -> date:
    return datetime.now(IST).date()


def _name_unknown(indices: frozenset[str]) -> None:
    """Name in the log, once a day, the index underlyings beyond the names every reader knows."""
    global _named
    extra = indices - frozenset(regimes.INDEX_UNDERLYINGS)
    today = _today()
    if not extra or _named == (today, extra):
        return
    _named = (today, extra)
    logger.warning(
        "The master contract lists index derivatives on %s, beyond the index names SUDARSHAN "
        "knows. Their contracts are taken as an index's: from 7 Dec 2026 they stop at 15:30 on "
        "their expiry day in Analyze mode, on the charts and in the scalping terminal. If one of "
        "them is a stock, its contracts would stop there 15 minutes before the F&O close.",
        ", ".join(sorted(extra)),
    )


def _read() -> Underlyings | None:
    from sqlalchemy import or_

    from database.symbol import SymToken, db_session

    futures = (
        db_session.query(SymToken.symbol, SymToken.instrumenttype)
        .filter(
            SymToken.exchange.in_(FO_EXCHANGES),
            or_(
                SymToken.instrumenttype.in_(("FUT", "FUTIDX", "FUTSTK")),
                SymToken.symbol.like("%FUT"),
            ),
        )
        .distinct()
        .all()
    )
    names = sorted({u for symbol, _ in futures if (u := regimes.underlying_of(symbol or ""))})
    if not names:
        return None

    def listed(exchanges: tuple[str, ...]) -> set[str]:
        rows = (
            db_session.query(SymToken.symbol)
            .filter(SymToken.exchange.in_(exchanges), SymToken.symbol.in_(names))
            .distinct()
        )
        return {symbol for (symbol,) in rows}

    cash, indices = listed(CASH_EXCHANGES), listed(INDEX_EXCHANGES)
    pairs = [(symbol or "", kind or "") for symbol, kind in futures]
    index_names = regimes.index_underlyings(pairs, cash=cash, indices=indices)
    _name_unknown(index_names)
    return Underlyings(index_names, frozenset(name for name in names if name in cash) - index_names)
