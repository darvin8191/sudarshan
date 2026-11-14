"""The family service's link to sudarshan-feed (M18.1, M6.7; design 2.5; cross-design Y3, Y12).

    sudarshan-feed  ZMQ PUB 127.0.0.1:5581  -> one SUB here: ``__health__`` and exactly the claimed
                                               keys (frame 0 compared whole: ZMQ matches by prefix)
                    HTTP    127.0.0.1:5580  <- PUT/DELETE /claims/sudarshan-family with the family's
                                               own token (/etc/sudarshan-family/feed-token), GET
                                               /health, GET /snapshot (both open)

No Upstox socket of its own: the feed holds the one socket for the ring, and this service claims
only what viewer screens follow, in ``ltpc``:

* **What is claimed** (``allocate``): the three indices, then the keys of the members' own book rows
  that a screen follows, then the other keys a screen follows (shared lists) in the order they were
  first followed. At most the quota (``SUDARSHAN_FAMILY_MAX_CLAIMS``, 100, the feed's own line for
  this client, Y12). A new list key is claimed only while the socket's total (``subscribed.total``
  from the feed's health) plus the new keys stays at or under 450 (Y12.3); books and indices first.
  A followed key left out is "past the cap": the phone is told so (viewer_ws: ``quotes``) and shows
  the last price with its time, never as real-time.
* **When**: at once after a change (gathered for 0.2 s), renewed every 10 minutes, and at once
  when the feed's ``started_at`` changes (it restarted and forgot the claims); released 5 minutes
  after the last viewer socket closed. A refused claim is said once in ten minutes and tried again.
* **Per tick**: the frame is parsed, kept in memory as the key's last frame and handed to the
  sockets. Nothing is written to disk.

The feed state every phone is told (``feed_state``): ``closed`` outside the market's sessions (the
calendar the books use); ``realtime`` while the feed's socket is connected and subscribed;
``reconnecting`` otherwise, and when no health frame came for 15 s.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import zmq
import zmq.asyncio

from sudarshan.feed.client_auth import auth_headers
from sudarshan.tasks import cancel_and_wait

from .symbol_map import INDEX_KEYS

log = logging.getLogger("sudarshan.family")

CLIENT = "sudarshan-family"
TOKEN_FILE = "feed-token"
MODE = "ltpc"
HEALTH = b"__health__"
RENEW_S = 600.0
RELEASE_AFTER_S = 300.0
#: Y12.3: new list keys only while the socket's total plus them stays at or under this.
LINE = 450
HEALTH_STALE_S = 15.0
DRAIN = 256
RCVHWM = 20_000
SETTLE_S = 0.2
LOOP_S = 1.0
RETRY_S = 30.0
ASK_HEALTH_EVERY_S = 5.0
SNAPSHOT_BATCH = 100
TIMEOUT = httpx.Timeout(3.0, connect=1.0)
WARN_EVERY_S = 600.0


@dataclass(frozen=True)
class Health:
    """What one health document said, and when it was heard (monotonic seconds)."""

    state: str
    live: bool
    started_at: str | None
    total: int | None
    heard_at: float


def parse_health(payload: Any, heard_at: float) -> Health | None:
    try:
        doc = json.loads(payload) if isinstance(payload, bytes | str) else payload
    except ValueError:
        return None
    if not isinstance(doc, dict):
        return None
    subscribed = doc.get("subscribed")
    total = subscribed.get("total") if isinstance(subscribed, dict) else None
    started = doc.get("started_at")
    return Health(str(doc.get("state") or ""), doc.get("subscriptions_live") is True,
                  started if isinstance(started, str) else None,
                  total if type(total) is int and total >= 0 else None, heard_at)  # fmt: skip


def feed_state(health: Health | None, market_open: bool, now: float) -> str:
    """``closed``, ``realtime`` or ``reconnecting`` (the contract's FeedState)."""
    if not market_open:
        return "closed"
    if health is None or now - health.heard_at > HEALTH_STALE_S:
        return "reconnecting"
    if health.state == "connected" and health.live:
        return "realtime"
    return "closed" if health.state == "closed" else "reconnecting"


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float) or value != value:
        return None
    return value


