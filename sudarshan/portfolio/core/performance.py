"""Money put in, absolute return, XIRR, time-weighted return and the year's figures (M7.30, M7.38).

What a tab has earned, in the numbers the Performance view and the dashboard card show. Money is
paise, every percentage is text of four decimals (money.pct) and nothing is a float. The one number
that cannot be exact, a yearly rate that solves an equation, is found in Decimal to far more digits
than it is shown with.

**The money flows** (design 4.7) are read from the replayed ledger's journal, one ``Flow`` for each
day on which any moved:

    purchase, rights issue          in    amount + charges
    sale                            out   amount - charges
    dividend, interest              out   the gross amount (TDS is tax paid, not a loss)
    shares brought in from outside  in    the value entered (the cost unless a value was given)
    shares taken out to outside     out   the value entered (the cost unless a value was given)
    fee on its own                  fee   a cost paid from outside: neither in nor out
    transfer between accounts, split, bonus shares: nothing moved

* Absolute return = (value + out - in - fee) / (in + fee). Net money put in = in + fee - out, so
  value less net money put in is the total gain, and that over (in + fee) is the same return.
* **XIRR** is the yearly rate r with sum(cash x (1 + r) ^ (-days / 365)) = 0 over the cash of each
  day (out - in - fee, then the value now on the valuation day), counting actual days over 365 as
  Excel does. With q = (1 + r) ^ (-1 / 365) that is a polynomial in q, and the solver only ever
  adds, multiplies and raises to a whole power in Decimal. It looks outward from 0% on both sides
  in steps of the same size and takes the first rate that fits, so if several do, the one nearest
  zero (on the log scale) wins; it halves the bracket it finds until it is narrower than 1e-17 of q.
  It is shown only when the valuation day is at least a year after the first day with cash.
* **TWR** multiplies the returns of the periods between complete days (a day with a closing value
  for everything): (value + out - fee) / (value before + in), money in working from the start of
  the period and money out leaving at its end. A day that is not complete joins the next complete
  day's period with its flows. A period with nothing at work is skipped, and one cannot lose more
  than everything in it. The product is exact (whole numbers, one division at the end).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from typing import NamedTuple

from . import words
from .days import add_days, days_between, financial_year, month_of
from .kinds import XIRR_MIN_DAYS
from .model import Ledger
from .money import pct, pct_of_rate

#: The arithmetic of the solver, whatever the process was told: 40 digits, no surprises.
_CONTEXT = Context(
    prec=40,
    rounding=ROUND_HALF_EVEN,
    Emin=-999999,
    Emax=999999,
    traps=[InvalidOperation, DivisionByZero, Overflow],
)
#: Actual days over 365, as Excel counts; also how long a time-weighted return must run to be
#: annualised.
_YEAR = 365
#: The solver stops when the bracket of q is narrower than this fraction of q: about 4e-15 of a
#: yearly rate, a million times finer than the four decimals of a percent it is shown with.
_NARROW = Decimal("1e-17")
_HALVINGS = 120
#: How far from 0% it looks, as a share of the days spanned: the log of the rate reaches about
#: +/-23, a loss of 99.99999999% a year or a gain of ten billion times. Beyond that, no fit.
_REACH_NUMERATOR, _REACH_DENOMINATOR = 5, 8
#: Past the first few steps each step is this many percent longer than the one before.
_STRIDE_PERCENT = 3
#: Two rates whose distances from 0% differ by less than this fraction are equally near: the gain
#: wins (the bisection has noise of 1e-17, so an exact tie must not be left to it).
_TIE = Decimal("1e-12")


# ---------------------------------------------------------------- money flows


@dataclass(frozen=True, slots=True)
class Flow:
    """The money of one day (design 4.7), in paise."""

    day: int
    in_paise: int = 0
    out_paise: int = 0
    fee_paise: int = 0

    @classmethod
    def of(cls, row: Mapping) -> Flow:
        return cls(row["day"], row["in_paise"], row["out_paise"], row["fee_paise"])

    def as_dict(self) -> dict:
        return asdict(self)

    @property
    def moved(self) -> bool:
        return bool(self.in_paise or self.out_paise or self.fee_paise)

    @property
    def cash_paise(self) -> int:
        """What the day does to the owner's pocket: money out less money in and fees."""
        return self.out_paise - self.in_paise - self.fee_paise


