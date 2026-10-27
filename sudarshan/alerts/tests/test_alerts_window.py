"""A special session's own window in the extra-days file, and the other two readers of that file."""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

import pytest
from alerts_fakes import ist

from sudarshan.alerts.config import Settings
from sudarshan.alerts.window import AlertWindow, parse_day_windows
from sudarshan.autologin.schedule import TradingCalendar, parse_window

REPO = Path(__file__).resolve().parents[3]
DEPLOY_TOOL = REPO / "sudarshan" / "ops" / "deploy" / "sudarshan-deploy"
MUHURAT = (
    "2026-11-08 14:05-15:40  # Muhurat, NSE circulars 149/2026 (cash) and 142/2026 (F&O to 15:40)\n"
)


def _window(tmp_path, extra: str) -> AlertWindow:
    (tmp_path / "holidays.txt").write_text("2026-11-09\n")
    (tmp_path / "extra.txt").write_text(extra)
    calendar = TradingCalendar(tmp_path / "holidays.txt", tmp_path / "extra.txt")
    return AlertWindow(calendar, parse_window("08:45-16:00"), False, tmp_path / "extra.txt")


def test_the_window_after_the_date():
    days = parse_day_windows(MUHURAT + "2026-11-14  # a special Saturday, usual hours\n")
    assert days == {ist(2026, 11, 8).date(): (14 * 60 + 5, 15 * 60 + 40)}
    assert parse_day_windows("2026-11-08 later # not a window\n") == {}


def test_a_window_that_cannot_be_used_is_logged_by_the_alerts(caplog):
    caplog.set_level(logging.WARNING, logger="sudarshan.alerts")
    assert parse_day_windows("2026-11-08 14:05-1540  # mistyped\n") == {}
    assert [(record.name, record.getMessage()) for record in caplog.records] == [
        (
            "sudarshan.alerts",
            "extra trading day 2026-11-08: '14:05-1540' is not a window (HH:MM-HH:MM, the start "
            "before the end), so the day keeps the usual hours",
        )
    ]


@pytest.mark.parametrize(
    ("at", "open_"),
    [
        (ist(2026, 11, 8, 10, 0), False),  # the usual hours do not apply that day
        (ist(2026, 11, 8, 13, 49), False),
        (ist(2026, 11, 8, 13, 50), True),  # 15 minutes before the pre-open, as 08:45 is
        (ist(2026, 11, 8, 15, 59), True),  # after the F&O close: its cancellations still come
        (ist(2026, 11, 8, 16, 0), False),  # 20 minutes after it, as 16:00 is after 15:40
        (ist(2026, 11, 10, 10, 0), True),  # an ordinary Tuesday keeps 08:45-16:00
    ],
)
def test_the_alert_window_on_a_special_day(tmp_path, at, open_):
    assert _window(tmp_path, MUHURAT).status(at)[0] is open_


def test_the_pause_starts_no_earlier_than_that_days_opening(tmp_path):
    window = _window(tmp_path, MUHURAT)
    assert window.opened_at(ist(2026, 11, 8, 14, 0)) == ist(2026, 11, 8, 13, 50)
    assert window.opened_at(ist(2026, 11, 10, 10, 0)) == ist(2026, 11, 10, 8, 45)
    assert window.next_open(ist(2026, 11, 8, 9, 0)) == ist(2026, 11, 8, 13, 50)
    # Strictly after now: at that very minute the next opening is Tuesday's (Monday a holiday).
    assert window.next_open(ist(2026, 11, 8, 13, 50)) == ist(2026, 11, 10, 8, 45)


def test_a_day_that_trades_anyway_keeps_its_usual_hours_and_adds_its_own(tmp_path):
    window = _window(tmp_path, "2026-11-10 18:00-19:15\n2026-11-11 14:00-17:00\n")
    assert window.spans(ist(2026, 11, 10).date()) == (
        (8 * 60 + 45, 16 * 60),
        (17 * 60 + 45, 19 * 60 + 35),
    )
    assert window.status(ist(2026, 11, 10, 10, 0)) == (True, "alert window")
    assert window.status(ist(2026, 11, 10, 17, 0)) == (False, "between the day's alert windows")
    assert window.status(ist(2026, 11, 10, 19, 34)) == (True, "alert window")
    assert window.next_open(ist(2026, 11, 10, 16, 30)) == ist(2026, 11, 10, 17, 45)
    # The pause after the gap counts from the first opening: those hours are a pause too.
    assert window.opened_at(ist(2026, 11, 10, 18, 0)) == ist(2026, 11, 10, 8, 45)
    assert window.spans(ist(2026, 11, 11).date()) == ((8 * 60 + 45, 17 * 60 + 20),)


