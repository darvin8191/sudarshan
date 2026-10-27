"""The feed's trading days: auto-login's calendar, extra trading days and a day's own window.

Sunday 8 Nov 2026 is NSE's Muhurat session (circulars 149/2026 and 142/2026: pre-open 14:05,
continuous trading from 14:20, equity derivatives to 15:40). Auto-login and the alerts read the
same two files; their own tests (test_autologin_schedule.py, test_alerts_window.py) pin how they
read them.
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
from sudarshan.feed.protocol import pb2
from sudarshan.feed.service import FeedService
from sudarshan.feed.window import MarketWindow, iso

MUHURAT = (
    "2026-11-08 14:05-15:40  # Muhurat, NSE circulars 149/2026 (cash) and 142/2026 (F&O to 15:40)\n"
)
BEFORE, OPEN, AFTER = "before the market window", "market window", "after the market window"
OWN = ": extra trading day, own window 14:05-15:40, feed 14:03-15:50"
USUAL = ": extra trading day, usual hours 08:58-15:50, watchdog after the first trade"
NIFTY = "NSE_INDEX|Nifty 50"


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
    assert window.status(sunday(15, 49, 59)) == (True, OPEN + USUAL)
    assert window.status(sunday(15, 50)) == (False, AFTER + USUAL)
    assert window.in_core_session(sunday(9, 15)) and window.in_core_session(sunday(15, 39))
    assert not window.in_core_session(sunday(9, 14)) and not window.in_core_session(sunday(15, 40))
    assert window.waits_for_first_trade(sunday(10))
    assert not window.waits_for_first_trade(ff.ist(2026, 11, 10, 10))  # an ordinary Tuesday
    assert iso(window.next_open(ff.ist(2026, 11, 6, 16))) == "2026-11-08T08:58:00+05:30"
    assert window.status(ff.ist(2026, 11, 7, 10)) == (False, "weekend")  # Saturday: not listed


@pytest.mark.parametrize(
    ("hms", "where", "core"),
    [
        ("10:00:00", BEFORE, False),  # the usual hours do not apply that day
        ("14:02:59", BEFORE, False),
        ("14:03:00", OPEN, False),  # 2 minutes before the pre-open, as 08:58 is before 09:00
        ("14:19:59", OPEN, False),  # the pre-open: quiet is normal, no stall watchdog
        ("14:20:00", OPEN, True),  # continuous trading 15 minutes in, as 09:15 is after 09:00
        ("15:39:59", OPEN, True),
        ("15:40:00", OPEN, False),  # the F&O close: the watchdog stops, the socket stays
        ("15:49:59", OPEN, False),
        ("15:50:00", AFTER, False),  # 10 minutes after the close, as on any day
    ],
)
def test_a_special_session_keeps_the_usual_margins_around_its_own_window(files, hms, where, core):
    window = window_with(files, MUHURAT)
    at = sunday(*map(int, hms.split(":")))
    assert window.status(at) == (where == OPEN, where + OWN)
    assert window.in_core_session(at) is core
    assert not window.waits_for_first_trade(at)  # its own window says when it trades


def test_the_next_opening_follows_each_days_own_hours(files):
    window = window_with(files, MUHURAT)
    assert iso(window.next_open(ff.ist(2026, 11, 6, 16))) == "2026-11-08T14:03:00+05:30"
    assert iso(window.next_open(sunday(10))) == "2026-11-08T14:03:00+05:30"
    assert iso(window.next_open(sunday(14, 3))) == "2026-11-10T08:58:00+05:30"  # after now
    assert iso(window.next_open(sunday(15, 50))) == "2026-11-10T08:58:00+05:30"  # Monday: holiday
    assert window.status(ff.ist(2026, 11, 10, 8, 58)) == (True, OPEN)  # an ordinary day
    assert not window.in_core_session(ff.ist(2026, 11, 7, 10))  # nothing to watch on a closed day


def test_the_margins_are_the_ones_the_usual_window_keeps(files):
    wide = window_with(files, MUHURAT, window=(8 * 60 + 45, 16 * 60))  # 15 min before, 20 after
    own = ": extra trading day, own window 14:05-15:40, feed 13:50-16:00"
    assert wide.status(sunday(13, 50)) == (True, OPEN + own)
    assert not wide.status(sunday(13, 49, 59))[0] and not wide.status(sunday(16))[0]
    # A usual window that would leave the session no hours at all: the session itself.
    narrow = window_with(files, MUHURAT, window=(10 * 60, 11 * 60))
    assert narrow.status(sunday(14, 5))[0] and not narrow.status(sunday(15, 40))[0]


def test_a_window_at_either_end_of_the_day_stays_inside_it(files):
    window = window_with(files, "2026-11-08 00:00-01:00\n2026-11-14 22:00-24:00\n")
    # Moved 2 minutes earlier it would start on the day before: kept at 00:00, never a crash.
    assert iso(window.next_open(ff.ist(2026, 11, 7, 20))) == "2026-11-08T00:00:00+05:30"
    assert window.describe(sunday(0, 30))["hours"] == "00:00-01:10"
    late = ff.ist(2026, 11, 14, 23, 59, 59)
    assert window.status(late)[0] and window.describe(late)["hours"] == "21:58-24:00"
    assert window.status(ff.ist(2026, 11, 15, 0, 0, 1)) == (False, "weekend")  # midnight ends it


def test_a_window_that_cannot_be_read_keeps_the_usual_hours_with_a_warning(files, caplog):
    caplog.set_level(logging.INFO, logger="sudarshan.feed")
    _, extra = files
    window = window_with(files, "2026-11-08 14:05-1540  # Muhurat, mistyped\n")
    assert window.status(sunday(10)) == (True, OPEN + USUAL)
    assert window.status(sunday(16)) == (False, AFTER + USUAL)
    warnings = [(r.name, r.getMessage()) for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings == [
        (
            "sudarshan.feed",
            f"{extra}: extra trading day 2026-11-08: '14:05-1540' is not a window (HH:MM-HH:MM, "
            "the start before the end), so the day keeps the usual hours",
        )
    ]
    assert window.describe(sunday(10))["extra_days"] == "1 date, 1 window problem"


def test_a_day_that_trades_anyway_keeps_its_usual_hours_and_adds_its_own(files):
    window = window_with(
        files, "2026-11-10 18:00-19:00\n2026-11-11 09:00-17:00\n2026-11-12  # a date alone\n"
    )

    def tuesday(h: int, m: int = 0) -> float:
        return ff.ist(2026, 11, 10, h, m)

    added = ": own window 18:00-19:00 added to the usual hours, feed 08:58-15:50, 17:58-19:10"
    assert window.status(tuesday(10)) == (True, OPEN + added)
    assert window.status(tuesday(16, 30)) == (False, "between the day's market windows" + added)
    assert window.status(tuesday(18, 30)) == (True, OPEN + added)
    cores = [
        window.in_core_session(tuesday(h, m)) for h, m in ((10, 0), (16, 30), (18, 14), (18, 15))
    ]
    assert cores == [True, False, False, True]
    assert iso(window.next_open(tuesday(16, 30))) == "2026-11-10T17:58:00+05:30"
    assert window.describe(tuesday(10))["hours"] == "08:58-15:50, 17:58-19:10"
    assert not window.waits_for_first_trade(tuesday(10))
    longer = ": own window 09:00-17:00 added to the usual hours, feed 08:58-17:10"
    assert window.status(ff.ist(2026, 11, 11, 17, 5)) == (True, OPEN + longer)
    # A date alone on such a day changes nothing: no label, and the watchdog runs from 09:15.
    thursday = ff.ist(2026, 11, 12, 10)
    assert window.status(thursday) == (True, OPEN)
    assert window.in_core_session(thursday) and not window.waits_for_first_trade(thursday)


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
    # One line, each reader's own margins: the alerts 15 and 20 minutes, the feed 2 and 10.
    assert alerts.spans(date(2026, 11, 8)) == ((13 * 60 + 50, 16 * 60),)
    assert window.describe(sunday(14, 30))["hours"] == "14:03-15:50"
    assert window.status(sunday(14, 30))[1].endswith(OWN)
    assert window.status(ff.ist(2026, 11, 14, 9))[1] == OPEN + USUAL


def test_a_missing_or_unreadable_extra_days_file_means_none_and_is_told_once(files, caplog):
    caplog.set_level(logging.INFO, logger="sudarshan.feed")
    _, extra = files
    window = window_with(files)  # no extra-days file: the shipped state
    for _ in range(3):
        assert window.status(sunday(14, 30)) == (False, "weekend")
    assert window.describe(sunday(14, 30))["extra_days"] == "missing"
    extra.mkdir()  # a directory where the file should be
    for _ in range(3):
        assert window.status(sunday(14, 30)) == (False, "weekend")
    assert window.describe(sunday(14, 30))["extra_days"] == "not readable"
    extra.rmdir()
    _write(extra, MUHURAT)  # repaired: read again, no restart
    assert window.status(sunday(14, 30)) == (True, OPEN + OWN)
    extra.unlink()  # gone again after a good read: told again
    assert window.status(sunday(14, 30)) == (False, "weekend")
    messages = [r.getMessage() for r in caplog.records]
    assert messages.count(f"{extra} not found: no extra trading days") == 2
    assert messages.count(f"{extra} not readable: no extra trading days") == 1


def test_every_read_of_the_extra_days_file_is_told_in_one_line(files, caplog):
    caplog.set_level(logging.INFO, logger="sudarshan.feed")
    _, extra = files
    window = window_with(files, MUHURAT + "2026-11-14  # a Saturday session at the usual hours\n")
    for _ in range(3):
        window.status(sunday(14, 30))
    _write(extra, MUHURAT)
    window.status(sunday(14, 30))
    told = [r.getMessage() for r in caplog.records if r.getMessage().startswith(f"{extra} read")]
    assert told == [
        f"{extra} read: extra trading days 2026-11-08 14:05-15:40, 2026-11-14 (usual)",
        f"{extra} read: extra trading days 2026-11-08 14:05-15:40",
    ]


def test_a_stray_byte_in_the_holidays_file_costs_only_its_own_line(files, caplog):
    # The feed's first reader caught OSError only: a decode error left status() and ended the run.
    holidays, _ = files
    holidays.write_bytes(b"2026-11-09\n\xff\xfe\n")
    window = window_with(files)
    for _ in range(3):
        assert window.status(ff.ist(2026, 11, 9, 10)) == (False, "NSE holiday")
    assert window.note == "1 date, 1 unreadable line"
    assert caplog.text.count("1 unreadable line ignored") == 1


def test_the_feed_follows_the_file_while_it_runs_and_health_says_why(
    feed_env, fast, monkeypatch, tmp_path
):
    monkeypatch.setattr(service, "CLOSED_POLL_S", 0.05)
    now = [sunday(14, 30)]  # Muhurat Sunday, in continuous trading
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
                "holidays": "1 date",
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
                "hours": "14:03-15:50",
                "holidays": "1 date",
                "extra_days": "1 date, 1 own window",
                "next_open": None,
            }
            _write(extra, "2026-11-08 13:00-14:00  # moved earlier\n")  # 14:30 is now after it
            await ff.wait_until(lambda: fake.close_codes == [1000])  # a close frame
            await ff.wait_until(lambda: svc.health()["state"] == "closed")
            health = svc.health()
            assert health["last_disconnect"]["by"] == "market window closed"
            assert (
                health["detail"]
                == health["market_window"]["reason"]
                == (AFTER + ": extra trading day, own window 13:00-14:00, feed 12:58-14:10")
            )
            assert health["market_window"]["hours"] == "12:58-14:10"
            assert health["market_window"]["next_open"] == "2026-11-10T08:58:00+05:30"
            _write(extra, MUHURAT)  # put back: open again, no restart
            await ff.wait_until(lambda: svc.health()["state"] == "connected")
            svc.stop("test")
            await asyncio.wait_for(run, 10)
            assert fake.authorize_calls == 2

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("at", "line", "quiet_reconnects"),
    [
        (sunday(10), "2026-11-08  # a date alone\n", False),  # a closed market's silence
        (sunday(10), "2026-11-08 14:05-1540  # mistyped\n", False),
        (ff.ist(2026, 10, 21, 10), "", True),  # an ordinary Wednesday, as before
        (sunday(14, 30), MUHURAT, True),  # inside the session its own window gives
    ],
)
def test_the_stall_watchdog_waits_for_a_reopened_days_first_trade(
    feed_env, fast, monkeypatch, tmp_path, at, line, quiet_reconnects
):
    monkeypatch.setattr(service, "STALL_S", 0.2)
    (tmp_path / "nse_holidays.txt").write_text("2026-11-09\n")
    (tmp_path / "nse_extra_trading_days.txt").write_text(line)

    async def scenario():
        async with ff.FakeUpstox() as fake:  # sends nothing unless told: a quiet market
            env = feed_env(fake, SUDARSHAN_FEED_ALWAYS_ON="0")
            svc = FeedService(Settings.from_env(env), clock=lambda: at)
            run = asyncio.create_task(svc.run())
            await ff.wait_until(lambda: svc.health()["state"] == "connected")
            await asyncio.sleep(1.0)  # five times STALL_S
            assert (fake.authorize_calls > 1) is quiet_reconnects, fake.authorize_calls
            if not quiet_reconnects:
                friday = int(at * 1000) - 2 * 86_400_000  # a snapshot of the last session's trade
                stale = pb2.Feed(ltpc=pb2.LTPC(ltp=24900.0, ltt=friday))
                await fake.sockets[-1].send(ff.response({NIFTY: stale}, kind="initial_feed"))
                await asyncio.sleep(1.0)
                assert fake.authorize_calls == 1  # an old trade does not say the day trades
                trade = pb2.Feed(ltpc=pb2.LTPC(ltp=25000.0, ltt=int(at * 1000) - 5000))
                await fake.sockets[-1].send(ff.response({NIFTY: trade}))
                await ff.wait_until(lambda: fake.authorize_calls > 1)  # now silence is a stall
            svc.stop("test")
            await asyncio.wait_for(run, 10)

    asyncio.run(scenario())


def test_each_reopened_day_waits_for_its_own_first_trade(feed_env, fast, monkeypatch, tmp_path):
    """A service that saw Friday trade, still running on the reopened Sunday: Friday's trade does
    not arm Sunday's watchdog, and Sunday's first trade does."""
    monkeypatch.setattr(service, "STALL_S", 0.2)
    (tmp_path / "nse_holidays.txt").write_text("2026-11-09\n")
    (tmp_path / "nse_extra_trading_days.txt").write_text("2026-11-08  # a date alone\n")
    clock = [ff.ist(2026, 11, 6, 9, 5)]  # Friday, before continuous trading: no watchdog yet

    async def scenario():
        async with ff.FakeUpstox() as fake:
            env = feed_env(fake, SUDARSHAN_FEED_ALWAYS_ON="0")
            svc = FeedService(Settings.from_env(env), clock=lambda: clock[0])
            run = asyncio.create_task(svc.run())
            await ff.wait_until(lambda: svc.health()["state"] == "connected")
            friday = pb2.Feed(ltpc=pb2.LTPC(ltp=24900.0, ltt=int(clock[0] * 1000) - 5000))
            await fake.sockets[-1].send(ff.response({NIFTY: friday}, kind="initial_feed"))
            await asyncio.sleep(0.5)
            clock[0] = sunday(10)  # two days on, the same process and socket
            await asyncio.sleep(1.0)  # five times STALL_S: Sunday has not traded yet
            assert fake.authorize_calls == 1
            trade = pb2.Feed(ltpc=pb2.LTPC(ltp=25000.0, ltt=int(clock[0] * 1000) - 5000))
            await fake.sockets[-1].send(ff.response({NIFTY: trade}))
            await ff.wait_until(lambda: fake.authorize_calls > 1)  # now silence is a stall
            svc.stop("test")
            await asyncio.wait_for(run, 10)

    asyncio.run(scenario())
