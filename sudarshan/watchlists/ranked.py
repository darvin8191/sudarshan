"""Upstox Smartlist rankings as rows (M8.9, W21): the catalogue, the mapping and the figures.

Pure: an answer and a ``by_key(instrument_key)`` lookup of the master come in as arguments; no I/O,
no clock, no logging. Smartlist ranks futures, options and MTF only (there is no cash-stock
movers list) and MTF is not offered. A ranking has no stream: it is read when the owner opens it,
returns to it or pulls to refresh, and always carries the time Upstox made it. The answer's exact
envelope is checked on a real read (design fact W-1), so ``extract`` accepts the rows where the
docs put them and says whether it found a list of rows at all; ``unusable`` names an answer whose
rows cannot be read (no list of rows, or no row carrying an ``instrument_key``), which the op
treats as no answer rather than as an empty or a fully left-out ranking (v0.9.0 review M2). A
figure that is not a finite number (text that is not a number, a NaN) is left out of its row; a
number sent as text is read.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from datetime import datetime
from typing import Any, NamedTuple

from sudarshan.watchlists import numfmt
from sudarshan.watchlists.words import ranked as words

SOURCES = ("futures", "options")
ASSETS = ("INDEX", "STOCK", "COMMODITY")
_FIRST_FOUR = ("TOP_TRADED", "MOST_ACTIVE", "OI_GAINERS", "OI_LOSERS")
_PRICE = ("PRICE_GAINERS", "PRICE_LOSERS")
CATEGORIES = {
    "futures": (*_FIRST_FOUR, *_PRICE, "PREMIUM", "DISCOUNT"),
    "options": (*_FIRST_FOUR, *_PRICE, "IV_GAINERS", "IV_LOSERS", "UNDER_5000", "UNDER_10000"),
}
DEFAULT_SIZE = 20
MAX_SIZE = 50
_ROW_KEYS = ("data", "rows", "items", "list", "results")
#: How many key names ``shape_of`` keeps of each level, and how much of each name.
_SHAPE_KEYS, _SHAPE_NAME = 20, 40
NO_ROWS_LIST, NO_KEYS = "no list of rows", "no row names an instrument_key"


class Extracted(NamedTuple):
    rows: list[dict]
    stamp: Any
    metric_key: str | None
    found: bool  # whether a list of rows was there at all (an empty one counts)


class Entry(NamedTuple):
    ref: str
    source: str
    asset: str
    category: str
    label: str


class RankRow(NamedTuple):
    rank: int
    key: str
    symbol: str
    exchange: str
    name: str
    underlying: str | None
    lot: int | None
    price: dict
    metric: dict
    figure: str | None


class Ranking(NamedTuple):
    rows: tuple[RankRow, ...]
    left_out: int
    made_at: int | None
    metric_key: str | None


def valid(source: str, asset: str, category: str) -> bool:
    """Only what the page offers: no MTF, commodities with the first four categories."""
    if source not in SOURCES or asset not in ASSETS or category not in CATEGORIES[source]:
        return False
    return asset != "COMMODITY" or category in _FIRST_FOUR


def ref_of(source: str, asset: str, category: str) -> str:
    """The favourite ref: ``ranked:futures:INDEX:OI_GAINERS``."""
    return f"ranked:{source}:{asset}:{category}"


def parse_ref(ref: str) -> tuple[str, str, str] | None:
    """The reverse of ``ref_of``; ``None`` for anything not offered."""
    parts = ref.split(":")
    if len(parts) == 4 and parts[0] == "ranked" and valid(*parts[1:]):
        return parts[1], parts[2], parts[3]
    return None


def catalogue() -> list[Entry]:
    """Every ranking the All lists sheet offers, futures first, then options."""
    out = []
    for src in SOURCES:
        for asset in ASSETS:
            for cat in CATEGORIES[src]:
                if valid(src, asset, cat):
                    out.append(Entry(ref_of(src, asset, cat), src, asset, cat,
                                     words.label(src, asset, cat)))  # fmt: skip
    return out


def clamp_size(size: int | None) -> int:
    """20 rows by default, 50 on request, never more (Upstox's page limit)."""
    return DEFAULT_SIZE if not size else max(1, min(int(size), MAX_SIZE))


def parse_time(value: Any) -> int | None:
    """Upstox's ``time_stamp`` as epoch milliseconds: a number, digits, or an ISO text (IST when
    it has no offset)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and not value.strip().lstrip("-").isdigit():
        try:
            dt = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
        return int((dt if dt.tzinfo else dt.replace(tzinfo=numfmt.IST)).timestamp() * 1000)
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return int(n if n > 1e11 else n * 1000)


def extract(answer: Any) -> Extracted:
    """``(rows, time_stamp, metric_key, found)`` from an answer, wherever the rows sit one level
    down; ``found`` is False when no list of rows is there at all."""
    body = answer.get("data", answer) if isinstance(answer, dict) else {}
    if isinstance(body, list):
        return Extracted([r for r in body if isinstance(r, dict)], None, None, True)
    if not isinstance(body, dict):
        return Extracted([], None, None, False)
    rows: list = []
    found = False
    for k in _ROW_KEYS:
        if isinstance(body.get(k), list):
            rows = [r for r in body[k] if isinstance(r, dict)]
            found = True
            break
    stamp = body.get("time_stamp", answer.get("time_stamp") if isinstance(answer, dict) else None)
    return Extracted(rows, stamp, body.get("metric_key"), found)


def unusable(answer: Any) -> str | None:
    """Why an answer Upstox gave as a success cannot be shown as a ranking, or None when it can:
    no list of rows anywhere the docs put one, or rows of which none names an ``instrument_key``
    (a key under another name would otherwise read as every contract missing from the master).
    An empty list of rows is a ranking with no rows, not an unusable answer."""
    got = extract(answer)
    if not got.found:
        return NO_ROWS_LIST
    if got.rows and not any(isinstance(r.get("instrument_key"), str) for r in got.rows):
        return NO_KEYS
    return None


def _names(value: Any) -> list[str]:
    if not isinstance(value, dict):
        return []
    return sorted(str(k)[:_SHAPE_NAME] for k in value)[:_SHAPE_KEYS]


def shape_of(answer: Any) -> dict[str, list[str]]:
    """The key names of an answer, for a log line about one this version cannot read: the top
    level, ``data`` and the first row. Names only, never a value."""
    data = answer.get("data") if isinstance(answer, dict) else None
    first = extract(answer).rows[:1]
    return {
        "answer": _names(answer),
        "data": _names(data),
        "row": _names(first[0] if first else None),
    }


def number(value: Any) -> float | None:
    """A finite number from an answer's field: a number, or a number written as text; None for
    anything else (a word, a NaN, an infinity, a true or false)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip().replace(",", ""))
        except ValueError:
            return None
    if not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def _block(raw: Any, names: tuple[str, ...]) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    return {n: number(raw.get(n)) for n in names}


