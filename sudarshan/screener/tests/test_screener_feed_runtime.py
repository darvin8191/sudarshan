"""Runtime 1 (SV5) against a fake feed and a master made with OpenAlgo's model: the day's
universes and claims, frames into bars and the store, the live Subject the run loop judges, a
feed pause opened and closed (S14), the engine line's facts, and a run made from live bars."""

from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest
from screener_helpers import ms
from test_screener_runs import SPEC
from test_screener_runtime import Calendar
from test_screener_universe import master as master_fixture  # noqa: F401  (the fixture)

from sudarshan.alerts.market.feedlink import HEALTH
from sudarshan.screener import config, runner, spec
from sudarshan.screener import store as store_module
from sudarshan.screener.runtime import LiveSource
from sudarshan.screener.store import OWNER, ScreenerStore, Tx

DAY = "2026-10-28"
RELIANCE = "NSE_EQ|INE002A01018"
SBIN = "NSE_EQ|INE062A01020"


class FakeFeed:
    def __init__(self):
        self.topics: frozenset = frozenset()
        self.claims: list[dict] = []
        self.dropped = 0

    def follow(self, keys):
        self.topics = frozenset(keys)

    async def put_claims(self, keys, timeout=5.0):
        self.claims.append(dict(keys))
        return {"claimed": len(keys)}

    async def drop_claims(self):
        self.dropped += 1
        return {}


def health(now_ms: int, *, state="connected", live=True, age=0.5) -> bytes:
    return json.dumps({"state": state, "subscriptions_live": live, "last_message_age_s": age,
                       "market_status": {"NSE_EQ": "NORMAL_OPEN"}}).encode()  # fmt: skip


def frame(key: str, at: int, ltp: float, vtt: int, cp: float = 90.0) -> bytes:
    return json.dumps(
        {"key": key, "ltp": ltp, "ltt": at, "ltq": 1, "vtt": vtt, "cp": cp, "ts_recv": at}
    ).encode()


@pytest.fixture
def world(tmp_path, master_fixture):  # noqa: F811
    store = ScreenerStore(tmp_path / "screener.db")
    settings = config.Settings.from_env(
        {"SUDARSHAN_SCREENER": "on", "SUDARSHAN_SCREENER_MAX_KEYS": "1"}
    )
    clock = {"now": ms(DAY, "09:14:00")}
    source = LiveSource(
        store, FakeFeed(), master_fixture, settings, lambda: clock["now"], Calendar()
    )
    yield store, source, clock
    store.close()
    store_module.stop()


def test_the_day_builds_universes_claims_the_keys_within_the_limit_and_restores_bars(world):
    store, source, clock = world
    assert source.new_day(clock["now"]) is True
    assert source.market.today == 20261028 and source.market.previous == 20261027
    source.refresh_universes([{"kind": "fno_stocks"}, {"kind": "indices"}], clock["now"])
    assert source.master_ready is True
    assert [m.symbol for m in source.members({"kind": "fno_stocks"})] == ["RELIANCE", "SBIN"]
    assert source.keys == {RELIANCE}, "max_keys 1: only the first key is claimed"
    assert source.feed.topics == {RELIANCE} and RELIANCE in source.market.auction
    asyncio.run(source.claim())
    assert source.feed.claims == [{RELIANCE: "full"}]
    stored = store.read(OWNER, Tx.universes)
    assert {u["name"] for u in stored} == {"fno_stocks", "indices"}
    assert store.read(OWNER, lambda tx: tx.universe_members("fno_stocks"))[0]["cas"] is True
    assert source.new_day(clock["now"]) is False


