"""The ring's own hours on both sides of 7 Dec 2026: auto-login, the feed, the alerts' window and
the backup keep their margins around each day's session (schedule.usual_on), so every end moves
with the F&O close from 15:40 to 15:45. Before 7 Dec each is exactly as configured.

Days: Fri 4 Dec and Thu 3 Dec (a SENSEX expiry) before, Tue 8 Dec (a NIFTY expiry) and Thu 10 Dec
after. A window the owner set moves the same way only when it ends where the usual one ends; any
other end the owner chose stays as set (review L3).
"""

from __future__ import annotations

import logging
from datetime import date, datetime

import pytest

from sudarshan.autologin import schedule, worker
from sudarshan.autologin.config import ConfigError
from sudarshan.autologin.schedule import IST, TradingCalendar, minutes, parse_window, usual_on

BEFORE = (date(2026, 12, 3), date(2026, 12, 4))
AFTER = (date(2026, 12, 7), date(2026, 12, 8), date(2026, 12, 10))


@pytest.fixture
def holidays(tmp_path):
    path = tmp_path / "nse_holidays.txt"
    path.write_text("# test list\n2026-12-25\n")
    return path


def at(day: date, hhmm: str) -> datetime:
    return datetime(day.year, day.month, day.day, *map(int, hhmm.split(":")), tzinfo=IST)


def text(span: tuple[int, int]) -> str:
    return schedule.window_text(span)


def test_the_day_s_session_runs_from_the_pre_open_to_its_f_and_o_close():
    assert schedule.SESSION == (9 * 60, 15 * 60 + 40)
    for day in BEFORE:
        assert text(schedule.session_of(day)) == "09:00-15:40"
    for day in AFTER:
        assert text(schedule.session_of(day)) == "09:00-15:45"


@pytest.mark.parametrize(
    ("usual", "after"),
    [
        ("08:58-15:50", "08:58-15:55"),  # the feed
        ("08:45-16:00", "08:45-16:05"),  # the alerts
        ("08:30-16:00", "08:30-16:05"),  # the backup, the REST governor's bulk hours
        ("08:30-15:40", "08:30-15:45"),  # auto-login's wake-ups
        ("09:15-15:40", "09:15-15:45"),  # market hours: no new login while unchecked
        ("09:00-15:40", "09:00-15:45"),  # Test now refused
        ("09:00-15:30", "09:00-15:35"),  # any window written against the session moves with it
    ],
)
def test_usual_hours_are_as_configured_before_and_move_with_the_close_after(usual, after):
    for day in BEFORE:
        assert text(usual_on(day, parse_window(usual))) == usual
    for day in AFTER:
        assert text(usual_on(day, parse_window(usual))) == after


@pytest.mark.parametrize(
    ("hhmm", "before", "after"),
    [("15:39", True, True), ("15:40", False, True), ("15:44", False, True), ("15:45", False, False)],
)
def test_auto_login_s_market_hours_and_test_now_follow_the_close(holidays, hhmm, before, after):
    calendar = TradingCalendar(holidays)
    for day in BEFORE:
        assert calendar.in_market_hours(at(day, hhmm)) is before
        assert calendar.blocks_test_now(at(day, hhmm)) is before
    for day in AFTER:
        assert calendar.in_market_hours(at(day, hhmm)) is after
        assert calendar.blocks_test_now(at(day, hhmm)) is after
    assert not calendar.in_market_hours(at(date(2026, 12, 25), "10:00"))  # Christmas


def test_a_special_day_after_the_change_keeps_its_own_window(holidays, tmp_path):
    extra = tmp_path / "nse_extra_trading_days.txt"
    extra.write_text("2026-12-12 14:05-15:45  # a Saturday session\n")
    calendar = TradingCalendar(holidays, extra)
    saturday = date(2026, 12, 12)
    assert calendar.blocks_test_now(at(saturday, "14:05"))
    assert calendar.in_market_hours(at(saturday, "15:44"))
    assert not calendar.in_market_hours(at(saturday, "15:45"))
    assert not calendar.blocks_test_now(at(saturday, "10:00"))


def _scheduled_wake(holidays, when: datetime, monkeypatch, caplog) -> int:
    """A scheduled wake-up at ``when``: EXIT_OK and a log line when outside the window, else it
    goes on to read OpenAlgo's settings (made to fail here: EXIT_CONFIG)."""

    def unreadable(path):
        raise ConfigError("not read in this test")

    monkeypatch.setattr(worker, "read_openalgo_env", unreadable)
    caplog.set_level(logging.INFO, logger="sudarshan.autologin")
    env = {"SUDARSHAN_NSE_HOLIDAYS_FILE": str(holidays)}
    return worker.main([], env=env, clock=lambda: when)


def test_a_wake_up_at_15_42_is_outside_the_window_before_and_inside_after(holidays, monkeypatch, caplog):
    assert _scheduled_wake(holidays, at(date(2026, 12, 4), "15:42"), monkeypatch, caplog) == 0
    assert "outside the auto-login window" in caplog.text
    caplog.clear()
    assert _scheduled_wake(holidays, at(date(2026, 12, 10), "15:42"), monkeypatch, caplog) == 2
    assert "outside the auto-login window" not in caplog.text
    caplog.clear()
    assert _scheduled_wake(holidays, at(date(2026, 12, 10), "15:45"), monkeypatch, caplog) == 0
    assert "outside the auto-login window" in caplog.text


