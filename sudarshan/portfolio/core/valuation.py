"""Value, Today and a tab's totals, from integers (M7.23, M7.24).

What a holding is worth now and how much of that is today's move: for one account's line, for a
holding (a row: one asset and every account that holds it) and for the tab. The service works them
out at its own prices; the phone works out the same sums as ticks arrive (valuation.ts), and both run
the vectors in tests/vectors/valuation.json, so the two cannot drift apart. ``Row.of`` and
``Fixed.of`` read the contract's JSON (``HoldingRow`` and ``TabFixed``) and ``as_dict`` writes
``TodayFigure`` and ``TabTotals`` back, so nothing is renamed on the way through.

* A line's value is ``round(qty_u x price_u / 10^8)`` paise. Lines are rounded one by one and then
  added: every sum on a screen is a sum of figures on that screen.
* A line's Today is ``value now - (open value + in - out)``, the open value rounded on its own.
  Its percentage is Today over ``open value + in``. Today needs the previous close only when
  units were held at the open, and the price only when units are held now; a figure it needs and
  does not have is "not known yet", never 0 (``unknown``).
* A holding with no price yet is counted at its cost, so one missing price does not look like a
  loss of everything in it; the tab says how many (``unpriced``).
* A holding sold out today is hidden from the list but its Today still counts, from ``Fixed``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .kinds import TODAY_MODES, TODAY_STATES
from .money import pct, value_paise

OK, UNKNOWN, PENDING, NONE = TODAY_STATES
MODE_OK, MODE_PENDING, MODE_NONE = TODAY_MODES


@dataclass(frozen=True, slots=True)
class Line:
    """One account's quantity of one asset (types.ts ``HoldingLine``)."""

    account_id: int
    qty_u: int
    open_qty_u: int
    cost_paise: int
    in_today_paise: int
    out_today_paise: int
    over_year_qty_u: int = 0

    @classmethod
    def of(cls, line: Mapping) -> Line:
        return cls(
            line["account_id"],
            line["qty_u"],
            line["open_qty_u"],
            line["cost_paise"],
            line["in_today_paise"],
            line["out_today_paise"],
            line.get("over_year_qty_u", 0),
        )

    def as_dict(self) -> dict:
        return {
            "account_id": self.account_id,
            "qty_u": self.qty_u,
            "open_qty_u": self.open_qty_u,
            "cost_paise": self.cost_paise,
            "in_today_paise": self.in_today_paise,
            "out_today_paise": self.out_today_paise,
            "over_year_qty_u": self.over_year_qty_u,
        }


@dataclass(frozen=True, slots=True)
class Row:
    """One asset with the price it is valued at and its lines (types.ts ``HoldingRow``)."""

    mode: str
    price_u: int | None
    prev_close_u: int | None
    lines: tuple[Line, ...]

    @classmethod
    def of(cls, row: Mapping) -> Row:
        price = row["price"]
        lines = tuple(Line.of(line) for line in row["lines"])
        return cls(row["today_mode"], price["price_u"], price["prev_close_u"], lines)

    @property
    def held(self) -> bool:
        """Whether any account holds units now; a row that does not is hidden from the list."""
        return any(line.qty_u for line in self.lines)


@dataclass(frozen=True, slots=True)
class Fixed:
    """The sums that do not move with a price (types.ts ``TabFixed``, less the liabilities)."""

    realised_paise: int = 0
    income_paise: int = 0
    fees_paise: int = 0
    money_in_paise: int = 0
    money_out_paise: int = 0
    #: Today from the holdings sold out today; None when one of them has no previous close.
    closed_today_paise: int | None = 0
    closed_today_base_paise: int = 0

    @classmethod
    def of(cls, fixed: Mapping) -> Fixed:
        return cls(
            fixed["realised_paise"],
            fixed["income_paise"],
            fixed["fees_paise"],
            fixed["money_in_paise"],
            fixed["money_out_paise"],
            fixed["closed_today_paise"],
            fixed["closed_today_base_paise"],
        )


@dataclass(frozen=True, slots=True)
class Today:
    """Today for a row or a tab (types.ts ``TodayFigure``): a figure when there is one."""

    state: str
    paise: int | None
    base_paise: int
    pct: str | None
    counted: int = 0
    unknown: int = 0
    pending: int = 0

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "paise": self.paise,
            "base_paise": self.base_paise,
            "pct": self.pct,
            "counted": self.counted,
            "unknown": self.unknown,
            "pending": self.pending,
        }


@dataclass(frozen=True, slots=True)
class RowFigures:
    """What a holding row shows: value, gain on cost and Today."""

    value_paise: int
    cost_paise: int
    unrealised_paise: int
    #: Gain over cost; None with no price (the row is counted at cost) or no cost (a bonus lot).
    gain_pct: str | None
    priced: bool
    today: Today

    def as_dict(self) -> dict:
        return {
            "value_paise": self.value_paise,
            "cost_paise": self.cost_paise,
            "unrealised_paise": self.unrealised_paise,
            "gain_pct": self.gain_pct,
            "priced": self.priced,
            "today": self.today.as_dict(),
        }


@dataclass(frozen=True, slots=True)
class Totals:
    """A tab at one set of prices (types.ts ``TabTotals``)."""

    value_paise: int
    cost_paise: int
    unrealised_paise: int
    overall_gain_paise: int
    absolute_return_pct: str | None
    today: Today
    #: Holdings counted at cost because they have no price yet.
    unpriced: int

    def as_dict(self) -> dict:
        return {
            "value_paise": self.value_paise,
            "cost_paise": self.cost_paise,
            "unrealised_paise": self.unrealised_paise,
            "overall_gain_paise": self.overall_gain_paise,
            "absolute_return_pct": self.absolute_return_pct,
            "today": self.today.as_dict(),
            "unpriced": self.unpriced,
        }


