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


def frame(key: str, at: int, ltp: float, vtt: int, cp: float = 90.0, ltq: int = 1) -> bytes:
    return json.dumps(
        {"key": key, "ltp": ltp, "ltt": at, "ltq": ltq, "vtt": vtt, "cp": cp, "ts_recv": at}
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
            sealed += source.on_frame(RELIANCE, frame(RELIANCE, at, price, vtt, ltq=10), at)
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
    store.write(  # made before the open, so the 09:30 check is its own
        OWNER, lambda tx: tx.screen_insert("Live", spec.check(SPEC), "active", ms(DAY, "09:00"))
    )
    clock["now"] = ms(DAY, "09:30:05")
    made = runner.Runner(store, source, lambda: clock["now"], Calendar())
    assert asyncio.run(made.tick()) == 1
    run = store.read(OWNER, lambda tx: tx.runs_page(1, None, 1))[0]
    assert run["matched"] == 1 and run["candle_end"] == ms(DAY, "09:30")
    values = store.read(OWNER, lambda tx: tx.run_values(run["id"]))
    assert values[RELIANCE]["c1"]["l"] == "107.10" and values[RELIANCE]["_ltp"] == 10760

    # a preview judges the last closed candle and saves nothing
    preview = asyncio.run(source.preview(plan, spec.check(SPEC), clock["now"]))
    assert (
        preview["label"] == "Preview on the 09:15 to 09:30 candle, not saved"
        and preview["count"] == 1
    )
    assert (
        preview["first"][0]["symbol"] == "RELIANCE"
        and preview["first"][0]["values"] == "Close 107.10"
    )
    assert preview["not_checked"] == {
        "count": 1,
        # SBIN is past the key limit (1 here): it says so, never "no price from Upstox yet" (LOW-6)
        "reasons": ["Not checked: past the 1 instrument the screener can follow tick by tick"],
    }
    early = asyncio.run(source.preview(plan, spec.check(SPEC), ms(DAY, "09:20")))
    assert early["count"] == 0 and early["label"].startswith("No candle has closed")

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


def test_a_run_judges_the_candle_with_its_last_minute(world):
    """The run for 09:15-09:30 is due at 09:30:05, the moment the 09:29 minute is sealed; the
    runner's tick may come before the feed job's: the run seals it first (release review MEDIUM-4)."""
    store, source, clock = world
    source.new_day(clock["now"])
    source.refresh_universes([{"kind": "fno_stocks"}], clock["now"])
    vtt, sealed = 0, []
    for minute in range(15):  # 09:15..09:29, price 99.00, and 101.00 in the last minute
        at = ms(DAY, "09:15:20") + minute * 60_000
        sealed += source.on_frame(HEALTH, health(at), at)
        vtt += 10
        price = 101.0 if minute == 14 else 99.0
        sealed += source.on_frame(RELIANCE, frame(RELIANCE, at, price, vtt), at)
        sealed += source.on_frame(HEALTH, health(at + 45_500), at + 45_500)
        if minute < 14:
            sealed += source.on_clock(at + 46_000)  # each minute sealed at its end + 5 s
    source.persist(sealed)
    stored = spec.check(dict(SPEC, where={"id": "c1", "left": {"field": "close", "tf": 15},
                                          "op": "gt", "right": {"num": "100"}}))  # fmt: skip
    store.write(OWNER, lambda tx: tx.screen_insert("Close over 100", stored, "active", 0))
    clock["now"] = ms(DAY, "09:30:05.200")  # the runner's tick lands before the feed's on_clock
    source.on_frame(HEALTH, health(clock["now"] - 200), clock["now"] - 200)
    made = runner.Runner(store, source, lambda: clock["now"], Calendar())
    asyncio.run(made.tick())
    run = store.read(OWNER, lambda tx: tx.runs_page(1, None, 1))[0]
    assert run["candle_end"] == ms(DAY, "09:30")
    assert run["matched"] == 1, "the run judged the candle without its last minute"


def test_a_screen_needing_more_candles_than_a_day_holds_says_it_waits_for_history(world):
    """Release review MEDIUM-6: the 15-min EMA cross needs 151 candles and a day gives 25; the
    warm-up words read as progress that cannot come today. The reason names what it waits for."""
    from sudarshan.screener import presets

    store, source, clock = world
    source.new_day(clock["now"])
    source.refresh_universes([{"kind": "fno_stocks"}], clock["now"])
    at = ms(DAY, "09:20:05")
    source.on_frame(HEALTH, health(at), at)
    source.on_frame(RELIANCE, frame(RELIANCE, at, 101.0, 10), at)
    cross = next(p for p in presets.PRESETS if p.key == "ema_cross_15")
    stored = spec.check(dict(cross.spec, target=None))
    screen = store.write(OWNER, lambda tx: tx.screen_insert("EMA", stored, "active", 0))
    clock["now"] = ms(DAY, "09:30:05")
    source.on_frame(HEALTH, health(clock["now"]), clock["now"])
    made = runner.Runner(store, source, lambda: clock["now"], Calendar())
    asyncio.run(made.tick())
    run = store.read(OWNER, lambda tx: tx.runs_page(screen["id"], None, 1))[0]
    values = store.read(OWNER, lambda tx: tx.run_values(run["id"]))
    reason = values.get(RELIANCE, {}).get("_not_checked")
    assert reason == (
        "Not checked: needs 151 candles of 15 minutes; a day gives 25, and history before today "
        "waits for its download (not set up yet)"
    ), f"the warm-up reads as progress that cannot come today: {reason!r}, {run['matched']} matched"


# ---- the 15:30 check held for the closing auction, from 7 Dec 2026 (v0.8.1 review N2) ----------
THURSDAY = "2026-12-10"
#: Close above 100.00 on the 15-minute candle: only the auction's price (105.00) makes it.
ABOVE = {**SPEC, "target": None}


@pytest.fixture
def december(tmp_path, master_fixture):  # noqa: F811
    store = ScreenerStore(tmp_path / "screener.db")
    settings = config.Settings.from_env({"SUDARSHAN_SCREENER": "on"})
    clock = {"now": ms(THURSDAY, "09:14:00")}
    source = LiveSource(
        store, FakeFeed(), master_fixture, settings, lambda: clock["now"], Calendar()
    )
    source.new_day(clock["now"])
    source.refresh_universes([{"kind": "fno_stocks"}], clock["now"])
    yield store, source, clock
    store.close()
    store_module.stop()


def _trade_to_the_close(source, clock) -> None:
    """RELIANCE and SBIN trade at 99.00 every minute from 15:15 to 15:29, each minute sealed."""
    vtt, sealed = 0, []
    for minute in range(15):
        at = ms(THURSDAY, "15:15:20") + minute * 60_000
        sealed += source.on_frame(HEALTH, health(at - 500), at - 500)
        vtt += 10
        for key in (RELIANCE, SBIN):
            sealed += source.on_frame(key, frame(key, at, 99.0, vtt, ltq=10), at)
        clock["now"] = at + 45_000
        sealed += source.on_frame(HEALTH, health(clock["now"]), clock["now"])
        sealed += source.on_clock(clock["now"])
    source.persist(sealed)


def _last_run(store, screen_id: int):
    return store.read(OWNER, lambda tx: tx.runs_page(screen_id, None, 1))[0]


def test_the_check_held_for_the_closing_auction_is_on_time_when_nothing_restarted(december):
    """Thu 10 Dec: the 15:30 check of an F&O stocks screen waits for the auction prints, which
    arrive at 15:39:40 and 15:39:41, and is made then with their price. Its history line and its
    Telegram digest say nothing of a late check: nothing restarted (the first fix said "checked
    late at 15:39 after a restart")."""
    store, source, clock = december
    screen = store.write(
        OWNER,
        lambda tx: tx.screen_insert("Above", spec.check(ABOVE), "active", ms(THURSDAY, "15:29")),
    )
    made = runner.Runner(store, source, lambda: clock["now"], Calendar())
    clock["now"] = ms(THURSDAY, "15:29:01")
    assert asyncio.run(made.tick()) == 1, "its first check: the 15:00-15:15 candle"
    _trade_to_the_close(source, clock)
    clock["now"] = ms(THURSDAY, "15:30:05")
    source.on_frame(HEALTH, health(clock["now"]), clock["now"])
    assert asyncio.run(made.tick()) == 0, "the 15:15-15:30 candle waits for the prints"
    sealed = []
    for key, at, vtt in ((RELIANCE, "15:39:40", 400), (SBIN, "15:39:41", 400)):
        moment = ms(THURSDAY, at)
        sealed += source.on_frame(HEALTH, health(moment - 500), moment - 500)
        sealed += source.on_frame(key, frame(key, moment, 105.0, vtt, ltq=250), moment)
    source.persist(sealed)
    assert source.printed_at(source.members({"kind": "fno_stocks"})[1]) == ms(THURSDAY, "15:39:41")
    clock["now"] = ms(THURSDAY, "15:39:42")
    assert asyncio.run(made.tick()) == 1
    run = _last_run(store, screen["id"])
    assert run["candle_end"] == ms(THURSDAY, "15:30") and run["matched"] == 2
    assert "late" not in (run["marks"] or {}), run["marks"]
    digest = next(
        r
        for r in store.read(OWNER, lambda tx: tx.outbox_due(clock["now"] + 10**6, 100))
        if r["kind"] == "digest" and r["ref"] == run["run_key"]
    )
    assert digest["payload"]["late_at"] is None


def test_a_restart_past_the_auction_hold_is_still_called_late(december):
    """The same day with the service down from 15:30:10 to 15:50: it never saw the prints, so the
    15:30 check is made at 15:50, five minutes after its hold ended at 15:45:05, and is marked
    late, as a check a restart delayed is."""
    store, source, clock = december
    screen = store.write(
        OWNER,
        lambda tx: tx.screen_insert("Above", spec.check(ABOVE), "active", ms(THURSDAY, "15:29")),
    )
    made = runner.Runner(store, source, lambda: clock["now"], Calendar())
    clock["now"] = ms(THURSDAY, "15:29:01")
    asyncio.run(made.tick())
    _trade_to_the_close(source, clock)
    clock["now"] = ms(THURSDAY, "15:30:05")
    assert asyncio.run(made.tick()) == 0
    clock["now"] = ms(THURSDAY, "15:50:00")
    again = LiveSource(
        store, FakeFeed(), source.master, source.settings, lambda: clock["now"], Calendar()
    )
    again.new_day(clock["now"])
    again.refresh_universes([{"kind": "fno_stocks"}], clock["now"])
    restarted = runner.Runner(store, again, lambda: clock["now"], Calendar())
    assert asyncio.run(restarted.tick()) == 1
    run = _last_run(store, screen["id"])
    assert run["candle_end"] == ms(THURSDAY, "15:30")
    assert (run["marks"] or {}).get("late") == ms(THURSDAY, "15:50:00"), run["marks"]


def test_only_a_screen_holding_an_index_waits_for_its_closing_value(december):
    """Thu 10 Dec (v0.8.1 review N1): a screen of indices holds its 15:30 check until 15:45:05 for
    the index's closing value, on time then; a watchlist screen whose list holds no index (INFY
    only) checks at 15:30:05, although a watchlist may hold indices. The first fix held both."""
    store, source, clock = december
    watch = {"kind": "watchlist", "list_id": 3}
    source.refresh_universes(
        [{"kind": "indices"}, watch],
        clock["now"],
        lists={3: [{"symbol": "INFY", "exchange": "NSE"}]},
    )
    assert [m.segment for m in source.members(watch)] == ["NSE_EQ"]
    assert {m.segment for m in source.members({"kind": "indices"})} == {"NSE_INDEX"}
    made_at = ms(THURSDAY, "15:29")
    ids = {
        kind: store.write(
            OWNER,
            lambda tx, name=kind, u=universe: tx.screen_insert(
                name, spec.check({**ABOVE, "universe": u}), "active", made_at
            ),
        )["id"]
        for kind, universe in (("indices", {"kind": "indices"}), ("watchlist", watch))
    }
    made = runner.Runner(store, source, lambda: clock["now"], Calendar())
    clock["now"] = ms(THURSDAY, "15:29:01")
    assert asyncio.run(made.tick()) == 2, "each screen's first check: the 15:00-15:15 candle"
    clock["now"] = ms(THURSDAY, "15:30:05")
    assert asyncio.run(made.tick()) == 1
    assert _last_run(store, ids["watchlist"])["candle_end"] == ms(THURSDAY, "15:30")
    assert _last_run(store, ids["indices"])["candle_end"] == ms(THURSDAY, "15:15")
    clock["now"] = ms(THURSDAY, "15:45:04")
    assert asyncio.run(made.tick()) == 0
    clock["now"] = ms(THURSDAY, "15:45:05")
    assert asyncio.run(made.tick()) == 2, "the held index check, and the watchlist's 15:45 one"
    run = _last_run(store, ids["indices"])
    assert run["candle_end"] == ms(THURSDAY, "15:30")
    assert "late" not in (run["marks"] or {}), run["marks"]


def test_an_auction_stock_the_screener_does_not_follow_is_not_waited_for(world, monkeypatch):
    """Review N9: SBIN is past the key limit (max_keys 1), so no print of it can ever arrive; a
    screen holding both waits only for RELIANCE's print and, once that has come, is free at once
    instead of waiting to the window's end for a print it cannot receive."""
    store, source, clock = world
    source.new_day(clock["now"])
    source.refresh_universes([{"kind": "fno_stocks"}], clock["now"])
    members = source.members({"kind": "fno_stocks"})
    assert [m.cas for m in members] == [True, True]
    assert [source.follows(m) for m in members] == [True, False]
    row = {"spec": {"universe": {"kind": "fno_stocks"}}}
    run = runner.Runner(store, source, lambda: clock["now"], Calendar())
    arrived = {RELIANCE: None}
    monkeypatch.setattr(source.bars, "printed_at", lambda key, today: arrived.get(key))
    assert run._waits(row).printed is None, "the followed stock has not printed yet"
    arrived[RELIANCE] = ms(DAY, "15:41")
    assert run._waits(row).printed == ms(DAY, "15:41"), "the unfollowed one is not waited for"
    # a source that cannot say which it follows counts every member, as before
    monkeypatch.delattr(LiveSource, "follows")
    assert run._waits(row).printed is None


def test_waits_of_skips_what_the_screener_does_not_follow():
    from sudarshan.screener import scheduler as sch
    from sudarshan.screener.runs import Member

    kept = Member("NSE_EQ|A", "A", "NSE", "NSE_EQ", cas=True)
    cut = Member("NSE_EQ|B", "B", "NSE", "NSE_EQ", cas=True)
    arrived = {kept: 1_000}
    assert sch.waits_of([kept, cut], arrived.get) == sch.Waits(printed=None)
    assert sch.waits_of([kept, cut], arrived.get, lambda m: m is kept) == sch.Waits(printed=1_000)
    assert sch.waits_of([cut], arrived.get, lambda m: False) == sch.Waits(printed=0)
