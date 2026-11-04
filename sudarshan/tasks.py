"""Cancelling asyncio tasks so that none is left running (shared by the SUDARSHAN services).

A ``task.cancel()`` can be lost. Measured with httpx 0.28.1, httpcore 1.0.9 and anyio 4.14.2 on
Python 3.13: a cancel that arrived while httpx was still opening its connection (inside
``anyio.connect_tcp``) was swallowed in 19 of 480 tries made 0 to 6 ms into a request. The task
carried on and parked at its next await, so ``task.cancel(); await asyncio.gather(task)`` waited
for ever, and a service's stop hung until systemd killed it.

``cancel_and_wait`` sends the cancel again, every ``again_after`` seconds, to any task still
running, until every task has ended.
"""

from __future__ import annotations

import asyncio
import logging
import time

log = logging.getLogger("sudarshan.tasks")

#: How long a cancelled task may keep running before it is cancelled again.
AGAIN_AFTER_S = 0.2


async def cancel_and_wait(*tasks: asyncio.Task | None, again_after: float = AGAIN_AFTER_S) -> None:
    """Cancel every task, and return only when each one has ended.

    A task still running ``again_after`` seconds after its cancel is cancelled again, as often as
    it takes, and logged once. ``None`` entries are skipped; a task that has already ended is
    left as it is. Each task's outcome is retrieved, so asyncio never reports an exception as
    never retrieved.

    When the caller is cancelled while it waits here, the tasks are still cancelled until they
    have ended (as ``asyncio.TaskGroup`` does), and then the caller's cancel is raised.
    """
    alive = [task for task in tasks if task is not None]
    for task in alive:
        task.cancel()
    began = time.monotonic()
    told: set[asyncio.Task] = set()
    interrupted: asyncio.CancelledError | None = None
    while pending := [task for task in alive if not task.done()]:
        try:
            await asyncio.wait(pending, timeout=again_after)
        except asyncio.CancelledError as exc:  # our caller's own cancel: finish first
            interrupted = exc
        for task in pending:
            if task.done():
                continue
            if task not in told:
                told.add(task)
                log.warning(
                    "Task %s was still running %.2f s after it was cancelled (a cancel can be "
                    "lost while an HTTP connection opens); cancelling it again",
                    task.get_name(), time.monotonic() - began,
                )  # fmt: skip
            task.cancel()
    for task in alive:
        if not task.cancelled():
            task.exception()  # retrieved: never reported as never retrieved
    if interrupted is not None:
        raise interrupted