def test_a_file_that_goes_away_takes_its_windows_with_it(tmp_path):
    window = _window(tmp_path, "2026-11-10 18:00-19:15\n")
    tuesday = ist(2026, 11, 10).date()
    assert window.spans(tuesday) == ((8 * 60 + 45, 16 * 60), (17 * 60 + 45, 19 * 60 + 35))
    (tmp_path / "extra.txt").unlink()
    assert window.spans(tuesday) == ((8 * 60 + 45, 16 * 60),)


def test_the_margins_stay_inside_the_day(tmp_path):
    window = _window(tmp_path, "2026-11-14 00:05-01:00\n2026-11-15 22:00-23:50\n")
    assert window.spans(ist(2026, 11, 14).date()) == ((0, 60 + 20),)
    assert window.spans(ist(2026, 11, 15).date()) == ((21 * 60 + 45, 24 * 60),)
    assert window.next_open(ist(2026, 11, 13, 20, 0)) == ist(2026, 11, 14, 0, 0)


def test_auto_login_still_reads_the_day(tmp_path):
    (tmp_path / "holidays.txt").write_text("")
    (tmp_path / "extra.txt").write_text(MUHURAT)
    calendar = TradingCalendar(tmp_path / "holidays.txt", tmp_path / "extra.txt")
    assert calendar.is_trading_day(ist(2026, 11, 8).date())  # a Sunday made a trading day


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_the_deploy_tool_still_reads_the_day(tmp_path):
    """The deploy tool's listed() and market_open(), cut out as they ship, on a fake clock."""
    text = DEPLOY_TOOL.read_text()
    start = text.index("listed() {")
    end = text.index("\n}\n", text.index("market_open() {")) + 3
    gate = text[start:end].replace("/etc/sudarshan/", f"{tmp_path}/")
    (tmp_path / "nse_holidays.txt").write_text("")
    (tmp_path / "nse_extra_trading_days.txt").write_text(MUHURAT)
    script = f'date() {{ echo "7 1830 2026-11-08"; }}\n{gate}\nmarket_open\n'
    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    assert done.returncode == 0  # trading: no deploy that day


def test_the_market_opens_with_the_pre_open_or_a_special_sessions_own_start(tmp_path):
    """Where a pause stops being quiet (service.py): the exchange's start, not the alerts' own."""
    extra = MUHURAT + "2026-11-10 08:00-08:30  # an early session\n2026-11-11 18:00-19:15\n"
    window = _window(tmp_path, extra)
    assert window.market_opens(ist(2026, 11, 6, 8, 50)) == ist(2026, 11, 6, 9, 0)  # a Friday
    assert window.market_opens(ist(2026, 11, 8, 13, 55)) == ist(2026, 11, 8, 14, 5)  # Muhurat
    assert window.market_opens(ist(2026, 11, 10, 7, 0)) == ist(2026, 11, 10, 8, 0)  # the earlier
    assert window.market_opens(ist(2026, 11, 11, 7, 0)) == ist(2026, 11, 11, 9, 0)
    always = AlertWindow(window.calendar, parse_window("08:45-16:00"), True, tmp_path / "extra.txt")
    assert always.market_opens(ist(2026, 11, 6, 8, 50)) is None


@pytest.mark.parametrize("value", ["16:00-08:45", "8:45-16:0", "08:45-16:00\u3000", "08:45"])
def test_a_window_setting_that_is_not_a_window_keeps_the_usual_hours(caplog, value):
    """The looser reading before took some of these: they cost a warning, never the start."""
    caplog.set_level(logging.WARNING, logger="sudarshan.alerts")
    assert Settings.from_env({"SUDARSHAN_ALERTS_WINDOW": value}).window == (8 * 60 + 45, 16 * 60)
    assert [(record.levelname, record.getMessage()) for record in caplog.records] == [
        (
            "WARNING",
            f"SUDARSHAN_ALERTS_WINDOW is set to {value!r}, which is not a time window "
            "(HH:MM-HH:MM, the start before the end), so the usual 08:45-16:00 is used",
        )
    ]
