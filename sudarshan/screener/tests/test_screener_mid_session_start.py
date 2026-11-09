"""A screen made, resumed or edited during the session (the screener, shipped OFF; round-3 review
MEDIUM-B6, owner default): one run on the latest candle closed by then, then its schedule.

A new version has a new run key for every boundary of the day, and nothing bounded how far back
the scheduler looked, so a 3-minute screen made at 14:00:10 ran all 95 of the day's checks at once
on the shared box: list changes and alerts for candles that closed before the screen existed. The
run loop and the replay check now start a version that began during the day from the latest
boundary closed by then (the screen's ``updated_at``, which every change moves). Real store and
run loop; the replay check over a written recording.
"""

from __future__ import annotations

import asyncio
from datetime import date

from screener_helpers import ms
from test_screener_runtime import Calendar

from sudarshan.screener import check, runner, spec
from sudarshan.screener import store as store_module
from sudarshan.screener.bars import Bars
from sudarshan.screener.runs import Member
from sudarshan.screener.store import OWNER, ScreenerStore

DAY = "2026-10-28"
STOCK = "NSE_EQ|AAA"


def screen_of(name: str, tf: int) -> dict:
    return spec.check({
        "name": name, "universe": {"kind": "watchlist", "list_id": 1},
        "run": {"on": "close", "tf": tf},
        "where": {"all": [{"id": "c1", "on": True, "left": {"field": "close", "tf": tf},
                           "op": "gt", "right": {"num": "100"}}]},
        "target": None,
    })  # fmt: skip


def test_the_run_loop_runs_a_screen_made_at_two_once_not_the_whole_day(tmp_path):
    """Made at 14:00:10 through the store, as the op does: the loop's first tick makes one run
    (the 13:57 to 14:00 candle), not 95."""
    store = ScreenerStore(tmp_path / "screener.db")
    try:
        stored = screen_of("Three-minute", 3)
        made_at = ms(DAY, "14:00:10")
        store.write(OWNER, lambda tx: tx.screen_insert(stored["name"], stored, "active", made_at))
        market = check.market_for(date(2026, 10, 28), Calendar(), frozenset(), made_at)
        source = check.ReplaySource(Bars(), market, {})
        loop = runner.Runner(store, source, lambda: made_at + 1_000, Calendar())
        assert asyncio.run(loop.tick(made_at + 1_000)) == 1, "every past check of the day ran"
        runs = store.read(OWNER, lambda tx: tx.runs_session(20261028))
        assert [(run["candle_end"], run["marks"]) for run in runs] == [
            (ms(DAY, "14:00"), {"not_loaded": True})
        ], "the latest closed candle, on time: not marked late"
    finally:
        store.close()
        store_module.stop()


def ticks_from_the_open() -> list[tuple[str, int, dict]]:
    out, vtt = [], 0
    for minute in range(0, 61):
        for second in (5, 35):
            at = ms(DAY, "09:15") + minute * 60_000 + second * 1000
            vtt += 10
            frame = {"key": STOCK, "ltp": 95.0 + minute * 0.2, "ltt": at, "ltq": 1, "vtt": vtt,
                     "cp": 90.0, "ts_recv": at}  # fmt: skip
            out.append((STOCK, at, frame))
    return out


def replayed(updated_at: int) -> list[tuple[str, bool]]:
    stored = screen_of("Fifteen-minute", 15)
    rows = [{"id": 1, "version": 3, "name": stored["name"], "spec": stored, "status": "active",
             "updated_at": updated_at}]  # fmt: skip
    members = {"watchlist:1": [Member(STOCK, "AAA", "NSE", "NSE_EQ", False)]}
    lines = asyncio.run(
        check.replay_day(ticks_from_the_open(), rows, members, Calendar(), day=date(2026, 10, 28))
    )
    return [(check.words.hm(line.at), line.late) for line in lines]


def test_the_replay_check_starts_a_version_where_the_service_started_it():
    """Edited at 10:00:10 on the replayed day: the replay runs the 10:00 candle then 10:15, as
    the service did, so --compare finds no run the service never made."""
    assert replayed(ms(DAY, "10:00:10")) == [("10:00", False), ("10:15", False)]


def test_a_version_from_another_day_is_replayed_over_the_whole_day():
    assert replayed(ms("2026-10-27", "16:30")) == [
        ("09:30", False), ("09:45", False), ("10:00", False), ("10:15", False),
    ]  # fmt: skip
