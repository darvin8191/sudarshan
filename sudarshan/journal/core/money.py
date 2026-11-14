"""Money arithmetic of the journal (JA1): integers, half-up, no float.

A price is ``price_u`` (rupees x 10^4, cross-design Y31) and a quantity an integer, so
``qty x price_u`` is exact in 10^-4 rupee ("u"). A figure becomes paise once, at the end, half-up
to the paisa (a tie goes away from zero, as ``Decimal`` ROUND_HALF_UP does). Sums are of exact
values, so no rounded average ever reaches a gross.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction

from sudarshan.journal.core import kinds

PRICE_SCALE = kinds.PRICE_SCALE
#: u per paisa.
U_PER_PAISE = kinds.VALUE_DIVISOR
IST = timezone(timedelta(seconds=kinds.IST_OFFSET_S))


def div_half_up(num: int, den: int) -> int:
    """``num / den`` rounded half away from zero, in integers (``den`` positive)."""
    if den <= 0:
        raise ValueError("the divisor must be positive")
    sign = -1 if num < 0 else 1
    return sign * ((abs(num) * 2 + den) // (2 * den))


def value_u(qty: int, price_u: int) -> int:
    """Exact value of ``qty`` at ``price_u``, in 10^-4 rupee."""
    return qty * price_u


def u_to_paise(value: int) -> int:
    """A value in 10^-4 rupee as paise, half-up."""
    return div_half_up(value, U_PER_PAISE)


def avg_price_u(total_value_u: int, qty: int) -> int:
    """The average price (``price_u``) of ``qty`` bought or sold for ``total_value_u``, half-up."""
    return div_half_up(total_value_u, qty)


def ratio_str(num: int | Fraction, den: int | Fraction, places: int) -> str:
    """``num / den`` as a Decimal string with ``places`` decimals, half-up, from exact fractions."""
    frac = Fraction(num) / Fraction(den)
    return fraction_str(frac, places)


def fraction_str(frac: Fraction, places: int) -> str:
    """An exact fraction as a fixed-place string, half-up (a tie goes away from zero)."""
    scaled = frac * (10**places)
    n = div_half_up(scaled.numerator, scaled.denominator)
    sign = "-" if n < 0 else ""
    digits = str(abs(n)).rjust(places + 1, "0")
    if places == 0:
        return sign + digits
    return f"{sign}{digits[:-places]}.{digits[-places:]}"


def decimal_str(value: Decimal, places: int) -> str:
    """A Decimal as a fixed-place string, half-up."""
    quantum = Decimal(1).scaleb(-places)
    return str(value.quantize(quantum, rounding=ROUND_HALF_UP))


# ---- days and instants (IST, no clock) -------------------------------------------------------
def to_date(day: int) -> date:
    """A YYYYMMDD day as a date."""
    return date(day // 10000, day // 100 % 100, day % 100)


def to_day(d: date) -> int:
    """A date as YYYYMMDD."""
    return d.year * 10000 + d.month * 100 + d.day


def ist_day(ms: int) -> int:
    """The IST calendar day of an instant, as YYYYMMDD."""
    return to_day(datetime.fromtimestamp(ms // 1000, tz=IST).date())


def day_start_ms(day: int) -> int:
    """The first instant (epoch ms) of an IST day given as YYYYMMDD."""
    d = to_date(day)
    return int(datetime(d.year, d.month, d.day, tzinfo=IST).timestamp()) * 1000


def ist_clock(ms: int) -> tuple[int, int, int]:
    """Hour, minute and second of an instant in IST."""
    t = datetime.fromtimestamp(ms // 1000, tz=IST)
    return t.hour, t.minute, t.second


def calendar_days_between(first_day: int, last_day: int) -> int:
    """Calendar days from one YYYYMMDD day to a later one."""
    return (to_date(last_day) - to_date(first_day)).days
