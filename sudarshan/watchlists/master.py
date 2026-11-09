"""The one reader of OpenAlgo's symbol master for Watchlists v2 (M8.8, W11).

OpenAlgo keeps the master as one immutable snapshot, replaced whole at each load. This module keeps
**one** generation of small reference maps (``search.Index``) tied to the snapshot it was built
from, and drops it the moment the snapshot changes: it never holds two generations and never
copies a row (the maps hold the snapshot's own row objects, so the old rows die with the old
generation). Lookups that OpenAlgo already indexes (symbol and exchange, instrument key) read its
snapshot directly and keep nothing. The limits of the integrator's exception hold here: no thread,
timer or event loop; the build runs in slices that yield to the hub every ``slice_rows`` rows, behind
a keyed single flight so two requests build once.
"""

from __future__ import annotations

import time
import weakref
from collections.abc import Callable
from typing import Any

from sudarshan.watchlists import search
from utils.keyed_locks import KeyedLocks

SLICE_ROWS = 5000
#: Every generation ever built that is still referenced: a test asks that it is one.
_ALIVE: weakref.WeakSet = weakref.WeakSet()
_flight = KeyedLocks(name="watchlists-master")


def openalgo_snapshot() -> Any:
    """OpenAlgo's current snapshot, or ``None`` when it is not loaded or has lapsed."""
    from database.token_db_enhanced import get_cache

    return get_cache().valid_snapshot()


class Generation:
    """The reference maps of one snapshot. Not a snapshot, and holds none."""

    def __init__(self, snapshot: Any, index: search.Index, rows: int) -> None:
        self._snap = weakref.ref(snapshot)
        self.index = index
        self.rows = rows
        _ALIVE.add(self)

    def is_for(self, snapshot: Any) -> bool:
        return self._snap() is snapshot


def generations_alive() -> int:
    """How many generations are still referenced anywhere (one, or none)."""
    return len(_ALIVE)


class Master:
    """Access to the master: ``source`` returns the current snapshot (or ``None``)."""

    def __init__(
        self,
        source: Callable[[], Any] | None = None,
        *,
        slice_rows: int = SLICE_ROWS,
        pause: Callable[[float], None] = time.sleep,
    ) -> None:
        self._source = source or openalgo_snapshot
        self._slice = slice_rows
        self._pause = pause
        self._gen: Generation | None = None
        self.builds = 0

    def _build(self, snap: Any) -> Generation:
        builder = search.IndexBuilder()
        total = 0
        for rows in list(snap.by_exchange.values()):
            for i in range(0, len(rows), self._slice):
                chunk = rows[i : i + self._slice]
                builder.add(chunk)
                total += len(chunk)
                self._pause(0)
        self.builds += 1
        return Generation(snap, builder.finish(), total)

    def generation(self) -> Generation | None:
        """The generation for OpenAlgo's current snapshot, built once and replaced as it changes."""
        snap = self._source()
        if snap is None:
            self._gen = None
            return None
        gen = self._gen
        if gen is not None and gen.is_for(snap):
            return gen
        with _flight.hold("master"):
            snap = self._source()
            if snap is None:
                self._gen = None
                return None
            gen = self._gen
            if gen is None or not gen.is_for(snap):
                gen = self._build(snap)
                self._gen = gen  # one assignment: the old generation is dropped here
            return gen

    def index(self) -> search.Index | None:
        gen = self.generation()
        return gen.index if gen else None

    def expiries(self, underlying: str, kind: str = "options") -> list[str]:
        """ISO expiry dates listed for an underlying's options or futures, soonest first."""
        ix = self.index()
        if ix is None:
            return []
        if kind == "futures":
            return sorted({iso for iso, _ in ix.futures.get(underlying, [])})
        return sorted(ix.options.get(underlying, {}))

    def strikes(self, underlying: str, expiry: str) -> list[float]:
        """The strikes listed for an underlying and an expiry, ascending."""
        ix = self.index()
        rows = ix.options.get(underlying, {}).get(expiry, []) if ix else []
        return sorted({r.strike for r in rows if r.strike is not None})

    def known(self, exchange: str, symbol: str) -> bool:
        """True when OpenAlgo's master lists the symbol on the exchange."""
        snap = self._source()
        return snap is not None and (symbol, exchange) in snap.by_symbol_exchange

    def by_token(self, token: str) -> Any:
        """The row of an Upstox instrument key, or ``None``."""
        snap = self._source()
        return None if snap is None else snap.by_token.get(token)

    def drop(self) -> None:
        """Let go of the generation (a test, or a logout)."""
        self._gen = None
