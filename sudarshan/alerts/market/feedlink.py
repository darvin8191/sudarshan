"""Market alerts' link to sudarshan-feed (M10.1): ticks and health in, claims and snapshots out.

    sudarshan-feed  ZMQ PUB 127.0.0.1:5581  -> SUB here: ``__health__`` and each alert's key
                    HTTP    127.0.0.1:5580  <- PUT/DELETE /claims/sudarshan-alerts, GET /snapshot

No new Upstox socket: the feed holds the one Analytics-token socket for every ring service, and
this client only claims the keys its Active alerts need (feed/claims.py), each in the mode they
need. ZMQ topics match by prefix ("NSE_INDEX|Nifty 50" also matches "NSE_INDEX|Nifty 500"), so a
frame's topic is compared exactly before its payload is read. Frames are drained up to DRAIN at a
time, then the loop yields, so one burst never starves the rest of the service. Every frame is
the key's whole current state (feed/README.md), so nothing is merged here.

Health arrives every 5 s and at once on a change of the exchange's status (M18.1); it is used as
it comes, never on a timer of its own (review A7.d).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field

import httpx
import zmq
import zmq.asyncio

log = logging.getLogger("sudarshan.alerts.market")

HEALTH = "__health__"
#: This service's client id at the feed: its claims, its quota (300) and its 409s carry it.
CLIENT = "sudarshan-alerts"
#: Frames taken from the socket before the loop yields.
DRAIN = 256
#: Frames the socket keeps for this subscriber before ZMQ drops the oldest for it.
RCVHWM = 20_000
SNAPSHOT_BATCH = 100
HTTP_TIMEOUT_S = 5.0


class FeedRefused(Exception):
    """The feed refused a claim (409 over a quota or a cap, 400 a bad key); str() is its words."""


class FeedUnreachable(Exception):
    """The feed did not answer."""


@dataclass(frozen=True)
class Health:
    """What one ``__health__`` frame said, and when it arrived (epoch ms)."""

    state: str
    live: bool
    heard_at: int
    started_at: str | None = None
    #: Segment -> the exchange's status (NORMAL_OPEN ...), as the feed last heard it.
    status: dict[str, str] = field(default_factory=dict)
    #: Seconds since the socket's last message of any kind (the whole socket, review A7.e).
    message_age_s: float | None = None


def parse_health(payload: bytes, heard_at: int) -> Health | None:
    try:
        doc = json.loads(payload)
    except ValueError:
        return None
    if not isinstance(doc, dict):
        return None
    status = doc.get("market_status")
    status = (
        {k: v for k, v in status.items() if isinstance(v, str)} if isinstance(status, dict) else {}
    )
    age = doc.get("last_message_age_s")
    age = float(age) if isinstance(age, int | float) and not isinstance(age, bool) else None
    started = doc.get("started_at")
    return Health(
        state=str(doc.get("state") or "unknown"),
        live=doc.get("subscriptions_live") is True,
        heard_at=heard_at,
        started_at=started if isinstance(started, str) else None,
        status=status,
        message_age_s=age,
    )


class FeedLink:
    """One ZMQ SUB and the service's HTTP client to the feed. Close it once (``close``)."""

    def __init__(self, zmq_endpoint: str, http_url: str, client: httpx.AsyncClient):
        self.http_url, self.client = http_url.rstrip("/"), client
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
        """Wait for a frame, then take those already waiting, up to DRAIN: (topic, payload),
        only health and the keys followed (a topic is matched exactly)."""
        frames = [await self.sock.recv_multipart()]
        while len(frames) < DRAIN and self.sock.getsockopt(zmq.EVENTS) & zmq.POLLIN:
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

    async def put_claims(self, keys: dict[str, str]) -> dict:
        """This service's whole set of keys, each in its mode; renews the claim."""
        return await self._call("PUT", f"/claims/{CLIENT}", {"keys": keys})

    async def drop_claims(self) -> dict:
        return await self._call("DELETE", f"/claims/{CLIENT}")

    async def snapshot(self, keys: list[str]) -> dict[str, dict]:
        """The feed's last frame of each key that has one, SNAPSHOT_BATCH keys a request."""
        found: dict[str, dict] = {}
        for start in range(0, len(keys), SNAPSHOT_BATCH):
            batch = ",".join(keys[start : start + SNAPSHOT_BATCH])
            ticks = (await self._call("GET", "/snapshot", params={"keys": batch})).get("ticks")
            for key, tick in (ticks or {}).items() if isinstance(ticks, dict) else ():
                if isinstance(tick, dict):
                    found[key] = tick | {"key": key}
        return found

    async def _call(self, method: str, path: str, body=None, params=None) -> dict:
        try:
            answer = await self.client.request(
                method, self.http_url + path, json=body, params=params, timeout=HTTP_TIMEOUT_S
            )
        except httpx.HTTPError as exc:
            raise FeedUnreachable(str(exc)) from exc
        try:
            payload = answer.json()
        except ValueError:
            payload = {}
        payload = payload if isinstance(payload, dict) else {}
        if answer.status_code in (400, 409):
            raise FeedRefused(str(payload.get("error") or "the feed refused the request"))
        if answer.status_code != 200:
            raise FeedUnreachable(f"the feed answered {answer.status_code}")
        return payload

    def close(self) -> None:
        self.sock.close(0)
        self.ctx.term()
