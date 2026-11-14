"""From a strategy to the contract its alert trades, and the size (M5.19, M12.10). Pure.

Every input is an argument: what the master lists (``known``), an underlying's future expiries,
which underlyings are an index's, the dated session closes, the time. Built on what the
watchlists already hold and test: TradingView's name rules (``watchlists/tvimport.py``), the
expiry pick (``watchlists/dynamic.py``: an expiry is open until its contract's stop by the day's
regime) and the stop itself (``watchlists/expiry.py``).

**An expiry is read from the master's list, never worked out from a weekday**, so a holiday shift,
a changed expiry day or a new lot size needs no code here.

The chart an alert comes from and the contract traded are separate on purpose: a strategy on the
index or on ``NIFTY1!`` may trade a future, or buy an option: a call for a long target, a put for
a short one, the strike counted from the underlying's last price in the master's own strike list.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from fractions import Fraction

from sudarshan.watchlists import dynamic
from sudarshan.watchlists.tvimport import CHECKED_INDEX_FUTURES, INDEX_ALIASES

from . import model as m

Known = Callable[[str, str], bool]

_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
_FOLLOW = re.compile(r"(.+?)([12])!")
_TV_OPTION = re.compile(r"([A-Z0-9&_-]+?)(\d{2})(\d{2})(\d{2})([CP])(\d+(?:\.\d+)?)")
_OA_DERIVATIVE = re.compile(r"([A-Z0-9&-]+?)(\d{2})([A-Z]{3})(\d{2})(?:FUT|\d+(?:\.\d+)?(?:CE|PE))")
_FO = {"NSE": "NFO", "BSE": "BFO"}
_COMMODITY = ("MCX", "NCDEX", "NCO")
_BSE_INDICES = ("SENSEX", "BANKEX")
_INDIA = timedelta(hours=5, minutes=30)
CARRY = ("NRML", "CNC")
_OFFSET = re.compile(r"ATM|(ITM|OTM)(10|[1-9])")
Strikes = Callable[[str], Sequence[float]]


@dataclass(frozen=True)
class Contract:
    """What an order is sent for."""

    exchange: str
    symbol: str
    expiry: str | None = None  # ISO date; None for a share
    stop_ms: int | None = None  # when it stops trading on its expiry day
    expires_today: bool = False
    underlying: str | None = None  # the freeze row's name, for a derivative


@dataclass(frozen=True)
class Unmapped:
    """No contract: ``reason`` is one of words.not_mapped's, or ``too_close`` with ``stop_ms``."""

    reason: str
    what: str = ""
    stop_ms: int | None = None


def india_date(now_ms: int) -> str:
    """The date in India at an instant, as YYYY-MM-DD."""
    return (datetime.fromtimestamp(now_ms / 1000, UTC) + _INDIA).date().isoformat()


def expiry_code(iso: str) -> str:
    """``2026-11-24`` as OpenAlgo writes it in a symbol: ``24NOV26``."""
    day = date.fromisoformat(iso)
    return f"{day.day:02d}{_MONTHS[day.month - 1]}{day.year % 100:02d}"


def future_symbol(underlying: str, iso: str) -> str:
    return f"{underlying}{expiry_code(iso)}FUT"


def expiry_of(symbol: str) -> tuple[str, str] | None:
    """(underlying, ISO expiry) of an OpenAlgo future or option symbol; ``None`` for a share."""
    found = _OA_DERIVATIVE.fullmatch(symbol or "")
    if not found or found.group(3) not in _MONTHS:
        return None
    base, day, month, year = found.groups()
    try:
        iso = date(2000 + int(year), _MONTHS.index(month) + 1, int(day)).isoformat()
    except ValueError:
        return None
    return base, iso


def chart_matches(strategy_chart: str | None, alert: m.Alert) -> bool:
    """Whether the alert came from the chart the strategy names (a delayed-data ending is already
    taken off the alert's exchange). A strategy that names no chart takes any."""
    if not strategy_chart:
        return True
    return alert.ticker is not None and f"{alert.exchange}:{alert.ticker}" == strategy_chart.upper()


