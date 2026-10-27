"""Tick grain (M10.2): prices in whole ticks, levels snapped once with Decimal, integer compares.

A price from the feed is snapped with ``round(v / g)``: feed prices sit on the grid up to float
noise, so 100.04999999999999 is 2001 ticks of 0.05. A level is converted once, exactly, with
Decimal: an upward edge rounds up to the grid and a downward edge rounds down, so a level off the
grid checks at the next tick in its direction ("Checks at ₹100.05 (nearest tick)"). Every compare
after that is between integers, which avoids the float traps: 0.15 / 0.05 is 2.9999999999999996
and 100 * 1.02 is 102.00000000000001.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation

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


@dataclass(frozen=True, slots=True)
class Grain:
    """One instrument's tick size, as Decimal for levels and as a float for feed prices."""

    step: Decimal
    size: float
    #: Decimals shown: the tick's own, never fewer than two (0.05 -> 2, 0.0025 -> 4, 1 -> 2).
    places: int

    @classmethod
    def of(cls, tick_size) -> Grain | None:
        step = to_decimal(tick_size)
        if step is None or step <= 0:
            return None
        step = step.normalize()
        return cls(step, float(step), max(2, -step.as_tuple().exponent))

    def up(self, level: Decimal) -> int:
        """An upward edge: the first tick at or above ``level``."""
        return int((level / self.step).to_integral_value(ROUND_CEILING))

    def down(self, level: Decimal) -> int:
        """A downward edge: the last tick at or below ``level``."""
        return int((level / self.step).to_integral_value(ROUND_FLOOR))

    def ticks(self, value) -> int | None:
        """A feed price in whole ticks; None unless it is a finite number above 0."""
        if not isinstance(value, int | float) or not 0 < value < math.inf:
            return None
        return round(value / self.size)

    def price(self, ticks: int) -> Decimal:
        """Whole ticks back to the exact price, with the grain's decimals: 2001 -> 100.05."""
        return (self.step * ticks).quantize(Decimal(1).scaleb(-self.places))
