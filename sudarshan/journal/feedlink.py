"""Live MAE and MFE from the price feed (JB6, M14.3; cross-design Y12, Y21).

    sudarshan-feed  HTTP 127.0.0.1:5580  <- PUT/DELETE /claims/sudarshan-journal (ltpc, at most 50)
                    ZMQ  127.0.0.1:5581  -> SUB here: ``__health__`` and the claimed keys only

* **Claims.** The instrument keys of the open trips (live and sandbox), the most recently opened
  first, at most ``max_claims`` (50: the feed's quota for this client), each in ``ltpc``. The set
  is put when it changes (a trip opened or closed), renewed every 10 minutes and again when the
  feed's ``started_at`` changes; with no open trip the claim is dropped. No new Upstox socket: the
  feed holds the one socket for every ring service.
* **Ticks.** The SUB follows exactly the claimed keys; ZMQ matches topics by prefix, so frame 0 is
  compared whole before its payload is read (the pattern of ``alerts/market/feedlink.py``). Each
  key keeps its last ``RECENT_MS`` of ticks; a tick older than that is folded into the running
  low and high, with their times, of each open trip of the key it falls inside.
* **At close** (the capture's hook, after the day was paired again) the trip's path is its folded
  low and high plus the recent ticks up to its last exit, so a tick after the exit never counts;
  ``core/excursion.py`` gives MAE, MFE and their instants, written on the trip with the tracked
  span in ``trip_track``. A trip opened before this process started has a path that starts late:
  the core notes "from HH:MM:SS", and the 17:00 job fills the span before from the recording.

Everything here is in memory and per tick costs a JSON parse and a few comparisons; logs carry
counts only, never a key or a price.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

import httpx
import zmq
import zmq.asyncio

from sudarshan.feed.client_auth import auth_headers

from .core import kinds
from .core.excursion import excursion
from .store import Scope

log = logging.getLogger("sudarshan.journal")

OWNER = Scope.owner()
HEALTH = "__health__"
CLIENT = kinds.SERVICE
MODE = "ltpc"
#: Frames taken from the socket before the loop yields, and the socket's own queue.
DRAIN = 256
RCVHWM = 20_000
HTTP_TIMEOUT_S = 5.0
#: How long a key keeps its own ticks before they are folded into the trips' low and high: the
#: time an exit fill may take to be recorded (a nudge, the 3 s debounce, one read).
RECENT_MS = 90_000
RENEW_S = 600.0
RETRY_S = 60.0
#: How often the open trips are read again besides the capture's hook (an owner's edit, a
#: trip the hook missed).
RESYNC_S = 60.0
LOG_EVERY_S = 600.0


class FeedRefused(Exception):
    """The feed refused the claim (409 over the quota or a cap, 400 a bad key)."""


class FeedUnreachable(Exception):
    """The feed did not answer."""


def price_u(value: Any) -> int | None:
    """A tick's ``ltp`` in price_u (rupees x 10^4, half up), or None when it is no price."""
    if value is None or isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        found = (Decimal(repr(value)) * kinds.PRICE_SCALE).quantize(Decimal(1), ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None
    return int(found) if found > 0 else None


def tick_point(payload: bytes) -> tuple[int, int] | None:
    """(ms, price_u) of one tick: its last trade time (the time it was received when the feed has
    no trade time yet) and its last price; None for a payload that has neither."""
    try:
        tick = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(tick, dict):
        return None
    price = price_u(tick.get("ltp"))
    at = tick.get("ltt") or tick.get("ts_recv")
    if price is None or not isinstance(at, int) or isinstance(at, bool) or at <= 0:
        return None
    return at, price


class FeedLink:
    """One ZMQ SUB and one HTTP client to the feed. ``close`` once (twice is harmless)."""

    def __init__(self, zmq_endpoint: str, http_url: str):
        self.http_url = http_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=HTTP_TIMEOUT_S, trust_env=False)
        self.ctx = zmq.asyncio.Context()
        self.sock = self.ctx.socket(zmq.SUB)
        self.sock.setsockopt(zmq.LINGER, 0)
        self.sock.setsockopt(zmq.RCVHWM, RCVHWM)
        self.sock.connect(zmq_endpoint)  # the feed binds; a SUB connects and reconnects itself
        self.sock.subscribe(HEALTH.encode())
        self.topics: frozenset[str] = frozenset()

    def follow(self, keys: Iterable[str]) -> None:
        """Receive exactly these keys' frames (and health) from now on."""
        want = frozenset(keys)
        for key in want - self.topics:
            self.sock.subscribe(key.encode())
        for key in self.topics - want:
            self.sock.unsubscribe(key.encode())
        self.topics = want

    async def receive(self) -> list[tuple[str, bytes]]:
        """Wait for a frame, then take those already waiting, up to DRAIN: (topic, payload) of
        health and the keys followed, each topic matched whole."""
        frames = [await self.sock.recv_multipart()]
        while len(frames) < DRAIN and self.sock.get(zmq.EVENTS) & zmq.POLLIN:
            frames.append(await self.sock.recv_multipart())
        out = []
        for frame in frames:
            if len(frame) != 2:
                continue
            try:
                topic = frame[0].decode()
            except UnicodeDecodeError:
                continue
            if topic == HEALTH or topic in self.topics:
                out.append((topic, frame[1]))
        return out

    async def put_claims(self, keys: Iterable[str]) -> None:
        await self._call("PUT", {"keys": dict.fromkeys(keys, MODE)})

    async def drop_claims(self) -> None:
        await self._call("DELETE", None)

    async def _call(self, method: str, body: Any) -> None:
        try:
            answer = await self.client.request(
                method, f"{self.http_url}/claims/{CLIENT}", json=body,
                headers=auth_headers(CLIENT), follow_redirects=False,
            )  # fmt: skip
        except httpx.HTTPError as exc:
            raise FeedUnreachable("the price feed did not answer") from exc
        if answer.status_code in (400, 409):
            try:
                said = answer.json().get("error")
            except (ValueError, AttributeError):
                said = None
            raise FeedRefused(str(said or "the price feed refused the claim"))
        if answer.status_code not in (200, 204) and not (
            method == "DELETE" and answer.status_code == 404
        ):
            raise FeedUnreachable(f"the price feed answered {answer.status_code}")

    async def aclose(self) -> None:
        await self.client.aclose()
        if not self.sock.closed:
            self.sock.close(0)
        if not self.ctx.closed:
            self.ctx.term()


