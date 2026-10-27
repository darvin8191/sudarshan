"""How long the engine takes: ``python -m sudarshan.alerts.market.bench``.

Section 7 of the market alerts design budgets about 3-5 microseconds per alert judged on the
server's ARM cores (40-60 per tick once the feed's JSON and ZMQ are counted). This runs a synthetic
session at full speed, a random walk across each alert's level, through the engine and reports
microseconds per alert judged. It touches no file, network or database; run it on the server to
check the budget.
"""

from __future__ import annotations

import argparse
import logging
import random
import time

from sudarshan.alerts.market import engine, spec

log = logging.getLogger("sudarshan.alerts.market.bench")

START = 1_792_556_100_000  # 2026-10-21 09:15 IST
SBIN = {
    "instrument_key": "NSE_EQ|INE062A01020",
    "symbol": "SBIN",
    "exchange": "NSE",
    "tick_size": "0.05",
}
MIX = (
    {"params": {"op": "crosses_above", "level": "100"}, "frequency": "every_time"},
    {"params": {"op": "crosses", "level": "100.03"}, "frequency": "every_time", "cooldown_s": 60},
    {"params": {"op": "at_or_above", "level": "100"}, "frequency": "per_candle", "candle": 5},
    {"params": {"op": "crosses_below", "level": "100"}, "frequency": "at_close", "candle": 15},
)


def alerts(count: int) -> list[spec.Alert]:
    out = []
    for n in range(count):
        row = spec.check(SBIN | {"kind": "price", "ends": None} | MIX[n % len(MIX)], START)
        out.append(spec.Alert.of(row | {"id": n + 1, "version": 1, "status": "active"}))
    return out


def run(alert_count: int, ticks: int, seed: int = 7) -> tuple[float, int]:
    """(microseconds per alert judged, fires) over ``ticks`` values for each alert."""
    rng = random.Random(seed)
    pool = alerts(alert_count)
    states = {a.id: engine.State() for a in pool}
    walk = [2000 + rng.randint(-3, 3) for _ in range(ticks)]
    fires = judged = 0
    began = time.perf_counter()
    for i, value in enumerate(walk):
        at = START + i * 250
        for a in pool:
            minutes = a.candle_min or 1
            candle = (at - START) // (minutes * 60_000)
            end = START + (candle + 1) * minutes * 60_000
            obs = engine.Obs(value, at, 20261021, a.cond.up, a.cond.down, candle, end)
            out = engine.step(a, states[a.id], obs, at)
            states[a.id] = out.state
            fires += out.fire is not None
            judged += 1
    elapsed = time.perf_counter() - began
    return elapsed / judged * 1e6, fires


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m sudarshan.alerts.market.bench")
    parser.add_argument("--alerts", type=int, default=500, help="alerts judged on every value")
    parser.add_argument("--ticks", type=int, default=2000, help="values per alert")
    parser.add_argument("--rounds", type=int, default=5, help="the best round is reported")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # The best of several rounds: on a shared machine the others measure the neighbours.
    per_alert, fires = min(run(args.alerts, args.ticks) for _ in range(max(1, args.rounds)))
    log.info(
        "%d alerts x %d values: %.2f microseconds per alert judged, best of %d (%d fires)",
        args.alerts,
        args.ticks,
        per_alert,
        args.rounds,
        fires,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
