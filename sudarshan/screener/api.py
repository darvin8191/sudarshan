"""The screener's sealed local API on 127.0.0.1:5584 (M9.1, design 6.3): OpenAlgo's bridge to it.

    GET  /health      unsealed: the ops health document and ``screener_state``; counts only
    POST /v1/<op>     the body one Fernet token sealed for <op> (sudarshan/screener/api/v1)

The browser never calls this. OpenAlgo's blueprint (blueprints/sudarshan_screener.py) checks the
owner's session, CSRF and that he is the owner, then forwards each request sealed under a key
derived from OpenAlgo's pepper (``sudarshan.vault.keys.SCREENER_API_INFO``), which only a process
that can read OpenAlgo's .env can make. A seal lives 60 s, its nonce is accepted once, the op in
the path must be the op inside it, and a seal under any other purpose (the internal routes, the
digests, the portfolio) never opens here. The nonces sit in a NonceBook that answers 503 when
full instead of forgetting one still valid (cross-design X9). A sealed request is at most 64 KiB,
checked from Content-Length before a byte of the body is read.

Styled on feed/http_api.py, alerts/market/api.py and portfolio/api.py: stdlib asyncio, bound to
127.0.0.1 only, one request per connection, a request with an Origin header refused (a browser
page), only the reading time-limited, one listener per port (cross-design R14: the sealed API and
the unsealed /health share it).

Every answer is the bridge's envelope, ``{"status": "success", "data": ...}`` or ``{"status":
"error", "error": {"code", "message", "field"}}`` with the message in the owner's words. Switched
off, every op answers ``{"enabled": false, "message": ...}`` and does nothing. This service
serves the owner only (principal ``owner``); a family read-only layer later adds principals and
read-only ops (6.5).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from cryptography.fernet import Fernet

from sudarshan.autologin.handoff import (
    SEAL_TTL_S,
    HandoffRejected,
    NonceBook,
    NoncesFull,
    open_sealed,
)

from . import ops as ops_module
from .contract import API_MAX_BYTES, LOCAL_HOST, OP_NAMES
from .contract import WORDS as SHARED_WORDS
from .ops import ApiError

log = logging.getLogger("sudarshan.screener")

OWNER = "owner"
HEALTH_PATH = "/health"
OPS_PREFIX = "/v1/"
#: The bridge lets through about 190 requests a minute (120 reads, 60 writes, 6 previews); the
#: book holds 2 x 60 s x 8 a second, X9's rule for a route's peak, and answers 503 past it.
PEAK_PER_S = 8
NONCES = 2 * SEAL_TTL_S * PEAK_PER_S
READ_TIMEOUT_S = 5.0
MAX_HEAD = 8 * 1024
LOCAL_PEERS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
REASONS = {
    200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed",
    408: "Request Timeout", 409: "Conflict", 411: "Length Required", 413: "Content Too Large",
    422: "Unprocessable Content", 500: "Internal Server Error", 501: "Not Implemented",
    503: "Service Unavailable",
}  # fmt: skip
WORDS = {
    "busy": "The screener is busy. Wait a minute, then try again.",
    "forbidden": "The screener could not confirm that this request came from OpenAlgo. If "
    "OpenAlgo's .env was changed, restart sudarshan-screener.",
    "principal": "This screener serves the account owner only.",
    "failed": "The screener could not do this just now. Try again in a minute.",
    "off": SHARED_WORDS["off"],
    "not_ready": SHARED_WORDS["not_ready"],
    "size": SHARED_WORDS["too_big"],
}


class Desk(Protocol):
    """What the API serves: whether the screener is on, its health document, and the context
    the ops are called with (the service's settings and its open store)."""

    enabled: bool

    def health(self) -> dict[str, Any]: ...


def off_data(reason: str | None = None) -> dict[str, Any]:
    """What every op answers while the screener is off: the switch, or ``reason`` (a file a
    newer release wrote, a store that will not open) in the owner's words."""
    return {"enabled": False, "message": reason or WORDS["off"]}


class ScreenerApi:
    """The listener. ``start`` binds 127.0.0.1:<port> and raises OSError when it is taken: the
    caller says so in words and tries again later; it never tries another port (X2)."""

    def __init__(
        self,
        desk: Desk,
        fernet: Fernet,
        *,
        registry: Mapping[str, ops_module.Handler] | None = None,
        nonces: NonceBook | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.desk, self.fernet, self.clock = desk, fernet, clock
        self.registry = ops_module.REGISTRY if registry is None else registry
        self.nonces = nonces if nonces is not None else NonceBook(NONCES)
        self.server: asyncio.Server | None = None
        self._warned: dict[str, float] = {}

    async def start(self, port: int) -> None:
        self.server = await asyncio.start_server(self._serve, LOCAL_HOST, port)

    @property
    def port(self) -> int | None:
        return self.server.sockets[0].getsockname()[1] if self.server else None

    async def close(self, wait_s: float = 2.0) -> None:
        if self.server is None:
            return
        self.server.close()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.server.wait_closed(), wait_s)
        self.server = None

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """One request, one answer, and the connection closed on every path."""
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
            try:
                method, path, length, headers = await asyncio.wait_for(
                    _read_head(reader), READ_TIMEOUT_S
                )
                self._check_head(method, length, headers)
                body = await asyncio.wait_for(
                    _read_body(reader, writer, length, headers), READ_TIMEOUT_S
                )
            except TimeoutError:  # only the read is a slow client; one raised by an op is a 500
                raise ApiError(408, "timeout", WORDS["failed"]) from None
            return 200, await self._route(method, path, body)
        except ApiError as exc:
            return exc.status, exc.body()
        except Exception:
            log.exception("a request to the screener's API failed")
            return 500, ApiError(500, "failed", WORDS["failed"]).body()

    @staticmethod
    def _check_head(method: str, length: int | None, headers: dict) -> None:
        """Refuse on the head alone: a browser page, no length, or more than the limit."""
        if "origin" in headers:  # a browser page; OpenAlgo's bridge sends none
            raise ApiError(403, "forbidden", "browser requests are not accepted")
        if method == "POST" and length is None:
            raise ApiError(411, "length", "Content-Length required")
        if length is not None and not 0 <= length <= API_MAX_BYTES:
            raise ApiError(413, "size", WORDS["size"])

    @staticmethod
    def _op_of(path: str) -> str | None:
        return path[len(OPS_PREFIX) :] if path.startswith(OPS_PREFIX) else None

    async def _route(self, method: str, path: str, body: bytes) -> Any:
        if path == HEALTH_PATH:
            if method != "GET":
                raise ApiError(405, "method", "method not allowed")
            return self.desk.health()
        op = self._op_of(path)
        if op not in OP_NAMES:
            raise ApiError(404, "not_found", "not found")
        if method != "POST":
            raise ApiError(405, "method", "method not allowed")
        message = self._open(op, body)
        if message.get("principal") != OWNER:
            self._note(op, "principal")
            raise ApiError(403, "principal", WORDS["principal"])
        if not self.desk.enabled:
            return {"status": "success", "data": off_data(getattr(self.desk, "off_reason", None))}
        handler = self.registry.get(op)
        if handler is None:
            raise ApiError(501, "not_ready", WORDS["not_ready"])
        args = message.get("args")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise ApiError(400, "bad_request", WORDS["failed"])
        return {"status": "success", "data": await handler(self.desk, args)}

    def _open(self, op: str, body: bytes) -> dict[str, Any]:
        try:
            return open_sealed(
                self.fernet,
                body,
                op=op,
                seen=self.nonces,
                now=self.clock(),
                max_bytes=API_MAX_BYTES,
            )
        except NoncesFull:
            log.warning("the screener's API is refusing requests: too many in the last two minutes")
            raise ApiError(503, "busy", WORDS["busy"]) from None
        except HandoffRejected as exc:
            self._note(op, str(exc))
            raise ApiError(403, "forbidden", WORDS["forbidden"]) from None

    def _note(self, op: str, reason: str) -> None:
        """A refused request: at debug always, a warning at most once in 10 minutes."""
        log.debug("the screener's API refused a %s request (%s)", op, reason)
        now = time.monotonic()
        if now - self._warned.get("refused", -1e9) >= 600:
            self._warned["refused"] = now
            log.warning(
                "the screener's API refused a %s request that was not sealed by this server's "
                "OpenAlgo (%s); more are logged at debug level for 10 minutes", op, reason,
            )  # fmt: skip


async def _read_head(reader: asyncio.StreamReader) -> tuple[str, str, int | None, dict[str, str]]:
    try:
        head = await reader.readuntil(b"\r\n\r\n")
    except (asyncio.LimitOverrunError, asyncio.IncompleteReadError):
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
    return method, target.partition("?")[0].rstrip("/") or "/", length, headers


async def _read_body(reader, writer, length: int | None, headers: dict) -> bytes:
    if not length:
        return b""
    if headers.get("expect", "").lower() == "100-continue":
        writer.write(b"HTTP/1.1 100 Continue\r\n\r\n")
    try:
        return await reader.readexactly(length)
    except asyncio.IncompleteReadError:
        raise ApiError(400, "malformed", "body shorter than Content-Length") from None
