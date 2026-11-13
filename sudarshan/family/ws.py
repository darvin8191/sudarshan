"""The phones' WebSocket at /family/ws, on the service's one listener (cross-design Y10).

websockets 15's own server refuses any method but GET before it can be asked, so the listener is
the stdlib HTTP of ``listener.py``, and a ``GET /family/ws`` with ``Upgrade: websocket`` is handed
here: websockets' sans-I/O ``ServerProtocol`` checks the handshake and does the framing, the pings'
answers and the close; this module only moves bytes between it and the connection. No RFC 6455
framing is written here.

Per socket: messages to the phone wait in a bounded queue (200) and a slow phone loses frames rather
than holding memory; a ping every 25 s; closed after 60 s without a byte from the phone. The
registry knows which session each socket belongs to, so a revoke closes that device's sockets at
once with 4401 and "Signed out on this device." (F4), and it caps the sockets at 20 in all.

What the phone and the service say to each other is the dialect's (F6: subscribe, ticks, books);
until then the default dialect answers only ``authenticate``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Callable
from typing import Any, Protocol

from websockets.frames import Frame, Opcode
from websockets.http11 import Request
from websockets.protocol import State
from websockets.server import ServerProtocol

from sudarshan.tasks import cancel_and_wait

from . import words
from .contract import WS_CLOSE_SIGNED_OUT

log = logging.getLogger("sudarshan.family")

QUEUE = 200
PING_EVERY_S = 25.0
SILENCE_S = 60.0
#: The largest message a phone may send (a subscribe of 150 symbols is a few KiB).
MAX_MESSAGE = 64 * 1024
#: A phone that does not take what is written within this long is cut off.
WRITE_TIMEOUT_S = 10.0
#: After a close frame, how long the phone has to answer before the connection is dropped.
CLOSE_TIMEOUT_S = 2.0
MAX_SOCKETS = 20
READ_CHUNK = 64 * 1024
_CLOSE = object()


class Dialect(Protocol):
    async def on_open(self, sock: ViewerSocket) -> None: ...
    async def on_message(self, sock: ViewerSocket, text: str) -> None: ...
    async def on_close(self, sock: ViewerSocket) -> None: ...


class Plain:
    """The default dialect: ``authenticate`` is answered (the session was checked at the
    handshake); anything else waits for the dialect that serves it."""

    async def on_open(self, sock: ViewerSocket) -> None:
        return None

    async def on_message(self, sock: ViewerSocket, text: str) -> None:
        try:
            message = json.loads(text)
        except ValueError:
            return
        if isinstance(message, dict) and message.get("action") == "authenticate":
            sock.send({"type": "auth", "status": "success"})

    async def on_close(self, sock: ViewerSocket) -> None:
        return None


class Registry:
    """The open sockets, by the hash of the session each belongs to."""

    def __init__(self, limit: int = MAX_SOCKETS):
        self.limit = limit
        self._by_session: dict[str, set[ViewerSocket]] = {}

    def __len__(self) -> int:
        return sum(len(found) for found in self._by_session.values())

    def full(self) -> bool:
        return len(self) >= self.limit

    def add(self, sock: ViewerSocket) -> None:
        self._by_session.setdefault(sock.session, set()).add(sock)

    def discard(self, sock: ViewerSocket) -> None:
        found = self._by_session.get(sock.session)
        if found is not None:
            found.discard(sock)
            if not found:
                del self._by_session[sock.session]

    def sessions(self) -> list[str]:
        return list(self._by_session)

    def sockets(self) -> list[ViewerSocket]:
        return [sock for found in self._by_session.values() for sock in found]

    def close_sessions(self, hashes: list[str], code: int = WS_CLOSE_SIGNED_OUT,
                       reason: str | None = None) -> int:  # fmt: skip
        """Close every socket of these sessions; how many."""
        closed = 0
        for hashed in hashes:
            for sock in list(self._by_session.get(hashed, ())):
                sock.close(code, reason if reason is not None else words.say("ws_signed_out"))
                closed += 1
        return closed


class ViewerSocket:
    """One phone's socket. ``run`` answers the handshake and serves until either side closes;
    ``send`` and ``close`` are called from the dialect and the service (same event loop)."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        *,
        session: str,
        member_id: int,
        dialect: Dialect,
        registry: Registry,
        clock: Callable[[], float] = time.monotonic,
        ping_every: float = PING_EVERY_S,
        silence: float = SILENCE_S,
    ):
        self.reader, self.writer = reader, writer
        self.session, self.member_id = session, member_id
        self.dialect, self.registry, self.clock = dialect, registry, clock
        self.ping_every, self.silence = ping_every, silence
        self.protocol = ServerProtocol(max_size=MAX_MESSAGE)
        self.queue: asyncio.Queue[Any] = asyncio.Queue(QUEUE)
        self.dropped = 0
        self.closing: tuple[int, str] | None = None
        self.heard = self.clock()
        self._ended = asyncio.Event()  # the phone closed its side, or the connection is gone

    @property
    def open(self) -> bool:
        return self.protocol.state is State.OPEN and self.closing is None

    def send(self, message: dict[str, Any]) -> bool:
        """Queue one message; False (and it is dropped) when the queue is full or the socket is
        closing: a slow phone loses frames, the service never waits for it."""
        if not self.open:
            return False
        try:
            self.queue.put_nowait(json.dumps(message, separators=(",", ":"), ensure_ascii=False))
        except asyncio.QueueFull:
            self.dropped += 1
            return False
        return True

    def close(self, code: int = 1000, reason: str = "") -> None:
        """Close with ``code`` and ``reason`` as soon as the writer gets to it."""
        if self.closing is not None:
            return
        self.closing = (code, reason)
        with contextlib.suppress(asyncio.QueueFull):
            self.queue.put_nowait(_CLOSE)
            return
        with contextlib.suppress(asyncio.QueueEmpty):
            self.queue.get_nowait()  # make room: the close goes before a frame the phone is slow on
        self.queue.put_nowait(_CLOSE)

    async def run(self, head: bytes) -> bool:
        """Answer the handshake in ``head`` (the request as read); True once it was accepted and
        the socket has served and closed, False when the handshake was refused."""
        self.protocol.receive_data(head)
        events = self.protocol.events_received()
        request = events[0] if events and isinstance(events[0], Request) else None
        if request is None:
            self.protocol.send_response(self.protocol.reject(400, "Not a WebSocket request.\n"))
            await self._flush()
            return False
        response = self.protocol.accept(request)
        self.protocol.send_response(response)
        if not await self._flush() or response.status_code != 101:
            return False
        self.registry.add(self)
        reading = asyncio.create_task(self._reading(), name="family-ws-read")
        writing = asyncio.create_task(self._writing(), name="family-ws-write")
        try:
            await self.dialect.on_open(self)
            await asyncio.wait({reading, writing}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            self.registry.discard(self)
            await cancel_and_wait(reading, writing)
            with contextlib.suppress(Exception):
                await self.dialect.on_close(self)
        return True

    async def _flush(self) -> bool:
        """Write what the protocol has for the phone; False when the phone is gone or too slow."""
        try:
            for chunk in self.protocol.data_to_send():
                if chunk:
                    self.writer.write(chunk)
                elif self.writer.can_write_eof():
                    self.writer.write_eof()
            await asyncio.wait_for(self.writer.drain(), WRITE_TIMEOUT_S)
            return True
        except (ConnectionError, TimeoutError, OSError):
            self.writer.transport.abort()
            return False

    async def _reading(self) -> None:
        try:
            await self._read_until_closed()
        finally:
            self._ended.set()

    async def _read_until_closed(self) -> None:
        while True:
            try:
                data = await self.reader.read(READ_CHUNK)
            except (ConnectionError, OSError):
                data = b""
            if not data:
                self.protocol.receive_eof()
                await self._flush()
                return
            self.heard = self.clock()
            self.protocol.receive_data(data)
            for event in self.protocol.events_received():
                if isinstance(event, Frame) and event.opcode is Opcode.TEXT and self.open:
                    try:
                        await self.dialect.on_message(self, event.data.decode("utf-8"))
                    except Exception:
                        log.exception("a family socket message could not be handled")
            if not await self._flush() or self.protocol.state is State.CLOSED:
                return

    async def _writing(self) -> None:
        pinged = self.clock()
        while True:
            wait = max(0.05, min(self.ping_every - (self.clock() - pinged),
                                 self.silence - (self.clock() - self.heard)))  # fmt: skip
            try:
                item = await asyncio.wait_for(self.queue.get(), wait)
            except TimeoutError:
                item = None
            if item is _CLOSE or self.closing is not None:
                await self._close_now(*(self.closing or (1000, "")))
                return
            if self.protocol.state is not State.OPEN:
                return
            if item is not None:
                self.protocol.send_text(item.encode("utf-8"))
            now = self.clock()
            if now - self.heard >= self.silence:
                await self._close_now(1001, "")
                return
            if now - pinged >= self.ping_every:
                self.protocol.send_ping(b"")
                pinged = now
            if not await self._flush():
                return

    async def _close_now(self, code: int, reason: str) -> None:
        """Send the close frame and give the phone a moment to answer it (the reader sees the
        answer), so that the phone reads the code and the words before the connection ends."""
        if self.protocol.state is State.OPEN:
            self.protocol.send_close(code, reason)
            if await self._flush():
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._ended.wait(), CLOSE_TIMEOUT_S)
