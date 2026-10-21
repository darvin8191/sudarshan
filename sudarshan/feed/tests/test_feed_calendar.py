"""The feed's trading days: auto-login's calendar, extra trading days and a day's own window.

Sunday 8 Nov 2026 is NSE's Muhurat session. Auto-login and the alerts read the same two files;
their own tests (test_autologin_schedule.py, test_alerts_window.py) pin how they read them.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import date, timedelta
from pathlib import Path

import feed_fakes as ff
import httpx
import pytest

from sudarshan.alerts.window import AlertWindow
from sudarshan.autologin.schedule import EXTRA_TRADING_DAYS_FILE, TradingCalendar
from sudarshan.feed import service
from sudarshan.feed.config import Settings
from sudarshan.feed.service import FeedService
from sudarshan.feed.window import MarketWindow, iso

MUHURAT = "2026-11-08 17:45-19:30  # Muhurat\n"
BEFORE, OPEN, AFTER = "before the market window", "market window", "after the market window"
OWN = ": extra trading day, own window 17:45-19:30, feed 17:43-19:40"
USUAL = ": extra trading day, usual hours 08:58-15:40"


def sunday(h: int, m: int = 0, s: int = 0) -> float:
    return ff.ist(2026, 11, 8, h, m, s)


def _write(path: Path, text: str) -> None:
    """Write, then move the mtime on, so the change is seen even where timestamps are coarse."""
    before = path.stat().st_mtime_ns if path.exists() else 0
    path.write_text(text)
    after = max(path.stat().st_mtime_ns, before + 10**9)
    os.utime(path, ns=(after, after))


@pytest.fixture
def files(tmp_path):
    holidays = tmp_path / "nse_holidays.txt"
    holidays.write_text("2026-11-09  # Monday, a holiday\n")
    return holidays, tmp_path / "nse_extra_trading_days.txt"


def window_with(files, extra: str | None = None, **kwargs) -> MarketWindow:
    holidays, extra_file = files
    if extra is not None:
        extra_file.write_text(extra)
    return MarketWindow(holidays, extra_days_file=extra_file, **kwargs)


def test_settings_read_the_extra_days_file_the_others_read():
    default = Path(EXTRA_TRADING_DAYS_FILE)
    assert default == Path("/etc/sudarshan/nse_extra_trading_days.txt")
    assert Settings.from_env({}).extra_days_file == default
    assert Settings.from_env({"SUDARSHAN_NSE_EXTRA_DAYS_FILE": ""}).extra_days_file == default
    custom = {"SUDARSHAN_NSE_EXTRA_DAYS_FILE": "/srv/extra.txt"}
    assert Settings.from_env(custom).extra_days_file == Path("/srv/extra.txt")


def test_an_extra_sunday_without_a_window_keeps_the_usual_hours(files):
    window = window_with(files, "2026-11-08  # a session at the usual hours\n")
    assert window.status(sunday(8, 57, 59)) == (False, BEFORE + USUAL)
    assert window.status(sunday(8, 58)) == (True, OPEN + USUAL)
    assert window.status(sunday(15, 39, 59)) == (True, OPEN + USUAL)
    assert window.status(sunday(15, 40)) == (False, AFTER + USUAL)
    assert window.in_core_session(sunday(9, 15)) and not window.in_core_session(sunday(9, 14))
    assert iso(window.next_open(ff.ist(2026, 11, 6, 16))) == "2026-11-08T08:58:00+05:30"
    assert window.status(ff.ist(2026, 11, 7, 10)) == (False, "weekend")  # Saturday: not listed


@pytest.mark.parametrize(
    ("hms", "where", "core"),
    [
        ("10:00:00", BEFORE, False),  # the usual hours do not apply that day
        ("17:42:59", BEFORE, False),
        ("17:43:00", OPEN, False),  # 2 minutes before the session, as 08:58 is before 09:00
        ("17:59:59", OPEN, False),  # its pre-open: quiet is normal, no stall watchdog
        ("18:00:00", OPEN, True),  # continuous trading 15 minutes in, as 09:15 is after 09:00
        ("19:29:59", OPEN, True),
        ("19:30:00", OPEN, False),  # the close: the watchdog stops, the socket stays
        ("19:39:59", OPEN, False),
        ("19:40:00", AFTER, False),  # 10 minutes after the close, as 15:40 is after 15:30
    ],
)
def test_a_special_session_keeps_the_usual_margins_around_its_own_window(files, hms, where, core):
    window = window_with(files, MUHURAT)
    at = sunday(*map(int, hms.split(":")))
    assert window.status(at) == (where == OPEN, where + OWN)
    assert window.in_core_session(at) is core


def test_the_next_opening_follows_each_days_own_hours(files):
    window = window_with(files, MUHURAT)
    assert iso(window.next_open(ff.ist(2026, 11, 6, 16))) == "2026-11-08T17:43:00+05:30"
    assert iso(window.next_open(sunday(10))) == "2026-11-08T17:43:00+05:30"
    assert iso(window.next_open(sunday(19, 40))) == "2026-11-10T08:58:00+05:30"  # Monday: holiday
    assert window.status(ff.ist(2026, 11, 10, 8, 58)) == (True, OPEN)  # an ordinary day


def test_the_margins_are_the_ones_the_usual_window_keeps(files):
    wide = window_with(files, MUHURAT, window=(8 * 60 + 45, 16 * 60))  # 15 min before, 30 after
    own = ": extra trading day, own window 17:45-19:30, feed 17:30-20:00"
    assert wide.status(sunday(17, 30)) == (True, OPEN + own)
    assert not wide.status(sunday(17, 29, 59))[0] and not wide.status(sunday(20))[0]
    # A usual window that would leave the session no hours at all: the session itself.
    narrow = window_with(files, MUHURAT, window=(10 * 60, 11 * 60))
    assert narrow.status(sunday(17, 45))[0] and not narrow.status(sunday(19, 30))[0]


def test_a_window_that_cannot_be_read_keeps_the_usual_hours_with_a_warning(files, caplog):
    caplog.set_level(logging.INFO, logger="sudarshan.feed")
    line = "2026-11-08 17:45-1930  # Muhurat, mistyped"
    window = window_with(files, line + "\n")
    assert window.status(sunday(10)) == (True, OPEN + USUAL)
    assert window.status(sunday(18)) == (False, AFTER + USUAL)
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert [(r.name, r.getMessage()) for r in warnings] == [
        (
            "sudarshan.feed",
            f"extra trading day {line!r}: the window is not HH:MM-HH:MM; usual hours",
        )
    ]
    assert window.describe(sunday(10))["extra_days"] == "1 dates, 1 unreadable windows"


def test_an_extra_day_wins_over_the_holiday_file_as_in_the_other_two_readers(files):
    # Auto-login's calendar, the alerts and the deploy tool all treat a date in both files as open.
    holidays, extra = files
    window = window_with(files, "2026-11-09 18:00-19:00  # a session on a listed holiday\n")
    own = ": extra trading day, own window 18:00-19:00, feed 17:58-19:10"
    assert window.status(ff.ist(2026, 11, 9, 18, 30)) == (True, OPEN + own)
    assert window.status(ff.ist(2026, 11, 9, 10)) == (False, BEFORE + own)
    assert TradingCalendar(holidays, extra).is_trading_day(date(2026, 11, 9))
    _write(extra, "")  # taken out of the extra-days file: the holiday closes the day again
    assert window.status(ff.ist(2026, 11, 9, 18, 30)) == (False, "NSE holiday")


def test_the_feed_and_the_alerts_read_the_same_days_and_windows(files):
    holidays, extra = files
    window = window_with(files, MUHURAT + "2026-11-14  # a special Saturday\n2026-11-09\n")
    calendar = TradingCalendar(holidays, extra)
    alerts = AlertWindow(calendar, (8 * 60 + 45, 16 * 60), False, extra)
    for offset in range(14):
        day = date(2026, 11, 2) + timedelta(days=offset)
        assert window.is_trading_day(day) is calendar.is_trading_day(day), day
    assert alerts.span(date(2026, 11, 8)) == (17 * 60 + 45, 19 * 60 + 30)
    assert window.status(sunday(18))[1].endswith(OWN)
    assert window.status(ff.ist(2026, 11, 14, 9))[1] == OPEN + USUAL


def test_a_missing_or_unreadable_extra_days_file_means_none_and_is_logged_once(files, caplog):
    caplog.set_level(logging.INFO, logger="sudarshan.feed")
    holidays, extra = files
    window = window_with(files)  # no extra-days file: the shipped state
    for _ in range(3):
        assert window.status(sunday(18)) == (False, "weekend")
    assert window.describe(sunday(18))["extra_days"] == "missing"
    extra.write_bytes(b"2026-11-08 17:45-19:30 \xff\xfe\n")  # not UTF-8
    for _ in range(3):
        assert window.status(sunday(18)) == (False, "weekend")
    assert window.describe(sunday(18))["extra_days"] == "not readable"
    messages = [r.getMessage() for r in caplog.records]
    assert sum(m == f"{extra} not found: no extra trading days" for m in messages) == 1
    assert sum(m == f"{extra} not readable: no extra trading days" for m in messages) == 1
    _write(extra, MUHURAT)  # repaired: read again, no restart
    assert window.status(sunday(18)) == (True, OPEN + OWN)


def test_a_holidays_file_that_is_not_utf8_no_longer_stops_the_feed(files, caplog):
    # The feed's own reader caught OSError only: the decode error left status() and ended the run.
    holidays, _ = files
    holidays.write_bytes(b"2026-11-09\n\xff\xfe\n")
    window = window_with(files)
    for _ in range(3):
        assert window.status(ff.ist(2026, 11, 9, 10)) == (True, OPEN)  # only weekends closed
    assert window.note == "not readable"
    assert caplog.text.count("not readable: only weekends count as closed") == 1


def test_the_feed_follows_the_file_while_it_runs_and_health_says_why(
    feed_env, fast, monkeypatch, tmp_path
):
    monkeypatch.setattr(service, "CLOSED_POLL_S", 0.05)
    now = [sunday(18)]  # Muhurat Sunday, inside the session
    (tmp_path / "nse_holidays.txt").write_text("2026-11-09\n")
    extra = tmp_path / "nse_extra_trading_days.txt"

    async def scenario():
        async with ff.FakeUpstox() as fake:
            env = feed_env(fake, SUDARSHAN_FEED_ALWAYS_ON="0")
            assert env["SUDARSHAN_NSE_EXTRA_DAYS_FILE"] == str(extra)
            svc = FeedService(Settings.from_env(env), clock=lambda: now[0])
            run = asyncio.create_task(svc.run())
            await ff.wait_until(lambda: svc.health()["state"] == "closed")
            assert svc.health()["market_window"] == {
                "open": False,
                "reason": "weekend",
                "hours": None,
                "holidays": "1 dates",
                "extra_days": "missing",
                "next_open": "2026-11-10T08:58:00+05:30",  # Monday 9 Nov is a holiday
            }
            assert fake.authorize_calls == 0
            _write(extra, MUHURAT)  # the owner lists the session while the service runs
            await ff.wait_until(lambda: svc.health()["state"] == "connected")
            async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{svc.http_port}") as http:
                health = (await http.get("/health")).json()
            assert health["market_window"] == {
                "open": True,
                "reason": OPEN + OWN,
                "hours": "17:43-19:40",
                "holidays": "1 dates",
                "extra_days": "1 dates, 1 own windows",
                "next_open": None,
            }
            _write(extra, "2026-11-08 17:00-17:45  # moved earlier\n")  # 18:00 is now after it
            await ff.wait_until(lambda: fake.close_codes == [1000])  # a close frame
            await ff.wait_until(lambda: svc.health()["state"] == "closed")
            health = svc.health()
            assert health["last_disconnect"]["by"] == "market window closed"
            assert (
                health["detail"]
                == health["market_window"]["reason"]
                == (AFTER + ": extra trading day, own window 17:00-17:45, feed 16:58-17:55")
            )
            assert health["market_window"]["hours"] == "16:58-17:55"
            assert health["market_window"]["next_open"] == "2026-11-10T08:58:00+05:30"
            svc.stop("test")
            await asyncio.wait_for(run, 10)
            assert fake.authorize_calls == 1

    asyncio.run(scenario())
