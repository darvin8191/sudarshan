"""The market alerts' sealed local API on 127.0.0.1:5582 (M10.1): OpenAlgo's bridge to them.

    GET  /health      unsealed: whether market alerts are on, their state, and counts only
    POST /v1/<op>     the body one Fernet token sealed for <op> (sudarshan/alerts/api/v1)

The browser never calls this. OpenAlgo's blueprint (blueprints/sudarshan_alerts.py) checks the
owner's session, CSRF and that he is the owner, then forwards each request sealed under a key
derived from OpenAlgo's pepper (sudarshan.vault.keys.ALERTS_API_INFO), which only a process that
can read OpenAlgo's .env can make. A seal lives 60 s, its nonce is accepted once, and it is at most
64 KiB; the op in the path must be the op inside it. The nonces are kept in a NonceBook that
answers 503 when full instead of forgetting one still valid (cross-design X9). A seal under any
other purpose (the screener's digests have their own) never opens here.

stdlib asyncio, as feed/http_api.py: bound to 127.0.0.1 only, one request per connection, a
request with an Origin header refused (a browser page: CSRF or DNS rebinding), and only the
reading time-limited: an op that has started (a claim at the feed, a test waiting for Telegram)
finishes.

Every answer is the bridge's envelope, ``{"status": "success", "data": ...}`` or ``{"status":
"error", "code": ..., "message": ..., "field": ...}``, the message in the owner's words, so the
bridge passes it on as it is. Every op names its principal; this service serves the owner only
(design 4.2), and any other principal is refused.

What answers the ops is a desk: ``desk.OwnerDesk`` over the running market alerts, or
``OffDesk`` while they are switched off, so the bridge learns that from ``/health`` (cross-design
R3) and the page says so.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Mapping
from typing import Any, Protocol

from cryptography.fernet import Fernet

from sudarshan.autologin.handoff import (
    SEAL_TTL_S,
    HandoffRejected,
    NonceBook,
    NoncesFull,
    open_sealed,
)

log = logging.getLogger("sudarshan.alerts.market")

SERVICE = "sudarshan-alerts"
OWNER = "owner"
HEALTH_PATH = "/health"
OPS_PREFIX = "/v1/"
OPS = frozenset(
    {"state", "create", "update", "delete", "events", "events_since", "prefs_get", "prefs_set",
     "test"}
)  # fmt: skip
#: The largest sealed request this route takes (cross-design X9).
MAX_SEALED = 64 * 1024
#: The bridge's limits let through at most 156 requests a minute (120 reads, 30 changes, 6
#: tests), a few hundred nonces in any two minutes. The book holds 2 x 60 s x 24 a second, as X9
#: asks of a route's peak, and answers 503 past it.
PEAK_PER_S = 24
NONCES = 2 * SEAL_TTL_S * PEAK_PER_S
READ_TIMEOUT_S = 5.0
MAX_HEAD = 8 * 1024
LOCAL_PEERS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
REASONS = {
    200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed",
    408: "Request Timeout", 409: "Conflict", 411: "Length Required", 413: "Content Too Large",
    500: "Internal Server Error", 503: "Service Unavailable",
}  # fmt: skip
#: The owner's words for what the transport refuses. The bridge shows its own for most of these;
#: they matter when a request is refused for a reason the owner can act on.
WORDS = {
    "busy": "The alert service is busy. Wait a minute, then try again.",
    "forbidden": "The alert service could not confirm that this request came from OpenAlgo. If "
    "OpenAlgo's .env was changed, restart sudarshan-alerts.",
    "principal": "This alert service serves the account owner only.",
    "failed": "The alert service could not do this just now. Try again in a minute.",
    "off": "Market alerts are switched off on the server.",
}


class ApiError(Exception):
    """An answer other than success: HTTP status, a code the page acts on, the owner's words."""

    def __init__(self, status: int, code: str, message: str, field: str | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.field = status, code, message, field

    def body(self) -> dict[str, Any]:
        body = {"status": "error", "code": self.code, "message": self.message}
        if self.field is not None:
            body["field"] = self.field
        return body


class Desk(Protocol):
    """What answers the API: counts for /health, and each op's data (or an ApiError)."""

    def health(self) -> dict[str, Any]: ...

    async def call(self, op: str, message: Mapping[str, Any]) -> Any: ...


class OffDesk:
    """Market alerts are switched off on the server (SUDARSHAN_MARKET_ALERTS is not on)."""

    def health(self) -> dict[str, Any]:
        return {"service": SERVICE, "market_alerts": "off", "state": "off", "alerts_active": 0,
                "instruments": 0}  # fmt: skip

    async def call(self, op: str, message: Mapping[str, Any]) -> Any:
        if op == "state":
            return off_state(int(time.time() * 1000))
        raise ApiError(409, "off", WORDS["off"])


def off_state(now: int) -> dict[str, Any]:
    """The page's state while market alerts are switched off: the line, and nothing else."""
    return {
        "enabled": False,
        "engine": {"code": "off", "line": "Switched off on the server", "real_time": False},
        "segments": {},
        "limits": {"instruments": 0, "instruments_used": 0, "active": 0, "active_used": 0},
        "prefs": {"telegram": True, "app": True},
        "alerts": [],
        "last_event_id": 0,
        "server_time": now,
    }


class MarketApi:
    """The listener. ``start`` binds 127.0.0.1:<port> and raises OSError when it is taken: the
    caller says so and keeps market alerts off; it never tries another port (cross-design X2)."""

    def __init__(
        self,
        desk: Desk,
        fernet: Fernet,
        *,
        nonces: NonceBook | None = None,
        max_bytes: int = MAX_SEALED,
    ):
        self.desk, self.fernet, self.max_bytes = desk, fernet, max_bytes
        self.nonces = nonces if nonces is not None else NonceBook(NONCES)
        self.server: asyncio.Server | None = None
        self._warned: dict[str, float] = {}

    async def start(self, port: int) -> None:
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", port)

    async def close(self, wait_s: float = 2.0) -> None:
        if self.server is None:
            return
        self.server.close()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.server.wait_closed(), wait_s)
        self.server = None

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """One request, one answer, and the connection closed on every path (a cancelled
        request's too, when the service stops)."""
        try:
            status, payload = await self._answer(reader, writer)
            data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
            head = (
                f"HTTP/1.1 {status} {REASONS.get(status, 'Error')}\r\n"
                f"Content-Type: application/json; charset=utf-8\r\nContent-Length: {len(data)}"
                "\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n"
            )
            with contextlib.suppress(ConnectionError):
                writer.write(head.encode() + data)
                await writer.drain()
        finally:
            writer.close()
        with contextlib.suppress(ConnectionError):
            await writer.wait_closed()

    async def _answer(self, reader, writer) -> tuple[int, Any]:
        try:
            peer = writer.get_extra_info("peername")
            if not peer or peer[0] not in LOCAL_PEERS:  # defence in depth: bound to loopback
                raise ApiError(403, "forbidden", WORDS["forbidden"])
            method, path, body = await asyncio.wait_for(
                _read(reader, writer, self.max_bytes), READ_TIMEOUT_S
            )
            return 200, await self._route(method, path, body)
        except ApiError as exc:
            return exc.status, exc.body()
        except TimeoutError:
            return 408, ApiError(408, "timeout", WORDS["failed"]).body()
        except Exception:
            log.exception("a request to the market alerts' API failed")
            return 500, ApiError(500, "failed", WORDS["failed"]).body()

    async def _route(self, method: str, path: str, body: bytes) -> Any:
        if path == HEALTH_PATH:
            if method != "GET":
                raise ApiError(405, "method", "method not allowed")
            return self.desk.health()
        op = path[len(OPS_PREFIX) :] if path.startswith(OPS_PREFIX) else None
        if op not in OPS:
            raise ApiError(404, "not_found", "not found")
        if method != "POST":
            raise ApiError(405, "method", "method not allowed")
        message = self._open(op, body)
        if message.get("principal") != OWNER:
            self._note(op, "principal")
            raise ApiError(403, "principal", WORDS["principal"])
        return {"status": "success", "data": await self.desk.call(op, message)}

    def _open(self, op: str, body: bytes) -> dict[str, Any]:
        try:
            return open_sealed(self.fernet, body, op=op, seen=self.nonces,
                               max_bytes=self.max_bytes)  # fmt: skip
        except NoncesFull:
            log.warning("the market alerts' API is refusing requests: too many in the last two "
                        "minutes")  # fmt: skip
            raise ApiError(503, "busy", WORDS["busy"]) from None
        except HandoffRejected as exc:
            self._note(op, str(exc))
            raise ApiError(403, "forbidden", WORDS["forbidden"]) from None

    def _note(self, op: str, reason: str) -> None:
        """A refused request: at debug always, a warning at most once in 10 minutes."""
        log.debug("the market alerts' API refused a %s request (%s)", op, reason)
        now = time.monotonic()
        if now - self._warned.get("refused", -1e9) >= 600:
            self._warned["refused"] = now
            log.warning("the market alerts' API refused a %s request that was not sealed by this "
                        "server's OpenAlgo (%s); more are logged at debug level for 10 minutes",
                        op, reason)  # fmt: skip