def held_expired(
    symbol: str | None,
    exchange: str | None,
    now_ms: int,
    indices: Collection[str],
    closes: dict[str, int] | None = None,
) -> bool:
    """Whether a held contract has stopped trading for good. A share never has."""
    found = expiry_of(symbol or "")
    if found is None:
        return False
    underlying, iso = found
    stop = dynamic.close_ms(iso, closes, index=underlying in indices, exchange=exchange or "NFO")
    return stop <= now_ms


def day_ended(product: str, held_since_ms: int | None, today: str) -> bool:
    """Whether a held note is of an intraday (MIS) position entered on an earlier India date.

    Such a position is gone: an intraday position is squared off before its session's close,
    with no alert. A carried product (NRML, CNC) keeps its note until its exit or its expiry.
    ``today`` is the date in India, YYYY-MM-DD; a note with no time is kept.
    """
    if product != "MIS" or not held_since_ms:
        return False
    return india_date(held_since_ms) < today


def _pick(
    strategy: m.Strategy,
    exchange: str,
    underlying: str,
    rule: str,
    now_ms: int,
    expiries: Sequence[str],
    indices: Collection[str],
    closes: dict[str, int] | None,
    entering: bool,
) -> tuple[str, int] | None:
    """The expiry a rule means now, from the listed ones, and when its contracts stop."""
    index = underlying in indices
    today, at = india_date(now_ms), now_ms
    if entering and strategy.roll_on_expiry_day and today in expiries:
        # A position that carries is never opened in a contract that ends this afternoon: from the
        # start of expiry day the rule reads as it will after today's stop (this month's and next
        # month's both move on, and so do the nearest and the next).
        at = max(at, dynamic.close_ms(today, closes, index=index, exchange=exchange))
    iso = dynamic.pick_expiries(list(expiries), at, closes, index=index, exchange=exchange).get(
        rule
    )
    if iso is None:
        return None
    return iso, dynamic.close_ms(iso, closes, index=index, exchange=exchange)


def _future(
    strategy: m.Strategy,
    exchange: str,
    underlying: str,
    rule: str,
    now_ms: int,
    known: Known,
    expiries: Sequence[str],
    indices: Collection[str],
    closes: dict[str, int] | None,
    entering: bool,
) -> Contract | Unmapped:
    picked = _pick(
        strategy, exchange, underlying, rule, now_ms, expiries, indices, closes, entering
    )
    if picked is None:
        return Unmapped("no_expiry", underlying)
    iso, stop = picked
    symbol = future_symbol(underlying, iso)
    if not known(exchange, symbol):
        return Unmapped("not_listed", symbol)
    if entering and stop - now_ms < strategy.no_entry_before_stop_min * 60_000:
        return Unmapped("too_close", symbol, stop)
    return Contract(exchange, symbol, iso, stop, iso == india_date(now_ms), underlying)


def option_side(symbol: str | None) -> str | None:
    """``CE`` or ``PE`` for an option's symbol, ``None`` for anything else."""
    tail = (symbol or "")[-2:]
    return tail if tail in ("CE", "PE") and expiry_of(symbol or "") else None


def wanted_side(target: int) -> str:
    """What an option strategy buys for a target: a call to be long, a put to be short. It never
    sells an option to open."""
    return "CE" if target > 0 else "PE"


def strike_steps(offset: str | None, side: str) -> int | None:
    """An offset as steps along the sorted strike list: ITM is a lower strike for a call and a
    higher one for a put, OTM the other way."""
    found = _OFFSET.fullmatch((offset or "ATM").upper())
    if not found:
        return None
    if not found.group(1):
        return 0
    steps = int(found.group(2))
    lower = (found.group(1) == "ITM") == (side == "CE")
    return -steps if lower else steps


