"""Index closes for the benchmarks: the backfill of daily candles (M7.31, design 5.5, P17).

``benchmark_close`` holds the closing level of NIFTY 50 and NIFTY 500 per day. The daily top-up
comes from the closing record (eod.py). This module fills the past, back to the earliest flow
date of any tab, so that "the same money in the index" has a level for every flow day.

* Daily candles go **only through the feed's request governor** (``/upstox/v3/historical-candle``
  as ``sudarshan-portfolio``, priority bulk). This service never calls Upstox for market data. The
  governor ships switched off: then it is said once in the journal and the closing record's top-up
  goes on alone.
* One-year requests, newest first, one request every 2 seconds, at most 30 in a run.
* Only after the day's last close plus 5 minutes (15:45 on a normal day) on trading days, or at
  any hour on a day the exchange is closed, so market-hours candle calls of other services never
  share the moment (sessions.py through the calendar).
* It stops where the data ends: a year with no candle is the end, and the day it ends at is kept
  in the job's note so that the next run does not ask again.
* NIFTY 500's key is looked up in the master contract. If it is not there, NIFTY 50 goes on alone,
  and the note says so in words.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import quote

from .core.days import day_of_instant, from_date, to_date
from .core.kinds import PRICE_SCALE
from .feedlink import CLIENT, INDEX_KEYS, json_of
from .store import Scope, Tx
from .timers import Schedule, run_forever
from .upstox import scaled

log = logging.getLogger("sudarshan.portfolio.benchmark")

OWNER = Scope.owner()
PAUSE_S = 2.0
MAX_REQUESTS = 30
CLOSED_AFTER_MIN = 5  # minutes after the day's last close
BENCHMARK_OF = dict(zip(INDEX_KEYS, ("nifty50", "nifty500"), strict=True))
WORDS = {
    "off": "Index history is not asked for: the feed's request governor is switched off. "
    "Benchmarks fill in day by day from the closing record.",
    "missing": "NIFTY 500 is not in the master contract, so only NIFTY 50 is followed.",
}
BEGINS = re.compile(r"(nifty50|nifty500) begins (\d{8})")


def candles(payload: dict) -> list[tuple[int, int]]:
    """``(day, close_u)`` of each daily candle, from the answer's ``data.candles`` rows
    ``[timestamp, open, high, low, close, ...]``. Rows that are not one are left out."""
    data = payload.get("data")
    rows = data.get("candles") if isinstance(data, dict) else None
    out = []
    for row in rows if isinstance(rows, list) else ():
        if not isinstance(row, list) or len(row) < 5 or not isinstance(row[0], str):
            continue
        try:
            day = from_date(date.fromisoformat(row[0][:10]))
            close = row[4]
            made = scaled(Decimal(str(close)), PRICE_SCALE)
        except (ValueError, ArithmeticError):
            continue
        if made is not None and made > 0:
            out.append((day, made))
    return out


def windows(earliest: int, today: int, have_to: int | None):
    """One-year ``(from, to)`` day windows, newest first, from ``today`` back to ``earliest``;
    ``have_to`` is the oldest day already held, from which the walk goes on."""
    top = to_date(today) if have_to is None else to_date(have_to) - timedelta(days=1)
    floor = to_date(earliest)
    while top >= floor:
        start = max(floor, top - timedelta(days=364))
        yield from_date(start), from_date(top)
        top = start - timedelta(days=1)


class Backfill:
    def __init__(
        self,
        service: Any,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        mono: Callable[[], float] = time.monotonic,
    ):
        self.service, self._sleep, self._mono = service, sleep, mono
        self._told: set[str] = set()
        self._last = 0.0

    def say_once(self, name: str, text: str) -> None:
        if name not in self._told:
            self._told.add(name)
            log.warning("%s", text)

    async def _pace(self) -> None:
        wait = self._last + PAUSE_S - self._mono()
        if wait > 0:
            await self._sleep(wait)
        self._last = self._mono()

    def due(self, now: int) -> bool:
        sched = Schedule(self.service.ensure_calendar())
        day = to_date(day_of_instant(now))
        if not sched.trading(day):
            return True
        close = sched.close_at(day)
        return close is not None and now >= close + CLOSED_AFTER_MIN * 60_000

    async def keys(self) -> tuple[list[str], str | None]:
        """The index keys to follow, and the sentence when one is not in the master contract."""
        found = list(await self.service.ensure_prices()._index_keys())
        return found, (WORDS["missing"] if len(found) < len(INDEX_KEYS) else None)

    async def run(self, now: int) -> tuple[str, str]:
        """One run. Returns ``(status, note)`` for the job log."""
        store = self.service.store
        loop = asyncio.get_running_loop()
        keys, missing = await self.keys()

        def plan(tx: Tx):
            dates = [r["trade_date"] for r in tx.rows("activity") if r["status"] == "posted"]
            have = {}
            for key in keys:
                name = BENCHMARK_OF[key]
                held = [d for d, _ in tx.index_closes().get(name, [])]
                have[key] = min(held) if held else None
            ends = {}
            for row in tx.job_runs():
                if row["name"] == "benchmark":
                    for name, day in BEGINS.findall(row["detail"] or ""):
                        ends[name] = min(int(day), ends.get(name, 99999999))
            return (min(dates) if dates else None), have, ends

        earliest, have, ends = await loop.run_in_executor(None, lambda: store.read(OWNER, plan))
        if earliest is None:
            return "skipped", "No entries yet, so there is nothing to compare with."
        today = day_of_instant(now)
        spent, stored, notes = 0, 0, [missing] if missing else []
        for key in keys:
            name = BENCHMARK_OF[key]
            floor = max(earliest, ends.get(name, 0))
            if have[key] is not None and have[key] <= floor:
                continue
            have_to = oldest = have[key]
            for start, end in windows(floor, today, have_to):
                if spent >= MAX_REQUESTS:
                    notes.append("stopped at the limit of requests for one run")
                    break
                await self._pace()
                spent += 1
                answer = await self.service.ensure_prices().feed.call(
                    "GET",
                    f"/upstox/v3/historical-candle/{quote(key, safe='')}/days/1/"
                    f"{to_date(end).isoformat()}/{to_date(start).isoformat()}",
                    headers={"X-Sudarshan-Client": CLIENT, "X-Sudarshan-Priority": "bulk"},
                )
                if answer is None or answer.status_code != 200:
                    if answer is not None and answer.headers.get("x-sudarshan-reason") == "off":
                        self.say_once("off", WORDS["off"])
                        return "skipped", WORDS["off"]
                    notes.append("the feed did not give index history; it will be asked again")
                    return "failed", "; ".join(notes)
                found = candles(json_of(answer))
                if not found:  # the data ends here: remember where, and do not ask again
                    notes.append(f"{name} begins {oldest if oldest is not None else end}")
                    break
                oldest = min([d for d, _ in found] + ([oldest] if oldest else []))
                stored += await loop.run_in_executor(None, self._put, name, found, now)
        detail = f"{stored} closes stored in {spent} requests"
        return "ok", "; ".join([detail, *notes])

    def _put(self, name: str, found: list[tuple[int, int]], now: int) -> int:
        def write(tx: Tx) -> int:
            n = 0
            for day, close in found:
                have = tx.index_close_get(name, day)
                if have is not None and have["source"] in ("feed", "cp", "quote"):
                    continue  # a record the service took itself stands
                tx.index_close_put(name, day, close, "candle")
                n += 1
            return n

        return self.service.store.write(OWNER, write)


async def benchmark_step(service: Any, now: int) -> str | None:
    if service.store is None:
        return None
    runner = getattr(service, "_backfill", None)
    if runner is None:
        runner = service._backfill = Backfill(service)
    today = day_of_instant(now)
    loop = asyncio.get_running_loop()
    done = await loop.run_in_executor(
        None, lambda: service.store.read(OWNER, lambda tx: tx.job_get("benchmark", today))
    )
    if done is not None or not runner.due(now):
        return None
    status, note = await runner.run(now)
    await loop.run_in_executor(
        None,
        lambda: service.store.write(
            OWNER, lambda tx: tx.job_put("benchmark", today, status, note, now)
        ),
    )
    log.info("index history %s: %s", status, note)
    return status


def register_jobs() -> None:
    from .service import job

    @job("benchmark")
    async def benchmark_job(service) -> None:
        await run_forever(service, "benchmark", benchmark_step)


__all__ = ["Backfill", "benchmark_step", "candles", "register_jobs", "windows"]
