"""Loss set-off and carry-forward (M7.27, M7.28): within the year, then brought forward.

Capital losses: a short-term loss reduces short-term gain and then long-term gain; a long-term
loss reduces long-term gain only. The year's own losses go first, then losses brought forward,
oldest year first. A loss can be used for the rule's number of years after its own; past that it
is listed as lapsed and never used.
"""

from __future__ import annotations

from . import rules, words
from .model import Carry
from .units import fy_first, fy_label, rupees

SHORT, LONG = "short", "long"
CAPITAL = ("stcl", "ltcl")
#: The business buckets in the order a brought-forward business loss meets this year's profit.
BUSINESS = ("business", "speculative")


def last_fy(loss_fy: int, bucket: str) -> int:
    """The last financial year (by its starting year) a loss of ``loss_fy`` can be used in."""
    return loss_fy + rules.at(rules.CARRY_YEARS, fy_first(loss_fy))[bucket]


def carry_row(loss_fy: int, bucket: str, paise: int, source: str) -> dict:
    return {"loss_fy": loss_fy, "bucket": bucket, "paise": paise, "source": source,
            "last_fy": last_fy(loss_fy, bucket)}  # fmt: skip


def brought_forward(carries, fy: int, buckets: tuple[str, ...]) -> tuple[list[Carry], list, list]:
    """The earlier years' losses of these buckets usable in ``fy``, oldest year first and in the
    buckets' order within a year; the ones whose years have run out; and, where the owner entered
    a loss for a year and bucket that was also computed here, the computed figure, which is only
    shown beside the owner's and never used."""
    rows = [c for c in carries if c.bucket in buckets and c.loss_fy < fy and c.paise > 0]
    owned = {(c.taxpayer, c.loss_fy, c.bucket) for c in rows if c.source == "owner"}
    beside = [c for c in rows if c.source != "owner" and (c.taxpayer, c.loss_fy, c.bucket) in owned]
    rows = sorted(
        (c for c in rows if c not in beside), key=lambda c: (c.loss_fy, buckets.index(c.bucket))
    )
    usable = [c for c in rows if last_fy(c.loss_fy, c.bucket) >= fy]
    as_rows = [
        [carry_row(c.loss_fy, c.bucket, c.paise, c.source) for c in group]
        for group in ([c for c in rows if c not in usable], beside)
    ]
    return usable, *as_rows


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
    usable, lapsed, beside = brought_forward(carries, fy, CAPITAL)
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
    return {"steps": steps, "carried": carried, "lapsed": lapsed, "own_figures": beside}


def business_setoff(non_speculative_net: int, speculative_net: int, carries, fy: int) -> dict:
    """The two business buckets of a year after set-off.

    This year first: a loss on derivatives reduces intraday (speculative) profit; a speculative
    loss reduces nothing else. Then losses brought forward, oldest year first: speculative ones
    against speculative profit only, then business ones against what is left of derivative
    profit and then of speculative profit. A derivative loss still left can reduce this year's
    other income except salary (that is the CA's sum, not done here), and the rest is carried."""
    left = {"business": non_speculative_net, "speculative": speculative_net}
    steps, carried, lapsed, beside = [], [], [], []

    def use(loss: int, target: str, **step) -> int:
        used = min(loss, max(left[target], 0))
        if used:
            left[target] -= used
            steps.append({**step, "against": target, "paise": used})
        return loss - used

    if non_speculative_net < 0:
        left["business"] = -use(-non_speculative_net, "speculative", step="same_year",
                                bucket="business", loss_fy=fy)  # fmt: skip
    for bucket, targets in (("speculative", ("speculative",)), ("business", BUSINESS)):
        usable, gone, shown = brought_forward(carries, fy, (bucket,))
        lapsed += gone
        beside += shown
        for carry in usable:
            rest = carry.paise
            for target in targets:
                rest = use(
                    rest, target, step="brought_forward", bucket=bucket, loss_fy=carry.loss_fy
                )
            if rest:
                row = carry_row(carry.loss_fy, bucket, rest, carry.source)
                (carried if row["last_fy"] > fy else lapsed).append(row)
    carried += [
        carry_row(fy, bucket, -left[bucket], "computed") for bucket in BUSINESS if left[bucket] < 0
    ]
    return {
        "taxable": {"non_speculative_paise": max(left["business"], 0),
                    "speculative_paise": max(left["speculative"], 0)},
        "available_other_heads_paise": max(-left["business"], 0),
        "steps": steps, "carried": carried, "lapsed": lapsed, "own_figures": beside,
    }  # fmt: skip


def sentences(result: dict, fy: int) -> list[str]:
    """What a set-off did, in words: each step, what is carried and to when, what lapsed."""
    out = [
        words.set_off_step(step, rupees(step["paise"]), fy_label(step["loss_fy"]))
        for step in result["steps"]
    ]
    if result.get("available_other_heads_paise"):
        out.append(words.other_heads(rupees(result["available_other_heads_paise"])))
    for row in result["carried"]:
        if row["bucket"] == "speculative" and row["loss_fy"] == fy:
            out.append(words.SPECULATIVE_STAYS)
        years = row["last_fy"] - row["loss_fy"]
        label, last = fy_label(row["loss_fy"]), fy_label(row["last_fy"])
        out.append(words.carried_on(row, rupees(row["paise"]), label, last, years))
    for row in result["lapsed"]:
        label, last = fy_label(row["loss_fy"]), fy_label(row["last_fy"])
        out.append(words.lapsed(row, rupees(row["paise"]), label, last))
    out += [
        words.own_figure(row, rupees(row["paise"]), fy_label(row["loss_fy"]))
        for row in result["own_figures"]
    ]
    if any(step["step"] == "brought_forward" for step in result["steps"]):
        out.append(words.SET_OFF_ORDER)
    return out
