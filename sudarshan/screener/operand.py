"""Operands and clauses: what a screen reads and how it compares, checked in the owner's words.

``operand`` and ``clause`` take what the phone sent and return two things: the stored form, with
every default filled in and every number as text, and the compiled form the engine runs. A thing
the engine cannot run raises ``SpecError``: the field it is about and one sentence saying what to
change. Pure: nothing here opens a file or reads a clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

from sudarshan.alerts.market.grain import to_decimal
from sudarshan.screener import catalog as cat
from sudarshan.screener.types import (
    DAY,
    FIRST_TODAY,
    LAST_PREV_DAY,
    MAX_AGO,
    MINUTE_TFS,
    NUM,
    PAISE,
    STATE,
    TF,
    WEEK,
    Clause,
    Field,
    Ind,
    Math,
    Num,
    Oi,
    Operand,
    Opt,
    plain,
)

NUMBER_LIMIT = 10**12
NUMBER_PLACES = 6
ALL_TFS_WORDS = "Pick a timeframe: 1, 3, 5, 15, 30 or 60 minutes, day or week."


class SpecError(ValueError):
    """A screen the engine cannot run; ``message`` is for the owner, ``field`` for the form."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field
        self.message = message


@dataclass
class Scope:
    """What the screen being checked says about itself, and what its operands add up to."""

    on: str  # "close" | "after_close"
    tf: TF  # the run's candle: DAY after the close
    universe: str
    need: dict = field(default_factory=dict)
    instances: set = field(default_factory=set)
    uses: set = field(default_factory=set)

    def require(self, tf: TF, count: int, who: str, hint: str, path: str) -> None:
        kept = cat.KEPT[tf]
        if count > kept:
            raise SpecError(
                path,
                f"{who} on {cat.candles_of(tf)} needs {count} candles of history before it is "
                f"ready; the screener keeps {kept}. {hint}",
            )
        self.need[tf] = max(self.need.get(tf, 0), count)


def number(raw, path: str) -> tuple[Fraction, str]:
    """A typed number as an exact fraction and as clean text."""
    value = to_decimal(raw)
    if value is None or abs(value) > NUMBER_LIMIT or value.as_tuple().exponent < -NUMBER_PLACES:
        raise SpecError(path, "Type a number, like 55 or 2500.50.")
    exact = Fraction(value)
    return exact, plain(exact)


def whole(raw, path: str, low: int, high: int, words: str) -> int:
    """An integer in [low, high]; a float is accepted only when it is a whole number."""
    kind = raw.__class__
    if kind is float and raw.is_integer():
        raw, kind = int(raw), int
    if kind is not int or not low <= raw <= high:
        raise SpecError(path, f"{words} must be a whole number from {low} to {high}.")
    return raw


def timeframe(raw, scope: Scope, path: str) -> TF:
    """The candle size of an operand: the run's own when none is given."""
    if raw is None:
        return scope.tf
    if raw.__class__ is str:
        known = raw in (DAY, WEEK)
    else:
        known = raw.__class__ is int and raw in MINUTE_TFS
    if not known:
        raise SpecError(path, ALL_TFS_WORDS)
    if scope.on == "after_close" and raw not in (DAY, WEEK):
        raise SpecError(
            path, "A check after the close can only read day and week candles. Pick Day or Week."
        )
    return raw


def offset(raw, tf: TF, path: str) -> tuple[object, int | str]:
    """(stored form, value): 0, {"ago": n}, "first_today" or "last_prev_day"."""
    if raw is None or raw == 0 and raw.__class__ is int:
        return 0, 0
    if raw.__class__ is dict and set(raw) == {"ago"}:
        raw = raw["ago"]
    if raw.__class__ is int:
        if raw == 0:
            return 0, 0
        if 0 < raw <= MAX_AGO:
            return {"ago": raw}, raw
        raise SpecError(path, f"You can look back {MAX_AGO} candles at most.")
    if raw in (FIRST_TODAY, LAST_PREV_DAY):
        if tf in (DAY, WEEK):
            first = raw == FIRST_TODAY
            what = "Today's first candle" if first else "Yesterday's last candle"
            raise SpecError(
                path,
                f"{what} is for minute candles. For {cat.TF_WORDS[tf]} candles, use 1 candle ago.",
            )
        return raw, raw
    raise SpecError(
        path,
        "Pick the latest candle, some candles ago, today's first candle or yesterday's last candle.",
    )


def _ago(at) -> int:
    return at if at.__class__ is int else 0


# ---- operands ----------------------------------------------------------------------------------
_KINDS = ("num", "field", "ind", "oi", "opt", "math")


