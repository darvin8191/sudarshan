"""The instruments a family phone may follow, and their feed keys (M18.1; design 2.4, 2.5, 4.2).

A phone names an instrument as OpenAlgo writes it (``SBIN`` on ``NSE``, ``NIFTY`` on
``NSE_INDEX``); the feed knows it by Upstox's instrument key (``NSE_EQ|INE062A01020``). The family
process cannot read OpenAlgo's master contract, and a map of its own also keeps a phone from
following what nobody shared, so this map holds only what the family view may show:

* the three indices of the market strip (NIFTY 50, NIFTY BANK, INDIA VIX), always;
* the rows of the books a member read (the portfolio's rows carry both names);
* the rows of the lists the owner shares (OpenAlgo's internal route resolves them, F7).

It is kept in memory and in family.db's ``symbol_map`` table (market data: names and keys, no
member and no figure), so after a restart a phone that subscribes again at once is answered before
the next read. At most ``CAP`` listings are kept, the least recently refreshed going first; rows
not refreshed for 30 days are dropped from the table.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete

from .errors import StoreError

log = logging.getLogger("sudarshan.family")

#: OpenAlgo's exchanges a family screen can show.
EXCHANGES = frozenset({"NSE", "BSE", "NFO", "BFO", "CDS", "BCD", "MCX", "NCDEX", "NSE_INDEX",
                       "BSE_INDEX"})  # fmt: skip
SYMBOL_RE = re.compile(r"[A-Z0-9][A-Z0-9&_.\-]{0,39}")
#: An Upstox instrument key, as the feed checks it (``sudarshan/feed/subscriptions.KEY_RE``).
KEY_RE = re.compile(r"[A-Z][A-Z0-9_]{1,15}\|[^|\x00-\x1f\x7f]{1,60}")
NAME_MAX = 100
PRICE_SCALE = 10_000  # tick sizes are kept as rupees x 10^4, as the portfolio keeps prices
CAP = 5_000
KEEP_MS = 30 * 86_400_000
SAVE_EVERY_S = 5.0


@dataclass(frozen=True)
class Listing:
    symbol: str
    exchange: str
    instrument_key: str
    name: str
    tick_size_u: int | None = None
    lot_size: int | None = None
    refreshed_at: int = 0

    @property
    def pair(self) -> tuple[str, str]:
        return self.symbol, self.exchange

    def row(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "exchange": self.exchange,
                "instrument_key": self.instrument_key, "name": self.name,
                "tick_size_u": self.tick_size_u, "lot_size": self.lot_size,
                "refreshed_at": self.refreshed_at}  # fmt: skip


INDICES = (
    Listing("NIFTY", "NSE_INDEX", "NSE_INDEX|Nifty 50", "NIFTY 50"),
    Listing("BANKNIFTY", "NSE_INDEX", "NSE_INDEX|Nifty Bank", "NIFTY BANK"),
    Listing("INDIAVIX", "NSE_INDEX", "NSE_INDEX|India VIX", "INDIA VIX"),
)
INDEX_KEYS = tuple(listing.instrument_key for listing in INDICES)


def pair_of(symbol: Any, exchange: Any) -> tuple[str, str] | None:
    """A phone's names, upper-cased, when they are names the family view could show."""
    if not isinstance(symbol, str) or not isinstance(exchange, str):
        return None
    pair = symbol.strip().upper(), exchange.strip().upper()
    return pair if SYMBOL_RE.fullmatch(pair[0]) and pair[1] in EXCHANGES else None


