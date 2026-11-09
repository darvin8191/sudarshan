"""A key followed again the same day (M9.1, the screener, shipped OFF; re-review MEDIUM-1).

A key let go (its only screen paused, an edit that dropped it, a watchlist universe that could not
be read at a rebuild, the key limit) and followed again kept its running total, so the first bar
after the gap took every trade made meanwhile: 1,750,020 against 20, read as a volume spike and
kept in the 20-candle volume average. The first frame of a trade made after the key comes back is
now its baseline, and the stretch it was not followed is that key's own pause, so the candles that
hold it read "Not checked" (UNVERIFIED), over a restart too. A key followed again before the open,
or after a stretch no longer than a pause, needs neither. Real LiveSource, Bars, candles and
ScreenerStore, the universe tests' master; the feed is a fake.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest
from screener_helpers import ms
from test_screener_feed_runtime import FakeFeed, health
from test_screener_runtime import Calendar
from test_screener_universe import master as master_fixture  # noqa: F401  (the fixture)

from sudarshan.alerts.market import clock
from sudarshan.alerts.market.feedlink import HEALTH
from sudarshan.alerts.market.sessions import sessions_of
from sudarshan.screener import candles, config, runs, spec
from sudarshan.screener import store as store_module
from sudarshan.screener.bars import UNFOLLOWED, Bars
from sudarshan.screener.runtime import LiveSource
from sudarshan.screener.store import OWNER, ScreenerStore
from sudarshan.screener.types import DAY as DAY_TF
from sudarshan.screener.types import HIT, NOT_CHECKED, PAUSED, UNVERIFIED

DAY = "2026-10-28"
RELIANCE = "NSE_EQ|INE002A01018"
#: VWAP reclaim on F&O stocks, and another screen on the indices.
BOTH = [{"kind": "indices"}, {"kind": "fno_stocks"}]
#: The other screen alone: nobody follows RELIANCE.
OTHER = [{"kind": "indices"}]


@pytest.fixture
def world(tmp_path, master_fixture):  # noqa: F811
    store = ScreenerStore(tmp_path / "screener.db")
    settings = config.Settings.from_env({"SUDARSHAN_SCREENER": "on"})
    yield store, settings, master_fixture
    store.close()
    store_module.stop()


class _Downloading:
    """OpenAlgo's master while a download runs: its symbol table is being filled again."""

    def __init__(self, real):
        self.real = real

    def __getattr__(self, name):
        return getattr(self.real, name)

    def download(self):
        return ("downloading", None)


def start(world, at: str, *, master=None) -> LiveSource:
    """The service started (or started again) at ``at``: a fresh LiveSource on the same store."""
    store, settings, real = world
    now = ms(DAY, at)
    source = LiveSource(store, FakeFeed(), master or real, settings, lambda: now, Calendar())
    source.new_day(now)
    return source


def follow(source, specs, at: str, *, reuse: bool = True) -> None:
    """The active screens' universes after a change (the op's reuse rebuild), claimed."""
    source.refresh_universes(specs, ms(DAY, at), reuse=reuse)
    asyncio.run(source.claim())


def frame(source, at: int, ltp: float, vtt: int) -> list:
    """A trade of 10 at ``at`` (epoch ms), health just before it, as the feed job hands them."""
    payload = {"key": RELIANCE, "ltp": ltp, "ltt": at, "ltq": 10, "vtt": vtt, "cp": 90.0,
               "ts_recv": at}  # fmt: skip
    sealed = source.on_frame(HEALTH, health(at), at)
    return sealed + source.on_frame(RELIANCE, json.dumps(payload).encode(), at)


def heard(source, at: int, ltp: float, vtt: int) -> list:
    """A trade the real feed link hands over only while it follows RELIANCE (``feed.follow``)."""
    if RELIANCE not in source.feed.topics:
        return source.on_frame(HEALTH, health(at), at)
    return frame(source, at, ltp, vtt)


def seal(source, at: int, sealed: list) -> list:
    """The clock at ``at`` (epoch ms) and the bars written to the store, as the feed job does."""
    sealed = sealed + source.on_frame(HEALTH, health(at), at) + source.on_clock(at)
    source.persist(sealed)
    return sealed