class Sums(NamedTuple):
    """Flows added up."""

    in_paise: int = 0
    out_paise: int = 0
    fee_paise: int = 0

    @property
    def put_in(self) -> int:
        """What the owner paid in, fees included: the base of the absolute return."""
        return self.in_paise + self.fee_paise

    @property
    def net_put_in(self) -> int:
        """Money put in less money taken out: what is still at work, or lost, or gained."""
        return self.in_paise + self.fee_paise - self.out_paise


def flows_of(ledger: Ledger) -> list[Flow]:
    """The money flows of a replayed tab, oldest day first, a day with none left out."""
    days: dict[int, list[int]] = {}
    for move in ledger.journal:
        entry, kind = move.entry, move.kind
        paid = received = fee = 0
        if kind in ("BUY", "RIGHTS"):
            paid = move.paise + entry.charges_paise
        elif kind == "SELL":
            received = move.paise - entry.charges_paise
        elif kind in ("DIVIDEND", "INTEREST"):
            received = move.paise
        elif kind == "FEE":
            fee = move.paise
        elif kind == "TRANSFER":
            if entry.account_id is None:
                paid = move.paise
            elif entry.to_account_id is None:
                received = move.paise
        if paid or received or fee:
            total = days.setdefault(move.day, [0, 0, 0])
            total[0] += paid
            total[1] += received
            total[2] += fee
    return [Flow(day, *total) for day, total in sorted(days.items())]


def totals(flows: Iterable[Flow], through: int | None = None) -> Sums:
    """The flows up to and including day ``through`` (all of them when None) added up."""
    live = [flow for flow in flows if through is None or flow.day <= through]
    return Sums(
        sum(flow.in_paise for flow in live),
        sum(flow.out_paise for flow in live),
        sum(flow.fee_paise for flow in live),
    )


def first_flow_day(flows: Iterable[Flow]) -> int | None:
    """The first day on which any money moved."""
    return min((flow.day for flow in flows if flow.moved), default=None)


def absolute_return(value_paise: int, sums: Sums) -> str | None:
    """(value + out - in - fee) / (in + fee) as a percentage; None when nothing was put in."""
    return pct(value_paise + sums.out_paise - sums.put_in, sums.put_in)


# ---------------------------------------------------------------- XIRR


def cashflows(flows: Iterable[Flow], value_paise: int, valuation_day: int) -> list[tuple[int, int]]:
    """The signed cash of each day for the XIRR: out less in and fees, and the value now on the
    valuation day. A day with none is left out, and so is a flow after the valuation day."""
    cash: dict[int, int] = {}
    for flow in flows:
        if flow.day <= valuation_day:
            cash[flow.day] = cash.get(flow.day, 0) + flow.cash_paise
    if value_paise:
        cash[valuation_day] = cash.get(valuation_day, 0) + value_paise
    return sorted((day, paise) for day, paise in cash.items() if paise)


def _both_ways(cash: Sequence[tuple[int, int]]) -> bool:
    """Whether some day pays and some day receives: without both there is no rate."""
    return any(paise < 0 for _, paise in cash) and any(paise > 0 for _, paise in cash)