def listing_of(row: Mapping[str, Any], now: int) -> Listing | None:
    """A listing from a book row or an internal-route row; None when a name or the key is not
    usable (a row the master could not resolve has no key: it cannot be followed)."""
    pair = pair_of(row.get("symbol"), row.get("exchange"))
    key = row.get("instrument_key")
    if pair is None or not isinstance(key, str) or not KEY_RE.fullmatch(key):
        return None
    name = row.get("name")
    name = name.strip()[:NAME_MAX] if isinstance(name, str) and name.strip() else pair[0]
    tick = row.get("tick_size")
    tick_u = row.get("tick_size_u")
    if tick_u is None and isinstance(tick, int | float) and not isinstance(tick, bool) and tick > 0:
        tick_u = round(tick * PRICE_SCALE)
    lot = row.get("lot_size")
    return Listing(pair[0], pair[1], key, name,
                   tick_u if isinstance(tick_u, int) and tick_u > 0 else None,
                   lot if isinstance(lot, int) and not isinstance(lot, bool) and lot > 0 else None,
                   now)  # fmt: skip


class SymbolMap:
    """(symbol, exchange) -> listing. Used from the service's one event loop."""

    def __init__(self, cap: int = CAP):
        self.cap = cap
        self._by_pair: dict[tuple[str, str], Listing] = {i.pair: i for i in INDICES}
        self._unsaved: dict[tuple[str, str], Listing] = {}
        self.loaded = False

    def __len__(self) -> int:
        return len(self._by_pair)

    def get(self, symbol: Any, exchange: Any) -> Listing | None:
        pair = pair_of(symbol, exchange)
        return self._by_pair.get(pair) if pair else None

    def put(self, listings: Iterable[Listing]) -> int:
        """Add or refresh listings (the indices are fixed); how many changed. A refresh that
        changes nothing but the time is kept in memory and saved at most once a day."""
        changed = 0
        for listing in listings:
            if listing.pair in {i.pair for i in INDICES}:
                continue
            old = self._by_pair.get(listing.pair)
            self._by_pair[listing.pair] = listing
            same = old is not None and old.row() | {"refreshed_at": 0} == listing.row() | {
                "refreshed_at": 0}  # fmt: skip
            if not same or listing.refreshed_at - old.refreshed_at >= 86_400_000:
                self._unsaved[listing.pair] = listing
                changed += not same
        self._trim()
        return changed

    def _trim(self) -> None:
        extra = len(self._by_pair) - self.cap
        if extra <= 0:
            return
        fixed = {i.pair for i in INDICES}
        oldest = sorted((v.refreshed_at, k) for k, v in self._by_pair.items() if k not in fixed)
        for _, pair in oldest[:extra]:
            del self._by_pair[pair]
            self._unsaved.pop(pair, None)

    # ---- family.db ------------------------------------------------------------------------------
    async def load(self, store: Any) -> int:
        """What the table holds, under what memory already has; how many were read."""
        rows = await store.read(lambda tx: tx.rows("symbol_map"))
        found = [listing for row in rows if (listing := listing_of(row, row["refreshed_at"]))]
        for listing in found:
            self._by_pair.setdefault(listing.pair, listing)
        self._trim()
        self.loaded = True
        return len(found)

    async def save(self, store: Any, now: int) -> int:
        """Write what changed since the last save in one transaction, and drop rows not
        refreshed for 30 days; how many were written. Nothing is lost when the write fails: it is
        tried again at the next save."""
        unsaved, self._unsaved = self._unsaved, {}

        def write(tx) -> None:
            for listing in unsaved.values():
                tx.delete("symbol_map", symbol=listing.symbol, exchange=listing.exchange)
                tx.insert("symbol_map", listing.row())
            table = tx.t("symbol_map")
            tx.conn.execute(delete(table).where(table.c.refreshed_at < now - KEEP_MS))

        try:
            await store.write(write)
        except StoreError:
            self._unsaved = unsaved | self._unsaved
            raise
        return len(unsaved)

    async def keep(self, store: Any, clock: Any) -> None:
        """The plane's piece ``symbols``: read the table once, then save changes every 5 s."""
        if not self.loaded:
            await self.load(store)
        while True:
            await asyncio.sleep(SAVE_EVERY_S)
            if self._unsaved:
                try:
                    await self.save(store, clock())
                except StoreError as exc:
                    log.error("the family symbol map could not be saved this time (%s)",
                              exc.cause)  # fmt: skip
