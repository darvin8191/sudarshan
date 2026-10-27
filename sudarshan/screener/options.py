"""Open interest and option-chain figures: build-up, PCR, max pain, ATM IV, straddle, IV rank.

Pure, like the rest of the engine: every figure is computed from numbers handed in, and one that
cannot be computed is a ``Why`` (never a guess, never a zero). Prices and strikes are paise
integers and open interest an integer, so a ratio is an exact ``Fraction`` and ``PCR > 1.2`` is
decided without a float. An implied volatility is whatever the feed reports (a float, in the
feed's own unit); what is derived from it stays a float.

Definitions:

- ``buildup``: price against its reference and open interest against theirs. Price up and OI up
  is a long build-up, price down and OI up a short build-up, price up and OI down short
  covering, price down and OI down long unwinding; anything unchanged is none. A move counts
  only when it is at least ``min_price`` / ``min_oi`` percent of its reference (0 by default).
- ``atm``: the strike nearest the spot; spot exactly between two strikes takes the lower.
- ``pcr``: the sum of put OI over the sum of call OI on the strikes followed that report both.
- ``max_pain``: the strike K minimising CE_OI x max(0, K - strike) + PE_OI x max(0, strike - K)
  summed over those strikes; the lowest K when two tie.
- ``atm_iv``: the mean of the ATM call's and put's IV (both needed, each above zero);
  ``straddle``: the ATM call's plus the ATM put's last price.
- ``iv_rank``: (today - lowest) / (highest - lowest) x 100 over the stored days and today.
  ``iv_percentile``: the share of stored days whose IV was strictly below today's, in percent.
  Either needs ``MIN_RANK_DAYS`` days (today included): a rank over three days is noise, and a
  screen that fired on it would fire all day.
- ``iv_change``: how far the ATM IV has moved from its value one candle ago, in percent of that
  value.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from fractions import Fraction

from sudarshan.screener import catalog as cat
from sudarshan.screener.price import percent_from
from sudarshan.screener.types import (
    NO_DATA,
    NO_OPTIONS,
    NOT_A_NUMBER,
    SHORT_STORE,
    ZERO,
    Opt,
    Why,
)

LONG_BUILDUP, SHORT_BUILDUP, SHORT_COVERING, LONG_UNWINDING, NONE = cat.BUILDUP_STATES
#: Days of IV (today included) a rank or a percentile needs before it says anything.
MIN_RANK_DAYS = 20

Value = int | float | Fraction | Why


# ---- open interest ------------------------------------------------------------------------------
def moved(now: int, ref: int, minimum: Fraction | int = 0) -> int:
    """+1 up, -1 down, 0 when unchanged or by less than ``minimum`` percent of ``ref``."""
    if now == ref:
        return 0
    if minimum and abs(now - ref) * 100 < minimum * abs(ref):
        return 0
    return 1 if now > ref else -1


def buildup(
    price_now: int,
    price_ref: int,
    oi_now: int,
    oi_ref: int,
    min_price: Fraction | int = 0,
    min_oi: Fraction | int = 0,
) -> str:
    """The build-up state of a price move and an open-interest move (integers only)."""
    price = moved(price_now, price_ref, min_price)
    oi = moved(oi_now, oi_ref, min_oi)
    if not price or not oi:
        return NONE
    if price > 0:
        return LONG_BUILDUP if oi > 0 else SHORT_COVERING
    return SHORT_BUILDUP if oi > 0 else LONG_UNWINDING


def oi_change_pct(oi_now: int, oi_ref: int | None) -> Fraction | Why:
    """Open interest against its reference, in percent of the reference."""
    return percent_from(oi_now, oi_ref, "oi")


# ---- the strikes followed -----------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Strike:
    strike: int  # paise
    ce_oi: int | None = None
    pe_oi: int | None = None
    ce_iv: float | None = None
    pe_iv: float | None = None
    ce_ltp: int | None = None  # paise
    pe_ltp: int | None = None


@dataclass(frozen=True, slots=True)
class Chain:
    """The strikes followed for one underlying, with its spot (paise)."""

    underlying: str
    spot: int | None
    strikes: tuple[Strike, ...]


def atm(chain: Chain) -> Strike | Why:
    """The strike nearest the spot; the lower one when the spot is exactly between two."""
    if not chain.strikes:
        return Why(NO_OPTIONS, (chain.underlying,))
    if chain.spot is None:
        return Why(NO_DATA, ("spot",))
    return min(chain.strikes, key=lambda s: (abs(s.strike - chain.spot), s.strike))


def _with_oi(chain: Chain) -> list[Strike]:
    return [s for s in chain.strikes if s.ce_oi is not None and s.pe_oi is not None]


def pcr(chain: Chain) -> Fraction | Why:
    """Put open interest over call open interest on the strikes that report both."""
    live = _with_oi(chain)
    if not live:
        return Why(NO_DATA, ("oi",))
    calls = sum(s.ce_oi for s in live)
    if calls == 0:
        return Why(ZERO)
    return Fraction(sum(s.pe_oi for s in live), calls)


def max_pain(chain: Chain) -> int | Why:
    """The strike at which option writers pay the least, the lowest when two tie."""
    live = _with_oi(chain)
    if not live:
        return Why(NO_DATA, ("oi",))

    def pain(k: int) -> int:
        return sum(s.ce_oi * max(0, k - s.strike) + s.pe_oi * max(0, s.strike - k) for s in live)

    return min((s.strike for s in live), key=lambda k: (pain(k), k))


def atm_iv(chain: Chain) -> float | Why:
    """The mean of the ATM call's and put's implied volatility."""
    at = atm(chain)
    if isinstance(at, Why):
        return at
    if not (at.ce_iv and at.ce_iv > 0 and at.pe_iv and at.pe_iv > 0):
        return Why(NO_DATA, ("iv",))
    value = (at.ce_iv + at.pe_iv) / 2
    return value if math.isfinite(value) else Why(NOT_A_NUMBER)


def straddle(chain: Chain) -> int | Why:
    """The ATM call's last price plus the ATM put's, in paise."""
    at = atm(chain)
    if isinstance(at, Why):
        return at
    if not (at.ce_ltp and at.ce_ltp > 0 and at.pe_ltp and at.pe_ltp > 0):
        return Why(NO_DATA, ("option price",))
    return at.ce_ltp + at.pe_ltp


# ---- IV over time -------------------------------------------------------------------------------
def _short(history: Sequence[float]) -> Why | None:
    days = len(history) + 1
    return Why(SHORT_STORE, (days, MIN_RANK_DAYS)) if days < MIN_RANK_DAYS else None


def iv_rank(history: Sequence[float], today: float) -> float | Why:
    """Where today's IV sits between the lowest and the highest of the stored days, in percent."""
    if (short := _short(history)) is not None:
        return short
    low, high = min(min(history), today), max(max(history), today)
    if high == low:
        return Why(ZERO)
    return (today - low) / (high - low) * 100.0