def _solve(days: Sequence[int], paise: Sequence[int]) -> Decimal | None:
    """The yearly rate nearest zero that makes the cash add up to nothing, or None.

    ``days`` counts from the first cash (0, then rising) and ``paise`` is the cash of each. The sum
    is a polynomial in q = (1 + r) ^ (-1 / 365), summed from the last term back (Horner's rule) so
    that each power is of a gap in days.
    """
    total = sum(paise)
    if total == 0:
        return Decimal(0)
    with localcontext(_CONTEXT):
        gaps = [after - before for before, after in zip(days, days[1:], strict=False)]
        cash = [Decimal(each) for each in paise]

        def npv(q: Decimal) -> Decimal:
            acc = cash[-1]
            for gap, each in zip(reversed(gaps), reversed(cash[:-1]), strict=True):
                acc = each + acc * q**gap
            return acc

        def narrow(near: Decimal, f_near: Decimal, far: Decimal) -> Decimal:
            """Halve the bracket from ``near`` (where the sum is ``f_near``) to ``far``."""
            for _ in range(_HALVINGS):
                if abs(far - near) <= _NARROW * min(near, far):
                    break
                mid = (near + far) / 2
                f_mid = npv(mid)
                if f_mid == 0:
                    return mid
                if (f_mid > 0) == (f_near > 0):
                    near, f_near = mid, f_mid
                else:
                    far = mid
            return (near + far) / 2

        step_q = 1 + Decimal(1) / (10 * days[-1])  # about a tenth of the log rate over the span
        # Each side: where it has looked to, the sum there, and what a step does to q (a higher
        # rate is a lower q).
        sides = [[Decimal(1), Decimal(total), 1 / step_q], [Decimal(1), Decimal(total), step_q]]
        reach = days[-1] * _REACH_NUMERATOR // _REACH_DENOMINATOR + 1
        found: list[Decimal] = []
        index = 0
        while not found and index < reach:
            step = max(1, index * _STRIDE_PERCENT // 100)
            index += step
            for side in sides:
                before, f_before = side[0], side[1]
                side[0] = before * side[2] ** step
                side[1] = npv(side[0])
                if side[1] == 0:
                    found.append(side[0])
                elif (side[1] > 0) != (f_before > 0):
                    found.append(narrow(before, f_before, side[0]))
        if not found:
            return None
        # Nearest zero on the log scale: q above 1 is a loss, below 1 a gain, 1 / q the same size.
        # ``found`` has the gain side first, so a tie goes to the gain.
        away = [q if q >= 1 else 1 / q for q in found]
        best = found[next(i for i, far in enumerate(away) if far <= min(away) * (1 + _TIE))]
        return 1 / best**_YEAR - 1


def _merged(cash: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    by_day: dict[int, int] = {}
    for day, paise in cash:
        by_day[day] = by_day.get(day, 0) + paise
    return sorted((day, paise) for day, paise in by_day.items() if paise)


def _rate(cash: Sequence[tuple[int, int]]) -> Decimal | None:
    """The rate of cash already merged by day (or None); needs a day that pays and one that gets."""
    if not _both_ways(cash):
        return None
    first = cash[0][0]
    return _solve([days_between(first, day) for day, _ in cash], [paise for _, paise in cash])


def xirr_rate(cash: Iterable[tuple[int, int]]) -> Decimal | None:
    """The yearly rate of dated cash, ``(day, paise)`` with money paid negative, as a Decimal rate
    (0.099714 is 9.9714%). None without a day that pays and a day that gets, or when no rate fits."""
    return _rate(_merged(cash))


@dataclass(frozen=True, slots=True)
class Xirr:
    """The XIRR a tab shows (types.ts ``XirrFigure``)."""

    state: str
    pct: str | None = None
    from_day: int | None = None
    words: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def xirr(flows: Iterable[Flow], value_paise: int, valuation_day: int) -> Xirr:
    """The XIRR of a tab on ``valuation_day``: ``shown``; ``later`` until a year after the first
    flow (the absolute return stands in, and ``from_day`` says when it appears); or ``none``.

    Needing a purchase and a value is checked first, so a tab that has lost everything is never
    promised a figure it will not get. The year is counted from the first day with cash: a day
    whose purchases and sales cancel out (a round trip) starts nothing.
    """
    live = [flow for flow in flows if flow.day <= valuation_day]
    cash = cashflows(live, value_paise, valuation_day)
    if not _both_ways(cash):
        return Xirr("none", words=words.XIRR_NEEDS_A_PURCHASE)
    first = cash[0][0]
    if days_between(first, valuation_day) < XIRR_MIN_DAYS:
        appears = add_days(first, XIRR_MIN_DAYS)
        return Xirr("later", from_day=appears, words=words.xirr_later(appears))
    rate = _rate(cash)
    if rate is None:
        return Xirr("none", words=words.XIRR_NO_RATE)
    with localcontext(_CONTEXT):
        return Xirr("shown", pct=pct_of_rate(rate))


def summary(flows: Iterable[Flow], value_paise: int, valuation_day: int) -> dict:
    """The money figures of a tab's PerformanceReport on ``valuation_day``, from its flows and
    the value now: what was put in and taken out, the gain, the absolute return and the XIRR."""
    live = [flow for flow in flows if flow.day <= valuation_day]
    sums = totals(live)
    return {
        "valuation_day": valuation_day,
        "first_flow_day": first_flow_day(live),
        "value_paise": value_paise,
        "money_in_paise": sums.in_paise,
        "money_out_paise": sums.out_paise,
        "fee_paise": sums.fee_paise,
        "net_put_in_paise": sums.net_put_in,
        "total_gain_paise": value_paise - sums.net_put_in,
        "absolute_return_pct": absolute_return(value_paise, sums),
        "xirr": xirr(live, value_paise, valuation_day).as_dict(),
    }


# ---------------------------------------------------------------- TWR


@dataclass(frozen=True, slots=True)
class Point:
    """One day of the daily series: its closing value (None when the day is not complete) and the
    money that moved on it."""

    day: int
    value_paise: int | None
    in_paise: int = 0
    out_paise: int = 0
    fee_paise: int = 0

    @classmethod
    def of(cls, row: Mapping) -> Point:
        return cls(
            row["day"],
            row["value_paise"],
            row.get("in_paise", 0),
            row.get("out_paise", 0),
            row.get("fee_paise", 0),
        )


class Period(NamedTuple):
    """One stretch between two complete days: it began at the close of ``start`` (or, for the
    first, when money first went to work) and ended at the close of ``day``. Its growth is
    ``grown / base``: what it ended with over what it began with."""

    start: int
    day: int
    base: int
    grown: int

    @property
    def pct(self) -> str:
        return pct(self.grown - self.base, self.base)


def _by_day(points: Iterable[Point]) -> list[Point]:
    """The points oldest first, two for one day made one (flows added, the later value kept)."""
    merged: dict[int, Point] = {}
    for point in sorted(points, key=lambda each: each.day):
        old = merged.get(point.day)
        if old is not None:
            point = Point(
                point.day,
                point.value_paise if point.value_paise is not None else old.value_paise,
                old.in_paise + point.in_paise,
                old.out_paise + point.out_paise,
                old.fee_paise + point.fee_paise,
            )
        merged[point.day] = point
    return list(merged.values())


def periods(points: Iterable[Point], opening_paise: int | None = None) -> list[Period]:
    """The returns between complete days, oldest first.

    ``opening_paise`` is the value before the first point: 0 for a tab that was empty, so that the
    first complete day's own return counts. With None the first complete day is where the TWR
    begins and the money before it is not part of it.
    """
    found: list[Period] = []
    value, base_day = opening_paise, None
    money_in = money_out = fees = 0
    window = None  # the first day since the last complete one
    for point in _by_day(points):
        if window is None:
            window = point.day
        money_in += point.in_paise
        money_out += point.out_paise
        fees += point.fee_paise
        if point.value_paise is None:
            continue
        base = None if value is None else value + money_in
        if base is not None and base > 0:
            grown = max(0, point.value_paise + money_out - fees)
            found.append(Period(window if base_day is None else base_day, point.day, base, grown))
        value, base_day, window = point.value_paise, point.day, None
        money_in = money_out = fees = 0
    return found


@dataclass(frozen=True, slots=True)
class Twr:
    """The time-weighted return a tab shows (types.ts ``TwrFigure``)."""

    start_day: int | None = None
    pct: str | None = None
    annualised_pct: str | None = None
    words: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


def _ratio(top: int, bottom: int) -> Decimal:
    """``top / bottom`` as a Decimal of the solver's precision, for whole numbers of any size.

    The product of a long series has tens of thousands of digits, and turning a whole number that
    long into a Decimal takes time that grows with its square: fifteen years of daily returns
    cost a second and thirty cost half a minute. So the quotient is taken in whole numbers first,
    to ninety digits or more (a power of two as the scale, which a Decimal holds exactly), and
    only that short number becomes a Decimal.
    """
    shift = max(0, 300 - (top.bit_length() - bottom.bit_length()))
    return Decimal((top << shift) // bottom) / Decimal(1 << shift)


def twr(points: Iterable[Point], opening_paise: int | None = None) -> Twr:
    """The product of the periods' growth, less one, with its start; annualised only when it spans
    more than 365 days. Nothing until there is a period."""
    found = periods(points, opening_paise)
    if not found:
        return Twr(words=words.TWR_NEEDS_PRICES)
    grown = base = 1
    for period in found:
        grown *= period.grown
        base *= period.base
    start, end = found[0].start, found[-1].day
    span = days_between(start, end)
    annual = None
    if span > _YEAR:
        with localcontext(_CONTEXT):
            rate = _ratio(grown, base) ** (Decimal(_YEAR) / Decimal(span)) - 1
            annual = pct_of_rate(rate)
    return Twr(start, pct(grown - base, base), annual, words.twr_since(start))


# ---------------------------------------------------------------- the year's figures


@dataclass(frozen=True, slots=True)
class Fy:
    """A financial year's realised gain, income and fees (types.ts ``FyFigure``)."""

    fy: int
    label: str
    realised_paise: int
    income_paise: int
    fees_paise: int

    def as_dict(self) -> dict:
        return asdict(self)


def by_financial_year(ledger: Ledger) -> list[Fy]:
    """Realised gain, income (gross) and fees for each financial year (April to March) that has
    any, oldest first. Charges on a trade are in its gain, not in the fees."""
    years: dict[int, list[int]] = {}
    for move in ledger.journal:
        kind = move.kind
        if kind == "SELL":
            slot, paise = 0, move.gain_paise
        elif kind in ("DIVIDEND", "INTEREST"):
            slot, paise = 1, move.paise
        elif kind == "FEE":
            slot, paise = 2, move.paise
        else:
            continue
        years.setdefault(financial_year(move.day), [0, 0, 0])[slot] += paise
    return [
        Fy(fy, f"FY {fy}-{(fy + 1) % 100:02d}", *total)
        for fy, total in sorted(years.items())
        if any(total)
    ]


def income_report(ledger: Ledger, valuation_day: int) -> dict:
    """Income (dividends and interest, gross) this financial year and in each of the twelve months
    that end with the valuation day's, oldest first, months with none shown as 0."""
    year, month = valuation_day // 10000, valuation_day // 100 % 100
    months = {}
    for back in range(11, -1, -1):
        number = year * 12 + month - 1 - back
        months[number // 12 * 100 + number % 12 + 1] = 0
    this_fy = 0
    for move in ledger.journal:
        if move.kind not in ("DIVIDEND", "INTEREST") or move.day > valuation_day:
            continue
        if financial_year(move.day) == financial_year(valuation_day):
            this_fy += move.paise
        if month_of(move.day) in months:
            months[month_of(move.day)] += move.paise
    return {
        "this_fy_paise": this_fy,
        "by_month": [{"month": number, "paise": paise} for number, paise in months.items()],
    }
