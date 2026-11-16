"""The return of the Upstox account's shares, from its own trades and closing records (M7.15).

The Upstox tab is read-only: nobody enters what was paid, so the money comes from Upstox's trade
history (M7.6, the share segment) and the value from the closing records (M7.12). The figures are
then the custom tabs' own (``performance``: absolute return, XIRR from a year after the first
money, the time-weighted return; ``history``: the same money in an index). This module only turns
trades and holdings into what those take.

**What is counted, and what is said as not counted.** Upstox gives three financial years of
trades. A share held from before the first year read, or one that reached the account without a
trade (an allotment, a transfer in, a bonus nobody applied), has no purchase here. It is *not
counted*: its value is left out of the return and it is listed with its quantity, never taken as
bought at 0, which would show its whole value as gain.

* Per share, the trades of a day are netted: what was bought and sold the same day is a round
  trip, and its money counts on both sides. A net purchase adds counted units.
* The units not counted are taken as the oldest: a net sale uses them up first (first in, first
  out), and that part of the sale's money is left out too, pro rata by quantity. Only the part
  that sells counted units is money out.
* How many units were held from before is worked back from the holdings: what is held now, less
  what the trades and the applied bonuses and splits account for. A bonus is ``num`` new for
  every ``den`` held and a split every ``den`` become ``num``, on the ex-date, before that day's
  trades, for counted and not counted units alike.
* Where the trades leave more counted units than are held (shares moved or tendered outside the
  market, a merger), only what is held is valued and the share is flagged: the return is then
  lower than the true one, and the view says so.
* A trade Upstox lists without an ISIN cannot be matched to a holding: it is left out and counted.

**The time-weighted return** runs on everything the account holds, from the first closing
record: a record's value is the whole holdings, so every share trade since then is money in or
out. A closing record is taken minutes after the close, when that day's delivery trades may not
be in the holdings yet, so a day on which the net quantity of any share changed has no value of
its own: its money joins the period that ends at the next record, which holds it either way.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from fractions import Fraction
from typing import NamedTuple

from .money import round_div, value_paise
from .performance import Flow, Point


class Trade(NamedTuple):
    """One executed share trade (reports.py ``TradeRow``, the EQ segment)."""

    day: int
    isin: str | None
    buy: bool
    qty_u: int
    amount_paise: int

    @classmethod
    def of(cls, row: Mapping) -> Trade:
        return cls(
            row["trade_date"],
            row.get("isin") or None,
            row["transaction_type"] == "BUY",
            row["qty_u"],
            row["amount_paise"],
        )


class Action(NamedTuple):
    """A bonus or a split the owner applied (``core.corpactions.action_row``)."""

    day: int
    isin: str
    kind: str
    num: int
    den: int


class Held(NamedTuple):
    """What the account held of one share at the closing record, and its price there."""

    isin: str
    qty_u: int
    price_u: int


class Uncounted(NamedTuple):
    """Units of a share that are held and have no purchase in the trades read."""

    isin: str
    qty_u: int
    value_paise: int


class Counted(NamedTuple):
    """The money and the value the return of the counted shares rests on."""

    flows: list[Flow]
    value_paise: int
    uncounted: list[Uncounted]
    #: Shares the trades say are held in a larger quantity than the holdings show, by how much.
    gone: list[tuple[str, int]]
    #: Money of sales that sold units not counted: left out of the money taken out.
    left_out_paise: int
    #: Trades without an ISIN, left out.
    unmatched: int


class _Day(NamedTuple):
    day: int
    bought_u: int
    paid: int
    sold_u: int
    got: int


def _days(trades: Iterable[Trade]) -> dict[str, list[_Day]]:
    """Each share's trades, netted by day, oldest day first."""
    sums: dict[tuple[str, int], list[int]] = {}
    for trade in trades:
        if trade.isin is None:
            continue
        total = sums.setdefault((trade.isin, trade.day), [0, 0, 0, 0])
        at = 0 if trade.buy else 2
        total[at] += trade.qty_u
        total[at + 1] += trade.amount_paise
    found: dict[str, list[_Day]] = {}
    for (isin, day), total in sorted(sums.items()):
        found.setdefault(isin, []).append(_Day(day, *total))
    return found


def _after(units: int, action: Action) -> int:
    """Units after a bonus (new units rounded down, as the tax lots count them) or a split (to
    the nearest unit)."""
    if action.kind == "BONUS":
        return units + units * action.num // action.den
    return round_div(units * action.num, action.den)


def _steps(days: Sequence[_Day], actions: Sequence[Action]) -> list[tuple[int, int, object]]:
    """A share's actions and trading days in order, an action before the trades of its day."""
    mixed = [(a.day, 0, a) for a in actions] + [(d.day, 1, d) for d in days]
    return sorted(mixed, key=lambda step: (step[0], step[1]))