def operand(
    raw, scope: Scope, path: str, what: str = "Pick what to check."
) -> tuple[dict, Operand]:
    if raw.__class__ is not dict:
        raise SpecError(path, what)
    kinds = [kind for kind in _KINDS if kind in raw]
    if len(kinds) != 1:
        raise SpecError(path, what)
    return _BUILD[kinds[0]](raw, scope, path)


def _num(raw: dict, scope: Scope, path: str):
    value, text = number(raw["num"], path)
    return {"num": text}, Num(value, text)


def _field(raw: dict, scope: Scope, path: str):
    name = raw["field"]
    spec = cat.FIELDS.get(name) if name.__class__ is str else None
    if spec is None:
        raise SpecError(path, "Pick a price, volume or level from the list.")
    if name == "oi" and scope.universe not in cat.OI_UNIVERSES:
        raise SpecError(path, _no_oi())
    if not spec.candle:
        return {"field": name}, Field(name, None, 0, spec.unit)
    tf = timeframe(raw.get("tf"), scope, f"{path}.tf")
    stored_at, at = offset(raw.get("at"), tf, f"{path}.at")
    return {"field": name, "tf": tf, "at": stored_at}, Field(name, tf, at, spec.unit)


def _no_oi() -> str:
    return "Open interest exists for futures and options. Pick F&O futures, index futures or option strikes."


def _params(spec: cat.IndSpec, raw, path: str) -> tuple[dict, dict]:
    """(stored, values): the settings with defaults; decimals are text in one, fractions in the other."""
    if raw is None:
        raw = {}
    if raw.__class__ is not dict:
        raise SpecError(f"{path}.p", f"Check the settings of {spec.label}.")
    known = {item.name for item in spec.params}
    for name in raw:
        if name not in known:
            raise SpecError(f"{path}.p", f"{spec.label} has no setting called '{name}'.")
    stored, values = {}, {}
    for item in spec.params:
        value = raw.get(item.name, item.default)
        where = f"{path}.p.{item.name}"
        words = f"{spec.label} {item.words}"
        if item.kind == "int":
            values[item.name] = stored[item.name] = whole(value, where, item.low, item.high, words)
        elif item.kind == "dec":
            exact, text = number(plain(value) if value.__class__ is Fraction else value, where)
            if not item.low <= exact <= item.high:
                raise SpecError(
                    where, f"{words} must be a number from {plain(item.low)} to {plain(item.high)}."
                )
            values[item.name], stored[item.name] = exact, text
        else:
            if value not in item.choices:
                raise SpecError(where, f"{words} must be one of: {', '.join(item.choices)}.")
            values[item.name] = stored[item.name] = value
    return stored, values


def _ind(raw: dict, scope: Scope, path: str):
    name = raw["ind"]
    spec = cat.IND.get(name) if name.__class__ is str else None
    if spec is None:
        raise SpecError(path, "Pick an indicator from the list.")
    stored_p, values = _params(spec, raw.get("p"), path)
    out = raw.get("out", spec.default_out)
    outs = cat.PIVOT_OUTS[values["kind"]] if name == "pivot" else spec.outs
    if out not in outs:
        raise SpecError(f"{path}.out", f"{spec.label} has no line called '{out}'.")
    if name == "pivot":
        tf, stored_at, at = DAY, None, 0
    else:
        tf = timeframe(raw.get("tf"), scope, f"{path}.tf")
        stored_at, at = offset(raw.get("at"), tf, f"{path}.at")
    if spec.tfs is not None and tf not in spec.tfs:
        raise SpecError(f"{path}.tf", f"{spec.label} can only be read on {_tfs_words(spec.tfs)}.")
    _related(spec, name, values, tf, path)
    if spec.family == "library":
        scope.instances.add((name, tuple(sorted(values.items())), tf))
    if name == "rs":
        scope.uses.add("index")
    stored = {"ind": name, "p": stored_p, "out": out, "tf": tf}
    if stored_at is not None:
        stored["at"] = stored_at
    unit = spec.unit(values, out)
    return stored, Ind(name, tuple(sorted(values.items())), out, tf, at, unit)


def _tfs_words(tfs: tuple) -> str:
    return "day candles" if tfs == (DAY,) else "minute candles"


def _related(spec: cat.IndSpec, name: str, values: dict, tf: TF, path: str) -> None:
    """Settings that only make sense together."""
    if name == "macd" and values["fast"] >= values["slow"]:
        raise SpecError(f"{path}.p.fast", "MACD fast length must be shorter than its slow length.")
    if name in ("orb_high", "orb_low"):
        minutes = values["minutes"]
        if minutes % 5 or (tf.__class__ is int and minutes % tf):
            raise SpecError(
                f"{path}.p.minutes",
                f"An opening range of {minutes} minutes cannot be built from {cat.candles_of(tf)}. "
                "Use whole steps of 5 minutes and candles that divide it.",
            )