def test_frames_become_sealed_bars_a_live_subject_and_a_run(world):
    store, source, clock = world
    source.new_day(clock["now"])
    source.refresh_universes([{"kind": "fno_stocks"}], clock["now"])
    assert (
        source.subject(
            source.members({"kind": "fno_stocks"})[0],
            spec.compile_plan(spec.check(SPEC)),
            ms(DAY, "09:30"),
        )
        is None
    )
    sealed = []
    vtt = 0
    at = ms(DAY, "09:15:00")
    sealed += source.on_frame(HEALTH, health(at), at)
    for minute in range(0, 16):  # trades every minute from 09:15 to 09:30, health every 30 s
        for second in (5, 35):
            at = ms(DAY, "09:15:00") + minute * 60_000 + second * 1000
            sealed += source.on_frame(HEALTH, health(at - 1000), at - 1000)
            vtt += 10
            price = 100.0 + minute * 0.5 + (0.1 if second == 35 else 0)
            sealed += source.on_frame(RELIANCE, frame(RELIANCE, at, price, vtt), at)
        clock["now"] = at + 30_000
        sealed += source.on_clock(clock["now"])
    source.persist(sealed)
    assert source.counts["trades"] == 32 and len(sealed) == 16, (
        "every minute to 09:30 is sealed by 09:31:05"
    )
    assert store.read(OWNER, lambda tx: len(tx.bars(RELIANCE))) == 16
    member = source.members({"kind": "fno_stocks"})[0]
    assert source.prices(member) == (10760, 9000)
    plan = spec.compile_plan(spec.check(SPEC))
    subject = source.subject(member, plan, ms(DAY, "09:30"))
    assert (
        subject is not None
        and subject.cp == 9000
        and subject.auction_end == source.market.sessions["NSE_EQ"].end
    )
    assert len(subject.series[15]) == 1 and subject.series[15].forming is False
    candle = subject.series[15].candle(0)
    assert (candle.o, candle.c, candle.v) == (10000, 10710, 300)

    # the run loop judges the live bars: close 107.10 > 100 is a hit
    store.write(
        OWNER, lambda tx: tx.screen_insert("Live", spec.check(SPEC), "active", clock["now"])
    )
    clock["now"] = ms(DAY, "09:30:05")
    made = runner.Runner(store, source, lambda: clock["now"], Calendar())
    assert asyncio.run(made.tick()) == 1
    run = store.read(OWNER, lambda tx: tx.runs_page(1, None, 1))[0]
    assert run["matched"] == 1 and run["candle_end"] == ms(DAY, "09:30")
    values = store.read(OWNER, lambda tx: tx.run_values(run["id"]))
    assert values[RELIANCE]["c1"]["l"] == "107.10" and values[RELIANCE]["_ltp"] == 10760

    # a restart restores today's bars from the store
    again = LiveSource(
        store, FakeFeed(), source.master, source.settings, lambda: clock["now"], Calendar()
    )
    again.new_day(clock["now"])
    assert len(again.bars.bars(RELIANCE)) == 16


def test_a_silent_or_down_feed_opens_a_pause_inside_the_session_and_the_engine_line_says_so(world):
    store, source, clock = world
    source.new_day(clock["now"])
    source.refresh_universes([{"kind": "fno_stocks"}], clock["now"])
    before = ms(DAY, "09:00:00")
    facts = source.engine_facts(before)
    assert facts["trading"] is False and facts["feed"] == "silent"
    at = ms(DAY, "10:00:00")
    source.on_frame(HEALTH, health(at), at)
    assert source.engine_facts(at)["feed"] == "ok" and source.market.pauses == ()
    assert source.engine_facts(at)["without_prices"] == 1 and source.engine_facts(at)["total"] == 1
    # silence of the whole socket for over 10 s: a pause from now
    later = at + 15_000
    source.on_frame(HEALTH, health(later, age=14.0), later)
    assert source.engine_facts(later)["feed"] == "reconnecting"
    assert len(source.market.pauses) == 1 and source.market.pauses[0].end is None
    assert source.market.pauses[0].start == later
    # health not heard for 12 s is the same as down; back again closes the pause
    back = later + 20_000
    source.on_frame(HEALTH, health(back), back)
    assert source.market.pauses[0].end == back and source.engine_facts(back)["feed"] == "ok"
    assert source.status()["pauses"] == 1
    # a feed stopped or without a token names the cause
    source.on_frame(HEALTH, health(back + 1000, state="token_rejected", live=False), back + 1000)
    assert source.engine_facts(back + 1000)["feed"] == "token_rejected"
    assert len(source.market.pauses) == 2
    assert date(2026, 10, 28) == source._today
