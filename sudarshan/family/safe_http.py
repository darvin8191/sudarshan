"""The transport under the family channels' HTTP clients: a request that a cancel stops while it
is connecting leaves no socket open (the bot's long poll, a push, the journal's counts).

Under httpx 0.28 (httpcore 1.0.9, anyio 4.14) two steps of opening a connection lose the socket
when the task is cancelled inside them:

* anyio's ``connect_tcp`` connects in a child task; a cancel that lands between that connect and
  the return drops the connected stream without closing it;
* httpcore's ``start_tls`` closes the TCP stream on an error but not on a cancel.

Either way the socket stays open after the client is closed, held by the event loop (its
transport keeps reading), and the peer waits on it. A stop cancels whatever request is in
flight, so each stop could leave one behind (a family test caught it on a CI machine:
``test_the_bot_leaves_no_descriptor_or_task_behind``). ``transport()`` gives httpx's own
transport with its network backend wrapped: the connect runs in a task of its own that the
caller's cancel does not reach, and a connection the caller no longer wants is closed as soon
as the connect ends (within its connect timeout); a cancelled TLS handshake closes its TCP
stream. Reading and writing are left to httpcore, which already closes a connection a cancel
interrupts.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
from collections.abc import Iterable
from typing import Any

import httpcore
import httpx

log = logging.getLogger("sudarshan.family")

#: Closes of connections nobody waits for any more (a strong reference until each is done).
_closing: set[asyncio.Task] = set()


async def _close(stream: httpcore.AsyncNetworkStream) -> None:
    try:
        await stream.aclose()
    except Exception:  # already closed or broken: either way it is not open any more
        log.debug("a family connection closed with an error while closing")


def _close_soon(stream: httpcore.AsyncNetworkStream) -> asyncio.Task:
    """Close ``stream`` in a task of its own, kept until it is done (a second cancel of the
    caller cannot stop it)."""
    closing = asyncio.ensure_future(_close(stream))
    _closing.add(closing)
    closing.add_done_callback(_closing.discard)
    return closing


def _close_late(task: asyncio.Future) -> None:
    """A connect the caller gave up on has ended: close what it opened."""
    if task.cancelled() or task.exception() is not None:
        return  # nothing was opened, or anyio closed it with its error
    _close_soon(task.result())


class CancelSafeStream(httpcore.AsyncNetworkStream):
    """A connection whose TLS handshake, when cancelled, closes the TCP stream under it."""

    def __init__(self, inner: httpcore.AsyncNetworkStream):
        self.inner = inner

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        return await self.inner.read(max_bytes, timeout)

    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        await self.inner.write(buffer, timeout)

    async def aclose(self) -> None:
        await self.inner.aclose()

    async def start_tls(self, ssl_context: ssl.SSLContext, server_hostname: str | None = None,
                        timeout: float | None = None) -> httpcore.AsyncNetworkStream:  # fmt: skip
        try:
            upgraded = await self.inner.start_tls(ssl_context, server_hostname, timeout)
        except asyncio.CancelledError:
            await asyncio.shield(_close_soon(self.inner))  # httpcore closes it on errors only
            raise
        return CancelSafeStream(upgraded)

    def get_extra_info(self, info: str) -> Any:
        return self.inner.get_extra_info(info)


class CancelSafeBackend(httpcore.AsyncNetworkBackend):
    """httpcore's network backend with a connect that a cancel cannot cut in half."""

    def __init__(self, inner: httpcore.AsyncNetworkBackend):
        self.inner = inner

    async def _connect(self, connecting: asyncio.Future) -> httpcore.AsyncNetworkStream:
        try:
            return CancelSafeStream(await asyncio.shield(connecting))
        except asyncio.CancelledError:
            connecting.add_done_callback(_close_late)
            raise

    async def connect_tcp(self, host: str, port: int, timeout: float | None = None,
                          local_address: str | None = None,
                          socket_options: Iterable[Any] | None = None,
                          ) -> httpcore.AsyncNetworkStream:  # fmt: skip
        opening = self.inner.connect_tcp(host, port, timeout, local_address, socket_options)
        return await self._connect(asyncio.ensure_future(opening))

    async def connect_unix_socket(self, path: str, timeout: float | None = None,
                                  socket_options: Iterable[Any] | None = None,
                                  ) -> httpcore.AsyncNetworkStream:  # fmt: skip
        opening = self.inner.connect_unix_socket(path, timeout, socket_options)
        return await self._connect(asyncio.ensure_future(opening))

    async def sleep(self, seconds: float) -> None:
        await self.inner.sleep(seconds)


def transport(**options: Any) -> httpx.AsyncHTTPTransport:
    """httpx's transport (``options`` as ``httpx.AsyncHTTPTransport`` takes them) on the
    cancel-safe backend. If a later httpx keeps its pool elsewhere, the plain transport is
    returned and the journal says so once; ``test_family_safe_http.py`` fails on it first."""
    made = httpx.AsyncHTTPTransport(**options)
    pool = getattr(made, "_pool", None)
    backend = getattr(pool, "_network_backend", None)
    if isinstance(backend, httpcore.AsyncNetworkBackend):
        if not isinstance(backend, CancelSafeBackend):
            pool._network_backend = CancelSafeBackend(backend)  # type: ignore[union-attr]
    elif not getattr(transport, "warned", False):
        transport.warned = True  # type: ignore[attr-defined]
        log.warning("the family service's HTTP clients run without the cancel-safe connect: "
                    "this httpx keeps its connection pool elsewhere")  # fmt: skip
    return made