def tick_data(frame: Mapping[str, Any]) -> dict[str, Any] | None:
    """A feed frame as the phone's ``market_data`` carries it (the contract's Tick, with
    ``prev_close`` beside ``close`` as the phone's live code reads it); None without a price."""
    ltp, close = _number(frame.get("ltp")), _number(frame.get("cp"))
    if ltp is None or ltp <= 0:
        return None
    close = close if close and close > 0 else None
    ltt = frame.get("ltt") if type(frame.get("ltt")) is int else None
    stamp = ltt or (frame.get("ts_recv") if type(frame.get("ts_recv")) is int else None)
    return {"ltp": ltp, "change": round(ltp - close, 4) if close else None,
            "change_percent": round((ltp - close) / close * 100, 4) if close else None,
            "close": close, "prev_close": close, "ltt": ltt, "timestamp": stamp}  # fmt: skip


def allocate(first: Iterable[str], watch: Iterable[str], held: frozenset[str],
             total: int | None, quota: int, line: int = LINE) -> tuple[str, ...]:  # fmt: skip
    """The keys to claim: ``first`` (indices, then book rows) in order up to the quota, then
    ``watch`` in screen order while the socket's total plus the new keys stays at or under the
    line (Y12); a key already held costs nothing new, and an unknown total admits no new list key."""
    out: list[str] = []
    for key in first:
        if len(out) < quota and key not in out:
            out.append(key)
    room = None if total is None else line - total - sum(k not in held for k in out)
    for key in watch:
        if len(out) >= quota:
            break
        if key in out:
            continue
        if key in held:
            out.append(key)
        elif room is not None and room > 0:
            out.append(key)
            room -= 1
    return tuple(out)


