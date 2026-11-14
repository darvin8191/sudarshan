"""The daily timers of the portfolio: the jobs register here (M7.12, M7.24; cross-design R5, R6, X11,
P22). The schedule itself, ``Schedule`` and ``run_forever``, moved to ``sudarshan/common/schedule.py``
(cross-design Y20, PC1) so that the family service and the journal use the same one; this module
re-exports them, so every import of ``portfolio.timers`` stands.

Each job module registers its own loop (P22); ``register_all`` is the one place the entry point
calls. A job of phase P7 (``kinds.SWITCHED_JOBS``) runs only while its switch is on: the gate is
put on here, once, so no job module has to remember.
"""

from __future__ import annotations

import logging
from functools import wraps
from typing import Any

from sudarshan.common.schedule import (  # noqa: F401  (re-exported)
    CHECK_EVERY_S,
    FROZEN_AFTER_TRADING_DAYS,
    MS_PER_MIN,
    SETTLE_AFTER_START_MIN,
    Schedule,
    Step,
    run_forever,
)

from .config import switch_on
from .core.kinds import SWITCH_ENV, SWITCHED_JOBS

log = logging.getLogger("sudarshan.portfolio")


def register_all() -> None:
    """Register every job of the service, each from its own module. Called once by the entry
    point; a job module imported here adds one line."""
    from . import benchmark, corpactions, eod, feedlink, probe, reports, resolver
    from .service import JOBS

    resolver.register_job()
    feedlink.register_job()
    probe.register_job()
    eod.register_jobs()
    benchmark.register_jobs()
    reports.register_jobs()
    corpactions.register_jobs()  # M7.36 (phase P7, lane C)
    try:
        from . import amfi
    except ImportError:  # pragma: no cover - the NAV job lands with PB7
        amfi = None
    if amfi is not None:
        amfi.register_jobs()
    gate_switched(JOBS)


def gate_switched(jobs: dict[str, Any]) -> None:
    """Put each job of the phase behind its switch: off, it says so once and ends, so nothing is
    read for it (design p7 2.4)."""
    for switch, names in SWITCHED_JOBS.items():
        for name in names:
            if name in jobs and getattr(jobs[name], "switch", None) is None:
                jobs[name] = _behind(switch, name, jobs[name])


def _behind(switch: str, name: str, function: Any) -> Any:
    @wraps(function)
    async def job(service: Any) -> None:
        if not switch_on(service, switch):
            log.info("the %s job does not run: %s is off", name, SWITCH_ENV[switch])
            return
        await function(service)

    job.switch = switch  # type: ignore[attr-defined]
    return job