def test_the_feed_holds_its_socket_and_watches_for_stalls_to_the_day_s_close(holidays):
    from sudarshan.feed.window import MarketWindow

    window = MarketWindow(holidays, window=parse_window("08:58-15:50"))
    for day in BEFORE:
        plan = window.plan(day)
        assert [text(s) for s in plan.spans] == ["08:58-15:50"]
        assert [text(s) for s in plan.cores] == ["09:15-15:40"]
        assert window.core_age(at(day, "15:42").timestamp()) is None
        assert window.status(at(day, "15:52").timestamp())[0] is False
    for day in AFTER:
        plan = window.plan(day)
        assert [text(s) for s in plan.spans] == ["08:58-15:55"]
        assert [text(s) for s in plan.cores] == ["09:15-15:45"]
        assert window.core_age(at(day, "15:42").timestamp()) is not None
        assert window.core_age(at(day, "15:45").timestamp()) is None
        assert window.status(at(day, "15:52").timestamp()) == (True, "market window")
        assert window.describe(at(day, "10:00").timestamp())["hours"] == "08:58-15:55"


def test_the_rest_governor_keeps_bulk_out_to_20_minutes_after_the_close(holidays):
    from sudarshan.feed.rest import MarketHours
    from sudarshan.feed.window import MarketWindow

    hours = MarketHours(MarketWindow(holidays).calendar)
    friday, thursday = date(2026, 12, 4), date(2026, 12, 10)
    assert hours.end_of(at(friday, "16:02").timestamp()) is None
    assert hours.end_of(at(friday, "15:59").timestamp()) == at(friday, "16:00").timestamp()
    assert hours.end_of(at(thursday, "16:02").timestamp()) == at(thursday, "16:05").timestamp()


def test_the_alerts_stay_connected_to_20_minutes_after_the_close(holidays):
    from sudarshan.alerts.window import AlertWindow

    alerts = AlertWindow(TradingCalendar(holidays), parse_window("08:45-16:00"), always_on=False)
    for day in BEFORE:
        assert [text(s) for s in alerts.spans(day)] == ["08:45-16:00"]
        assert alerts.status(at(day, "16:02"))[0] is False
    for day in AFTER:
        assert [text(s) for s in alerts.spans(day)] == ["08:45-16:05"]
        assert alerts.status(at(day, "16:02")) == (True, "alert window")
        assert alerts.market_opens(at(day, "08:00")) == at(day, "09:00")


def test_the_backup_keeps_out_to_20_minutes_after_the_close(holidays):
    from sudarshan.ops.backup import block_spans, market_blocks

    calendar = TradingCalendar(holidays)
    for day in BEFORE:
        assert [text(s) for s in block_spans(calendar, day)] == ["08:30-16:00"]
        assert not market_blocks(calendar, at(day, "16:03"))
    for day in AFTER:
        assert [text(s) for s in block_spans(calendar, day)] == ["08:30-16:05"]
        assert market_blocks(calendar, at(day, "16:03"))
        assert not market_blocks(calendar, at(day, "16:05"))
    assert minutes("16:30") > block_spans(calendar, AFTER[-1])[0][1]  # the timer runs after it


# ---- a window the owner set keeps its own end (review L3) ------------------------------------------
@pytest.mark.parametrize(
    ("setting", "written", "after"),
    [
        ("08:30-09:15", "08:30-15:40", "08:30-09:15"),  # auto-login: no login after the open
        ("08:30-12:00", "08:30-15:40", "08:30-12:00"),
        ("08:30-15:40", "08:30-15:40", "08:30-15:45"),  # the usual end moves with the close
        ("08:58-15:50", "08:58-15:50", "08:58-15:55"),  # the feed's usual window
        ("08:58-16:10", "08:58-15:50", "08:58-16:10"),  # an end the owner chose
        ("09:00-23:55", "08:45-16:00", "09:00-23:55"),  # the alerts kept on all evening
        ("08:45-16:00", "08:45-16:00", "08:45-16:05"),  # the alerts' usual window
    ],
)
def test_a_window_the_owner_set_keeps_its_own_end_unless_it_is_the_usual_one(setting, written, after):
    for day in BEFORE:
        assert text(usual_on(day, parse_window(setting), parse_window(written))) == setting
    for day in AFTER:
        assert text(usual_on(day, parse_window(setting), parse_window(written))) == after


def test_the_feed_and_the_alerts_keep_an_end_the_owner_set(holidays):
    from sudarshan.alerts.window import AlertWindow
    from sudarshan.feed.window import MarketWindow

    feed = MarketWindow(holidays, window=parse_window("08:58-16:10"))
    alerts = AlertWindow(TradingCalendar(holidays), parse_window("09:00-23:55"), always_on=False)
    for day in (*BEFORE, *AFTER):
        assert [text(s) for s in feed.plan(day).spans] == ["08:58-16:10"]
        assert [text(s) for s in alerts.spans(day)] == ["09:00-23:55"]


def test_an_auto_login_window_ending_at_the_open_is_not_stretched_past_it(holidays, monkeypatch, caplog):
    """SUDARSHAN_AUTOLOGIN_WINDOW=08:30-09:15 means no scheduled login after the open: from 7 Dec
    it read 08:30-09:20. It stays 08:30-09:15."""
    monkeypatch.setattr(worker, "read_openalgo_env", lambda path: (_ for _ in ()).throw(
        ConfigError("not read in this test")))  # fmt: skip
    caplog.set_level(logging.INFO, logger="sudarshan.autologin")
    env = {"SUDARSHAN_NSE_HOLIDAYS_FILE": str(holidays), "SUDARSHAN_AUTOLOGIN_WINDOW": "08:30-09:15"}
    thursday = date(2026, 12, 10)
    assert worker.main([], env=env, clock=lambda: at(thursday, "09:16")) == 0
    assert "outside the auto-login window" in caplog.text
    caplog.clear()
    assert worker.main([], env=env, clock=lambda: at(thursday, "09:14")) == 2
    assert "outside the auto-login window" not in caplog.text