def _oi(raw: dict, scope: Scope, path: str):
    what = raw["oi"]
    if what.__class__ is not str or what not in cat.OI_WHATS:
        raise SpecError(path, "Pick the open interest build-up or its change.")
    if scope.universe not in cat.OI_UNIVERSES:
        raise SpecError(path, _no_oi())
    window = raw.get("window") or {"kind": "day"}
    kind = window.get("kind") if window.__class__ is dict else None
    if kind == "day":
        stored_window, compiled_window, tf = {"kind": "day"}, ("day",), scope.tf
        stored_tf = {}
    elif kind == "candles":
        tf = timeframe(raw.get("tf"), scope, f"{path}.tf")
        n = whole(window.get("n"), f"{path}.window.n", 1, 50, "The number of candles")
        stored_window, compiled_window, stored_tf = (
            {"kind": "candles", "n": n},
            ("candles", n),
            {"tf": tf},
        )
    else:
        raise SpecError(f"{path}.window", "Pick the day so far or a number of candles.")
    stored = {"oi": what, "window": stored_window, **stored_tf}
    zero = Fraction(0)
    min_price = min_oi = zero
    if what == "buildup":
        for name, key in (("min_price_pct", "price"), ("min_oi_pct", "OI")):
            value, text = number(raw.get(name, "0"), f"{path}.{name}")
            if value < 0:
                raise SpecError(f"{path}.{name}", f"The smallest {key} move cannot be below 0.")
            stored[name] = text
            if name == "min_price_pct":
                min_price = value
            else:
                min_oi = value
    scope.uses.add("oi")
    return stored, Oi(what, compiled_window, tf, min_price, min_oi, cat.OI_WHATS[what])


def _opt(raw: dict, scope: Scope, path: str):
    what = raw["opt"]
    spec = cat.OPT.get(what) if what.__class__ is str else None
    if spec is None:
        raise SpecError(path, "Pick PCR, max pain, implied volatility, the straddle or a rank.")
    stored: dict = {"opt": what}
    underlying = "NIFTY"
    if spec.underlying:
        underlying = raw.get("underlying", "self")
        if underlying not in (*cat.UNDERLYINGS, "self"):
            raise SpecError(
                f"{path}.underlying", "Pick NIFTY, BANKNIFTY or the row's own underlying."
            )
        stored["underlying"] = underlying
    tf = None
    if spec.timed:
        tf = timeframe(raw.get("tf"), scope, f"{path}.tf")
        stored["tf"] = tf
    scope.uses.add("options" if spec.underlying else "vix")
    return stored, Opt(what, underlying, tf, spec.unit)


def _math(raw: dict, scope: Scope, path: str):
    op, args = raw["math"], raw.get("args")
    if op not in cat.MATH_OPS or args.__class__ is not list or len(args) != 2:
        raise SpecError(path, "Pick add, subtract, multiply or divide, with two values.")
    (sa, a), (sb, b) = (
        operand(args[0], scope, f"{path}.args.0", "Pick the first value."),
        operand(args[1], scope, f"{path}.args.1", "Pick the second value."),
    )
    if STATE in (a.unit, b.unit):
        raise SpecError(path, "Arithmetic works on numbers, not on states like a build-up.")
    a_num, b_num = a.kind == "num", b.kind == "num"
    if a_num and b_num:
        raise SpecError(path, "Both values are plain numbers. Type the result instead.")
    if op in ("add", "sub"):
        if a_num:
            a = adopt(a, b.unit)
        elif b_num:
            b = adopt(b, a.unit)
        elif a.unit != b.unit:
            raise SpecError(
                path,
                "Add or subtract values of the same kind: price with price, volume with volume.",
            )
        unit = b.unit if a_num else a.unit
    elif op == "mul":
        if not (a_num or b_num):
            raise SpecError(path, "Multiply a value by a number. Two values cannot be multiplied.")
        unit = (b if a_num else a).unit
    else:
        if a_num:
            raise SpecError(path, "Divide a value by a number, or two values of the same kind.")
        if b_num and b.value == 0:
            raise SpecError(f"{path}.args.1", "You cannot divide by zero.")
        unit = a.unit if b_num else (NUM if a.unit == b.unit else None)
        if unit is None:
            raise SpecError(path, "Divide a value by a number, or two values of the same kind.")
    for part in (a, b):
        _only_obv(part, path)
    stored_args = [sa if not a_num else {"num": a.text}, sb if not b_num else {"num": b.text}]
    return {"math": op, "args": stored_args}, Math(op, a, b, unit)


