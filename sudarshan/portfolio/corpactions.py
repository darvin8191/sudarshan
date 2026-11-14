"""The nightly read of Upstox's corporate actions (phase P7, lane C: M7.36, design p7 6.2).

Upstox lists a company's dividends, bonus issues, splits and rights issues by ISIN
(``GET /v2/fundamentals/{isin}/corporate-actions``, under Fundamentals: open to the read-only
Analytics Token, with no static IP). They are public facts, kept in ``ca_event`` for everyone
(``migrations.SHARED_TABLES``); what the owner decided about one is his own (``ca_decision``).

* **Which instruments.** The ISINs the Upstox account holds (its last closing record and the
  holdings last read), the listed shares the custom tabs have entries for, and the shares in
  Upstox's trade history (a split of a share sold since still changes its lots in the tax paper).
* **When.** Once a night from 23:45, after the NAV job, or before 08:00 when the night's run was
  missed; never in market hours. An instrument is read once a week in rotation, about a seventh
  of them a night; one with an event inside 14 days is read every night; one never read comes
  first (``core.corpactions.tonight``).
* **Inside its own budget.** This is not market data, so it does not go through the feed's
  request governor (whose list is market-data GETs). It spends from a ``Budget`` of its own, one
  API name, 1 a second, 20 a minute and 200 in 30 minutes (a tenth of Upstox's per-API figures),
  paced so that the minute bucket is never the stop, with the same 60 second pause on a 429 and
  the same pause on a refused token as the live book (``UpstoxReader``), through its token and
  its one HTTP client. No thread, task or client of its own.
* **Nothing is guessed.** A row that cannot be read is left out and counted in the read's note;
  an answer that is not Upstox's shape changes nothing; an event Upstox no longer lists is marked
  gone and kept, and comes back if it is listed again.
* **Nothing changes a lot here.** A bonus or a split is only stored. It reaches the lots when the
  owner accepts it (``ops/ca_ops.py``).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

import httpx

from .budget import PAUSE_S, Budget
from .core import corpactions as core
from .core.days import add_days, day_of_instant
from .store import Scope, Tx
from .upstox import _signature, minute_of_day

log = logging.getLogger("sudarshan.portfolio.corpactions")

OWNER = Scope.owner()
API = "corporate_actions"
PATH = "/v2/fundamentals/{isin}/corporate-actions"
#: 1 a second, 20 a minute, 200 in 30 minutes: a tenth of Upstox's figures for one API.
LIMITS = ((1, 1.0), (20, 60.0), (200, 1800.0))
#: Seconds between two reads of a run (under 20 a minute), and the most reads of one run.
PACE_S = 3.1
MAX_READS = 150
#: The run begins at 23:45 IST; a night that was missed is made up before 08:00.
RUN_AT_MIN = 23 * 60 + 45
CATCH_UP_BEFORE_MIN = 8 * 60
#: A run ends after this many reads in a row that Upstox did not answer.
SILENT_STOP = 3
#: One read on demand a minute (the owner opening an instrument's actions).
ON_DEMAND_EVERY_MS = 60_000
ISIN = re.compile(r"IN[A-Z0-9]{10}")
#: Outcomes that end a run: asking again tonight would only repeat them.
STOPS = ("slow", "busy", "no_token", "token_refused")
WORDS = {
    "none": "Upstox lists no corporate actions for this instrument.",
    "slow": "Upstox asked SUDARSHAN to slow down; the rest is read tomorrow night.",
    "busy": "SUDARSHAN has read Upstox as often as it allows for now; the rest is read "
    "tomorrow night.",
    "no_token": "SUDARSHAN cannot find its Upstox token on this server.",
    "token_refused": "Upstox did not accept SUDARSHAN's read-only token.",
    "silent": "Upstox did not answer; the rest is read tomorrow night.",
}


def isin_of_key(instrument_key: Any) -> str | None:
    """The ISIN inside an Upstox instrument key (``NSE_EQ|INE...``); None when it holds none."""
    tail = instrument_key.rpartition("|")[2] if isinstance(instrument_key, str) else ""
    return tail if ISIN.fullmatch(tail) else None


def event_hash(event: dict[str, Any]) -> str:
    return hashlib.sha256(core.identity(event).encode("utf-8")).hexdigest()


def known_isins(tx: Tx, held_now: list[dict[str, Any]] | None = None) -> set[str]:
    """Every ISIN whose corporate actions matter to the owner (the module's first point)."""
    found = {row.get("isin") for row in held_now or ()}
    days = [row["day"] for row in tx.rows("upstox_eod", "day")]
    if days:
        lines = tx.rows("upstox_eod_holding", "instrument_key", day=days[-1])
        found |= {isin_of_key(line["instrument_key"]) for line in lines}
    used = {row["asset_id"] for row in tx.rows("activity")}
    found |= {a["isin"] for a in tx.rows("asset", kind="listed") if a["id"] in used}
    for record in tx.report_list("trades"):
        rows = (record.get("payload") or {}).get("rows") or []
        found |= {row.get("isin") for row in rows if isinstance(row, dict)}
    return {isin for isin in found if isinstance(isin, str) and ISIN.fullmatch(isin)}


def due_day(now: int) -> int | None:
    """The night a run at ``now`` belongs to (the day it began on), or None outside its hours."""
    minute, today = minute_of_day(now), day_of_instant(now)
    if minute >= RUN_AT_MIN:
        return today
    return add_days(today, -1) if minute < CATCH_UP_BEFORE_MIN else None


class CorpActions:
    """Reads corporate actions through the live book's token and client, and keeps them."""

    def __init__(
        self,
        service: Any,
        upstox: Any,
        *,
        budget: Budget | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        mono: Callable[[], float] = time.monotonic,
    ):
        self.service, self.upstox = service, upstox
        self.budget = budget or Budget(LIMITS)
        self._sleep, self._mono = sleep, mono
        self._last = float("-inf")
        self._asked_at: int | None = None

    # ---- one call --------------------------------------------------------------------------
    async def _call(self, isin: str) -> tuple[str, Any]:
        """``("ok", payload)`` or ``("<why>", None)``; spends one read of the budget."""
        upstox = self.upstox
        if not ISIN.fullmatch(isin):
            return "none", None
        if upstox._refused:
            if _signature(upstox.token_file) == upstox._refused_at:
                return "token_refused", None
            upstox._refused = False
            upstox._told.discard("401")
        if self.budget.paused_for(API):
            return "slow", None
        token = upstox._token()
        if token is None:
            return "no_token", None
        if not self.budget.take(API):
            return "busy", None
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        try:
            answer = await upstox.client.get(
                upstox.base_url + PATH.format(isin=isin), headers=headers
            )
        except httpx.HTTPError as exc:
            log.warning("Upstox corporate actions did not answer (%s)", type(exc).__name__)
            return "silent", None
        finally:
            del token, headers
        status = answer.status_code
        if status == 401:
            upstox._refused, upstox._refused_at = True, _signature(upstox.token_file)
            log.warning("Upstox refused the read-only token; reads pause until it changes.")
            return "token_refused", None
        if status == 429 or upstox._rate_coded(answer):
            self.budget.pause(API, PAUSE_S)
            log.warning("Upstox asked for a pause on corporate actions: %d seconds", int(PAUSE_S))
            return "slow", None
        if status >= 500:
            log.warning("Upstox corporate actions answered with a server fault (%d)", status)
            return "silent", None
        if status != 200:
            return "none", None
        try:
            return "ok", json.loads(answer.text, parse_float=Decimal)
        except ValueError:
            return "unreadable", None

    # ---- one instrument --------------------------------------------------------------------
    async def read_one(self, isin: str, now: int) -> str:
        """Read one ISIN and keep what it lists. The outcome: ``ok``, ``none`` (Upstox has
        nothing under this ISIN; asked again in a week), ``unreadable`` or ``silent`` (nothing
        changed; asked again tomorrow), or one of ``STOPS``."""
        outcome, payload = await self._call(isin)
        if outcome == "ok":
            try:
                parsed = core.parse_answer(isin, payload)
            except ValueError:
                outcome = "unreadable"
        if outcome == "unreadable":
            log.warning("a corporate-actions answer could not be read; nothing was changed")
        if outcome not in ("ok", "none"):
            return outcome

        def write(tx: Tx) -> None:
            if outcome == "none":
                tx.ca_read_put(isin, now, "none", WORDS["none"])
                return
            listed = []
            for event in parsed["events"]:
                listed.append(event_hash(event))
                tx.ca_event_put({**event, "raw_hash": listed[-1]}, now)
            tx.ca_events_gone(isin, listed, now)
            left = sum(parsed["skipped"].values())
            note = f"{left} rows could not be read and were left out." if left else None
            tx.ca_read_put(isin, now, "ok", note)

        await self._store(lambda store: store.write(OWNER, write))
        return outcome

    async def _store(self, call: Callable[[Any], Any]) -> Any:
        store = self.service.store
        return await asyncio.get_running_loop().run_in_executor(None, lambda: call(store))

    # ---- the night's run -------------------------------------------------------------------
    def _held_now(self) -> list[dict[str, Any]]:
        memory = getattr(self.upstox, "_memory", {}).get("holdings")
        return list(getattr(memory, "data", None) or [])

    async def plan(self, now: int) -> list[str]:
        today, held = day_of_instant(now), self._held_now()

        def look(tx: Tx) -> list[str]:
            reads = {r["isin"]: r["read_at"] for r in tx.ca_reads()}
            days = {k: None if v is None else day_of_instant(v) for k, v in reads.items()}
            soon = tx.ca_events(from_day=today, to_day=add_days(today, core.NEAR_DAYS))
            near = {event["isin"] for event in soon}
            return core.tonight(sorted(known_isins(tx, held)), days, near, today, MAX_READS)

        return await self._store(lambda store: store.read(OWNER, look))

    async def _pace(self) -> None:
        wait = self._last + PACE_S - self._mono()
        if wait > 0:
            await self._sleep(wait)
        self._last = self._mono()

    async def run(self, now: int) -> tuple[str, str]:
        """One night's reads. Returns ``(status, note)`` for the job log."""
        todo = await self.plan(now)
        counts = {"ok": 0, "none": 0, "failed": 0}
        silent, stop = 0, None
        for isin in todo:
            await self._pace()
            outcome = await self.read_one(isin, self.service.clock())
            if outcome in STOPS:
                stop = outcome
                break
            counts[outcome if outcome in counts else "failed"] += 1
            silent = silent + 1 if outcome == "silent" else 0
            if silent >= SILENT_STOP:
                stop = "silent"
                break
        note = (
            f"{counts['ok']} of {len(todo)} instruments read, {counts['none']} with nothing "
            f"listed, {counts['failed']} not read"
        )
        if stop is not None:
            note = f"{note}. {WORDS[stop]}"
        # ``kinds.JOB_STATUSES``: a night that read nothing of what was due has failed.
        return ("failed" if todo and not counts["ok"] + counts["none"] else "ok"), note

    # ---- on demand -------------------------------------------------------------------------
    async def read_now(self, isin: str, now: int) -> str:
        """One read the owner asked for: only an instrument that matters to him, and at most one
        a minute. ``wait`` when it is too soon, ``unknown`` for any other ISIN."""
        held = self._held_now()
        known = await self._store(lambda s: s.read(OWNER, lambda tx: known_isins(tx, held)))
        if isin not in known:
            return "unknown"
        if self._asked_at is not None and 0 <= now - self._asked_at < ON_DEMAND_EVERY_MS:
            return "wait"
        self._asked_at = now
        return await self.read_one(isin, now)


def ensure(service: Any) -> CorpActions:
    """The service's one reader, built on first use."""
    reader = getattr(service, "_corpactions", None)
    if reader is None:
        reader = service._corpactions = CorpActions(service, service.ensure_upstox())
    return reader


async def corporate_actions_step(service: Any, now: int) -> str | None:
    """Run the night's reads once, when they are due and not yet done."""
    night = due_day(now)
    if service.store is None or night is None:
        return None
    store, loop = service.store, asyncio.get_running_loop()
    done = await loop.run_in_executor(
        None, lambda: store.read(OWNER, lambda tx: tx.job_get(API, night))
    )
    if done is not None:
        return None
    try:
        status, note = await ensure(service).run(now)
    except Exception:
        log.exception("the nightly read of corporate actions failed")
        status, note = "failed", "Corporate actions could not be read tonight."
    await loop.run_in_executor(
        None,
        lambda: store.write(
            OWNER, lambda tx: tx.job_put(API, night, status, note, service.clock())
        ),
    )
    log.info("corporate actions %s: %s", status, note)
    return status


def register_jobs() -> None:
    from .service import job
    from .timers import run_forever

    @job(API)
    async def corporate_actions_job(service) -> None:
        await run_forever(service, API, corporate_actions_step)


__all__ = [
    "CorpActions",
    "LIMITS",
    "corporate_actions_step",
    "due_day",
    "ensure",
    "event_hash",
    "isin_of_key",
    "known_isins",
    "register_jobs",
]
