"""A health page for sudarshan-alerts: GET /health on 127.0.0.1 (M18.1, M6.8).

    curl -s 127.0.0.1:5585/health | python3 -m json.tool

What a person at a terminal, or a later check, wants first: which release is installed (``release``,
read from the deploy tool's record; ``version`` is this service's own code and is not the release),
whether the service is up and what it is doing, whether the owner's key was found, and what has gone
to Telegram. Nothing secret is on it: no key, no user name, no message text.

Stdlib asyncio, one request per connection, loopback only, read-only. It is a convenience and never
the job: a port that cannot be had is logged and the order alerts carry on without the page.

The document itself is ``health_document(service)``, a plain function: the page below serves it,
and the market alerts' local API (which owns this service's port 5582 and answers ``/health`` too)
serves the same document with its own fields added, after which this listener goes away.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from sudarshan.ops.release import installed_release

from . import __version__

log = logging.getLogger("sudarshan.alerts")

READ_TIMEOUT_S = 5.0  # the whole request must arrive within this
CLOSE_WAIT_S = 2.0  # a client that will not go does not hold a stop longer than this
MAX_HEAD = 8192
LOCAL_PEERS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
REASONS = {
    200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found",
    405: "Method Not Allowed", 408: "Request Timeout", 500: "Internal Server Error",
}  # fmt: skip


def health_document(service) -> dict:
    """The health document: plain facts from memory and one small file, nothing secret.

    Safe to call from anywhere in the service's process (the page below, the market alerts' API):
    synchronous, it changes nothing, and its only I/O is reading the deploy tool's record of the
    installed release. The values are copies, so a caller may add to or change the result. The
    names are fixed; a caller adding its own fields must not reuse them (the market alerts name
    theirs ``market_state`` and so on).
    """
    outbox = service.outbox
    return {
        "service": "sudarshan-alerts",
        "release": installed_release(service.settings.deployed_tag_file),
        "version": __version__,
        "state": service.state,
        "detail": service._detail,
        "window": service._window_text(),
        "owner_ready": service.owner is not None,
        "owner_problem": service._owner_problem,
        "stats": dict(service.stats),
        "telegram": dict(outbox.stats) if outbox is not None else None,
    }


class HealthPage:
    """The listener. ``start`` says whether it is up; ``close`` always ends it."""

    def __init__(self, service):
        self.service = service
        self.port: int | None = None
        self._server: asyncio.Server | None = None
        self._open: set[asyncio.StreamWriter] = set()

    async def start(self, port: int | None) -> bool:
        """Listen on 127.0.0.1 only. None is switched off; a taken port is logged, not raised."""
        if port is None:
            return False
        try:
            self._server = await asyncio.start_server(
                self._serve, "127.0.0.1", port, limit=MAX_HEAD
            )
        except OSError as exc:
            log.warning(
                "The health page could not start: port %d on this server is not available (%s). "
                "Order alerts are not affected.",
                port, exc.strerror or "unknown reason",
            )  # fmt: skip
            return False
        except Exception:  # whatever it is, the alerts matter more than the page
            log.exception("The health page could not start. Order alerts are not affected.")
            return False
        self.port = self._server.sockets[0].getsockname()[1]
        log.info("health page: 127.0.0.1:%d", self.port)
        return True

    async def close(self) -> None:
        server, self._server = self._server, None
        if server is None:
            return
        server.close()
        for writer in list(self._open):
            writer.close()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(server.wait_closed(), CLOSE_WAIT_S)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._open.add(writer)
        try:
            status, payload = await self._answer(reader, writer)
            data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
            head = (
                f"HTTP/1.1 {status} {REASONS.get(status, 'Error')}\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(data)}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n"
            )
            writer.write(head.encode() + data)
            await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass  # the client left, or the page is closing
        except Exception:
            log.exception("the health page failed on a request")
        finally:
            self._open.discard(writer)
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    async def _answer(self, reader, writer) -> tuple[int, dict]:
        peer = writer.get_extra_info("peername")
        if not peer or peer[0] not in LOCAL_PEERS:  # defence in depth: it is bound to loopback
            return 403, {"error": "local clients only"}
        try:
            method, path, headers = await asyncio.wait_for(_read_head(reader), READ_TIMEOUT_S)
        except TimeoutError:
            return 408, {"error": "request not received in time"}
        except (asyncio.LimitOverrunError, ValueError):
            return 400, {"error": "malformed request"}
        if "origin" in headers:  # a browser page (DNS rebinding); a local client sends none
            return 403, {"error": "browser requests are not accepted"}
        if path.split("?", 1)[0].rstrip("/") != "/health":
            return 404, {"error": "not found"}
        if method != "GET":
            return 405, {"error": "method not allowed"}
        try:
            return 200, health_document(self.service)
        except Exception:  # a bug here costs the page, never the service
            log.exception("the health page could not be built")
            return 500, {"error": "internal error"}


async def _read_head(reader: asyncio.StreamReader) -> tuple[str, str, dict[str, str]]:
    """The method, the target and the lower-cased headers; a body, if any, is never read."""
    head = await reader.readuntil(b"\r\n\r\n")
    request_line, *lines = head.decode("latin-1").split("\r\n")
    method, target, _ = request_line.split(" ", 2)
    headers = {}
    for line in lines:
        name, sep, value = line.partition(":")
        if sep:
            headers[name.strip().lower()] = value.strip()
    return method, target, headers