def minute(source, at: str, ltp: float, vtts) -> list:
    """Trades of 10 at :20 and :40 of the minute ``at``, then the clock past its seal."""
    begin, sealed = ms(DAY, at), []
    for n, vtt in enumerate(vtts):
        sealed += frame(source, begin + 20_000 * (n + 1), ltp, vtt)
    return seal(source, begin + 66_000, sealed)


def bar_at(source, at: str):
    return next(bar for bar in source.bars.bars(RELIANCE) if bar.start == ms(DAY, at))


def unverified(source, tf, boundary: str) -> bool:
    """Whether the last candle of ``tf`` read at ``boundary`` is UNVERIFIED (judged "Not
    checked"), as the engine reads it."""
    series = candles.read(source.bars, RELIANCE, tf, source.market, ms(DAY, boundary))
    return bool(series.flags[-1] & UNVERIFIED)


def preopen(source, vtt: int = 50_000) -> None:
    frame(source, ms(DAY, "09:07:30"), 100.0, vtt)  # the pre-open match: no bar's volume


def morning(source) -> None:
    """VWAP reclaim made before the open, the pre-open volume, and the 09:15 bar of 20."""
    follow(source, BOTH, "09:00", reuse=False)
    preopen(source)
    minute(source, "09:15", 100.0, [50_010, 50_020])
    assert bar_at(source, "09:15").v == 20


def pause_rows(world, source) -> list[dict]:
    return world[0].read(OWNER, lambda tx: tx.pauses(source.market.today))


def gap_rows(world, source) -> list[tuple]:
    """RELIANCE's own pauses (its gaps): (start, end)."""
    rows = pause_rows(world, source)
    return [(row["start"], row["end"]) for row in rows if row.get("key") == RELIANCE]


# ---- MEDIUM-1: the gap's volume is in no bar, and the gap is not checked ----------------------------
@pytest.mark.parametrize(
    "gap", [False, True], ids=["control-followed-throughout", "paused-10-to-11"]
)
def test_a_key_followed_again_starts_its_volume_from_then_and_its_gap_reads_not_checked(world, gap):
    source = start(world, "08:50")
    morning(source)
    traded = 50_020
    if gap:
        follow(source, OTHER, "10:00")  # the owner pauses the screen; no other screen has RELIANCE
        assert RELIANCE not in source.keys
        follow(source, BOTH, "11:00")  # he resumes it; RELIANCE traded 1,750,000 meanwhile
        assert RELIANCE in source.keys
        traded = 1_800_020
    minute(source, "11:00", 101.0, [traded + 10, traded + 20])
    assert bar_at(source, "11:00").v == 20, "the first bar after the resume holds the paused hour"
    # the 11:00 to 11:05 candle and the day hold the hour nobody watched: never judged
    assert unverified(source, 5, "11:01:06") is gap
    assert unverified(source, DAY_TF, "11:01:06") is gap


def test_a_screen_paused_before_the_open_and_resumed_at_ten_has_a_baseline(world):
    """The release review's own words ("makes or resumes at 10:00") for a screen resumed in the
    pre-open and paused before 09:15: its one pre-open frame put the key in the bars."""
    source = start(world, "08:50")
    follow(source, BOTH, "09:00", reuse=False)
    preopen(source)
    follow(source, OTHER, "09:10")  # paused
    follow(source, BOTH, "10:00")  # resumed at 10:00
    minute(source, "10:00", 101.0, [1_800_010, 1_800_020])
    assert bar_at(source, "10:00").v == 20, "the first bar holds the morning's volume"
    assert unverified(source, DAY_TF, "10:01:06"), "09:15 to 10:00 was not watched"


def test_a_key_followed_again_before_the_open_needs_no_pause(world):
    """Nothing trades between the pre-open match and the open, so the pre-open frame still gives
    the baseline, though no frame comes after the key is followed again."""
    source = start(world, "08:50")
    follow(source, BOTH, "09:00", reuse=False)
    preopen(source)
    follow(source, OTHER, "09:08")
    follow(source, BOTH, "09:12")
    minute(source, "09:15", 100.0, [50_010, 50_020])
    assert bar_at(source, "09:15").v == 20, "the pre-open volume belongs to no bar"
    assert not unverified(source, 5, "09:16:06")
    assert not unverified(source, DAY_TF, "09:16:06")
    assert pause_rows(world, source) == []


