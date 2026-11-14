"""Goals (M7.34; design p7 4.8): how far a target is, and what to add each month to reach it.

Pure: every input is an argument; "today" is the as-of day the caller names.

A goal is a target amount on a target day, counted against what its linked tabs are worth now (or
an amount typed by hand), growing at a yearly return the owner assumes. With ``r`` that return:

    i = (1 + r) ^ (1 / 12) - 1          the same return by the month
    g = (1 + i) ^ months                what a rupee held now becomes by the target day
    need = target - value now x g       what the money already there will not reach

``need`` at or below nothing means the goal is on track without adding more, and the amount to add
is 0, never below it. Otherwise the amount is ``need x i / (g - 1)``, paid at each month's end
(at no return: ``need / months``), rounded **up** to the whole rupee, so that paying it does reach
the target. ``months`` is the whole months from the as-of day to the target day, and at least 1: a
goal due this month, or overdue, asks for all of what is missing at once.

The arithmetic is Decimal at 40 digits in a context of its own; the answer is whole paise.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_HALF_EVEN, Context, Decimal, localcontext

from .kinds import PAISE_PER_RUPEE
from .money import pct

#: The yearly return a goal assumes until the owner sets one: 10.00% (owner decision 13).
DEFAULT_RATE_BP = 1000
BP = 10_000
_CONTEXT = Context(prec=40, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999, traps=[])
#: Thirty of the forty digits are kept before rounding up, so that an amount that is a whole
#: number of rupees is not pushed a rupee higher by the last digits of a root.
_KEPT = Decimal(1).scaleb(-30)


def months_left(as_of_day: int, target_day: int) -> int:
    """Whole months from ``as_of_day`` to ``target_day`` (days as YYYYMMDD): 14 Nov 2026 to
    31 Mar 2031 is 52; a month is whole on the same day of the month. 0 when less than a month is
    left, or none."""
    months = (target_day // 10000 - as_of_day // 10000) * 12
    months += target_day // 100 % 100 - as_of_day // 100 % 100
    if target_day % 100 < as_of_day % 100:
        months -= 1
    return max(0, months)


def required_monthly(target_paise: int, value_now_paise: int, months: int, rate_bp: int) -> int:
    """What to add at each month's end to reach the target, in paise (a whole number of rupees,
    rounded up); 0 when what is there already grows to the target. ``months`` below 1 counts as
    1; ``rate_bp`` is the assumed return in basis points a year, not below 0."""
    months = max(1, months)
    with localcontext(_CONTEXT):
        yearly = 1 + Decimal(rate_bp) / BP
        monthly = yearly ** (Decimal(1) / 12) - 1 if rate_bp else Decimal(0)
        growth = (1 + monthly) ** months
        need = target_paise - value_now_paise * growth
        if need <= 0:
            return 0
        each = need * monthly / (growth - 1) if monthly else need / months
        rupees = (each / PAISE_PER_RUPEE).quantize(_KEPT).to_integral_value(ROUND_CEILING)
        return int(rupees) * PAISE_PER_RUPEE


def figures(
    target_paise: int, value_now_paise: int, as_of_day: int, target_day: int, rate_bp: int
) -> dict:
    """What a ``Goal`` (types.ts) says beside what the owner typed: the value now, the progress
    (not capped at 100: the bar is the phone's), the amount to add a month, the months left and
    whether it is on track without adding more."""
    months = months_left(as_of_day, target_day)
    monthly = required_monthly(target_paise, value_now_paise, months, rate_bp)
    return {
        "value_now_paise": value_now_paise,
        "progress_pct": pct(value_now_paise, target_paise),
        "required_monthly_paise": monthly,
        "months_left": months,
        "on_track": monthly == 0,
    }
