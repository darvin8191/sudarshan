"""Run sudarshan-feed with every step of its event loop timed: a test tool, never deployed.

    python sudarshan/feed/tests/loop_probe.py OUT.json   (from the repo root, the feed's env set)

Each step is one callback of the loop. Its cost is the CPU time of the loop's thread, so a step
the operating system interrupts on a busy machine does not look longer than it was (on a shared
virtual machine a single reading can still come out several times its cost). A step is the
governor's when it runs one of the HTTP API's request tasks (http_api._serve: in these tests only
the warm start's calls arrive there), a governor request with its hand-over (RestGovernor.serve)
or the watch for its caller hanging up (http_api._hang_up), the governor's timer or its save
(whose disk work runs on the saver thread, outside the loop); every other step is the feed's.
At exit OUT.json gets [wall time, CPU ms, owner] for each step over 0.2 ms, and for each owner the
number of steps and their CPU in all.
"""

from __future__ import annotations

import asyncio
import asyncio.events
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from sudarshan.feed import service  # noqa: E402

OUT = Path(sys.argv[1])
long_steps: list[tuple[float, float, str]] = []
totals: dict[str, list[float]] = {"governor": [0, 0.0], "feed": [0, 0.0]}
_run = asyncio.events.Handle._run
GOVERNOR_TASKS = {"_serve", "RestGovernor.serve", "_hang_up"}
GOVERNOR_CALLBACKS = {"RestGovernor._pump", "RestGovernor.save"}


def _owner(callback) -> str:
    task = getattr(callback, "__self__", None)
    if isinstance(task, asyncio.Task):
        return "governor" if task.get_coro().__qualname__ in GOVERNOR_TASKS else "feed"
    return "governor" if getattr(callback, "__qualname__", "") in GOVERNOR_CALLBACKS else "feed"


def _timed(handle) -> None:
    start = time.thread_time()
    try:
        _run(handle)
    finally:
        cpu_ms, owner = (time.thread_time() - start) * 1000, _owner(handle._callback)
        totals[owner][0] += 1
        totals[owner][1] += cpu_ms
        if cpu_ms > 0.2:
            long_steps.append((time.time(), round(cpu_ms, 3), owner))


asyncio.events.Handle._run = _timed
_serve = service._serve


async def _serve_and_report(cfg) -> None:
    try:
        await _serve(cfg)
    finally:
        OUT.write_text(json.dumps({"long_steps": long_steps, "totals": totals}))


service._serve = _serve_and_report
sys.exit(service.main())
