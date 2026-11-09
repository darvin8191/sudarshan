"""A restart that finds OpenAlgo's master not ready (M9.x, the screener): the claims a plain stop
kept at the feed stay until the universes can be built, and are dropped only once they have been
built and are empty. The first claim of a process used to drop them, so a crash restart during the
master download took the screener's instruments off the shared socket for up to five minutes."""

from __future__ import annotations

import asyncio

from screener_helpers import ms
from test_screener_feed_runtime import FakeFeed
from test_screener_runtime import Calendar
from test_screener_universe import master as master_fixture  # noqa: F401

from sudarshan.screener import config
from sudarshan.screener import store as store_module
from sudarshan.screener.runtime import LiveSource
from sudarshan.screener.store import ScreenerStore

DAY = "2026-10-28"


class Downloading:
    """OpenAlgo's master while its download runs: nothing can be built from it."""

    def __init__(self, real):
        self.real = real

    def __getattr__(self, name):
        return getattr(self.real, name)

    def download(self):
        return ("downloading", None)


def make(tmp_path, master):
    store = ScreenerStore(tmp_path / "screener.db")
    settings = config.Settings.from_env({"SUDARSHAN_SCREENER": "on"})
    clock = {"now": ms(DAY, "11:00")}
    feed = FakeFeed()
    source = LiveSource(store, feed, master, settings, lambda: clock["now"], Calendar())
    source.new_day(clock["now"])
    return store, source, feed, clock


def test_a_restart_that_finds_the_master_not_ready_keeps_the_claims_the_stop_kept(
    tmp_path,
    master_fixture,  # noqa: F811
):
    store, source, feed, clock = make(tmp_path, Downloading(master_fixture))
    try:
        source.refresh_universes([{"kind": "fno_stocks"}], clock["now"])  # full: the master fails
        asyncio.run(source.claim())
        assert not source.master_ready and not source.keys
        assert feed.dropped == 0, "the restart dropped the claims the stop kept"
    finally:
        store.close()
        store_module.stop()


def test_claims_are_dropped_once_the_universes_are_built_and_empty(tmp_path, master_fixture):  # noqa: F811
    store, source, feed, clock = make(tmp_path, master_fixture)
    try:
        source.refresh_universes([], clock["now"])  # no active screen: nothing to follow
        asyncio.run(source.claim())
        assert feed.dropped == 1
    finally:
        store.close()
        store_module.stop()


def test_a_screen_changed_while_the_master_is_still_downloading_keeps_the_claims(
    tmp_path,
    master_fixture,  # noqa: F811
):
    """After a crash restart during the master download the owner edits or pauses a screen: the
    rebuild that follows the change builds nothing, and must not count as the first build, or it
    drops the claims the stop kept."""
    store, source, feed, clock = make(tmp_path, Downloading(master_fixture))
    try:
        source.refresh_universes([{"kind": "fno_stocks"}], clock["now"])  # full: the master fails
        asyncio.run(source.claim())
        assert feed.dropped == 0
        clock["now"] += 60_000
        source.refresh_universes([{"kind": "fno_stocks"}], clock["now"], reuse=True)
        asyncio.run(source.claim())
        assert not source.master_ready and not source.keys
        assert feed.dropped == 0, "a rebuild that built nothing dropped the claims the stop kept"
    finally:
        store.close()
        store_module.stop()


def test_a_screen_changed_once_the_master_is_ready_still_lets_go_of_what_it_no_longer_needs(
    tmp_path,
    master_fixture,  # noqa: F811
):
    store, source, feed, clock = make(tmp_path, master_fixture)
    try:
        source.refresh_universes([{"kind": "fno_stocks"}], clock["now"])
        asyncio.run(source.claim())
        assert source.keys and feed.dropped == 0
        clock["now"] += 60_000
        source.refresh_universes([], clock["now"], reuse=True)  # the screen is paused
        asyncio.run(source.claim())
        assert feed.dropped == 1
    finally:
        store.close()
        store_module.stop()
