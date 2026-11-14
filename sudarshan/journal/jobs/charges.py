"""Upstox's actual charges, read night by night (P7 J3, M14.2; design p7 4.6).

A step of the 22:00 catch-up, through the portfolio's journal purpose like every account read
(``pnl_report``, part ``charges``, with ``from`` and ``to``). It runs only while ``SUDARSHAN_TAX``
is on, and asks at most ``MAX_CALLS`` questions a night:

1. **The last 3 trading days**, each segment the journal holds live fills of that day and has
   no answer for: one read with ``from = to = day``, kept in ``upstox_charges`` as a ``day`` row.
   An answer that is all zero or empty while fills exist is Upstox not having the figures yet:
   the row is kept as asked, the day is asked again the next night, ``GIVE_UP`` nights in all.
2. **Each month that has ended**, from its 3rd day of the next month on: one read a segment for
   the first to the last of the month, the ``month`` row. It is what the comparison of estimate
   and actual rests on, and it answers for the days of the month Upstox never answered one by
   one (``actual.spread_month``). A month not answered after ``GIVE_UP`` nights is asked again
   only on the 3rd of each month.
3. **The walk back**: every older day since the journal's first fill that was never answered,
   newest first, ``WALKBACK_DAYS`` days a night, until all are. Nothing is kept about where the
   walk stands: the days still to ask are read off the fills and the rows each night.

Every answered day is shared among its orders and trades at once, in the transaction that keeps
the row. Days and months older than the three financial years Upstox keeps are never asked.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

from .. import actual
from ..core import charges as charges_core
from ..core import kinds
from ..core.money import ist_day, to_date, to_day
from ..migrations import BASIS_DAY, BASIS_MONTH
from ..portfolio import PortfolioUnavailable, link_for
from ..store import Scope

log = logging.getLogger("sudarshan.journal")

OWNER = Scope.owner()
MAX_CALLS = 20
WALKBACK_DAYS = 5
#: The day of the month from which the month before is asked for.
MONTH_FROM_DAY = 3
#: The financial years Upstox's reports reach, this one included.
REPORT_YEARS = 3
OFF = "charges: switched off (SUDARSHAN_TAX)"


def financial_year(day: int) -> int:
    """The year an Indian financial year starts in: 20261109 is in FY 2026-27, so 2026."""
    return day // 10000 if day // 100 % 100 >= 4 else day // 10000 - 1


def charges_row(found: Mapping[str, Any], segment: str, start: int, end: int, asked: int,
                now: int) -> dict[str, Any]:  # fmt: skip
    """The portfolio's ``charges`` answer for a period as an ``upstox_charges`` row; a part
    Upstox left empty stays None."""
    return {
        "segment": segment, "financial_year": financial_year(start), "period_from": start,
        "period_to": end, "total_paise": found.get("total_paise"),
        "brokerage_paise": found.get("brokerage_paise"), "gst_paise": found.get("gst_paise"),
        "stt_paise": found.get("stt_paise"), "stamp_paise": found.get("stamp_duty_paise"),
        "transaction_paise": found.get("transaction_paise"),
        "clearing_paise": found.get("clearing_paise"), "ipft_paise": found.get("ipft_paise"),
        "others_paise": found.get("others_paise"),
        "sebi_paise": found.get("sebi_turnover_paise"),
        "dp_paise": found.get("demat_transaction_paise"), "asked": asked, "read_at": now,
    }  # fmt: skip


def month_due(first: int, today: int) -> bool:
    """Whether the month starting on ``first`` is asked for on ``today``: from the 3rd of the
    month after it."""
    d = to_date(first)
    after = date(d.year + (d.month == 12), d.month % 12 + 1, MONTH_FROM_DAY)
    return to_date(today) >= after


def plan_night(traded: list[tuple[int, str]], rows: list[Mapping[str, Any]], recent: list[int],
               today: int) -> tuple[list[tuple[str, int, int]], int]:  # fmt: skip
    """What to ask tonight, in order, as (segment, from, to), and how many days are still left
    to walk back after it. ``traded`` is every (day, exchange) with a live fill, ``rows`` the
    kept ``upstox_charges`` rows, ``recent`` the last trading days before today."""
    kept = {(r["segment"], int(r["period_from"]), int(r["period_to"])): r for r in rows}
    oldest = financial_year(today) - REPORT_YEARS + 1
    days = sorted({(day, actual.SEGMENT_OF[exchange]) for day, exchange in traded
                   if exchange in actual.SEGMENT_OF and day < today
                   and financial_year(day) >= oldest})  # fmt: skip

    def open_(key: tuple[str, int, int]) -> bool:
        row = kept.get(key)
        return not actual.ready(row) and int((row or {}).get("asked") or 0) < actual.GIVE_UP

    wanted = [(s, d, d) for d, s in days if open_((s, d, d))]
    first = [w for w in wanted if w[1] in recent]
    older = sorted((w for w in wanted if w[1] not in recent), key=lambda w: (-w[1], w[0]))
    walk_days = sorted({w[1] for w in older}, reverse=True)
    tonight = set(walk_days[:WALKBACK_DAYS])
    months = []
    for start, end, segment in sorted({(*actual.month_of(d), s) for d, s in days}):
        if not month_due(start, today) or actual.ready(kept.get((segment, start, end))):
            continue
        asked = int((kept.get((segment, start, end)) or {}).get("asked") or 0)
        if asked < actual.GIVE_UP or today % 100 == MONTH_FROM_DAY:
            months.append((segment, start, end))
    queue = [*first, *months, *(w for w in older if w[1] in tonight)]
    return queue[:MAX_CALLS], len(walk_days) - len(tonight)


async def _thread(fn):
    return await asyncio.get_running_loop().run_in_executor(None, fn)


async def actual_charges(service: Any, days: list[int], now: int) -> str:
    """The catch-up's charges step (see the module's words); returns the log's line."""
    if not getattr(service.settings, "tax", False):
        return OFF
    today = ist_day(now)

    def read(tx):
        return (tx.fill_days(actual.LIVE), tx.rows("upstox_charges", order="period_from"),
                tx.settings().get("charges_plan") or charges_core.DEFAULT_PLAN)  # fmt: skip

    traded, rows, plan = await _thread(lambda: service.store.read(OWNER, read))
    plan = plan if plan in kinds.CHARGE_PLANS else charges_core.DEFAULT_PLAN
    queue, left = plan_night(traded, rows, days, today)
    if not queue:
        return "charges: nothing to ask Upstox for"
    kept = {(r["segment"], int(r["period_from"]), int(r["period_to"])): r for r in rows}
    link, found, unread = link_for(service), [], 0
    for n, (segment, start, end) in enumerate(queue):
        try:
            answer = await link.pnl_report(segment, financial_year(start), "charges",
                                           start=start, end=end)  # fmt: skip
        except PortfolioUnavailable as exc:
            unread = len(queue) - n
            log.info("charges: stopped after %d of %d reads (%s)", n, len(queue), exc.kind)
            break
        if answer.get("state") != "ok" or not isinstance(answer.get("charges"), Mapping):
            unread += 1
            continue
        asked = int((kept.get((segment, start, end)) or {}).get("asked") or 0) + 1
        found.append(charges_row(answer["charges"], segment, start, end, asked, now))

    def write(tx):
        orders: set[str] = set()
        for row in found:
            tx.upstox_charges_put(row, now)
        for row in found:
            if row["period_from"] == row["period_to"]:
                orders.update(actual.spread_day(tx, row["period_from"], row["segment"], plan, now))
        # A month just answered, or a day just given up, may let a month answer for its days.
        for segment, first in sorted({(r["segment"], actual.month_of(r["period_from"])[0])
                                      for r in found}):  # fmt: skip
            orders.update(actual.spread_month(tx, first, segment, plan, now))
        return actual.apply_trips(tx, actual.trips_of_orders(tx, orders), now) if orders else 0

    trips = await _thread(lambda: service.store.write(OWNER, write))
    day_rows = [r for r in found if r["period_from"] == r["period_to"]]
    answered = sum(1 for r in day_rows if actual.ready(r))
    months = sum(1 for r in found if r["period_from"] != r["period_to"] and actual.ready(r))
    line = (
        f"charges: {answered} days and {months} months answered by Upstox, "
        f"{len(found) - answered - months} not ready yet, {trips} trades now carry actual charges"
    )
    if unread:
        line += f", {unread} not read"
    if left:
        line += f", {left} earlier days still to ask"
    return line


__all__ = ["BASIS_DAY", "BASIS_MONTH", "actual_charges", "charges_row", "plan_night"]
