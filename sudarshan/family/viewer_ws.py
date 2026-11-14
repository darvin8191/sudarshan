"""What a family phone and the service say on /family/ws (M18.1, M6.7; design 2.5; cross-design
Y11, Y12).

FA's listener upgrades the socket and websockets' sans-I/O protocol frames it (``ws.py``: the queue
of 200 that drops frames for a slow phone, a ping every 25 s, the close after 60 s of silence, the
close with 4401 on a revoke, the shares of the 20 sockets). This is the dialect, the subset of
OpenAlgo's proxy protocol that the phone's MarketDataManager reads:

    phone                                          service
    {"action": "authenticate", "family": true} ->  {"type": "auth", "status": "success"}
                                                   {"type": "feed_state", "state", "since"}
    {"action": "subscribe", "symbols": [...]}  ->  {"type": "subscribe", "status", "symbols"}
                                                   {"type": "quotes", "quotes", "capped"}
    {"action": "unsubscribe", "symbols": [...]}
    {"action": "screen", "name": ...}          ->  the books read what the screen shows
                                                   {"type": "market_data", ...}: ticks, 100 ms
                                                   {"type": "feed_state", ...} on a change
                                                   {"type": "book", "part", "read_at"} (Y11)

* **The session** is checked at the handshake and again on every message, as the viewer API checks
  every request; one that ended closes the socket (4401, "Signed out on this device."). The member
  row read then is the one the socket's reads and masks use. At most 40 messages in 10 s.
* **Symbols**: at most 150 a socket. One the family view does not know (``symbol_map``) is refused
  with "Not available in the family view". A known one is followed only when it is the member's
  to see: an index, a row of their own books, a row of a list shared with them; until the books or
  lists that would say so are read it waits, and then it is followed or refused.
* **Ticks** (``market_data``) are kept per socket and symbol and sent every 100 ms, the newest
  of each; the 100 ms is the only delay added to what the feed delivers.
* **Quotes** (``{"type": "quotes", "quotes": [Quote], "capped": [{symbol, exchange}],
  "message"?}``): after a subscribe, the last price and its time of each symbol just followed (the
  feed's snapshot: never real-time; the first tick replaces it); and whenever the symbols past the
  feed's cap change, ``capped`` lists every one of this socket's (``message`` "Not real-time · too
  many symbols open" while there are any).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from sudarshan.tasks import cancel_and_wait

from . import words
from .contract import LIMITS, SCREENS, WS_CLOSE_SIGNED_OUT
from .errors import FamilyError
from .listener import Response
from .symbol_map import INDEX_KEYS, INDICES, Listing, SymbolMap, pair_of

log = logging.getLogger("sudarshan.family")

MAX_SYMBOLS = LIMITS["ws_symbols"]
COALESCE_S = 0.1
RATE = (40, 10.0)
NOT_AVAILABLE = "Not available in the family view"
TOO_MANY = f"At most {MAX_SYMBOLS} symbols can follow prices on one screen."
CAPPED = "Not real-time · too many symbols open"
#: A member's waiting symbols trigger their books and lists to be read at most this often.
ENSURE_EVERY_S = 30.0
Pair = tuple[str, str]


class Source(Protocol):
    """What says which keys a member may follow (the books, the shared lists): the keys, whether
    it has an answer for the member yet, and a read that gets one."""

    def keys(self, member_id: int) -> set[str]: ...
    def known(self, member_id: int) -> bool: ...
    def ensure(self, member: Mapping[str, Any]) -> Awaitable[Any]: ...


@dataclass(eq=False)
class Seat:
    """One authenticated socket: its member as read at its last message, its screen, and what it
    follows (pair -> key), waits for, and which of those are past the cap."""

    sock: Any
    member: dict[str, Any]
    screen: str | None = None
    follows: dict[Pair, str] = field(default_factory=dict)
    waiting: dict[Pair, str] = field(default_factory=dict)
    capped: frozenset[Pair] = frozenset()


def quote_of(listing: Listing, frame: Mapping[str, Any] | None) -> dict[str, Any]:
    """A listing and its last feed frame as the contract's Quote (nulls with no frame)."""
    frame = frame or {}
    ltp, close = frame.get("ltp"), frame.get("cp")
    ltp = ltp if isinstance(ltp, int | float) and not isinstance(ltp, bool) and ltp > 0 else None
    close = close if isinstance(close, int | float) and not isinstance(close, bool) and close > 0 \
        else None  # fmt: skip
    at = frame.get("ltt") if type(frame.get("ltt")) is int else frame.get("ts_recv")
    return {"symbol": listing.symbol, "exchange": listing.exchange, "name": listing.name,
            "ltp": ltp, "change": round(ltp - close, 4) if ltp and close else None,
            "change_percent": round((ltp - close) / close * 100, 4) if ltp and close else None,
            "close": close, "at": at if type(at) is int else None}  # fmt: skip


