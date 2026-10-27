"""The screener's vocabulary: timeframes, three-valued logic, candles, compiled operands, outcomes.

Pure, like the rest of the engine: nothing here opens a file, reads a clock or logs.

Numbers keep their kind so a comparison is exact wherever the data is exact: a price is an ``int``
in paise, a quantity an ``int``, a typed number or a derived ratio a ``Fraction`` and only an
indicator's output is a ``float`` (float64, in paise units for a price indicator). ``engine`` compares
exact with exact by cross-multiplication and falls back to floats only when one side is an indicator.

A result that cannot be computed is not a ``False``: it is ``Tri.UNKNOWN`` with a ``Why``. A root
that stays UNKNOWN is "Not checked", never a miss, so a data gap can never empty a watchlist.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from fractions import Fraction
from typing import NamedTuple

import numpy as np

# Timeframes: minutes, then the day and the week.
TF = int | str
MINUTE_TFS: tuple[int, ...] = (1, 3, 5, 15, 30, 60)
DAY, WEEK = "D", "W"
TIMEFRAMES: tuple[TF, ...] = (*MINUTE_TFS, DAY, WEEK)
#: Offsets other than "n candles ago" (an int, 0 is the latest candle).
FIRST_TODAY, LAST_PREV_DAY = "first_today", "last_prev_day"
MAX_AGO = 100

# What a value is measured in. A typed number adopts the unit of what it is compared with.
PAISE, QTY, PCT, RUPEES, NUM, STATE = "paise", "qty", "pct", "rupees", "num", "state"
UNITS = (PAISE, QTY, PCT, RUPEES, NUM, STATE)


def plain(value: Fraction | int) -> str:
    """A number as plain decimal text with no trailing zeros: Fraction(5, 2) -> '2.5', 3 -> '3'."""
    value = Fraction(value)
    num, den = value.numerator, value.denominator
    if den == 1:
        return str(num)
    places, scale = 0, 1
    while scale % den and places < 12:
        scale, places = scale * 10, places + 1
    if scale % den:  # a ratio with no short decimal form (1/3): six places
        places, scale = 6, 10**6
        digits = round(abs(value) * scale)
    else:
        digits = abs(num) * (scale // den)
    text = str(digits).rjust(places + 1, "0")
    text = f"{text[:-places]}.{text[-places:]}".rstrip("0").rstrip(".")
    return f"-{text}" if num < 0 and text != "0" else text


class Tri(enum.Enum):
    """Kleene's three truth values. Never use one as a bool: ``if tri:`` raises."""

    FALSE = 0
    TRUE = 1
    UNKNOWN = 2

    def __bool__(self) -> bool:
        raise TypeError("a three-valued result has no truth value: compare it with Tri.TRUE")


TRUE, FALSE, UNKNOWN = Tri.TRUE, Tri.FALSE, Tri.UNKNOWN


def tri(value: bool) -> Tri:
    return TRUE if value else FALSE


def all_of(items: Iterable[Tri]) -> Tri:
    """Any FALSE gives FALSE, else any UNKNOWN gives UNKNOWN. No items decide nothing: UNKNOWN."""
    seen = unknown = False
    for item in items:
        seen = True
        if item is FALSE:
            return FALSE
        unknown = unknown or item is UNKNOWN
    return UNKNOWN if unknown or not seen else TRUE


def any_of(items: Iterable[Tri]) -> Tri:
    """Any TRUE gives TRUE, else any UNKNOWN gives UNKNOWN. No items decide nothing: UNKNOWN."""
    seen = unknown = False
    for item in items:
        seen = True
        if item is TRUE:
            return TRUE
        unknown = unknown or item is UNKNOWN
    return UNKNOWN if unknown or not seen else FALSE


def not_(item: Tri) -> Tri:
    return UNKNOWN if item is UNKNOWN else (FALSE if item is TRUE else TRUE)


def none_of(items: Iterable[Tri]) -> Tri:
    """NOT ANY."""
    return not_(any_of(items))