_BUILD = {"num": _num, "field": _field, "ind": _ind, "oi": _oi, "opt": _opt, "math": _math}


def adopt(num: Num, unit: str) -> Num:
    """A typed number in the unit it is compared with: prices are typed in rupees."""
    if unit == PAISE:
        return Num(num.value * 100, num.text, PAISE)
    return Num(num.value, num.text, unit)


def is_obv(item: Operand) -> bool:
    return item.kind == "ind" and (item.name == "obv" or dict(item.params).get("source") == "obv")


def _only_obv(item: Operand, path: str) -> None:
    if is_obv(item):
        raise SpecError(path, OBV_WORDS)


OBV_WORDS = (
    "On-balance volume starts from the first candle the screener holds, so its level means nothing. "
    "Compare it with its own average, or use rising or falling."
)


# ---- reading the plan's reach ------------------------------------------------------------------
def tfs_of(item: Operand) -> set:
    if item.kind == "math":
        return tfs_of(item.a) | tfs_of(item.b)
    if item.kind in ("num", "opt") and getattr(item, "tf", None) is None:
        return set()
    if item.kind == "field" and item.tf is None:
        return set()
    if item.kind == "ind" and item.name == "pivot":
        return set()
    return {item.tf}


def account(item: Operand, extra: int, scope: Scope, path: str, state: str | None = None) -> None:
    """Add the history ``item`` needs, ``extra`` candles more for a cross or a run of candles.

    ``state`` names the pattern a clause asks about (doji needs one candle, NR7 seven).
    """
    kind = item.kind
    if kind == "math":
        account(item.a, extra, scope, path)
        account(item.b, extra, scope, path)
    elif kind == "field":
        _account_field(item, extra, scope, path)
    elif kind == "ind":
        _account_ind(item, extra, scope, path, state)
    elif kind == "oi":
        count = 1 if item.window[0] == "day" else item.window[1] + 1
        scope.require(item.tf, count + extra, "Looking back that far", _SMALLER, path)
    elif kind == "opt" and item.tf is not None:
        scope.require(item.tf, 2, "Reading the change in implied volatility", _SMALLER, path)


_SMALLER = "Use a smaller number or a smaller timeframe."
_SHORTER = "Use a shorter length or a smaller timeframe."


def _account_field(item: Field, extra: int, scope: Scope, path: str) -> None:
    if item.tf is None:
        if item.name in cat.DAY_LEVELS or item.name in ("year_high", "year_low"):
            scope.require(
                DAY, 2 if item.name.startswith("prev_day") else 1, "Reading the day", _SMALLER, path
            )
        return
    ago = _ago(item.at)
    who = f"Looking {ago} candles back" if ago else "Reading it"
    scope.require(item.tf, ago + 1 + extra, who, _SMALLER, path)
    if item.name == "change_pct":
        scope.require(DAY, 1, "Reading the day", _SMALLER, path)


def _account_ind(item: Ind, extra: int, scope: Scope, path: str, state: str | None) -> None:
    spec = cat.IND[item.name]
    params = dict(item.params)
    if item.name == "pivot":
        scope.require(DAY, 2, spec.title(params), _SHORTER, path)
        return
    ago = _ago(item.at)
    if item.name == "pattern":
        count = cat.PATTERN_NEEDS.get(state, max(cat.PATTERN_NEEDS.values()))
    else:
        count = spec.warmup(params, item.out)
    scope.require(item.tf, count + ago + extra, spec.title(params), _SHORTER, path)


# ---- clauses -----------------------------------------------------------------------------------
_COMPARES = ("gt", "gte", "lt", "lte", "eq", "within_pct", "crosses_above", "crosses_below")


def clause(raw, scope: Scope, path: str, cid: str) -> tuple[dict, Clause]:
    op = raw.get("op")
    spec = cat.OPS.get(op) if op.__class__ is str else None
    if spec is None:
        raise SpecError(f"{cid}.op", "Pick how to compare.")
    s_left, left = operand(raw.get("left"), scope, f"{cid}.left")
    stored = {"id": cid, "on": True, "left": s_left, "op": op}
    right, extra, args, s_args = None, 0, (), {}
    if spec.arity == cat.BINARY:
        s_right, right = operand(
            raw.get("right"), scope, f"{cid}.right", "Pick what to compare it with."
        )
        left, right = _pair(left, right, op, cid)
        stored["left"], stored["right"] = _restate(s_left, left), _restate(s_right, right)
        _binary(spec, op, left, right, cid)
        if spec.crossing:
            extra = 1
    else:
        _unary(op, left, cid)
    if spec.args:
        s_args, args, extra_n = _args(op, raw.get("args"), left, cid)
        stored["args"] = s_args
        extra = max(extra, extra_n)
    account(left, extra, scope, f"{cid}.left", dict(args).get("name"))
    if right is not None:
        account(right, extra, scope, f"{cid}.right")
    return stored, Clause(cid, left, op, right, args)


