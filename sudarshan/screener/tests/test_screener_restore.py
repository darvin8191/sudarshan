"""What the runtime keeps of today's bars across a restart and a new day (release review BLOCKER-1).

bar_today holds one trading day. A start or a midnight restores only that day's sealed bars and
clears every earlier day from the store and from memory, so the day candle, ``first_today`` and
every day operand read today alone, and memory stays flat however many days the service runs.
Real LiveSource, real ScreenerStore, real Bars and candles, the universe tests' master.
"""

from __future__ import annotations

import json
import time
import tracemalloc

import pytest
from screener_helpers import ms
from test_screener_feed_runtime import FakeFeed, health
from test_screener_runtime import Calendar
from test_screener_universe import master as master_fixture  # noqa: F401  (the fixture)

from sudarshan.alerts.market.feedlink import HEALTH
from sudarshan.screener import candles, config
from sudarshan.screener import store as store_module
from sudarshan.screener.runtime import LiveSource
from sudarshan.screener.store import OWNER, ScreenerStore
from sudarshan.screener.types import DAY as DAY_TF

DAY1, DAY2 = "2026-10-28", "2026-10-29"
RELIANCE = "NSE_EQ|INE002A01018"


def trade(key: str, at: int, ltp: float, vtt: int, recv: int | None = None, ltq: int = 1) -> bytes:
    return json.dumps(
        {"key": key, "ltp": ltp, "ltt": at, "ltq": ltq, "vtt": vtt, "cp": 90.0,
         "ts_recv": at if recv is None else recv}
    ).encode()  # fmt: skip


def feed_minutes(source, day: str, start: str, minutes: int, price0: float, vtt0: int, step: int):
    """Trades at :05 and :35 of every minute from ``start``, health before each; every minute
    sealed after its grace and written to the store, as the feed job does."""
    vtt = vtt0
    sealed = []
    begin = ms(day, start)
    for minute in range(minutes):
        for second in (5, 35):
            at = begin + minute * 60_000 + second * 1000
            sealed += source.on_frame(HEALTH, health(at - 500), at - 500)
            vtt += step
            sealed += source.on_frame(RELIANCE, trade(RELIANCE, at, price0 + minute * 0.5, vtt), at)
        tick = begin + minute * 60_000 + 66_000
        sealed += source.on_frame(HEALTH, health(tick - 500), tick - 500)
        sealed += source.on_clock(tick)
    source.persist(sealed)
    return vtt, sealed


@pytest.fixture
def world(tmp_path, master_fixture):  # noqa: F811
    store = ScreenerStore(tmp_path / "screener.db")
    settings = config.Settings.from_env({"SUDARSHAN_SCREENER": "on"})
    yield store, settings, master_fixture, tmp_path
    store.close()
    store_module.stop()


def make(store, settings, master, now: int) -> LiveSource:
    clock = {"now": now}
    source = LiveSource(store, FakeFeed(), master, settings, lambda: clock["now"], Calendar())
    source.new_day(now)
    source.refresh_universes([{"kind": "fno_stocks"}], now)
    return source


# ---- BLOCKER-1: a new day starts with only its own bars ---------------------------------------------
def test_the_next_day_starts_with_only_its_own_bars(world):
    store, settings, master, _ = world
    day1 = make(store, settings, master, ms(DAY1, "09:00"))
    feed_minutes(day1, DAY1, "09:15", 16, 100.0, 1_000, 10)  # 09:15..09:30, prices 100..107.5
    assert store.read(OWNER, lambda tx: len(tx.bars())) == 16

    # midnight: the same process rolls to day 2 (a restart overnight does the same)
    day1.new_day(ms(DAY2, "00:00:05"))
    assert day1.bars.bars(RELIANCE) == (), "yesterday's bars are held as today's"
    assert store.read(OWNER, lambda tx: len(tx.bars())) == 0, "yesterday's bars stay in bar_today"

    # day 2: pre-open volume 50,000 (last trade 09:07:30), then trades from 09:15 at 200..207.5
    pre = ms(DAY2, "09:07:30")
    day1.on_frame(HEALTH, health(pre), pre)
    day1.on_frame(RELIANCE, trade(RELIANCE, pre, 200.0, 50_000, recv=pre + 500), pre + 500)
    feed_minutes(day1, DAY2, "09:15", 16, 200.0, 50_000, 10)
    bars = day1.bars.bars(RELIANCE)
    first_today = next(b for b in bars if b.start == ms(DAY2, "09:15"))
    day = candles.read(day1.bars, RELIANCE, DAY_TF, day1.market, ms(DAY2, "09:31")).candle(0)
    assert len(bars) == 16
    assert (day.o, day.h, day.low, day.v) == (20000, 20750, 20000, 320), (
        "the day candle starts with yesterday's bars"
    )
    assert first_today.v == 20, "the 09:15 bar counts the pre-open volume"

    # a restart later on day 2 reads day 2's bars back, and no others (with each bar's last trade)
    again = make(store, settings, master, ms(DAY2, "11:00"))
    assert again.bars.bars(RELIANCE) == bars


