"""The replay check (SV9): a synthetic recording replayed with the tick clock, the runs it prints,
and --compare against a store the real run loop filled from the same ticks (clean, a difference,
an explained difference), with the exit codes the owner's one command relies on."""

from __future__ import annotations

import asyncio
import gzip
import json
from pathlib import Path

from screener_helpers import ms
from test_screener_feed_runtime import Calendar as _Calendar
from test_screener_runs import SPEC

from sudarshan.alerts.market.feedlink import HEALTH
from sudarshan.screener import check, runner, spec
from sudarshan.screener import store as store_module
from sudarshan.screener.bars import Bars
from sudarshan.screener.runs import Member
from sudarshan.screener.store import OWNER, ScreenerStore

DAY = "2026-10-28"
A, B = "NSE_EQ|AAA", "NSE_EQ|BBB"


class Calendar(_Calendar):
    pass


def write_recording(path: Path) -> list[tuple[str, int, dict]]:
    """Two stocks trading every 30 s from 09:15 to 10:16: A climbs above 100, B stays below."""
    ticks = []
    vtt = {A: 0, B: 0}
    for minute in range(0, 61):
        for second in (5, 35):
            at = ms(DAY, "09:15:00") + minute * 60_000 + second * 1000
            for key, base in ((A, 95.0), (B, 80.0)):
                vtt[key] += 10
                ltp = base + minute * 0.2 if key == A else base
                frame = {
                    "key": key,
                    "ltp": ltp,
                    "ltt": at,
                    "ltq": 1,
                    "vtt": vtt[key],
                    "cp": 90.0,
                    "ts_recv": at,
                }
                ticks.append((key, at, frame))
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        for _, _, frame in ticks:
            handle.write(json.dumps(frame) + "\n")
    return ticks


def test_the_replay_prints_each_run_with_the_tick_clock(tmp_path):
    recording = tmp_path / "2026-10-28.jsonl.gz"
    write_recording(recording)
    spec_file = tmp_path / "screen.json"
    spec_file.write_text(json.dumps({**SPEC, "target": None}))
    said: list[str] = []
    code = check.main(
        [str(recording), "--spec", str(spec_file), "--keys", f"{A},{B}",
         "--holidays", str(tmp_path / "none"), "--extra-days", str(tmp_path / "none2")],
        say=said.append,
    )  # fmt: skip
    assert code == check.CLEAN, said
    runs = [line for line in said if "Above hundred" in line]
    assert len(runs) == 4, said  # 09:30, 09:45, 10:00, 10:15 (the 10:16 tick seals 10:15)
    assert runs[0].startswith("09:30  Above hundred  0 matched  0 entered  0 left")
    assert "No alert: 0 match, 1 needed" in runs[0]
    assert runs[1].startswith(
        "09:45  Above hundred  1 matched  1 entered (AAA)"
    )  # 95 + 29 x 0.2 = 100.8 at 09:44:35
    assert "digest: Alert raised: 1 entered; 1 match now" in runs[1]
    assert runs[2].startswith("10:00  Above hundred  1 matched  0 entered  0 left")
    assert (
        runs[3].startswith("10:15  Above hundred  1 matched  0 entered  0 left")
        and "No alert: nothing new" in runs[3]
    )
    assert said[-1] == "4 runs replayed from 2026-10-28.jsonl.gz"
    missing = check.main(
        [str(tmp_path / "none.jsonl.gz"), "--spec", str(spec_file)], say=said.append
    )
    assert missing == check.CANNOT and said[-1].startswith("No recording")


def test_compare_is_clean_against_the_live_runs_and_names_what_differs(tmp_path):
    recording = tmp_path / "2026-10-28.jsonl.gz"
    ticks = write_recording(recording)
    db = tmp_path / "screener.db"
    store = ScreenerStore(db)
    try:
        stored = spec.check({**SPEC, "target": None})
        screen = store.write(
            OWNER, lambda tx: tx.screen_insert(stored["name"], stored, "active", ms(DAY, "09:00"))
        )
        members = [Member(A, "AAA", "NSE", "NSE_EQ"), Member(B, "BBB", "NSE", "NSE_EQ")]
        store.write(OWNER, lambda tx: tx.universe_replace("fno_stocks", "fno_stocks", [
            {"key": m.key, "symbol": m.symbol, "exchange": m.exchange, "segment": m.segment, "cas": False}
            for m in members
        ], ms(DAY, "09:00")))  # fmt: skip

        # the real run loop over the same ticks, as the service would have run it
        class Source(check.ReplaySource):
            pass

        bars = Bars()
        calendar = Calendar()
        market = check.market_for(
            __import__("datetime").date(2026, 10, 28), calendar, frozenset(), ticks[0][1]
        )
        source = Source(bars, market, {"fno_stocks": members})
        clock = {"now": ticks[0][1]}
        made = runner.Runner(store, source, lambda: clock["now"], calendar)
        tick_clock = ticks[0][1] - ticks[0][1] % 1000

        async def drive():
            nonlocal tick_clock
            for key, at, frame in ticks:
                while tick_clock <= at:
                    clock["now"] = tick_clock
                    source.market = check.Market(
                        *market[:4], check.Feed("replay", True, tick_clock), (), True, frozenset()
                    )
                    bars.on_clock(tick_clock, source.market)
                    await made.tick(tick_clock)
                    tick_clock += 1000
                source.market = check.Market(
                    *market[:4], check.Feed("replay", True, at), (), True, frozenset()
                )
                source.last[key] = frame
                bars.on_tick(frame, at, source.market)
            for _ in range(6):
                clock["now"] = tick_clock
                bars.on_clock(tick_clock, source.market)
                await made.tick(tick_clock)
                tick_clock += 1000

        asyncio.run(drive())
        assert made.made == 4
    finally:
        store.close()
        store_module.stop()

    args = [str(recording), "--from-db", "--db", str(db), "--compare",
            "--holidays", str(tmp_path / "none"), "--extra-days", str(tmp_path / "none2")]  # fmt: skip
    said: list[str] = []
    assert check.main(args, say=said.append) == check.CLEAN, said
    assert said[-1] == "Replay check: 4 runs compared, all match"

    # the service's figures differ on one run: unexplained, exit 1
    store = ScreenerStore(db)
    try:
        run_id = store.read(OWNER, lambda tx: tx.runs_page(screen["id"], None, 1))[0]["id"]
        store.write(OWNER, lambda tx: tx.run_finish(run_id, {}, matched=9))
    finally:
        store.close()
        store_module.stop()
    said = []
    assert check.main(args, say=said.append) == check.DIFFERENT
    assert any(line.startswith("DIFFERENT: ") and "service 9 matched" in line for line in said)
    assert said[-1].startswith("Replay check: 1 difference(s)")

    # the same run marked late by the service: explained, exit 0
    store = ScreenerStore(db)
    try:
        store.write(OWNER, lambda tx: tx.run_finish(run_id, {"late": ms(DAY, "10:20")}, matched=9))
    finally:
        store.close()
        store_module.stop()
    said = []
    assert check.main(args, say=said.append) == check.CLEAN
    assert any(line.startswith("explained: ") and "was late" in line for line in said)
    assert said[-1] == "Replay check: 4 runs compared, all match (1 explained)"
    assert HEALTH  # the recording carries no health frames: the replay counts the feed as healthy


