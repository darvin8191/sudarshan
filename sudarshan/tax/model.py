"""What the tax core is given and what it gives back (M7.27, M7.28), as integers and nothing else.

The portfolio's adapter builds these from Upstox's reports and the ledgers; the core never reads
anything itself. ``build`` makes any of them from a plain row (a JSON vector or a stored row).
"""

from __future__ import annotations

from dataclasses import dataclass, fields

SIDES = ("BUY", "SELL")
SEGMENTS = ("EQ", "FO", "COM", "CD", "MF")
BUCKETS = ("stcl", "ltcl", "business", "speculative")


@dataclass(frozen=True, slots=True)
class Trade:
    """One fill. ``instrument`` is the ISIN for EQ and MF, and for a derivative the contract
    ``(exchange, scrip, expiry day, strike_u, option_type)`` with "FUT" for a future. A buyback is
    a SELL with ``kind_hint`` "buyback", dated the day of the buyback. ``asset_class`` is the
    ledger's tag: "equity" makes a fund unit equity-oriented, "manual" and "liability" are never
    computed. Charges are None when not known per trade (Upstox's history)."""

    taxpayer: int
    account: int
    day: int
    seq: int
    side: str
    segment: str
    instrument: str | tuple
    qty_u: int
    price_u: int
    amount_paise: int
    source: str = "upstox_history"
    charges_ex_stt_paise: int | None = None
    stt_paise: int | None = None
    kind_hint: str | None = None
    asset_class: str | None = None


@dataclass(frozen=True, slots=True)
class Action:
    """A bonus (``num`` new for every ``den`` held, at nil cost, dated ``ex_day``) or a split
    (every ``den`` become ``num``; cost and day stay). Applied only when ``accepted``."""

    kind: str
    instrument: str
    ex_day: int
    num: int
    den: int
    new_isin: str | None = None
    accepted: bool = True


@dataclass(frozen=True, slots=True)
class OpeningLot:
    """Units held from before the records begin, in the units of ``acquired_day``."""

    taxpayer: int
    account: int
    instrument: str
    acquired_day: int
    qty_u: int
    cost_paise: int
    source: str = "owner"


@dataclass(frozen=True, slots=True)
class Carry:
    """A loss of ``loss_fy`` brought forward, as the owner entered it or as computed here."""

    taxpayer: int
    loss_fy: int
    bucket: str
    paise: int
    source: str = "owner"


@dataclass(frozen=True, slots=True)
class Settings:
    """A taxpayer's readings of the open rules; None takes the rule row's default."""

    turnover_method: str | None = None
    exempt_order: str | None = None
    lt_boundary: str | None = None
    allocate_expenses: bool = True


@dataclass(frozen=True, slots=True)
class Lot:
    """Units of one acquisition still held. ``basis_qty_u`` is their number as on 31 Jan 2018
    terms (a later split changes ``qty_u`` only), which the 2018 price is multiplied by."""

    taxpayer: int
    account: int
    isin: str
    acquired_day: int
    qty_u: int
    cost_paise: int
    source: str
    basis_qty_u: int


@dataclass(frozen=True, slots=True)
class Disposal:
    """Part of a lot that a sale took. ``acquired_day`` and ``cost_paise`` are None when no
    purchase record exists; ``charges_paise`` is the sale's own non-STT charges where known."""

    taxpayer: int
    account: int
    isin: str
    sale_day: int
    seq: int
    qty_u: int
    sale_paise: int
    acquired_day: int | None
    cost_paise: int | None
    basis_qty_u: int = 0
    lot_source: str | None = None
    charges_paise: int | None = None
    kind_hint: str | None = None


@dataclass(frozen=True, slots=True)
class Realised:
    """One closed piece of business trading: a derivative round trip, or a day's intraday shares.
    ``kind`` is "futures", "options" or "speculative"; ``flag`` is "assumed_zero" for an option
    taken as expired worthless."""

    taxpayer: int
    account: int
    instrument: str | tuple
    kind: str
    open_day: int
    close_day: int
    qty_u: int
    buy_paise: int
    sell_paise: int
    pnl_paise: int
    flag: str | None = None


def build(cls, row: dict):
    """A model object from a plain row; a contract written as a list becomes its tuple."""
    values = {f.name: row[f.name] for f in fields(cls) if f.name in row}
    if isinstance(values.get("instrument"), list):
        values["instrument"] = tuple(values["instrument"])
    return cls(**values)


@dataclass(frozen=True, slots=True)
class Gain:
    """One disposal worked out. ``status`` is "ok", or why it is left out of every total:
    "needs_fmv" (bought before 1 Feb 2018 and the price of 31 Jan 2018 is not known),
    "unknown_cost" (no purchase record) or "exempt_buyback". ``receipt_paise`` is what was
    received; ``sale_paise`` is the sale value used (nil for a buyback taxed as a dividend, whose
    receipt is then ``dividend_paise``). ``allocated`` marks expenses shared out of a year's total."""

    taxpayer: int
    account: int
    isin: str
    acquired_day: int | None
    sale_day: int
    qty_u: int
    receipt_paise: int
    sale_paise: int
    cost_paise: int | None
    expenses_paise: int
    gain_paise: int | None
    term: str | None
    rate_bp: int | None
    status: str = "ok"
    fmv_used: bool = False
    allocated: bool = False
    buyback: str | None = None
    dividend_paise: int = 0
