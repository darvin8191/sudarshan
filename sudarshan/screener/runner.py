"""The run loop (M9.14, design 3.6 and 3.7): every second, the runs that are due, made and recorded.

``Runner.tick(now)`` reads the active screens, asks the scheduler which boundaries are due and not
yet recorded, evaluates each (runs.evaluate, in slices that yield to the loop) and records it
(runs.record, one write transaction). A run that fails to evaluate is recorded ``failed`` so the
same boundary is not tried for ever; the next boundary is tried on its own.

The candles and facts come from a ``runs.Source`` the runtime registers as
``service.parts["source"]`` (SV5); the sessions come from ``sessions_of`` and the calendar (X11),
rebuilt at midnight or when a calendar file changes (``Runner.market`` is re-read each tick from
the day key). Without a source the job idles: nothing is judged on no data.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sudarshan.alerts.market.sessions import day_key, sessions_of
from sudarshan.autologin.schedule import IST

from . import runs, scheduler, spec
from .screen_ops import sched_screen
from .store import OWNER, StoreError, Tx
from .types import RunKey

log = logging.getLogger("sudarshan.screener")

TICK_S = 1.0


class Runner:
    def __init__(
        self, store, source: runs.Source, clock, calendar, *, slice_size: int = runs.SLICE
    ):
        self.store, self.source, self.clock, self.calendar = store, source, clock, calendar
        self.slice_size = slice_size
        self._day: int | None = None
        self._sessions: Mapping = {}
        self.made = 0
        self.failed = 0
        self.last_run_key: str | None = None

    def status(self) -> dict[str, Any]:
        return {
            "runs_made": self.made,
            "runs_failed": self.failed,
            "last_run_key": self.last_run_key,
        }

    def sessions(self, now: int):
        day = datetime.fromtimestamp(now / 1000, IST).date()
        key = day_key(day)
        if key != self._day:
            windows = self.calendar.windows() if hasattr(self.calendar, "windows") else {}
            self._sessions = sessions_of(
                day, trading_day=self.calendar.is_trading_day(day), window=windows.get(day)
            )
            self._day = key
        return self._sessions

    def _printed(self, screen_row: Mapping[str, Any]):
        members = self.source.members(screen_row["spec"]["universe"])
        cas = [m for m in members if m.cas]
        printed = getattr(self.source, "printed", None)
        return all(printed(m) for m in cas) if (cas and printed is not None) else True

    async def tick(self, now: int | None = None) -> int:
        now = self.clock() if now is None else now
        sessions = self.sessions(now)
        if not sessions:
            return 0
        try:
            active = self.store.read(
                OWNER, lambda tx: [r for r in tx.screens() if r["status"] == "active"]
            )
        except StoreError:
            return 0
        rows = {r["id"]: r for r in active}
        due = scheduler.due(
            [sched_screen(r) for r in active],
            sessions,
            now,
            lambda key: self.store.read(OWNER, lambda tx: tx.run_by_key(key) is not None),
            printed=lambda s: self._printed(rows[s.id]),
        )
        made = 0
        for item in due:
            await self._one(rows[item.screen.id], item)
            made += 1
        return made

    async def _one(self, screen_row: Mapping[str, Any], due: scheduler.Due) -> None:
        now = self.clock()
        try:
            plan = spec.compile_plan(screen_row["spec"])
            members = list(self.source.members(screen_row["spec"]["universe"]))
            ctx = self.source.context(due.boundary.end, due.key.session)
            judged = await runs.evaluate(
                plan, members, self.source, ctx, slice_size=self.slice_size
            )
        except Exception:
            self.failed += 1
            log.exception("the run %s could not be evaluated; it is recorded as failed", due.key)
            self._record_failed(screen_row, due, now)
            return
        try:
            report = self.store.write(
                OWNER,
                lambda tx: runs.record(
                    tx,
                    tx.screen(screen_row["id"]),
                    tx.state(screen_row["id"]),
                    due,
                    judged,
                    self.clock(),
                ),
            )
        except StoreError:
            log.exception("the run %s could not be recorded", due.key)
            self.failed += 1
            return
        self.made += 1
        self.last_run_key = report.run_key
        log.info(
            "run %s: %s, %d matched, %d entered, %d exited, %d not checked%s",
            report.run_key, report.status, report.matched, report.entered, report.exited,
            report.not_checked, " (late)" if due.late else "",
        )  # fmt: skip

    def _record_failed(self, screen_row: Mapping[str, Any], due: scheduler.Due, now: int) -> None:
        def write(tx: Tx) -> None:
            tx.run_insert(
                {
                    "screen_id": screen_row["id"],
                    "screen_version": due.key.version,
                    "run_key": str(due.key),
                    "session": due.key.session,
                    "candle_start": due.boundary.start,
                    "candle_end": due.boundary.end,
                    "ran_at": now,
                    "status": "failed",
                    "engine_version": runs.ENGINE_VERSION,
                }
            )

        try:
            self.store.write(OWNER, write)
        except StoreError:
            log.exception("the failed run %s could not be recorded either", due.key)


async def run_runner(service, *, tick_s: float = TICK_S) -> None:
    """The job: a Runner over the service's store and the registered source."""
    source = service.parts.get("source")
    if source is None or service.store is None:
        log.info("no price source is registered in this process; no screen is checked")
        return
    runner = Runner(service.store, source, service.clock, service.ensure_calendar())
    service.parts["runner"] = runner
    while True:
        try:
            await runner.tick()
        except Exception:
            log.exception("the run loop failed on a tick")
        await asyncio.sleep(tick_s)


__all__ = ["Runner", "RunKey", "run_runner"]