def map_rows(answer: Any, category: str, by_key: Callable[[str], Any]) -> Ranking:
    """Upstox rows to symbols through the master, in Upstox's order; unknown keys are counted."""
    rows, stamp, metric_key, _ = extract(answer)
    out: list[RankRow] = []
    gone = 0
    for i, r in enumerate(rows, 1):
        key = r.get("instrument_key")
        row = by_key(key) if isinstance(key, str) else None
        if row is None:
            gone += 1
            continue
        price = _block(r.get("price"), ("current", "close_price", "change_abs", "change_pct"))
        metric = _block(r.get("metric"), ("current", "previous", "change_abs", "change_pct"))
        out.append(RankRow(i, key, row.symbol, row.exchange, row.name, getattr(row, "underlying", None),
                           getattr(row, "lotsize", None), price, metric,
                           words.figure(category, metric, price)))  # fmt: skip
    return Ranking(tuple(out), gone, parse_time(stamp), metric_key)


def new_since(before: tuple[RankRow, ...], after: tuple[RankRow, ...]) -> list[RankRow]:
    """Rows in the new ranking that were not in the last one, in their new order."""
    seen = {r.key for r in before}
    return [r for r in after if r.key not in seen]


def by_underlying(rows: tuple[RankRow, ...], underlying: str) -> list[RankRow]:
    """The chip filter: Upstox has none, so the page narrows the 50 itself."""
    return [r for r in rows if (r.underlying or "").upper() == underlying.upper()]