# Why a value could not be computed. ``detail`` holds the numbers words.py needs to say it.
WARMING = "warming"  # (need, have): an indicator holds fewer candles than its warm-up
NO_CANDLE = "no_candle"  # (): no candle at that offset
NO_DATA = "no_data"  # (name,): a value the instrument has not got (previous close, OI, VWAP, ...)
ZERO = "zero"  # (): a division by zero
NOT_A_NUMBER = "nan"  # (): the library returned no number
PAUSED = "paused"  # (start,): the price feed paused during the candle and it was not repaired
NO_AUCTION = "no_auction"  # (): a closing-auction stock whose auction price has not come
NO_OPTIONS = "no_options"  # (underlying,): the option strikes are not followed
SHORT_STORE = "short_store"  # (have, need): fewer stored days than the rank asks for
WINDOW_OPEN = "window_open"  # (minutes,): the opening range has not ended
OTHER_DAY = "other_day"  # (name,): only today's candles can be read for this value
NO_FEED = "no_feed"  # (): no price from the feed yet


@dataclass(frozen=True, slots=True)
class Why:
    code: str
    detail: tuple = ()


class Candle(NamedTuple):
    """One candle of any timeframe. Prices in paise, volume and OI as integers."""

    start: int  # epoch ms
    end: int
    session: int  # YYYYMMDD of the session it belongs to
    o: int
    h: int
    low: int
    c: int
    v: int
    oi: int = -1  # -1: no open interest (cash, indices)
    atp: int = 0  # session VWAP at the candle's last tick, in paise; 0: none
    flags: int = 0


#: Candle flags: a bar built while the feed paused, one repaired from REST, one that took a late
#: trade or the exchange's own 1-minute candle.
UNVERIFIED, REPAIRED, LATE, FROM_I1 = 1, 2, 4, 8
_COLUMNS = ("start", "end", "session", "o", "h", "l", "c", "v", "oi", "atp", "flags")


@dataclass(frozen=True, slots=True, eq=False)
class Series:
    """Candles of one timeframe, oldest first, as read-only columns.

    ``forming`` is True when the last candle had not ended at the boundary the series was cut for:
    the screener reads only sealed 1-minute bars, so a day candle at the 10:15 run is the
    aggregate of 09:15 to 10:15.
    """

    tf: TF
    start: np.ndarray
    end: np.ndarray
    session: np.ndarray
    o: np.ndarray
    h: np.ndarray
    l: np.ndarray  # noqa: E741 (low: the column names mirror o, h, c)
    c: np.ndarray
    v: np.ndarray
    oi: np.ndarray
    atp: np.ndarray
    flags: np.ndarray
    forming: bool = False

    def __len__(self) -> int:
        return len(self.start)

    @classmethod
    def of(cls, tf: TF, candles: Iterable[Candle], forming: bool = False) -> Series:
        rows = list(candles)
        columns = []
        for index in range(len(_COLUMNS)):
            column = np.array([row[index] for row in rows], dtype=np.int64)
            column.flags.writeable = False
            columns.append(column)
        return cls(tf, *columns, forming=forming)

    def candle(self, index: int) -> Candle:
        return Candle(
            int(self.start[index]), int(self.end[index]), int(self.session[index]),
            int(self.o[index]), int(self.h[index]), int(self.l[index]), int(self.c[index]),
            int(self.v[index]), int(self.oi[index]), int(self.atp[index]), int(self.flags[index]),
        )  # fmt: skip

    def column(self, name: str) -> np.ndarray:
        return getattr(self, name)

    def index_of_start(self, start: int) -> int | None:
        """The index of the candle that starts at ``start``, or None."""
        at = int(np.searchsorted(self.start, start))
        return at if at < len(self) and int(self.start[at]) == start else None


# Compiled operands. Frozen and hashable, so an indicator is computed once per distinct instance.
@dataclass(frozen=True, slots=True)
class Num:
    #: In the base unit of ``unit``: a price typed in rupees is held in paise.
    value: Fraction
    #: The number as the owner typed it, normalised ("1.5"), for words and values.
    text: str
    unit: str = NUM
    kind = "num"


