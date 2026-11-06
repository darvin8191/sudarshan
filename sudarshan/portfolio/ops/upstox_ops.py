"""The Upstox tab's two ops (M7.1 to M7.4, design 3.1 and 3.6, 7.2).

``upstox_book`` reads the parts asked for through the reader (single flight, five seconds of
memory, the service's own budget). ``upstox_history`` reads the closing records the 15:55 job
wrote. Neither writes anything; neither shows a status, a code or a header to the owner.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sudarshan.alerts.market import sessions

from .. import serialize
from ..core.days import day_of_instant, to_date
from ..core.kinds import UPSTOX_HISTORY_DAYS, UPSTOX_PARTS
from ..errors import ApiError
from ..upstox import MAX_AGE_S
from . import op
from .common import OWNER, answer, need, work

ASK = "The request was incomplete. Reload the page."
MAX_AGE_LIMIT_S = 3600


def _parts(message) -> list[str]:
    asked = message.get("parts")
    if asked is None:
        return list(UPSTOX_PARTS)
    if not isinstance(asked, list) or not asked or not all(a in UPSTOX_PARTS for a in asked):
        raise ApiError(400, "bad_request", ASK)
    return [a for a in UPSTOX_PARTS if a in asked]


def _max_age(message) -> int:
    value = message.get("max_age")
    if value is None:
        return MAX_AGE_S
    if value.__class__ is not int or not 0 <= value <= MAX_AGE_LIMIT_S:
        raise ApiError(400, "bad_request", ASK)
    return value


@op("upstox_book")
async def upstox_book(ctx, message):
    parts, age = _parts(message), _max_age(message)
    reader = ctx.ensure_upstox()
    book = await reader.book(parts, age)
    return serialize.out(OWNER, book)


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
    now = ctx.clock()

    def read() -> list[dict[str, Any]]:
        return store.read(OWNER, lambda tx: tx.rows("upstox_eod", "day"))

    rows = (await work(read))[-limit:]
    days = [_day(ctx, row, now) for row in rows]
    note = None if days else "No closing record has been taken yet. The first is taken at 15:55."
    return serialize.out(OWNER, {"days": days, "note": note})


def _day(ctx, row: dict[str, Any], now: int) -> dict[str, Any]:
    """One closing record. It stays provisional until the next trading day's settle time; it is
    late when it was taken more than half an hour after the close time of its day."""
    from ..upstox import minute_of_day

    day = to_date(row["day"])
    settled_at = _next_trading_day(ctx.ensure_calendar(), day)
    settle_ms = _settle_ms(settled_at, ctx.settings.settle_at_min)
    taken = day_of_instant(row["read_at"])
    late = taken != row["day"] or minute_of_day(row["read_at"]) > LATE_AFTER_MIN
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
    }


#: A record taken after 16:25 IST (30 minutes after the 15:55 target) was not taken on time.
LATE_AFTER_MIN = 16 * 60 + 25


def _settle_ms(day: date, settle_at_min: int) -> int:
    from ..core.days import IST_OFFSET_MS, MS_PER_DAY

    midnight = (day.toordinal() - date(1970, 1, 1).toordinal()) * MS_PER_DAY - IST_OFFSET_MS
    return midnight + settle_at_min * 60_000
