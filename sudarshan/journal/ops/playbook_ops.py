"""``playbooks_list``, ``playbook_create`` and ``playbook_update`` (JB4, M14.5: playbooks and
their checklists; the default checklist is the seeded playbook "General")."""

from __future__ import annotations

from ..book import create_playbook, update_playbook
from ..views import done, playbook_out
from . import op
from .common import OWNER, flag, need, text, whole, work

FIELDS = ("name", "name_alt", "description", "rules", "default_risk_paise", "archived")


@op("playbooks_list")
async def playbooks_list(ctx, args):
    store = need(ctx)
    rows = await work(lambda: store.read(OWNER, lambda tx: tx.rows("playbook", order="name,id")))
    return [playbook_out(r) for r in rows]


@op("playbook_create")
async def playbook_create(ctx, args):
    store = need(ctx)
    changes = {k: args[k] for k in FIELDS if k in args and k != "archived"}
    request_id = text(args, "request_id", limit=64, default=None) or None
    now = ctx.clock()
    row = await work(
        lambda: store.write(OWNER, lambda tx: create_playbook(tx, changes, request_id, now))
    )
    return done(row["id"], row["version"])


@op("playbook_update")
async def playbook_update(ctx, args):
    store = need(ctx)
    playbook_id = whole(args, "id", low=1)
    version = whole(args, "version", low=1)
    changes = {k: args[k] for k in FIELDS if k in args}
    if "archived" in changes:
        changes["archived"] = flag(args, "archived")
    now = ctx.clock()
    row = await work(
        lambda: store.write(
            OWNER, lambda tx: update_playbook(tx, playbook_id, version, changes, now)
        )
    )
    return done(row["id"], row["version"])
