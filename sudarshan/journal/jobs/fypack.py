"""The job for the tax pack, 1 April at 07:00 for the financial year just ended (M15.5) (JB9).

The work is ``periods.build_period``; this is the once-a-day run ``schedule.py`` asks for.
"""

from __future__ import annotations

from typing import Any

from ..periods import period_job
from ..schedule import register_job


async def fy_job(service: Any, today: int, now: int, late: bool) -> str:
    return await period_job(service, "fy", today, now)


def register_jobs() -> None:
    register_job("fy", fy_job)
