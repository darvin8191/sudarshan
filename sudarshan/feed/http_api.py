"""Local HTTP API on 127.0.0.1:5580: stdlib asyncio, JSON in and out, one request per connection.

GET  /health                        service state (also published on ZMQ topic __health__)
GET  /snapshot?keys=K1,K2           last tick per key (null when none yet); keys URL-encoded
GET  /snapshot/all                  every key the service has a tick for
GET  /subscriptions                 desired subscription set by mode
POST /subscribe   {"mode": M, "keys": [...]}    400 bad request, 409 over an Upstox cap
POST /unsubscribe {"keys": [...]}
GET    /claims                      every client's claims (M18.1)
PUT    /claims/<client> {"keys": {KEY: MODE}}   replace and renew; 400 bad request, 409 over a cap
DELETE /claims/<client>
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from urllib.parse import SplitResult, parse_qs, urlsplit

from .subscriptions import CapError

log = logging.getLogger("sudarshan.feed.http")

MAX_BODY = 1 << 20
READ_TIMEOUT_S = 10.0
LOCAL_PEERS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
PATHS = {"/health", "/snapshot", "/snapshot/all", "/subscriptions", "/subscribe", "/unsubscribe",
         "/claims"}  # fmt: skip
CLAIMS = "/claims/"
REASONS = {
    200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed",
    408: "Request Timeout", 409: "Conflict", 413: "Content Too Large", 500: "Internal Server Error",
}  # fmt: skip


class HTTPError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def is_local(peer) -> bool:
    return bool(peer) and peer[0] in LOCAL_PEERS


async def start_http(service, port: int) -> asyncio.Server:
    """Bind 127.0.0.1 only; a taken port raises OSError (a startup error, never a new port)."""
    return await asyncio.start_server(lambda r, w: _serve(service, r, w), "127.0.0.1", port)


async def _serve(service, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        if not is_local(writer.get_extra_info("peername")):  # defence in depth: bound to loopback
            raise HTTPError(403, "local clients only")
        # Only reading is time-limited: a subscribe that has started sending must finish.
        method, url, body = await asyncio.wait_for(_read(reader, writer), READ_TIMEOUT_S)
        status, payload = 200, await _route(service, method, url, body)
    except HTTPError as exc:
        status, payload = exc.status, {"error": str(exc)}
    except CapError as exc:
        status, payload = 409, {"error": str(exc)}
    except ValueError as exc:  # request validation from the service
        status, payload = 400, {"error": str(exc)}
    except TimeoutError:
        status, payload = 408, {"error": "request not received in time"}
    except Exception:
        log.exception("HTTP request failed")
        status, payload = 500, {"error": "internal error"}
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    head = (
        f"HTTP/1.1 {status} {REASONS.get(status, 'Error')}\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(data)}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n"
    )
    with contextlib.suppress(ConnectionError):
        writer.write(head.encode() + data)
        await writer.drain()
    writer.close()
    with contextlib.suppress(ConnectionError):
        await writer.wait_closed()


async def _read(reader, writer) -> tuple[str, SplitResult, bytes]:
    try:
        head = await reader.readuntil(b"\r\n\r\n")
        request_line, *lines = head.decode("latin-1").split("\r\n")
        method, target, _ = request_line.split(" ", 2)
        headers = {}
        for line in lines:
            name, sep, value = line.partition(":")
            if sep:
                headers[name.strip().lower()] = value.strip()
        length = int(headers.get("content-length") or 0)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ValueError):
        raise HTTPError(400, "malformed HTTP request") from None
    if "origin" in headers:  # a browser page (CSRF / DNS rebinding); local services send none
        raise HTTPError(403, "browser requests are not accepted")
    if not 0 <= length <= MAX_BODY:
        raise HTTPError(413, f"body larger than {MAX_BODY} bytes")
    if length and headers.get("expect", "").lower() == "100-continue":
        writer.write(b"HTTP/1.1 100 Continue\r\n\r\n")
    try:
        body = await reader.readexactly(length) if length else b""
    except asyncio.IncompleteReadError:
        raise HTTPError(400, "body shorter than Content-Length") from None
    return method, urlsplit(target), body


def _json_object(body: bytes, example: str) -> dict:
    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        raise HTTPError(400, "body must be JSON") from None
    if not isinstance(payload, dict):
        raise HTTPError(400, f"body must be a JSON object like {example}")
    return payload


async def _route(service, method: str, url: SplitResult, body: bytes) -> dict:
    path = url.path.rstrip("/") or "/"
    if method == "GET" and path == "/health":
        return service.health()
    if method == "GET" and path == "/snapshot/all":
        return {"ticks": service.snapshot(None)}
    if method == "GET" and path == "/snapshot":
        raw = ",".join(parse_qs(url.query).get("keys", []))
        keys = [k.strip() for k in raw.split(",") if k.strip()]
        if not keys:
            raise HTTPError(400, "give ?keys=KEY1,KEY2 (URL-encoded) or use /snapshot/all")
        return {"ticks": service.snapshot(keys)}
    if method == "GET" and path == "/subscriptions":
        return service.subscriptions()
    if method == "POST" and path in ("/subscribe", "/unsubscribe"):
        payload = _json_object(body, '{"mode": "ltpc", "keys": [...]}')
        if path == "/subscribe":
            return await service.subscribe(payload.get("mode"), payload.get("keys"))
        return await service.unsubscribe(payload.get("keys"))
    if method == "GET" and path == "/claims":
        return service.claims_view()
    if path.startswith(CLAIMS) and method in ("PUT", "DELETE"):
        client = path[len(CLAIMS) :]
        if method == "DELETE":
            return await service.drop_claims(client)
        payload = _json_object(body, '{"keys": {"NSE_EQ|INE002A01018": "full"}}')
        return await service.put_claims(client, payload.get("keys"))
    if path in PATHS or path.startswith(CLAIMS):
        raise HTTPError(405, "method not allowed")
    raise HTTPError(404, "not found")
