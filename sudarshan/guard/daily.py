"""The daily loss limit (M5.14): today's P&L in a pipe, and whether it has reached the limit.

The figure, live: Upstox's short-term positions, as the Upstox module's own cache returns them
(the read the gate makes anyway to tell an exit from a new order). Each row's ``realised`` plus
``unrealised`` is the day's P&L as Upstox documents it; its ``pnl`` is the position's net, which
differs on a position carried in from an earlier day, so it is used only for a row that has
neither, and the figure is then marked approximate. Holdings are not in it. It is gross, before
charges, like services/risk.

The figure, sandbox: the sandbox's own book, today's realised P&L and its open positions' P&L
(readers.sandbox_day).

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


def live_figure(rows: list) -> DayFigure:
    """Today's P&L from Upstox's raw short-term position rows."""
    realised = unrealised = 0.0
    approximate = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        day_realised, day_unrealised = _number(row.get("realised")), _number(row.get("unrealised"))
        if day_realised is None and day_unrealised is None:
            net = _number(row.get("pnl"))
            if net is not None and net != 0:
                unrealised += net
                approximate = True
            continue
        realised += day_realised or 0.0
        unrealised += day_unrealised or 0.0
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
