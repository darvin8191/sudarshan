"""The journal's sealed local API on 127.0.0.1:5586 (JB2, design 7.2 as amended by Y8, Y16, Y17).

    GET  /health      unsealed: the service's health document and ``journal_state``; no owner data
    POST /v1/<op>     the body one Fernet token sealed for <op> under one of four purposes

The browser never calls this. Four purposes open here, each with its own key (derived from
OpenAlgo's pepper in ``sudarshan/vault/keys.py``), its own principal, its own size limit and its
own list of ops:

    api       principal ``owner``             64 KiB  the ops of OpenAlgo's bridge (kinds.OPS)
    delivery  principal ``sudarshan-alerts``  16 KiB  report_get, report_file, delivery (Y8)
    nudge     principal ``sudarshan-alerts``   4 KiB  nudge (Y17): kind, sandbox and at
    family    principal ``sudarshan-family``   4 KiB  family_day (Y16): counts only

Which purpose a seal belongs to is which key opens it. A seal lives 60 s, its nonce is accepted
once, and the op in the path must be the op inside it. A principal that is not the purpose's, or
an op that is not on its list (``delivery`` under the bridge's key, say), answers 403 and is
logged once a minute. The size limit of the path's op (the largest of the purposes that open it)
is checked from Content-Length before a byte of the body is read; each purpose's own limit when
its key is tried. The nonces sit in a NonceBook that answers 503 when full instead of forgetting
one still valid (cross-design X9).

Styled on ``feed/http_api.py`` and the portfolio's ``api.py``: stdlib asyncio, bound to 127.0.0.1
only, one request per connection, a request with an Origin header refused (a browser page), only
the reading time-limited (an op that has started finishes), one listener for the service
(cross-design R14).

Every answer is the bridge's envelope, ``{"status": "success", "data": ...}`` or ``{"status":
"error", "code": ..., "message": ..., "field": ...}`` with the message in the owner's words.
Switched off, every op answers ``{"enabled": false, "message": ...}`` and does nothing. An op
nobody has built yet answers "This part of the journal is not ready yet."
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Callable, Mapping
from typing import Any, NamedTuple, Protocol

from cryptography.fernet import Fernet

from sudarshan.autologin.handoff import (
    SEAL_TTL_S,
    HandoffRejected,
    NonceBook,
    NoncesFull,
    open_sealed,
)

from . import ops as ops_module
from .core import kinds
from .errors import ApiError
from .ops import common as ops_common

log = logging.getLogger("sudarshan.journal")

HEALTH_PATH = "/health"
OPS_PREFIX = "/v1/"
#: The order the purposes' keys are tried in.
PURPOSE_ORDER = ("api", "delivery", "nudge", "family")
#: The ops each purpose opens.
ALLOWED = {
    "api": frozenset(op.name for op in kinds.OPS),
    "delivery": frozenset(kinds.DELIVERY_OPS),
    "nudge": frozenset(kinds.NUDGE_OPS),
    "family": frozenset(kinds.FAMILY_OPS),
}
#: The largest sealed request each op takes: the largest limit of the purposes that open it.
LIMITS = {
    name: max(kinds.SEAL_LIMITS[p] for p, opened in ALLOWED.items() if name in opened)
    for name in sorted(set().union(*ALLOWED.values()))
}
#: The fields of a seal that are the seal's own, never an op's argument.
SEAL_FIELDS = frozenset({"op", "nonce", "principal", "args"})
#: The bridge lets through about 200 requests a minute and the nudges add one a second: the book
#: holds 2 x 60 s x 8 a second, X9's rule for a route's peak, and answers 503 past it.
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
#: The owner's words for what the transport refuses. The bridge shows its own for most of these.
WORDS = {
    "busy": "The journal service is busy. Wait a minute, then try again.",
    "forbidden": "The journal service could not confirm that this request came from OpenAlgo. "
    "If OpenAlgo's .env was changed, restart sudarshan-journal.",
    "principal": "This journal service serves the account owner only.",
    "failed": "The journal could not do that just now. Your trades are safe; try again in a minute.",
    "off": "The journal is switched off on the server.",
    "not_ready": "This part of the journal is not ready yet.",
    "size": "That is too large for the journal. Send less at a time.",
    "op": "This request is not one the journal answers for that caller.",
}


class Purpose(NamedTuple):
    """One purpose that opens here: its key, its principal, its ops and its size limit."""

    name: str
    fernet: Fernet
    principal: str
    ops: frozenset[str]
    max_bytes: int


def purposes_from(keys: Mapping[str, Fernet]) -> list[Purpose]:
    """The purposes for the keys given, in ``PURPOSE_ORDER`` (a purpose without a key is not
    served: its seals answer 403)."""
    return [
        Purpose(name, keys[name], kinds.PRINCIPALS[name], ALLOWED[name], kinds.SEAL_LIMITS[name])
        for name in PURPOSE_ORDER
        if name in keys
    ]


class Desk(Protocol):
    """What the API serves: whether the journal is on, why not, and its health document."""

    enabled: bool
    off_reason: str | None

    def health(self) -> dict[str, Any]: ...


def off_data(reason: str | None = None) -> dict[str, Any]:
    """What every op answers while the journal is off: the switch, or ``reason`` (a file a newer
    release wrote, a store that will not open) in the owner's words."""
    return {"enabled": False, "message": reason or WORDS["off"]}


