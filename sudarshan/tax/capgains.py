"""Capital gains on listed shares and equity fund units (M7.27): one disposal, then the year.

For one disposal: the sale value, less the cost (for a purchase before 1 Feb 2018, the higher of
the cost and the lower of the 31 Jan 2018 value and the sale value), less the sale's expenses
without STT. Long-term when sold after the first anniversary of the purchase. The rate is the row
in force on the day of sale. A buyback follows the regime of its day. A disposal that cannot be
worked out is returned with its reason and is in no total: it is never costed at zero.
"""

from __future__ import annotations

from . import losses, rules
from .model import Disposal, Gain, Settings
from .units import add_years, allocate, bp_of, fy_first, fy_of, value_paise

SHORT, LONG = losses.SHORT, losses.LONG


def term_of(acquired_day: int, sale_day: int, boundary: str | None = None) -> str:
    """Long-term when held more than the rule's 12 months: sold after the anniversary of the
    purchase by calendar dates (29 Feb has its anniversary on 28 Feb), the anniversary itself
    still short-term, or already long-term on the other reading of the rule row."""
    line = add_years(acquired_day, rules.at(rules.LT_MONTHS, sale_day) // 12)
    if rules.choice(rules.LT_BOUNDARY, sale_day, boundary) == "on_anniversary":
        return LONG if sale_day >= line else SHORT
    return LONG if sale_day > line else SHORT


def gain_of(
    disposal: Disposal,
    fmv: dict[str, int],
    settings: Settings | None = None,
    expenses_paise: int | None = None,
) -> Gain:
    """Work one disposal out. ``fmv`` gives the highest price of 31 Jan 2018 per ISIN (price_u);
    ``expenses_paise`` is this sale's share of a year's non-STT charges where the caller
    allocated one, else the sale's own non-STT charges are used when known."""
    d, day = disposal, disposal.sale_day
    regime = rules.at(rules.BUYBACK, day) if d.kind_hint == "buyback" else None
    as_dividend = regime == "dividend_and_loss"
    value = 0 if as_dividend else d.sale_paise
    allocated = expenses_paise is not None and not as_dividend
    expenses = 0 if as_dividend else expenses_paise if allocated else d.charges_paise or 0
    cost, status, fmv_used, term, rate = d.cost_paise, "ok", False, None, None
    if cost is None:
        status = "unknown_cost"
    else:
        term = term_of(d.acquired_day, day, settings.lt_boundary if settings else None)
        rate = rules.at(rules.LTCG_RATE if term == LONG else rules.STCG_RATE, day)
        old = d.acquired_day < rules.at(rules.GRANDFATHER, day)["before"]
        if regime == "exempt":
            status = "exempt_buyback"
        elif old and value > cost:  # at or below cost the 2018 price cannot change the cost
            price = fmv.get(d.isin)
            if price is None:
                status = "needs_fmv"
            else:
                floor = min(value_paise(d.basis_qty_u, price), value)
                cost, fmv_used = max(cost, floor), floor > cost
    return Gain(
        d.taxpayer, d.account, d.isin, d.acquired_day, day, d.qty_u, d.sale_paise, value, cost,
        expenses, value - cost - expenses if status == "ok" else None, term, rate, status,
        fmv_used, allocated, regime, d.sale_paise if as_dividend else 0,
    )  # fmt: skip


def allocate_expenses(disposals, fy: int, total_paise: int) -> list[int]:
    """Share a year's non-STT share charges over that year's delivery sales by their value
    (largest remainder, so the shares add up to the total). A buyback and a sale of another year
    take nothing; the result is in the order of ``disposals``."""
    weights = [
        d.sale_paise if fy_of(d.sale_day) == fy and d.kind_hint != "buyback" else 0
        for d in disposals
    ]
    return allocate(total_paise, weights)


def year_schedule(gains, fy: int, carries=(), settings: Settings | None = None) -> dict:
    """A taxpayer's capital-gains schedule for one financial year from its worked disposals.

    Gains by term and rate; the year's losses and then brought-forward losses set off (in a year
    with two rates, against the pools in the exemption's order); the long-term exemption on what
    is left; the tax at the special rates with the cess as its own line; what is carried on."""
    rows = [g for g in gains if fy_of(g.sale_day) == fy]
    ok = [g for g in rows if g.status == "ok"]
    order = rules.choice(
        rules.EXEMPT_ORDER, fy_first(fy), settings.exempt_order if settings else None
    )
    buckets: dict[tuple, dict] = {}
    for g in ok:
        bucket = buckets.setdefault(
            (g.term, g.rate_bp),
            {
                "term": g.term,
                "rate_bp": g.rate_bp,
                "gain_paise": 0,
                "loss_paise": 0,
                "first": g.sale_day,
            },
        )
        bucket["gain_paise" if g.gain_paise > 0 else "loss_paise"] += abs(g.gain_paise)
        bucket["first"] = min(bucket["first"], g.sale_day)
    by_bucket = sorted(
        buckets.values(),
        key=lambda b: b["first"] if order == "chronological" else (-b["rate_bp"], b["first"]),
    )
    pools = [
        {"term": b["term"], "rate_bp": b["rate_bp"], "paise": b["gain_paise"]} for b in by_bucket
    ]
    totals = {
        f"{short}_{kind}_paise": sum(b[f"{kind}_paise"] for b in by_bucket if b["term"] == term)
        for term, short in ((SHORT, "st"), (LONG, "lt"))
        for kind in ("gain", "loss")
    }
    set_off = losses.capital_setoff(
        pools, totals["st_loss_paise"], totals["lt_loss_paise"], carries, fy
    )
    limit = rules.at(rules.LTCG_EXEMPT, fy_first(fy))
    exempt, by_rate = limit, []
    for pool in pools:
        used = min(exempt, pool["paise"]) if pool["term"] == LONG else 0
        if used:
            pool["paise"] -= used
            exempt -= used
            by_rate.append({"rate_bp": pool["rate_bp"], "paise": used})
    lines = [
        {"term": p["term"], "rate_bp": p["rate_bp"], "taxable_paise": p["paise"],
         "tax_paise": bp_of(p["paise"], p["rate_bp"])}
        for p in pools
    ]  # fmt: skip
    tax, cess_bp = sum(line["tax_paise"] for line in lines), rules.at(rules.CESS_BP, fy_first(fy))
    return {
        "fy": fy,
        "schedule": ok,
        "by_bucket": [{k: v for k, v in b.items() if k != "first"} for b in by_bucket],
        **totals,
        "set_off": set_off["steps"],
        "exemption": {
            "limit_paise": limit,
            "used_paise": limit - exempt,
            "order": order,
            "by_rate": by_rate,
        },
        "taxable": {
            "st_paise": sum(p["paise"] for p in pools if p["term"] == SHORT),
            "lt_paise": sum(p["paise"] for p in pools if p["term"] == LONG),
        },
        "indicative_tax": {
            "lines": lines,
            "tax_paise": tax,
            "cess_bp": cess_bp,
            "cess_paise": bp_of(tax, cess_bp),
        },
        "carried": set_off["carried"],
        "lapsed": set_off["lapsed"],
        "needs_fmv": [g for g in rows if g.status == "needs_fmv"],
        "unknown_cost": [g for g in rows if g.status == "unknown_cost"],
        "exempt_buybacks": [g for g in rows if g.status == "exempt_buyback"],
        "buyback_dividend_paise": sum(g.dividend_paise for g in rows),
    }
