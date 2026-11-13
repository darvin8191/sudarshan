"""OpenAlgo's market calendar, asked the two questions follow rows need (M8.10, W15).

``closes`` answers the session close of an expiry day where the calendar has a dated row for it (a
special session such as an evening Muhurat, or a holiday row that leaves the exchange open with its
own window); a date it has no such row for is left out, and ``dynamic.close_ms`` closes it at 15:30
IST. The calendar's *default* F&O close (15:40, after SEBI's closing auction) is deliberately not
used: an expiring contract stops trading at 15:30 whatever the rest of the segment does, and the
design (owner decision 4) rolls a row at that close. ``market_open`` says whether the underlying's
own exchange is trading at an instant, by the calendar's effective window. Request-driven reads of
OpenAlgo's own tables; no clock here (the instant is an argument), no thread, no timer. The
calendar's scoped session is removed after each question.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from sudarshan.watchlists import numfmt
from utils.logging import get_logger

logger = get_logger(__name__)


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
