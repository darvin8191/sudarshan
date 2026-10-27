"""The market's day for the runtime (M10.1): sessions, the trading day before, the CAS keys.

Built only from sessions.py through TradingCalendar (review A7.a-c, cross-design X11):

- a day's sessions come from ``is_trading_day`` and its own window in the extra-days file. A
  weekday that trades anyway (not a holiday) and is given a window too would need two sessions a
  day, which sessions.py does not hold yet: that day keeps its usual hours and the added window is
  refused loudly (one ERROR a day), never the other way round, which would leave 09:15-15:30
  unchecked;
- the trading day before is ``sessions.previous_trading_day`` (Mon 9 Nov 2026 -> Sun 8 Nov);
- the runtime asks again every CHECK_S and at IST midnight, so a changed calendar file counts
  within half a minute.

CAS keys (cross-design X6a): the cash stocks among the alerts' keys whose company has stock
futures in the master contract (``fno_stocks``: NFO futures in OpenAlgo's symtoken, read
read-only), or every stock from 7 Dec 2026 (sessions.closes_by_auction). The master is read at
start and each morning at MASTER_AT, after the day's login has downloaded it.
"""

from __future__ import annotations

import logging
import re
from datetime import date
from pathlib import Path

from sudarshan.alerts.market import sessions
from sudarshan.alerts.market.sessions import Session
from sudarshan.autologin.schedule import TradingCalendar, window_text

log = logging.getLogger("sudarshan.alerts.market")

#: How often the calendar is asked again for today's sessions.
CHECK_S = 30.0
#: IST minutes: the master contract is read again each day at 09:00, after the 08:35-08:55 login.
MASTER_AT = 9 * 60
#: An OpenAlgo future: [Base][DDMMMYY]FUT.
_FUTURE = re.compile(r"(?P<base>.+?)\d{2}[A-Z]{3}\d{2}FUT")


class MasterUnavailable(Exception):
    """OpenAlgo's master contract could not be read."""


class Days:
    def __init__(self, calendar: TradingCalendar):
        self.calendar = calendar
        self._refused: set[date] = set()

    def window(self, day: date) -> tuple[int, int] | None:
        """The day's own window when sessions.py can use it; an added one is refused."""
        cal = self.calendar
        window = cal.day_window(day)
        if window is not None and cal.trades_anyway(day):
            if day not in self._refused:
                self._refused = {day}  # told once a day; only the latest day is kept
                log.error(
                    "%s trades its usual hours and the extra-days file adds a window %s to it: "
                    "market alerts check the usual hours only and not the added window, until "
                    "they can hold two sessions in one day",
                    day, window_text(window),
                )  # fmt: skip
            return None
        return window

    def of(self, day: date) -> tuple[dict[str, Session], int]:
        """(session key -> session, the trading day before as YYYYMMDD or 0)."""
        trading = self.calendar.is_trading_day(day)
        found = sessions.sessions_of(day, trading_day=trading, window=self.window(day))
        before = sessions.previous_trading_day(day, self.calendar.is_trading_day)
        return found, sessions.day_key(before) if before else 0


def cas_keys(alerts, day: date, fno: frozenset[str]) -> frozenset[str]:
    """The keys among ``alerts`` (spec.Alert) that close by auction on ``day``."""
    return frozenset(
        alert.key
        for alert in alerts
        if sessions.closes_by_auction(alert.segment, day, derivatives=alert.symbol in fno)
    )


def fno_stocks(db_path: Path) -> frozenset[str]:
    """The companies with stock futures in OpenAlgo's master contract (NFO, ``...FUT``), read
    read-only; index futures are left out. Raises MasterUnavailable."""
    from sqlalchemy import select
    from sqlalchemy.exc import SQLAlchemyError

    from database.engine_factory import create_db_engine
    from database.symbol import SymToken
    from sudarshan.alerts.owner import database_urls

    query = select(SymToken.symbol).where(SymToken.exchange == "NFO", SymToken.symbol.like("%FUT"))
    for url in database_urls(db_path):
        engine = create_db_engine(url)
        try:
            with engine.connect() as conn:
                symbols = conn.execute(query).scalars().all()
        except SQLAlchemyError:
            continue
        finally:
            engine.dispose()
        bases = {match["base"] for s in symbols if (match := _FUTURE.fullmatch(s or ""))}
        return frozenset(bases - set(sessions.INDEX_UNDERLYINGS))
    raise MasterUnavailable(str(db_path))
