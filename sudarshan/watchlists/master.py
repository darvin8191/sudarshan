"""The one reader of OpenAlgo's symbol master for Watchlists v2 (M8.8, W11).

OpenAlgo keeps the master as one immutable snapshot, replaced whole at each load. This module keeps
**one** generation of small reference maps (``search.Index``) for the whole process, tied to the
snapshot it was built from, and never copies a row (the maps hold the snapshot's own row objects).

**One per process.** ``shared()`` is the process's only ``Master``, made lazily (``utils/lazy``),
and every reader uses it: ``refs/list.py`` now, and the built-in lists, follow rows, search and the
members route later. A request never builds or keeps a generation of its own; two requests in flight
together get the same one, and a test fails when any module of the feature constructs a ``Master``.

**Built once per snapshot.** The build runs in slices that yield to the hub every ``slice_rows``
rows, behind a keyed single flight: the first caller builds, the others wait for it and share the
result. The previous generation is let go before the next is built, so two are never built side by
side.

**Dropped when OpenAlgo publishes the next snapshot.** A generation holds only a weak reference to
its snapshot, with a callback: when OpenAlgo replaces the snapshot and the old one is freed, the
generation is dropped at that moment, without waiting for the next request, so the old rows are
never kept alive by these maps. A snapshot that lapses (OpenAlgo's session reset) is dropped at the
next call. The callback only compares and assigns under a real lock it never waits for.

Lookups that OpenAlgo already indexes (symbol and exchange, instrument key) read its snapshot
directly and keep nothing. No thread, timer or event loop.
"""

from __future__ import annotations

import time
import weakref
from collections.abc import Callable
from typing import Any

from sudarshan.watchlists import search
from utils import real_threading
from utils.keyed_locks import KeyedLocks
from utils.lazy import LazyInit

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

    def __init__(
        self,
        snapshot: Any,
        index: search.Index,
        rows: int,
        on_gone: Callable[[weakref.ref], None] | None = None,
    ) -> None:
        self._snap = weakref.ref(snapshot, on_gone)
        self.index = index
        self.rows = rows
        _ALIVE.add(self)

    def is_for(self, snapshot: Any) -> bool:
        return self._snap() is snapshot


def generations_alive() -> int:
    """How many generations are still referenced anywhere (one, or none)."""
    return len(_ALIVE)


def _forget_when_gone(master: Master) -> Callable[[weakref.ref], None]:
    """The callback a generation's weak reference runs when its snapshot is freed. It holds the
    ``Master`` weakly too, so a generation never keeps its master alive."""
    owner = weakref.ref(master)

    def gone(snap_ref: weakref.ref) -> None:
        found = owner()
        if found is not None:
            found._forget(snap_ref)

    return gone


class Master:
    """Access to the master. Readers use ``shared()``; a test may build its own over ``source``,
    a callable returning the current snapshot (or ``None``)."""

    def __init__(
        self,
        source: Callable[[], Any] | None = None,
        *,
        slice_rows: int = SLICE_ROWS,
        pause: Callable[[float], None] = time.sleep,
    ) -> None:
        self._source = source
        self._slice = slice_rows
        self._pause = pause
        self._gen: Generation | None = None
        #: Guards the swap of ``_gen`` and nothing else (an identity check and one assignment).
        self._slot = real_threading.Lock()
        self.builds = 0

    def snapshot(self) -> Any:
        """The current snapshot, read at each call (the default source is OpenAlgo's)."""
        return self._source() if self._source is not None else openalgo_snapshot()

    def _swap(self, old: Generation | None, new: Generation | None) -> bool:
        """Put ``new`` in the slot if ``old`` is still there. The one that leaves is freed after
        the lock is released, so the critical section stays an identity check and an assignment."""
        with self._slot:
            held = self._gen
            if held is not old:
                return False
            self._gen = new
        del held
        return True

    def _forget(self, snap_ref: weakref.ref) -> None:
        """A snapshot was freed: drop its generation now. Runs inside a garbage collector callback,
        on whatever thread freed the snapshot, so it never waits: when the slot is busy, the swap
        under way replaces the generation anyway."""
        if not self._slot.acquire(blocking=False):
            return
        try:
            held = self._gen
            if held is None or held._snap is not snap_ref:
                held = None
            else:
                self._gen = None
        finally:
            self._slot.release()
        del held

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
        return Generation(snap, builder.finish(), total, _forget_when_gone(self))

    def generation(self) -> Generation | None:
        """The generation for OpenAlgo's current snapshot, built once and replaced as it changes."""
        snap = self.snapshot()
        gen = self._gen
        if snap is None:
            if gen is not None:
                self._swap(gen, None)  # lapsed: the maps go with it
            return None
        if gen is not None and gen.is_for(snap):
            return gen
        with _flight.hold(id(self)):  # one build at a time; the others wait and share it
            snap = self.snapshot()
            gen = self._gen
            if snap is None:
                if gen is not None:
                    self._swap(gen, None)
                return None
            if gen is not None and gen.is_for(snap):
                return gen
            if gen is not None:
                self._swap(gen, None)  # never two generations built side by side
            del gen
            built = self._build(snap)
            self._swap(None, built)
            return built

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
        snap = self.snapshot()
        return snap is not None and (symbol, exchange) in snap.by_symbol_exchange

    def loaded(self) -> bool:
        """True when OpenAlgo's snapshot is loaded and current."""
        return self.snapshot() is not None

    def row(self, exchange: str, symbol: str) -> Any:
        """OpenAlgo's own row of a symbol on an exchange, or ``None``. Nothing is kept."""
        snap = self.snapshot()
        return None if snap is None else snap.by_symbol_exchange.get((symbol, exchange))

    @staticmethod
    def isin(row: Any) -> str | None:
        """The ISIN a row carries in its instrument key (cash rows do), or ``None``."""
        return None if row is None else search.isin_of(getattr(row, "token", None))

    def by_token(self, token: str) -> Any:
        """The row of an Upstox instrument key, or ``None``."""
        snap = self.snapshot()
        return None if snap is None else snap.by_token.get(token)

    def drop(self) -> None:
        """Let go of the generation (a test, or a logout)."""
        gen = self._gen
        if gen is not None:
            self._swap(gen, None)


#: The process's one ``Master``. Its factory only constructs: nothing is read until it is used.
_shared: LazyInit[Master] = LazyInit(Master, "watchlists-master")


def shared() -> Master:
    """The process's one ``Master``: every reader of the master uses it, so every request in
    flight shares one generation of the maps."""
    return _shared.get()
