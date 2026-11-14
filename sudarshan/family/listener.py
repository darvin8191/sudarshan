"""The family service's one listener on 127.0.0.1:5585 (R14; cross-design Y10): stdlib asyncio HTTP.

The pattern of ``sudarshan/feed/http_api.py``: bound to loopback only, one request per connection,
JSON in and out, only the reading time-limited. Every door shares it (``contract.py``): the viewer
API that nginx forwards, the phones' WebSocket (handed to ``ws.py`` and websockets' sans-I/O
protocol), the sealed admin and nudge doors, and /health. The rules that hold before any door is
asked, so that no door can forget one:

* a sealed door (``/v1/...``) or /health with X-Forwarded-For, X-Forwarded-Proto or X-Real-IP is
  answered 404: nginx sets them, and nothing it forwards may reach those doors (design 2.3);
* a sealed door or /health with an Origin header (a browser page) is refused;
* a body is read only after its length was checked against the door's cap (admin 64 KiB, nudge
  4 KiB, viewer 64 KiB; X9), and a chunked body is refused, so a request cannot be smuggled;
* the head is at most 16 KiB and must arrive within 10 s;
* a head it cannot read (a target urlsplit refuses, a length that is not ASCII digits) is answered
  400 in words, never a traceback and a 500;
* at most ``MAX_CONNECTIONS`` connections are open at a time (each holds a task and a socket until
  it ends, a WebSocket for hours): past them a new one is answered busy at once, before its head is
  read, and drained for a moment after the answer so that the close does not reset the connection
  before the answer is read (review H2).

A taken port raises OSError from ``start``; the service says so in words and tries again every
minute, never another port (X2).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, unquote, urlsplit

from .contract import (
    ADMIN_PREFIX,
    FORWARDED_HEADERS,
    HEALTH_PATH,
    LIMITS,
    NUDGE_PATH,
    VIEWER_PREFIX,
    WS_PATH,
)
from .errors import FamilyError

log = logging.getLogger("sudarshan.family")

HOST = "127.0.0.1"
LOCAL_PEERS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
MAX_HEAD = 16 * 1024
READ_TIMEOUT_S = 10.0
WRITE_TIMEOUT_S = 30.0
#: Open connections at most; a WebSocket counts as one (review H2).
MAX_CONNECTIONS = 200
#: How long a connection refused as busy is drained after its answer.
LINGER_S = 1.0
CAPS = {
    "admin": LIMITS["admin_bytes"],
    "nudge": LIMITS["nudge_bytes"],
    "viewer": LIMITS["viewer_bytes"],
    "health": 0,
    "ws": 0,
}
SEALED = {"admin", "nudge", "health"}
REASONS = {
    101: "Switching Protocols", 200: "OK", 400: "Bad Request", 401: "Unauthorized",
    403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed", 409: "Conflict", 410: "Gone",
    413: "Content Too Large", 422: "Unprocessable Content", 429: "Too Many Requests",
    500: "Internal Server Error", 501: "Not Implemented", 503: "Service Unavailable",
    504: "Gateway Timeout",
}  # fmt: skip
_FORWARDED = {name.lower() for name in FORWARDED_HEADERS}


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, str]
    headers: dict[str, str]
    door: str
    head: bytes
    body: bytes = b""
    peer: Any = None

    @property
    def upgrade(self) -> bool:
        return self.method == "GET" and self.headers.get("upgrade", "").lower() == "websocket"


@dataclass
class Response:
    status: int
    payload: Any
    headers: list[tuple[str, str]] = field(default_factory=list)

    @classmethod
    def ok(cls, data: Any, headers: list[tuple[str, str]] | None = None) -> Response:
        return cls(200, {"status": "success", "data": data}, headers or [])

    @classmethod
    def refused(cls, error: FamilyError, headers: list[tuple[str, str]] | None = None) -> Response:
        return cls(error.status, error.body(), headers or [])


def door_of(path: str) -> str:
    """Which door a path is for: admin, nudge, viewer, ws, health, or none."""
    if path == HEALTH_PATH:
        return "health"
    if path.startswith(ADMIN_PREFIX) or path == ADMIN_PREFIX.rstrip("/"):
        return "admin"
    if path == NUDGE_PATH:
        return "nudge"
    if path.startswith("/v1/") or path == "/v1":
        return "sealed-other"
    if path == WS_PATH:
        return "ws"
    if path == VIEWER_PREFIX or path.startswith(VIEWER_PREFIX + "/"):
        return "viewer"
    return "none"


class Router:
    """What answers the requests the listener has checked (the service)."""

    async def http(self, request: Request) -> Response:
        raise NotImplementedError

    async def upgrade(
        self, request: Request, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> Response | None:
        """Serve a WebSocket on the connection (None when it ran), or refuse with a Response."""
        raise NotImplementedError


class Listener:
    def __init__(
        self,
        router: Router,
        *,
        read_timeout: float = READ_TIMEOUT_S,
        max_connections: int = MAX_CONNECTIONS,
    ):
        self.router, self.read_timeout = router, read_timeout
        self.max_connections = max_connections
        self.server: asyncio.Server | None = None
        self._connections: set[asyncio.Task] = set()
        self._refusing: set[asyncio.Task] = set()  # answered busy, drained for a moment
        self.refused = 0

    async def start(self, port: int) -> None:
        """Bind 127.0.0.1:<port>; OSError when it is taken (never another port)."""
        self.server = await asyncio.start_server(self._serve, HOST, port, limit=MAX_HEAD)

    @property
    def port(self) -> int | None:
        return self.server.sockets[0].getsockname()[1] if self.server else None

    async def close(self, wait_s: float = 2.0) -> None:
        """Stop listening, and end the connections still open (sockets first get their close)."""
        if self.server is None:
            return
        self.server.close()
        live = [task for task in self._connections | self._refusing if not task.done()]
        if live:
            await asyncio.wait(live, timeout=wait_s)
        for task in live:
            task.cancel()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.server.wait_closed(), wait_s)
        self.server = None

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        if len(self._connections) >= self.max_connections:
            self.refused += 1
            if task is not None:
                self._refusing.add(task)
            try:
                await _refuse(reader, writer)
            finally:
                if task is not None:
                    self._refusing.discard(task)
            return
        if task is not None:
            self._connections.add(task)
        try:
            response = await self._answer(reader, writer)
            if response is not None:
                await write(writer, response)
        finally:
            if task is not None:
                self._connections.discard(task)
            await finish(writer)

    async def _answer(self, reader, writer) -> Response | None:
        try:
            peer = writer.get_extra_info("peername")
            if not peer or peer[0] not in LOCAL_PEERS:  # defence in depth: bound to loopback
                raise FamilyError("forbidden")
            try:
                request = await asyncio.wait_for(read_head(reader, peer), self.read_timeout)
            except TimeoutError:
                raise FamilyError("invalid") from None
            check(request)
            if request.door == "ws" and request.upgrade:
                return await self.router.upgrade(request, reader, writer)
            length = int(request.headers.get("content-length") or 0)
            if length:
                if request.headers.get("expect", "").lower() == "100-continue":
                    writer.write(b"HTTP/1.1 100 Continue\r\n\r\n")
                try:
                    request.body = await asyncio.wait_for(
                        reader.readexactly(length), self.read_timeout
                    )
                except (TimeoutError, asyncio.IncompleteReadError):
                    raise FamilyError("invalid") from None
            return await self.router.http(request)
        except FamilyError as exc:
            return Response.refused(exc)
        except Exception:
            log.exception("a request to the family service failed")
            return Response.refused(FamilyError("failed"))


async def read_head(reader: asyncio.StreamReader, peer: Any) -> Request:
    try:
        head = await reader.readuntil(b"\r\n\r\n")
    except (asyncio.LimitOverrunError, asyncio.IncompleteReadError, ValueError):
        raise FamilyError("invalid") from None
    try:
        request_line, *lines = head.decode("latin-1").split("\r\n")
        method, target, version = request_line.split(" ", 2)
        if not version.startswith("HTTP/1."):
            raise ValueError
        headers: dict[str, str] = {}
        for line in lines:
            name, sep, value = line.partition(":")
            if sep:
                key = name.strip().lower()
                if key in headers and key not in ("cookie",):
                    raise ValueError  # a header twice is how two readers come to disagree
                headers[key] = (
                    f"{headers[key]}; {value.strip()}" if key in headers else value.strip()
                )
        # A target such as //[x/ is an IPv6 host that does not close: urlsplit raises for it.
        parts = urlsplit(target)
        path = unquote(parts.path) if parts.path else "/"
        query = dict(parse_qsl(parts.query, keep_blank_values=True))
    except ValueError:
        raise FamilyError("invalid") from None
    return Request(method, path, query, headers, door_of(path), head, peer=peer)


def check(request: Request) -> None:
    """The rules every door shares (see the module's words)."""
    headers, door = request.headers, request.door
    if door in SEALED or door == "sealed-other":
        if _FORWARDED & headers.keys() or door == "sealed-other":
            raise FamilyError("not_found")
        if "origin" in headers:
            raise FamilyError("forbidden")
    if door == "none":
        raise FamilyError("not_found")
    if "transfer-encoding" in headers:
        raise FamilyError("invalid")
    raw = headers.get("content-length")
    if raw is None:
        if request.method in ("POST", "PUT", "PATCH"):
            raise FamilyError("invalid")
        return
    # ASCII digits only: "\xb2" (a superscript two) is a digit to isdigit() and int() refuses it.
    if not (raw.isascii() and raw.isdigit()):
        raise FamilyError("invalid")
    if int(raw) > CAPS.get(door, 0):
        raise FamilyError("too_large")


async def write(writer: asyncio.StreamWriter, response: Response) -> None:
    """Hand one answer to the client: JSON with its length, never cached, then the close."""
    if writer.is_closing():
        return
    data = (
        response.payload
        if isinstance(response.payload, bytes)
        else json.dumps(response.payload, separators=(",", ":"), ensure_ascii=False).encode()
    )
    fields = [
        ("Content-Type", "application/json; charset=utf-8"),
        ("Content-Length", str(len(data))),
        ("Cache-Control", "no-store"),
        ("X-Content-Type-Options", "nosniff"),
        ("Connection", "close"),
        *response.headers,
    ]
    head = f"HTTP/1.1 {response.status} {REASONS.get(response.status, 'Error')}\r\n"
    head += "".join(f"{name}: {value}\r\n" for name, value in fields) + "\r\n"
    try:
        writer.write(head.encode("latin-1") + data)
        await asyncio.wait_for(writer.drain(), WRITE_TIMEOUT_S)
    except (ConnectionError, TimeoutError, OSError):
        writer.transport.abort()


async def _refuse(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Busy, before the head is read; then the request is drained for a moment (a close with its
    bytes unread would reset the connection, and the answer would be lost with it)."""
    try:
        await write(writer, Response.refused(FamilyError("busy")))
        if not writer.is_closing() and writer.can_write_eof():
            writer.write_eof()
        with contextlib.suppress(Exception):
            async with asyncio.timeout(LINGER_S):
                while await reader.read(MAX_HEAD):
                    pass
    finally:
        await finish(writer)


async def finish(writer: asyncio.StreamWriter) -> None:
    """Close the connection on every path; one that will not close in time is dropped."""
    try:
        writer.close()
        await asyncio.wait_for(writer.wait_closed(), 2.0)
    except (ConnectionError, TimeoutError, OSError):
        writer.transport.abort()
    except asyncio.CancelledError:
        writer.transport.abort()
        raise
