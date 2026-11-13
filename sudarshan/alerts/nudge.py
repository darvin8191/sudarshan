"""One sealed "orders changed" signal for the ring services that follow the owner's orders (Y17).

The alerts service is the only ring service that sees the order stream, with the owner's key held
loopback-only. After it has handled each order update it tells every receiver switched on that
something changed, and nothing else:

    {"op": "nudge", "nonce": ..., "principal": "sudarshan-alerts",
     "kind": "orders" | "holdings", "sandbox": bool, "at": <ms>}

``kind`` is ``holdings`` when the update can change holdings (a CNC fill), else ``orders``. There
is no order data in it: no symbol, order id, quantity, price or status. The receiver reads what it
needs from the books itself.

  family   127.0.0.1:5585/v1/nudge, sealed under FAMILY_NUDGE_INFO, switched by
           ``SUDARSHAN_FAMILY=on`` in alerts.env. The listener belongs to the account
           ``sudarshan-family``; when that account does not exist the receiver counts as absent.
  journal  127.0.0.1:5586/v1/nudge, sealed under JOURNAL_NUDGE_INFO, switched by
           ``SUDARSHAN_JOURNAL=on``. The journal runs as the same user as this service.

Both are off by default, so a release that ships this posts nothing.

A nudge is a hint, never a message that must arrive: the receiver also re-reads on its own timer.
So the posts are coalesced to one a second for each receiver (what arrived in that second is one
nudge: ``holdings`` if any was, ``sandbox`` only if all were), have a 2 second timeout and are
never retried. A receiver that is slow or down never delays another, and nothing here is awaited
by the order alert path: the sender runs beside it, in tasks of its own, and ``note`` only writes
to memory. Before each post the listener on the port is looked up (loopback.py): the sealed body
holds no secret, but a nudge is only sent to the account that is meant to hold the port. A failure
is logged once in ten minutes for each receiver and kind.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import pwd
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

import httpx
from cryptography.fernet import Fernet

from sudarshan.autologin.handoff import seal

from . import loopback
from .orders import parse

log = logging.getLogger("sudarshan.alerts")

FAMILY_URL = "http://127.0.0.1:5585/v1/nudge"
JOURNAL_URL = "http://127.0.0.1:5586/v1/nudge"
PRINCIPAL = "sudarshan-alerts"
OP = "nudge"
FAMILY_ACCOUNT = "sudarshan-family"
#: At most one post a second for each receiver.
COALESCE_S = 1.0
#: The whole post, connecting and answering, never longer than this. Never retried.
TIMEOUT_S = 2.0
#: A failure of one kind for one receiver is logged this often at most.
LOG_EVERY_S = 600.0
ORDERS, HOLDINGS = "orders", "holdings"
ON = {"1", "on", "true", "yes"}


def nudge_kind(message: Mapping) -> str:
    """``holdings`` when the order update can change holdings (a CNC fill), else ``orders``."""
    update = parse(dict(message))
    if update is not None and update.product.upper() == "CNC":
        if update.state == "complete" or update.filled > 0:
            return HOLDINGS
    return ORDERS


def is_sandbox(message: Mapping) -> bool:
    return str(message.get("mode") or "").lower() == "analyze"


def account_uid(name: str = FAMILY_ACCOUNT, lookup: Callable = pwd.getpwnam) -> int | None:
    """The uid of the account called ``name``, None when there is no such account."""
    try:
        return int(lookup(name).pw_uid)
    except (KeyError, ValueError, OSError):
        return None


@dataclass(frozen=True)
class Receiver:
    """Where a nudge goes: the name for the log, the address, the key and who must hold the port.

    ``uid`` answers with the uid of the account that must own the listener, or None when that
    account does not exist (the receiver is then absent: nothing is posted).
    """

    name: str
    url: str
    fernet: Fernet
    uid: Callable[[], int | None]


class OnceIn:
    """Say something at most once in ``every`` seconds for each key."""

    def __init__(self, every: float = LOG_EVERY_S, clock: Callable[[], float] = time.monotonic):
        self.every, self.clock = every, clock
        self._said: dict[tuple, float] = {}

    def __call__(self, key: tuple) -> bool:
        now = self.clock()
        last = self._said.get(key)
        if last is not None and now - last < self.every:
            return False
        self._said[key] = now
        return True


@dataclass
class _Line:
    """One receiver's coalescing state."""

    receiver: Receiver
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    pending: tuple[bool, bool, int] | None = None  # (holdings, every update sandbox, newest at ms)
    last: float | None = None  # monotonic start of the last post


