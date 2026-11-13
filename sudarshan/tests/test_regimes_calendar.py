"""OpenAlgo's market calendar on both sides of 7 Dec 2026 (database/market_calendar_db.py).

NFO and BFO end at 15:40 until 6 Dec 2026 and at 15:45 from 7 Dec, read by date from the regime
table: a row that holds a close OpenAlgo or SUDARSHAN shipped (15:30 or 15:40) follows it, and a
close the owner set is left alone on every date. Nothing is stored, so an install keeps 15:40 for
every day before 7 Dec. Run against a real (throwaway) calendar database, seeded as at startup,
and against rows forced back to the old values.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

VECTORS = json.loads(Path(__file__).with_name("regimes_vectors.json").read_text(encoding="utf-8"))
FRI, MON, TUE, THU = date(2026, 12, 4), date(2026, 12, 7), date(2026, 12, 8), date(2026, 12, 10)
SHIPPED_BEFORE_CAS = 15 * 3_600_000 + 30 * 60_000  # 15:30, the close before 3 Aug 2026
SHIPPED = 15 * 3_600_000 + 40 * 60_000  # 15:40


@pytest.fixture
def calendar():
    """The market calendar as an install has it after startup: tables, holidays, timings."""
    import database.market_calendar_db as mcd

    mcd.ensure_market_calendar_tables_exists()
    mcd.MarketTiming.query.delete()
    mcd.db_session.commit()
    mcd.seed_market_timings()
    mcd.clear_market_calendar_cache()
    yield mcd
    mcd.db_session.rollback()
    mcd.MarketTiming.query.delete()
    mcd.db_session.commit()
    mcd.seed_market_timings()
    mcd.clear_market_calendar_cache()
    mcd.db_session.remove()


def set_row(mcd, exchange: str, start: str, end: str) -> None:
    """What the admin page's edit stores, without its side effect on the defaults dict."""
    row = mcd.MarketTiming.query.filter_by(exchange_code=exchange).first()
    sh, sm = map(int, start.split(":"))
    eh, em = map(int, end.split(":"))
    row.start_time, row.end_time = start, end
    row.start_offset, row.end_offset = (sh * 60 + sm) * 60_000, (eh * 60 + em) * 60_000
    mcd.db_session.commit()
    mcd.clear_market_calendar_cache()


def window(mcd, day: date, exchange: str) -> tuple[str, str] | None:
    """The effective window as HH:MM in India."""
    found = mcd.get_effective_session_window(day, exchange)
    if found is None:
        return None
    return tuple(
        datetime.fromtimestamp(found[k] / 1000, mcd.IST).strftime("%H:%M")
        for k in ("start_ms", "end_ms")
    )


def listed(mcd, day: date, exchange: str) -> tuple[str, str]:
    """The day's timing as get_market_timings_for_date lists it, against its own midnight."""
    (found,) = [t for t in mcd.get_market_timings_for_date(day) if t["exchange"] == exchange]
    midnight = int(datetime.combine(day, datetime.min.time()).timestamp() * 1000)
    return tuple(
        f"{(found[k] - midnight) // 3_600_000:02d}:{(found[k] - midnight) % 3_600_000 // 60_000:02d}"
        for k in ("start_time", "end_time")
    )


def test_a_fresh_install_keeps_15_40_to_4_december_and_reads_15_45_from_7_december(calendar):
    for exchange in ("NFO", "BFO"):
        assert window(calendar, FRI, exchange) == ("09:15", "15:40")
        assert window(calendar, date(2026, 12, 3), exchange) == ("09:15", "15:40")
        for day in (MON, TUE, THU):
            assert window(calendar, day, exchange) == ("09:15", "15:45")
        assert listed(calendar, FRI, exchange) == ("09:15", "15:40")
        assert listed(calendar, THU, exchange) == ("09:15", "15:45")
    for exchange in ("NSE", "BSE"):  # cash and indices close at 15:30 in both
        assert window(calendar, FRI, exchange) == window(calendar, THU, exchange) == ("09:15", "15:30")


@pytest.mark.parametrize("case", VECTORS["days"], ids=[case["date"] for case in VECTORS["days"]])
def test_every_vector_day_as_the_calendar_reads_it(calendar, case):
    day = date.fromisoformat(case["date"])
    assert window(calendar, day, "NFO") == tuple(case["fo"])
    assert window(calendar, day, "BFO") == tuple(case["fo"])
    assert window(calendar, day, "NSE") == tuple(case["cash"])


