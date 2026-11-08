"""The outbox sender (M9.15, design 3.7 step 4): each side effect of a run, sent once it is recorded.

A run's transaction queues rows; this job sends them in order and marks each done. A failure
retries with backoff (2, 5, 10, 30, 60 s), up to 30 minutes for a list sync and 5 minutes for a
digest, never for a push (a late toast is no use). Each is idempotent at the far end: an add that
exists is a no-op, a remove only touches items the screener added, the alerts store refuses a
digest claim it already holds.

The job reads and writes the store on its thread (``store.read`` / ``store.write``) and calls
OpenAlgo and the alerts service through ``link.Link`` (or a test's fake with the same methods).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any

from . import listsync
from .contract import EVENT_RUN
from .store import OWNER, StoreError, Tx

log = logging.getLogger("sudarshan.screener")

BACKOFF_S = (2, 5, 10, 30, 60)
GIVE_UP_MS = {"list_sync": 30 * 60_000, "digest": 5 * 60_000, "push": 0}
TICK_S = 1.0
BATCH = 20


def next_attempt(kind: str, attempts: int, created_at: int, now: int) -> int | None:
    """When to try again (epoch ms), or None to give up."""
    limit = GIVE_UP_MS.get(kind, 0)
    if limit == 0:
        return None
    wait = BACKOFF_S[min(attempts, len(BACKOFF_S) - 1)] * 1000
    then = now + wait
    return then if then - created_at <= limit else None


class Sender:
    """Sends what the outbox holds; one ``tick`` per second from the job."""

    def __init__(self, store, link, clock, *, on_sent=None):
        self.store, self.link, self.clock = store, link, clock
        self.sent = {"list_sync": 0, "digest": 0, "push": 0}
        self.failed = dict(self.sent)
        self._on_sent = on_sent

    def status(self) -> dict[str, Any]:
        return {"sent": dict(self.sent), "failed": dict(self.failed)}

    async def tick(self) -> int:
        now = self.clock()
        try:
            due = self.store.read(OWNER, lambda tx: tx.outbox_due(now, BATCH))
        except StoreError:
            return 0
        for row in due:
            await self._send(row)
        return len(due)

    async def _send(self, row: Mapping[str, Any]) -> None:
        kind, payload = row["kind"], row["payload"]
        try:
            if kind == "list_sync":
                await listsync.sync(
                    self.store,
                    self.link,
                    screen_id=int(payload["screen_id"]),
                    list_id=int(payload.get("list_id") or 0),
                    create_name=payload.get("create_name"),
                    ref=row["ref"],
                    now=self.clock(),
                )
            elif kind == "digest":
                await self.link.digest(payload)
            elif kind == "push":
                await self.link.push([{"event": EVENT_RUN, "data": payload}])
            else:
                raise ValueError(f"unknown outbox kind {kind!r}")
        except Exception as exc:
            self._failed(row, exc)
            return
        self.sent[kind] = self.sent.get(kind, 0) + 1
        now = self.clock()
        self.store.write(OWNER, lambda tx: tx.outbox_settle(row["id"], "done", now))
        if self._on_sent is not None:
            self._on_sent(row)

    def _failed(self, row: Mapping[str, Any], exc: BaseException) -> None:
        now = self.clock()
        again = next_attempt(row["kind"], row["attempts"], row["created_at"], now)
        if again is None:
            self.failed[row["kind"]] = self.failed.get(row["kind"], 0) + 1
            log.warning("a %s could not be sent and is dropped: %s", row["kind"], exc)
            self.store.write(OWNER, lambda tx: tx.outbox_settle(row["id"], "failed", now))
            return
        log.info(
            "a %s could not be sent (%s); trying again at +%ds",
            row["kind"],
            exc,
            (again - now) // 1000,
        )
        self.store.write(
            OWNER, lambda tx: tx.outbox_settle(row["id"], "pending", now, next_at=again)
        )


async def run_sender(service, *, tick_s: float = TICK_S) -> None:
    """The job: a Sender over the service's store and link, ticking every second."""
    link = service.parts.get("link")
    if link is None or service.store is None:
        log.info("the outbox sender has no link to OpenAlgo in this process; nothing is sent")
        return
    sender = Sender(service.store, link, service.clock)
    service.parts["outbox"] = sender
    while True:
        try:
            await sender.tick()
        except Exception:
            log.exception("the outbox sender failed on a tick")
        await asyncio.sleep(tick_s)


def _tx_noop(tx: Tx) -> None:  # pragma: no cover - keeps Tx referenced for type readers
    return None


__all__ = ["BACKOFF_S", "GIVE_UP_MS", "Sender", "next_attempt", "run_sender"]
