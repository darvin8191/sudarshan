"""The price book: what a listed share is worth now (M7.24, design 5.1 and 5.2; fills ``Prices``).

For a listed asset, in this order:

1. **The feed's last tick**, ``GET /snapshot?keys=`` on the loopback (at most 200 keys a call),
   when it is newer than the last closing record. The price is converted from the feed's float once,
   by ``alerts.market.grain.paise``, and scaled to price_u (paise x 100).
2. **The latest closing record** (``price_eod``).
3. **A quote**, only through the feed's REST governor (P17) and only if it answers: at most once
   per key per session, kept for the session with its time (P18). The governor ships switched off;
   when it is off or absent that is said once in the log and the next step follows. This module
   never calls Upstox itself.
4. **"No price yet"**.

The previous close follows 5.1: the tick's ``cp``, then the previous day's final closing record,
then the quote's.

All network work is in ``warm`` (async, called by an op before it reads the store): the snapshot,
the quotes, the claims at the feed and the auction flags land in memory. ``now`` and the other
methods of ``Prices`` run inside a store read and only look at that memory and at the store, so a
slow feed can never hold the store's one thread.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from datetime import date
from typing import Any

import httpx

from sudarshan.alerts.market import sessions
from sudarshan.alerts.market.grain import paise
from sudarshan.autologin.schedule import TradingCalendar

from .core.days import day_of_instant
from .feedlink import CLIENT as _CLIENT
from .feedlink import INDEX_KEYS, READ_BUDGET_S, READ_CALL_S, Claims, FeedClient, json_of
from .resolver import SEGMENT, Resolver, ResolverUnavailable
from .store import PortfolioStore, Scope, Tx
from .views import NO_PRICE, Prices, manual_point

log = logging.getLogger("sudarshan.portfolio.prices")

CLIENT = _CLIENT
SNAPSHOT_BATCH = 200
QUOTE_BATCH = 200
CLOSE_QUOTE_BATCH = 500
NO_AUCTION = (False, None, None, None)


def price_u(value) -> int | None:
    """A feed price (rupees, a float) as price_u: whole paise, times 100."""
    made = paise(value)
    return None if made is None else made * 100


def _day(at_ms: int) -> int:
    return day_of_instant(at_ms)


class PriceBook(Prices):
    def __init__(
        self,
        feed_url: str,
        resolver: Resolver | None,
        calendar: TradingCalendar,
        *,
        max_claims: int = 200,
        client: httpx.AsyncClient | None = None,
    ):
        self.feed = FeedClient(feed_url, client)
        self.claims = Claims(self.feed, max_claims)
        self.resolver, self.calendar, self.max_claims = resolver, calendar, max_claims
        self._ticks: dict[str, Mapping] = {}
        self._quotes: dict[tuple[int, str], dict[str, Any]] = {}
        self._asked: set[tuple[int, str]] = set()
        self._auction: dict[int, tuple] = {}
        self._indexes: tuple[str, ...] | None = None

    async def aclose(self) -> None:
        await self.feed.aclose()

    def _say_once(self, name: str, text: str, *args: Any) -> None:
        self.feed.say_once(name, text, *args)

    async def _index_keys(self) -> tuple[str, ...]:
        """The index keys the master contract knows. NIFTY 500 missing there is said once and
        NIFTY 50 goes on alone; an unreadable contract changes nothing and is asked again."""
        if self._indexes is not None:
            return self._indexes
        if self.resolver is None:
            return INDEX_KEYS
        loop = asyncio.get_running_loop()
        try:
            found = await loop.run_in_executor(
                None, lambda: [k for k in INDEX_KEYS if self.resolver.by_key(k) is not None]
            )
        except ResolverUnavailable:
            return INDEX_KEYS
        if INDEX_KEYS[0] in found and INDEX_KEYS[1] not in found:
            self._say_once("noindex", "NIFTY 500 is not in the master contract, so only NIFTY 50 "
                           "is followed.")  # fmt: skip
            found = [INDEX_KEYS[0]]
        self._indexes = tuple(found) if found else INDEX_KEYS
        return self._indexes

    async def follow(self, store: PortfolioStore | None, now: int, reader: Any = None) -> bool:
        """Keep the feed's set of this service's keys current: the indexes first, then every
        listed asset, then the Upstox holdings the reader last saw. The "claims" job calls this."""
        if store is None:
            return False
        loop = asyncio.get_running_loop()

        def keys(tx: Tx) -> list[str]:
            return self._by_value(tx)

        mine = await loop.run_in_executor(None, lambda: store.read(Scope.owner(), keys))
        theirs = reader.held_keys() if reader is not None and hasattr(reader, "held_keys") else []
        return await self.claims.sync(
            [*await self._index_keys(), *mine, *sorted(set(theirs) - set(mine))], now
        )

    @staticmethod
    def _by_value(tx: Tx) -> list[str]:
        """The pricing keys of the listed assets, the most valuable holdings first (5.2): units
        held in any tab at the newest closing price, or at cost when there is none."""
        from .core.ledger import replay
        from .core.model import Entry, Refusal
        from .core.money import value_paise

        assets = {a["id"]: a for a in tx.rows("asset", kind="listed") if a["instrument_key"]}
        held: dict[int, list[int]] = {}
        for tab in tx.rows("tab"):
            try:
                ledger = replay([Entry.of(r) for r in tx.entries(tab["id"])])
            except Refusal:
                continue
            for (_, asset_id), pos in ledger.positions.items():
                if asset_id in assets:
                    got = held.setdefault(asset_id, [0, 0])
                    got[0] += pos.qty_u
                    got[1] += pos.cost_paise
        worth = {}
        for asset_id, (qty_u, cost) in held.items():
            eods = tx.rows("price_eod", "day", asset_id=asset_id)
            worth[asset_id] = value_paise(qty_u, eods[-1]["price_u"]) if eods and qty_u else cost
        ordered = sorted(
            assets.values(), key=lambda a: (-worth.get(a["id"], 0), a["instrument_key"])
        )
        return [a["instrument_key"] for a in ordered]

    def index_tick(self, key: str, today: int) -> dict[str, Any] | None:
        """The last tick of an index if it is from ``today``: ``{"price_u", "cp_u", "at"}``."""
        tick = self._ticks.get(key)
        at = tick.get("ltt") if tick else None
        last = price_u(tick.get("ltp")) if tick else None
        if last is None or not isinstance(at, int) or _day(at) != today:
            return None
        return {"price_u": last, "cp_u": price_u(tick.get("cp")), "at": at}

    async def official_closes(self, keys: list[str], now: int) -> dict[str, tuple[int, str]]:
        """The official close of each key as ``(price_u, "cp" or "quote")``: the tick's ``cp``
        when its ``ltt`` is today, else the quote's previous close through the governor (500 keys
        a call). A key neither gives is left out."""
        today = _day(now)
        found: dict[str, tuple[int, str]] = {}
        for key, tick in (await self._snapshot(keys)).items():
            close, at = price_u(tick.get("cp")), tick.get("ltt")
            if close is not None and isinstance(at, int) and _day(at) == today:
                found[key] = (close, "cp")
        rest = [k for k in keys if k not in found]
        for start in range(0, len(rest), CLOSE_QUOTE_BATCH):
            batch = rest[start : start + CLOSE_QUOTE_BATCH]
            answer = await self._call(
                "GET",
                "/upstox/v3/market-quote/quotes",
                params={"instrument_key": ",".join(batch)},
                headers={"X-Sudarshan-Client": CLIENT, "X-Sudarshan-Priority": "bulk"},
            )
            if answer is None or answer.status_code != 200:
                if answer is not None:
                    self._say_once("closeoff", "Official closes by quote are not asked for: the "
                                   "feed's request governor does not answer them.")  # fmt: skip
                break
            for item in (json_of(answer).get("data") or {}).values():
                if not isinstance(item, dict):
                    continue
                key, close = item.get("instrument_token"), price_u(item.get("prev_close_price"))
                if isinstance(key, str) and close is not None:
                    found[key] = (close, "quote")
        return found

    async def _call(self, method: str, path: str, **kwargs) -> httpx.Response | None:
        return await self.feed.call(method, path, **kwargs)

    # ---- warming (all network work) -----------------------------------------------------------------
    async def warm_for_read(self, store: PortfolioStore, now: int) -> None:
        """``warm`` for a read the phone waits on: short calls and a total budget, so a feed that
        never answers costs the read about a second and a half, not its whole time."""
        self.feed.call_timeout = READ_CALL_S
        try:
            await asyncio.wait_for(self.warm(store, now), READ_BUDGET_S)
        except TimeoutError:
            self._say_once("slowfeed", "The price feed was too slow; this read used the last "
                           "prices it had.")  # fmt: skip
        finally:
            self.feed.call_timeout = None

    async def warm(self, store: PortfolioStore, now: int, scope: Scope | None = None) -> None:
        """Bring the feed's ticks, the quotes the governor allows, the claims and the auction flags
        into memory for the listed assets in the book."""
        scope = scope or Scope.owner()
        loop = asyncio.get_running_loop()

        def read(tx: Tx):
            out = []
            for asset in tx.rows("asset", kind="listed"):
                eods = tx.rows("price_eod", "day", asset_id=asset["id"])
                out.append((asset, bool(eods)))
            return out, self._by_value(tx)

        listed, order = await loop.run_in_executor(None, lambda: store.read(scope, read))
        keyed = {a["instrument_key"]: (a, has_eod) for a, has_eod in listed if a["instrument_key"]}
        session = _day(now)
        if self._quotes and next(iter(self._quotes))[0] != session:
            self._quotes, self._asked = {}, set()  # a quote is kept for its session only (P18)
        await self.claims.sync([*await self._index_keys(), *order], now)
        ticks = await self._snapshot([*await self._index_keys(), *sorted(keyed)])
        self._ticks = ticks  # a whole generation at once
        need = [k for k, (_, has_eod) in keyed.items() if k not in ticks and not has_eod]
        await self._quote([k for k in need if (session, k) not in self._asked], session, now)
        await loop.run_in_executor(None, lambda: self._flag_auctions([a for a, _ in listed], now))

    async def _snapshot(self, keys: list[str]) -> dict[str, Mapping]:
        found: dict[str, Mapping] = {}
        for start in range(0, len(keys), SNAPSHOT_BATCH):
            batch = keys[start : start + SNAPSHOT_BATCH]
            answer = await self._call("GET", "/snapshot", params={"keys": ",".join(batch)})
            if answer is None or answer.status_code != 200:
                continue
            ticks = json_of(answer).get("ticks")
            for key, tick in (ticks or {}).items() if isinstance(ticks, dict) else ():
                if isinstance(tick, dict):
                    found[key] = tick
        return found

    async def _quote(self, keys: list[str], session: int, now: int) -> None:
        """Quotes for keys with no tick and no closing record, once each per session, only
        through the feed's governor. Off or absent: said once, and the next step follows."""
        for start in range(0, len(keys), QUOTE_BATCH):
            batch = keys[start : start + QUOTE_BATCH]
            self._asked.update((session, k) for k in batch)
            answer = await self._call(
                "GET",
                "/upstox/v3/market-quote/quotes",
                params={"instrument_key": ",".join(batch)},
                headers={"X-Sudarshan-Client": CLIENT, "X-Sudarshan-Priority": "interactive"},
            )
            if answer is None:
                return
            if answer.status_code != 200:
                if answer.headers.get("x-sudarshan-reason") == "off":
                    self._say_once("govoff", "Quotes are not asked for: the feed's request "
                                   "governor is switched off.")  # fmt: skip
                else:
                    self._say_once(f"gov{answer.status_code}", "The feed's request governor did "
                                   "not give quotes (%d).", answer.status_code)  # fmt: skip
                return
            for item in (json_of(answer).get("data") or {}).values():
                if isinstance(item, dict):
                    self._take_quote(item, session, now)

    def _take_quote(self, item: Mapping, session: int, now: int) -> None:
        key = item.get("instrument_token")
        last = price_u(item.get("last_price"))
        if not isinstance(key, str) or last is None:
            return
        prev = None
        for name in ("cp", "prev_close_price", "prev_close"):
            prev = prev or price_u(item.get(name))
        self._quotes[session, key] = {"price_u": last, "prev_close_u": prev, "at": now}

    def auction_for(self, symbol: str | None, exchange: str | None, now: int) -> tuple:
        """``(cas, from, until, close)`` of one stock today (P24, X6m), from sessions.py."""
        segment = SEGMENT.get(exchange or "")
        if segment is None or not symbol or self.resolver is None:
            return NO_AUCTION
        today = _day(now)
        day = date(today // 10000, today // 100 % 100, today % 100)
        try:
            derivatives = self.resolver.has_derivatives(symbol)
        except ResolverUnavailable:
            return NO_AUCTION
        if not sessions.closes_by_auction(segment, day, derivatives=derivatives):
            return NO_AUCTION
        trading, window = self.calendar.is_trading_day(day), self.calendar.day_window(day)
        span = sessions.auction_window(segment, day, trading_day=trading, window=window)
        close = sessions.auction_close(segment, day, trading_day=trading, window=window)
        return (True, *(span or (None, None)), close)

    def _flag_auctions(self, assets: list[Mapping], now: int) -> None:
        """Which stocks close by auction today, and the window, for the listed assets."""
        self._auction = {
            a["id"]: self.auction_for(a["symbol"], a["exchange"], now)
            for a in assets
            if SEGMENT.get(a["exchange"] or "") is not None
        }

    # ---- the seam (inside a store read: memory and the store only) -----------------------------------
    def now(self, tx: Tx, asset: Mapping[str, Any], today: int) -> dict[str, Any]:
        if asset["kind"] == "manual":
            return super().now(tx, asset, today)
        eods = tx.rows("price_eod", "day", asset_id=asset["id"])
        last = eods[-1] if eods else None
        if asset["kind"] == "mf":
            # A NAV the owner entered by hand stands from AMFI's newest day on: the owner's own value
            # wins on a day, as in the history (Prices.closes), so now and the series agree
            # (design 4.5). Its previous close is AMFI's last NAV before its day.
            typed = manual_point(tx, asset, today, from_day=last["day"] if last else None)
            if typed is not None:
                before = [e for e in eods if e["day"] < typed["day"]]
                typed["prev_close_u"] = before[-1]["price_u"] if before else None
                return typed
        tick = (
            self._ticks.get(asset.get("instrument_key") or "")
            if asset["kind"] == "listed"
            else None
        )
        live = price_u(tick.get("ltp")) if tick else None
        at = tick.get("ltt") if tick else None
        if live is not None and isinstance(at, int) and (last is None or at > last["at"]):
            prev = price_u(tick.get("cp"))
            if prev is None:
                earlier = [e for e in eods if e["day"] < _day(at)]
                prev = earlier[-1]["price_u"] if earlier else None
            return _info(live, prev, _day(at), at, "feed", "last", False)
        if last is not None:
            prev = last["prev_close_u"]
            if prev is None:
                earlier = [e for e in eods if e["day"] < last["day"]]
                prev = earlier[-1]["price_u"] if earlier else None
            return _info(
                last["price_u"], prev, last["day"], last["at"], last["source"], last["basis"],
                bool(last["late"]),
            )  # fmt: skip
        quoted = self._quotes.get((today, asset.get("instrument_key") or ""))
        if quoted is not None:
            return _info(quoted["price_u"], quoted["prev_close_u"], today, quoted["at"],
                         "quote", "last", False)  # fmt: skip
        return dict(NO_PRICE)

    def last_session_day(self, today: int, now: int) -> int:
        """The latest trading day whose session has begun, from the exchange's calendar."""
        day = date(today // 10000, today // 100 % 100, today % 100)
        if self.calendar.is_trading_day(day):
            span = sessions.session(
                "NSE_EQ", day, trading_day=True, window=self.calendar.day_window(day)
            )
            if span is not None and now >= span.start:
                return today
        before = sessions.previous_trading_day(day, self.calendar.is_trading_day)
        return sessions.day_key(before) if before else today

    def holidays(self) -> tuple[int, ...]:
        """The exchange's holidays as the calendar file lists them, as days (M7.31)."""
        return tuple(sorted(sessions.day_key(day) for day in self.calendar.holidays()))

    def auction(self, asset: Mapping[str, Any], today: int):
        return self._auction.get(asset["id"], NO_AUCTION)


def _info(price, prev, day, at, source, basis, late) -> dict[str, Any]:
    return {
        "price_u": price,
        "prev_close_u": prev,
        "day": day,
        "at": at,
        "source": source,
        "basis": basis,
        "late": late,
    }
