"""The close check after the cash post-close session (JB5; cross-design Y22.2).

At the post-close session's end plus 5 minutes (16:05 on a normal day) the day's whole list of
trades is read through the portfolio's journal purpose: a fill the live reads missed is added, a
recorded one Upstox's list no longer holds is marked withdrawn, the day is paired again (the
capture's hooks) and the day is marked checked, with what the check found in the owner's words.
The sandbox's fills are read from sandbox.db at the same time. A read that does not happen fails
the run, which is asked again every 15 minutes until midnight; the 22:00 read follows whatever
this one did.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..capture import Outcome, capture_for
from ..core.words import plural
from ..schedule import JobFailed, register_job
from ..store import Scope

OWNER = Scope.owner()


def check_note(out: Outcome) -> str:
    """What the close check found, as the day report and the journal page say it."""
    if not (out.new or out.back or out.withdrawn or out.fixed):
        return "The close check agreed with the fills recorded live."
    parts = []
    if out.new or out.back:
        parts.append(f"found {plural(out.new + out.back, 'more fill', 'more fills')}")
    if out.withdrawn:
        parts.append(f"took out {plural(out.withdrawn, 'fill')} Upstox's book no longer lists")
    if out.fixed:
        parts.append(f"corrected {plural(out.fixed, 'fill')}")
    return f"The close check {', '.join(parts)}; the journal is corrected."


async def check_day(service: Any, today: int, now: int, late: bool) -> str:
    capture = capture_for(service)
    live = await capture.read_live("close_reconcile", withdraw=True)
    if live.state != "ok":
        raise JobFailed(f"The day's trades could not be read for the close check: {live.words}")
    sandbox = await capture.read_sandbox("sandbox_book")
    note = check_note(live)
    await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: service.store.write(
            OWNER,
            lambda tx: tx.day_close_put(
                "live", today, {"reconciled_at": now, "reconcile_note": note}, now
            ),
        ),
    )
    service.counts["last_reconcile_ms"] = now
    from ..reports import build_eod

    # the day report already kept says what the check found; it is not sent again
    await build_eod(service, today, now, announce=False, create=False)
    return f"live: {live.line()}; sandbox: {sandbox.line()}"


def register_jobs() -> None:
    register_job("check", check_day)
