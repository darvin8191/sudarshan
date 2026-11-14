"""Which head a trade belongs to (M7.27, M7.28): the one rule the pack and a trip's label share.

Delivery shares and equity fund units are capital gains; a share bought and sold on one day
without delivery is speculative business; a derivative is non-speculative business; anything else
is listed as not computed, never as zero.
"""

from __future__ import annotations

from .model import Trade

DELIVERY, EQUITY_MF, INTRADAY, DERIVATIVE, NOT_COMPUTED = (
    "delivery_equity",
    "equity_mf",
    "intraday_equity",
    "derivative",
    "not_computed",
)
DERIVATIVE_SEGMENTS = ("FO", "COM", "CD")
NOT_COMPUTED_CLASSES = ("manual", "liability")
CASH_EXCHANGES = ("NSE", "BSE")
DERIVATIVE_EXCHANGES = ("NFO", "BFO", "MCX", "CDS", "BCD", "NCDEX", "NCO")
CASH_PRODUCTS = ("CNC", "MIS")


def head_of(trade: Trade) -> str:
    """The head of one trade by itself. An EQ trade is delivery here; how much of a day's buying
    and selling is intraday is ``intraday_qty``'s to say, because it needs the whole day."""
    if trade.asset_class in NOT_COMPUTED_CLASSES:
        return NOT_COMPUTED
    if trade.segment in DERIVATIVE_SEGMENTS:
        return DERIVATIVE
    if trade.segment == "EQ":
        return DELIVERY
    if trade.segment == "MF" and trade.asset_class == "equity":
        return EQUITY_MF
    return NOT_COMPUTED


def intraday_qty(bought_u: int, sold_u: int, all_delivery: bool = False) -> int:
    """How much of one day's trading in one share, in one account, was intraday: the lesser of
    what was bought and what was sold (the exchange settles a day net), unless the owner marked
    the day as all delivery."""
    return 0 if all_delivery else min(bought_u, sold_u)


def kind_of_contract(contract: tuple) -> str:
    """ "options" or "futures", from the contract's option type."""
    return "options" if contract[4] in ("CE", "PE") else "futures"


def head_of_product(exchange: str, symbol: str, product: str, same_day: bool = False) -> str:
    """The head of a round trip whose product is known (the journal's label). On a cash exchange
    MIS is intraday, and so is a CNC trip opened and closed on one day (``same_day``): nothing was
    delivered. ``symbol`` is taken so the rule can grow without its callers changing."""
    if exchange in DERIVATIVE_EXCHANGES:
        return DERIVATIVE
    if exchange in CASH_EXCHANGES and product in CASH_PRODUCTS:
        return INTRADAY if product == "MIS" or same_day else DELIVERY
    return NOT_COMPUTED