async def _read(reader: asyncio.StreamReader, writer, max_bytes: int) -> tuple[str, str, bytes]:
    try:
        head = await reader.readuntil(b"\r\n\r\n")
    except asyncio.LimitOverrunError:
        raise ApiError(400, "malformed", "malformed HTTP request") from None
    except asyncio.IncompleteReadError:
        raise ApiError(400, "malformed", "malformed HTTP request") from None
    if len(head) > MAX_HEAD:
        raise ApiError(400, "malformed", "HTTP head too large")
    try:
        request_line, *lines = head.decode("latin-1").split("\r\n")
        method, target, _ = request_line.split(" ", 2)
        headers = {}
        for line in lines:
            name, sep, value = line.partition(":")
            if sep:
                headers[name.strip().lower()] = value.strip()
        length = headers.get("content-length")
        length = int(length) if length is not None else None
    except ValueError:
        raise ApiError(400, "malformed", "malformed HTTP request") from None
    if "origin" in headers:  # a browser page; OpenAlgo's bridge sends none
        raise ApiError(403, "forbidden", "browser requests are not accepted")
    if method == "POST" and length is None:
        raise ApiError(411, "length", "Content-Length required")
    length = length or 0
    if not 0 <= length <= max_bytes:
        raise ApiError(413, "size", "request too large")
    if length and headers.get("expect", "").lower() == "100-continue":
        writer.write(b"HTTP/1.1 100 Continue\r\n\r\n")
    try:
        body = await reader.readexactly(length) if length else b""
    except asyncio.IncompleteReadError:
        raise ApiError(400, "malformed", "body shorter than Content-Length") from None
    return method, target.partition("?")[0].rstrip("/") or "/", body