class FeedLink:
    """One ZMQ SUB and the claims. ``run`` is the data plane's piece ``feed``."""

    def __init__(self, *, http: httpx.AsyncClient, feed_url: str, zmq_url: str, etc_dir: Path,
                 quota: int, clock: Callable[[], int], market_open: Callable[[int], bool],
                 wanted: Callable[[], tuple[list[str], list[str]]], sockets: Callable[[], int],
                 on_tick: Callable[[str, dict], None], on_state: Callable[[str, int], None],
                 on_claims: Callable[[frozenset[str]], None],
                 monotonic: Callable[[], float] = time.monotonic):  # fmt: skip
        self.http, self.url, self.zmq_url, self.etc_dir = (
            http,
            feed_url.rstrip("/"),
            zmq_url,
            etc_dir,
        )
        self.quota, self.clock, self.market_open, self.mono = quota, clock, market_open, monotonic
        self.wanted, self.sockets = wanted, sockets
        self.on_tick, self.on_state, self.on_claims = on_tick, on_state, on_claims
        self.health: Health | None = None
        self.state, self.since = "reconnecting", clock()
        self.ticks: dict[str, dict[str, Any]] = {}  # the last frame of each claimed key
        self.allocated: tuple[str, ...] = ()
        self.claimed: frozenset[str] = frozenset()  # what the feed took at the last PUT
        self.claimed_at = -1e9
        self.claimed_started: str | None = None
        self.failed_at: float | None = None
        self.last_socket_at: float | None = None
        self.counts = {"ticks": 0, "claims": 0, "refused": 0}
        self._asked_at = -1e9
        self._wake = asyncio.Event()
        self._ctx: zmq.asyncio.Context | None = None
        self._sock: Any = None
        self._topics: frozenset[bytes] = frozenset()
        self._warned: dict[str, float] = {}

    def wake(self) -> None:
        """Something a claim depends on changed (a socket, what it follows, a book)."""
        if self.sockets():
            self.last_socket_at = self.mono()
        self._wake.set()

    # ---- the bus --------------------------------------------------------------------------------
    def _open(self) -> None:
        """The context and the SUB; each kept as soon as made, so ``_close`` ends what a failure
        half way left open (a malformed endpoint raises in ``connect``)."""
        self._ctx = zmq.asyncio.Context()
        self._sock = self._ctx.socket(zmq.SUB)
        self._sock.setsockopt(zmq.LINGER, 0)
        self._sock.setsockopt(zmq.RCVHWM, RCVHWM)
        self._sock.connect(self.zmq_url)  # the feed binds; a SUB connects and reconnects itself
        self._sock.subscribe(HEALTH)
        self._topics = frozenset()
        self._follow(self.allocated)

    def _close(self) -> None:
        sock, ctx, self._sock, self._ctx = self._sock, self._ctx, None, None
        if sock is not None and not sock.closed:
            sock.close(0)
        if ctx is not None and not ctx.closed:
            ctx.term()

    def _follow(self, keys: Iterable[str]) -> None:
        want = frozenset(key.encode() for key in keys)
        if self._sock is not None:
            for topic in want - self._topics:
                self._sock.subscribe(topic)
            for topic in self._topics - want:
                self._sock.unsubscribe(topic)
        self._topics = want

    def key_of(self, topic: bytes) -> str | None:
        """The claimed key a frame is for: frame 0 compared whole (mutation target 15)."""
        return topic.decode() if topic in self._topics else None

    async def _receive(self) -> None:
        while True:
            frames = [await self._sock.recv_multipart()]
            while len(frames) < DRAIN and self._sock.get(zmq.EVENTS) & zmq.POLLIN:
                frames.append(await self._sock.recv_multipart())
            for frame in frames:
                if len(frame) != 2:
                    continue
                if frame[0] == HEALTH:
                    self._heard(parse_health(frame[1], self.mono()))
                elif (key := self.key_of(frame[0])) is not None:
                    self._tick(key, frame[1])

    def _tick(self, key: str, payload: bytes) -> None:
        try:
            frame = json.loads(payload)
        except ValueError:
            return
        data = tick_data(frame) if isinstance(frame, dict) else None
        if data is None:
            return
        self.ticks[key] = frame
        self.counts["ticks"] += 1
        self.on_tick(key, data)

    def _heard(self, health: Health | None) -> None:
        if health is None:
            return
        self.health = health
        if self.claimed and health.started_at != self.claimed_started:
            self._wake.set()  # the feed restarted and forgot the claims
        self.update_state()

    def update_state(self) -> None:
        now = self.clock()
        state = feed_state(self.health, self.market_open(now), self.mono())
        if state != self.state:
            self.state, self.since = state, now
            self.on_state(state, now)

    async def _watch(self) -> None:
        """The state follows the clock too (the close, a feed gone silent): looked at each second."""
        while True:
            await asyncio.sleep(LOOP_S)
            self.update_state()

    # ---- the claims -----------------------------------------------------------------------------
    async def _claims(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), LOOP_S)
                await asyncio.sleep(SETTLE_S)  # gather a burst of subscribes into one claim
            except TimeoutError:
                pass
            self._wake.clear()
            await self.step()

    async def step(self) -> None:
        now = self.mono()
        if self.sockets():
            self.last_socket_at = now
        elif self.last_socket_at is None or now - self.last_socket_at >= RELEASE_AFTER_S:
            if self.claimed or self.allocated:
                await self._release(now)
            return
        else:
            return  # no socket, but for less than 5 minutes: the claims stay
        first, watch = self.wanted()
        total = await self._total(now)
        alloc = allocate((*INDEX_KEYS, *first), watch, frozenset(self.allocated), total, self.quota)
        if alloc != self.allocated:
            self.allocated = alloc
            self._follow(alloc)
            for key in [k for k in self.ticks if k not in alloc]:
                del self.ticks[key]
        self.on_claims(frozenset(alloc))  # every step: a symbol just followed may be past the cap
        started = self.health.started_at if self.health else None
        due = frozenset(alloc) != self.claimed or now - self.claimed_at >= RENEW_S or (
            self.claimed and started is not None and started != self.claimed_started)  # fmt: skip
        if due and (self.failed_at is None or now - self.failed_at >= RETRY_S):
            await self._put(alloc, now)

    async def _total(self, now: float) -> int | None:
        """The socket's total, from health heard in the last 15 s, else asked (at most every 5 s)."""
        if self.health is not None and now - self.health.heard_at <= HEALTH_STALE_S:
            return self.health.total
        if now - self._asked_at >= ASK_HEALTH_EVERY_S:
            self._asked_at = now
            try:
                answer = await self.http.get(f"{self.url}/health", timeout=TIMEOUT)
                self._heard(
                    parse_health(answer.content, now) if answer.status_code == 200 else None
                )
            except httpx.HTTPError:
                self._warn("down", "the price feed is not answering the family view")
        return self.health.total if self.health is not None else None

    async def _put(self, keys: tuple[str, ...], now: float) -> None:
        body = {"keys": dict.fromkeys(sorted(keys), MODE)}
        try:
            answer = await self.http.put(f"{self.url}/claims/{CLIENT}", json=body, timeout=TIMEOUT,
                                         headers=auth_headers(TOKEN_FILE, self.etc_dir))  # fmt: skip
        except httpx.HTTPError:
            self.failed_at = now
            self._warn("down", "the price feed is not answering the family view")
            return
        if answer.status_code == 200:
            self.claimed, self.claimed_at, self.failed_at = frozenset(keys), now, None
            self.claimed_started = self.health.started_at if self.health else None
            self.counts["claims"] += 1
            return
        self.failed_at = now
        self.counts["refused"] += 1
        self._warn(f"put{answer.status_code}", "the price feed refused the family view's %d "
                   "symbols (%d); they show their last price until it takes them",
                   len(keys), answer.status_code)  # fmt: skip

    async def _release(self, now: float) -> None:
        if self.failed_at is not None and now - self.failed_at < RETRY_S:
            return
        try:
            answer = await self.http.delete(f"{self.url}/claims/{CLIENT}", timeout=TIMEOUT,
                                            headers=auth_headers(TOKEN_FILE, self.etc_dir))  # fmt: skip
        except httpx.HTTPError:
            self.failed_at = now
            return
        if answer.status_code in (200, 204, 404):
            self.claimed, self.allocated, self.failed_at = frozenset(), (), None
            self._follow(())
            self.ticks.clear()
            self.on_claims(frozenset())
        else:
            self.failed_at = now

    # ---- what the sockets and the market route read -----------------------------------------
    async def snapshot(self, keys: Iterable[str]) -> dict[str, dict[str, Any]]:
        """The last frame of each key: this service's own, else the feed's ``/snapshot`` (a key
        the feed never had a tick for is left out)."""
        keys = list(dict.fromkeys(keys))
        found = {key: self.ticks[key] for key in keys if key in self.ticks}
        rest = [key for key in keys if key not in found]
        for start in range(0, len(rest), SNAPSHOT_BATCH):
            batch = rest[start : start + SNAPSHOT_BATCH]
            try:
                answer = await self.http.get(f"{self.url}/snapshot", timeout=TIMEOUT,
                                             params={"keys": ",".join(batch)})  # fmt: skip
                ticks = answer.json().get("ticks") if answer.status_code == 200 else None
            except (httpx.HTTPError, ValueError, AttributeError):
                break
            for key, frame in (ticks or {}).items() if isinstance(ticks, dict) else ():
                if key in batch and isinstance(frame, dict):
                    found[key] = frame
        return found

    def health_view(self) -> dict[str, Any]:
        return {"state": self.state, "claimed": len(self.claimed), "following": len(self._topics),
                **self.counts}  # fmt: skip

    async def run(self) -> None:
        tasks: list[asyncio.Task] = []
        try:
            self._open()
            tasks = [asyncio.create_task(job(), name=f"family-feed-{job.__name__.strip('_')}")
                     for job in (self._receive, self._claims, self._watch)]  # fmt: skip
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for task in done:
                task.result()
        finally:
            try:
                await cancel_and_wait(*tasks)
            finally:
                # Always, even when the stop cancels again while the tasks end: a socket left
                # open makes the context's term() hang the process at exit.
                self._close()

    def _warn(self, kind: str, text: str, *args: Any) -> None:
        now = self.mono()
        if now - self._warned.get(kind, -1e9) >= WARN_EVERY_S:
            self._warned[kind] = now
            log.warning(text, *args)