# ---- what the service decides before the sums ------------------------------------------------
def today_mode(asset_kind: str, basis: str | None, price_day: int | None, today: int) -> str:
    """What Today means for a holding: a listed stock always has one; a mutual fund only once its
    NAV is dated today (until AMFI publishes it, ``pending``); a value kept by hand has none."""
    if asset_kind == "listed":
        return MODE_OK
    if asset_kind == "mf" and basis == "nav":
        return MODE_OK if price_day == today else MODE_PENDING
    return MODE_NONE


def previous_close_u(
    price_u: int | None, prev_close_u: int | None, price_day: int | None, today: int
) -> int | None:
    """The close Today is measured from. A price from an earlier session (the market has not
    opened yet, or it is a holiday) is itself the latest close, so Today is only today's trades."""
    if price_u is not None and price_day is not None and price_day < today:
        return price_u
    return prev_close_u


# ---- lines and rows ---------------------------------------------------------------------------
def line_today(line: Line, price_u: int | None, prev_close_u: int | None) -> tuple[int, int] | None:
    """``(Today, base)`` of one line in paise; None when a price it needs is not known.

    The price is needed for the units held now, the previous close for the units held at the open.
    """
    if (line.qty_u and price_u is None) or (line.open_qty_u and prev_close_u is None):
        return None
    now = value_paise(line.qty_u, price_u) if line.qty_u else 0
    opened = value_paise(line.open_qty_u, prev_close_u) if line.open_qty_u else 0
    base = opened + line.in_today_paise
    return now - base + line.out_today_paise, base


def row_today(row: Row) -> Today:
    """Today for one holding: its lines added, or why there is no figure."""
    if row.mode == MODE_NONE:
        return Today(NONE, None, 0, None)
    if row.mode == MODE_PENDING:
        return Today(PENDING, None, 0, None, pending=1)
    paise = base = 0
    for line in row.lines:
        figure = line_today(line, row.price_u, row.prev_close_u)
        if figure is None:
            return Today(UNKNOWN, None, 0, None, unknown=1)
        paise += figure[0]
        base += figure[1]
    return Today(OK, paise, base, pct(paise, base), counted=1)


def row_figures(row: Row) -> RowFigures:
    cost = sum(line.cost_paise for line in row.lines)
    priced = row.price_u is not None
    if priced:
        value = sum(value_paise(line.qty_u, row.price_u) for line in row.lines)
    else:
        value = cost
    gain = value - cost
    return RowFigures(
        value, cost, gain, pct(gain, cost) if priced else None, priced, row_today(row)
    )


# ---- the tab ----------------------------------------------------------------------------------
def closed_today(rows: Iterable[Row]) -> tuple[int | None, int]:
    """``(Today, base)`` of the holdings sold out today, which the list no longer shows.

    None for the figure when one of them has no previous close: a figure with a holding missing
    from it would be wrong without saying so. A fund waiting for its NAV is left out, as in the
    list.
    """
    paise = base = 0
    for row in rows:
        if row.held:
            continue
        today = row_today(row)
        if today.state == UNKNOWN:
            return None, 0
        if today.state == OK:
            paise += today.paise
            base += today.base_paise
    return paise, base


def tab_today(todays: Iterable[Today], fixed: Fixed) -> Today:
    """A tab's Today from its listed holdings' figures and the holdings sold out today.

    ``counted``, ``unknown`` and ``pending`` count listed holdings only. A closed figure that is
    None is for the screen to say ("... not counting a holding sold today"). With nothing counted
    the state says the most useful reason: not known yet, else waiting for a NAV, else no Today.
    """
    paise = base = counted = unknown = pending = 0
    for today in todays:
        if today.state == OK:
            paise += today.paise
            base += today.base_paise
            counted += 1
        elif today.state == UNKNOWN:
            unknown += 1
        elif today.state == PENDING:
            pending += 1
    closed = fixed.closed_today_paise
    has_closed = closed is not None and (closed != 0 or fixed.closed_today_base_paise != 0)
    if has_closed:
        paise += closed
        base += fixed.closed_today_base_paise
    if counted or has_closed:
        state = OK
    elif unknown or closed is None:
        state = UNKNOWN
    elif pending:
        state = PENDING
    else:
        state = NONE
    if state != OK:
        return Today(state, None, 0, None, counted, unknown, pending)
    return Today(OK, paise, base, pct(paise, base), counted, unknown, pending)


def tab_totals(rows: Iterable[Row], fixed: Fixed) -> Totals:
    """The tab's figures for these rows (the holdings in the list) and the sums beside them.

    A row nobody holds is skipped, so passing every row of the tab is harmless: the holdings sold
    out today are already in ``fixed``.
    """
    value = cost = unpriced = 0
    todays = []
    for row in rows:
        if not row.held:
            continue
        figures = row_figures(row)
        value += figures.value_paise
        cost += figures.cost_paise
        if not figures.priced:
            unpriced += 1
        todays.append(figures.today)
    unrealised = value - cost
    overall = unrealised + fixed.realised_paise + fixed.income_paise - fixed.fees_paise
    put_in = fixed.money_in_paise + fixed.fees_paise
    absolute = pct(value + fixed.money_out_paise - put_in, put_in)
    return Totals(value, cost, unrealised, overall, absolute, tab_today(todays, fixed), unpriced)
