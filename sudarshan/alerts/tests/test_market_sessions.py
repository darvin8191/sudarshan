"""Sessions per segment (amendments A1 and A2): usual hours, special days, the calendar helpers."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from sudarshan.alerts.market import sessions
from sudarshan.autologin.schedule import IST, parse_window

WED = date(2026, 10, 21)
MUHURAT = date(2026, 11, 8)  # a Sunday


def clock(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, IST).strftime("%Y-%m-%d %H:%M")


@pytest.mark.parametrize(
    ("segment", "start", "end"),
    [
        ("NSE_EQ", "09:15", "15:30"),
        ("BSE_EQ", "09:15", "15:30"),
        ("NSE_INDEX", "09:15", "15:30"),
        ("BSE_INDEX", "09:15", "15:30"),
        ("NSE_FO", "09:15", "15:40"),
        ("BSE_FO", "09:15", "15:40"),
    ],
)
def test_usual_session_per_segment(segment, start, end):
    found = sessions.session(segment, WED, trading_day=True)
    assert (clock(found.start), clock(found.end)) == (f"2026-10-21 {start}", f"2026-10-21 {end}")
    assert found.key == 20261021 and found.segment == segment


@pytest.mark.parametrize(
    ("segment", "start", "end"),
    [("NSE_EQ", "14:20", "15:30"), ("NSE_INDEX", "14:20", "15:30"), ("NSE_FO", "14:20", "15:40")],
)
def test_muhurat_window_gives_each_segment_its_share(segment, start, end):
    """The extra-days line ``2026-11-08 14:05-15:40``: pre-open 14:05, normal market 14:20."""
    found = sessions.session(segment, MUHURAT, trading_day=True, window=parse_window("14:05-15:40"))
    assert (clock(found.start), clock(found.end)) == (f"2026-11-08 {start}", f"2026-11-08 {end}")


def test_an_evening_window_keeps_the_offsets():
    window = parse_window("17:45-19:30")
    assert sessions.hours("NSE_EQ", window) == (18 * 60, 19 * 60 + 20)
    assert sessions.hours("NSE_FO", window) == (18 * 60, 19 * 60 + 30)


def test_a_window_too_short_for_a_segment_leaves_it_closed():
    window = parse_window("14:00-14:20")
    assert sessions.hours("NSE_EQ", window) is None  # would be 14:15-14:10
    assert sessions.hours("NSE_FO", window) == (14 * 60 + 15, 14 * 60 + 20)
    assert sessions.session("NSE_EQ", WED, trading_day=True, window=window) is None


def test_no_session_on_a_closed_day_or_an_uncovered_segment():
    assert sessions.session("NSE_EQ", WED, trading_day=False) is None
    assert sessions.session("MCX_FO", WED, trading_day=True) is None
    assert sessions.hours("NCD_FO") is None


def test_segment_and_day_keys():
    assert sessions.segment_of("NSE_INDEX|Nifty 50") == "NSE_INDEX"
    assert sessions.day_key(MUHURAT) == 20261108 and sessions.key_day(20261108) == MUHURAT
    assert (
        sessions.epoch_ms(WED, 555)
        == int(datetime(2026, 10, 21, 9, 15, tzinfo=IST).timestamp()) * 1000
    )
    assert clock(sessions.epoch_ms(WED, 24 * 60)) == "2026-10-22 00:00"


HOLIDAYS = {date(2026, 10, 20)}  # Tuesday


def trades(day: date) -> bool:
    return day == MUHURAT or (day.weekday() < 5 and day not in HOLIDAYS)


def test_previous_trading_day_skips_weekends_and_holidays():
    assert sessions.previous_trading_day(WED, trades) == date(2026, 10, 19)  # over the holiday
    assert sessions.previous_trading_day(date(2026, 10, 26), trades) == date(2026, 10, 23)
    assert sessions.previous_trading_day(date(2026, 11, 9), trades) == MUHURAT
    assert sessions.previous_trading_day(WED, lambda day: False) is None


def test_next_session_for_the_market_closed_words():
    def window_of(day):
        return parse_window("14:05-15:40") if day == MUHURAT else None

    def nxt(at: datetime, segment="NSE_EQ"):
        found = sessions.next_session(
            segment, int(at.timestamp() * 1000), is_trading_day=trades, window_of=window_of
        )
        return clock(found.start) if found else None

    assert nxt(datetime(2026, 10, 21, 8, 0, tzinfo=IST)) == "2026-10-21 09:15"
    assert nxt(datetime(2026, 10, 21, 9, 15, tzinfo=IST)) == "2026-10-22 09:15"  # started
    assert nxt(datetime(2026, 10, 23, 15, 30, tzinfo=IST)) == "2026-10-26 09:15"  # Fri close
    assert nxt(datetime(2026, 11, 6, 16, 0, tzinfo=IST)) == "2026-11-08 14:20"  # Muhurat
    assert nxt(datetime(2026, 11, 6, 16, 0, tzinfo=IST), "NSE_FO") == "2026-11-08 14:20"
    never = sessions.next_session(
        "NSE_EQ", 0, is_trading_day=lambda day: False, window_of=window_of
    )
    assert never is None
