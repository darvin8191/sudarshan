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

from collections.abc import Callable, Collection, Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sudarshan import index_contracts, regimes

IST = ZoneInfo("Asia/Kolkata")
FO_EXCHANGES = frozenset({"NFO", "BFO"})
#: The cash market whose day an instrument follows: an index's is its exchange's.
CASH_DAY = {"NSE": "NSE", "BSE": "BSE", "NSE_INDEX": "NSE", "BSE_INDEX": "BSE"}
MINUTE_MS = 60_000
#: The exchanges whose hours the day's facts carry (the dashboard's status row and hours sheet).
DAY_EXCHANGES = ("NSE", "BSE", "NFO", "BFO", "MCX")
#: A cash market's pre-open starts this long before its normal market (09:00 for 09:15).
PRE_OPEN_MIN = 15
#: The expiring underlyings the dashboard names first, then the rest in alphabetical order.
PROMINENT = ("NIFTY", "BANKNIFTY", "SENSEX", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50", "BANKEX",
             "SENSEX50")  # fmt: skip
#: How far ahead the next trading day is looked for.
LOOK_AHEAD_DAYS = 15
#: One day's hours: {exchange: (open, close)} in epoch ms, for each exchange that trades that day.
Hours = Mapping[str, tuple[int, int]]


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


def _prominence(underlying: str) -> tuple[int, str]:
    return (PROMINENT.index(underlying) if underlying in PROMINENT else len(PROMINENT), underlying)


def _next_trading_day(day: date, hours_on: Callable[[date], Hours]) -> date | None:
    """The first day after ``day`` the cash market trades on, within LOOK_AHEAD_DAYS."""
    for ahead in range(1, LOOK_AHEAD_DAYS + 1):
        later = day + timedelta(days=ahead)
        if "NSE" in hours_on(later):
            return later
    return None


def _exchange_hours(exchange: str, day: date, span: tuple[int, int]) -> dict[str, int | None]:
    """An exchange's day: a cash market's pre-open, and from 7 Dec 2026 its closing auction's end
    (the price at about 15:40, ``Regime.close_after_cash`` after its close); None for the rest."""
    start, end = span
    cash = exchange in CASH_DAY
    after = regimes.regime_on(day).close_after_cash
    return {
        "pre_open": start - PRE_OPEN_MIN * MINUTE_MS if cash else None,
        "open": start,
        "close": end,
        "auction_until": end + after * MINUTE_MS if cash and after > 0 else None,
    }