def test_the_rows_the_chart_reads_carry_the_change_as_the_vectors_say(calendar):
    rows = {row["exchange"]: row for row in calendar.get_all_market_timings()}
    for expected in VECTORS["timings"]:
        row = rows[expected["exchange"]]
        assert (row["start_offset"], row["end_offset"]) == (
            expected["start_offset"], expected["end_offset"]
        )  # fmt: skip
        assert row["changes"] == expected["changes"]
    assert rows["MCX"]["changes"] == [] and rows["CDS"]["changes"] == []


def test_a_row_still_on_the_old_15_30_close_is_unchanged_before_7_december(calendar):
    """A populated database forced back to the close shipped before SEBI's auction."""
    set_row(calendar, "NFO", "09:15", "15:30")
    assert window(calendar, FRI, "NFO") == ("09:15", "15:30")
    assert window(calendar, THU, "NFO") == ("09:15", "15:45")
    rows = {row["exchange"]: row for row in calendar.get_all_market_timings()}
    assert rows["NFO"]["end_time"] == "15:30" and rows["NFO"]["changes"][0]["end_time"] == "15:45"


@pytest.mark.parametrize("end", ["15:35", "15:45", "16:00", "15:29"])
def test_a_close_the_owner_set_is_left_alone_on_every_date(calendar, end):
    set_row(calendar, "BFO", "09:15", end)
    assert window(calendar, FRI, "BFO") == ("09:15", end)
    assert window(calendar, THU, "BFO") == ("09:15", end)
    rows = {row["exchange"]: row for row in calendar.get_all_market_timings()}
    assert rows["BFO"]["changes"] == []
    assert window(calendar, THU, "NFO") == ("09:15", "15:45")  # the other row still follows


def test_an_open_the_owner_moved_stays_while_the_shipped_close_follows(calendar):
    set_row(calendar, "NFO", "09:00", "15:40")
    assert window(calendar, FRI, "NFO") == ("09:00", "15:40")
    assert window(calendar, THU, "NFO") == ("09:00", "15:45")


def test_without_rows_the_shipped_defaults_follow_the_same_rule(calendar):
    calendar.MarketTiming.query.delete()
    calendar.db_session.commit()
    calendar.clear_market_calendar_cache()
    assert window(calendar, FRI, "NFO") == ("09:15", "15:40")
    assert window(calendar, THU, "NFO") == ("09:15", "15:45")
    rows = {row["exchange"]: row for row in calendar.get_all_market_timings()}
    assert rows["NFO"]["id"] is None and rows["NFO"]["changes"][0]["from"] == "2026-12-07"


def test_a_holiday_and_a_weekend_stay_closed(calendar):
    assert window(calendar, date(2026, 12, 25), "NFO") is None  # Christmas
    assert window(calendar, date(2026, 12, 12), "NFO") is None  # Saturday


def test_timing_on_moves_the_close_and_its_hh_mm_together(calendar):
    row = {"exchange": "NFO", "start_time": "09:15", "end_time": "15:40",
           "start_offset": 33_300_000, "end_offset": SHIPPED}  # fmt: skip
    assert calendar.timing_on("NFO", row, FRI) == row
    moved = calendar.timing_on("NFO", row, MON)
    assert (moved["end_time"], moved["end_offset"], moved["start_time"]) == ("15:45", 56_700_000, "09:15")
    assert calendar.timing_on("MCX", {**row, "exchange": "MCX"}, MON)["end_time"] == "15:40"
    assert calendar.timing_changes("NFO", {"end_offset": SHIPPED_BEFORE_CAS})[0]["from"] == "2026-12-07"
    assert calendar.timing_changes("NFO", {"end_offset": None}) == []
    assert calendar.timing_changes("NFO", None) == []


def test_openscript_states_the_date_s_regular_session(calendar):
    """The chart's OpenScript facts read every bar of a run against this one window."""
    from services.openscript_instrument_service import _regular_session

    assert _regular_session("NFO", FRI)["end"] == "15:40"
    assert _regular_session("NFO", THU)["end"] == "15:45"
    assert _regular_session("NSE", THU)["end"] == "15:30"
