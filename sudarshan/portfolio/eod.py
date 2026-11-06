"""The closing record, the settle and the catch-up (M7.12, design 3.6 and 5.3).

**Closing record**, once on each trading day at the day's last close plus 15 minutes (15:55; on
Muhurat day 8 Nov 2026 the same rule gives 15:55 of its own window):

* ``price_eod`` for every listed asset the price book has a price of today, ``basis = last``;
* the newest NAV of held funds (amfi.py), ``basis = nav``;
* ``benchmark_close`` for both indexes;
* ``upstox_eod`` and its holding lines from the Upstox reader.

If the service was down then, the record is taken at the next start the same day and marked
**late**. A day missed entirely gets its record from the next settle (prices from ``cp``, no
Upstox part).

**Settle** at the session start plus 5 minutes of the next trading day: each ``last`` record of
the previous trading day becomes the official close (``basis = close``), the tick's ``cp`` when its
``ltt`` is today, else the quote's previous close. A record that no one took gets one.

**Frozen** after 3 trading days: a record older than that never changes (timers.Schedule.frozen).

``job_run`` holds one row per job and day, so each runs once a day however often the loop asks.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from typing import Any

from .core.days import day_of_instant, to_date
from .core.money import value_paise
from .feedlink import INDEX_KEYS
from .store import Scope, Tx
from .timers import Schedule, run_forever

log = logging.getLogger("sudarshan.portfolio.eod")

OWNER = Scope.owner()
#: A record taken more than this long after its time is "late".
LATE_AFTER_MS = 5 * 60_000
#: The benchmark names the history reads, by the feed key of the index.
BENCHMARK_OF = dict(zip(INDEX_KEYS, ("nifty50", "nifty500"), strict=True))


def schedule(service: Any) -> Schedule:
    return Schedule(service.ensure_calendar(), eod_after_min=service.settings.eod_after_min)


def _write(service: Any, fn):
    return asyncio.get_running_loop().run_in_executor(None, lambda: service.store.write(OWNER, fn))


def _read(service: Any, fn):
    return asyncio.get_running_loop().run_in_executor(None, lambda: service.store.read(OWNER, fn))


# ---- the closing record --------------------------------------------------------------------
async def record_day(service: Any, today: int, now: int, late: bool, sched: Schedule) -> str:
    """Take the closing record of ``today``; returns the sentence the job log keeps."""
    store, prices = service.store, service.ensure_prices()
    await prices.warm(store, now)
    counts = {"prices": 0, "indexes": 0}

    def write(tx: Tx) -> None:
        for asset in tx.rows("asset", kind="listed"):
            have = tx.price_eod_get(asset["id"], today)
            if have is not None and have["basis"] == "close":
                continue
            info = prices.now(tx, asset, today)
            if info["price_u"] is None or info["day"] != today or info["source"] not in (
                "feed", "quote"
            ):  # fmt: skip
                continue
            tx.price_eod_put(
                asset["id"], today,
                {"price_u": info["price_u"], "prev_close_u": info["prev_close_u"], "basis": "last",
                 "source": info["source"], "late": 1 if late else 0, "at": info["at"]},
                now,
            )  # fmt: skip
            counts["prices"] += 1
        for key, name in BENCHMARK_OF.items():
            tick = prices.index_tick(key, today)
            have = tx.index_close_get(name, today)
            if tick is None or (have is not None and have["source"] != "feed"):
                continue
            tx.index_close_put(name, today, tick["price_u"], "feed")
            counts["indexes"] += 1
        from .amfi import apply_navs

        counts["funds"] = apply_navs(tx, today, sched, now)

    await _write(service, write)
    upstox = await record_upstox(service, today, now)
    return (
        f"{counts['prices']} prices, {counts['indexes']} indexes, {counts.get('funds', 0)} funds, "
        f"Upstox {upstox}"
    )


async def record_upstox(service: Any, today: int, now: int) -> str:
    """The Upstox account's part of the record: nothing is written when it could not be read."""
    reader = service.ensure_upstox()
    book = await reader.book(["holdings", "positions", "funds"], 0)
    holdings, positions, funds = (book[p] for p in ("holdings", "positions", "funds"))
    fresh = {name: part for name, part in book.items() if part["state"] == "ok"}
    if not fresh:
        log.warning("The Upstox part of the closing record could not be read.")
        return "not read"

    def write(tx: Tx) -> None:
        by_key = {a["instrument_key"]: a["id"] for a in tx.rows("asset", kind="listed")}
        rows = holdings["data"] if "holdings" in fresh else []
        head = {
            "read_at": now,
            "holdings_value_paise": sum(value_paise(r["qty_u"], r["last_price_u"]) for r in rows)
            if "holdings" in fresh else None,
            "holdings_cost_paise": sum(value_paise(r["qty_u"], r["avg_price_u"]) for r in rows)
            if "holdings" in fresh else None,
            "positions_pnl_paise": sum(r["pnl_paise"] for r in positions["data"])
            if "positions" in fresh else None,
            "funds": json.dumps(funds["data"], sort_keys=True) if "funds" in fresh else None,
        }  # fmt: skip
        lines = [
            {"instrument_key": r["instrument_key"], "asset_id": by_key.get(r["instrument_key"]),
             "qty": r["qty_u"], "t1_qty": r["t1_qty_u"], "avg_price_u": r["avg_price_u"],
             "price_u": r["last_price_u"], "basis": "last"}
            for r in rows
        ]  # fmt: skip
        tx.upstox_eod_put(today, head, lines, now)

    await _write(service, write)
    return "ok" if len(fresh) == 3 else "partly read"


