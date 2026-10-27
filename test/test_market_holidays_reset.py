"""Resetting the holiday calendar is one transaction (SUDARSHAN M3.7).

upgrade/migrate_market_holidays.py calls reset_holiday_data() on every
migrate_all run, so every deploy deletes the calendar and seeds it again. The
delete and each year's seed used to commit on their own, so a failure after the
delete left the years not yet seeded empty until the next good run: no
holidays, no Muhurat session, every closed day treated as an open one.
"""

import pytest
from sqlalchemy.orm import Session, scoped_session, sessionmaker

import database.market_calendar_db as mc
from database.engine_factory import create_db_engine


@pytest.fixture
def calendar(tmp_path, monkeypatch):
    """The calendar an install seeds at startup, on its own scratch file."""
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
        yield engine
    finally:
        mc.clear_market_calendar_cache()
        mc.Base.query = previous_query
        held.remove()
        engine.dispose()


def _rows(engine):
    """Every holiday and exchange row, ids included, as the tables hold them."""
    with Session(engine) as session:
        holidays = {
            (h.id, h.holiday_date, h.description, h.holiday_type, h.year)
            for h in session.query(mc.Holiday)
        }
        exchanges = {
            (e.id, e.holiday_id, e.exchange_code, e.is_open, e.start_time, e.end_time)
            for e in session.query(mc.HolidayExchange)
        }
    return holidays, exchanges


def _calendar(engine):
    """What the calendar says, without row ids: one entry per holiday."""
    with Session(engine) as session:
        return sorted(
            (
                h.holiday_date,
                h.description,
                h.holiday_type,
                h.year,
                sorted(
                    (e.exchange_code, e.is_open, e.start_time, e.end_time)
                    for e in session.query(mc.HolidayExchange).filter(
                        mc.HolidayExchange.holiday_id == h.id
                    )
                ),
            )
            for h in session.query(mc.Holiday)
        )


@pytest.mark.parametrize("failing_year", [2025, 2026])
def test_a_reset_that_fails_after_the_delete_leaves_the_calendar_as_it_was(
    calendar, monkeypatch, failing_year
):
    before = _rows(calendar)
    assert before[0] and before[1]
    seen = {}

    def fails_after_the_delete(**_kwargs):
        # The delete has already run, in the transaction the reset holds open.
        seen["holidays left"] = (
            mc.db_session.query(mc.Holiday).filter(mc.Holiday.year == failing_year).count()
        )
        raise RuntimeError("injected failure after the delete")

    monkeypatch.setattr(mc, f"seed_holidays_{failing_year}", fails_after_the_delete)

    assert mc.reset_holiday_data() is False
    assert seen == {"holidays left": 0}
    assert _rows(calendar) == before


def test_a_reset_that_succeeds_holds_the_whole_seed_again(calendar):
    before = _calendar(calendar)
    assert mc.reset_holiday_data() is True
    assert _calendar(calendar) == before
