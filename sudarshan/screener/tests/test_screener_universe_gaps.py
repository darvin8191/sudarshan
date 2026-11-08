"""Universes the runtime could not build, and what a run does then (release review HIGH-3).

"Not checked" is not "missed": a run on an empty universe, or on one that was not built (the
master contract being downloaded, a screen made after the day's build), records every member it
holds as not checked, changes no membership and queues no list change, so a data gap can never
empty the owner's watchlist (design 3.4). A master that OpenAlgo is still downloading, or whose
symbol table is empty, is not ready: the universes wait and are built again later.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from screener_helpers import ms
from sqlalchemy import create_engine, text
from test_screener_feed_runtime import FakeFeed, health
from test_screener_runs import SESSION, SPEC, due_for
from test_screener_runtime import Calendar
from test_screener_universe import master as master_fixture  # noqa: F401  (the fixture)

from sudarshan.alerts.market.feedlink import HEALTH
from sudarshan.screener import config, runner, runs, spec, words
from sudarshan.screener import membership as mem
from sudarshan.screener import store as store_module
from sudarshan.screener.runtime import LiveSource
from sudarshan.screener.store import OWNER, ScreenerStore

DAY = "2026-10-28"
NOT_LOADED = "Not checked: the instruments of this screen are not loaded yet"


@pytest.fixture
def world(tmp_path, master_fixture):  # noqa: F811
    store = ScreenerStore(tmp_path / "screener.db")
    settings = config.Settings.from_env({"SUDARSHAN_SCREENER": "on"})
    clock = {"now": ms(DAY, "09:00")}
    source = LiveSource(
        store, FakeFeed(), master_fixture, settings, lambda: clock["now"], Calendar()
    )
    source.new_day(clock["now"])
    yield store, source, clock
    store.close()
    store_module.stop()


def held_row(store, screen_id: int, list_id: int, key: str, symbol: str) -> None:
    base = mem.Row(key, symbol, "NSE", "active", in_list=True, first_seen_at=1)
    store.write(
        OWNER,
        lambda tx: tx.membership_put(
            screen_id, list_id, {n: getattr(base, n) for n in mem.Row.__dataclass_fields__}, 1
        ),
    )


def test_an_empty_universe_does_not_empty_the_list(world):
    store, _source, _clock = world
    screen = store.write(OWNER, lambda tx: tx.screen_insert("S", spec.check(SPEC), "active", 1))
    held_row(store, screen["id"], 7, "NSE_EQ|A", "AAA")
    # two runs while the universe could not be built (the source answers no members)
    for k in (3, 4):
        due = due_for(screen, k)
        report = store.write(
            OWNER,
            lambda tx, due=due: runs.record(
                tx, tx.screen(screen["id"]), tx.state(screen["id"]), due, [], due.boundary.at
            ),
        )
        assert report.status == "done" and report.exited == 0 and report.missing == 0
    row = store.read(OWNER, lambda tx: tx.memberships(screen_id=screen["id"], list_id=7))[0]
    queued = store.read(OWNER, lambda tx: [r["kind"] for r in tx.outbox_due(10**15, 50)])
    assert SESSION and row["status"] == "active" and row["misses"] == 0, (
        "a data gap took the instrument out of the list"
    )
    assert "list_sync" not in queued, "a data gap queued a change to the owner's list"
    run = store.read(OWNER, lambda tx: tx.runs_page(screen["id"], None, 1))[0]
    assert run["not_checked"] == 1 and run["matched"] == 0
    assert NOT_LOADED.lower() in [m.lower() for m in words.mark_words(run["marks"])]
    changes = store.read(OWNER, lambda tx: tx.run_changes(run["id"]))
    assert [(c["key"], c["change"]) for c in changes] == [("NSE_EQ|A", "not_checked")]
    values = store.read(OWNER, lambda tx: tx.run_values(run["id"]))
    assert values["NSE_EQ|A"]["_not_checked"] == NOT_LOADED


def test_a_screen_whose_universe_is_not_built_keeps_its_members(world):
    store, source, clock = world
    source.refresh_universes([{"kind": "fno_stocks"}], clock["now"])  # the day's build
    futures = spec.check(dict(SPEC, name="Index futures", universe={"kind": "index_futures"}))
    screen = store.write(OWNER, lambda tx: tx.screen_insert("F", futures, "active", 1))
    held_row(store, screen["id"], 7, "NSE_FO|5", "NIFTY30OCT26FUT")
    clock["now"] = ms(DAY, "09:30:05")
    made = runner.Runner(store, source, lambda: clock["now"], Calendar())
    assert asyncio.run(made.tick()) == 1
    clock["now"] = ms(DAY, "09:45:05")
    assert asyncio.run(made.tick()) == 1
    row = store.read(OWNER, lambda tx: tx.memberships(screen_id=screen["id"], list_id=7))[0]
    assert row["status"] == "active", "an unbuilt universe took the member out of the list"
    queued = store.read(OWNER, lambda tx: [r["kind"] for r in tx.outbox_due(10**15, 50)])
    assert "list_sync" not in queued
    for run in store.read(OWNER, lambda tx: tx.runs_page(screen["id"], None, 5)):
        assert run["status"] == "done" and run["not_checked"] == 1 and run["exited"] == 0


def _status_table(master, status: str | None) -> None:
    engine = create_engine(f"sqlite:///{master.db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE IF NOT EXISTS master_contract_status (broker TEXT PRIMARY KEY, "
                "status TEXT, message TEXT, last_updated DATETIME, total_symbols TEXT, "
                "is_ready BOOLEAN, last_download_time DATETIME, download_date DATE, "
                "exchange_stats TEXT, download_duration_seconds INTEGER)"
            )
        )
        conn.execute(text("DELETE FROM master_contract_status"))
        if status is not None:
            conn.execute(
                text(
                    "INSERT INTO master_contract_status (broker, status, last_updated, "
                    "last_download_time) VALUES ('upstox', :s, '2026-10-28 08:40:00', "
                    "'2026-10-28 08:40:00')"
                ),
                {"s": status},
            )
    engine.dispose()


def test_a_master_being_downloaded_or_empty_is_not_ready_and_the_universes_wait(world):
    store, source, clock = world
    _status_table(source.master, "downloading")
    source.refresh_universes([{"kind": "fno_stocks"}], clock["now"])
    assert source.master_ready is False, "universes were built from a master still downloading"
    assert source.members({"kind": "fno_stocks"}) == []

    _status_table(source.master, "success")
    source.refresh_universes([{"kind": "fno_stocks"}], clock["now"])
    assert source.master_ready is True and len(source.members({"kind": "fno_stocks"})) == 2

    # OpenAlgo deletes the symbol table before it fills it again: an empty table is not ready,
    # and the universes already built stay as they are
    engine = create_engine(f"sqlite:///{source.master.db_path}")
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM symtoken"))
    engine.dispose()
    source.refresh_universes([{"kind": "fno_stocks"}], clock["now"])
    assert source.master_ready is False, "an empty symbol table was read as today's master"
    assert len(source.members({"kind": "fno_stocks"})) == 2


# ---- release review MEDIUM-7: universes the live source never built --------------------------------
def index_frame(source, key: str, ltp: float, at: int) -> None:
    source.on_frame(HEALTH, health(at), at)
    source.on_frame(key, json.dumps({"key": key, "ltp": ltp, "ltt": at, "cp": ltp}).encode(), at)


def test_option_windows_find_their_own_index_price(world):
    store, source, clock = world
    source.refresh_universes([{"kind": "indices"}], clock["now"])
    at = ms(DAY, "09:15:01")
    index_frame(source, "NSE_INDEX|Nifty Bank", 52_000.0, at)
    index_frame(source, "NSE_INDEX|Nifty 50", 25_760.0, at)
    nifty, bank = source._index_frame("NIFTY"), source._index_frame("BANKNIFTY")
    assert bank is not None, "BANKNIFTY's options never find the Nifty Bank index price"
    assert nifty["ltp"] == 25_760.0, "NIFTY's options read the Nifty Bank price"
    assert bank["ltp"] == 52_000.0


def test_an_option_window_waits_for_its_index_price_and_is_built_when_it_comes(world):
    store, source, clock = world
    options = {"kind": "options", "underlying": "NIFTY"}
    source.refresh_universes([options], clock["now"])
    assert source.members(options) == [] and "NSE_INDEX|Nifty 50" in source.keys, (
        "the index an option window needs is not followed"
    )
    assert source.window_due() is False
    index_frame(source, "NSE_INDEX|Nifty 50", 25_760.0, ms(DAY, "09:15:01"))
    assert source.window_due() is True
    source.refresh_universes([options], clock["now"], reuse=True)
    strikes = {m.symbol for m in source.members(options)}
    assert "NIFTY30OCT2625750CE" in strikes and "NIFTY30OCT2625750PE" in strikes
    assert source.window_due() is False
