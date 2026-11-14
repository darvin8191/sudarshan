"""Capital gains on listed shares and equity fund units (M7.27): one disposal, then the year.

For one disposal: the sale value, less the cost (for a purchase before 1 Feb 2018, the higher of
the cost and the lower of the 31 Jan 2018 value and the sale value), less the sale's expenses
without STT. Long-term when sold after the first anniversary of the purchase. The rate is the row
in force on the day of sale. A buyback follows the regime of its day. A disposal that cannot be
worked out is returned with its reason and is in no total: it is never costed at zero.
"""

from __future__ import annotations

from . import rules
from .model import Disposal, Gain, Settings
from .units import add_years, value_paise

SHORT, LONG = "short", "long"


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
