"""Numbers, dates and times as a trader reads them: Indian grouping, IST, no clock of its own (pure)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def group(n: int) -> str:
    """``12345678`` to ``1,23,45,678``."""
    sign = "-" if n < 0 else ""
    s = str(abs(n))
    if len(s) <= 3:
        return sign + s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return sign + ",".join(parts) + "," + tail


def num(x: float, places: int = 2) -> str:
    """``25912.35`` to ``25,912.35``; trailing zeros are dropped (``25,900``, ``292.5``)."""
    text = f"{abs(x):.{places}f}"
    whole, _, frac = text.partition(".")
    frac = frac.rstrip("0")
    out = group(int(whole)) + ("." + frac if frac else "")
    return ("-" if x < 0 and float(text) != 0 else "") + out


def day(iso: str) -> str:
    """``2026-11-24`` to ``24 Nov 2026``."""
    d = date.fromisoformat(iso)
    return f"{d.day} {_MONTHS[d.month - 1]} {d.year}"


def short_day(iso: str) -> str:
    """``2026-11-24`` to ``24 Nov``."""
    d = date.fromisoformat(iso)
    return f"{d.day} {_MONTHS[d.month - 1]}"


def hm(ms: int) -> str:
    """An epoch in milliseconds as IST ``10:42``."""
    return datetime.fromtimestamp(ms / 1000, IST).strftime("%H:%M")


def hms(ms: int) -> str:
    """An epoch in milliseconds as IST ``10:42:05``."""
    return datetime.fromtimestamp(ms / 1000, IST).strftime("%H:%M:%S")
