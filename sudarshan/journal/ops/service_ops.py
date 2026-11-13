"""``reconcile_now`` and ``family_day`` (JB7; design 7.2, cross-design Y16).

* ``reconcile_now``: the owner's "Check now": the day's trades read once through the portfolio and
  the sandbox's from sandbox.db, as the close check reads them, at most once a minute. It only
  adds: what Upstox no longer lists is taken out by the close check after the close, never in
  the middle of a session from one read.
* ``family_day``: counts only, for the family service (``JOURNAL_FAMILY_INFO``): the live trades
  closed on a day, and how many of them won or lost by their net after the charges estimate. No
  money, no price, no quantity: the family service words its line itself.
"""

from __future__ import annotations

from ..capture import capture_for
from ..core.words import plural
from ..errors import ApiError
from ..views import done
from . import op
from .common import OWNER, need, whole, work

RECONCILE_GAP_MS = 60_000
TOO_SOON = "The journal read the day's trades less than a minute ago. Try again in a minute."
DAY_LOW, DAY_HIGH = 19_700_101, 29_991_231


@op("reconcile_now")
async def reconcile_now(ctx, args):
    need(ctx)
    now = ctx.clock()
    last = ctx.parts.get("reconcile_at")
    if isinstance(last, int) and now - last < RECONCILE_GAP_MS:
        raise ApiError(422, "refused", TOO_SOON)
    ctx.parts["reconcile_at"] = now
    capture = capture_for(ctx)
    live = await capture.read_live("close_reconcile", withdraw=False)
    if live.state != "ok":
        raise ApiError(503, "unavailable", live.words or "Upstox could not be read just now.")
    await capture.read_sandbox("sandbox_book")
    found = live.new + live.back
    words = (
        f"The day's trades were read: {plural(found, 'more fill', 'more fills')} found."
        if found
        else "The day's trades were read: the journal already had every fill."
    )
    return done(None, None, words)


@op("family_day")
async def family_day(ctx, args):
    store = need(ctx)
    day = whole(args, "day", low=DAY_LOW, high=DAY_HIGH)

    def read(tx):
        trips = tx.rows("round_trip", mode="live", status="closed", close_day=day)
        nets = [int(t["net_paise"] or 0) for t in trips]
        return {"day": day, "trades": len(trips), "won": sum(1 for n in nets if n > 0),
                "lost": sum(1 for n in nets if n < 0)}  # fmt: skip

    return await work(lambda: store.read(OWNER, read))
