"""What the run loop and the dashboard's read cost late in a day of 1-minute screens (release review
HIGH-4). The loop ticks every second on the service's one event loop, and every store call waits
for the store thread, so an idle tick must ask the store a fixed number of questions, never one
per boundary already past (the review measured 4,141 reads and 3.8 s at 15:00:30 with twelve
screens); ``today``, which the dashboard reads after every run push, must answer in a few
queries whatever the number of runs (it took 4.2 s, past the bridge's 3 s). The time bounds are
generous (this box and the ARM runner); the counts are the contract.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from screener_helpers import ms
from sqlalchemy import event
from test_screener_runs import FakeSource
from test_screener_runtime import Calendar

from sudarshan.screener import runner, spec
from sudarshan.screener import scheduler as sch
from sudarshan.screener import store as store_module
from sudarshan.screener.ops import REGISTRY
from sudarshan.screener.screen_ops import sched_screen
from sudarshan.screener.service import Service
from sudarshan.screener.store import OWNER, ScreenerStore
from sudarshan.screener.tests.test_screener_service import FERNET, settings_for

DAY = "2026-10-28"
SESSION = 20261028
SCREENS = 12  # SUDARSHAN_SCREENER_MAX_ACTIVE, the most that check during the day
ONE_MINUTE = {
    "schema": 1,
    "name": "One minute",
    "universe": {"kind": "fno_stocks"},
    "run": {"on": "close", "tf": 1},
    "where": {"id": "c1", "left": {"field": "close", "tf": 1}, "op": "gt", "right": {"num": "1"}},
}


class Counting:
    def __init__(self, store):
        self.store, self.reads = store, 0

    def read(self, who, fn):
        self.reads += 1
        return self.store.read(who, fn)

    def write(self, who, fn):
        return self.store.write(who, fn)


@pytest.fixture
def opened(tmp_path):
    made = ScreenerStore(tmp_path / "screener.db")
    yield made
    made.close()
    store_module.stop()


def test_an_idle_tick_late_in_the_day_asks_the_store_a_fixed_number_of_questions(opened):
    stored = spec.check(ONE_MINUTE)
    now = ms(DAY, "15:00:30")
    rows = [
        opened.write(OWNER, lambda tx, n=n: tx.screen_insert(f"S{n}", stored, "active", 1))
        for n in range(SCREENS)
    ]
    sessions = runner.Runner(opened, None, lambda: now, Calendar()).sessions(now)

    def record_all(tx):
        values = []
        for row in rows:
            for b in sch.boundaries(sched_screen(row), sessions):
                if b.at > now:
                    break
                values.append({
                    "principal": OWNER, "screen_id": row["id"], "screen_version": row["version"],
                    "run_key": str(sch.RunKey(row["id"], row["version"], SESSION, "1", b.k)),
                    "session": SESSION, "candle_start": b.start, "candle_end": b.end,
                    "ran_at": b.at, "status": "done", "engine_version": 1,
                })  # fmt: skip
        tx.conn.execute(tx._t("run").insert(), values)
        return len(values)

    recorded = opened.write(OWNER, record_all)
    assert recorded == SCREENS * 345
    counting = Counting(opened)
    made = runner.Runner(counting, FakeSource({}), lambda: now, Calendar())
    made.pruned_day = SESSION  # the retention already ran today
    began = time.perf_counter()
    done = asyncio.run(made.tick(now))
    took = time.perf_counter() - began
    assert done == 0
    assert counting.reads <= 2, (
        f"an idle tick made {counting.reads} store reads: every past boundary is looked up"
    )
    assert took < 1.5, f"an idle tick took {took:.2f} s of the loop"
    # the next second's tick asks only for the active screens
    counting.reads = 0
    assert asyncio.run(made.tick(now + 1000)) == 0 and counting.reads == 1


def test_today_answers_in_a_few_queries_whatever_the_number_of_runs(tmp_path):
    svc = Service(settings_for(1, SUDARSHAN_SCREENER_STATE_DIR=str(tmp_path)), FERNET, jobs={})
    svc.store = ScreenerStore(tmp_path / "screener.db")
    svc.clock = lambda: ms(DAY, "15:00:30")
    try:
        stored = spec.check(ONE_MINUTE)
        ids = [
            svc.store.write(OWNER, lambda tx, n=n: tx.screen_insert(f"S{n}", stored, "active", 0))[
                "id"
            ]
            for n in range(SCREENS)
        ]
        runs, changes = [], []
        for sid in ids:
            for k in range(345):
                end = ms(DAY, "09:16") + k * 60_000
                run_id = len(runs) + 1
                runs.append({
                    "id": run_id, "principal": OWNER, "screen_id": sid, "screen_version": 1,
                    "run_key": f"{sid}:v1:{SESSION}:1:{k}", "session": SESSION,
                    "candle_start": end - 60_000, "candle_end": end, "ran_at": end + 5000,
                    "status": "done", "engine_version": 1, "matched": k % 7,
                    "entered": 1 if k == 0 else 0, "exited": 0, "missing": 0, "not_checked": 40,
                })  # fmt: skip
                changes += [
                    {"run_id": run_id, "principal": OWNER, "key": f"NSE_EQ|K{i:03d}",
                     "symbol": f"S{i:03d}", "exchange": "NSE", "change": "not_checked", "rank": None}
                    for i in range(40)
                ]  # fmt: skip

        def fill(tx):
            tx.conn.execute(tx._t("run").insert(), runs)
            tx.conn.execute(tx._t("run_change").insert(), changes)

        svc.store.write(OWNER, fill)
        statements = []
        listen = svc.store.engine

        def count(conn, cursor, statement, *args):
            statements.append(statement)

        event.listen(listen, "before_cursor_execute", count)
        try:
            began = time.perf_counter()
            answer = asyncio.run(REGISTRY["today"](svc, {}))
            took = time.perf_counter() - began
        finally:
            event.remove(listen, "before_cursor_execute", count)
        assert answer["session"] == SESSION
        selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
        assert len(selects) <= 6, (
            f"today made {len(selects)} queries: it reads every change row of every run"
        )
        assert took < 1.5, f"today took {took:.2f} s (the bridge waits 3 s; the loop is blocked)"
        cards = {c["id"]: c for c in answer["screens"]}
        assert len(cards) == SCREENS and cards[ids[0]]["entered"] == 1
        assert cards[ids[0]]["matches"] == 344 % 7
    finally:
        svc.store.close()
        store_module.stop()


def test_today_lists_the_days_real_entries_and_exits(tmp_path):
    """The latest changes come from the membership (release review LOW-5): a member carried from
    yesterday is not new because the day's first run lists the whole match set, and a member
    only missing in its grace has not left."""
    from sudarshan.screener import membership as mem

    svc = Service(settings_for(1, SUDARSHAN_SCREENER_STATE_DIR=str(tmp_path)), FERNET, jobs={})
    svc.store = ScreenerStore(tmp_path / "screener.db")
    svc.clock = lambda: ms(DAY, "11:00")
    try:
        stored = spec.check(ONE_MINUTE)
        sid = svc.store.write(OWNER, lambda tx: tx.screen_insert("Breakout", stored, "active", 0))[
            "id"
        ]
        yesterday, at_10, at_1030 = ms("2026-10-27", "14:00"), ms(DAY, "10:00"), ms(DAY, "10:30")

        def fill(tx):
            run = {"principal": OWNER, "screen_id": sid, "screen_version": 1, "session": SESSION,
                   "status": "done", "engine_version": 1}  # fmt: skip
            first = tx.conn.execute(tx._t("run").insert().values(
                **run, run_key="a", candle_start=at_10 - 60_000, candle_end=at_10,
                ran_at=at_10, matched=2, entered=1)).inserted_primary_key[0]  # fmt: skip
            second = tx.conn.execute(tx._t("run").insert().values(
                **run, run_key="b", candle_start=at_1030 - 60_000, candle_end=at_1030,
                ran_at=at_1030, matched=1, exited=1, missing=1)).inserted_primary_key[0]  # fmt: skip
            tx.run_changes_put(first, [
                {"key": "A", "symbol": "AAA", "exchange": "NSE", "change": "in", "rank": 0},
                {"key": "B", "symbol": "BBB", "exchange": "NSE", "change": "in", "rank": 1},
            ])  # fmt: skip
            tx.run_changes_put(second, [
                {"key": "C", "symbol": "CCC", "exchange": "NSE", "change": "out", "rank": None},
                {"key": "D", "symbol": "DDD", "exchange": "NSE", "change": "out", "rank": None},
            ])  # fmt: skip
            for key, symbol, status, seen, gone in (
                ("A", "AAA", mem.ACTIVE, at_10, None),
                ("B", "BBB", mem.ACTIVE, yesterday, None),
                ("C", "CCC", mem.MISSING, yesterday, None),
                ("D", "DDD", mem.EXITED, yesterday, at_1030),
            ):
                base = mem.Row(key, symbol, "NSE", status, first_seen_at=seen, exited_at=gone)
                tx.membership_put(
                    sid, 7, {n: getattr(base, n) for n in mem.Row.__dataclass_fields__}, 1
                )

        svc.store.write(OWNER, fill)
        answer = asyncio.run(REGISTRY["today"](svc, {}))
        recent = [(r["kind"], r["symbol"]) for r in answer["recent"]]
        assert recent == [("exited", "DDD"), ("entered", "AAA")], recent
        assert answer["screens"] == [
            {"id": sid, "name": "Breakout", "entered": 1, "exited": 1, "matches": 1}
        ]
    finally:
        svc.store.close()
        store_module.stop()
