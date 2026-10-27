"""Everything a screen can say: fields, indicators, operators, universes, their units and limits.

One table for the checker (spec), the engine, the words and the phone's builder, so the four cannot
disagree. Pure: no I/O.

Units (types.py): a typed number takes the unit of what it is compared with, so ``close > 2500.03``
reads the number as rupees and compares in paise, and ``change_pct >= 2`` reads 2 as a percent.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from fractions import Fraction

from sudarshan.screener.types import (
    DAY,
    NUM,
    PAISE,
    PCT,
    QTY,
    RUPEES,
    STATE,
    TF,
    WEEK,
    plain,
)

SCHEMA = 1
NAME_MAX = 60
LIST_NAME_MAX = 64
MAX_CLAUSES = 20
MAX_DEPTH = 3
MAX_INSTANCES = 12
#: Candles the screener holds per timeframe (series length is capped at 1,600). The history it
#: loads once is 30 days of 1-minute bars (3 to 30-minute candles are built from them), 90 days of
#: 60-minute bars and the daily candles; 63 trading days of 7 sixty-minute candles is 441.
KEPT: dict[TF, int] = {1: 1600, 3: 1600, 5: 1575, 15: 525, 30: 273, 60: 441, DAY: 1600, WEEK: 320}

TF_WORDS: dict[TF, str] = {
    1: "1-min", 3: "3-min", 5: "5-min", 15: "15-min", 30: "30-min", 60: "60-min",
    DAY: "day", WEEK: "week",
}  # fmt: skip


def candles_of(tf: TF) -> str:
    """'15-min candles', 'day candles'."""
    return f"{TF_WORDS[tf]} candles"


# ---- what a candle or the session offers ------------------------------------------------------
@dataclass(frozen=True, slots=True)
class FieldSpec:
    unit: str
    #: True: read from a candle (takes a timeframe and an offset). False: a level of the session.
    candle: bool
    words: str


FIELDS: dict[str, FieldSpec] = {
    "open": FieldSpec(PAISE, True, "Open"),
    "high": FieldSpec(PAISE, True, "High"),
    "low": FieldSpec(PAISE, True, "Low"),
    "close": FieldSpec(PAISE, True, "Close"),
    "volume": FieldSpec(QTY, True, "Volume"),
    "oi": FieldSpec(QTY, True, "Open interest"),
    "vwap": FieldSpec(PAISE, True, "VWAP"),
    "change_pct": FieldSpec(PCT, True, "% change from the previous close"),
    "turnover": FieldSpec(RUPEES, True, "Turnover"),
    "prev_close": FieldSpec(PAISE, False, "Previous close"),
    "day_open": FieldSpec(PAISE, False, "Day open"),
    "day_high": FieldSpec(PAISE, False, "Day high"),
    "day_low": FieldSpec(PAISE, False, "Day low"),
    "prev_day_high": FieldSpec(PAISE, False, "Yesterday's high"),
    "prev_day_low": FieldSpec(PAISE, False, "Yesterday's low"),
    "prev_day_close": FieldSpec(PAISE, False, "Yesterday's close"),
    "year_high": FieldSpec(PAISE, False, "52-week high"),
    "year_low": FieldSpec(PAISE, False, "52-week low"),
    "gap_pct": FieldSpec(PCT, False, "Opening gap %"),
}
#: Session levels that move with every candle: a cross against them can never be told.
RUNNING_LEVELS = frozenset({"day_open", "day_high", "day_low"})
#: Session levels that need the day candles.
DAY_LEVELS = frozenset(
    {"day_open", "day_high", "day_low", "prev_day_high", "prev_day_low", "prev_day_close", "gap_pct"}
)  # fmt: skip

PRICE_SOURCES = ("close", "open", "high", "low", "hl2", "hlc3", "ohlc4")
VOLUME_SOURCES = ("volume", "oi", "obv")
SOURCES = (*PRICE_SOURCES, *VOLUME_SOURCES)


# ---- indicators -------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Param:
    name: str
    kind: str  # "int" | "dec" | "choice"
    default: object
    low: Fraction | int | None = None
    high: Fraction | int | None = None
    choices: tuple = ()
    words: str = ""


@dataclass(frozen=True, slots=True)
class IndSpec:
    label: str
    #: "library": over openalgo.ta (counts toward the 12 instances); "price": the screener's own.
    family: str
    params: tuple[Param, ...]
    outs: tuple[str, ...]
    #: Unit of each output; "source" follows the ``source`` parameter.
    units: Mapping[str, str]
    #: Candles before the value counts as ready (types.WARMING).
    warmup: Callable[[Mapping, str], int]
    title: Callable[[Mapping], str]
    #: Timeframes it can be read on; None: any.
    tfs: tuple | None = None
    #: Output values that mean something to read: {"direction": {-1: "up trend", 1: "down trend"}}.
    meanings: Mapping[str, Mapping[int, str]] | None = None

    @property
    def default_out(self) -> str:
        return self.outs[0]

    def param(self, name: str) -> Param | None:
        for item in self.params:
            if item.name == name:
                return item
        return None

    def unit(self, params: Mapping, out: str) -> str:
        unit = self.units[out]
        if unit != "source":
            return unit
        return QTY if params.get("source") in VOLUME_SOURCES else PAISE


def _length(default: int, low: int = 1, high: int = 500, name: str = "length") -> Param:
    return Param(name, "int", default, low, high, words=name.replace("_", " "))


def _dec(name: str, default: str, low: str, high: str) -> Param:
    return Param(name, "dec", Fraction(default), Fraction(low), Fraction(high), words=name)


def _source(default: str = "close", choices: tuple = SOURCES) -> Param:
    return Param("source", "choice", default, choices=choices, words="source")


def _times(factor: int, name: str = "length"):
    return lambda p, out: factor * p[name]


def _fixed(unit: str, *outs: str) -> dict[str, str]:
    return dict.fromkeys(outs, unit)


def _hma_warmup(p: Mapping, out: str) -> int:
    return p["length"] + math.ceil(math.sqrt(p["length"]))


def _named(label: str, *names: str) -> Callable[[Mapping], str]:
    """'EMA 20', 'MACD 12, 26, 9': the label and the parameters, in their order."""

    def title(p: Mapping) -> str:
        shown = [
            plain(p[name]) if isinstance(p[name], Fraction) else str(p[name])
            for name in names
            if name in p
        ]
        return f"{label} {', '.join(shown)}" if shown else label

    return title


_CLASSIC = ("P", "R1", "R2", "R3", "S1", "S2", "S3")
_CPR = ("P", "TC", "BC")
_PRICE_ONLY = ("close", "open", "high", "low")
_MINUTES = (1, 3, 5, 15, 30, 60)

IND: dict[str, IndSpec] = {
    "sma": IndSpec("SMA", "library", (_length(20), _source()), ("value",),
                   {"value": "source"}, _times(1), _named("SMA", "length")),
    "wma": IndSpec("WMA", "library", (_length(20), _source()), ("value",),
                   {"value": "source"}, _times(1), _named("WMA", "length")),
    "vwma": IndSpec("VWMA", "library", (_length(20), _source("close", PRICE_SOURCES)), ("value",),
                    {"value": PAISE}, _times(1), _named("VWMA", "length")),
    "ema": IndSpec("EMA", "library", (_length(20), _source()), ("value",),
                   {"value": "source"}, _times(3), _named("EMA", "length")),
    "hma": IndSpec("HMA", "library", (_length(20),), ("value",), {"value": PAISE},
                   _hma_warmup, _named("HMA", "length")),
    "tema": IndSpec("TEMA", "library", (_length(20),), ("value",), {"value": PAISE},
                    _times(9), _named("TEMA", "length")),
    "rsi": IndSpec("RSI", "library", (_length(14, 2, 200),), ("value",), {"value": NUM},
                   _times(7), _named("RSI", "length")),
    "macd": IndSpec("MACD", "library",
                    (_length(12, 1, 200, "fast"), _length(26, 2, 400, "slow"),
                     _length(9, 1, 200, "signal")),
                    ("macd", "signal", "hist"), _fixed(PAISE, "macd", "signal", "hist"),
                    lambda p, out: 3 * p["slow"] + 3 * p["signal"],
                    _named("MACD", "fast", "slow", "signal")),
    "stochastic": IndSpec("Stochastic", "library",
                          (_length(14, 1, 200, "k"), _length(3, 1, 50, "d"),
                           _length(3, 1, 50, "smooth")),
                          ("k", "d"), _fixed(NUM, "k", "d"),
                          lambda p, out: p["k"] + p["d"] + p["smooth"],
                          _named("Stochastic", "k", "d", "smooth")),
    "supertrend": IndSpec("Supertrend", "library",
                          (_length(10, 1, 100, "atr_length"), _dec("factor", "3", "0.5", "10")),
                          ("value", "direction"), {"value": PAISE, "direction": NUM},
                          _times(7, "atr_length"), _named("Supertrend", "atr_length", "factor"),
                          meanings={"direction": {-1: "up trend", 1: "down trend"}}),
    "bbands": IndSpec("Bollinger Bands", "library",
                      (_length(20, 2, 500), _dec("mult", "2", "0.1", "10")),
                      ("upper", "middle", "lower", "pct_b", "width"),
                      {"upper": PAISE, "middle": PAISE, "lower": PAISE, "pct_b": NUM, "width": NUM},
                      _times(1), _named("Bollinger Bands", "length", "mult")),
    "adx": IndSpec("ADX", "library", (_length(14, 2, 100),), ("adx", "plus_di", "minus_di"),
                   _fixed(NUM, "adx", "plus_di", "minus_di"), _times(10), _named("ADX", "length")),
    "atr": IndSpec("ATR", "library", (_length(14, 1, 100),), ("value",), {"value": PAISE},
                   _times(7), _named("ATR", "length")),
    "cci": IndSpec("CCI", "library", (_length(20, 2, 200),), ("value",), {"value": NUM},
                   lambda p, out: p["length"] + 1, _named("CCI", "length")),
    "mfi": IndSpec("MFI", "library", (_length(14, 2, 200),), ("value",), {"value": NUM},
                   lambda p, out: p["length"] + 1, _named("MFI", "length")),
    "cmf": IndSpec("CMF", "library", (_length(20, 2, 200),), ("value",), {"value": NUM},
                   lambda p, out: p["length"] + 1, _named("CMF", "length")),
    "williams_r": IndSpec("Williams %R", "library", (_length(14, 2, 200),), ("value",),
                          {"value": NUM}, lambda p, out: p["length"] + 1,
                          _named("Williams %R", "length")),
    "donchian": IndSpec("Donchian", "library", (_length(20, 2, 500),),
                        ("upper", "lower", "middle"), _fixed(PAISE, "upper", "lower", "middle"),
                        _times(1), _named("Donchian", "length")),
    "keltner": IndSpec("Keltner", "library",
                       (_length(20, 2, 200), _length(10, 1, 100, "atr_length"),
                        _dec("mult", "2", "0.1", "10")),
                       ("upper", "middle", "lower"), _fixed(PAISE, "upper", "middle", "lower"),
                       lambda p, out: max(3 * p["length"], 7 * p["atr_length"]),
                       _named("Keltner", "length", "atr_length", "mult")),
    "rvol": IndSpec("Relative volume", "library", (_length(20, 2, 500),), ("value",),
                    {"value": NUM}, _times(1), _named("Relative volume", "length")),
    "obv": IndSpec("OBV", "library", (), ("value",), {"value": QTY}, lambda p, out: 1,
                   lambda p: "OBV"),
    # The screener's own: cheap, so they do not count toward the 12 instances.
    "highest": IndSpec("Highest", "price",
                       (_length(20), _source("high", (*_PRICE_ONLY, "volume"))), ("value",),
                       {"value": "source"}, _times(1), _named("Highest", "length")),
    "lowest": IndSpec("Lowest", "price",
                      (_length(20), _source("low", (*_PRICE_ONLY, "volume"))), ("value",),
                      {"value": "source"}, _times(1), _named("Lowest", "length")),
    "orb_high": IndSpec("Opening range high", "price",
                        (Param("minutes", "int", 15, 5, 60, words="minutes"),), ("value",),
                        {"value": PAISE}, lambda p, out: 1, _named("Opening range high", "minutes"),
                        tfs=_MINUTES),
    "orb_low": IndSpec("Opening range low", "price",
                       (Param("minutes", "int", 15, 5, 60, words="minutes"),), ("value",),
                       {"value": PAISE}, lambda p, out: 1, _named("Opening range low", "minutes"),
                       tfs=_MINUTES),
    "pivot": IndSpec("Pivot", "price",
                     (Param("kind", "choice", "classic", choices=("classic", "cpr"), words="kind"),),
                     (*_CLASSIC, "TC", "BC"), _fixed(PAISE, *_CLASSIC, "TC", "BC"),
                     lambda p, out: 2, lambda p: f"Pivot ({p['kind']})", tfs=(DAY,)),
    "rs": IndSpec("Relative strength", "price",
                  (Param("index", "choice", "NIFTY50", choices=("NIFTY50", "NIFTYBANK"),
                         words="index"), _length(20, 1, 100, "n")),
                  ("value",), {"value": PCT}, lambda p, out: p["n"] + 1,
                  lambda p: f"Relative strength {p['n']} vs {p['index']}"),
    "pattern": IndSpec("Candle pattern", "price", (), ("state",), {"state": STATE},
                       lambda p, out: 1, lambda p: "Candle pattern"),
}  # fmt: skip
PIVOT_OUTS = {"classic": _CLASSIC, "cpr": _CPR}
PATTERNS = (
    "bullish_engulfing", "bearish_engulfing", "doji", "hammer", "shooting_star", "inside_bar", "nr7",
)  # fmt: skip
BUILDUP_STATES = ("long_buildup", "short_buildup", "short_covering", "long_unwinding", "none")
STATES = {"buildup": BUILDUP_STATES, "pattern": PATTERNS}
#: Candles a pattern needs to exist (the current one and what it is compared with).
PATTERN_NEEDS = dict.fromkeys(PATTERNS, 2) | {"doji": 1, "hammer": 1, "shooting_star": 1, "nr7": 7}

# ---- open interest and options -----------------------------------------------------------------
OI_WHATS = {"buildup": STATE, "oi_change_pct": PCT}
OI_WINDOWS = ("day", "candles")
UNDERLYINGS = ("NIFTY", "BANKNIFTY")


@dataclass(frozen=True, slots=True)
class OptSpec:
    unit: str
    words: str
    #: True: reads a timeframe (the change over one candle).
    timed: bool = False
    #: True: names an underlying; False: the India VIX stand-in.
    underlying: bool = True


OPT: dict[str, OptSpec] = {
    "pcr": OptSpec(NUM, "PCR"),
    "max_pain": OptSpec(PAISE, "Max pain"),
    "atm_iv": OptSpec(PCT, "ATM implied volatility"),
    "iv_change": OptSpec(PCT, "Change in ATM implied volatility", timed=True),
    "straddle": OptSpec(PAISE, "ATM straddle price"),
    "iv_rank": OptSpec(PCT, "IV rank"),
    "iv_pct": OptSpec(PCT, "IV percentile"),
    "vix_rank": OptSpec(PCT, "India VIX rank", underlying=False),
    "vix_pct": OptSpec(PCT, "India VIX percentile", underlying=False),
}

# ---- operators -------------------------------------------------------------------------------
BINARY, UNARY = "binary", "unary"


@dataclass(frozen=True, slots=True)
class OpSpec:
    arity: str
    words: str
    #: The arguments it takes: "pct" (a percent), "n" (candles), "low"/"high", "name" (a state).
    args: tuple = ()
    crossing: bool = False


OPS: dict[str, OpSpec] = {
    "gt": OpSpec(BINARY, "Is above"),
    "gte": OpSpec(BINARY, "Is at or above"),
    "lt": OpSpec(BINARY, "Is below"),
    "lte": OpSpec(BINARY, "Is at or below"),
    "eq": OpSpec(BINARY, "Equals"),
    "between": OpSpec(UNARY, "Is between", ("low", "high")),
    "within_pct": OpSpec(BINARY, "Is within % of", ("pct",)),
    "crosses_above": OpSpec(BINARY, "Crosses above", crossing=True),
    "crosses_below": OpSpec(BINARY, "Crosses below", crossing=True),
    "rising": OpSpec(UNARY, "Is rising for", ("n",)),
    "falling": OpSpec(UNARY, "Is falling for", ("n",)),
    "is": OpSpec(UNARY, "Is", ("name",)),
}
MAX_RISING = 20
MATH_OPS = ("add", "sub", "mul", "div")

# ---- where a screen looks and when -----------------------------------------------------------
UNIVERSES = ("fno_stocks", "fno_futures", "index_futures", "indices", "options", "index_list",
             "watchlist")  # fmt: skip
#: Shipped lists: id -> (name, followed tick by tick, so a candle-close screen can use it).
INDEX_LISTS: dict[str, tuple[str, bool]] = {
    "NIFTY50": ("NIFTY 50", True),
    "NIFTYBANK": ("NIFTY BANK", True),
    "NIFTY100": ("NIFTY 100", False),
    "NIFTY200": ("NIFTY 200", False),
    "NIFTY500": ("NIFTY 500", False),
}
#: Universes with open interest.
OI_UNIVERSES = frozenset({"fno_futures", "index_futures", "options", "watchlist"})
RUN_ON = ("close", "after_close")
RUN_TFS = (1, 3, 5, 15, 30, 60)
GRACE_MODES = ("runs", "day_end", "never")
AFTER_TRIGGER = ("continue", "pause")
ALERT_COLUMNS = ("ltp", "change_pct", "value")
DEFAULT_GRACE_RUNS = 2
DEFAULT_MAX_ADDS = 10
MAX_ADDS = 250
DEFAULT_ALERT_CAP = 3