@dataclass
class Track:
    """One open trip's running low and high, folded from ticks older than RECENT_MS."""

    trip_id: int
    key: str
    start_ms: int
    low: tuple[int, int] | None = None  # (ms, price_u)
    high: tuple[int, int] | None = None
    first_ms: int | None = None
    ticks: int = 0

    def fold(self, ms: int, price: int) -> None:
        if ms < self.start_ms:
            return
        if self.low is None or price < self.low[1]:
            self.low = (ms, price)
        if self.high is None or price > self.high[1]:
            self.high = (ms, price)
        self.first_ms = ms if self.first_ms is None else min(self.first_ms, ms)
        self.ticks += 1


@dataclass(frozen=True)
class Span:
    """What a trip's ticks give the core: the real points (the low and the high found, and any
    tick kept whole) and the instant the ticks cover the trip from."""

    points: list[tuple[int, int]]
    covered_from: int | None
    ticks: int


def span_of(track: Track, recent: Iterable[tuple[int, int]], end_ms: int) -> Span:
    """A trip that closed at ``end_ms``: its folded low and high and the recent ticks inside its
    span; a tick after the exit never counts."""
    inside = [(ms, price) for ms, price in recent if track.start_ms <= ms <= end_ms]
    points = [p for p in (track.low, track.high) if p is not None] + inside
    starts = [ms for ms, _ in inside] + ([track.first_ms] if track.first_ms is not None else [])
    return Span(points, min(starts) if starts else None, track.ticks + len(inside))


