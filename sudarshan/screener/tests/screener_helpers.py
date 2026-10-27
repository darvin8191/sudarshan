"""Shared by the screener tests: clock text, candle builders, and plain-JSON views of plans.

The vectors files are hand-written JSON; these helpers only turn the code's objects into the same
plain values so a vector can be compared with what the code made.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from fractions import Fraction

from sudarshan.screener.types import Candle, Series, plain

from sudarshan.autologin.schedule import IST  # isort: skip


def ms(day: str, clock_text: str) -> int:
    """'2026-10-28', '10:00:00.300' (or '10:00') -> epoch ms in IST."""
    hh, mm, *rest = clock_text.split(":")
    ss = rest[0] if rest else "0"
    start = datetime.combine(date.fromisoformat(day), datetime.min.time(), IST)
    seconds = int(hh) * 3600 + int(mm) * 60
    return int(start.timestamp()) * 1000 + seconds * 1000 + round(float(ss) * 1000)


def hms(at: int) -> str:
    text = datetime.fromtimestamp(at / 1000, IST).strftime("%H:%M:%S.%f")[:-3]
    return text[:-4] if text.endswith(".000") else text


def day_key(day: str) -> int:
    return int(day.replace("-", ""))


def candle(day: str, start: str, minutes: int, o, h, low, c, v=0, **kw) -> Candle:
    """A candle of ``minutes`` starting at ``start`` on ``day``; prices in paise."""
    begin = ms(day, start)
    return Candle(begin, begin + minutes * 60_000, day_key(day), o, h, low, c, v, **kw)


def flat(day: str, start: str, minutes: int, price: int, v=0, **kw) -> Candle:
    return candle(day, start, minutes, price, price, price, price, v, **kw)


def series(tf, candles, forming=False) -> Series:
    return Series.of(tf, candles, forming)


def ladder(day: str, start: str, minutes: int, closes, v=100) -> list[Candle]:
    """Back-to-back candles, one per close, each opening where the last closed."""
    out, begin, last = [], ms(day, start), None
    for close in closes:
        open_ = close if last is None else last
        out.append(
            Candle(
                begin,
                begin + minutes * 60_000,
                day_key(day),
                open_,
                max(open_, close),
                min(open_, close),
                close,
                v,
            )  # fmt: skip
        )
        begin, last = begin + minutes * 60_000, close
    return out


def after(day: date, days: int) -> date:
    return day + timedelta(days=days)


# ---- plans as plain JSON -------------------------------------------------------------------
def _value(value) -> str:
    return plain(value) if isinstance(value, (Fraction, int)) else str(value)


def operand(item) -> dict:
    kind = item.kind
    if kind == "num":
        return {"num": item.text, "value": plain(item.value), "unit": item.unit}
    if kind == "field":
        return {"field": item.name, "tf": item.tf, "at": item.at, "unit": item.unit}
    if kind == "ind":
        params = {k: _value(v) if isinstance(v, Fraction) else v for k, v in item.params}
        return {
            "ind": item.name, "p": params, "out": item.out, "tf": item.tf, "at": item.at,
            "unit": item.unit,
        }  # fmt: skip
    if kind == "oi":
        return {
            "oi": item.what, "window": list(item.window), "tf": item.tf,
            "min_price": plain(item.min_price), "min_oi": plain(item.min_oi), "unit": item.unit,
        }  # fmt: skip
    if kind == "opt":
        return {"opt": item.what, "underlying": item.underlying, "tf": item.tf, "unit": item.unit}
    return {"math": item.op, "args": [operand(item.a), operand(item.b)], "unit": item.unit}


def clause(item) -> dict:
    out = {"id": item.id, "op": item.op, "left": operand(item.left)}
    if item.right is not None:
        out["right"] = operand(item.right)
    if item.args:
        out["args"] = {k: _value(v) if isinstance(v, Fraction) else v for k, v in item.args}
    return out


def plan(item) -> dict:
    return {
        "ids": [c.id for c in item.clauses],
        "counts": {"instances": len(item.instances), "clauses": len(item.clauses)},
        "on": item.on,
        "tf": item.tf,
        "need": {str(k): v for k, v in item.need.items()},
        "instances": [
            [name, {k: _value(v) if isinstance(v, Fraction) else v for k, v in params}, tf]
            for name, params, tf in item.instances
        ],
        "uses": sorted(item.uses),
        "clauses": [clause(c) for c in item.clauses],
        "rank": operand(item.rank) if item.rank is not None else None,
        "rank_order": item.rank_order,
    }


def pointer(document, path: str):
    """RFC 6901 style: '/where/all/0/right'. '' is the whole document."""
    node = document
    for part in path.split("/")[1:]:
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node
