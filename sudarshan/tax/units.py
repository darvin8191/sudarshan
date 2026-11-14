"""Integer money and calendar days for the tax core; the one place it divides.

The same rules as the portfolio's ledger (which this package may not import): a value is
``qty_u x price_u / 10^8`` paise rounded half away from zero, part of an amount is taken from what
remains so the last part takes the rest, and a total is shared by largest remainder so the shares
add up exactly. A day is an IST calendar day YYYYMMDD; a financial year is named by the year it
starts in (2026 is FY 2026-27).
"""

from __future__ import annotations

VALUE_DIVISOR = 10**8
BP = 10_000
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def round_div(numerator: int, denominator: int) -> int:
    """``numerator / denominator`` as a whole number, half away from zero."""
    whole, rest = divmod(abs(numerator), denominator)
    if 2 * rest >= denominator:
        whole += 1
    return whole if numerator >= 0 else -whole


def value_paise(qty_u: int, price_u: int) -> int:
    return round_div(qty_u * price_u, VALUE_DIVISOR)


def share_of(amount: int, part: int, whole: int) -> int:
    """The share of ``amount`` that ``part`` of the remaining ``whole`` takes; all of it at the end."""
    return amount if part == whole else round_div(amount * part, whole)


def bp_of(amount: int, bp: int) -> int:
    """``bp`` basis points of an amount (1250 is 12.5%)."""
    return round_div(amount * bp, BP)


def allocate(total: int, weights: list[int]) -> list[int]:
    """Share ``total`` by ``weights``: floors first, then one paisa each to the largest remainders
    (the earlier weight when they tie). All zero weights share nothing."""
    whole = sum(weights)
    if whole <= 0:
        return [0] * len(weights)
    shares = [total * weight // whole for weight in weights]
    order = sorted(range(len(weights)), key=lambda i: (-(total * weights[i] % whole), i))
    for i in order[: total - sum(shares)]:
        shares[i] += 1
    return shares


def add_years(day: int, count: int = 1) -> int:
    """The same date ``count`` years on; 29 February in a year without one becomes 28 February."""
    year, month, number = day // 10000 + count, day // 100 % 100, day % 100
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    if month == 2 and number == 29 and not leap:
        number = 28
    return year * 10000 + month * 100 + number


def fy_of(day: int) -> int:
    return day // 10000 if day // 100 % 100 >= 4 else day // 10000 - 1


def fy_first(fy: int) -> int:
    return fy * 10000 + 401


def fy_last(fy: int) -> int:
    return (fy + 1) * 10000 + 331


def fy_label(fy: int) -> str:
    return f"FY {fy}-{(fy + 1) % 100:02d}"


def day_text(day: int) -> str:
    """A day as a report writes it: 20260803 is "3 Aug 2026"."""
    return f"{day % 100} {MONTHS[day // 100 % 100 - 1]} {day // 10000}"


def rupees(paise: int) -> str:
    """Whole paise as a plain rupee figure for a sentence: 15500000 is "Rs 1,55,000"."""
    rupee, rest = divmod(abs(paise), 100)
    text = str(rupee)
    head, tail = text[:-3], text[-3:]
    while len(head) > 2:
        tail = head[-2:] + "," + tail
        head = head[:-2]
    text = (head + "," if head else "") + tail
    return ("-" if paise < 0 else "") + "Rs " + text + (f".{rest:02d}" if rest else "")