def excursion_values(trip: Mapping[str, Any], span: Span,
                     source: str = "recording") -> dict[str, Any]:  # fmt: skip
    """The trip's excursion columns (``core/excursion.py``). The instant the ticks cover the trip
    from goes in as a point at the entry price: a move of 0 changes no figure, and the core then
    notes a path that starts late ("from 10:01:30") from the first tick, not from the low."""
    path = list(span.points)
    if span.covered_from is not None:
        path.append((span.covered_from, int(trip["entry_avg_u"])))
    found = excursion(
        trip["side"], int(trip["entry_avg_u"]), int(trip["qty_max"]), path,
        int(trip["opened_ms"]), int(trip["closed_ms"]), int(trip["net_paise"] or 0), source,
    )  # fmt: skip
    return {
        "mae_paise": found["mae_paise"], "mfe_paise": found["mfe_paise"],
        "mae_ms": found["mae_ms"], "mfe_ms": found["mfe_ms"],
        "excursion_source": found["source"], "excursion_note": found["note"],
    }  # fmt: skip


def track_values(span: Span, end_ms: int) -> dict[str, Any] | None:
    """The tracked span of a trip for ``trip_track``: where it starts, its low and its high."""
    if not span.points or span.covered_from is None:
        return None
    low = min(span.points, key=lambda p: (p[1], p[0]))
    high = max(span.points, key=lambda p: (p[1], -p[0]))
    return {"from_ms": span.covered_from, "to_ms": end_ms, "low_u": low[1], "low_ms": low[0],
            "high_u": high[1], "high_ms": high[0], "ticks": span.ticks}  # fmt: skip


