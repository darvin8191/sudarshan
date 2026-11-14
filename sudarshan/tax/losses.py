"""Loss set-off and carry-forward (M7.27, M7.28): within the year, then brought forward.

Capital losses: a short-term loss reduces short-term gain and then long-term gain; a long-term
loss reduces long-term gain only. The year's own losses go first, then losses brought forward,
oldest year first. A loss can be used for the rule's number of years after its own; past that it
is listed as lapsed and never used.
"""

from __future__ import annotations

from . import rules
from .model import Carry
from .units import fy_first

SHORT, LONG = "short", "long"
CAPITAL = ("stcl", "ltcl")


def last_fy(loss_fy: int, bucket: str) -> int:
    """The last financial year (by its starting year) a loss of ``loss_fy`` can be used in."""
    return loss_fy + rules.at(rules.CARRY_YEARS, fy_first(loss_fy))[bucket]


def carry_row(loss_fy: int, bucket: str, paise: int, source: str) -> dict:
    return {"loss_fy": loss_fy, "bucket": bucket, "paise": paise, "source": source,
            "last_fy": last_fy(loss_fy, bucket)}  # fmt: skip


def brought_forward(carries, fy: int, buckets: tuple[str, ...]) -> tuple[list[Carry], list[dict]]:
    """The earlier years' losses of these buckets usable in ``fy``, oldest year first and in the
    buckets' order within a year, and the ones whose years have run out."""
    rows = [c for c in carries if c.bucket in buckets and c.loss_fy < fy and c.paise > 0]
    rows.sort(key=lambda c: (c.loss_fy, buckets.index(c.bucket)))
    usable = [c for c in rows if last_fy(c.loss_fy, c.bucket) >= fy]
    lapsed = [carry_row(c.loss_fy, c.bucket, c.paise, c.source) for c in rows if c not in usable]
    return usable, lapsed


def _absorb(pools: list[dict], term: str, loss: int, steps: list, **step) -> int:
    """Set ``loss`` against the gains of one term, pool by pool in their order; what is left."""
    for pool in pools:
        used = min(loss, pool["paise"]) if pool["term"] == term else 0
        if used:
            pool["paise"] -= used
            loss -= used
            steps.append({**step, "against": term, "rate_bp": pool["rate_bp"], "paise": used})
    return loss


def capital_setoff(pools: list[dict], st_loss: int, lt_loss: int, carries, fy: int) -> dict:
    """Reduce the gain ``pools`` ({term, rate_bp, paise}, already in the order to use them) by
    the year's losses and then by those brought forward. The pools are changed in place."""
    steps: list[dict] = []
    left = _absorb(pools, SHORT, st_loss, steps, step="same_year", bucket="stcl", loss_fy=fy)
    st_left = _absorb(pools, LONG, left, steps, step="same_year", bucket="stcl", loss_fy=fy)
    lt_left = _absorb(pools, LONG, lt_loss, steps, step="same_year", bucket="ltcl", loss_fy=fy)
    usable, lapsed = brought_forward(carries, fy, CAPITAL)
    carried = []
    for carry in usable:
        step = {"step": "brought_forward", "bucket": carry.bucket, "loss_fy": carry.loss_fy}
        left = carry.paise
        if carry.bucket == "stcl":
            left = _absorb(pools, SHORT, left, steps, **step)
        left = _absorb(pools, LONG, left, steps, **step)
        if left:
            row = carry_row(carry.loss_fy, carry.bucket, left, carry.source)
            (carried if row["last_fy"] > fy else lapsed).append(row)
    carried += [
        carry_row(fy, bucket, paise, "computed")
        for bucket, paise in (("stcl", st_left), ("ltcl", lt_left))
        if paise
    ]
    return {"steps": steps, "carried": carried, "lapsed": lapsed}