def _bought(
    strategy: m.Strategy,
    target: int,
    now_ms: int,
    known: Known,
    expiries: Sequence[str],
    strikes_of: Strikes | None,
    price: float | None,
    indices: Collection[str],
    closes: dict[str, int] | None,
    entering: bool,
) -> Contract | Unmapped:
    exchange, underlying = strategy.exchange, strategy.underlying
    if target == 0:
        return Unmapped("nothing_held", underlying)
    rule = strategy.expiry_rule or "nearest"
    picked = _pick(
        strategy, exchange, underlying, rule, now_ms, expiries, indices, closes, entering
    )
    if picked is None:
        return Unmapped("no_option_expiry", underlying)
    iso, stop = picked
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not price > 0:
        return Unmapped("no_price", underlying)
    strikes = sorted(set(strikes_of(iso))) if strikes_of else []
    side = wanted_side(target)
    steps = strike_steps(strategy.strike_offset, side)
    atm = dynamic.atm_strike(price, strikes)
    strike = None if atm is None or steps is None else dynamic.offset_strike(strikes, atm, steps)
    if strike is None:
        return Unmapped("no_strike", f"{underlying} {expiry_code(iso)}")
    written = str(int(strike)) if strike == int(strike) else str(strike)
    symbol = f"{underlying}{expiry_code(iso)}{written}{side}"
    if not known(exchange, symbol):
        return Unmapped("not_listed", symbol)
    if entering and stop - now_ms < strategy.no_entry_before_stop_min * 60_000:
        return Unmapped("too_close", symbol, stop)
    return Contract(exchange, symbol, iso, stop, iso == india_date(now_ms), underlying)


def _cash(exchange: str, name: str, known: Known) -> Contract | Unmapped:
    if name in INDEX_ALIASES:
        return Unmapped("index", INDEX_ALIASES[name])
    tries = [name]
    if "_" in name:
        tries += [name.replace("_", "-"), name.replace("_", "&")]
    for candidate in tries:
        if known(exchange, candidate):
            return Contract(exchange, candidate)
    return Unmapped("unknown", f"{exchange}:{name}")


def _option(exchange: str, ticker: str, found: re.Match, known: Known, now_ms: int, indices,
            closes, entering: bool, no_entry_min: int) -> Contract | Unmapped:  # fmt: skip
    root, yy, mm, dd, side, strike = found.groups()
    if exchange != "NSE":  # no BSE option's TradingView form is checked yet
        return Unmapped("unchecked", f"{exchange}:{ticker}")
    try:
        iso = date(2000 + int(yy), int(mm), int(dd)).isoformat()
    except ValueError:
        return Unmapped("unknown", f"{exchange}:{ticker}")
    underlying = INDEX_ALIASES.get(root, root)
    strike = strike[:-2] if strike.endswith(".0") else strike
    symbol = f"{underlying}{expiry_code(iso)}{strike}{'CE' if side == 'C' else 'PE'}"
    fo = _FO[exchange]
    if not known(fo, symbol):
        return Unmapped("not_listed", symbol)
    stop = dynamic.close_ms(iso, closes, index=underlying in indices, exchange=fo)
    if entering and stop - now_ms < no_entry_min * 60_000:
        return Unmapped("too_close", symbol, stop)
    return Contract(fo, symbol, iso, stop, iso == india_date(now_ms), underlying)


