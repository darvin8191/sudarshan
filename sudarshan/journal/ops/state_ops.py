"""``state``: what the journal holds today, for the page's header (JB2; the ``State`` record).

Counts of the live journal only: today's fills, the open trades, and when the last close check
read the day's book. Nothing here is money.
"""

from __future__ import annotations

from ..core import kinds
from ..core.money import ist_day
from . import op
from .common import OWNER, need, work


@op("state")
async def state(ctx, args):
    store = need(ctx)
    today = ist_day(ctx.clock())

    def read(tx):
        return {
            "enabled": True,
            "fills_today": tx.count("fill", mode="live", day=today),
            "open_trips": tx.count("round_trip", mode="live", status="open"),
            "last_reconcile_ms": tx.max_of("day_close", "reconciled_at", mode="live"),
            "limits": dict(kinds.LIMITS),
        }

    return await work(lambda: store.read(OWNER, read))
