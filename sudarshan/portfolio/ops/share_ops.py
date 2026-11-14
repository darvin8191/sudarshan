"""Sharing and privacy (M7.37; cross-design Y6): the owner's ops that write them, and the one read
op of the family purpose that says what a member was given.

``share_set``, ``share_remove``, ``shares_list`` and ``member_privacy_set`` open under the owner's
purpose only (core/kinds.SHARING_OPS): the owner's Family page sends them through OpenAlgo's
portfolio bridge, from his own browser and in the same action as it writes the family service's
own record, so the portfolio holds the floor the family purpose applies. ``shares_for_viewer`` is
the family purpose's: the member's own tabs.
"""

from __future__ import annotations

from typing import Any

from .. import serialize, sharing
from ..core.kinds import PRIVACY_CATEGORIES
from . import op
from .common import OWNER, answer, caller, need, work


def _lone(message) -> Any:
    return message.get("version")


@op("share_set")
async def share_set(ctx, message):
    store, now = need(ctx), ctx.clock()
    return await answer(
        lambda: store.write(
            OWNER,
            lambda tx: sharing.share_set(
                tx,
                message.get("tab_id"),
                message.get("viewer"),
                message.get("level"),
                message.get("expires_at"),
                _lone(message),
                now,
            ),
        ),
        serialize.share,
    )


@op("share_remove")
async def share_remove(ctx, message):
    store = need(ctx)
    await work(
        lambda: store.write(
            OWNER,
            lambda tx: sharing.share_remove(
                tx, message.get("tab_id"), message.get("viewer"), _lone(message)
            ),
        )
    )
    return {"removed": True}


@op("shares_list")
async def shares_list(ctx, message):
    store = need(ctx)
    found = await work(
        lambda: store.read(
            OWNER,
            lambda tx: (
                sharing.shares_list(tx, message.get("tab_id"), message.get("viewer")),
                [
                    serialize.member_privacy(r)
                    for r in tx.rows("member_privacy", "member")
                    if message.get("viewer") in (None, r["member"])
                ],
            ),
        )
    )
    shares, members = found
    return {"shares": [serialize.share(r) for r in shares], "privacy": members}


@op("member_privacy_set")
async def member_privacy_set(ctx, message):
    store, now = need(ctx), ctx.clock()
    row = await work(
        lambda: store.write(
            OWNER,
            lambda tx: sharing.privacy_set(
                tx,
                message.get("member"),
                message.get("role"),
                message.get("shown"),
                _lone(message),
                now,
            ),
        )
    )
    return serialize.member_privacy(row)


@op("shares_for_viewer")
async def shares_for_viewer(ctx, message):
    """The tabs shared with the member asking, their levels and expiry, and which categories are
    hidden from them (a list of names, so that the answer carries no field a mask removes)."""
    store, who = need(ctx), caller()
    access = who.access
    tabs = await work(
        lambda: store.read(
            OWNER,
            lambda tx: {t["id"]: t for t in tx.rows("tab", "position,id")},
        )
    )
    shared = []
    for share in access.shares:
        tab = tabs.get(share["tab_id"])
        if tab is not None and not tab["archived"]:
            shared.append(
                {
                    "tab_id": tab["id"],
                    "name": tab["name"],
                    "kind": tab["kind"],
                    "level": share["level"],
                    "expires_at": share["expires_at"],
                }
            )
    hides = [c for c in PRIVACY_CATEGORIES if c in access.hidden]
    return serialize.out(who.scope, {"tabs": shared, "hides": hides, "member": access.member})