def test_a_gap_no_longer_than_a_pause_keeps_the_running_total(world):
    """Let go and followed again within 10 s (two quick changes): the trade made meanwhile lands
    in the next bar, as for a frame that was lost, and nothing is marked."""
    source = start(world, "08:50")
    morning(source)
    follow(source, OTHER, "10:00")
    follow(source, BOTH, "10:00:08")  # RELIANCE traded 10 at 10:00:04, seen by nobody
    seal(source, ms(DAY, "10:01:06"), frame(source, ms(DAY, "10:00:20"), 101.0, 50_040))
    assert bar_at(source, "10:00").v == 20
    assert not unverified(source, 5, "10:01:06")
    assert pause_rows(world, source) == []


def test_the_minute_held_open_when_the_key_was_let_go_keeps_its_own_volume(world):
    """Let go at 10:00:25 with the 10:00 minute open and followed again at 10:00:45: that minute
    holds what came before the let-go, sealed when the key comes back (nothing more of it can
    come); a trade made after the comeback lands in the next bar, and both are not checked."""
    source = start(world, "08:50")
    morning(source)
    frame(source, ms(DAY, "10:00:20"), 101.0, 50_030)
    follow(source, OTHER, "10:00:25")
    follow(source, BOTH, "10:00:45")  # RELIANCE traded 10 at 10:00:30, seen by nobody
    assert bar_at(source, "10:00").v == 10
    sealed = frame(source, ms(DAY, "10:00:50"), 101.5, 50_050)
    sealed += frame(source, ms(DAY, "10:01:10"), 102.0, 50_060)
    seal(source, ms(DAY, "10:02:06"), sealed)
    assert bar_at(source, "10:00").v == 10
    assert bar_at(source, "10:01").v == 20, "the 10:00:50 trade and the 10:01:10 trade"
    assert unverified(source, 1, "10:01:06") and unverified(source, 1, "10:02:06")


# ---- a restart ----------------------------------------------------------------------------------------
def test_the_gap_still_reads_not_checked_after_a_restart(world):
    source = start(world, "08:50")
    morning(source)
    follow(source, OTHER, "10:00")
    follow(source, BOTH, "11:00")
    minute(source, "11:00", 101.0, [1_800_030, 1_800_040])
    source.mark_seen(ms(DAY, "11:01:06"))  # it stops at once: its own pause is too short to mark
    again = start(world, "11:01:10")
    follow(again, BOTH, "11:01:10", reuse=False)  # a start rebuilds every universe
    minute(again, "11:02", 102.0, [1_800_050, 1_800_060])
    assert bar_at(again, "11:00").v == 20 and bar_at(again, "11:02").v == 20
    assert unverified(again, 5, "11:03:06") and unverified(again, DAY_TF, "11:03:06")
    assert gap_rows(world, again) == [(ms(DAY, "10:00"), ms(DAY, "11:00"))]


@pytest.mark.parametrize("op", [False, True], ids=["no-op-during-download", "op-during-download"])
def test_a_restart_while_the_master_downloads_keeps_the_stretch_out_of_the_first_bar(world, op):
    """Re-review MEDIUM-A1. Restarted at 10:00:02 (a fast restart: the seen mark is 2 s old, so
    no service pause) while OpenAlgo fills its symbol table again: the start's rebuild finds the
    master not ready and follows nothing, so the feed link drops RELIANCE's frames until the
    5-minute retry builds the universes at 10:05. RELIANCE traded 500,000 meanwhile. An op during
    the download (a rebuild that builds nothing) let the keys go and kept the gap already; with no
    op, the first set after the restore took the restored keys as followed all along, and the
    10:05 bar held 500,020, judged as verified."""
    source = start(world, "08:50")
    morning(source)
    minute(source, "09:59", 100.0, [100_010, 100_020])
    source.mark_seen(ms(DAY, "10:00:00"))
    again = start(world, "10:00:02", master=_Downloading(world[2]))
    follow(again, BOTH, "10:00:02", reuse=False)  # the start: the master is not ready
    assert again.feed.topics == frozenset() and not again.master_ready
    if op:
        follow(again, BOTH, "10:01")  # the owner edits a screen during the download
    heard(again, ms(DAY, "10:02:30"), 100.5, 300_000)  # dropped: the link follows nothing
    again.master = world[2]  # the download has finished
    follow(again, BOTH, "10:05", reuse=False)  # the 5-minute retry
    assert RELIANCE in again.feed.topics
    minute(again, "10:05", 101.0, [600_030, 600_040])
    assert bar_at(again, "10:05").v == 20, "the first bar after the build holds the stretch"
    assert unverified(again, 5, "10:06:06"), "the 10:05 candle holds minutes nobody watched"
    assert gap_rows(world, again) == [(ms(DAY, "10:00:02"), ms(DAY, "10:05"))]