def symbol(pair: Pair) -> dict[str, str]:
    return {"symbol": pair[0], "exchange": pair[1]}


class Dialect:
    """The sockets' dialect; one per service, used from its one event loop."""

    def __init__(self, *, judge: Callable[[str], Awaitable[Any]], symbols: SymbolMap, books: Any,
                 monotonic: Callable[[], float] = time.monotonic):  # fmt: skip
        self.judge, self.symbols, self.books, self.mono = judge, symbols, books, monotonic
        self.feed: Any = None  # the FeedLink, seated by install
        self.sources: list[Source] = []
        self.on_screen: list[Callable[[Seat], None]] = []
        self.seats: dict[Any, Seat] = {}
        self.by_key: dict[str, set[tuple[Any, Pair]]] = {}
        self.order: dict[str, int] = {}
        self.counts = {"messages": 0, "refused": 0, "sent": 0}
        self._seq = itertools.count()
        self._heard: dict[Any, deque[float]] = {}
        self._pending: dict[Any, dict[Pair, dict[str, Any]]] = {}
        self._due = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()
        self._ensured: dict[int, float] = {}

    # ---- FA's ViewerSocket calls these ----------------------------------------------------------
    async def on_open(self, sock: Any) -> None:
        return None  # a phone authenticates first; its seat is made then

    async def on_message(self, sock: Any, text: str) -> None:
        heard = self._heard.setdefault(sock, deque())
        now = self.mono()
        while heard and now - heard[0] > RATE[1]:
            heard.popleft()
        heard.append(now)
        if len(heard) > RATE[0]:
            sock.close(1008, "Too many messages from this screen. Reload the page.")
            return
        try:
            message = json.loads(text)
        except ValueError:
            return
        if not isinstance(message, dict) or not isinstance(message.get("action"), str):
            return
        verdict = await self.judge(sock.session)
        if isinstance(verdict, FamilyError):
            sock.close(WS_CLOSE_SIGNED_OUT, words.say("ws_signed_out"))
            return
        seat = self.seats.get(sock)
        if seat is None:
            seat = self.seats[sock] = Seat(sock, dict(verdict.member))
        seat.member = dict(verdict.member)
        self.counts["messages"] += 1
        action = message["action"]
        if action == "authenticate":
            sock.send({"type": "auth", "status": "success"})
            if self.feed is not None:
                sock.send({"type": "feed_state", "state": self.feed.state,
                           "since": self.feed.since})  # fmt: skip
        elif action == "subscribe":
            self._subscribe(seat, message.get("symbols"))
        elif action == "unsubscribe":
            self._unsubscribe(seat, message.get("symbols"))
        elif action == "screen":
            name = message.get("name")
            seat.screen = name if name in SCREENS else None
            self.books.screen(sock, seat.member, seat.screen)
            for hook in self.on_screen:
                hook(seat)

    async def on_close(self, sock: Any) -> None:
        self._heard.pop(sock, None)
        self._pending.pop(sock, None)
        seat = self.seats.pop(sock, None)
        self.books.closed(sock)
        if seat is not None:
            for pair, key in seat.follows.items():
                self._drop(sock, pair, key)
            self._changed()

    # ---- following ------------------------------------------------------------------------------
    def visible(self, member_id: int) -> set[str]:
        keys = set(INDEX_KEYS)
        for source in self.sources:
            keys |= source.keys(member_id)
        return keys

    def _pairs(self, raw: Any) -> list[Pair | None]:
        if not isinstance(raw, list):
            return []
        out = []
        for item in raw[: MAX_SYMBOLS * 2]:
            ok = isinstance(item, dict)
            out.append(pair_of(item.get("symbol"), item.get("exchange")) if ok else None)
        return out

    def _subscribe(self, seat: Seat, raw: Any) -> None:
        refused, over, taken, waits = [], [], [], False
        visible = self.visible(int(seat.member["id"]))
        for pair in self._pairs(raw):
            listing = self.symbols.get(*pair) if pair else None
            if listing is None:
                refused.append(pair)
            elif pair in seat.follows or pair in seat.waiting:
                taken.append(pair)
            elif len(seat.follows) + len(seat.waiting) >= MAX_SYMBOLS:
                over.append(pair)
            elif listing.instrument_key in visible:
                self._follow(seat, pair, listing.instrument_key)
                taken.append(pair)
            else:
                seat.waiting[pair] = listing.instrument_key
                waits = True
        sock = seat.sock
        if refused:
            self.counts["refused"] += len(refused)
            sock.send({"type": "subscribe", "status": "error", "message": NOT_AVAILABLE,
                       "symbols": [symbol(p) for p in refused if p]})  # fmt: skip
        if over:
            sock.send({"type": "subscribe", "status": "error", "message": TOO_MANY,
                       "symbols": [symbol(p) for p in over]})  # fmt: skip
        if taken:
            sock.send({"type": "subscribe", "status": "success",
                       "symbols": [symbol(p) for p in taken]})  # fmt: skip
            self._quotes(seat, taken)
            self._changed()
        if waits:
            self._ensure(seat.member)

    def _unsubscribe(self, seat: Seat, raw: Any) -> None:
        for pair in self._pairs(raw):
            if pair is None:
                continue
            seat.waiting.pop(pair, None)
            key = seat.follows.pop(pair, None)
            if key is not None:
                self._drop(seat.sock, pair, key)
        seat.capped = frozenset(p for p in seat.capped if p in seat.follows)
        self._changed()

    def _follow(self, seat: Seat, pair: Pair, key: str) -> None:
        seat.follows[pair] = key
        self.by_key.setdefault(key, set()).add((seat.sock, pair))
        self.order.setdefault(key, next(self._seq))

    def _drop(self, sock: Any, pair: Pair, key: str) -> None:
        holders = self.by_key.get(key)
        if holders is not None:
            holders.discard((sock, pair))
            if not holders:
                del self.by_key[key]
                self.order.pop(key, None)
        pending = self._pending.get(sock)
        if pending is not None:
            pending.pop(pair, None)

    def recheck(self, member_id: int) -> None:
        """A member's books or lists were read: what waited is followed or refused, and what they
        may no longer see (an unshared list) is let go, once every source has an answer."""
        known = all(source.known(member_id) for source in self.sources)
        visible = self.visible(member_id)
        for seat in [s for s in self.seats.values() if int(s.member["id"]) == member_id]:
            moved = [p for p, k in seat.waiting.items() if k in visible]
            for pair in moved:
                self._follow(seat, pair, seat.waiting.pop(pair))
            gone: list[Pair] = []
            if known:
                for pair, key in [(p, k) for p, k in seat.follows.items() if k not in visible]:
                    del seat.follows[pair]
                    self._drop(seat.sock, pair, key)
                    gone.append(pair)
                gone += list(seat.waiting)
                seat.waiting.clear()
                seat.capped = frozenset(p for p in seat.capped if p in seat.follows)
            if gone:
                seat.sock.send({"type": "subscribe", "status": "error", "message": NOT_AVAILABLE,
                                "symbols": [symbol(p) for p in gone]})  # fmt: skip
            if moved:
                seat.sock.send({"type": "subscribe", "status": "success",
                                "symbols": [symbol(p) for p in moved]})  # fmt: skip
                self._quotes(seat, moved)
            if moved or gone:
                self._changed()

    def settle(self, member: Mapping[str, Any]) -> None:
        """A source's answer for the member changed (F7: a list shared or no longer shared):
        recheck now, and again once every source has an answer, the books being read for the
        member when they have none (what may be let go is decided only on every answer)."""
        member_id = int(member["id"])
        self.recheck(member_id)
        if all(source.known(member_id) for source in self.sources):
            return

        async def go() -> None:
            await asyncio.gather(*(s.ensure(member) for s in self.sources), return_exceptions=True)
            self.recheck(member_id)

        self._spawn(go())

    def _ensure(self, member: Mapping[str, Any]) -> None:
        member_id, now = int(member["id"]), self.mono()
        if now - self._ensured.get(member_id, -1e9) < ENSURE_EVERY_S:
            return
        for old in [m for m, at in self._ensured.items() if now - at >= ENSURE_EVERY_S]:
            del self._ensured[old]  # a member id is never reused: keep only the live window
        self._ensured[member_id] = now

        async def go() -> None:
            await asyncio.gather(*(s.ensure(member) for s in self.sources), return_exceptions=True)
            self.recheck(member_id)

        self._spawn(go())

    # ---- what the feed link asks and tells -----------------------------------------------------
    def wanted(self) -> tuple[list[str], list[str]]:
        """Followed keys in the order first followed: the members' book rows, then the rest."""
        book = self.books.book_keys()
        keys = sorted(self.by_key, key=lambda k: self.order.get(k, 0))
        first = [k for k in keys if k in book and k not in INDEX_KEYS]
        return first, [k for k in keys if k not in book and k not in INDEX_KEYS]

    def sockets(self) -> int:
        return len(self.seats)

    def open_sockets(self) -> list[tuple[Any, Mapping[str, Any]]]:
        return [(sock, seat.member) for sock, seat in self.seats.items()]

    def tick(self, key: str, data: dict[str, Any]) -> None:
        for sock, pair in self.by_key.get(key, ()):
            self._pending.setdefault(sock, {})[pair] = {
                "type": "market_data", "symbol": pair[0], "exchange": pair[1], "mode": 1,
                "data": data}  # fmt: skip
            self._due.set()

    def state(self, state: str, since: int) -> None:
        for sock in self.seats:
            sock.send({"type": "feed_state", "state": state, "since": since})

    def claims(self, allocated: frozenset[str]) -> None:
        """The feed link's claim changed: each socket whose symbols past the cap changed is told."""
        for seat in self.seats.values():
            capped = frozenset(p for p, k in seat.follows.items() if k not in allocated)
            if capped != seat.capped:
                changed = capped ^ seat.capped
                seat.capped = capped
                self._quotes(seat, changed)

    def _changed(self) -> None:
        if self.feed is not None:
            self.feed.wake()

    def _quotes(self, seat: Seat, pairs: Iterable[Pair]) -> None:
        """Quotes for ``pairs``; which are past the cap is the feed link's to say (``claims``)."""
        self._spawn(self._send_quotes(seat, list(pairs)))

    async def _send_quotes(self, seat: Seat, pairs: list[Pair]) -> None:
        keys = {p: seat.follows[p] for p in pairs if p in seat.follows}
        frames = await self.feed.snapshot(set(keys.values())) if self.feed is not None else {}
        if self.seats.get(seat.sock) is not seat:
            return  # closed while the snapshot was read
        quotes = []
        for pair, key in keys.items():
            listing = self.symbols.get(*pair)
            if listing is not None:
                quotes.append(quote_of(listing, frames.get(key)))
        message: dict[str, Any] = {"type": "quotes", "quotes": quotes,
                                   "capped": [symbol(p) for p in sorted(seat.capped)]}  # fmt: skip
        if seat.capped:
            message["message"] = CAPPED
        seat.sock.send(message)

    def _spawn(self, coro: Awaitable[None]) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._done)

    def _done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            log.error("a family socket task failed", exc_info=task.exception())

    # ---- the market route and the flusher -----------------------------------------------------
    async def route_market(self, request: Any, args: dict[str, Any], session: Any) -> Response:
        feed = self.feed
        frames = await feed.snapshot(INDEX_KEYS) if feed is not None else {}
        return Response.ok({"feed": feed.state if feed else "reconnecting",
                            "since": feed.since if feed else None,
                            "indices": [quote_of(i, frames.get(i.instrument_key))
                                        for i in INDICES]})  # fmt: skip

    async def run(self) -> None:
        """Send what ticked, every 100 ms: the newest of each symbol, to each socket."""
        while True:
            await self._due.wait()
            await asyncio.sleep(COALESCE_S)
            self._due.clear()
            pending, self._pending = self._pending, {}
            for sock, messages in pending.items():
                if sock in self.seats:
                    for message in messages.values():
                        self.counts["sent"] += bool(sock.send(message))

    async def close(self) -> None:
        tasks, self._tasks = list(self._tasks), set()
        await cancel_and_wait(*tasks)

    def health(self) -> dict[str, int]:
        return {"sockets": len(self.seats), "following": len(self.by_key), **self.counts}


