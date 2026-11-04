"""``mf_search``: schemes from AMFI's list by name, category, AMC or scheme code (M7.24, 5.4)."""

from __future__ import annotations

from typing import Any

from .. import serialize
from ..amfi import WORDS
from ..core.allocation import mf_class
from ..core.kinds import MF_SEARCH_MAX
from ..errors import ApiError
from . import op
from .common import OWNER, need, work

MAX_QUERY = 100


@op("mf_search")
async def mf_search(ctx, message):
    store = need(ctx)
    text = message.get("q", "")
    limit = message.get("limit")
    if not isinstance(text, str) or len(text) > MAX_QUERY:
        raise ApiError(400, "bad_request", "The request was incomplete. Reload the page.")
    if limit is None:
        limit = MF_SEARCH_MAX
    if limit.__class__ is not int or not 1 <= limit <= MF_SEARCH_MAX:
        raise ApiError(400, "bad_request", "The request was incomplete. Reload the page.")
    words = text.split()
    if not ctx.settings.amfi:
        return serialize.out(OWNER, {"items": [], "note": WORDS["off"]})

    def read() -> tuple[list[dict[str, Any]], int]:
        def run(tx):
            return (tx.mf_search(words, limit) if words else []), tx.mf_count()

        return store.read(OWNER, run)

    found, total = await work(read)
    items = [
        {
            "amfi_code": r["amfi_code"],
            "name": r["name"],
            "category": r["category"],
            "amc": r["amc"],
            "asset_class": mf_class(r["category"]),
            "nav_u": r["nav_u"],
            "nav_day": r["nav_date"],
        }
        for r in found
    ]
    note = WORDS["empty"] if total == 0 else None
    return serialize.out(OWNER, {"items": items, "note": note})