def test_a_normal_restart_marks_nothing(world):
    """The guard: a start that follows its keys at once (the restore and the first set at the
    same moment) keeps the running total and records no gap."""
    source = start(world, "08:50")
    morning(source)
    source.mark_seen(ms(DAY, "10:00:00"))
    again = start(world, "10:00:02")
    follow(again, BOTH, "10:00:02", reuse=False)
    minute(again, "10:00", 101.0, [50_030, 50_040])
    assert bar_at(again, "10:00").v == 20, "what traded since the last bar, as for a lost frame"
    assert not unverified(again, 5, "10:01:06")
    assert gap_rows(world, again) == []


def test_a_key_the_restart_did_not_follow_is_taken_as_let_go_from_the_restart(world):
    """After a restart its screen is paused (or its watchlist could not be read): the bars of
    the morning are restored, and the key followed again at 10:00 starts from then."""
    source = start(world, "08:50")
    morning(source)
    source.mark_seen(ms(DAY, "09:16:06"))
    again = start(world, "09:16:08")
    follow(again, OTHER, "09:16:08", reuse=False)
    follow(again, BOTH, "10:00")
    minute(again, "10:00", 101.0, [1_800_010, 1_800_020])
    assert bar_at(again, "10:00").v == 20
    assert unverified(again, 5, "10:01:06") and unverified(again, DAY_TF, "10:01:06")


# ---- a key first followed after the open (design A1) -----------------------------------------------
def judge(source, line: dict, boundary: str):
    """RELIANCE judged at ``boundary`` by a 5-min screen on F&O stocks, as a run does: the line,
    and a 5-min line that always holds (so the instrument is due at every 5-min close it trades
    in, whatever the line reads)."""
    traded = {"id": "c0", "on": True, "left": {"field": "close", "tf": 5}, "op": "gt",
              "right": {"num": "0"}}  # fmt: skip
    _, plan = spec.build({"name": "A1", "universe": {"kind": "fno_stocks"},
                          "run": {"on": "close", "tf": 5},
                          "where": {"all": [traded, {"id": "c1", "on": True, **line}]}})  # fmt: skip
    member = next(m for m in source.members({"kind": "fno_stocks"}) if m.key == RELIANCE)
    ctx = source.context(ms(DAY, boundary), source.market.today)
    (found,) = asyncio.run(runs.evaluate(plan, [member], source, ctx))
    return found.outcome


DAY_OPEN = {"left": {"field": "day_open"}, "op": "gt", "right": {"num": "1"}}
FIRST_CANDLE = {"left": {"field": "close", "tf": 5, "at": "first_today"}, "op": "gt",
                "right": {"num": "1"}}  # fmt: skip
LATEST_CANDLE = {"left": {"field": "close", "tf": 5}, "op": "gt", "right": {"num": "1"}}


def paused(outcome) -> bool:
    """Not checked because a candle it read holds a stretch nobody watched, for no other reason."""
    return outcome.kind == NOT_CHECKED and {why.code for why in outcome.why} == {PAUSED}


@pytest.mark.parametrize("claimed", ["09:00", "11:00"], ids=["control-before-open", "at-11"])
def test_a_key_first_followed_after_the_open_reads_its_day_and_first_candle_not_checked(
    world, claimed
):
    """The first screen on F&O stocks is made at 11:00: RELIANCE has bars from 11:00 only, so its
    "day open" would be its 11:00 price and its "first candle" the 11:00 one, judged as verified.
    09:15 to 11:00 is now its gap: the day and the first candle read "Not checked", over a restart
    too, while the candles after its first bar are judged as usual."""
    source = start(world, "08:50")
    follow(source, OTHER, "09:00", reuse=False)  # another screen's keys since the morning
    follow(source, BOTH, claimed)
    traded = 50_020
    if claimed == "09:00":
        preopen(source)
        minute(source, "09:15", 100.0, [50_010, 50_020])
    else:
        assert gap_rows(world, source) == [(ms(DAY, "09:15"), ms(DAY, "11:00"))]
        traded = 1_800_000
    minute(source, "11:00", 101.0, [traded + 10, traded + 20])
    minute(source, "11:06", 102.0, [traded + 30, traded + 40])
    late = claimed == "11:00"
    assert paused(judge(source, DAY_OPEN, "11:10")) is late
    assert paused(judge(source, FIRST_CANDLE, "11:10")) is late
    assert judge(source, LATEST_CANDLE, "11:10").kind == HIT, "the 11:05 candle is whole"
    if not late:
        assert judge(source, DAY_OPEN, "11:10").kind == HIT
        assert judge(source, FIRST_CANDLE, "11:10").kind == HIT
        assert gap_rows(world, source) == []
    source.mark_seen(ms(DAY, "11:10:30"))
    again = start(world, "11:10:32")  # a fast restart: the keyed row brings the gap back
    follow(again, BOTH, "11:10:32", reuse=False)
    assert paused(judge(again, DAY_OPEN, "11:10")) is late
    assert judge(again, LATEST_CANDLE, "11:10").kind == HIT


