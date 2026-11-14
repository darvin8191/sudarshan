"""A synthetic master for the options tests: rows as OpenAlgo's snapshot holds them, built from
``vectors/chain.json``. Every symbol, key and figure is made up. Not a test module."""

from __future__ import annotations

import json
from pathlib import Path

from sudarshan.watchlists import search

V = json.loads((Path(__file__).parent / "vectors" / "chain.json").read_text(encoding="utf-8"))


class Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class Snap:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def rows() -> list[Row]:
    out = []
    for symbol, exchange, typ, underlying, expiry, strike, lot, token in V["master"]:
        out.append(Row(symbol=symbol, name=symbol, exchange=exchange, instrumenttype=typ,
                       underlying=underlying, expiry=expiry, strike=strike, lotsize=lot,
                       tick_size=0.05, token=token))  # fmt: skip
    return out


def world():
    """``(index, by_token, snapshot)`` over the vector's master."""
    master = rows()
    by_token = {r.token: r for r in master}
    by_ex: dict = {}
    for r in master:
        by_ex.setdefault(r.exchange, []).append(r)
    snap = Snap(by_exchange={k: tuple(v) for k, v in by_ex.items()},
                by_symbol_exchange={(r.symbol, r.exchange): r for r in master},
                by_token=by_token, session_start=None)  # fmt: skip
    return search.build_index(master), by_token.get, snap
