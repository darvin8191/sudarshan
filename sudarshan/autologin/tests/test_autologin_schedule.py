"""Trading days, the windows that guard a working login, and the 03:30 token expiry, in IST."""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from pathlib import Path

import pytest

from sudarshan.autologin.messages import MESSAGES
from sudarshan.autologin.schedule import (
    DEFAULT_WINDOW,
    IST,
    MARKET_HOURS,
    TEST_BLOCKED,
    UPSTOX_TOKEN_EXPIRY,
    TradingCalendar,
    parse_window,
    trading_session_start,
)

REPO = Path(__file__).resolve().parents[3]
MONDAY = date(2026, 10, 19)
SATURDAY = date(2026, 10, 24)
HOLIDAY = date(2026, 11, 9)  # in the fake holiday file


@pytest.fixture
def extra_file(tmp_path):
    return tmp_path / "nse_extra_trading_days.txt"


def test_weekends_and_holidays_are_closed(holidays_file):
    calendar = TradingCalendar(holidays_file)
    assert calendar.is_trading_day(MONDAY)
    assert not calendar.is_trading_day(SATURDAY) and not calendar.is_trading_day(HOLIDAY)


def test_an_extra_day_opens_a_weekend_or_a_holiday(holidays_file, extra_file):
    extra_file.write_text(f"# Muhurat and a special session\n{SATURDAY}\n{HOLIDAY}  # Diwali\n")
    calendar = TradingCalendar(holidays_file, extra_file)
    assert calendar.is_trading_day(SATURDAY) and calendar.is_trading_day(HOLIDAY)
    assert calendar.is_trading_day(MONDAY)
    sunday = date(2026, 10, 25)
    assert not calendar.is_trading_day(sunday)
    # Read again when the file changes, as the holiday file is.
    extra_file.write_text(f"{sunday}\n")
    os.utime(extra_file, ns=(1, 2))
    assert calendar.is_trading_day(sunday) and not calendar.is_trading_day(SATURDAY)


def test_the_extra_days_file_is_optional_and_quiet(holidays_file, extra_file, caplog):
    caplog.set_level(logging.WARNING)
    calendar = TradingCalendar(holidays_file, extra_file)  # does not exist
    assert calendar.extra_days() == set() and calendar.is_trading_day(MONDAY)
    assert caplog.text == ""
    extra_file.write_text("")  # empty, as shipped
    assert calendar.extra_days() == set()


def test_a_missing_holiday_file_warns_once(tmp_path, caplog):
    caplog.set_level(logging.WARNING)
    calendar = TradingCalendar(tmp_path / "missing.txt")
    assert calendar.is_trading_day(HOLIDAY) and calendar.is_trading_day(MONDAY)
    assert caplog.text.count("only weekends count as closed") == 1


@pytest.mark.parametrize(
    ("hhmm", "market", "test_blocked"),
    [
        ("08:59", False, False),
        ("09:00", False, True),  # the pre-open: Test now would replace a working login
        ("09:14", False, True),
        ("09:15", True, True),
        ("15:29", True, True),
        ("15:30", True, True),  # the cash close: F&O trades to 15:40 (since 3 Aug 2026)
        ("15:39", True, True),
        ("15:40", False, False),
    ],
)
def test_windows(holidays_file, hhmm, market, test_blocked):
    calendar = TradingCalendar(holidays_file)
    hour, minute = map(int, hhmm.split(":"))
    at = datetime(2026, 10, 19, hour, minute, tzinfo=IST)
    assert calendar.in_market_hours(at) is market
    assert calendar.blocks_test_now(at) is test_blocked
    saturday = at.replace(day=24)
    assert not calendar.in_market_hours(saturday) and not calendar.blocks_test_now(saturday)


def test_the_page_and_the_answer_give_the_hours_test_now_is_refused():
    """F&O trades to 15:40 since 3 Aug 2026: the guards, the page and the answer all say so."""
    assert MARKET_HOURS == ("09:15", "15:40") and TEST_BLOCKED == ("09:00", "15:40")
    hours = f"from {TEST_BLOCKED[0]} to {TEST_BLOCKED[1]} on a trading day"
    page = REPO / "frontend" / "src" / "sudarshan" / "pages" / "AutoLogin.tsx"
    assert hours in " ".join(page.read_text(encoding="utf-8").split())
    assert hours in MESSAGES["market_hours"]


def test_auto_login_may_start_until_the_f_and_o_close():
    assert parse_window(DEFAULT_WINDOW) == (8 * 60 + 30, 15 * 60 + 40)


@pytest.mark.parametrize(
    ("at", "since"),
    [
        (datetime(2026, 10, 19, 3, 29, tzinfo=IST), datetime(2026, 10, 18, 3, 30, tzinfo=IST)),
        (datetime(2026, 10, 19, 3, 30, tzinfo=IST), datetime(2026, 10, 19, 3, 30, tzinfo=IST)),
        (datetime(2026, 10, 19, 9, 0, tzinfo=IST), datetime(2026, 10, 19, 3, 30, tzinfo=IST)),
    ],
)
def test_the_last_upstox_token_expiry(at, since):
    assert UPSTOX_TOKEN_EXPIRY == "03:30"
    assert trading_session_start(at, rollover=UPSTOX_TOKEN_EXPIRY) == since
