"""An option window follows its index through the day, and a watchlist universe follows the list
(gap audit GA7.1, GA7.2).

Before, a window was placed once, on the first price its index showed (often the last close, heard
before the open), and stayed there whatever the index did; and a watchlist universe held what the
list held when the universes were built. Now a window placed before 09:16 is placed again at
09:16 on a price of the session, and again when the index comes near its outermost strike; and a
watchlist is read again before each run of a screen that looks at it. Nothing here reaches a
network: the feed, the master and the bridge's list route are fakes.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from screener_helpers import ms
from test_screener_feed_runtime import FakeFeed
from test_screener_runs import SPEC
from test_screener_runtime import Calendar
from test_screener_universe import TODAY
from test_screener_universe import master as master_fixture  # noqa: F401  (the fixture)

from sudarshan.screener import config, runner, spec, universe
from sudarshan.screener import store as store_module
from sudarshan.screener.runtime import LiveSource
from sudarshan.screener.store import OWNER, ScreenerStore

DAY = "2026-10-28"
NIFTY = "NSE_INDEX|Nifty 50"
WINDOW = {"kind": "options", "underlying": "NIFTY"}
LEVELS = [strike * 100 for strike in range(25000, 26550, 50)]  # the fixture's chain, in paise


def index_frame(at: int, ltp: float) -> bytes:
    return json.dumps({"key": NIFTY, "ltp": ltp, "ltt": at, "cp": 25750.0, "ts_recv": at}).encode()


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


def strikes_of(source) -> tuple[int, int]:
    """The window's lowest and highest strike, in rupees."""
    rows = [source._listings[m.key].strike // 100 for m in source.members(WINDOW)]
    return min(rows), max(rows)


def tick(source, clock, at: str, ltp: float | None = None) -> bool:
    """The feed job's own step: a frame if one is given, then whether a window is due, and the
    rebuild it would then make. Whether one was made."""
    clock["now"] = ms(DAY, at)
    if ltp is not None:
        source.on_frame(NIFTY, index_frame(clock["now"], ltp), clock["now"])
    if not source.window_due(clock["now"]):
        return False
    source.refresh_universes([WINDOW], clock["now"], reuse=True)
    return True


# ---- the pure rule -----------------------------------------------------------------------------------
def test_near_edge_is_the_money_within_three_strikes_of_a_side_that_has_more_to_follow():
    low, high = 2525000, 2625000  # the money at 25750, ten strikes each side
    assert universe.near_edge(LEVELS, low, high, 2575000) is False
    assert universe.near_edge(LEVELS, low, high, 2605000) is False  # four strikes from the top
    assert universe.near_edge(LEVELS, low, high, 2610000) is True  # three
    assert universe.near_edge(LEVELS, low, high, 2660000) is True  # past the window altogether
    assert universe.near_edge(LEVELS, low, high, 2540000) is True  # three from the bottom
    assert universe.near_edge(LEVELS, low, high, 2545000) is False


def test_a_window_at_the_end_of_the_chain_stays():
    # the top of the window is the chain's last strike: nothing further to follow
    assert universe.near_edge(LEVELS, 2575000, 2650000, 2645000) is False
    assert universe.near_edge(LEVELS, 2500000, 2600000, 2505000) is False
    assert universe.near_edge([], 1, 2, 1) is False


def test_a_narrow_window_moves_once_not_at_every_tick():
    # one strike each side: placed again only when the money is the outermost strike itself
    assert universe.near_edge(LEVELS, 2570000, 2580000, 2575000, strikes=1) is False
    assert universe.near_edge(LEVELS, 2570000, 2580000, 2580000, strikes=1) is True


def test_the_window_is_the_chain_helper_s(master_fixture):  # noqa: F811
    rows = master_fixture.rows("NFO", ["CE", "PE"])
    assert universe.chain_levels(rows, "NIFTY", TODAY) == LEVELS
    assert universe.chain_levels(rows, "RELIANCE", TODAY) == []


# ---- at 09:16 ----------------------------------------------------------------------------------------
def test_a_window_placed_before_the_open_is_placed_again_at_0916(world):
    _store, source, clock = world
    source.refresh_universes([WINDOW], clock["now"])
    assert source.members(WINDOW) == [] and source.window_due(clock["now"]) is False
    assert tick(source, clock, "09:05", 25750.0), "the first price places the window"
    assert strikes_of(source) == (25250, 26250)
    # the open gaps up 150 points: seven strikes from the top, so not near the edge
    assert tick(source, clock, "09:15:30", 25900.0) is False
    assert tick(source, clock, "09:15:59") is False
    assert tick(source, clock, "09:16:00"), "a window placed on the last close stayed there"
    assert strikes_of(source) == (25400, 26400)
    assert source.status()["reanchored"] == 1
    # the new strikes are followed, the index with them; the ones left behind are let go
    assert NIFTY in source.feed.topics
    assert {m.key for m in source.members(WINDOW)} <= source.feed.topics
    assert len(source.feed.topics) == 1 + 2 * 21
    assert tick(source, clock, "09:16:01") is False, "it is placed once, not at every tick"


def test_0916_waits_for_a_price_of_the_session(world):
    _store, source, clock = world
    source.refresh_universes([WINDOW], clock["now"])
    assert tick(source, clock, "09:05", 25750.0)
    assert tick(source, clock, "09:16:30") is False, "placed again on the pre-open price"
    assert tick(source, clock, "09:17", 25900.0), "the session's first price places it"
    assert strikes_of(source) == (25400, 26400)


def test_a_window_placed_after_0916_is_not_placed_again_by_the_clock(world):
    _store, source, clock = world
    clock["now"] = ms(DAY, "10:00")
    source.refresh_universes([WINDOW], clock["now"])
    assert tick(source, clock, "10:00:01", 25750.0)
    assert tick(source, clock, "10:00:02") is False and tick(source, clock, "11:00") is False


# ---- near the edge -----------------------------------------------------------------------------------
def test_a_window_follows_its_index_to_the_edge_and_stops_at_the_chain_s_end(world):
    _store, source, clock = world
    clock["now"] = ms(DAY, "10:00")
    source.refresh_universes([WINDOW], clock["now"])
    assert tick(source, clock, "10:00:01", 25750.0) and strikes_of(source) == (25250, 26250)
    assert tick(source, clock, "10:30", 26050.0) is False  # four strikes from the top
    assert tick(source, clock, "10:31", 26100.0), "the index reached the edge and nothing moved"
    assert strikes_of(source) == (25600, 26500)  # the chain ends at 26500
    assert tick(source, clock, "10:32", 26110.0) is False
    assert tick(source, clock, "10:40", 26480.0) is False, "no strikes beyond: the window stays"
    assert tick(source, clock, "11:30", 25700.0), "and it follows the index back down"
    assert strikes_of(source) == (25200, 26200)
    assert source.status()["reanchored"] == 2


def test_a_window_that_cannot_be_read_again_stays_as_it_was(world, monkeypatch):
    _store, source, clock = world
    clock["now"] = ms(DAY, "10:00")
    source.refresh_universes([WINDOW], clock["now"])
    assert tick(source, clock, "10:00:01", 25750.0)
    held = list(source.members(WINDOW))

    def down():
        raise universe.MasterUnavailable

    monkeypatch.setattr(source, "_master_whole", down)
    assert tick(source, clock, "10:31", 26100.0), "the rebuild is tried"
    assert list(source.members(WINDOW)) == held, "a master download emptied the window"
    assert {m.key for m in held} <= source.feed.topics
    assert tick(source, clock, "10:31:30") is False, "it is not tried again every second"
    monkeypatch.undo()
    assert tick(source, clock, "10:32:01") and strikes_of(source) == (25600, 26500)


def test_the_window_is_as_wide_as_the_setting_says(tmp_path, master_fixture):  # noqa: F811
    store = ScreenerStore(tmp_path / "screener.db")
    settings = config.Settings.from_env(
        {"SUDARSHAN_SCREENER": "on", "SUDARSHAN_SCREENER_OPTION_STRIKES": "2"}
    )
    clock = {"now": ms(DAY, "10:00")}
    source = LiveSource(
        store, FakeFeed(), master_fixture, settings, lambda: clock["now"], Calendar()
    )
    try:
        source.new_day(clock["now"])
        source.refresh_universes([WINDOW], clock["now"])
        assert tick(source, clock, "10:00:01", 25750.0)
        assert strikes_of(source) == (25650, 25850) and len(source.members(WINDOW)) == 10
        assert tick(source, clock, "10:05", 25800.0), "one strike from the top of a narrow window"
        assert strikes_of(source) == (25700, 25900)
        assert tick(source, clock, "10:05:01") is False
    finally:
        store.close()
        store_module.stop()


# ---- a watchlist, read again at each run ---------------------------------------------------------------
LIST = {"kind": "watchlist", "list_id": 3}


class Lists:
    """The bridge's list route, as the feed job's reader answers it."""

    def __init__(self, *symbols):
        self.symbols, self.reads, self.fail = list(symbols), 0, False

    async def __call__(self, specs):
        self.reads += 1
        if self.fail:
            return {}
        items = [{"id": i, "symbol": s, "exchange": "NSE"} for i, s in enumerate(self.symbols)]
        return {int(s["list_id"]): items for s in specs if s["kind"] == "watchlist"}


@pytest.fixture
def listed(world):
    store, source, clock = world
    clock["now"] = ms(DAY, "09:20")
    lists = Lists("SBIN", "INFY")
    source.lists_reader = lists
    screen = store.write(
        OWNER,
        lambda tx: tx.screen_insert("Mine", spec.check(dict(SPEC, universe=LIST)), "active", 1),
    )
    first = asyncio.run(lists([LIST]))
    lists.reads = 0
    source.refresh_universes([LIST], clock["now"], lists=first)
    return store, source, clock, lists, screen


def symbols(source) -> list[str]:
    return [m.symbol for m in source.members(LIST)]


def test_a_watchlist_universe_follows_the_list_at_the_next_run(listed):
    _store, source, clock, lists, _screen = listed
    assert symbols(source) == ["SBIN", "INFY"]
    lists.symbols = ["SBIN", "RELIANCE"]
    clock["now"] = ms(DAY, "09:30:05")
    assert asyncio.run(source.fresh_members(LIST, clock["now"])) is True
    assert symbols(source) == ["SBIN", "RELIANCE"], "the universe kept the morning's list"
    assert source.feed.topics == {m.key for m in source.members(LIST)}
    assert len(source.feed.claims[-1]) == 2, "the feed was not asked for the new instrument"
    assert source.status()["lists_reread"] == 1


def test_an_unchanged_list_changes_nothing_and_is_read_once_a_close(listed):
    _store, source, clock, lists, _screen = listed
    claims = len(source.feed.claims)
    clock["now"] = ms(DAY, "09:30:05")
    assert asyncio.run(source.fresh_members(LIST, clock["now"])) is False
    # a second screen on the same list at the same close shares the read
    assert asyncio.run(source.fresh_members(LIST, clock["now"] + 500)) is False
    assert lists.reads == 1 and len(source.feed.claims) == claims
    # the order of a list is not a change
    lists.symbols = ["INFY", "SBIN"]
    assert asyncio.run(source.fresh_members(LIST, clock["now"] + 60_000)) is False
    assert symbols(source) == ["SBIN", "INFY"]


def test_a_list_that_cannot_be_read_stays_as_it_was_last_read(listed):
    _store, source, clock, lists, _screen = listed
    lists.fail = True
    clock["now"] = ms(DAY, "09:30:05")
    assert asyncio.run(source.fresh_members(LIST, clock["now"])) is False
    assert symbols(source) == ["SBIN", "INFY"], "a failed read emptied the universe"


def test_a_list_not_read_at_the_build_is_loaded_at_the_next_run(world):
    store, source, clock = world
    clock["now"] = ms(DAY, "09:20")
    lists = Lists("SBIN")
    source.lists_reader = lists
    store.write(
        OWNER,
        lambda tx: tx.screen_insert("Mine", spec.check(dict(SPEC, universe=LIST)), "active", 1),
    )
    source.refresh_universes([LIST], clock["now"], lists={})  # the bridge did not answer
    assert source._uni_built(LIST) is False
    assert asyncio.run(source.fresh_members(LIST, ms(DAY, "09:30:05"))) is True
    assert symbols(source) == ["SBIN"]


def test_other_universes_are_left_alone(listed):
    _store, source, clock, lists, _screen = listed
    assert asyncio.run(source.fresh_members({"kind": "fno_stocks"}, clock["now"] + 9000)) is False
    assert lists.reads == 0


def test_the_run_loop_reads_the_list_before_it_judges(listed):
    store, source, clock, lists, screen = listed
    lists.symbols = ["SBIN", "INFY", "RELIANCE"]
    clock["now"] = ms(DAY, "09:30:06")
    made = runner.Runner(store, source, lambda: clock["now"], Calendar())
    assert asyncio.run(made.tick()) >= 1
    assert symbols(source) == ["SBIN", "INFY", "RELIANCE"], "the run judged the morning's list"
    run = store.read(OWNER, lambda tx: tx.latest_run(screen["id"]))
    # nothing has a price in this test, so every instrument reads not checked: all three of them
    assert run is not None and run["not_checked"] == 3