def iv_percentile(history: Sequence[float], today: float) -> Fraction | Why:
    """The share of stored days whose IV was strictly below today's, in percent."""
    if (short := _short(history)) is not None:
        return short
    return Fraction(100 * sum(1 for value in history if value < today), len(history))


def iv_change(now: float | None, ago: float | None) -> float | Why:
    """The ATM IV against its value one candle ago, in percent of that value."""
    if now is None or ago is None:
        return Why(NO_DATA, ("iv",))
    if ago == 0:
        return Why(ZERO)
    return (now - ago) / ago * 100.0


# ---- answering an operand -----------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class View:
    """What the runtime knows about options at one run; called with an ``Opt`` operand.

    ``chains`` and ``iv_history`` are keyed by underlying ("NIFTY", "BANKNIFTY", ...) with the
    stored days of ATM IV oldest first and today left out; ``iv_ago`` by (underlying, timeframe);
    the India VIX stands in for an index whose option IV is not stored.
    """

    chains: Mapping[str, Chain] = field(default_factory=dict)
    iv_history: Mapping[str, Sequence[float]] = field(default_factory=dict)
    iv_ago: Mapping[tuple, float] = field(default_factory=dict)
    vix_now: float | None = None
    vix_history: Sequence[float] = ()

    def __call__(self, opt: Opt, own: str | None = None) -> Value:
        what = opt.what
        if what in ("vix_rank", "vix_pct"):
            if self.vix_now is None:
                return Why(NO_DATA, ("India VIX",))
            rank = iv_rank if what == "vix_rank" else iv_percentile
            return rank(self.vix_history, self.vix_now)
        name = own if opt.underlying == "self" else opt.underlying
        chain = self.chains.get(name) if name else None
        if chain is None:
            return Why(NO_OPTIONS, (name or "self",))
        if what == "pcr":
            return pcr(chain)
        if what == "max_pain":
            return max_pain(chain)
        if what == "straddle":
            return straddle(chain)
        iv = atm_iv(chain)
        if what == "atm_iv" or isinstance(iv, Why):
            return iv
        if what == "iv_change":
            return iv_change(iv, self.iv_ago.get((name, opt.tf)))
        history = self.iv_history.get(name, ())
        return iv_rank(history, iv) if what == "iv_rank" else iv_percentile(history, iv)
