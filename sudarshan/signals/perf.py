"""The TRINETRA view's score (T8, M13.8, design 3.7.6): nightly marks and the performance op.

Twice a trading day the service reads the index's last tick from the price feed's snapshot (``GET
127.0.0.1:5580/snapshot``: no REST to Upstox, no claim, no socket of its own): at 09:16:30 the open
(the first price after 09:15) with the previous close Upstox names beside it (``cp``), and at 16:20
the close (the last tick of the day, from 15:25 on). Each day's three figures go to signals.db's
``level`` table; then every change's marks are filled from them.

The score, worked out from the timeline and the levels whenever it is asked (``score``, pure):

* A case is a change of kind first, state or bias (a new reading with a bias). Its reference is the
  close of its day (the trading day on or before it); after 1, 3 and 5 sessions it is compared with
  the closes of the trading days that follow. Bull is right when the index rose, bear when it fell;
  neutral names no direction, so it is counted and given its median move, never a share.
* ``gap_bias``: whether the open of the session it was about (the first 09:15 at or after it) gapped
  the way it said against the previous close (session 0).
* Under 30 cases a row says "too few cases to judge" and gives no figure.
"""

from __future__ import annotations

import asyncio
import logging
import statistics
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from urllib.parse import quote

import httpx

from sudarshan.signals import contract as c
from sudarshan.signals.service import job

log = logging.getLogger("sudarshan.signals")

IST = timezone(timedelta(seconds=c.IST_OFFSET_S), "IST")
#: The feed's keys of the two indices (``feed/default_keys.txt`` follows both, in ltpc mode).
INDEX_KEYS = {"NIFTY": "NSE_INDEX|Nifty 50", "BANKNIFTY": "NSE_INDEX|Nifty Bank"}
OPEN_AT = time(9, 16, 30)
CLOSE_AT = time(16, 20)
#: A tick from before these times of its day is not that day's open or close.
OPEN_FROM = time(9, 15)
CLOSE_FROM = time(15, 25)
SCORED_KINDS = ("first", "state", "bias")
FEED_TIMEOUT_S = 3.0
LOOK_AHEAD_DAYS = 15


def ist(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, IST)


def ms_of(day: date, at: time) -> int:
    return int(datetime.combine(day, at, IST).timestamp() * 1000)


def day_key(day: date) -> int:
    return int(day.strftime("%Y%m%d"))


def day0(at_ms: int, is_trading_day: Callable[[date], bool]) -> date:
    """The trading day on or before the change's own IST day: its close is the reference."""
    day = ist(at_ms).date()
    for _ in range(LOOK_AHEAD_DAYS):
        if is_trading_day(day):
            return day
        day -= timedelta(days=1)
    return day


def after(day: date, sessions: int, is_trading_day: Callable[[date], bool]) -> date | None:
    """The trading day ``sessions`` sessions after ``day`` (None past the look-ahead)."""
    found = 0
    for _ in range(LOOK_AHEAD_DAYS * 2):
        day += timedelta(days=1)
        if is_trading_day(day):
            found += 1
            if found == sessions:
                return day
    return None


def gap_day(at_ms: int, is_trading_day: Callable[[date], bool]) -> date | None:
    """The session a gap bias is about: the first trading day whose 09:15 is at or after it."""
    day = ist(at_ms).date()
    for _ in range(LOOK_AHEAD_DAYS):
        if is_trading_day(day) and at_ms <= ms_of(day, OPEN_FROM):
            return day
        day += timedelta(days=1)
    return None


def _row(module, instrument, bias, sessions, moves: list[float], hits: int) -> dict[str, Any]:
    cases = len(moves)
    judged = cases >= c.PERF["min_cases"]
    neutral = bias == "neutral"
    return {
        "module": module,
        "instrument": instrument,
        "bias": bias,
        "sessions": sessions,
        "cases": cases,
        "hits": 0 if neutral else hits,
        "hit_pct": f"{hits / cases * 100:.2f}" if judged and not neutral else None,
        "median_move_pct": f"{statistics.median(moves):.2f}" if judged else None,
        "judged": judged and not neutral,
        "words": c.WORDS["too_few"]
        if not judged
        else (c.WORDS["no_direction"] if neutral else None),
    }


