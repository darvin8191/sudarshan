"""Prices in paise (M10.2): every price and level counted in hundredths, compared as integers.

NSE revises cash and stock-futures tick sizes every month by price band (since 15 Apr 2025:
below ₹250 0.01, ₹250-1,000 0.05, ₹1,000-5,000 0.10, ₹5,000-10,000 0.50, ...), and the band
edges are round numbers, exactly where traders put levels, while an alert lives 60 days. A tick
size frozen when the alert was set would snap a price from a finer tick onto the level: 249.98
read on a 0.05 grid is 250.00, and "crosses above 250" would fire below it. So the engine counts
in paise (hundredths of a rupee, or of an index point), the finest step every covered segment
trades in: NSE, BSE, NFO, BFO and their indices; options move in 0.05, a whole number of paise.

A feed price becomes paise with ``round(v * 100)``: feed prices sit on the paisa grid up to float
noise (249.98 * 100 is 24997.999999999996). A level converts once, exactly, with Decimal: an
upward edge rounds up to the next paisa and a downward edge down, so "crosses above 100.03"
fires at the first price at or above 100.03 whatever the tick is that day. Every compare after
that is between integers, which avoids the float traps (0.15 / 0.05 is 2.9999999999999996, 100 *
1.02 is 102.00000000000001).

The instrument's tick size is kept only for the "Checks at ₹100.05 (nearest tick)" hint. Currency
derivatives (CDS, BCD: ticks of 0.0025) trade below a paisa: spec refuses a tick that is not a
whole number of paise, and a finer unit is needed before such a segment is accepted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation

#: One paisa (or 0.01 of an index point): the unit every price and level is counted in.
PAISA = Decimal("0.01")
PER_RUPEE = 100
#: Indices have no tick size in the master; their values move in hundredths.
INDEX_TICK = Decimal("0.01")


def to_decimal(value) -> Decimal | None:
    """A finite Decimal from a JSON number or typed text ("25,900.50"); None for anything else."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, float):
        value = repr(value)  # the shortest text that reads back as this float: 0.15 -> '0.15'
    elif isinstance(value, str):
        value = value.strip().replace(",", "")
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def paise(value) -> int | None:
    """A feed price in paise; None unless it is a finite number of at least one paisa (a value
    under half a paisa, 0.004, is no price)."""
    kind = value.__class__  # exact: a bool is not a price
    if (kind is float or kind is int) and 0 < value < math.inf:
        count = round(value * PER_RUPEE)
        return count if count else None
    return None


def edge_up(level: Decimal) -> int:
    """An upward edge in paise: the first paisa at or above ``level``."""
    return int((level * PER_RUPEE).to_integral_value(ROUND_CEILING))


def edge_down(level: Decimal) -> int:
    """A downward edge in paise: the last paisa at or below ``level``."""
    return int((level * PER_RUPEE).to_integral_value(ROUND_FLOOR))


def rupees(amount: int) -> Decimal:
    """Paise back to the exact price with two decimals: 25003 -> 250.03."""
    return (Decimal(amount) / PER_RUPEE).quantize(PAISA)


@dataclass(frozen=True, slots=True)
class Grain:
    """One instrument's tick size as the master contract gave it when the alert was set.

    Only for the hint of where a level off the tick checks; the engine counts in paise."""

    step: Decimal

    @classmethod
    def of(cls, tick_size) -> Grain | None:
        step = to_decimal(tick_size)
        if step is None or step <= 0:
            return None
        return cls(step.normalize())

    @property
    def whole_paise(self) -> bool:
        """True when every price on this tick is a whole number of paise (0.05, 0.10, 1)."""
        return (self.step * PER_RUPEE) % 1 == 0

    def up(self, level: Decimal) -> Decimal:
        """The first price on this tick at or above ``level``."""
        return (level / self.step).to_integral_value(ROUND_CEILING) * self.step

    def down(self, level: Decimal) -> Decimal:
        """The last price on this tick at or below ``level``."""
        return (level / self.step).to_integral_value(ROUND_FLOOR) * self.step