@dataclass(frozen=True, slots=True)
class Field:
    name: str
    tf: TF | None  # None for a session-level field (prev_close, day_high, ...)
    at: int | str
    unit: str
    kind = "field"


@dataclass(frozen=True, slots=True)
class Ind:
    name: str
    params: tuple  # sorted ((name, value), ...)
    out: str
    tf: TF
    at: int | str
    unit: str
    kind = "ind"

    @property
    def instance(self) -> tuple:
        """What is computed once: the same instance serves every output, offset and screen."""
        return (self.name, self.params, self.tf)


@dataclass(frozen=True, slots=True)
class Oi:
    what: str  # "buildup" | "oi_change_pct"
    window: tuple  # ("day",) or ("candles", n)
    tf: TF
    #: Smallest |price move| and |OI move| (percent) that count as a state at all.
    min_price: Fraction
    min_oi: Fraction
    unit: str
    kind = "oi"


@dataclass(frozen=True, slots=True)
class Opt:
    what: str  # pcr | max_pain | atm_iv | iv_change | straddle | iv_rank | iv_pct | vix_rank | ...
    underlying: str  # "NIFTY" | "BANKNIFTY" | "self"
    tf: TF | None  # only iv_change reads it
    unit: str
    kind = "opt"


@dataclass(frozen=True, slots=True)
class Math:
    op: str  # add | sub | mul | div
    a: Operand
    b: Operand
    unit: str
    kind = "math"


Operand = Num | Field | Ind | Oi | Opt | Math


@dataclass(frozen=True, slots=True)
class Clause:
    id: str
    left: Operand
    op: str
    right: Operand | None
    #: Operator arguments, sorted ((name, value), ...): pct, n, low, high, name.
    args: tuple = ()

    def arg(self, name: str):
        for key, value in self.args:
            if key == name:
                return value
        return None


@dataclass(frozen=True, slots=True)
class Group:
    kind: str  # "all" | "any" | "none"
    items: tuple


@dataclass(frozen=True, slots=True)
class Not:
    item: Node


Node = Clause | Group | Not


@dataclass(frozen=True, slots=True)
class Plan:
    """A screen compiled for the engine: lines that are off are gone."""

    #: "close" (every candle end of ``tf``) or "after_close" (16:05, day and week candles).
    on: str
    tf: TF
    root: Node
    #: The lines in tree order, for the values a match keeps.
    clauses: tuple
    rank: Operand | None
    rank_order: str
    #: Distinct library indicators (name, params, tf): at most 12.
    instances: tuple
    #: Candles each timeframe must hold for the plan to be fully ready.
    need: Mapping[TF, int]
    #: What outside data the plan reads: "options", "index", "oi", "vix".
    uses: frozenset


# What one instrument's judgement at one boundary came to.
HIT, MISS, NOT_CHECKED, NOT_DUE = "hit", "miss", "not_checked", "not_due"
OUTCOMES = (HIT, MISS, NOT_CHECKED, NOT_DUE)


@dataclass(frozen=True, slots=True)
class Outcome:
    kind: str
    why: tuple = ()  # Why, for NOT_CHECKED
    #: clause id -> {"l": text, "r": text | None, "forming": bool}, kept for a HIT.
    values: Mapping | None = None
    #: The rank value (an exact number or a float), None when it could not be computed.
    rank: object = None
    #: A candle read at this run had been repaired after a pause.
    repaired: bool = False
    forming: bool = False


class RunKey(NamedTuple):
    """``<screen>:v<version>:<session>:<tf>:<k>``: unique per run in the store."""

    screen: int
    version: int
    session: int
    tf: str  # "1", "3", "5", "15", "30", "60" or "after_close"
    k: int

    def __str__(self) -> str:
        return f"{self.screen}:v{self.version}:{self.session}:{self.tf}:{self.k}"

    @classmethod
    def parse(cls, text: str) -> RunKey:
        screen, version, session, tf, k = text.split(":")
        if not version.startswith("v"):
            raise ValueError(f"not a run key: {text!r}")
        return cls(int(screen), int(version[1:]), int(session), tf, int(k))