def _restate(stored: dict, item: Operand) -> dict:
    """The stored form of an operand whose number took its unit: the text the owner typed."""
    return {"num": item.text} if item.kind == "num" else stored


def _pair(left: Operand, right: Operand, op: str, cid: str):
    """Both sides in one unit: a typed number takes the other side's."""
    if STATE in (left.unit, right.unit):
        raise SpecError(
            f"{cid}.op", "A state like a build-up or a pattern is checked with 'Is', not compared."
        )
    l_num, r_num = left.kind == "num", right.kind == "num"
    if l_num and r_num:
        raise SpecError(f"{cid}.right", "Compare a value with a number, not two numbers.")
    if r_num:
        right = adopt(right, left.unit)
    elif l_num:
        left = adopt(left, right.unit)
    elif left.unit != right.unit:
        raise SpecError(
            f"{cid}.right",
            "Compare like with like: a price with a price, a volume with a volume, a % with a %.",
        )
    return left, right


def _binary(spec: cat.OpSpec, op: str, left: Operand, right: Operand, cid: str) -> None:
    obv = (is_obv(left), is_obv(right))
    if any(obv) and not all(obv):
        raise SpecError(f"{cid}.right", OBV_WORDS)
    if not spec.crossing:
        return
    a, b = tfs_of(left), tfs_of(right)
    if a and b and len(a | b) > 1:
        raise SpecError(
            f"{cid}.right",
            "A cross compares two lines on the same candles. Pick the same timeframe on both sides.",
        )
    for side, name in ((left, "left"), (right, "right")):
        if side.kind in ("oi", "opt"):
            raise SpecError(
                f"{cid}.{name}",
                "A cross needs a line with candles behind it. Use 'Is above' or 'Is below' for this one.",
            )
        if side.kind == "field" and side.name in cat.RUNNING_LEVELS:
            raise SpecError(
                f"{cid}.{name}",
                "The day's open, high and low move with every candle, so a cross against them can "
                "never happen. Use 'Is above' or 'Is below'.",
            )


def _unary(op: str, left: Operand, cid: str) -> None:
    if op == "is":
        if (left.kind == "oi" and left.what == "buildup") or (
            left.kind == "ind" and left.name == "pattern"
        ):
            return
        raise SpecError(
            f"{cid}.left", "'Is' works on a build-up or a candle pattern. Pick one of those."
        )
    if left.unit == STATE:
        raise SpecError(f"{cid}.op", "A build-up or a pattern is checked with 'Is'.")
    if left.kind == "num":
        raise SpecError(f"{cid}.left", "Pick a value to check, not a number.")
    if is_obv(left) and op not in ("rising", "falling"):
        raise SpecError(f"{cid}.left", OBV_WORDS)


def _args(op: str, raw, left: Operand, cid: str):
    """(stored, compiled, extra candles) for the operator's own arguments."""
    raw = raw if raw.__class__ is dict else {}
    path = f"{cid}.args"
    if op == "within_pct":
        pct, text = number(raw.get("pct"), f"{path}.pct")
        if not 0 < pct <= 100:
            raise SpecError(f"{path}.pct", "The distance must be above 0% and at most 100%.")
        return {"pct": text}, (("pct", pct),), 0
    if op == "between":
        low, low_text = number(raw.get("low"), f"{path}.low")
        high, high_text = number(raw.get("high"), f"{path}.high")
        if low > high:
            raise SpecError(f"{path}.low", "The lower number must not be above the higher one.")
        probe = adopt(Num(low, low_text), left.unit)
        probe_high = adopt(Num(high, high_text), left.unit)
        return (
            {"low": low_text, "high": high_text},
            (("high", probe_high.value), ("low", probe.value)),
            0,
        )
    if op in ("rising", "falling"):
        n = whole(raw.get("n", 1), f"{path}.n", 1, cat.MAX_RISING, "The number of candles")
        return {"n": n}, (("n", n),), n
    states = cat.STATES["buildup" if left.kind == "oi" else "pattern"]
    name = raw.get("name")
    if name not in states:
        raise SpecError(f"{path}.name", f"Pick one of: {', '.join(states)}.")
    return {"name": name}, (("name", name),), 0
