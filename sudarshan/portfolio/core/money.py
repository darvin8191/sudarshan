"""Money, quantities and prices as integers (M7.26, cross-design X3).

The portfolio counts in paise, ``qty_u`` (units x 10^6) and ``price_u`` (rupees x 10^4) and divides
in exactly one place, ``round_div``, which rounds half away from zero: half a paisa is always a
paisa more, and a loss mirrors a gain. Python's ``round`` (half to even) and a float never touch a
figure the owner reads. The phone does the same sums with BigInt (valuation.ts) and both run the
vectors in tests/vectors/money.json.

* The value of a holding line is ``round(qty_u x price_u / 10^8)`` paise. Lines are rounded one by
  one and then added.
* The cost of part of a lot is ``round(remaining cost x part / remaining quantity)``, and the last
  unit takes exactly what is left, so a lot's cost is always used up exactly.
* A percentage is computed from integers, to four decimals, half away from zero, with a plain
  minus, and is never ``-0.0000``.
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal

from .kinds import (
    MAX_PAISE,
    MAX_PRICE_U,
    MAX_QTY_U,
    PAISE_PER_RUPEE,
    PCT_DECIMALS,
    PRICE_SCALE,
    VALUE_DIVISOR,
)

PCT_SCALE = 10**PCT_DECIMALS
QTY_DECIMALS = 6  # QTY_SCALE is 10^6
#: A price in paise times this is the same price as price_u (paise are 10^-2 rupees, price_u 10^-4).
PAISE_TO_PRICE_U = PRICE_SCALE // PAISE_PER_RUPEE


def is_int(value) -> bool:
    """A whole number as the store keeps it: not a float, not a bool."""
    return value.__class__ is int


def qty_ok(qty_u) -> bool:
    return is_int(qty_u) and 0 < qty_u <= MAX_QTY_U


def price_ok(price_u) -> bool:
    return is_int(price_u) and 0 <= price_u <= MAX_PRICE_U


def paise_ok(paise) -> bool:
    return is_int(paise) and 0 <= paise <= MAX_PAISE


def step_ok(qty_u: int, decimals: int) -> bool:
    """Whether a quantity has at most ``decimals`` decimals (shares 0, fund units 4, grams 3)."""
    return 0 <= decimals <= QTY_DECIMALS and qty_u % 10 ** (QTY_DECIMALS - decimals) == 0


def round_div(numerator: int, denominator: int) -> int:
    """``numerator / denominator`` as a whole number, half away from zero."""
    if denominator < 0:
        numerator, denominator = -numerator, -denominator
    whole, rest = divmod(abs(numerator), denominator)
    if 2 * rest >= denominator:
        whole += 1
    return whole if numerator >= 0 else -whole


def value_paise(qty_u: int, price_u: int) -> int:
    """What ``qty_u`` units are worth at ``price_u``; also an entry's amount from quantity x price."""
    return round_div(qty_u * price_u, VALUE_DIVISOR)


def unit_price_u(cost_paise: int, qty_u: int) -> int | None:
    """A cost per unit as a price (an average cost); None for no units."""
    return round_div(cost_paise * VALUE_DIVISOR, qty_u) if qty_u > 0 else None


def share_of_cost(cost_paise: int, part_qty_u: int, whole_qty_u: int) -> int:
    """The cost of ``part_qty_u`` of a lot that still holds ``whole_qty_u`` at ``cost_paise``.

    Taken from what remains of the lot, never from its original size, and the last unit takes the
    rest: three sales of one unit from a 10,000 paise lot of three cost 3,333, 3,334 and 3,333.
    """
    if part_qty_u == whole_qty_u:
        return cost_paise
    return round_div(cost_paise * part_qty_u, whole_qty_u)


def format_pct(scaled: int) -> str:
    """A percentage counted in ten-thousandths of a percent as text: 80000 -> "8.0000"."""
    whole, fraction = divmod(abs(scaled), PCT_SCALE)
    return f"{'-' if scaled < 0 else ''}{whole}.{fraction:0{PCT_DECIMALS}d}"


def pct(numerator: int, denominator: int) -> str | None:
    """``numerator / denominator`` as a percentage text; None when there is nothing to divide by."""
    if denominator == 0:
        return None
    return format_pct(round_div(numerator * 100 * PCT_SCALE, denominator))


def pct_of_rate(rate: Decimal) -> str:
    """A rate as a percentage text: Decimal("0.099714") -> "9.9714"."""
    scaled = (rate * 100 * PCT_SCALE).to_integral_value(ROUND_HALF_UP)
    return format_pct(int(scaled))


def feed_price_u(value) -> int | None:
    """A price from the feed as ``price_u``: ``paise(v) x 100``, the way the alerts count it.

    Identical to ``sudarshan.alerts.market.grain.paise`` (a test holds the two together, because
    this package may not import it): None unless it is a finite number of at least one paisa.
    """
    if (value.__class__ is float or value.__class__ is int) and 0 < value < math.inf:
        count = round(value * PAISE_PER_RUPEE)
        return count * PAISE_TO_PRICE_U if count else None
    return None
