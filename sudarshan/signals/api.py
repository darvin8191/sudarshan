"""The TRINETRA view's sealed local API on 127.0.0.1:5588 (T5, T6; design 3.5).

    GET  /health      unsealed: the service's health document; never TRINETRA's text
    POST /v1/<op>     the body one Fernet token sealed for <op> under one of two purposes

The browser never calls this. Two purposes open here, each with its own key (derived from OpenAlgo's
pepper in ``sudarshan/vault/keys.py``), its own principal, its own size limit and its own ops
(``contract.PURPOSES``, ``PRINCIPALS``, ``SEAL_LIMITS``, ``PURPOSE_OPS``):

    api      principal ``owner``              16 KiB  the ops of OpenAlgo's bridge
    journal  principal ``sudarshan-journal``   4 KiB  state_at, state

Which purpose a seal belongs to is which key opens it. A seal lives 60 s, its nonce is accepted
once, and the op in the path must be the op inside it. A principal that is not the purpose's, or an
op not on its list, answers 403. The size limit of the path's op is checked from Content-Length
before a byte of the body is read. The nonces sit in a NonceBook that answers 503 when full.

The journal's transport (``sudarshan/journal/api.py``): stdlib asyncio, bound to 127.0.0.1 only,
one request per connection, a request with an Origin header refused, only the reading
time-limited, one listener for the service (R14). Every answer is ``{"status": "success", "data":
...}`` or ``{"status": "error", "code", "message", "field"?}`` with the message in the owner's
words. While the view is off or not set up, every op's data is the contract's ``Off`` record.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, NamedTuple, Protocol

from cryptography.fernet import Fernet

from sudarshan.autologin.handoff import (
    SEAL_TTL_S,
    HandoffRejected,
    NonceBook,
    NoncesFull,
    open_sealed,
)
from sudarshan.signals import contract as c

log = logging.getLogger("sudarshan.signals")

PURPOSE_ORDER = c.SERVED_PURPOSES
ALLOWED = {name: frozenset(ops) for name, ops in c.PURPOSE_OPS.items()}
#: The largest sealed request each op takes: the largest limit of the purposes that open it.
LIMITS = {
    name: max(c.SEAL_LIMITS[p] for p, opened in ALLOWED.items() if name in opened)
    for name in sorted(set().union(*ALLOWED.values()))
}
SEAL_FIELDS = frozenset({"op", "nonce", "principal", "args"})
#: The bridge lets through at most 150 requests a minute and the journal one at a trade's entry:
#: the book holds 2 x 60 s x 4 a second and answers 503 past it (cross-design X9).
PEAK_PER_S = 4
NONCES = 2 * SEAL_TTL_S * PEAK_PER_S
READ_TIMEOUT_S = 5.0
MAX_HEAD = 8 * 1024
LOCAL_PEERS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
REASONS = {
    200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed",
    408: "Request Timeout", 409: "Conflict", 411: "Length Required", 413: "Content Too Large",
    500: "Internal Server Error", 501: "Not Implemented", 503: "Service Unavailable",
}  # fmt: skip

Handler = Callable[[Any, dict[str, Any]], Awaitable[Any]]


class ApiError(Exception):
    """A refusal in the owner's words; ``body`` is the service's error envelope."""

    def __init__(self, status: int, code: str, message: str, field: str | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.field = status, code, message, field

    def body(self) -> dict[str, Any]:
        out = {"status": "error", "code": self.code, "message": self.message}
        if self.field:
            out["field"] = self.field
        return out


class Purpose(NamedTuple):
    name: str
    fernet: Fernet
    principal: str
    ops: frozenset[str]
    max_bytes: int


def purposes_from(keys: Mapping[str, Fernet]) -> list[Purpose]:
    """The purposes for the keys given, in order (a purpose without a key is not served)."""
    return [
        Purpose(name, keys[name], c.PRINCIPALS[name], ALLOWED[name], c.SEAL_LIMITS[name])
        for name in PURPOSE_ORDER
        if name in keys
    ]


class Desk(Protocol):
    """What the API serves: why the view answers as off (None when it is on), its health."""

    def off_message(self) -> str | None: ...

    def health(self) -> dict[str, Any]: ...


class SignalsApi:
    """The listener. ``start`` binds 127.0.0.1:<port> and raises OSError when it is taken: the
    caller says so in words and tries again later; it never tries another port."""

    def __init__(
        self,
        desk: Desk,
        keys: Mapping[str, Fernet],
        *,
        registry: Mapping[str, Handler] | None = None,
        nonces: NonceBook | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.desk, self.clock = desk, clock
        self.purposes = purposes_from(keys)
        self.registry: Mapping[str, Handler] = registry if registry is not None else {}
        self.nonces = nonces if nonces is not None else NonceBook(NONCES)
        self.server: asyncio.Server | None = None
        self._warned: dict[str, float] = {}

    async def start(self, port: int) -> None:
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", port)

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
                raise ApiError(403, "forbidden", c.WORDS["sealed"])
            try:
                method, path, length, headers = await asyncio.wait_for(
                    _read_head(reader), READ_TIMEOUT_S
                )
                self._check_head(method, path, length, headers)
                body = await asyncio.wait_for(
                    _read_body(reader, writer, length, headers), READ_TIMEOUT_S
                )
            except TimeoutError:
                raise ApiError(408, "timeout", c.WORDS["failed"]) from None
            return 200, await self._route(method, path, body)
        except ApiError as exc:
            return exc.status, exc.body()
        except Exception:
            log.exception("a request to the TRINETRA reader's API failed")
            return 500, ApiError(500, "failed", c.WORDS["failed"]).body()

    def _check_head(self, method: str, path: str, length: int | None, headers: dict) -> None:
        if "origin" in headers:  # a browser page; OpenAlgo's bridge sends none
            raise ApiError(403, "forbidden", "browser requests are not accepted")
        if method == "POST" and length is None:
            raise ApiError(411, "length", "Content-Length required")
        limit = LIMITS.get(_op_of(path) or "", c.SEAL_LIMITS["journal"])
        if length is not None and not 0 <= length <= limit:
            raise ApiError(413, "size", c.WORDS["too_big"])

    async def _route(self, method: str, path: str, body: bytes) -> Any:
        if path == c.HEALTH_PATH:
            if method != "GET":
                raise ApiError(405, "method", "method not allowed")
            return self.desk.health()
        op = _op_of(path)
        if op not in LIMITS:
            raise ApiError(404, "not_found", "not found")
        if method != "POST":
            raise ApiError(405, "method", "method not allowed")
        purpose, message = self._open(op, body)
        if message.get("principal") != purpose.principal:
            self._note(op, f"principal under {purpose.name}")
            raise ApiError(403, "principal", c.WORDS["forbidden"])
        if op not in purpose.ops:
            self._note(op, f"op under {purpose.name}")
            raise ApiError(403, "forbidden", c.WORDS["forbidden"])
        off = self.desk.off_message()
        if off is not None:
            return {"status": "success", "data": {"enabled": False, "message": off}}
        handler = self.registry.get(op)
        if handler is None:
            raise ApiError(501, "not_ready", c.WORDS["not_ready"])
        args = message.get("args")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise ApiError(400, "bad_request", c.WORDS["bad_request"])
        return {"status": "success", "data": await handler(self.desk, args)}

    def _open(self, op: str, body: bytes) -> tuple[Purpose, dict[str, Any]]:
        """The purpose whose key opens the seal, and the message. A seal no key opens is refused;
        any other reason (expiry, the wrong op, a replayed nonce) ends the search at once."""
        reason = "seal"
        for purpose in self.purposes:
            try:
                return purpose, open_sealed(
                    purpose.fernet, body, op=op, seen=self.nonces, now=self.clock(),
                    max_bytes=purpose.max_bytes,
                )  # fmt: skip
            except NoncesFull:
                log.warning("the TRINETRA reader's API is refusing requests: too many in two "
                            "minutes")  # fmt: skip
                raise ApiError(503, "busy", c.WORDS["busy"]) from None
            except HandoffRejected as exc:
                reason = str(exc)
                if reason not in ("seal", "size"):
                    break
        self._note(op, reason)
        raise ApiError(403, "forbidden", c.WORDS["sealed"])

    def _note(self, op: str, reason: str) -> None:
        """A refused request: at debug always, a warning at most once in 10 minutes."""
        log.debug("the TRINETRA reader's API refused a %s request (%s)", op, reason)
        now = time.monotonic()
        if now - self._warned.get("refused", -1e9) >= 600:
            self._warned["refused"] = now
            log.warning("the TRINETRA reader's API refused a %s request that was not sealed for "
                        "it (%s); more are logged at debug level for 10 minutes",
                        op, reason)  # fmt: skip


def _op_of(path: str) -> str | None:
    return path[len(c.OPS_PREFIX) :] if path.startswith(c.OPS_PREFIX) else None


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
