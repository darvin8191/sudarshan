"""How long the engine takes per tick: ``python -m sudarshan.alerts.market.bench``.

Section 7 of the market alerts design budgets 40-60 microseconds per tick on the server's ARM
cores (about 15-20 for ``json.loads``, 10-20 for the ZMQ receive, 3-5 per alert judged). This runs
a synthetic session at full speed, as the replay tool's ``--speed 0`` would: feed frames as JSON
bytes, a random walk across each alert's level, through ``json.loads``, the gate and every alert
on the key. It reports microseconds per tick and per alert judged, best of a few
rounds (on a shared machine the rest measure the neighbours). The ZMQ receive is not counted. It
touches no file, network or database; run it on the server to check the budget.
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import time
from datetime import date

from sudarshan.alerts.market import clock, sessions, spec
from sudarshan.alerts.market.book import Book

log = logging.getLogger("sudarshan.alerts.market.bench")

DAY = date(2026, 10, 21)
START = sessions.epoch_ms(DAY, 555)  # 09:15 IST
MIX = (
    {"params": {"op": "crosses_above", "level": "100"}, "frequency": "every_time"},
    {"params": {"op": "crosses", "level": "100.03"}, "frequency": "every_time", "cooldown_s": 60},
    {"params": {"op": "at_or_above", "level": "100"}, "frequency": "per_candle", "candle": 5},
    {"params": {"op": "crosses_below", "level": "100"}, "frequency": "at_close", "candle": 15},
    {"params": {"op": "crosses", "level": "100", "source": "bid"}, "frequency": "every_time"},
    {"kind": "move", "params": {"direction": "either", "percent": "0.5"}},
    {
        "kind": "day_break",
        "params": {"side": "high", "after": "09:15"},
        "frequency": "every_time",
        "cooldown_s": 60,
    },
    {"kind": "vwap", "params": {"direction": "either"}, "cooldown_s": 0},
)


def book(keys: int, per_key: int) -> Book:
    out = Book()
    for k in range(keys):
        for j in range(per_key):
            fields = {
                "instrument_key": f"NSE_EQ|BENCH{k:05d}",
                "symbol": f"BENCH{k}",
                "exchange": "NSE",
                "tick_size": "0.05",
                "kind": "price",
                "ends": None,
            } | MIX[(k * per_key + j) % len(MIX)]
            row = spec.check(fields, START) | {"id": k * per_key + j + 1, "version": 1}
            out.put(spec.Alert.of(row | {"status": "active"}))
    return out


def frames(keys: int, count: int, seed: int) -> list[bytes]:
    """Feed frames in arrival order: each key walks around 100.00 in 0.05 steps."""
    rng = random.Random(seed)
    prices = [2000] * keys
    out = []
    for n in range(count):
        k = n % keys
        prices[k] += rng.randint(-2, 2)
        at = START + n * 2  # about 500 ticks a second across the keys
        ltp = prices[k] * 0.05
        frame = {
            "key": f"NSE_EQ|BENCH{k:05d}", "ltp": round(ltp, 2), "ltt": at, "ltq": 1 + n % 7,
            "cp": 99.5, "bid": round(ltp - 0.05, 2), "bid_qty": 10, "ask": round(ltp + 0.05, 2),
            "ask_qty": 12, "atp": 100.01, "vtt": 10_000 + n, "ts_recv": at + 15,
            "ohlc": {"open": 99.8, "high": 101.0, "low": 99.0, "close": round(ltp, 2), "vol": n,
                     "ts": START},
        }  # fmt: skip
        out.append(json.dumps(frame, separators=(",", ":")).encode())
    return out


def run(keys: int, per_key: int, count: int, seed: int = 7) -> tuple[float, float, int]:
    """(microseconds per tick, of which json.loads, fires) over ``count`` frames."""
    sess = {s: sessions.session(s, DAY, trading_day=True) for s in sessions.SEGMENTS}
    pool, data = book(keys, per_key), frames(keys, count, seed)
    loads, on_tick = json.loads, pool.on_tick
    fires = 0
    world = None
    began = time.perf_counter()
    for n, payload in enumerate(data):
        now = START + n * 2 + 20
        if n % 2500 == 0:  # the feed's health every 5 s rebuilds what the runtime knows
            world = clock.Market(20261021, sess, 20261019, feed=clock.Feed("connected", True, now))
        result = on_tick(loads(payload), now, world)
        fires += sum(out.fire is not None for out in result.outcomes)
    elapsed = time.perf_counter() - began
    began = time.perf_counter()
    for payload in data:
        loads(payload)
    parse = time.perf_counter() - began
    return elapsed / count * 1e6, parse / count * 1e6, fires


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sudarshan.alerts.market.bench")
    parser.add_argument("--keys", type=int, default=300, help="instruments with alerts")
    parser.add_argument("--per-key", type=int, default=2, help="alerts on each instrument")
    parser.add_argument("--ticks", type=int, default=60_000, help="feed frames to judge")
    parser.add_argument("--rounds", type=int, default=3, help="the best round is reported")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    rounds = [run(args.keys, args.per_key, args.ticks) for _ in range(max(1, args.rounds))]
    per_tick, parse, fires = min(rounds)
    log.info(
        "%d instruments x %d alerts, %d ticks: %.1f microseconds per tick, of which json.loads "
        "%.1f; %.1f per alert judged; best of %d (%d fires)",
        args.keys,
        args.per_key,
        args.ticks,
        per_tick,
        parse,
        (per_tick - parse) / args.per_key,
        len(rounds),
        fires,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
