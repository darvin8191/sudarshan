"""An instrument's own session facts for one day, for the chart and the order dialogs (v0.8.1 review
M1, M2, M3). The phone never computes sessions: the chart's calendar is the exchange's
(frontend/src/lib/trading/sessionHours.ts), and what belongs to one instrument comes from here.

- ``closing``: from 7 Dec 2026 a stock with futures closes by auction after the cash close and its
  print comes by 15:45; an index's closing value may come then too. A print stamped from the cash
  close to that moment belongs to the day's last intraday candle, as the alerts and the screener
  take it (sessions.closing_window). None before 7 Dec, when the print came inside the session, and
  for a stock without futures, F&O and a day the cash market does not trade.
- ``stops_at``: an expiring NFO or BFO contract stops trading on its expiry day at its regime's
  stop (``regimes.expiry_stop``): 15:40 until 6 Dec 2026; from 7 Dec 15:30 for an index contract
  and 15:45 for a stock contract. The chart opens no candle after it, the countdown ends at it,
  and the marker and the order dialogs say the contract has stopped trading.

``offered_expiries`` is the same stop for the scalping terminal: today's expiry leaves the list it
offers at its contract's stop, not at the F&O close, so the nearest expiry then is the next one.

Both are epoch milliseconds. The day's window is OpenAlgo's calendar for the exchange (special
sessions and holidays included), the same one the chart's calendar is built from; the times are
the regime table's; index contracts and stocks with futures are the master's
(``sudarshan/index_contracts.py``).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sudarshan import index_contracts, regimes

IST = ZoneInfo("Asia/Kolkata")
FO_EXCHANGES = frozenset({"NFO", "BFO"})
#: The cash market whose day an instrument follows: an index's is its exchange's.
CASH_DAY = {"NSE": "NSE", "BSE": "BSE", "NSE_INDEX": "NSE", "BSE_INDEX": "BSE"}
MINUTE_MS = 60_000


def midnight_ms(day: date) -> int:
    """IST midnight of ``day`` in epoch milliseconds."""
    return int(datetime(day.year, day.month, day.day, tzinfo=IST).timestamp()) * 1000


def closing_of(
    exchange: str, day: date, cash_end_ms: int | None, *, auction: bool
) -> tuple[int, int] | None:
    """When a closing print after the cash close may come on ``day``, (from, until) in epoch ms:
    for a stock that closes by auction (``auction``) or an index, from the cash market's end that
    day (``cash_end_ms``, None when it does not trade) to the print's last moment."""
    if cash_end_ms is None or exchange not in CASH_DAY:
        return None
    if not (auction or exchange.endswith("_INDEX")):
        return None
    regime = regimes.regime_on(day)
    if regime.close_after_cash <= 0:  # the price came inside the cash session
        return None
    return cash_end_ms, cash_end_ms + regime.print_after_cash * MINUTE_MS


def stop_of(
    exchange: str, day: date, expiry: date | None, *, index: bool, session_end_ms: int | None
) -> int | None:
    """When an expiring NFO or BFO contract stops trading on its expiry day ``day`` (epoch ms),
    never after that day's own session end; None on another day or a day it does not trade."""
    if exchange not in FO_EXCHANGES or expiry != day or session_end_ms is None:
        return None
    stop = midnight_ms(day) + regimes.expiry_stop(expiry, index=index) * MINUTE_MS
    return min(stop, session_end_ms)


def offered_expiries(
    expiries: Sequence[str], now_ms: int, *, index: bool
) -> tuple[list[str], int | None]:
    """The expiries (DDMMMYY, nearest first) still trading at ``now_ms``, and when the nearest of
    them stops today (epoch ms), or None: today's expiry leaves the list at its contract's stop
    (15:40 until 6 Dec 2026; from 7 Dec 15:30 for an index's, 15:45 for a stock's)."""
    day = datetime.fromtimestamp(now_ms / 1000, IST).date()
    offered, rolls_at = [], None
    for text in expiries:
        expiry = parse_expiry(text)
        if expiry == day:
            stop = midnight_ms(day) + regimes.expiry_stop(expiry, index=index) * MINUTE_MS
            if now_ms >= stop:
                continue
            rolls_at = stop if not offered else rolls_at
        offered.append(text)
    return offered, rolls_at


def parse_expiry(text: Any) -> date | None:
    """The master's expiry (08-DEC-26), or the scalping terminal's (08DEC26), or None."""
    for shape in ("%d-%b-%y", "%d-%b-%Y", "%d%b%y"):
        try:
            return datetime.strptime(str(text or "").strip().upper(), shape).date()
        except ValueError:
            continue
    return None


def facts(symbol: str, exchange: str, now_ms: int) -> dict[str, Any]:
    """The instrument's facts for the IST day of ``now_ms``, read from OpenAlgo's calendar and
    master: ``{"day": "2026-12-08", "stops_at": ms | None, "closing": {"from", "until"} | None}``."""
    from database.market_calendar_db import get_effective_session_window
    from database.token_db import get_symbol_info

    day = datetime.fromtimestamp(now_ms / 1000, IST).date()
    symbol, exchange = symbol.strip().upper(), exchange.strip().upper()
    found: dict[str, Any] = {"day": day.isoformat(), "stops_at": None, "closing": None}

    def end_of(code: str) -> int | None:
        window = get_effective_session_window(day, code)
        return int(window["end_ms"]) if window else None

    if exchange in FO_EXCHANGES:
        info = get_symbol_info(symbol, exchange)
        expiry = parse_expiry(getattr(info, "expiry", None)) if info is not None else None
        if expiry == day:
            index = index_contracts.is_index_contract(symbol)
            found["stops_at"] = stop_of(exchange, day, expiry, index=index,
                                        session_end_ms=end_of(exchange))  # fmt: skip
    elif exchange in CASH_DAY:
        auction = not exchange.endswith("_INDEX") and index_contracts.has_futures(symbol)
        late = closing_of(exchange, day, end_of(CASH_DAY[exchange]), auction=auction)
        if late is not None:
            found["closing"] = {"from": late[0], "until": late[1]}
    return found
