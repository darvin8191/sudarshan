"""``nudge``: the alerts' "orders changed" (JB3', cross-design Y17). Answered at once; the read it
starts runs in the service's own loop (``nudge.py``)."""

from __future__ import annotations

from ..core import kinds
from ..nudge import nudges_for
from . import op
from .common import choice, flag, whole


@op("nudge")
async def nudge(ctx, args):
    choice(args, "kind", kinds.NUDGE_KINDS)
    sandbox = flag(args, "sandbox")
    whole(args, "at", low=0)
    nudges_for(ctx).note(sandbox)
    return {"id": None, "version": None, "words": None}
