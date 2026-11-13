"""The daily timers of the portfolio: the jobs register here (M7.12, M7.24; cross-design R5, R6, X11,
P22). The schedule itself, ``Schedule`` and ``run_forever``, moved to ``sudarshan/common/schedule.py``
(cross-design Y20, PC1) so that the family service and the journal use the same one; this module
re-exports them, so every import of ``portfolio.timers`` stands.

Each job module registers its own loop (P22); ``register_all`` is the one place the entry point
calls.
"""

from __future__ import annotations

from sudarshan.common.schedule import (  # noqa: F401  (re-exported)
    CHECK_EVERY_S,
    FROZEN_AFTER_TRADING_DAYS,
    MS_PER_MIN,
    SETTLE_AFTER_START_MIN,
    Schedule,
    Step,
    run_forever,
)


def register_all() -> None:
    """Register every job of the service, each from its own module. Called once by the entry
    point; a job module imported here adds one line."""
    from . import benchmark, eod, feedlink, reports, resolver

    resolver.register_job()
    feedlink.register_job()
    eod.register_jobs()
    benchmark.register_jobs()
    reports.register_jobs()
    try:
        from . import amfi
    except ImportError:  # pragma: no cover - the NAV job lands with PB7
        return
    amfi.register_jobs()
