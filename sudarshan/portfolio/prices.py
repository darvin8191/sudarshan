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
from .resolver import SEGMENT, Resolver, ResolverUnavailable
from .store import PortfolioStore, Scope, Tx
from .views import NO_PRICE, Prices

log = logging.getLogger("sudarshan.portfolio.prices")

CLIENT = "sudarshan-portfolio"
SNAPSHOT_BATCH = 200
QUOTE_BATCH = 200
HTTP_TIMEOUT_S = 5.0
CLAIM_RENEW_MS = 10 * 60 * 1000
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
        self.feed_url = feed_url.rstrip("/")
        self.resolver, self.calendar, self.max_claims = resolver, calendar, max_claims
        self._client = client
        self._own_client = client is None
        self._ticks: dict[str, Mapping] = {}
        self._quotes: dict[tuple[int, str], dict[str, Any]] = {}
        self._asked: set[tuple[int, str]] = set()
        self._auction: dict[int, tuple] = {}
        self._claimed: frozenset[str] = frozenset()
        self._claimed_at = 0
        self._feed_started: str | None = None
        self._told: set[str] = set()
        self._feed_down = False

    # ---- the client ----------------------------------------------------------------------------
    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=HTTP_TIMEOUT_S)
        return self._client

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None and self._own_client:
            await client.aclose()

    def _say_once(self, name: str, text: str, *args: Any) -> None:
        if name not in self._told:
            self._told.add(name)
            log.warning(text, *args)

    # ---- warming (all network work) -----------------------------------------------------------------
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
            return out

        listed = await loop.run_in_executor(None, lambda: store.read(scope, read))
        keyed = {a["instrument_key"]: (a, has_eod) for a, has_eod in listed if a["instrument_key"]}
        session = _day(now)
        if self._quotes and next(iter(self._quotes))[0] != session:
            self._quotes, self._asked = {}, set()  # a quote is kept for its session only (P18)
        await self._claim(sorted(keyed), now)
        ticks = await self._snapshot(sorted(keyed))
        self._ticks = ticks  # a whole generation at once
        need = [k for k, (_, has_eod) in keyed.items() if k not in ticks and not has_eod]
        await self._quote([k for k in need if (session, k) not in self._asked], session, now)
        await loop.run_in_executor(None, lambda: self._flag_auctions([a for a, _ in listed], now))

    async def _call(self, method: str, path: str, **kwargs) -> httpx.Response | None:
        try:
            answer = await self.client.request(method, self.feed_url + path, **kwargs)
        except httpx.HTTPError:
            if not self._feed_down:
                log.warning("The price feed is not answering; prices come from the last closing "
                            "record until it does.")  # fmt: skip
            self._feed_down = True
            return None
        if self._feed_down:
            log.info("The price feed answers again.")
            self._feed_down = False
        return answer

    async def _snapshot(self, keys: list[str]) -> dict[str, Mapping]:
        found: dict[str, Mapping] = {}
        for start in range(0, len(keys), SNAPSHOT_BATCH):
            batch = keys[start : start + SNAPSHOT_BATCH]
            answer = await self._call("GET", "/snapshot", params={"keys": ",".join(batch)})
            if answer is None or answer.status_code != 200:
                continue
            ticks = _json(answer).get("ticks")
            for key, tick in (ticks or {}).items() if isinstance(ticks, dict) else ():
                if isinstance(tick, dict):
                    found[key] = tick
        return found

    async def _claim(self, keys: list[str], now: int) -> None:
        """Ask the feed to follow these keys (5.2): when the set changes, every 10 minutes, and
        when the feed has restarted. Never a fallback to /subscribe."""
        health = await self._call("GET", "/health")
        started = _json(health).get("started_at") if health is not None else None
        restarted = started is not None and started != self._feed_started
        want = frozenset(keys[: self.max_claims])
        if len(keys) > self.max_claims:
            self._say_once("cap", "More than %d listed holdings: the rest are priced by quote.",
                           self.max_claims)  # fmt: skip
        if (
            health is None
            or not want
            or (want == self._claimed and not restarted and now - self._claimed_at < CLAIM_RENEW_MS)
        ):
            return
        answer = await self._call(
            "PUT", f"/claims/{CLIENT}", json={"keys": dict.fromkeys(want, "ltpc")}
        )
        if answer is None:
            return
        if answer.status_code == 200:
            self._claimed, self._claimed_at, self._feed_started = want, now, started
        elif answer.status_code == 404:
            self._say_once("noclaims", "The feed does not take claims yet; the portfolio prices "
                           "from closing records and quotes.")  # fmt: skip
        elif answer.status_code == 409:
            self._say_once("claimcap", "The feed has no room for the portfolio's keys; they are "
                           "priced from closing records and quotes.")  # fmt: skip

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
            for item in (_json(answer).get("data") or {}).values():
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


def _json(answer: httpx.Response | None) -> dict:
    if answer is None:
        return {}
    try:
        found = answer.json()
    except ValueError:
        return {}
    return found if isinstance(found, dict) else {}
