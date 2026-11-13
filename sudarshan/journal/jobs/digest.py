"""The job for the week in review, Sunday 18:00 (M15.3) (JB9).

The work is ``periods.build_period``; this is the once-a-day run ``schedule.py`` asks for.
"""

from __future__ import annotations

from typing import Any

from ..periods import period_job
from ..schedule import register_job


async def weekly_job(service: Any, today: int, now: int, late: bool) -> str:
    return await period_job(service, "weekly", today, now)


def register_jobs() -> None:
    register_job("weekly", weekly_job)