def test_a_key_the_store_holds_no_bar_of_is_first_followed_at_the_restart(world):
    """The restart clause: nothing says whether a key with no bar in the store was followed
    before the restart, so the first set after it counts as its first follow."""
    source = start(world, "08:50")
    follow(source, OTHER, "09:00", reuse=False)
    source.mark_seen(ms(DAY, "10:00:00"))
    again = start(world, "10:00:02")
    follow(again, BOTH, "10:00:02", reuse=False)
    assert gap_rows(world, again) == [(ms(DAY, "09:15"), ms(DAY, "10:00:02"))]
    minute(again, "10:01", 101.0, [700_010, 700_020])
    minute(again, "10:06", 101.0, [700_030, 700_040])
    assert bar_at(again, "10:01").v == 20, "the first frame after the follow is the baseline"
    assert paused(judge(again, DAY_OPEN, "10:10"))
    assert judge(again, LATEST_CANDLE, "10:10").kind == HIT


# ---- the closing auction --------------------------------------------------------------------------
def test_the_closing_auction_print_after_a_comeback_adds_only_its_own_volume():
    """A stock that closes by auction on a day it trades to the cash end (from 7 Dec 2026), let
    go at 15:29:45 with its last minute open and followed again at 15:31: the print still adds
    only the auction's volume to that sealed minute, never the day's (the running total gave way
    to the baseline). Pure Bars."""
    day, today = "2026-12-08", 20261208
    sessions = sessions_of(date(2026, 12, 8), trading_day=True)

    def world(at: str) -> clock.Market:
        feed = clock.Feed("connected", True, ms(day, at))
        return clock.Market(today, sessions, 20261207, {}, feed, (), False, frozenset({RELIANCE}))

    def tick(bars: Bars, at: str, ltt: str, ltp: float, vtt: int, ltq: int = 10):
        payload = {"key": RELIANCE, "ltt": ms(day, ltt), "ltp": ltp, "ltq": ltq, "vtt": vtt,
                   "ts_recv": ms(day, at)}  # fmt: skip
        return bars.on_tick(payload, ms(day, at), world(at))

    bars = Bars()
    bars.follow_from([RELIANCE], ms(day, "09:00"), world("09:00"))
    assert tick(bars, "15:29:10", "15:29:10", 100.0, 1_000_000).check == clock.OK
    bars.follow_from([], ms(day, "15:29:45"), world("15:29:45"))
    back = bars.follow_from([RELIANCE], ms(day, "15:31"), world("15:31"))
    assert [(s.bar.start, s.bar.v) for s in back.sealed] == [(ms(day, "15:29"), 1_000_000)]
    gap = clock.Pause(ms(day, "15:29:45"), ms(day, "15:31"), UNFOLLOWED)
    assert back.gaps == ((RELIANCE, gap),) and bars.gaps(RELIANCE) == (gap,)
    tick(bars, "15:31:30", "15:29:58", 100.5, 1_000_500)  # the auction's book; 500 traded unseen
    printed = tick(bars, "15:36:00", "15:35:30", 101.0, 1_050_500, ltq=50_000)
    assert printed.check == clock.AUCTION_PRINT
    (last,) = [bar for bar in bars.bars(RELIANCE) if bar.start == ms(day, "15:29")]
    assert (last.c, last.v) == (10100, 1_050_000), "the print adds the auction's 50,000"
