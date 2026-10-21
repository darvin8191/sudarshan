"""On Muhurat Sunday, 8 November 2026, the market status follows the published session.

SUDARSHAN's live screens, the strategy host and Flow decide "market closed" from
the calendar. On the placeholder 18:00-19:15 session they greyed every price as
closed during the real afternoon session and called the empty 18:00 market open.
The exchanges' hours and their sources are in database/market_calendar_db.py.
"""

from datetime import date, datetime

import pytest
from sqlalchemy.orm import scoped_session, sessionmaker

import database.market_calendar_db as mc
from database.engine_factory import create_db_engine
from services import market_calendar_service

MUHURAT = date(2026, 11, 8)
UPDATED = ("NSE", "BSE", "NFO", "BFO", "CDS", "BCD")

#: (hour, minute) IST on 8 November 2026 -> the exchanges open then.
OPEN_AT = {
    (14, 30): set(UPDATED),
    (15, 35): {"NFO", "BFO"},  # equity derivatives trade to 15:40
    (18, 30): set(),  # the placeholder's hours: nothing of these trades
}


def ist(hour, minute):
    return mc.IST.localize(datetime(MUHURAT.year, MUHURAT.month, MUHURAT.day, hour, minute))


@pytest.fixture
def calendar(tmp_path, monkeypatch):
    """The calendar a new install seeds at startup, on its own scratch file."""
    engine = create_db_engine(f"sqlite:///{(tmp_path / 'openalgo.db').as_posix()}")
    held = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))
    monkeypatch.setattr(mc, "engine", engine)
    monkeypatch.setattr(mc, "db_session", held)
    # Swapped by hand: monkeypatch reads the old value with getattr, and a
    # query property read on the declarative base itself raises.
    previous_query = mc.Base.__dict__["query"]
    mc.Base.query = held.query_property()
    try:
        mc.clear_market_calendar_cache()
        mc.ensure_market_calendar_tables_exists()
        yield mc
    finally:
        mc.clear_market_calendar_cache()
        mc.Base.query = previous_query
        held.remove()
        engine.dispose()


@pytest.fixture
def clock(monkeypatch):
    """``clock(hour, minute)`` sets the calendar's now to that time on 8 November, IST."""

    class Frozen(datetime):
        at = None

        @classmethod
        def now(cls, tz=None):
            return cls.at.astimezone(tz) if tz else cls.at.replace(tzinfo=None)

    monkeypatch.setattr(mc, "datetime", Frozen)

    def set_to(hour, minute):
        Frozen.at = ist(hour, minute)

    return set_to


@pytest.mark.parametrize(("hour", "minute"), list(OPEN_AT))
def test_the_market_status_follows_the_published_session(calendar, clock, hour, minute):
    clock(hour, minute)
    status = calendar.get_market_hours_status()["exchanges"]

    for exchange in UPDATED:
        expected = exchange in OPEN_AT[(hour, minute)]
        assert calendar.is_market_open(exchange) is expected, (exchange, hour, minute)
        assert status[exchange]["is_open"] is expected, (exchange, hour, minute)
        assert status[exchange]["is_special"] is True


def test_the_status_states_the_published_hours(calendar, clock):
    clock(14, 30)
    status = calendar.get_market_hours_status()["exchanges"]

    hours = {
        exchange: (status[exchange]["start_time"], status[exchange]["end_time"])
        for exchange in UPDATED
    }
    assert hours == {
        "NSE": ("14:20", "15:30"),
        "BSE": ("14:20", "15:30"),
        "NFO": ("14:20", "15:40"),
        "BFO": ("14:20", "15:40"),
        "CDS": ("14:20", "15:30"),
        "BCD": ("14:20", "15:30"),
    }


@pytest.mark.parametrize(("hour", "minute"), list(OPEN_AT))
def test_the_windows_the_screens_read_follow_the_published_session(calendar, hour, minute):
    """The timings service and the screens' own rule: open while start <= now <= end.

    /api/v1/market/timings answers from this service, and the admin timings the
    live screens load carry the same function's windows as ``market_status``.
    """
    ok, body, code = market_calendar_service.get_timings(MUHURAT.isoformat())
    assert ok and code == 200
    assert body["data"] == calendar.get_market_timings_for_date(MUHURAT)
    windows = {row["exchange"]: row for row in body["data"]}

    now = int(ist(hour, minute).timestamp() * 1000)
    open_now = {
        exchange
        for exchange in UPDATED
        if exchange in windows
        and windows[exchange]["start_time"] <= now <= windows[exchange]["end_time"]
    }
    assert open_now == OPEN_AT[(hour, minute)]
