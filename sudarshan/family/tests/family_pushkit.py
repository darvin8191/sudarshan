"""Shared pieces for the push and outbox tests (F10, T2): the family's VAPID files, phones'
subscriptions, and a fake push service behind the real ``PushOnly`` client (in memory, or on a real
local socket for the descriptor tests).

Everything is synthetic: every key is made per run, every endpoint is
``https://fcm.googleapis.com/fcm/send/SYNTH-...`` and never reached: the fake answers in its place.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import os
from collections import deque
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from sudarshan.family import push as push_module
from sudarshan.family.push import make_http as push_http  # bound once: tests patch the module's
from sudarshan.push import Vapid

ENDPOINT = "https://fcm.googleapis.com/fcm/send/SYNTH-{n}"


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def write_vapid(etc: Path) -> Vapid:
    """The pair the provision step makes, in ``etc``; the Vapid it gives."""
    etc.mkdir(parents=True, exist_ok=True)
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                            serialization.NoEncryption())  # fmt: skip
    (etc / push_module.VAPID_PRIVATE).write_bytes(pem)
    vapid = Vapid(key)
    (etc / push_module.VAPID_PUBLIC).write_text(vapid.public_key + "\n", encoding="ascii")
    return vapid


def subscription(n: int = 1) -> dict[str, Any]:
    """What a phone's ``PushSubscription.toJSON()`` gives, with keys made for this run."""
    phone = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)  # fmt: skip
    return {"endpoint": ENDPOINT.format(n=n), "keys": {"p256dh": b64url(phone),
                                                       "auth": b64url(os.urandom(16))}}  # fmt: skip


class FakePushService:
    """Answers pushes: each endpoint's queued statuses first (then 201), and records every
    request's endpoint, headers and body length."""

    def __init__(self):
        self.answers: dict[str, deque] = {}
        self.seen: list[dict[str, Any]] = []
        self.retry_after: str | None = None

    def queue(self, endpoint: str, *statuses: int) -> None:
        self.answers.setdefault(endpoint, deque()).extend(statuses)

    def answer(self, endpoint: str, headers: dict[str, str], size: int) -> tuple[int, dict]:
        self.seen.append({"endpoint": endpoint, "headers": headers, "size": size})
        waiting = self.answers.get(endpoint)
        status = waiting.popleft() if waiting else 201
        extra = (
            {"Retry-After": self.retry_after} if self.retry_after and status in (429, 503) else {}
        )
        return status, extra

    async def handler(self, request: httpx.Request) -> httpx.Response:
        status, extra = self.answer(str(request.url), dict(request.headers), len(request.content))
        return httpx.Response(status, headers=extra)

    def http(self) -> httpx.AsyncClient:
        return push_http(httpx.MockTransport(self.handler))

    def headers(self) -> list[dict[str, str]]:
        return [s["headers"] for s in self.seen]


class ToLocalPush(httpx.AsyncBaseTransport):
    """After the push client's own allow-list passed a request, sends it to a local port through
    httpx's real transport (real sockets), keeping the endpoint in a header for the fake."""

    def __init__(self, port: int):
        self.port, self.inner = port, httpx.AsyncHTTPTransport(retries=0)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url.copy_with(scheme="http", host="127.0.0.1", port=self.port)
        headers = {**request.headers, "X-Endpoint": str(request.url)}
        moved = httpx.Request("POST", url, headers=headers, content=request.content,
                              extensions=request.extensions)  # fmt: skip
        return await self.inner.handle_async_request(moved)

    async def aclose(self) -> None:
        await self.inner.aclose()


class PushServer:
    """A ``FakePushService`` on a real socket of 127.0.0.1 (one request per connection)."""

    def __init__(self, fake: FakePushService):
        self.fake, self.server, self.port = fake, None, 0

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self.port

    def http(self) -> httpx.AsyncClient:
        return push_http(ToLocalPush(self.port))

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.server.wait_closed(), 2)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            headers = {k.strip().lower(): v.strip()
                       for k, _, v in (line.partition(":") for line in lines[1:] if ":" in line)}  # fmt: skip
            body = await reader.readexactly(int(headers.get("content-length", "0")))
            status, extra = self.fake.answer(headers.get("x-endpoint", ""), headers, len(body))
            more = "".join(f"{k}: {v}\r\n" for k, v in extra.items())
            writer.write(f"HTTP/1.1 {status} X\r\nContent-Length: 0\r\n{more}Connection: close\r\n\r\n"
                         .encode())  # fmt: skip
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
