"""Sales matched to purchases for the tax pack (review H1): by date, not by trade.

The tax pack lists a trade under the kind of trade and the holding period of each sale, in the
financial year of that sale. A trade the journal pairs (``core/pairing.py``) runs until its
position is back at 0, so a delivery position bought once and sold in parts across a year end is
one trade closed in the later year; the tax pack needs each sale on its own. This module does
that from a trade's legs, as Indian brokers' profit and loss statements do:

1. Each day's buys and sales of the position are matched with each other first, up to the smaller
   quantity: bought and sold the same day (shares: intraday, whatever the product), at the day's
   average buying and selling prices.
2. The rest of a day's sales are matched first in, first out against what is held: each matched
   quantity is a lot with its own purchase day, at the day's average selling price.
3. The rest of a day's buys are held, at the day's average buying price.

A lot is "held over 12 months" when its sale date is after its purchase date plus 12 calendar
months (bought 10 Mar 2027 and sold 9 Mar 2028 is not, across 29 Feb), never by counting seconds.
A short position (a derivative) is the mirror: sales first, then the buys that cover them.

Charges follow the orders: each day's charges on one side (``charges``, keyed by day and action)
are shared among the lots that side of that day made, by quantity. Everything is exact (fractions
of 10^-4 rupee and of a paisa) until a total is rounded half-up once.

Pure: legs in, lots out; nothing here reads a clock or a file.
"""

from __future__ import annotations

import calendar
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from fractions import Fraction
from typing import Any

from sudarshan.journal.core import money


@dataclass(frozen=True)
class Lot:
    """One quantity bought (or sold short) on ``open_day`` and closed on ``close_day``."""

    qty: int
    open_day: int
    close_day: int
    entry_value_u: Fraction
    exit_value_u: Fraction
    side: str
    charges_paise: Fraction

    @property
    def same_day(self) -> bool:
        return self.open_day == self.close_day

    @property
    def gross_u(self) -> Fraction:
        diff = self.exit_value_u - self.entry_value_u
        return diff if self.side == "long" else -diff


def plus_months(day: int, months: int) -> int:
    """``day`` plus calendar months; a day the later month lacks is its last (29 Feb + 12 months
    is 28 Feb)."""
    d = money.to_date(day)
    index = d.month - 1 + months
    year, month = d.year + index // 12, index % 12 + 1
    last = calendar.monthrange(year, month)[1]
    return money.to_day(date(year, month, min(d.day, last)))


def held_over_12_months(open_day: int, close_day: int) -> bool:
    """True when the sale date is after the purchase date plus 12 calendar months."""
    return close_day > plus_months(open_day, 12)


def _day_groups(legs: Iterable[Mapping[str, Any]]) -> list[tuple[int, dict[str, list[int]]]]:
    """Per day, in order: the quantity and exact value bought and sold."""
    days: dict[int, dict[str, list[int]]] = {}
    for leg in legs:
        side = days.setdefault(int(leg["day"]), {"BUY": [0, 0], "SELL": [0, 0]})
        part = side[str(leg["action"]).upper()]
        part[0] += int(leg["qty"])
        part[1] += money.value_u(int(leg["qty"]), int(leg["price_u"]))
    return sorted(days.items())


def lots_of(legs: Iterable[Mapping[str, Any]], side: str = "long",
            charges: Mapping[tuple[int, str], int] | None = None) -> list[Lot]:  # fmt: skip
    """The lots of one trade, from its legs: each leg has ``day``, ``action`` (BUY or SELL),
    ``qty`` (the leg's own quantity) and ``price_u``. ``side`` is the trade's: a long's entries
    are buys, a short's sales. ``charges`` maps (day, action) to the paise its orders' charges
    came to. The lots of a trade still open are those sold so far."""
    opening, closing = ("BUY", "SELL") if side == "long" else ("SELL", "BUY")
    fees = charges or {}
    held: deque[list[Any]] = deque()  # [qty, day, value per unit, charges per unit]
    out: list[Lot] = []
    for day, parts in _day_groups(legs):
        in_qty, in_value = parts[opening]
        out_qty, out_value = parts[closing]
        in_unit = Fraction(in_value, in_qty) if in_qty else Fraction(0)
        out_unit = Fraction(out_value, out_qty) if out_qty else Fraction(0)
        in_fee = Fraction(fees.get((day, opening), 0), in_qty) if in_qty else Fraction(0)
        out_fee = Fraction(fees.get((day, closing), 0), out_qty) if out_qty else Fraction(0)
        same = min(in_qty, out_qty)
        if same:
            out.append(Lot(same, day, day, in_unit * same, out_unit * same, side,
                           (in_fee + out_fee) * same))  # fmt: skip
        selling = out_qty - same
        while selling and held:
            lot = held[0]
            take = min(selling, lot[0])
            out.append(Lot(take, lot[1], day, lot[2] * take, out_unit * take, side,
                           (lot[3] + out_fee) * take))  # fmt: skip
            lot[0] -= take
            selling -= take
            if not lot[0]:
                held.popleft()
        if in_qty - same:
            held.append([in_qty - same, day, in_unit, in_fee])
    return out


def paise(value: Fraction) -> int:
    """An exact amount of paise, rounded half-up once."""
    return money.div_half_up(value.numerator, value.denominator)


def u_paise(value_u: Fraction) -> int:
    """An exact value in 10^-4 rupee as paise, rounded half-up once."""
    scaled = value_u / money.U_PER_PAISE
    return money.div_half_up(scaled.numerator, scaled.denominator)