class Excursions:
    """The service's tracker (a part: ``excursions_for``)."""

    def __init__(self, service: Any, link: FeedLink | None = None):
        self.service = service
        settings = service.settings
        self.link = link or FeedLink(settings.feed_zmq, settings.feed_url)
        self.max_claims = settings.max_claims
        self.tracks: dict[int, Track] = {}
        self.recent: dict[str, deque[tuple[int, int]]] = {}
        self.claimed: tuple[str, ...] | None = None  # None: not put yet
        self.wanted: tuple[str, ...] = ()
        self.started_at: str | None = None
        self.due = asyncio.Event()
        self._said: dict[str, float] = {}
        self._sync_lock = asyncio.Lock()

    # ---- the open trips ------------------------------------------------------------------------
    async def sync(self, mode: str | None = None, days: set[int] | None = None) -> None:
        """Read the open trips again: track the new ones, close the ones that closed, follow
        their keys and mark the claim due when the set changed. Also the capture's hook."""
        async with self._sync_lock:
            store = self.service.store
            if store is None:
                return
            rows = await asyncio.get_running_loop().run_in_executor(
                None, lambda: store.read(OWNER, lambda tx: tx.rows("round_trip", status="open"))
            )
            open_now = {r["id"]: r for r in rows if r.get("instrument_key")}
            for trip_id in [t for t in self.tracks if t not in open_now]:
                await self._finish(self.tracks.pop(trip_id))
            for trip_id, row in open_now.items():
                if trip_id not in self.tracks:
                    self.tracks[trip_id] = Track(
                        trip_id, row["instrument_key"], int(row["opened_ms"])
                    )
            newest = sorted(open_now.values(), key=lambda r: (-int(r["opened_ms"]), r["id"]))
            keys: list[str] = []
            for row in newest:
                if row["instrument_key"] not in keys:
                    keys.append(row["instrument_key"])
            self.wanted = tuple(keys[: self.max_claims])
            self.link.follow(self.wanted)
            for key in [k for k in self.recent if k not in self.wanted]:
                del self.recent[key]
            if self.wanted != self.claimed:
                self.due.set()

    async def _finish(self, track: Track) -> None:
        """A tracked trip that is no longer open: its excursion when it closed, nothing when it
        became an orphan or was replaced."""
        store, now = self.service.store, self.service.clock()
        recent = list(self.recent.get(track.key, ()))  # taken here: the ticks loop appends to it

        def write(tx):
            trip = tx.get("round_trip", track.trip_id)
            if trip is None or trip["status"] != "closed" or trip["closed_ms"] is None:
                return False
            span = span_of(track, recent, int(trip["closed_ms"]))
            tx.touch("round_trip", track.trip_id, excursion_values(trip, span), now)
            kept = track_values(span, int(trip["closed_ms"]))
            if kept is not None:
                tx.trip_track_put(track.trip_id, kept, now)
            return True

        await asyncio.get_running_loop().run_in_executor(None, lambda: store.write(OWNER, write))

    # ---- ticks ---------------------------------------------------------------------------------
    def on_tick(self, key: str, payload: bytes) -> None:
        point = tick_point(payload)
        if point is None:
            return
        recent = self.recent.setdefault(key, deque())
        recent.append(point)
        edge = point[0] - RECENT_MS
        while recent and recent[0][0] < edge:
            ms, price = recent.popleft()
            for track in self.tracks.values():
                if track.key == key:
                    track.fold(ms, price)

    def on_health(self, payload: bytes) -> None:
        try:
            doc = json.loads(payload)
        except ValueError:
            return
        started = doc.get("started_at") if isinstance(doc, dict) else None
        if isinstance(started, str) and started != self.started_at:
            if self.started_at is not None:
                self.due.set()  # the feed restarted: put the claim again
            self.started_at = started

    async def run_ticks(self) -> None:
        """The SUB's loop: frames as they come, a burst at most DRAIN at a time."""
        await self.sync()
        while True:
            for topic, payload in await self.link.receive():
                if topic == HEALTH:
                    self.on_health(payload)
                else:
                    self.on_tick(topic, payload)
            await asyncio.sleep(0)

    # ---- claims --------------------------------------------------------------------------------
    async def run_claims(self) -> None:
        """Put the claim when it is due (a change, the feed restarted), renew it every 10
        minutes, read the open trips again every minute; a refusal is said and tried again."""
        renewed = 0.0
        while True:
            try:
                await asyncio.wait_for(self.due.wait(), RESYNC_S)
            except TimeoutError:
                await self.sync()
            if self.due.is_set() or time.monotonic() - renewed >= RENEW_S:
                self.due.clear()
                if await self.claim():
                    renewed = time.monotonic()
                else:
                    await asyncio.sleep(RETRY_S)
                    self.due.set()

    async def claim(self) -> bool:
        wanted = self.wanted
        try:
            if wanted:
                await self.link.put_claims(wanted)
            elif self.claimed is None or self.claimed:
                await self.link.drop_claims()
        except FeedRefused as exc:
            self._say("refused", "The price feed refused the journal's claim (%s); MAE and MFE "
                      "wait for it", exc)  # fmt: skip
            return False
        except FeedUnreachable:
            self._say("down", "The price feed is not answering; MAE and MFE wait for it")
            return False
        self.claimed = wanted
        self.service.counts["claims"] = len(wanted)
        log.info("feed claims: %d keys, %d trips tracked", len(wanted), len(self.tracks))
        return True

    def _say(self, kind: str, message: str, *args: Any) -> None:
        now = time.monotonic()
        if now - self._said.get(kind, -1e9) >= LOG_EVERY_S:
            self._said[kind] = now
            log.warning(message, *args)

    async def aclose(self) -> None:
        await self.link.aclose()


def excursions_for(service: Any) -> Excursions:
    """The service's one tracker, its hook added to the capture's (after the day's pairing)."""

    def make(svc: Any) -> Excursions:
        from .capture import capture_for

        made = Excursions(svc)
        capture_for(svc).hooks.append(made.sync)
        return made

    return service.part("excursions", make)


def register_jobs() -> None:
    from .service import job

    @job("ticks")
    async def ticks(service) -> None:
        await excursions_for(service).run_ticks()

    @job("claims")
    async def claims(service) -> None:
        await excursions_for(service).run_claims()
