"""The portfolio's sealed local API on 127.0.0.1:5583 (M7.18, design 7.2): OpenAlgo's bridge to it.

    GET  /health      unsealed: the service's health document and ``portfolio_state``; no owner data
    POST /v1/<op>     the body one Fernet token sealed for <op> (sudarshan/portfolio/api/v1)

The browser never calls this. OpenAlgo's blueprint (blueprints/sudarshan_portfolio.py) checks the
owner's session, CSRF and that he is the owner, then forwards each request sealed under a key
derived from OpenAlgo's pepper (``sudarshan.vault.keys.PORTFOLIO_API_INFO``), which only a
process that can read OpenAlgo's .env can make. A seal lives 60 s, its nonce is accepted once,
the op in the path must be the op inside it, and a seal under a purpose this service does not
serve never opens here.

Three purposes open here, each with its own key, principal and list of ops (cross-design Y4, Y6):

    owner    principal ``owner``                  the portfolio's own ops (the owner's bridge)
    family   principal ``viewer:<member id>``     read ops only; the scope (the member's shared
                                                  tabs and the mask) is built here from the stored
                                                  shares and privacy, never taken from the caller
    journal  principal ``sudarshan-journal``      account reads only

Which purpose a seal belongs to is which key opens it. A principal that is not the purpose's, or an
op that is not on its list (every write op under family or journal), answers 403 and is logged.
The answer of a non-owner purpose passes the scope's mask once more on its way out. The nonces sit in a NonceBook that answers 503 when full instead of forgetting one still
valid (cross-design X9). Sizes: a sealed request is at most 64 KiB, an import's at most
``kinds.IMPORT_MAX_BYTES`` (the file as text seals to 1.5 to 1.9 MiB, see kinds.py), and the limit
is checked from Content-Length before a byte of the body is read.

Styled on feed/http_api.py and alerts/market/api.py: stdlib asyncio, bound to 127.0.0.1 only, one
request per connection, a request with an Origin header refused (a browser page), only the
reading time-limited (an op that has started finishes), one listener per port (cross-design R14).

Every answer is the bridge's envelope, ``{"status": "success", "data": ...}`` or ``{"status":
"error", "code": ..., "message": ..., "field": ...}`` with the message in the owner's words.
Switched off, every op answers ``{"enabled": false, ...}`` and does nothing. An op nobody has
built yet answers "This part of the portfolio is not ready yet." The owner's purpose serves
principal ``owner``; the family and journal purposes are above.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
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
from . import serialize, sharing
from .core import kinds
from .errors import ApiError
from .ops import common as ops_common

log = logging.getLogger("sudarshan.portfolio")

OWNER = "owner"
HEALTH_PATH = "/health"
OPS_PREFIX = "/v1/"
#: The largest sealed request an op takes: 64 KiB, an upload's (``import_preview``) the import limit.
MAX_SEALED = kinds.API_MAX_BYTES
LIMITS = dict.fromkeys(kinds.SERVICE_OP_NAMES, kinds.API_MAX_BYTES) | {
    spec.name: kinds.IMPORT_MAX_BYTES if spec.upload else kinds.API_MAX_BYTES for spec in kinds.OPS
}
OWNER_PURPOSE, FAMILY, JOURNAL = "owner", "family", "journal"
#: The ops each purpose opens (the family and journal lists are all reads).
ALLOWED = {
    OWNER_PURPOSE: frozenset((*kinds.OP_NAMES, *kinds.OWNER_EXTRA_OPS)),
    FAMILY: frozenset(kinds.FAMILY_OPS),
    JOURNAL: frozenset(kinds.JOURNAL_OPS),
}
FAMILY_PRINCIPAL = re.compile(re.escape(kinds.FAMILY_PRINCIPAL_PREFIX) + r"[A-Za-z0-9_-]{1,40}")
#: The bridge lets through about 190 requests a minute (120 reads, 60 writes, 10 imports); the
#: book holds 2 x 60 s x 8 a second, X9's rule for a route's peak, and answers 503 past it.
PEAK_PER_S = 8
NONCES = 2 * SEAL_TTL_S * PEAK_PER_S
READ_TIMEOUT_S = 5.0
#: An import's body is megabytes: a slow client gets longer, but not for ever.
UPLOAD_READ_TIMEOUT_S = 20.0
MAX_HEAD = 8 * 1024
LOCAL_PEERS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
REASONS = {
    200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed",
    408: "Request Timeout", 409: "Conflict", 411: "Length Required", 413: "Content Too Large",
    500: "Internal Server Error", 501: "Not Implemented", 503: "Service Unavailable",
}  # fmt: skip
#: The owner's words for what the transport refuses. The bridge shows its own for most of these.
WORDS = {
    "busy": "The portfolio service is busy. Wait a minute, then try again.",
    "forbidden": "The portfolio service could not confirm that this request came from OpenAlgo. "
    "If OpenAlgo's .env was changed, restart sudarshan-portfolio.",
    "principal": "This portfolio service serves the account owner only.",
    "failed": "The portfolio service could not do this just now. Try again in a minute.",
    "off": "The portfolio is switched off on the server.",
    "not_ready": "This part of the portfolio is not ready yet.",
    "size": "That is too large for the portfolio service. Send a smaller file.",
    "op": "This request is not one the portfolio service answers for that caller.",
}


class Desk(Protocol):
    """What the API serves: whether the portfolio is on, its health document, and the context
    the ops are called with (the service's settings and its open store)."""

    enabled: bool

    def health(self) -> dict[str, Any]: ...


def off_data(reason: str | None = None) -> dict[str, Any]:
    """What every op answers while the portfolio is off: the switch, or ``reason`` (a file a newer
    release wrote, a store that will not open) in the owner's words."""
    return {"enabled": False, "message": reason or WORDS["off"]}


class PortfolioApi:
    """The listener. ``start`` binds 127.0.0.1:<port> and raises OSError when it is taken: the
    caller says so in words and tries again later; it never tries another port (cross-design X2)."""

    def __init__(
        self,
        desk: Desk,
        fernet: Fernet,
        *,
        registry: Mapping[str, ops_module.Handler] | None = None,
        nonces: NonceBook | None = None,
        clock: Callable[[], float] = time.time,
        family_fernet: Fernet | None = None,
        journal_fernet: Fernet | None = None,
    ):
        self.desk, self.fernet, self.clock = desk, fernet, clock
        #: The purposes this listener serves, in the order their keys are tried.
        self.purposes: list[tuple[str, Fernet]] = [(OWNER_PURPOSE, fernet)]
        if family_fernet is not None:
            self.purposes.append((FAMILY, family_fernet))
        if journal_fernet is not None:
            self.purposes.append((JOURNAL, journal_fernet))
        self.registry = ops_module.REGISTRY if registry is None else registry
        self.nonces = nonces if nonces is not None else NonceBook(NONCES)
        self.server: asyncio.Server | None = None
        self._warned: dict[str, float] = {}

    async def start(self, port: int) -> None:
        self.server = await asyncio.start_server(self._serve, kinds.LOCAL_HOST, port)

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
                    _read_body(reader, writer, length, headers),
                    UPLOAD_READ_TIMEOUT_S if (length or 0) > MAX_SEALED else READ_TIMEOUT_S,
                )
            except TimeoutError:  # only the read is a slow client; one raised by an op is a 500
                raise ApiError(408, "timeout", WORDS["failed"]) from None
            return 200, await self._route(method, path, body)
        except ApiError as exc:
            return exc.status, exc.body()
        except Exception:
            log.exception("a request to the portfolio's API failed")
            return 500, ApiError(500, "failed", WORDS["failed"]).body()

    def _check_head(self, method: str, path: str, length: int | None, headers: dict) -> None:
        """Refuse on the head alone: a browser page, no length, or more than the op's limit."""
        if "origin" in headers:  # a browser page; OpenAlgo's bridge sends none
            raise ApiError(403, "forbidden", "browser requests are not accepted")
        if method == "POST" and length is None:
            raise ApiError(411, "length", "Content-Length required")
        if length is not None and not 0 <= length <= self._limit(path):
            raise ApiError(413, "size", WORDS["size"])

    @staticmethod
    def _op_of(path: str) -> str | None:
        return path[len(OPS_PREFIX) :] if path.startswith(OPS_PREFIX) else None

    def _limit(self, path: str) -> int:
        return LIMITS.get(self._op_of(path) or "", MAX_SEALED)

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
        if not self._principal_ok(purpose, principal):
            self._note(op, f"principal under {purpose}")
            raise ApiError(403, "principal", WORDS["principal"])
        if op not in ALLOWED[purpose]:
            self._refuse(purpose, op, principal)
            raise ApiError(403, "forbidden", WORDS["op"])
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
        caller = await self._caller(purpose, principal, args)
        token = ops_common.CALLER.set(caller)
        try:
            # The arguments are what the bridge put under "args"; op, nonce and principal stay
            # the seal's.
            data = await handler(self.desk, args)
        finally:
            ops_common.CALLER.reset(token)
        if purpose != OWNER_PURPOSE:
            data = serialize.out(caller.scope, data)  # the mask once more, on the way out (Y6)
        return {"status": "success", "data": data}

    @staticmethod
    def _principal_ok(purpose: str, principal: Any) -> bool:
        if not isinstance(principal, str):
            return False
        if purpose == OWNER_PURPOSE:
            return principal == OWNER
        if purpose == FAMILY:
            return FAMILY_PRINCIPAL.fullmatch(principal) is not None
        return principal == kinds.JOURNAL_PRINCIPAL

    async def _caller(self, purpose: str, principal: str, args: dict) -> ops_common.Caller:
        """Who the op answers. For the family purpose the scope is built here from the stored
        shares and privacy; ``args.tabs`` can only narrow it and ``args.mask`` only add to it."""
        if purpose != FAMILY:
            return ops_common.Caller(purpose, principal)
        store = ops_common.need(self.desk)
        member = principal[len(kinds.FAMILY_PRINCIPAL_PREFIX) :]
        clock = getattr(self.desk, "clock", None)  # the service's own, in milliseconds
        now = int(clock()) if clock is not None else int(self.clock() * 1000)
        access = await ops_common.work(
            lambda: store.read(
                ops_common.OWNER,
                lambda tx: sharing.access_for(tx, member, now, args.get("tabs"), args.get("mask")),
            )
        )
        return ops_common.Caller(purpose, principal, access.scope, access)

    def _refuse(self, purpose: str, op: str, principal: Any) -> None:
        """An op that is not on the purpose's list: a security event, said once a minute per op
        and purpose (a flood of them is one line)."""
        log.debug("the portfolio's API refused %s under the %s purpose", op, purpose)
        key = f"{purpose}:{op}"
        now = time.monotonic()
        if now - self._warned.get(key, -1e9) >= 60:
            self._warned[key] = now
            log.warning(
                "the portfolio's API refused the op %s under the %s purpose (principal %s): "
                "that purpose opens read ops only", op, purpose, str(principal)[:60],
            )  # fmt: skip

    def _open(self, op: str, body: bytes) -> tuple[str, dict[str, Any]]:
        """The purpose whose key opens the seal, and the message. A seal no key opens is refused
        as one that was not sealed by this server's OpenAlgo; any other reason (size, expiry, the
        wrong op, a replayed nonce) ends the search at once."""
        reason = "seal"
        for purpose, fernet in self.purposes:
            try:
                return purpose, open_sealed(
                    fernet, body, op=op, seen=self.nonces, now=self.clock(),
                    max_bytes=LIMITS[op],
                )  # fmt: skip
            except NoncesFull:
                log.warning(
                    "the portfolio's API is refusing requests: too many in the last two minutes"
                )
                raise ApiError(503, "busy", WORDS["busy"]) from None
            except HandoffRejected as exc:
                reason = str(exc)
                if reason != "seal":
                    break
        self._note(op, reason)
        raise ApiError(403, "forbidden", WORDS["forbidden"])

    def _note(self, op: str, reason: str) -> None:
        """A refused request: at debug always, a warning at most once in 10 minutes."""
        log.debug("the portfolio's API refused a %s request (%s)", op, reason)
        now = time.monotonic()
        if now - self._warned.get("refused", -1e9) >= 600:
            self._warned["refused"] = now
            log.warning(
                "the portfolio's API refused a %s request that was not sealed by this server's "
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