def install(plane: Any, service: Any) -> Dialect:
    """Seat the sockets' dialect, the feed link and the symbol map in the data plane: the
    service's dialect and its viewer ``market`` route, the plane's pieces ``feed``, ``sockets``
    and ``symbols``, and the books' hooks (the rows they read become followable)."""
    from . import sessions
    from .feedlink import FeedLink
    from .symbol_map import listing_of

    settings, symbols = service.settings, SymbolMap()

    async def judge(hashed: str) -> Any:
        return await service.store.write(lambda tx: sessions.judge(tx, hashed, service.clock()))

    dialect = Dialect(judge=judge, symbols=symbols, books=plane.books)
    dialect.sources.append(plane.books)
    feed = FeedLink(http=plane.http, feed_url=settings.feed_url, zmq_url=settings.feed_zmq,
                    etc_dir=settings.etc_dir, quota=settings.max_claims, clock=service.clock,
                    market_open=lambda now: plane.market.open("positions", now),
                    wanted=dialect.wanted, sockets=dialect.sockets, on_tick=dialect.tick,
                    on_state=dialect.state, on_claims=dialect.claims)  # fmt: skip
    dialect.feed = feed

    def read(member_id: int, part: str, kept: Any) -> None:
        if part in ("positions", "holdings") and isinstance(kept.data, list):
            now = service.clock()
            symbols.put(found for row in kept.data
                        if isinstance(row, Mapping) and (found := listing_of(row, now)))  # fmt: skip
        dialect.recheck(member_id)
        feed.wake()

    plane.books.open_sockets = dialect.open_sockets
    plane.books.after_read.append(read)
    plane.parts.update(feed=feed.run, sockets=dialect.run,
                       symbols=lambda: symbols.keep(service.store, service.clock))  # fmt: skip
    plane.closers.append(dialect.close)
    plane.reports.update(feed=feed.health_view, sockets=dialect.health)
    plane.dialect, plane.feed, plane.symbols = dialect, feed, symbols
    service.dialect = dialect
    service.viewer._market = dialect.route_market
    return dialect
