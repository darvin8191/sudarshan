"""Synthetic masters for the built-in list tests (M8.8, W12): rows as OpenAlgo's snapshot holds
them, built from the hand-typed vectors. Not a test module."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from sudarshan.watchlists import search

V = json.loads((Path(__file__).parent / "vectors" / "presets.json").read_text(encoding="utf-8"))


class Row:
    """A master row as OpenAlgo's snapshot holds it: a plain object, so it can be weakly referenced."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def ms(text: str) -> int:
    return int(datetime.fromisoformat(text).astimezone(UTC).timestamp() * 1000)


def rows_of(spec) -> list:
    """Master rows as OpenAlgo's snapshot holds them (the fields search.IndexBuilder reads)."""
    out = []
    for n, (symbol, exchange, typ, underlying, expiry) in enumerate(spec):
        cash = {"NSE": "NSE_EQ", "BSE": "BSE_EQ"}.get(exchange)
        token = f"{cash}|INE{n % 1000:03d}A01018" if cash and typ == "EQ" else f"{exchange}|{n}"
        out.append(Row(
            symbol=symbol, name=symbol, exchange=exchange, instrumenttype=typ,
            underlying=underlying, expiry=expiry, strike=None, lotsize=1, tick_size=0.05,
            token=token))  # fmt: skip
    return out


def indices_rows(skip=()):
    bse = set(V["bse_indices"])
    return [(n, "BSE_INDEX" if n in bse else "NSE_INDEX", "INDEX", None, None)
            for n in V["indices"] if n not in skip]  # fmt: skip


def index_of(spec):
    return search.build_index(rows_of(spec))


def keys(rows) -> list[str]:
    return [f"{r.exchange}:{r.symbol}" for r in rows]


class Snap:
    """A snapshot as OpenAlgo's is: a plain object (so it can be weakly referenced)."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def snapshot_of(spec, session_start=None):
    rows = rows_of(spec)
    by_ex: dict = {}
    for r in rows:
        by_ex.setdefault(r.exchange, []).append(r)
    snap = Snap(
        by_exchange={k: tuple(v) for k, v in by_ex.items()},
        by_symbol_exchange={(r.symbol, r.exchange): r for r in rows},
        by_token={r.token: r for r in rows}, session_start=session_start)  # fmt: skip
    return snap, rows
