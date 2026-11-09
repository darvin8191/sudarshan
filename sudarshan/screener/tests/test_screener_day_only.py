"""A screen whose lines read only day candles, checked at minute closes (the screener, shipped
OFF; round-3 review HIGH-B1).

The sources built only the timeframes a screen's lines read, and the engine decides whether an
instrument is due from the run's own candle (one that ends at the boundary), so such a screen was
never due: every run recorded 0 matches and 0 not checked all day. "Forming day candle at each
15-minute close", which the editor's default run gives any screen of day lines, never matched.
The live source and the replay check now build the run's own timeframe too. Real LiveSource (bars
from ticks), runs.evaluate and the engine; the replay check over a written recording.
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

from sudarshan.alerts.market.feedlink import HEALTH
from sudarshan.screener import check, config, runs, spec
from sudarshan.screener import store as store_module
from sudarshan.screener.runs import Member
from sudarshan.screener.runtime import LiveSource
from sudarshan.screener.store import ScreenerStore
from sudarshan.screener.types import HIT

DAY = "2026-10-28"
RELIANCE = "NSE_EQ|INE002A01018"
#: Opened at its high, more than 5 lakh shares today, a day low above 100: day lines only.
OPENED_AT_HIGH = {
    "name": "Opened at the high", "universe": {"kind": "fno_stocks"},
    "run": {"on": "close", "tf": 15},
    "where": {"all": [
        {"id": "c1", "on": True, "left": {"field": "day_open"}, "op": "eq",
         "right": {"field": "day_high"}},
        {"id": "c2", "on": True, "left": {"field": "volume", "tf": "D"}, "op": "gt",
         "right": {"num": "500000"}},
        {"id": "c3", "on": True, "left": {"field": "day_low"}, "op": "gt", "right": {"num": "100"}},
    ]},
}  # fmt: skip


@pytest.fixture
def source(tmp_path, master_fixture):  # noqa: F811
    store = ScreenerStore(tmp_path / "screener.db")
    settings = config.Settings.from_env({"SUDARSHAN_SCREENER": "on"})
    now = ms(DAY, "08:50")
    found = LiveSource(store, FakeFeed(), master_fixture, settings, lambda: now, Calendar())
    found.new_day(now)
    found.refresh_universes([{"kind": "fno_stocks"}], ms(DAY, "09:00"), reuse=False)
    asyncio.run(found.claim())
    yield found
    store.close()
    store_module.stop()


def trades_from_the_open(source) -> None:
    """RELIANCE opens at its high, 250.00, at 09:15 and falls 5 paise every 20 s to 09:30; the
    exchange's day candle comes with every frame, as in full mode."""
    vtt, price, sealed = 400_000, 250.0, []
    day_start = ms(DAY, "00:00")
    for n in range(0, 15 * 60, 20):
        at = ms(DAY, "09:15") + n * 1000
        vtt += 20_000
        ohlc = {"open": 250.0, "high": 250.0, "low": price, "close": price, "vol": vtt,
                "ts": day_start}  # fmt: skip
        payload = {"key": RELIANCE, "ltp": price, "ltt": at, "ltq": 20_000, "vtt": vtt,
                   "cp": 245.0, "ts_recv": at, "ohlc": ohlc}  # fmt: skip
        sealed += source.on_frame(HEALTH, health(at), at)
        sealed += source.on_frame(RELIANCE, json.dumps(payload).encode(), at)
        price = round(price - 0.05, 2)
    at = ms(DAY, "09:30:06")
    sealed += source.on_frame(HEALTH, health(at), at) + source.on_clock(at)
    source.persist(sealed)


@pytest.mark.parametrize("variant", ["day-lines-only", "control-with-a-15-min-line"])
def test_a_screen_of_day_lines_is_judged_at_each_15_minute_close(source, variant):
    raw = json.loads(json.dumps(OPENED_AT_HIGH))
    if variant.startswith("control"):  # a line on the run's own candles: always built before
        line = {"id": "c9", "on": True, "left": {"field": "close", "tf": 15}, "op": "gt",
                "right": {"num": "1"}}  # fmt: skip
        raw["where"]["all"].append(line)
    _, plan = spec.build(raw)
    trades_from_the_open(source)
    member = next(m for m in source.members({"kind": "fno_stocks"}) if m.key == RELIANCE)
    ctx = source.context(ms(DAY, "09:30"), source.market.today)
    (judged,) = asyncio.run(runs.evaluate(plan, [member], source, ctx))
    assert judged.outcome.kind == HIT, "a screen of day lines at a 15-min close is never judged"
    assert judged.outcome.forming, "judged on the forming day candle, and marked so"
    assert judged.outcome.values["c1"] == {"l": "250.00", "r": "250.00", "forming": True}


def test_the_replay_check_judges_a_screen_of_day_lines_at_its_closes():
    """The replay check builds the same timeframes as the live source: a stock climbing from 95
    has its forming day close above 100 from the 09:45 check on."""
    stock = "NSE_EQ|AAA"
    ticks, vtt = [], 0
    for minute in range(0, 46):
        for second in (5, 35):
            at = ms(DAY, "09:15") + minute * 60_000 + second * 1000
            vtt += 10
            frame = {"key": stock, "ltp": 95.0 + minute * 0.2, "ltt": at, "ltq": 1, "vtt": vtt,
                     "cp": 90.0, "ts_recv": at}  # fmt: skip
            ticks.append((stock, at, frame))
    screen = spec.check({
        "name": "Day close above 100", "universe": {"kind": "watchlist", "list_id": 1},
        "run": {"on": "close", "tf": 15},
        "where": {"all": [{"id": "c1", "on": True, "left": {"field": "close", "tf": "D"},
                           "op": "gt", "right": {"num": "100"}}]},
        "target": None,
    })  # fmt: skip
    rows = [{"id": 1, "version": 1, "name": screen["name"], "spec": screen, "status": "active"}]
    members = {"watchlist:1": [Member(stock, "AAA", "NSE", "NSE_EQ", False)]}
    lines = asyncio.run(check.replay_day(ticks, rows, members, Calendar(), day=date(2026, 10, 28)))
    assert [(check.words.hm(line.at), line.matched) for line in lines] == [
        ("09:30", 0),  # 95 + 14 x 0.2 = 97.80 at 09:29:35
        ("09:45", 1),  # 95 + 29 x 0.2 = 100.80 at 09:44:35
        ("10:00", 1),
    ]