class NudgeSender:
    """Posts the nudges. ``note`` from the order path, ``run`` as a task, ``close`` at the end.

    Args:
        receivers: The receivers switched on (none: ``note`` does nothing).
        client: The service's HTTP client (no proxies, no redirects, shared: no socket of its own).
        tables: The /proc tables the listener check reads (a test passes its own).
        clock: Monotonic seconds. wall: Seconds since 1970, for the ``at`` the body carries.
    """

    def __init__(
        self,
        receivers: Sequence[Receiver],
        client: httpx.AsyncClient,
        *,
        tables=loopback.TCP_TABLES,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], float] = time.time,
    ) -> None:
        self.client, self.tables, self.clock, self.wall = client, tables, clock, wall
        self._lines = [_Line(receiver) for receiver in receivers]
        self._quiet = OnceIn(LOG_EVERY_S, clock)
        self._tasks: list[asyncio.Task] = []
        self.stats = {"posted": 0, "failed": 0, "refused": 0}

    def __bool__(self) -> bool:
        return bool(self._lines)

    def note(self, kind: str, sandbox: bool, at_ms: int | None = None) -> None:
        """An order update was handled. Never waits, never raises, sends nothing itself."""
        at = int(self.wall() * 1000) if at_ms is None else at_ms
        for line in self._lines:
            holdings, every_sandbox, newest = line.pending or (False, True, 0)
            line.pending = (
                holdings or kind == HOLDINGS,
                every_sandbox and sandbox,
                max(newest, at),
            )
            line.wake.set()

    async def run(self) -> None:
        """One sending task for each receiver, until cancelled."""
        self._tasks = [
            asyncio.create_task(self._run_one(line), name=f"nudge: {line.receiver.name}")
            for line in self._lines
        ]
        try:
            await asyncio.gather(*self._tasks)
        finally:
            for task in self._tasks:
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _run_one(self, line: _Line) -> None:
        while True:
            await line.wake.wait()
            if line.last is not None:
                wait = line.last + COALESCE_S - self.clock()
                if wait > 0:
                    await asyncio.sleep(wait)  # more arrive meanwhile: they join this nudge
            line.wake.clear()
            pending, line.pending = line.pending, None
            if pending is None:
                continue
            line.last = self.clock()
            try:
                await asyncio.wait_for(self._post(line.receiver, pending), TIMEOUT_S)
            except TimeoutError:
                self._failed(line.receiver, "timeout", "did not answer in time")
            except asyncio.CancelledError:
                raise
            except Exception:  # a nudge costs nothing when it does not arrive
                self._failed(line.receiver, "error", "could not be reached")
                log.debug("nudge failed", exc_info=True)

    async def _post(self, receiver: Receiver, pending: tuple[bool, bool, int]) -> None:
        holdings, sandbox, at = pending
        wanted = receiver.uid()
        if wanted is None:
            if self._quiet((receiver.name, "absent")):
                log.info(
                    "No nudges to the %s service: its account does not exist on this server",
                    receiver.name,
                )
            self.stats["refused"] += 1
            return
        held = loopback.holder(receiver.url, self.tables, expected_uid=wanted)
        if held != loopback.OURS:
            self.stats["refused"] += 1
            if held == loopback.NOBODY:
                self._failed(receiver, "down", "is not running", counted=False)
            else:
                self._failed(receiver, "listener", "is not held by its own account", counted=False)
            return
        body = seal(
            receiver.fernet,
            OP,
            principal=PRINCIPAL,
            kind=HOLDINGS if holdings else ORDERS,
            sandbox=sandbox,
            at=at,
        )
        try:
            answer = await self.client.post(
                receiver.url,
                content=body,
                headers={"Content-Type": "application/octet-stream", "Accept": "application/json"},
                timeout=TIMEOUT_S,
            )
        except httpx.HTTPError:
            self._failed(receiver, "down", "did not answer")
            return
        if answer.status_code >= 300:
            self._failed(receiver, f"status-{answer.status_code // 100}", "did not take it")
            return
        self.stats["posted"] += 1

    def _failed(self, receiver: Receiver, kind: str, why: str, counted: bool = True) -> None:
        if counted:
            self.stats["failed"] += 1
        if self._quiet((receiver.name, kind)):
            log.warning("A nudge to the %s service was not delivered: it %s", receiver.name, why)

    async def close(self) -> None:
        """Nothing is held back at a stop: an unsent nudge is only a hint, and is dropped."""
        for line in self._lines:
            line.pending = None
        for task in self._tasks:
            task.cancel()
        with contextlib.suppress(Exception):
            await asyncio.gather(*self._tasks, return_exceptions=True)


def receivers_for(
    family: bool, journal: bool, environ: Mapping[str, str], *, lookup: Callable = pwd.getpwnam
) -> list[Receiver]:
    """The receivers the switches turn on, keyed from the pepper. Never raises: a nudge never stops
    order alerts, so a missing pepper is logged and means no receivers."""
    if not (family or journal):
        return []
    from sudarshan.vault import FAMILY_NUDGE_INFO, JOURNAL_NUDGE_INFO, fernet_for, pepper_from

    found: list[Receiver] = []
    try:
        pepper = pepper_from(environ)
        if family:
            found.append(
                Receiver(
                    "family",
                    FAMILY_URL,
                    fernet_for(pepper, FAMILY_NUDGE_INFO),
                    lambda: account_uid(FAMILY_ACCOUNT, lookup),
                )
            )
        if journal:
            found.append(
                Receiver("journal", JOURNAL_URL, fernet_for(pepper, JOURNAL_NUDGE_INFO), os.getuid)
            )
    except Exception:
        log.exception("Nudges are on but cannot be sealed; none are sent")
        return []
    return found
