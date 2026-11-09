"""The daily loss limit (M5.14): today's P&L in a pipe, and whether it has reached the limit.

The figure, live: Upstox's short-term positions, as the Upstox module's own cache returns them
(the read the gate makes anyway to tell an exit from a new order). Each row's ``realised`` plus
``unrealised`` is the day's P&L as Upstox documents it, but Upstox has been seen to return an
absurd ``unrealised`` on a carried row and zeroed P&L fields on a closed one, so each row's
figure is checked against the day's P&L from its own trades and prices:

    (day_sell_value - day_buy_value)
        + (quantity x last_price - overnight_quantity x close_price) x multiplier

When the two disagree, the row's own trades and prices are used and the figure is marked
approximate; so it is when a row lacks one of Upstox's two figures and that formula cannot be
worked out either (its ``pnl``, the position's net, is used then). Holdings are not in it, and
neither is delivery: a CNC row in cash (product D on NSE or BSE) is left out, so a dip in shares
bought to hold never locks F&O (the owner's decision). It is gross, before charges, like
services/risk.

The figure, sandbox: the sandbox's own book, today's realised P&L and its open positions' P&L,
its CNC cash positions left out the same way (readers.sandbox_day).

The decision is services.risk.evaluate_aggregate's combined stop, reused and never
reimplemented (CLAUDE.md "Risk rules live in services/risk/"): reaching the limit exactly counts.

The day is India's date. A lock written for it holds until that date ends, so an order sent that
evening (an AMO) is refused too. The figure is looked at only while the order's own segment is
in session, by the exchange's own hours (database/market_calendar_db: F&O to 15:40, cash to
15:30, MCX into the night; amendment R11), never by one 15:30 for every segment. Outside its
session a book can still show the day before (Upstox resets it in the morning), and a lock
written from it would hold a whole new day.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime

from services.risk.aggregate import evaluate_aggregate
from services.risk.models import AggregateRisk

from .store import IST


@dataclass(frozen=True)
class DayFigure:
    """Today's P&L in one pipe, gross."""

    realised: float
    unrealised: float
    approximate: bool = False  # a row had no day figures: its net P&L was used instead
    marked_at: datetime | None = None  # the sandbox: when its open positions were last marked

    @property
    def total(self) -> float:
        return self.realised + self.unrealised


def _number(value: object) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


#: Upstox's own figures for a row stand when they agree with its trades and prices to within a
#: rupee or a hundredth of the figure, whichever is more.
AGREE_RS = 1.0
AGREE_SHARE = 0.01
#: Upstox's cash segments: a product D row there is delivery (CNC).
CASH_SEGMENTS = frozenset({"NSE_EQ", "BSE_EQ"})
CASH_EXCHANGES = frozenset({"NSE", "BSE"})


def is_delivery(row: dict) -> bool:
    """A delivery (CNC) row: product D in cash, which the day's figure leaves out."""
    if str(row.get("product") or "").strip().upper() != "D":
        return False
    segment = str(row.get("instrument_token") or "").split("|", 1)[0].strip().upper()
    if segment:
        return segment in CASH_SEGMENTS
    return str(row.get("exchange") or "").strip().upper() in CASH_EXCHANGES


def day_from_trades(row: dict) -> float | None:
    """The row's P&L for the day from its own trades and prices, or None when a field the
    formula needs is missing. An intraday row (product I) carries nothing overnight."""
    sold, bought = _number(row.get("day_sell_value")), _number(row.get("day_buy_value"))
    quantity = _number(row.get("quantity"))
    overnight = _number(row.get("overnight_quantity"))
    if overnight is None and str(row.get("product") or "").strip().upper() == "I":
        overnight = 0.0
    if sold is None or bought is None or quantity is None or overnight is None:
        return None
    held = 0.0
    if quantity:
        last = _number(row.get("last_price"))
        if last is None:
            return None
        held += quantity * last
    if overnight:
        close = _number(row.get("close_price"))
        if close is None:
            return None
        held -= overnight * close
    multiplier = _number(row.get("multiplier"))
    if multiplier is None or multiplier <= 0:
        multiplier = 1.0  # NSE's F&O and cash rows are 1; a zeroed row says nothing else
    return round(sold - bought + held * multiplier, 2)


def _agrees(own: float, computed: float) -> bool:
    return abs(own - computed) <= max(AGREE_RS, AGREE_SHARE * max(abs(own), abs(computed)))


def _row_figure(row: dict) -> tuple[float, float, bool] | None:
    """One row's ``(realised, unrealised, approximate)``, or None when it says nothing."""
    realised, unrealised = _number(row.get("realised")), _number(row.get("unrealised"))
    closed = _number(row.get("quantity")) == 0
    if closed and realised is not None and unrealised is None:
        unrealised = 0.0  # a closed row holds nothing open
    computed = day_from_trades(row)
    if realised is not None and unrealised is not None:
        if computed is None or _agrees(realised + unrealised, computed):
            return realised, unrealised, False
    if computed is not None:
        return (computed, 0.0, True) if closed else (0.0, computed, True)
    net = _number(row.get("pnl"))
    if net is not None and net != 0:
        return 0.0, net, True
    if realised:
        return realised, 0.0, True
    if unrealised:
        return 0.0, unrealised, True
    return None


def live_figure(rows: list) -> DayFigure:
    """Today's P&L from Upstox's raw short-term position rows."""
    realised = unrealised = 0.0
    approximate = False
    for row in rows:
        if not isinstance(row, dict) or is_delivery(row):
            continue
        figure = _row_figure(row)
        if figure is None:
            continue
        realised += figure[0]
        unrealised += figure[1]
        approximate = approximate or figure[2]
    return DayFigure(realised, unrealised, approximate)


def reached(limit: float | None, figure: DayFigure) -> bool:
    """Whether today's figure has reached a loss of ``limit`` (at it counts)."""
    if limit is None:
        return False
    risk = AggregateRisk(combined_stoploss=float(limit))
    return evaluate_aggregate(risk, figure.realised, figure.unrealised).breached


def day_of(at: datetime) -> date:
    """India's date for an aware moment: the day a lock is written for."""
    return at.astimezone(IST).date()


def in_session(window: dict | None, at: datetime) -> bool:
    """Whether ``at`` falls in a segment's session window (market_calendar_db's epoch ms)."""
    if not window:
        return False
    now_ms = int(at.timestamp() * 1000)
    return int(window["start_ms"]) <= now_ms <= int(window["end_ms"])
