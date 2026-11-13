"""The journal's job times and the once-a-day rule (JB5): vectors S1-S6, mutation targets 19 and
20, and the cash post-close end sessions.py now gives (cross-design Y22)."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from pathlib import Path

import pytest

from sudarshan.alerts.market import sessions
from sudarshan.autologin.schedule import TradingCalendar
from sudarshan.journal import schedule as schedule_module
from sudarshan.journal import store
from sudarshan.journal.config import Settings
from sudarshan.journal.core.money import IST, to_date
from sudarshan.journal.schedule import (
    JOB_NAMES,
    JobFailed,
    JournalSchedule,
    once_a_day,
)
from sudarshan.journal.store import JournalStore, Scope

OWNER = Scope.owner()
VECTORS = json.loads((Path(__file__).parent / "vectors" / "schedule.json").read_text())["vectors"]
TIMES = [v for v in VECTORS if "due" in v]
RUNS = [v for v in VECTORS if "job" in v]


def clock_ms(day: int, clock: str) -> int:
    h, m = (int(x) for x in clock.split(":"))
    d = to_date(day)
    return int(datetime(d.year, d.month, d.day, h, m, tzinfo=IST).timestamp() * 1000)


def clock_of(ms: int | None) -> str | None:
    return None if ms is None else datetime.fromtimestamp(ms / 1000, IST).strftime("%H:%M")


def calendar(tmp_path: Path, holidays=(), extra=()) -> TradingCalendar:
    hol, ext = tmp_path / "holidays.txt", tmp_path / "extra.txt"
    hol.write_text("".join(f"{d}  # synthetic\n" for d in holidays))
    ext.write_text("".join(f"{line}\n" for line in extra))
    return TradingCalendar(hol, ext)


@pytest.mark.parametrize("vector", TIMES, ids=[v["id"] for v in TIMES])
def test_job_times(tmp_path, vector):
    plan = JournalSchedule(calendar(tmp_path, vector["holidays"], vector["extra_days"]),
                           Settings(enabled=True))  # fmt: skip
    day = to_date(vector["day"])
    got = {name: (lambda d: d and clock_of(d.at))(plan.due(name, day)) for name in JOB_NAMES}
    assert got == vector["due"], vector["note"]


def test_the_brief_follows_the_sessions_start_not_a_clock(tmp_path):
    """Mutation target 19: the lead is taken from the first session's start (a setting of 45
    minutes gives 08:30 on a normal day and 13:35 on the Muhurat day)."""
    cal = calendar(tmp_path, extra=["2026-11-08 14:05-15:40"])
    plan = JournalSchedule(cal, Settings(enabled=True, brief_before_min=45))
    assert clock_of(plan.brief_at(date(2026, 11, 10))) == "08:30"
    assert clock_of(plan.brief_at(date(2026, 11, 8))) == "13:35"


def test_the_post_close_end_is_the_cash_close_and_thirty_minutes(tmp_path):
    for day, window in ((date(2026, 11, 10), None), (date(2026, 12, 8), None),
                        (date(2026, 11, 8), (845, 940))):  # fmt: skip
        end = sessions.post_close_end(day, trading_day=True, window=window)
        assert clock_of(end) == "16:00"
    assert sessions.post_close_end(date(2026, 11, 10), trading_day=False) is None


def test_the_last_three_trading_days_skip_holidays_and_weekends(tmp_path):
    plan = JournalSchedule(calendar(tmp_path, holidays=["2026-11-06"]), Settings(enabled=True))
    days = plan.previous_trading_days(date(2026, 11, 10), 3)
    assert days == [date(2026, 11, 9), date(2026, 11, 5), date(2026, 11, 4)]


# ---- the once-a-day rule -----------------------------------------------------------------------
class Box:
    """Just what once_a_day reads of the service: the store, the settings, a schedule part."""

    def __init__(self, tmp_path: Path):
        self.settings = Settings(enabled=True, state_dir=tmp_path / "state")
        self.store = JournalStore(tmp_path / "journal.db")
        self.parts = {"schedule": JournalSchedule(calendar(tmp_path), self.settings)}

    def part(self, name, make):
        return self.parts.setdefault(name, make(self))


@pytest.fixture
def box(tmp_path):
    made = Box(tmp_path)
    yield made
    made.store.close()
    store.stop()


@pytest.mark.parametrize("vector", RUNS, ids=[v["id"] for v in RUNS])
def test_once_a_day(box, vector):
    day = vector["day"]
    for row in vector["ran"]:
        box.store.write(OWNER, lambda tx, row=row: tx.job_put(
            row["name"], day, row["status"], "earlier", clock_ms(day, row["at"])))  # fmt: skip
    runs = []

    async def run(service, today, now, late):
        runs.append((today, late))
        return "done"

    status = asyncio.run(once_a_day(box, vector["job"], clock_ms(day, vector["now"]), run))
    assert status == vector["status"], vector["note"]
    assert len(runs) == (status is not None)
    if status is not None:
        row = box.store.read(OWNER, lambda tx: tx.job_get(vector["job"], day))
        assert (row["status"], row["detail"]) == (status, "done")


def test_a_run_that_cannot_do_its_work_is_failed_in_words_and_asked_again(box):
    day, calls = 20261110, []

    async def run(service, today, now, late):
        calls.append(now)
        if len(calls) == 1:
            raise JobFailed("The day's trades could not be read for the close check: down.")
        if len(calls) == 2:
            raise RuntimeError("a fault")
        return "fine"

    first = asyncio.run(once_a_day(box, "check", clock_ms(day, "16:05"), run))
    row = box.store.read(OWNER, lambda tx: tx.job_get("check", day))
    assert first == "failed" and row["detail"].startswith("The day's trades could not be read")
    assert asyncio.run(once_a_day(box, "check", clock_ms(day, "16:10"), run)) is None
    assert asyncio.run(once_a_day(box, "check", clock_ms(day, "16:20"), run)) == "failed"
    row = box.store.read(OWNER, lambda tx: tx.job_get("check", day))
    assert row["detail"] == "The job stopped on a fault; it is tried again later."
    assert asyncio.run(once_a_day(box, "check", clock_ms(day, "16:35"), run)) == "late"
    assert asyncio.run(once_a_day(box, "check", clock_ms(day, "23:59"), run)) is None
    assert len(calls) == 3


def test_nothing_runs_after_midnight_for_the_day_before(box):
    async def run(service, today, now, late):
        raise AssertionError("must not run")

    # 00:30 on 11 Nov: the 10 Nov day report was missed; the new day's is not due yet
    assert asyncio.run(once_a_day(box, "eod", clock_ms(20261111, "00:30"), run)) is None


def test_no_store_no_run():
    class Off:
        store = None

    assert asyncio.run(once_a_day(Off(), "eod", 0, None)) is None


def test_every_job_name_is_known_to_the_schedule(tmp_path):
    plan = JournalSchedule(calendar(tmp_path), Settings(enabled=True))
    with pytest.raises(ValueError):
        plan.due("tea", date(2026, 11, 10))
    assert set(schedule_module.TRADING_JOBS) | set(schedule_module.CALENDAR_JOBS) == set(JOB_NAMES)