# ---- BLOCKER-1: memory and bar_today stay flat across days ------------------------------------------
KEYS, BARS = 40, 375
DAYS = ("2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29")


def fill(store: ScreenerStore, day: str) -> None:
    """One trading day of sealed bars for KEYS keys, as the feed job writes them."""
    start = ms(day, "09:15")
    rows = [
        {"key": f"NSE_EQ|K{k:03d}", "start": start + m * 60_000, "o": 10000, "h": 10010,
         "l": 9990, "c": 10005, "v": 1234, "oi": None, "atp": 10001, "src": "ticks", "flags": 0}
        for k in range(KEYS) for m in range(BARS)
    ]  # fmt: skip
    store.write(OWNER, lambda tx: tx.conn.execute(tx._t("bar_today").insert(), rows))


def held(source: LiveSource) -> int:
    return sum(len(source.bars.bars(f"NSE_EQ|K{k:03d}")) for k in range(KEYS))


def test_each_day_holds_only_its_own_bars_in_the_store_and_in_memory(world):
    store, settings, master, _ = world
    peaks = []
    for day in DAYS:
        fill(store, day)
        # a restart in the afternoon of that day (systemd, a deploy, a crash)
        source = LiveSource(store, FakeFeed(), master, settings, lambda: 0, Calendar())
        tracemalloc.start()
        began = time.perf_counter()
        source.new_day(ms(day, "13:00"))
        took = time.perf_counter() - began
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        peaks.append(peak)
        assert held(source) == KEYS * BARS, f"{day}: earlier days' bars are restored as today's"
        stored = store.read(OWNER, lambda tx: len(tx.bars()))
        assert stored == KEYS * BARS, f"{day}: bar_today keeps every earlier day"
        assert took < 20, f"{day}: the restore blocked the loop for {took:.1f} s"
    assert max(peaks) < 1.5 * min(peaks), f"the restore's memory grows with the days: {peaks}"

    # midnight after the last day: nothing of it is held, in memory or in the store
    source.new_day(ms("2026-10-30", "00:00:05"))
    assert held(source) == 0
    assert store.read(OWNER, lambda tx: len(tx.bars())) == 0


# ---- HIGH-1: a restart keeps the running volume -------------------------------------------------------
def test_a_restart_does_not_put_the_days_volume_into_its_first_bar(world):
    store, settings, master, _ = world
    first = make(store, settings, master, ms(DAY1, "09:00"))
    vtt, _ = feed_minutes(first, DAY1, "09:15", 45, 100.0, 0, 20_000)  # 09:15..09:59, 1.8 M
    # sudarshan-screener restarts at 10:02 (systemd Restart=on-failure, or a deploy)
    again = make(store, settings, master, ms(DAY1, "10:02"))
    at = ms(DAY1, "10:02:10")
    again.on_frame(HEALTH, health(at - 500), at - 500)
    again.on_frame(RELIANCE, trade(RELIANCE, at, 123.0, vtt + 100, ltq=100), at)
    again.on_frame(HEALTH, health(ms(DAY1, "10:03:05")), ms(DAY1, "10:03:05"))
    sealed = again.on_clock(ms(DAY1, "10:03:06"))
    bar = next(s.bar for s in sealed if s.bar.start == ms(DAY1, "10:02"))
    assert vtt == 1_800_000
    assert bar.v == 100, "the first bar after a restart carries the whole day's volume"
    assert next(s.cum for s in sealed if s.bar.start == ms(DAY1, "10:02")) == vtt + 100


def test_a_key_first_seen_after_a_restart_counts_only_what_it_traded_after(world):
    """SBIN had pre-open volume and no trade before the restart, so the store holds no bar of it:
    its first trade after the restart is the baseline, not the day's whole running total."""
    store, settings, master, _ = world
    first = make(store, settings, master, ms(DAY1, "09:00"))
    feed_minutes(first, DAY1, "09:15", 5, 100.0, 0, 10)  # RELIANCE only
    sbin = "NSE_EQ|INE062A01020"
    again = make(store, settings, master, ms(DAY1, "10:02"))
    at = ms(DAY1, "10:02:10")
    again.on_frame(HEALTH, health(at - 500), at - 500)
    again.on_frame(sbin, trade(sbin, at, 812.0, 50_000 + 70, ltq=70), at)
    later = at + 20_000
    again.on_frame(HEALTH, health(later - 500), later - 500)
    again.on_frame(sbin, trade(sbin, later, 812.5, 50_000 + 70 + 30, ltq=30), later)
    again.on_frame(HEALTH, health(ms(DAY1, "10:03:05")), ms(DAY1, "10:03:05"))
    sealed = again.on_clock(ms(DAY1, "10:03:06"))
    bar = next(s.bar for s in sealed if s.key == sbin)
    assert bar.v == 100, "a key's first bar after a restart counts its pre-open volume"