def test_compare_on_a_later_day_starts_with_the_carried_members_and_reads_that_day_only(tmp_path):
    """Release review MEDIUM-11: from day 2 the replay started every screen with no member, so a
    member carried from earlier days read as entered, and every earlier day's run read as one the
    replay did not make: DIFFERENT, exit 1, on a day the service got right."""
    from sudarshan.screener import membership as mem

    recording = tmp_path / "2026-10-28.jsonl.gz"
    ticks = write_recording(recording)
    db = tmp_path / "screener.db"
    store = ScreenerStore(db)
    yesterday = ms("2026-10-27", "15:30")
    try:
        stored = spec.check({**SPEC, "target": None})
        screen = store.write(
            OWNER, lambda tx: tx.screen_insert(stored["name"], stored, "active", yesterday)
        )
        members = [Member(A, "AAA", "NSE", "NSE_EQ"), Member(B, "BBB", "NSE", "NSE_EQ")]
        store.write(OWNER, lambda tx: tx.universe_replace("fno_stocks", "fno_stocks", [
            {"key": m.key, "symbol": m.symbol, "exchange": m.exchange, "segment": m.segment, "cas": False}
            for m in members
        ], ms(DAY, "09:00")))  # fmt: skip
        carried = mem.Row(A, "AAA", "NSE", mem.ACTIVE, first_seen_at=yesterday - 3_600_000,
                          last_seen_at=yesterday, hits=3)  # fmt: skip

        def earlier_day(tx):
            tx.membership_put(
                screen["id"],
                0,
                {n: getattr(carried, n) for n in mem.Row.__dataclass_fields__},
                yesterday,
            )
            run = tx.run_insert({
                "screen_id": screen["id"], "screen_version": 1, "run_key": f"{screen['id']}:v1:20261027:15:24",
                "session": 20261027, "candle_start": yesterday - 900_000, "candle_end": yesterday,
                "ran_at": yesterday + 5000, "status": "done", "engine_version": 1,
            })  # fmt: skip
            tx.run_finish(run["id"], {}, matched=1)

        store.write(OWNER, earlier_day)
        made = drive_live(store, ticks, members)
        assert made.made == 4
        live = store.read(OWNER, lambda tx: tx.runs_session(20261028))
        assert sum(r["entered"] for r in live) == 0, "the carried member entered in the live run"
    finally:
        store.close()
        store_module.stop()

    args = [str(recording), "--from-db", "--db", str(db), "--compare",
            "--holidays", str(tmp_path / "none"), "--extra-days", str(tmp_path / "none2")]  # fmt: skip
    said: list[str] = []
    code = check.main(args, say=said.append)
    assert code == check.CLEAN, said
    assert said[-1] == "Replay check: 4 runs compared, all match"


def drive_live(store, ticks, members):
    """The real run loop over the recording's ticks, as the service would have run it."""
    from datetime import date

    bars = Bars()
    calendar = Calendar()
    market = check.market_for(date(2026, 10, 28), calendar, frozenset(), ticks[0][1])
    source = check.ReplaySource(bars, market, {"fno_stocks": members})
    clock = {"now": ticks[0][1]}
    made = runner.Runner(store, source, lambda: clock["now"], calendar)
    tick_clock = ticks[0][1] - ticks[0][1] % 1000

    def at(moment):
        return check.Market(*market[:4], check.Feed("replay", True, moment), (), True, frozenset())

    async def drive():
        nonlocal tick_clock
        for key, when, frame in ticks:
            while tick_clock <= when:
                clock["now"] = tick_clock
                source.market = at(tick_clock)
                bars.on_clock(tick_clock, source.market)
                await made.tick(tick_clock)
                tick_clock += 1000
            source.market = at(when)
            source.last[key] = frame
            bars.on_tick(frame, when, source.market)
        for _ in range(6):
            clock["now"] = tick_clock
            bars.on_clock(tick_clock, source.market)
            await made.tick(tick_clock)
            tick_clock += 1000

    asyncio.run(drive())
    return made
