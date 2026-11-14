"""Which head a trade belongs to (M7.27, M7.28): the one rule the pack and a trip's label share.

Delivery shares and equity fund units are capital gains; a share bought and sold on one day
without delivery is speculative business; a derivative is non-speculative business; anything else
is listed as not computed, never as zero.

A cash-segment trade is a share only when its ISIN says so (``is_equity_share``). The cash
segment also carries ETF units (gold, liquid, debt and equity ones), REIT and InvIT units, gold
bonds, government securities, debentures and preference shares, none of which takes a listed
share's rates, its yearly exemption or the 31 January 2018 rule as a matter of course. So each of
those, and anything whose ISIN is missing or unknown, is not computed, whatever class its ledger
gives it (a listed asset's class is "equity" until the owner changes it, so the class cannot say).
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
#: An Indian ISIN is "IN", the issuer's kind (E: a company; 9: a company's partly paid issue),
#: four characters for the issuer, two for the kind of security, two for the issue and a check
#: character. Kind "01" is an equity share. A fund's or ETF's unit begins INF, a government
#: security or gold bond IN0, and a company's other papers carry another kind (a preference
#: share, a debenture, a REIT's or an InvIT's unit).
EQUITY_ISSUERS = ("INE", "IN9")
EQUITY_SECURITY = "01"


def is_equity_share(instrument: object) -> bool:
    """Whether an ISIN is a company's equity share, by its own characters; never a guess."""
    return (
        isinstance(instrument, str)
        and len(instrument) == 12
        and instrument.isascii()
        and instrument.isalnum()
        and instrument == instrument.upper()
        and instrument[:3] in EQUITY_ISSUERS
        and instrument[7:9] == EQUITY_SECURITY
    )


def head_of(trade: Trade) -> str:
    """The head of one trade by itself. An EQ trade of a company's equity share is delivery here;
    how much of a day's buying and selling is intraday is ``intraday_qty``'s to say, because it
    needs the whole day. An EQ trade of anything else is not computed."""
    if trade.asset_class in NOT_COMPUTED_CLASSES:
        return NOT_COMPUTED
    if trade.segment in DERIVATIVE_SEGMENTS:
        return DERIVATIVE
    if trade.segment == "EQ":
        return DELIVERY if is_equity_share(trade.instrument) else NOT_COMPUTED
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


def head_of_product(
    exchange: str, symbol: str, product: str, same_day: bool = False, isin: str | None = None
) -> str:
    """The head of a round trip whose product is known (the journal's label). On a cash exchange
    MIS is intraday, and so is a CNC trip opened and closed on one day (``same_day``): nothing was
    delivered. As for ``head_of``, a cash trip is a share's only when its ``isin`` is a company's
    equity share: an ETF's, a REIT's or a bond's trip, and one whose ISIN is not known, is not
    computed. ``symbol`` is taken so the rule can grow without its callers changing."""
    if exchange in DERIVATIVE_EXCHANGES:
        return DERIVATIVE
    if exchange in CASH_EXCHANGES and product in CASH_PRODUCTS and is_equity_share(isin):
        return INTRADAY if product == "MIS" or same_day else DELIVERY
    return NOT_COMPUTED