class JournalApi:
    """The listener. ``start`` binds 127.0.0.1:<port> and raises OSError when it is taken: the
    caller says so in words and tries again later; it never tries another port (cross-design X2)."""

    def __init__(
        self,
        desk: Desk,
        keys: Mapping[str, Fernet],
        *,
        registry: Mapping[str, ops_module.Handler] | None = None,
        nonces: NonceBook | None = None,
        clock: Callable[[], float] = time.time,
    ):
        self.desk, self.clock = desk, clock
        self.purposes = purposes_from(keys)
        self.registry = ops_module.REGISTRY if registry is None else registry
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
                raise ApiError(403, "forbidden", WORDS["forbidden"])
            try:
                method, path, length, headers = await asyncio.wait_for(
                    _read_head(reader), READ_TIMEOUT_S
                )
                self._check_head(method, path, length, headers)
                body = await asyncio.wait_for(
                    _read_body(reader, writer, length, headers), READ_TIMEOUT_S
                )
            except TimeoutError:  # only the read is a slow client; one raised by an op is a 500
                raise ApiError(408, "timeout", WORDS["failed"]) from None
            return 200, await self._route(method, path, body)
        except ApiError as exc:
            return exc.status, exc.body()
        except Exception:
            log.exception("a request to the journal's API failed")
            return 500, ApiError(500, "failed", WORDS["failed"]).body()

    def _check_head(self, method: str, path: str, length: int | None, headers: dict) -> None:
        """Refuse on the head alone: a browser page, no length, or more than the op's limit."""
        if "origin" in headers:  # a browser page; OpenAlgo's bridge sends none
            raise ApiError(403, "forbidden", "browser requests are not accepted")
        if method == "POST" and length is None:
            raise ApiError(411, "length", "Content-Length required")
        limit = LIMITS.get(self._op_of(path) or "", kinds.SEAL_LIMITS["api"])
        if length is not None and not 0 <= length <= limit:
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
        if op not in LIMITS:
            raise ApiError(404, "not_found", "not found")
        if method != "POST":
            raise ApiError(405, "method", "method not allowed")
        purpose, message = self._open(op, body)
        principal = message.get("principal")
        if principal != purpose.principal:
            self._note(op, f"principal under {purpose.name}")
            raise ApiError(403, "principal", WORDS["principal"])
        if op not in purpose.ops:
            self._refuse(purpose.name, op)
            raise ApiError(403, "forbidden", WORDS["op"])
        if not self.desk.enabled:
            return {"status": "success", "data": off_data(getattr(self.desk, "off_reason", None))}
        handler = self.registry.get(op)
        if handler is None:
            raise ApiError(501, "not_ready", WORDS["not_ready"])
        args = self._args(purpose, message)
        token = ops_common.CALLER.set(ops_common.Caller(purpose.name, principal))
        try:
            data = await handler(self.desk, args)
        finally:
            ops_common.CALLER.reset(token)
        return {"status": "success", "data": data}

    @staticmethod
    def _args(purpose: Purpose, message: Mapping[str, Any]) -> dict[str, Any]:
        """The op's arguments: what the caller put under "args", or, for a nudge, the fields the
        alerts' sender writes into the seal itself (kind, sandbox, at)."""
        if purpose.name == "nudge":
            return {k: v for k, v in message.items() if k not in SEAL_FIELDS}
        args = message.get("args")
        if args is None:
            return {}
        if not isinstance(args, dict):
            raise ApiError(400, "bad_request", ops_common.ASK)
        return args

    def _refuse(self, purpose: str, op: str) -> None:
        """An op that is not on the purpose's list: a security event, said once a minute per op
        and purpose (a flood of them is one line)."""
        log.debug("the journal's API refused %s under the %s purpose", op, purpose)
        key = f"{purpose}:{op}"
        now = time.monotonic()
        if now - self._warned.get(key, -1e9) >= 60:
            self._warned[key] = now
            log.warning("the journal's API refused the op %s under the %s purpose", op, purpose)

    def _open(self, op: str, body: bytes) -> tuple[Purpose, dict[str, Any]]:
        """The purpose whose key opens the seal, and the message. A seal no key opens is refused
        as one that was not sealed by this server's OpenAlgo; any other reason (size, expiry, the
        wrong op, a replayed nonce) ends the search at once."""
        reason = "seal"
        for purpose in self.purposes:
            try:
                return purpose, open_sealed(
                    purpose.fernet, body, op=op, seen=self.nonces, now=self.clock(),
                    max_bytes=purpose.max_bytes,
                )  # fmt: skip
            except NoncesFull:
                log.warning("the journal's API is refusing requests: too many in two minutes")
                raise ApiError(503, "busy", WORDS["busy"]) from None
            except HandoffRejected as exc:
                reason = str(exc)
                if reason not in ("seal", "size"):
                    break
        self._note(op, reason)
        raise ApiError(403, "forbidden", WORDS["forbidden"])

    def _note(self, op: str, reason: str) -> None:
        """A refused request: at debug always, a warning at most once in 10 minutes."""
        log.debug("the journal's API refused a %s request (%s)", op, reason)
        now = time.monotonic()
        if now - self._warned.get("refused", -1e9) >= 600:
            self._warned["refused"] = now
            log.warning(
                "the journal's API refused a %s request that was not sealed by this server "
                "(%s); more are logged at debug level for 10 minutes", op, reason,
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
