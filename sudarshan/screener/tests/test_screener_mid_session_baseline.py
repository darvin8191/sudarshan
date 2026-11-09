"""A key first followed during the session (M9.x, the screener, shipped OFF): the volume it traded
before it was followed belongs to no bar. A screen made, resumed or edited at 10:00, a master
download or an option window claims keys in a process that started before the open; the bars
held nothing of those keys, so the first bar took the day's whole running volume and kept it in the
20-candle volume average for 100 minutes. The day candle's volume is the exchange's running total
(MEDIUM-B5), which is the day's own figure, not a spike. Real LiveSource, Bars and candles."""

from __future__ import annotations

import json

import pytest
from screener_helpers import ms
from test_screener_feed_runtime import FakeFeed, health
from test_screener_runtime import Calendar
from test_screener_universe import master as master_fixture  # noqa: F401

from sudarshan.alerts.market.feedlink import HEALTH
from sudarshan.screener import candles, config
from sudarshan.screener import store as store_module
from sudarshan.screener.runtime import LiveSource
from sudarshan.screener.store import ScreenerStore
from sudarshan.screener.types import DAY as DAY_TF

DAY = "2026-10-28"
RELIANCE = "NSE_EQ|INE002A01018"
NIFTY = "NSE_INDEX|Nifty 50"


@pytest.fixture
def live(tmp_path, master_fixture):  # noqa: F811
    store = ScreenerStore(tmp_path / "screener.db")
    settings = config.Settings.from_env({"SUDARSHAN_SCREENER": "on"})
    clock = {"now": ms(DAY, "08:50")}
    source = LiveSource(
        store, FakeFeed(), master_fixture, settings, lambda: clock["now"], Calendar()
    )
    source.new_day(clock["now"])  # started before the open, as on any normal morning
    yield store, source, clock
    store.close()
    store_module.stop()


def frame(source, key, at, ltp, vtt, ltq=10):
    payload = {"key": key, "ltp": ltp, "ltt": at, "ltq": ltq, "vtt": vtt, "cp": 90.0, "ts_recv": at}
    return source.on_frame(key, json.dumps(payload).encode(), at)


@pytest.mark.parametrize(
    "claimed", ["09:00", "10:00"], ids=["control-before-open", "during-session"]
)
def test_a_key_first_followed_during_the_session_has_a_baseline(live, claimed):
    store, source, clock = live
    source.refresh_universes([{"kind": "indices"}], ms(DAY, "09:00"))  # another screen's keys
    at_claim = ms(DAY, claimed)
    # the owner makes a screen on F&O stocks at `claimed`: the fix round builds and claims it now
    source.refresh_universes([{"kind": "indices"}, {"kind": "fno_stocks"}], at_claim, reuse=True)
    assert RELIANCE in source.keys
    sealed = []
    day_volume = 1_800_000  # RELIANCE has traded this much today before its first frame reaches us
    if claimed == "09:00":  # followed before the open: the pre-open frame gives the baseline
        at = ms(DAY, "09:07:30")
        sealed += source.on_frame(HEALTH, health(at), at)
        sealed += frame(source, RELIANCE, at, 100.0, 50_000)
        day_volume = 50_000
    first = ms(DAY, "10:00:20") if claimed == "10:00" else ms(DAY, "09:15:20")
    start = first - 20_000
    for n, (dt, price) in enumerate([(0, 100.0), (20_000, 101.0)]):
        at = first + dt
        sealed += source.on_frame(HEALTH, health(at), at)
        sealed += frame(source, RELIANCE, at, price, day_volume + 10 * (n + 1))
    at = start + 66_000
    sealed += source.on_frame(HEALTH, health(at), at)
    sealed += source.on_clock(at)
    bar = next(s.bar for s in sealed if s.key == RELIANCE and s.bar.start == start)
    daily = candles.read(source.bars, RELIANCE, DAY_TF, source.market, at)
    assert bar.v <= 20, "the first bar of a key followed during the session holds the day's volume"
    assert int(daily.v[-1]) == day_volume + 20, "the day's volume is the exchange's running total"