def score(
    changes: Iterable[Mapping[str, Any]],
    levels: Mapping[tuple[int, str], Mapping[str, Any]],
    is_trading_day: Callable[[date], bool],
    module: str | None = None,
) -> dict[str, Any]:
    """The contract's ``Performance`` from the timeline's changes and the daily levels."""
    groups: dict[tuple, tuple[list[float], list[int]]] = {}
    for ch in changes:
        if ch["kind"] not in SCORED_KINDS or (module is not None and ch["module"] != module):
            continue
        inst, bias = ch["instrument"], ch["bias"]
        if ch["module"] == "gap_bias":
            target = gap_day(ch["at_ms"], is_trading_day)
            level = levels.get((day_key(target), inst)) if target else None
            if not level or not level.get("open_px") or not level.get("prev_close_px"):
                continue
            move = (level["open_px"] / level["prev_close_px"] - 1) * 100
            moves, hits = groups.setdefault((ch["module"], inst, bias, 0), ([], [0]))
            moves.append(move)
            hits[0] += (bias == "bull" and move > 0) or (bias == "bear" and move < 0)
            continue
        start = day0(ch["at_ms"], is_trading_day)
        base = levels.get((day_key(start), inst), {}).get("close_px")
        if not base:
            continue
        for k in c.PERF["sessions"]:
            later = after(start, k, is_trading_day)
            close = levels.get((day_key(later), inst), {}).get("close_px") if later else None
            if not close:
                continue
            move = (close / base - 1) * 100
            moves, hits = groups.setdefault((ch["module"], inst, bias, k), ([], [0]))
            moves.append(move)
            hits[0] += (bias == "bull" and move > 0) or (bias == "bear" and move < 0)
    order = {name: n for n, name in enumerate(c.KNOWN_MODULES)}
    rows = [
        _row(m, i, b, k, moves, hits[0])
        for (m, i, b, k), (moves, hits) in sorted(
            groups.items(),
            key=lambda kv: (
                order.get(kv[0][0], len(order)),
                kv[0][0],
                c.INSTRUMENTS.index(kv[0][1]),
                c.BIASES.index(kv[0][2]),
                kv[0][3],
            ),
        )  # fmt: skip
    ]
    days = [day for (day, _inst), level in levels.items() if level.get("close_px")]
    return {
        "module": module,
        "as_of_day": max(days) if days else None,
        "rows": rows,
        "words": None if rows else c.WORDS["no_scores"],
    }


def next_wake(now_ms: int, is_trading_day: Callable[[date], bool]) -> tuple[int, str]:
    """The next sample after ``now_ms``: its time and ``open`` or ``close``."""
    day = ist(now_ms).date()
    for _ in range(LOOK_AHEAD_DAYS):
        if is_trading_day(day):
            for at, kind in ((OPEN_AT, "open"), (CLOSE_AT, "close")):
                moment = ms_of(day, at)
                if moment > now_ms:
                    return moment, kind
        day += timedelta(days=1)
    return now_ms + 86_400_000, "none"


def _tick(ticks: Mapping[str, Any], key: str) -> dict[str, Any] | None:
    tick = ticks.get(key)
    if not isinstance(tick, dict):
        return None
    ltp, ltt, cp = tick.get("ltp"), tick.get("ltt"), tick.get("cp")
    if not isinstance(ltp, int | float) or isinstance(ltp, bool) or not ltp > 0:
        return None
    if not isinstance(ltt, int) or isinstance(ltt, bool):
        return None
    good_cp = isinstance(cp, int | float) and not isinstance(cp, bool) and cp > 0
    return {"ltp": float(ltp), "ltt": ltt, "cp": float(cp) if good_cp else None}


def levels_from(ticks: Mapping[str, Any], kind: str, now_ms: int) -> list[dict[str, Any]]:
    """Today's open (with the previous close) or close of each index from the feed's ticks; a
    tick from before the time of day it stands for, or from another day, is left out."""
    today = ist(now_ms).date()
    out = []
    for instrument, key in INDEX_KEYS.items():
        tick = _tick(ticks, key)
        if tick is None or ist(tick["ltt"]).date() != today or tick["ltt"] > now_ms:
            continue
        if kind == "open" and tick["ltt"] >= ms_of(today, OPEN_FROM):
            out.append({"day": day_key(today), "instrument": instrument, "open_px": tick["ltp"],
                        "prev_close_px": tick["cp"], "at_ms": tick["ltt"]})  # fmt: skip
        elif kind == "close" and tick["ltt"] >= ms_of(today, CLOSE_FROM):
            out.append({"day": day_key(today), "instrument": instrument, "close_px": tick["ltp"],
                        "at_ms": tick["ltt"]})  # fmt: skip
    return out


async def read_feed(feed_url: str) -> dict[str, Any]:
    """The feed's last tick of each index (an empty dict when the feed does not answer)."""
    keys = ",".join(quote(key, safe="") for key in INDEX_KEYS.values())
    try:
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
            answer = await client.get(
                f"{feed_url}/snapshot?keys={keys}",
                timeout=httpx.Timeout(FEED_TIMEOUT_S, connect=1.0),
            )
        ticks = answer.json().get("ticks") if answer.status_code == 200 else None
    except (httpx.HTTPError, ValueError, AttributeError):
        ticks = None
    return ticks if isinstance(ticks, dict) else {}


async def sample(service: Any, kind: str) -> int:
    """Read the feed and store today's open or close; after the close, fill the marks. How many
    levels were stored."""
    now = service.clock()
    found = levels_from(await read_feed(service.settings.feed_url), kind, now)
    if not found:
        log.warning("The price feed gave no %s of the indices today; TRINETRA's score skips the day",
                    kind)  # fmt: skip
        return 0
    await service.store.call(service.store.levels_put, found)
    if kind == "close":
        made = await service.store.call(service.store.fill_marks, service.calendar.is_trading_day)
        log.info("TRINETRA's marks: %d filled from today's close", made)
    return len(found)


@job("marks")
async def marks(service: Any) -> None:
    """Sleep to the next sample on a trading day, take it, and go on (waking at least hourly, so a
    changed clock or calendar is noticed)."""
    while True:
        moment, kind = next_wake(service.clock(), service.calendar.is_trading_day)
        wait_s = max(0.0, (moment - service.clock()) / 1000)
        if wait_s > 3600:
            await asyncio.sleep(3600)
            continue
        await asyncio.sleep(wait_s)
        if kind in ("open", "close"):
            try:
                await sample(service, kind)
            except Exception:
                log.exception("TRINETRA's %s sample failed; the next one is tried as usual", kind)