def day_facts(
    now_ms: int,
    *,
    hours_on: Callable[[date], Hours],
    occasion_on: Callable[[date], tuple[str, str] | None],
    expiries: Mapping[str, Mapping[str, Collection[date]]] | None,
    indices: Collection[str],
) -> dict[str, Any]:
    """The IST day of ``now_ms`` as the dashboard's status row and hours sheet read it (M3.5,
    M3.6). Pure: the calendar, the master and the clock are arguments.

    ``hours_on(day)`` gives each exchange that trades that day with its window; ``occasion_on(day)``
    the calendar's ("special" or "holiday", its words) or None; ``expiries`` the master's expiry days
    per exchange and underlying, None while it is not loaded; ``indices`` the index underlyings.

    The answer: ``trading_day`` (the cash market trades today), ``note`` ("Weekend", "Holiday",
    "Special session", "Closed" for a weekday the calendar gives neither hours nor a holiday, or
    None) and ``occasion`` (the calendar's words for a holiday or a special session); each
    exchange's hours; the index expiries of today and of the next trading day, each
    with when its contracts stop trading, and how many stock underlyings expire those days
    (``expiries`` and ``stock_expiries``, None while the master is not loaded); and ``next_open``,
    the cash market's next open at or after now. Every instant is epoch milliseconds."""
    day = datetime.fromtimestamp(now_ms / 1000, IST).date()
    today, occasion = hours_on(day), occasion_on(day)
    trading = "NSE" in today
    if trading:
        note = "Special session" if occasion and occasion[0] == "special" else None
    elif occasion is not None:
        note = "Holiday"
    else:  # a weekday the calendar gives no hours and no holiday for: closed, cause not claimed
        note = "Weekend" if day.weekday() >= 5 else "Closed"
    following = _next_trading_day(day, hours_on)
    nse = today.get("NSE")
    if nse is not None and now_ms < nse[0]:
        next_open: int | None = nse[0]
    else:
        next_open = hours_on(following)["NSE"][0] if following else None

    listed: list[dict[str, Any]] | None = None if expiries is None else []
    stocks: list[dict[str, Any]] | None = None if expiries is None else []
    for when in (day if trading else None, following):
        if when is None or listed is None or stocks is None:
            continue
        spans = hours_on(when)
        for exchange in ("NFO", "BFO"):
            end = spans.get(exchange, (None, None))[1]
            names = [u for u, days in expiries.get(exchange, {}).items() if when in days]
            named = sorted((u for u in names if u in indices), key=_prominence)
            for underlying in named:
                stop = stop_of(exchange, when, when, index=True, session_end_ms=end)
                listed.append({"underlying": underlying, "exchange": exchange,
                               "day": when.isoformat(), "stops_at": stop})  # fmt: skip
            if len(names) > len(named):
                stop = stop_of(exchange, when, when, index=False, session_end_ms=end)
                stocks.append({"exchange": exchange, "day": when.isoformat(),
                               "count": len(names) - len(named), "stops_at": stop})  # fmt: skip
    return {
        "day": day.isoformat(),
        "trading_day": trading,
        "note": note,
        "occasion": occasion[1] if occasion and (note is not None) else None,
        "exchanges": {
            exchange: _exchange_hours(exchange, day, today[exchange])
            for exchange in DAY_EXCHANGES
            if exchange in today
        },
        "expiries": listed,
        "stock_expiries": stocks,
        "next_open": next_open,
    }


def _master_expiries() -> dict[str, dict[str, frozenset[date]]] | None:
    """The in-memory master's NFO and BFO expiry days per underlying; None while it is not loaded
    (before the morning's download, or after the 03:00 session reset)."""
    from database.token_db_enhanced import get_cache

    snapshot = get_cache().valid_snapshot()
    if snapshot is None:
        return None
    found: dict[str, dict[str, frozenset[date]]] = {exchange: {} for exchange in FO_EXCHANGES}
    for (exchange, underlying), texts in snapshot.expiries_by_exchange_underlying.items():
        if exchange in FO_EXCHANGES and underlying:
            days = frozenset(day for text in texts if (day := parse_expiry(text)) is not None)
            found[exchange][underlying] = days
    return found


def today(now_ms: int) -> dict[str, Any]:
    """``day_facts`` for the IST day of ``now_ms``, from OpenAlgo's market calendar (the one the
    chart's hours and the instrument facts above read, special sessions and holidays included)
    and its in-memory master. Reads nothing from the broker and changes nothing."""
    from database.market_calendar_db import get_effective_session_window, get_holidays_by_year

    def hours_on(day: date) -> dict[str, tuple[int, int]]:
        found = {}
        for exchange in DAY_EXCHANGES:
            window = get_effective_session_window(day, exchange)
            if window:
                found[exchange] = (int(window["start_ms"]), int(window["end_ms"]))
        return found

    def occasion_on(day: date) -> tuple[str, str] | None:
        kinds = {"SPECIAL_SESSION": "special", "TRADING_HOLIDAY": "holiday"}
        for row in get_holidays_by_year(day.year):
            if row.get("date") == day.isoformat() and row.get("holiday_type") in kinds:
                return kinds[row["holiday_type"]], str(row.get("description") or "")
        return None

    return day_facts(now_ms, hours_on=hours_on, occasion_on=occasion_on,
                     expiries=_master_expiries(),
                     indices=index_contracts.underlyings().indices)  # fmt: skip
