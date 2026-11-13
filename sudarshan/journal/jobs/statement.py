"""The job for the monthly statement, the 1st at 07:00 for the month just ended (M15.4) (JB9).

The work is ``periods.build_period``; this is the once-a-day run ``schedule.py`` asks for.
"""

from __future__ import annotations

from typing import Any

from ..periods import period_job
from ..schedule import register_job


async def monthly_job(service: Any, today: int, now: int, late: bool) -> str:
    return await period_job(service, "monthly", today, now)


def register_jobs() -> None:
    register_job("monthly", monthly_job)
