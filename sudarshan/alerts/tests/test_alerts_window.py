"""A special session's own window in the extra-days file, and the other two readers of that file."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from alerts_fakes import ist

from sudarshan.alerts.window import AlertWindow, parse_day_windows
from sudarshan.autologin.schedule import TradingCalendar, parse_window

REPO = Path(__file__).resolve().parents[3]
DEPLOY_TOOL = REPO / "sudarshan" / "ops" / "deploy" / "sudarshan-deploy"
MUHURAT = "2026-11-08 17:45-19:30 # Muhurat\n"


def _window(tmp_path, extra: str) -> AlertWindow:
    (tmp_path / "holidays.txt").write_text("2026-11-09\n")
    (tmp_path / "extra.txt").write_text(extra)
    calendar = TradingCalendar(tmp_path / "holidays.txt", tmp_path / "extra.txt")
    return AlertWindow(calendar, parse_window("08:45-16:00"), False, tmp_path / "extra.txt")


def test_the_window_after_the_date():
    days = parse_day_windows(MUHURAT + "2026-11-14  # a special Saturday, usual hours\n")
    assert days == {ist(2026, 11, 8).date(): (17 * 60 + 45, 19 * 60 + 30)}
    assert parse_day_windows("2026-11-08 later # not a window\n") == {}


@pytest.mark.parametrize(
    ("at", "open_"),
    [
        (ist(2026, 11, 8, 10, 0), False),  # the usual hours do not apply that day
        (ist(2026, 11, 8, 17, 44), False),
        (ist(2026, 11, 8, 17, 45), True),
        (ist(2026, 11, 8, 19, 29), True),
        (ist(2026, 11, 8, 19, 30), False),
        (ist(2026, 11, 10, 10, 0), True),  # an ordinary Tuesday keeps 08:45-16:00
    ],
)
def test_the_alert_window_on_a_special_day(tmp_path, at, open_):
    assert _window(tmp_path, MUHURAT).status(at)[0] is open_


def test_the_pause_starts_no_earlier_than_that_days_opening(tmp_path):
    window = _window(tmp_path, MUHURAT)
    assert window.opened_at(ist(2026, 11, 8, 18, 0)) == ist(2026, 11, 8, 17, 45)
    assert window.opened_at(ist(2026, 11, 10, 10, 0)) == ist(2026, 11, 10, 8, 45)
    assert window.next_open(ist(2026, 11, 8, 9, 0)) == ist(2026, 11, 8, 17, 45)


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
