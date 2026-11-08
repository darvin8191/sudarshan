"""The order size limit (M20.7; owner decision 6): the part of an order that adds risk, over the
owner's cap, is refused before it is sent. Pure: the lot size arrives as an argument.

- Derivatives (F&O, MCX, currency): the cap is in lots, the quantity over the instrument's lot
  size from the master contract (NIFTY 65, BANKNIFTY 30 in October 2026).
- Cash (NSE, BSE): the cap is in shares.
- What counts is the part of an order that adds risk: all of one that opens or adds; for one that
  would close a position and open it the other way, the part beyond closing it. An exit is never
  limited.
- An instrument whose lot size is unknown is not held to it: the gate lets the order go, noted.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import band, rules

LOTS, SHARES = "lots", "shares"


@dataclass(frozen=True)
class Excess:
    """How far an order is over the cap, in the cap's own unit."""

    unit: str  # LOTS or SHARES
    amount: float  # what the order adds, in lots or shares
    cap: int


def adding(verdict: rules.Verdict, quantity: int | None) -> int:
    """How much of an order adds risk: 0 for one that reduces it."""
    if verdict.reducing or not quantity:
        return 0
    if verdict.reason in (rules.CROSSES, rules.ON_ITS_WAY):
        return max(0, quantity - verdict.room)
    return quantity


def unit_of(exchange: str) -> str | None:
    """The cap's unit on an exchange, or None where no cap applies (an index, say)."""
    exchange = str(exchange or "").strip().upper()
    if exchange in rules.EQUITY_CASH:
        return SHARES
    if exchange in band.DERIVATIVE_EXCHANGES:
        return LOTS
    return None


def check(exchange: str, quantity: int, lot_size: float | None, settings) -> Excess | None:
    """Whether ``quantity``, what an order adds, is over the owner's cap (store.RiskSettings):
    None when it is within it, when no cap applies, or when the lot size is unknown."""
    unit = unit_of(exchange)
    if unit == SHARES:
        cap = int(settings.max_qty_cash)
        return Excess(SHARES, quantity, cap) if quantity > cap else None
    if unit == LOTS and lot_size and lot_size > 0:
        cap = int(settings.max_lots_fo)
        lots = quantity / lot_size
        return Excess(LOTS, round(lots, 2), cap) if lots > cap else None
    return None