def resolve(
    strategy: m.Strategy,
    now_ms: int,
    *,
    known: Known,
    expiries: Sequence[str] = (),
    indices: Collection[str] = (),
    closes: dict[str, int] | None = None,
    entering: bool = True,
    target: int = 1,
    strikes_of: Strikes | None = None,
    price: float | None = None,
) -> Contract | Unmapped:
    """The contract the strategy trades now.

    ``expiries`` are the ISO expiry dates the master lists for the strategy's underlying's
    futures (its options, for an option strategy); ``entering`` is False for an exit, which is
    never refused for being close to the stop and never rolled forward. An option strategy also
    needs the ``target`` (which side to buy), the strikes the master lists for an expiry
    (``strikes_of``) and the underlying's last ``price``.
    """
    if strategy.exchange in _COMMODITY:
        return Unmapped("commodity")
    if strategy.kind == m.OPTION:
        return _bought(
            strategy, target, now_ms, known, expiries, strikes_of, price, indices, closes,
            entering,
        )  # fmt: skip
    if strategy.kind == m.FUTURE:
        return _future(
            strategy, strategy.exchange, strategy.underlying, strategy.expiry_rule or "month",
            now_ms, known, expiries, indices, closes, entering,
        )  # fmt: skip
    if strategy.kind != m.SAME or not strategy.chart or ":" not in strategy.chart:
        return Unmapped("no_chart")
    exchange, ticker = strategy.chart.upper().split(":", 1)
    if exchange in _COMMODITY:
        return Unmapped("commodity")
    if exchange not in _FO:
        return Unmapped("unknown", strategy.chart)
    follow = _FOLLOW.fullmatch(ticker)
    if follow:
        name, which = follow.groups()
        root = INDEX_ALIASES.get(name, name)
        # Only the index futures whose TradingView name is the index's own word are checked; a
        # stock's future and every BSE future wait for one real name to be seen.
        if exchange == "BSE" or root in _BSE_INDICES or root not in CHECKED_INDEX_FUTURES:
            return Unmapped("unchecked", strategy.chart)
        return _future(
            strategy, _FO[exchange], root, "month" if which == "1" else "next_month",
            now_ms, known, expiries, indices, closes, entering,
        )  # fmt: skip
    option = _TV_OPTION.fullmatch(ticker)
    if option and not known(exchange, ticker):
        return _option(
            exchange, ticker, option, known, now_ms, indices, closes, entering,
            strategy.no_entry_before_stop_min,
        )  # fmt: skip
    return _cash(exchange, ticker, known)


def futures_underlying(strategy: m.Strategy) -> str | None:
    """The underlying whose future expiries ``resolve`` needs, or ``None`` when it needs none."""
    if strategy.kind == m.FUTURE:
        return strategy.underlying
    if strategy.kind == m.SAME and strategy.chart and ":" in strategy.chart:
        follow = _FOLLOW.fullmatch(strategy.chart.upper().split(":", 1)[1])
        if follow:
            return INDEX_ALIASES.get(follow.group(1), follow.group(1))
    return None


@dataclass(frozen=True)
class NotSized:
    reason: str  # lot, freeze, no_size, not_whole or cap
    figure: int = 0


def quantity(strategy: m.Strategy, lot_size: int | None, freeze_qty: int = 0) -> int | NotSized:
    """The strategy's fixed size in units: lots times the contract's lot size read from the
    master when the order is built, or shares. Above the exchange's freeze quantity nothing is
    sent; the intake never splits an order."""
    if strategy.lots:
        if not isinstance(lot_size, int) or isinstance(lot_size, bool) or lot_size <= 0:
            return NotSized("lot")
        units = strategy.lots * lot_size
    else:
        units = int(strategy.quantity or 0)
        if units <= 0:
            return NotSized("lot")
    if freeze_qty and units > freeze_qty:
        return NotSized("freeze", int(freeze_qty))
    return units


def scaled_quantity(
    strategy: m.Strategy, size: float | None, lot_size: int | None, freeze_qty: int = 0
) -> int | NotSized:
    """A scaled strategy's size in units: TradingView's position size times the strategy's
    multiplier. It must come to a whole number of lots (of shares, for a share: ``lot_size`` 1)
    and never above the strategy's cap; anything else is not sized and nothing is sent. A size is
    never rounded: a rounded size is a position the chart does not have."""
    if not isinstance(lot_size, int) or isinstance(lot_size, bool) or lot_size <= 0:
        return NotSized("lot")
    if isinstance(size, bool) or not isinstance(size, (int, float)) or not 0 < size < 1e12:
        return NotSized("no_size")
    if not strategy.multiplier or not strategy.max_lots:
        return NotSized("no_size")
    units = Fraction(str(size)) * Fraction(str(strategy.multiplier))
    if units.denominator != 1 or units <= 0 or int(units) % lot_size:
        return NotSized("not_whole", lot_size)
    units = int(units)
    if units // lot_size > strategy.max_lots:
        return NotSized("cap", strategy.max_lots * lot_size)
    if freeze_qty and units > freeze_qty:
        return NotSized("freeze", int(freeze_qty))
    return units
