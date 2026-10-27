"""The limit price band (M20.7, design section 6): a risk-increasing priced order too far from the
market is refused before it is sent. Pure: the quote arrives as an argument.

- It applies to LIMIT, SL and SL-M orders (and modifies to them) that add risk; never to an exit
  and never to a MARKET order, which keeps Upstox's own market protection.
- The reference for a BUY is the higher of the last price and the best ask, for a SELL the lower
  of the last price and the best bid, zeros ignored: a limit at the price the market shows now is
  never refused because the last trade on an illiquid option is old.
- The band is a share of the reference, and for options never less than a floor in rupees, since
  a 5 rupee option needs one. Options, futures and cash each have their own share.
- The aggressive side only, as NSE's own limit price protection: a BUY above the reference plus
  the band, a SELL below it less the band. A stop-limit's limit is held to its trigger instead,
  and a stop-market is refused when its trigger is already through the market by more than the
  band (it would fill at once). A passive order far from the market is refused only when the
  owner turns on both sides.
- No quote, no check: the order goes, and the gate notes it (the exchange's own band still
  stands behind it).
"""

from __future__ import annotations

from dataclasses import dataclass

from . import rules

OPTION, FUTURE, CASH = "option", "future", "cash"
DERIVATIVE_EXCHANGES = frozenset({"NFO", "BFO", "MCX", "CDS", "BCD", "NCDEX", "NCO"})


@dataclass(frozen=True)
class Quote:
    ltp: float = 0.0
    bid: float = 0.0
    ask: float = 0.0


@dataclass(frozen=True)
class Band:
    pct: float  # a share of the reference, in percent
    floor: float = 0.0  # rupees: the band is never narrower (options)


@dataclass(frozen=True)
class Breach:
    """Why a price is outside the band, in numbers, for the sentence."""

    side: int
    what: str  # "price", "limit" (a stop-limit's, against its trigger) or "trigger" (SL-M)
    price: float
    reference: float  # the market, or the trigger a stop-limit's limit is held to
    away_pct: float  # how far the price is from the reference, in percent
    band_pct: float
    floor: float
    above: bool  # above the reference, or below it


def segment_of(symbol: str, exchange: str) -> str | None:
    """Which band an instrument takes: an option, a future, cash, or none."""
    symbol = str(symbol or "").strip().upper()
    exchange = str(exchange or "").strip().upper()
    if exchange in rules.EQUITY_CASH:
        return CASH
    if exchange in DERIVATIVE_EXCHANGES:
        if symbol.endswith(("CE", "PE")):
            return OPTION
        if symbol.endswith("FUT"):
            return FUTURE
    return None


def band_for(segment: str | None, settings) -> Band | None:
    """The owner's band for a segment (store.RiskSettings), or None."""
    if segment == OPTION:
        return Band(settings.band_opt_pct, settings.band_opt_min_rs)
    if segment == FUTURE:
        return Band(settings.band_fut_pct)
    if segment == CASH:
        return Band(settings.band_cash_pct)
    return None


def reference(side: int, quote: Quote) -> float:
    """The market an order is measured against: the touch on its own side, or the last price."""
    if side == rules.BUY:
        prices = [p for p in (quote.ltp, quote.ask) if p > 0]
        return max(prices) if prices else 0.0
    prices = [p for p in (quote.ltp, quote.bid) if p > 0]
    return min(prices) if prices else 0.0


def width(band: Band, around: float) -> float:
    return max(around * band.pct / 100.0, band.floor)


def _breach(side, what, price, ref, band, above) -> Breach:
    away = abs(price - ref) / ref * 100.0 if ref > 0 else 0.0
    return Breach(side, what, price, ref, round(away, 1), band.pct, band.floor, above)


def check(
    side: int | None,
    price_type: str,
    price: float,
    trigger: float,
    quote: Quote | None,
    band: Band | None,
    *,
    both_sides: bool = False,
) -> Breach | None:
    """Whether a risk-increasing order's price is outside the band; None when it is inside, the
    band does not apply, or there is no quote (fail open: the gate notes it)."""
    price_type = str(price_type or "").upper()
    if band is None or side not in (rules.BUY, rules.SELL) or price_type == "MARKET":
        return None
    buy = side == rules.BUY
    if price_type == "SL":
        if trigger <= 0 or price <= 0:
            return None
        gap = width(band, trigger)
        if buy and price > trigger + gap:
            return _breach(side, "limit", price, trigger, band, True)
        if not buy and price < trigger - gap:
            return _breach(side, "limit", price, trigger, band, False)
        return None
    if quote is None:
        return None
    ref = reference(side, quote)
    if ref <= 0:
        return None
    gap = width(band, ref)
    if price_type == "SL-M":
        if trigger <= 0:
            return None
        if buy and trigger < ref - gap:
            return _breach(side, "trigger", trigger, ref, band, False)
        if not buy and trigger > ref + gap:
            return _breach(side, "trigger", trigger, ref, band, True)
        return None
    if price_type != "LIMIT" or price <= 0:
        return None
    if buy and price > ref + gap:
        return _breach(side, "price", price, ref, band, True)
    if not buy and price < ref - gap:
        return _breach(side, "price", price, ref, band, False)
    if both_sides and buy and price < ref - gap:
        return _breach(side, "price", price, ref, band, False)
    if both_sides and not buy and price > ref + gap:
        return _breach(side, "price", price, ref, band, True)
    return None
