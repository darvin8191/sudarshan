"""sudarshan-feed as market alerts see it, on 127.0.0.1: a ZMQ PUB and the HTTP API they use."""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import datetime
from urllib.parse import parse_qs, unquote, urlsplit

import zmq
import zmq.asyncio

OPEN = {"NSE_EQ": "NORMAL_OPEN", "NSE_FO": "NORMAL_OPEN", "NSE_INDEX": "NORMAL_OPEN",
        "BSE_EQ": "NORMAL_OPEN", "BSE_FO": "NORMAL_OPEN", "BSE_INDEX": "NORMAL_OPEN"}  # fmt: skip


def ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


class FakeFeed:
    """Publishes ``__health__`` every ``beat_s`` and the ticks a test sends; answers
    ``PUT``/``DELETE /claims/<client>`` (recording each body) and ``GET /snapshot``.

    ``health`` is the document published; ``refuse`` (status, error) answers the next claim.
    """

    def __init__(self, clock, beat_s: float = 0.05):
        self.clock, self.beat_s = clock, beat_s
        self.health = {
            "state": "connected",
            "subscriptions_live": True,
            "started_at": "2026-10-21T08:58:00+05:30",
            "market_status": dict(OPEN),
            "last_message_age_s": 0.1,
        }
        self.claims: list[dict] = []
        self.drops = 0
        self.snapshot: dict[str, dict] = {}
        self.snapshot_asks: list[list[str]] = []
        self.refuse: tuple[int, str] | None = None
        self.answering = True

    async def __aenter__(self) -> FakeFeed:
        self.ctx = zmq.asyncio.Context()
        self.pub = self.ctx.socket(zmq.PUB)
        self.pub.setsockopt(zmq.LINGER, 0)
        self.pub.bind("tcp://127.0.0.1:*")
        self.endpoint = self.pub.getsockopt_string(zmq.LAST_ENDPOINT)
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        self._beat = asyncio.create_task(self._heartbeat())
        return self

    async def __aexit__(self, *exc) -> None:
        self._beat.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._beat
        self.server.close()
        await self.server.wait_closed()
        self.pub.close(0)
        self.ctx.term()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def keys(self) -> dict[str, str]:
        """The last claim's keys."""
        return self.claims[-1]["keys"] if self.claims else {}

    async def _heartbeat(self) -> None:
        while True:
            await self.send_health()
            await asyncio.sleep(self.beat_s)

    async def send_health(self) -> None:
        await self.pub.send_multipart([b"__health__", json.dumps(self.health).encode()])

    async def tick(self, key: str, ltp: float | None = None, *, ltt: datetime | None = None,
                   recv: datetime | None = None, **fields) -> dict:  # fmt: skip
        """One frame: the key's state now; ``ltp`` with its trade time (now unless ``ltt``)."""
        now = self.clock()
        frame = {"key": key, "ts_recv": ms(recv or now)} | fields
        if ltp is not None:
            frame |= {"ltp": ltp, "ltt": ms(ltt or now), "ltq": fields.get("ltq", 1)}
        await self.pub.send_multipart([key.encode(), json.dumps(frame).encode()])
        return frame

    async def _serve(self, reader, writer) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            request_line, *lines = head.decode("latin-1").split("\r\n")
            headers = {}
            for line in lines:
                name, _, value = line.partition(":")
                headers[name.strip().lower()] = value.strip()
            body = await reader.readexactly(int(headers.get("content-length") or 0))
        except (asyncio.IncompleteReadError, ConnectionError):
            writer.close()
            return
        method, target, _ = request_line.split(" ", 2)
        status, answer = self._answer(method, urlsplit(target), body)
        data = json.dumps(answer).encode()
        with contextlib.suppress(ConnectionError):
            writer.write(
                f"HTTP/1.1 {status} X\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode()
                + data
            )
            await writer.drain()
        writer.close()

    def _answer(self, method: str, url, body: bytes) -> tuple[int, dict]:
        if not self.answering:
            return 503, {"error": "busy"}
        path = unquote(url.path)
        if path.startswith("/claims/") and method == "PUT":
            if self.refuse is not None:
                status, error = self.refuse
                self.refuse = None
                return status, {"error": error}
            self.claims.append(json.loads(body))
            return 200, {
                "ok": True,
                "claimed": len(self.keys),
                "subscribed": {},
                "applied_live": True,
            }
        if path.startswith("/claims/") and method == "DELETE":
            self.drops += 1
            return 200, {"ok": True, "subscribed": {}, "applied_live": True}
        if path == "/snapshot" and method == "GET":
            keys = [k for k in ",".join(parse_qs(url.query).get("keys", [])).split(",") if k]
            self.snapshot_asks.append(keys)
            return 200, {"ticks": {key: self.snapshot.get(key) for key in keys}}
        return 404, {"error": "not found"}
