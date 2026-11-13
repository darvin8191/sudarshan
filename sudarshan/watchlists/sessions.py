"""OpenAlgo's market calendar, asked the two questions follow rows need (M8.10, W15).

``closes`` answers the session close of an expiry day where the calendar has a dated row for it (a
special session such as an evening Muhurat, or a holiday row that leaves the exchange open with its
own window); a date it has no such row for is left out, and ``expiry.stop_ms`` stops it by
the regime of the day (the one rule of the watchlists package, read from the alerts sessions
table). The calendar's *default* F&O close is deliberately not used: only a dated row for a
special day overrides the regime. ``market_open`` says whether the underlying's
own exchange is trading at an instant, by the calendar's effective window. ``index_underlyings``
says which underlyings' contracts are an index's, as the master contract says. Request-driven reads
of OpenAlgo's own tables; no clock here (the instant is an argument), no thread, no timer. The
calendar's scoped session is removed after each question.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from sudarshan.watchlists import numfmt
from utils.logging import get_logger

logger = get_logger(__name__)


def index_underlyings() -> frozenset[str]:
    """The underlyings whose contracts are an index's (their expiry stops at 15:30 from 7 Dec
    2026): OpenAlgo's one rule, the master contract's answer (``sudarshan.index_contracts``, which
    the sandbox, the scalping terminal and the chart ask too, v0.8.1 review L1). Read at most every
    ten minutes; while the master cannot be read, the names every reader knows, never a guess
    (v0.9.0 review M1). Its symbol session is released by the request's teardown."""
    from sudarshan import index_contracts

    return index_contracts.underlyings().indices


def closes(dates: Iterable[str], exchange: str) -> dict[str, int]:
    """``{iso date: epoch ms}`` of the dated session closes the calendar has for ``exchange``."""
    out: dict[str, int] = {}
    wanted = sorted(set(dates))
    if not wanted:
        return out
    from database import market_calendar_db as cal

    try:
        for iso in wanted:
            day = date.fromisoformat(iso)
            window = cal.get_special_session(day, exchange) or cal.get_holiday_exchange_window(
                day, exchange
            )
            if window and window.get("end_ms"):
                out[iso] = int(window["end_ms"])
    except Exception:
        logger.exception("Watchlists: the market calendar could not be read for session closes")
        return {}
    finally:
        cal.db_session.remove()
    return out


def market_open(exchange: str, now_ms: int) -> bool:
    """True when ``exchange`` is trading at ``now_ms`` by the calendar's window for that day. A
    calendar that cannot be read says open: a row is then never called closed on a guess."""
    from database import market_calendar_db as cal

    try:
        window = cal.get_effective_session_window(numfmt.ist_date(now_ms), exchange)
    except Exception:
        logger.exception("Watchlists: the market calendar could not be read for the session")
        return True
    finally:
        cal.db_session.remove()
    return bool(window) and window["start_ms"] <= now_ms <= window["end_ms"]
