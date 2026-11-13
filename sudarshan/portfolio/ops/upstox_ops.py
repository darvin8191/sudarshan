"""The Upstox tab's two ops (M7.1 to M7.4, design 3.1 and 3.6, 7.2).

``upstox_book`` reads the parts asked for through the reader (single flight, five seconds of
memory, the service's own budget). ``upstox_history`` reads the closing records the closing job
wrote (at the day's last close plus 15 minutes: 15:55, and 16:00 from 7 Dec 2026). Neither
writes anything; neither shows a status, a code or a header to the owner.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sudarshan.alerts.market import sessions

from .. import serialize
from ..core.days import day_of_instant, to_date
from ..core.kinds import REPORT_SEGMENTS, UPSTOX_HISTORY_DAYS, UPSTOX_PAGE_PARTS, UPSTOX_PARTS
from ..errors import ApiError
from ..upstox import MAX_AGE_S
from . import op
from .common import OWNER, answer, caller, need, scope_now, work

ASK = "The request was incomplete. Reload the page."
MAX_AGE_LIMIT_S = 3600


def _parts(message, allowed=UPSTOX_PAGE_PARTS, default=...) -> list[str] | None:
    """The parts asked for, in contract order. The owner's page reads the four of the Upstox tab
    (``UPSTOX_PAGE_PARTS``); orders and trades are read for the family and the journal only."""
    asked = message.get("parts")
    if asked is None:
        return list(allowed) if default is ... else default
    if not isinstance(asked, list) or not asked or not all(a in allowed for a in asked):
        raise ApiError(400, "bad_request", ASK)
    return [a for a in allowed if a in asked]


def _max_age(message) -> int:
    value = message.get("max_age")
    if value is None:
        return MAX_AGE_S
    if value.__class__ is not int or not 0 <= value <= MAX_AGE_LIMIT_S:
        raise ApiError(400, "bad_request", ASK)
    return value


@op("upstox_book")
async def upstox_book(ctx, message):
    who = caller()
    if who.purpose == "family":
        parts = who.access.book_parts(_parts(message, UPSTOX_PARTS, default=None))
    else:
        parts = _parts(message)
    age = _max_age(message)
    extra = {} if who.principal == "owner" else {"principal": who.principal}
    if extra:
        # A member reads no fresher than the five seconds of memory: max_age 0 from a family
        # screen would spend the per-second reads the owner's own tab needs (review M4).
        age = max(age, MAX_AGE_S)
    book = await ctx.ensure_upstox().book(parts, age, **extra)
    return serialize.out(who.scope, book)


def _next_trading_day(calendar, day: date) -> date:
    for _ in range(14):
        day = date.fromordinal(day.toordinal() + 1)
        if calendar.is_trading_day(day):
            return day
    return day


@op("upstox_history")
async def upstox_history(ctx, message):
    store = need(ctx)
    limit = message.get("limit")
    if limit is None:
        limit = UPSTOX_HISTORY_DAYS
    if limit.__class__ is not int or not 1 <= limit <= UPSTOX_HISTORY_DAYS:
        raise ApiError(400, "bad_request", ASK)
    now, who = ctx.clock(), caller()
    if who.purpose == "family":
        who.access.need_upstox_tab()

    def read() -> list[dict[str, Any]]:
        return store.read(who.scope, lambda tx: tx.rows("upstox_eod", "day"))

    rows = (await work(read))[-limit:]
    sched = _schedule(ctx)
    days = [_day(ctx, row, now, sched) for row in rows]
    note = None if days else _first_record_note(sched, now)
    return serialize.out(who.scope, {"days": days, "note": note})


def _schedule(ctx):
    from ..eod import schedule

    return schedule(ctx)


def _hhmm(epoch_ms: int) -> str:
    from ..upstox import minute_of_day

    minute = minute_of_day(epoch_ms)
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _first_record_note(sched, now: int) -> str:
    """No record yet: when the first is taken, the time of that day (15:55; 16:00 from 7 Dec
    2026, when F&O closes at 15:45)."""
    day = to_date(day_of_instant(now))
    for _ in range(15):
        due = sched.eod_at(day)
        if due is not None and due > now:
            return f"No closing record has been taken yet. The first is taken at {_hhmm(due)}."
        day += timedelta(days=1)
    return "No closing record has been taken yet. The first is taken after the next close."


def _late_after_min(sched, day: date) -> int:
    """The minute after which a record of ``day`` was not taken on time: 45 minutes after the
    day's last close (16:25 on a day that closes at 15:40, 16:30 from 7 Dec 2026)."""
    from ..upstox import minute_of_day

    closes = sched.close_at(day)
    return LATE_AFTER_MIN if closes is None else minute_of_day(closes) + LATE_AFTER_CLOSE_MIN


