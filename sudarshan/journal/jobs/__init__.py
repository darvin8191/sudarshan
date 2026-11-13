"""The journal's jobs, each registering its own loop with ``service.job`` (JB2).

``register_all`` is the one place the entry point calls; a job module adds one line here.
"""

from __future__ import annotations


def register_all() -> None:
    """Register every job of the service, each from its own module. Called once by the entry
    point, before the service starts."""
    from .. import feedlink, nudge, reports
    from . import catchup, check, day, digest, excursion, fypack, statement

    nudge.register_jobs()
    feedlink.register_jobs()
    reports.register_jobs()
    day.register_jobs()
    check.register_jobs()
    excursion.register_jobs()
    catchup.register_jobs()
    digest.register_jobs()
    statement.register_jobs()
    fypack.register_jobs()
