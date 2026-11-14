"""Corporate actions, expected dividends and the calendar (phase P7, lane C: M7.13, M7.29, M7.36).

Owner ops only (none is a family or a journal op), each behind ``SUDARSHAN_PORTFOLIO_INSIGHTS``
(``ops.switched``). ``ca_list`` shows Upstox's events with the owner's decisions and the bonuses
and splits waiting for his one tap; ``ca_decide`` is that tap. Neither reads Upstox by itself: the
events come from the nightly read (``corpactions.py``), except one instrument a minute that the
owner asks to be read again (``isin``). Nothing here writes a ledger: a tab gets a split or a bonus
through the entry form, and ``ca_decide`` only records that it did.
"""

from __future__ import annotations

from typing import Any

from .. import corpactions
from ..core.days import add_days, day_of_instant, is_day
from ..core.kinds import CALENDAR_NEXT_DAYS, CALENDAR_PAST_DAYS
from ..errors import ApiError
from . import op
from .common import OWNER, answer, need, whole

ASK = "The request was incomplete. Reload the page."


def _span(message, today: int) -> tuple[int, int]:
    """The days asked for; without them the calendar's reach back and on."""
    start = message.get("from") or add_days(today, -CALENDAR_PAST_DAYS)
    end = message.get("to") or add_days(today, CALENDAR_NEXT_DAYS)
    if not is_day(start) or not is_day(end) or start > end:
        raise ApiError(400, "bad_request", ASK)
    return start, end


def _scope(message) -> str | None:
    scope = message.get("scope")
    if scope is not None and not (isinstance(scope, str) and 0 < len(scope) <= 24):
        raise ApiError(400, "bad_request", ASK)
    return scope


def _held_now(ctx) -> list[dict[str, Any]]:
    return corpactions.ensure(ctx)._held_now()


@op("ca_list")
async def ca_list(ctx, message):
    store, now = need(ctx), ctx.clock()
    today = day_of_instant(now)
    start, end = _span(message, today)
    scope, held = _scope(message), _held_now(ctx)
    isin = message.get("isin")
    if isinstance(isin, str):  # the owner opened one instrument: read it again, once a minute
        await corpactions.ensure(ctx).read_now(isin, now)
    return await answer(
        lambda: store.read(
            OWNER,
            lambda tx: corpactions.listing(
                tx, today, scope=scope, from_day=start, to_day=end, held_now=held
            ),
        )
    )


@op("ca_decide")
async def ca_decide(ctx, message):
    store, now = need(ctx), ctx.clock()
    event_id = whole(message, "id")
    args = {key: message.get(key) for key in ("scope", "state", "ratio_num", "ratio_den",
                                              "version", "request_id")}  # fmt: skip
    keep = ("id", "event_id", "scope", "state", "ratio_num", "ratio_den", "activity_id", "version")
    return await answer(
        lambda: store.write(
            OWNER, lambda tx: corpactions.decide(tx, event_id, args, day_of_instant(now), now)
        ),
        lambda row: {key: row[key] for key in keep},
    )
