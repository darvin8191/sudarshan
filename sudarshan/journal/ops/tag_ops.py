"""``tags_list``, ``tag_create`` and ``tag_update`` (JB4, M14.4: mistake tags and labels)."""

from __future__ import annotations

from ..book import create_tag, update_tag
from ..core import kinds
from ..views import done, tag_out
from . import op
from .common import OWNER, choice, flag, need, text, whole, work

REQUEST_ID = 64


@op("tags_list")
async def tags_list(ctx, args):
    store = need(ctx)
    rows = await work(lambda: store.read(OWNER, lambda tx: tx.rows("tag", order="kind,name,id")))
    return [tag_out(r) for r in rows]


@op("tag_create")
async def tag_create(ctx, args):
    store = need(ctx)
    kind = choice(args, "kind", kinds.TAG_KINDS)
    name = text(args, "name", limit=200)
    colour = choice(args, "colour", kinds.TAG_COLOURS, "neutral")
    request_id = text(args, "request_id", limit=REQUEST_ID, default=None) or None
    now = ctx.clock()
    row = await work(
        lambda: store.write(OWNER, lambda tx: create_tag(tx, kind, name, colour, request_id, now))
    )
    return done(row["id"], row["version"])


@op("tag_update")
async def tag_update(ctx, args):
    store = need(ctx)
    tag_id = whole(args, "id", low=1)
    version = whole(args, "version", low=1)
    changes = {}
    if "name" in args:
        changes["name"] = text(args, "name", limit=200)
    if "colour" in args:
        changes["colour"] = choice(args, "colour", kinds.TAG_COLOURS)
    if "archived" in args:
        changes["archived"] = flag(args, "archived")
    now = ctx.clock()
    row = await work(
        lambda: store.write(OWNER, lambda tx: update_tag(tx, tag_id, version, changes, now))
    )
    return done(row["id"], row["version"])