def opening_units(days: Sequence[_Day], actions: Sequence[Action], held_u: int) -> int:
    """How many units of a share were held before its first trade read, worked back from what is
    held now: never below nothing."""
    factor, known = Fraction(1), Fraction(0)
    for _, _, step in _steps(days, actions):
        if isinstance(step, Action):
            grow = Fraction(step.num, step.den)
            grow = grow + 1 if step.kind == "BONUS" else grow
            factor, known = factor * grow, known * grow
        else:
            known += step.bought_u - step.sold_u
    missing = held_u - known
    if missing <= 0 or factor <= 0:
        return 0
    before = missing / factor
    return round_div(before.numerator, before.denominator)


def _share(
    days: Sequence[_Day], actions: Sequence[Action], held_u: int
) -> tuple[dict[int, list[int]], int, int]:
    """One share replayed: ``(money by day as [in, out], counted units at the end, money of
    sales left out)``."""
    loose, counted, left_out = opening_units(days, actions, held_u), 0, 0
    money: dict[int, list[int]] = {}
    for _, _, step in _steps(days, actions):
        if isinstance(step, Action):
            loose, counted = _after(loose, step), _after(counted, step)
            continue
        paid, got = step.paid, step.got
        net = step.bought_u - step.sold_u
        if net >= 0:
            counted += net
        else:
            sold = -net
            from_loose = min(loose, sold)
            loose -= from_loose
            rest = sold - from_loose
            unknown = max(0, rest - counted)  # sold, and never seen bought: not counted either
            counted -= rest - unknown
            skipped = round_div(step.got * (from_loose + unknown), step.sold_u)
            got -= skipped
            left_out += skipped
        if paid or got:
            day = money.setdefault(step.day, [0, 0])
            day[0] += paid
            day[1] += got
    return money, counted, left_out


def counted(
    trades: Iterable[Trade],
    held: Iterable[Held],
    actions: Iterable[Action] = (),
    through: int | None = None,
) -> Counted:
    """The flows and the value of the shares that have a purchase in ``trades``.

    ``held`` is the closing record valued (one line per ISIN); ``through`` the last day whose
    trades that record holds (later trades and actions are left alone). Flows are oldest first.
    """
    live = [t for t in trades if through is None or t.day <= through]
    acts: dict[str, list[Action]] = {}
    for action in actions:
        if through is None or action.day <= through:
            acts.setdefault(action.isin, []).append(action)
    by_isin = _days(live)
    lines = {line.isin: line for line in held}
    money: dict[int, list[int]] = {}
    value = left_out = 0
    uncounted: list[Uncounted] = []
    gone: list[tuple[str, int]] = []
    for isin in sorted(set(by_isin) | set(lines)):
        line = lines.get(isin)
        held_u = line.qty_u if line else 0
        days, units, skipped = _share(by_isin.get(isin, ()), acts.get(isin, ()), held_u)
        left_out += skipped
        for day, (paid, got) in days.items():
            total = money.setdefault(day, [0, 0])
            total[0] += paid
            total[1] += got
        if units > held_u:
            gone.append((isin, units - held_u))
        if line is None:
            continue
        mine = min(units, held_u)
        value += value_paise(mine, line.price_u)
        if held_u > mine:
            rest = held_u - mine
            uncounted.append(Uncounted(isin, rest, value_paise(rest, line.price_u)))
    flows = [Flow(day, paid, got) for day, (paid, got) in sorted(money.items()) if paid or got]
    unmatched = sum(1 for t in live if t.isin is None)
    return Counted(flows, value, uncounted, gone, left_out, unmatched)


def delivery_days(trades: Iterable[Trade]) -> set[int]:
    """The days on which the net quantity of some share changed: a closing record of such a day
    may or may not hold the day's trades yet."""
    net: dict[tuple[str | None, int], int] = {}
    for trade in trades:
        key = (trade.isin, trade.day)
        net[key] = net.get(key, 0) + (trade.qty_u if trade.buy else -trade.qty_u)
    return {day for (_, day), units in net.items() if units}


def points(records: Iterable[tuple[int, int | None]], trades: Iterable[Trade]) -> list[Point]:
    """The daily series the time-weighted return of the whole holdings is worked out from.

    ``records`` are the closing records as ``(day, value of the holdings)``, None for a day whose
    holdings were not read. Every share trade from the first record on is money in or out on its
    day; a day whose delivery trades changed a quantity has no value of its own.
    """
    values = dict(records)
    if not values:
        return []
    first = min(values)
    live = [t for t in trades if t.day >= first]
    moved = delivery_days(live)
    money: dict[int, list[int]] = {}
    for trade in live:
        day = money.setdefault(trade.day, [0, 0])
        day[0 if trade.buy else 1] += trade.amount_paise
    found = []
    for day in sorted(set(values) | set(money)):
        paid, got = money.get(day, (0, 0))
        found.append(Point(day, None if day in moved else values.get(day), paid, got))
    return found


def last_quiet_day(days: Iterable[int], trades: Iterable[Trade], before: int) -> int | None:
    """The newest closing record to value the counted shares at: one from before ``before`` (the
    day the trades were last read, whose own trades may not be listed yet) and of a day on which
    no delivery trade changed a quantity, so that its holdings are the trades up to it."""
    moved = delivery_days(trades)
    fit = [day for day in days if day < before and day not in moved]
    return max(fit, default=None)