async def eod_step(service: Any, now: int) -> str | None:
    """Take today's closing record if it is due and not taken. Returns the status written."""
    if service.store is None:
        return None
    sched, today = schedule(service), day_of_instant(now)
    at = sched.eod_at(to_date(today))
    if at is None or now < at or (await _read(service, lambda tx: tx.job_get("eod", today))):
        return None
    late = now - at > LATE_AFTER_MS
    try:
        detail = await record_day(service, today, now, late, sched)
        status = "late" if late else "ok"
    except Exception:
        log.exception("today's closing record could not be taken")
        detail, status = "The closing record could not be taken.", "failed"
    await _write(service, lambda tx: tx.job_put("eod", today, status, detail, now))
    log.info("closing record %s: %s", status, detail)
    return status


# ---- the settle ----------------------------------------------------------------------------
async def settle_day(service: Any, today: int, now: int, sched: Schedule) -> tuple[str, int]:
    previous = sched.previous_trading_day(to_date(today))
    if previous is None:
        return "no earlier trading day", 0
    before = previous.year * 10000 + previous.month * 100 + previous.day
    prices = service.ensure_prices()
    midnight = (to_date(today).toordinal() - date(1970, 1, 1).toordinal()) * 86_400_000 - 19_800_000

    def plan(tx: Tx):
        assets = [a for a in tx.rows("asset", kind="listed") if a["instrument_key"]
                  and a["created_at"] < midnight]  # fmt: skip
        want = {}
        for asset in assets:
            have = tx.price_eod_get(asset["id"], before)
            if have is None or have["basis"] == "last":
                want[asset["instrument_key"]] = (asset["id"], have)
        indexes = {
            key for key, name in BENCHMARK_OF.items()
            if (tx.index_close_get(name, before) or {"source": "feed"})["source"] == "feed"
        }  # fmt: skip
        return want, indexes

    want, indexes = await _read(service, plan)
    found = await prices.official_closes([*sorted(want), *sorted(indexes)], now)

    def apply(tx: Tx) -> tuple[int, int, int]:
        if sched.frozen(before, today):
            return 0, 0, 0
        settled = missed = 0
        for key, (asset_id, have) in want.items():
            official = found.get(key)
            if official is None:
                continue
            price, how = official
            if have is None:
                missed += 1
                row = {"price_u": price, "prev_close_u": None, "late": 1, "at": now}
            else:
                settled += 1
                row = {"price_u": price, "prev_close_u": have["prev_close_u"],
                       "late": have["late"], "at": have["at"]}  # fmt: skip
            tx.price_eod_put(asset_id, before, {**row, "basis": "close", "source": how}, now)
        done = 0
        for key in indexes:
            if key in found:
                tx.index_close_put(BENCHMARK_OF[key], before, found[key][0], found[key][1])
                done += 1
        return settled, missed, done

    settled, missed, done = await _write(service, apply)
    left = len(want) - settled - missed
    return (
        f"{settled} settled, {missed} taken from the settle, {done} indexes, {left} without a close",
        left,
    )


async def settle_step(service: Any, now: int) -> str | None:
    """Settle the previous close once a day. A settle that left a stock without an official close
    is written as failed and tried again once at the next start the same day (the frozen days
    stay untouched either way)."""
    if service.store is None:
        return None
    sched, today = schedule(service), day_of_instant(now)
    at = sched.settle_at(to_date(today))
    if at is None or now < at:
        return None
    row = await _read(service, lambda tx: tx.job_get("settle", today))
    if row is not None and (row["status"] != "failed" or service.settle_tried == today):
        return None
    service.settle_tried = today
    try:
        detail, left = await settle_day(service, today, now, sched)
        status = "failed" if left else "ok"
        if left:
            detail += "; asked again at the next start today"
    except Exception:
        log.exception("the settle of the previous close failed")
        detail, status = "The settle of the previous close failed.", "failed"
    await _write(service, lambda tx: tx.job_put("settle", today, status, detail, now))
    log.info("settle %s: %s", status, detail)
    return status


def register_jobs() -> None:
    from .service import job

    @job("eod")
    async def eod_job(service) -> None:
        await run_forever(service, "eod", eod_step)

    @job("settle")
    async def settle_job(service) -> None:
        await run_forever(service, "settle", settle_step)