def _day(ctx, row: dict[str, Any], now: int, sched=None) -> dict[str, Any]:
    """One closing record. It stays provisional until the next trading day's settle time; it is
    late when it was taken more than half an hour after the time it was due (``due_at``: the day's
    last close plus 15 minutes)."""
    from ..upstox import minute_of_day

    sched = sched or _schedule(ctx)
    day = to_date(row["day"])
    settled_at = _next_trading_day(ctx.ensure_calendar(), day)
    settle_ms = _settle_ms(settled_at, ctx.settings.settle_at_min)
    taken = day_of_instant(row["read_at"])
    late = taken != row["day"] or minute_of_day(row["read_at"]) > _late_after_min(sched, day)
    # A part that was not read is not known: null, never 0, and the day says which.
    value, cost = row["holdings_value_paise"], row["holdings_cost_paise"]
    pnl = row["positions_pnl_paise"]
    not_read = []
    if value is None or cost is None:
        not_read.append("Holdings were not read at the close.")
    if pnl is None:
        not_read.append("Positions were not read at the close.")
    return {
        "day": row["day"],
        "read_at": row["read_at"],
        "holdings_value_paise": value,
        "holdings_cost_paise": cost,
        "positions_pnl_paise": pnl,
        "not_read": not_read,
        "provisional": now < settle_ms,
        "late": late,
        # When the record of that day was due (epoch ms): the page names its time.
        "due_at": sched.eod_at(day),
    }


#: A record taken more than this long after the day's last close was not taken on time: 30 minutes
#: after the record's usual time, 15 minutes after the close (16:25 for 15:40; 16:30 from 7 Dec
#: 2026, when F&O closes at 15:45). LATE_AFTER_MIN when the day has no session (never expected).
LATE_AFTER_CLOSE_MIN = 45
LATE_AFTER_MIN = 16 * 60 + 25


def _settle_ms(day: date, settle_at_min: int) -> int:
    from ..core.days import IST_OFFSET_MS, MS_PER_DAY

    midnight = (day.toordinal() - date(1970, 1, 1).toordinal()) * MS_PER_DAY - IST_OFFSET_MS
    return midnight + settle_at_min * 60_000


# ---- the reports (M7.6 trade history, M7.7 trade-wise P&L and charges) -----------------------------
def _report_args(ctx, message) -> tuple[int, str]:
    from .. import reports

    today = day_of_instant(ctx.clock())
    fy, segment = message.get("fy"), str(message.get("segment") or "").upper()
    if fy.__class__ is not int or not reports.year_ok(fy, today):
        raise ApiError(400, "bad_request", reports.WORDS["year"])
    if segment not in REPORT_SEGMENTS:
        raise ApiError(400, "bad_request", reports.WORDS["segment"])
    return fy, segment


@op("report_years")
async def report_years(ctx, message):
    from .. import reports

    today = day_of_instant(ctx.clock())
    return serialize.out(OWNER, {"today": today, "years": reports.years_of(today)})


@op("report_trades")
async def report_trades(ctx, message):
    """The executed trades of one financial year and segment, from the cache or read now."""
    need(ctx)
    fy, segment = _report_args(ctx, message)
    reader = ctx.ensure_reports()
    return serialize.out(
        OWNER,
        await reader.report("trades", fy, segment, force=message.get("force") is True),
    )


@op("report_pnl")
async def report_pnl(ctx, message):
    """The trade-wise realised P&L of one financial year and segment, with Upstox's charges."""
    need(ctx)
    fy, segment = _report_args(ctx, message)
    reader = ctx.ensure_reports()
    return serialize.out(
        OWNER,
        await reader.report("pnl", fy, segment, force=message.get("force") is True),
    )
