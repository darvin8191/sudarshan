"""The job for the tax pack, 1 April at 07:00 for the financial year just ended (M15.5) (JB9),
and again 4 April at 07:00, when the year's last charges have arrived (``schedule.FY_AGAIN_AT``):
the same job, the same year, the report built over. That second build never puts a pack without
the tax working paper over one that has it (v0.13.0 verify N3).

The work is ``periods.build_period``; this is the once-a-day run ``schedule.py`` asks for.
"""

from __future__ import annotations

from typing import Any

from ..core.money import to_date
from ..periods import period_job
from ..schedule import FY_AGAIN_AT, register_job


async def fy_job(service: Any, today: int, now: int, late: bool) -> str:
    day = to_date(today)  # the day the run is for, also when a missed run is built later
    again = (day.month, day.day) == FY_AGAIN_AT[:2]
    return await period_job(service, "fy", today, now, keep_paper=again)


def register_jobs() -> None:
    register_job("fy", fy_job)
