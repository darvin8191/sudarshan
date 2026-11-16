"""The week against NIFTY 50 in the week in review (M15.3, GA10.5): the close of the week's last
trading day over the close of the trading day before the week. The closes are the portfolio's;
a day it lacks is taken from the price feed's ``cp`` when the tick names that day, and the index
is otherwise left out, never measured to another day.

Fixed dates and a synthetic holiday file: Wednesday 11 Nov, Monday 16 Nov and Friday 20 Nov 2026
are closed. Every value is synthetic and nothing leaves 127.0.0.1.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from journal_fakes import FakeFeed
from test_journal_analytics import book as book  # noqa: F401  (a fixture)
from test_journal_capture import files as files  # noqa: F401  (a fixture)
from test_journal_capture import ist_ms
from test_journal_capture import rig as rig  # noqa: F401  (a fixture)
from test_journal_reports import call

from sudarshan.autologin.schedule import TradingCalendar
from sudarshan.journal import periods
from sudarshan.journal.schedule import JournalSchedule
from sudarshan.journal.store import Scope

OWNER = Scope.owner()
HOLIDAYS = "2026-11-11\n2026-11-16\n2026-11-20\n"
W46 = periods.week_span(periods.to_date(20261109), 20261115)  # Mon 9 - Sun 15 Nov
W47 = periods.week_span(periods.to_date(20261116), 20261122)  # Mon 16 - Sun 22 Nov


@pytest.fixture
def plan(tmp_path):
    path = tmp_path / "holidays.txt"
    path.write_text(HOLIDAYS)
    return JournalSchedule(TradingCalendar(path), SimpleNamespace(eod_after_min=15))


def tick(day: int, clock: str, cp: float, ltp: float = 26_000.0) -> dict:
    return {"key": periods.NIFTY50_KEY, "ltp": ltp, "ltt": ist_ms(day, clock), "cp": cp}


# ---- which two days --------------------------------------------------------------------------
def test_a_holiday_inside_the_week_leaves_friday_its_last_day(plan):
    assert periods.week_close_days(plan, W46, ist_ms(20261115, "18:00:00")) == (20261113, 20261106)


def test_a_week_whose_friday_is_a_holiday_ends_on_thursday(plan):
    # Monday 16 Nov is closed too, so the day before the week is still Friday 13 Nov.
    assert periods.week_close_days(plan, W47, ist_ms(20261122, "18:00:00")) == (20261119, 20261113)


def test_a_running_week_ends_on_the_last_day_whose_session_has_closed(plan):
    running = periods.week_span(periods.to_date(20261117), 20261118)  # asked on Wednesday
    assert periods.week_close_days(plan, running, ist_ms(20261118, "11:00:00")) == (
        20261117,
        20261113,
    )
    assert periods.week_close_days(plan, running, ist_ms(20261118, "16:00:00"))[0] == 20261118
    first_day = periods.week_span(periods.to_date(20261117), 20261117)
    assert periods.week_close_days(plan, first_day, ist_ms(20261117, "11:00:00")) is None


# ---- what a tick's cp names --------------------------------------------------------------------
def test_cp_names_the_trading_day_before_the_ticks_own_skipping_holidays(plan):
    # Tuesday 17 Nov's tick: Monday 16 is closed, so its cp is Friday 13 Nov's close.
    assert periods.cp_close(plan, tick(20261117, "10:00:00", 25_250.5)) == (20261113, 252_505_000)
    # Monday 23 Nov's tick: Friday 20 is closed, so its cp is Thursday 19 Nov's close.
    assert periods.cp_close(plan, tick(20261123, "09:16:00", 25_400.0))[0] == 20261119
    # Thursday 19 Nov's own last tick, as the Sunday after it reads it: Wednesday's close.
    assert periods.cp_close(plan, tick(20261119, "15:34:00", 25_300.0))[0] == 20261118


@pytest.mark.parametrize(
    "bad",
    [None, "tick", {}, {"cp": 25_000.0}, {"ltt": ist_ms(20261117, "10:00:00")},
     {"ltt": True, "cp": 25_000.0}, {"ltt": ist_ms(20261117, "10:00:00"), "cp": 0},
     {"ltt": ist_ms(20261117, "10:00:00"), "cp": "25000"}],
)  # fmt: skip
def test_a_tick_without_a_time_or_a_price_names_nothing(plan, bad):
    assert periods.cp_close(plan, bad) is None


# ---- the week in review, end to end ------------------------------------------------------------
@pytest.fixture
def feed(rig, tmp_path):
    made = FakeFeed()
    url = made.start()
    holidays = tmp_path / "holidays.txt"
    holidays.write_text(HOLIDAYS)
    settings = rig.svc.settings
    rig.svc.settings = type(settings)(
        **{**settings.__dict__, "feed_url": url, "holidays_file": holidays}
    )
    rig.svc.parts.pop("schedule", None)
    yield made
    made.stop()


def closes(*pairs: tuple[int, int]) -> dict:
    return {
        "state": "ok",
        "index": "nifty50",
        "closes": [{"day": day, "close_u": close} for day, close in pairs],
    }


def built_text(rig, period: str) -> str:
    call(rig, "report_build", kind="weekly", period=period)
    rows = rig.svc.store.read(OWNER, lambda tx: tx.rows("report", kind="weekly", order="id"))
    return rows[-1]["text"]


def snapshot_reads(feed) -> list:
    return [c for c in feed.calls if c[1] == "/snapshot"]


def test_the_portfolios_closes_ask_nothing_of_the_feed(rig, book, feed):  # noqa: F811
    rig.now = ist_ms(20261122, "18:00:00")
    rig.portfolio.answers["benchmark_closes"] = closes((20261113, 250_000_000),
                                                       (20261119, 252_500_000))  # fmt: skip
    assert "index +1.00%" in built_text(rig, "2026W47")
    assert snapshot_reads(feed) == []


def test_friday_a_holiday_the_sunday_run_cannot_take_thursday_from_the_feed(rig, book, feed):  # noqa: F811
    """The Sunday after: the feed's last tick is Thursday's, its cp Wednesday's close. Nothing
    measures the week, so the index is left out, not measured to Wednesday."""
    rig.now = ist_ms(20261122, "18:00:00")
    rig.portfolio.answers["benchmark_closes"] = closes((20261113, 250_000_000),
                                                       (20261118, 251_000_000))  # fmt: skip
    feed.ticks[periods.NIFTY50_KEY] = tick(20261119, "15:34:00", 25_100.0)
    text = built_text(rig, "2026W47")
    assert "index" not in text.lower().replace("set your trading capital", "")
    assert len(snapshot_reads(feed)) == 1


def test_friday_a_holiday_a_rebuild_on_monday_takes_thursday_from_the_feeds_cp(rig, book, feed):  # noqa: F811
    rig.now = ist_ms(20261123, "10:00:00")
    rig.portfolio.answers["benchmark_closes"] = closes((20261113, 250_000_000))
    feed.ticks[periods.NIFTY50_KEY] = tick(20261123, "09:59:58", 25_250.0, ltp=25_610.0)
    assert "index +1.00%" in built_text(rig, "2026W47")  # 25,250 over Friday 13 Nov's 25,000
    (read,) = snapshot_reads(feed)
    assert read[2]["keys"] == periods.NIFTY50_KEY


def test_a_holiday_week_takes_fridays_close_from_a_tick_after_the_monday_holiday(rig, book, feed):  # noqa: F811
    """W46 has Wednesday 11 Nov closed, and Monday 16 Nov after it is closed too: Tuesday's tick
    names Friday 13 Nov, the week's last trading day."""
    rig.now = ist_ms(20261117, "11:00:00")
    rig.portfolio.answers["benchmark_closes"] = closes((20261106, 250_000_000))
    feed.ticks[periods.NIFTY50_KEY] = tick(20261117, "10:59:59", 24_750.0)
    assert "index −1.00%" in built_text(rig, "2026W46")


def test_a_feed_that_does_not_answer_leaves_the_index_out(rig, book, feed):  # noqa: F811
    rig.now = ist_ms(20261123, "10:00:00")
    rig.portfolio.down = True
    feed.down = True
    text = built_text(rig, "2026W47")
    assert "index" not in text.lower().replace("set your trading capital", "")
