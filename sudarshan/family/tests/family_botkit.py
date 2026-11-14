"""Shared pieces for the bot's and the channels' tests (F8, F10, T2): a fake Telegram behind the
real allow-list (in memory, or on a real local socket for the descriptor tests), a clock whose
sleep moves time instead of waiting, a service with just what the bot reads, and Telegram updates.

Everything is synthetic: the token is made per run, chat ids are 9xxxxxxxx (nobody's), the
members are "Member A" and the like, and nothing reaches the network: the local socket listens on
an ephemeral port of 127.0.0.1, and the client still names api.telegram.org, so the allow-list
judges the request it would really make.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import secrets
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx

from sudarshan.family import bot as bot_module
from sudarshan.family import safe_http
from sudarshan.family.quiet import MarketHours
from sudarshan.family.telegram_client import TelegramClient, make_http

USERNAME = "SynthFamilyBot"


def new_token() -> str:
    """A token shaped as BotFather's, made for this run only."""
    return f"{secrets.randbelow(10**9) + 10**9}:{secrets.token_urlsafe(30)[:35]}"


class FakeTelegram:
    """Telegram's Bot API for one token: getMe, getUpdates (from a queue), sendMessage
    (recorded). ``refuse`` answers 401, ``conflict`` 409, ``slow`` a 429 for that many sends,
    ``block`` a 403 for every send."""

    def __init__(self, token: str, username: str = USERNAME):
        self.token, self.username = token, username
        self.updates: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict[str, Any]] = []
        self.calls: list[str] = []
        self.refuse = False
        self.conflict = False
        self.slow = 0
        self.block = False
        self.next_id = 1

    def push(self, text: Any, chat: int = 900_000_001, *, date: int | None = None,
             chat_type: str = "private", **more: Any) -> None:  # fmt: skip
        message: dict[str, Any] = {"message_id": self.next_id,
                                   "chat": {"id": chat, "type": chat_type}, **more}  # fmt: skip
        if text is not None:
            message["text"] = text
        if date is not None:
            message["date"] = date
        self.updates.put_nowait({"update_id": self.next_id, "message": message})
        self.next_id += 1

    def texts(self, chat: int | None = None) -> list[str]:
        return [m["text"] for m in self.sent if chat is None or m["chat_id"] == chat]

    async def answer(self, path: str, content: bytes) -> tuple[int, dict[str, Any]]:
        _, bot_token, method = path.split("/", 2)
        self.calls.append(method)
        if bot_token != f"bot{self.token}" or self.refuse:
            return 401, {"ok": False, "error_code": 401, "description": "Unauthorized"}
        if method == "getMe":
            return 200, {"ok": True, "result": {"id": 1, "is_bot": True, "username": self.username}}
        if method == "getUpdates":
            if self.conflict:
                return 409, {"ok": False, "error_code": 409, "description": "Conflict"}
            offset = json.loads(content).get("offset", 0)
            found = []
            with contextlib.suppress(TimeoutError):
                found.append(await asyncio.wait_for(self.updates.get(), 0.05))
                while not self.updates.empty():
                    found.append(self.updates.get_nowait())
            return 200, {"ok": True, "result": [u for u in found if u["update_id"] >= offset]}
        if method == "sendMessage":
            if self.slow:
                self.slow -= 1
                return 429, {"ok": False, "error_code": 429, "parameters": {"retry_after": 2}}
            if self.block:
                return 403, {"ok": False, "error_code": 403, "description": "Forbidden"}
            self.sent.append(json.loads(content))
            return 200, {"ok": True, "result": {"message_id": len(self.sent)}}
        return 404, {"ok": False, "error_code": 404}

    async def handler(self, request: httpx.Request) -> httpx.Response:
        status, payload = await self.answer(request.url.path, request.content)
        return httpx.Response(status, json=payload)

    def http(self) -> httpx.AsyncClient:
        """The bot's own client, allow-list and all, in front of this fake (in memory)."""
        return make_http(inner=httpx.MockTransport(self.handler))


class ToLocal(httpx.AsyncBaseTransport):
    """Sends what the client addressed to Telegram to a local port, through httpx's real
    transport: real sockets, so a descriptor that is not closed shows."""

    def __init__(self, port: int):
        self.port, self.inner = port, safe_http.transport(retries=0)  # as the bot's own

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = request.url.copy_with(scheme="http", host="127.0.0.1", port=self.port)
        moved = httpx.Request(request.method, url, headers=request.headers,
                              content=request.content, extensions=request.extensions)  # fmt: skip
        return await self.inner.handle_async_request(moved)

    async def aclose(self) -> None:
        await self.inner.aclose()


class TelegramServer:
    """A ``FakeTelegram`` on a real socket of 127.0.0.1 (one request per connection)."""

    def __init__(self, fake: FakeTelegram):
        self.fake, self.server = fake, None
        self.port = 0

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self.port

    def http(self) -> httpx.AsyncClient:
        return make_http(inner=ToLocal(self.port))

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.server.wait_closed(), 2)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            path = lines[0].split(" ")[1]
            length = next((int(v) for k, _, v in (line.partition(":") for line in lines[1:])
                           if k.strip().lower() == "content-length"), 0)  # fmt: skip
            status, payload = await self.fake.answer(path, await reader.readexactly(length))
            data = json.dumps(payload).encode()
            writer.write(f"HTTP/1.1 {status} X\r\nContent-Type: application/json\r\n"
                         f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode()
                         + data)  # fmt: skip
            await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()


class Time:
    """A monotonic clock whose sleep moves it instead of waiting (and yields once)."""

    def __init__(self, at: float = 1_000.0):
        self.now = at
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += max(0.0, seconds)
        await asyncio.sleep(0)


class Notices:
    def __init__(self):
        self.seen: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, kind: str, member: Any = None, **facts: Any) -> bool:
        self.seen.append((kind, facts))
        return True


class Weekdays:
    def is_trading_day(self, day) -> bool:
        return day.weekday() < 5

    def day_window(self, day) -> None:
        return None


def fake_service(store: Any, clock: Any, etc: Path, viewer: Any = None) -> SimpleNamespace:
    """What the bot reads of the service: the store, the clock, the notices, the viewer's routes
    and the settings' folder."""
    return SimpleNamespace(store=store, clock=clock, notices=Notices(),
                           viewer=viewer or SimpleNamespace(),
                           settings=SimpleNamespace(etc_dir=etc))  # fmt: skip


def make_bot(service: Any, fake: FakeTelegram, etc: Path, time_: Time | None = None):
    """A bot on the fake Telegram whose token file holds the fake's token."""
    etc.mkdir(parents=True, exist_ok=True)
    (etc / bot_module.TOKEN_FILE).write_text(fake.token + "\n", encoding="ascii")
    clock = time_ or Time()
    made = bot_module.Bot(service, TelegramClient(fake.http()),
                          token_path=etc / bot_module.TOKEN_FILE, market=MarketHours(Weekdays()),
                          monotonic=clock, sleep=clock.sleep)  # fmt: skip
    return made, clock


async def ready(made: bot_module.Bot) -> None:
    """The bot as after its first good getMe."""
    made.read_token()
    answer = await made.client.get_me(made._token)
    made.username, made.state = answer.result["username"], "polling"


async def settle(made: bot_module.Bot) -> None:
    """Let the bot's background writes (its audit lines) finish, then close its client."""
    if made._tasks:
        await asyncio.gather(*made._tasks, return_exceptions=True)
    await made.client.http.aclose()


def update(text: Any, chat: int = 900_000_001, **more: Any) -> dict[str, Any]:
    message = {"message_id": 1, "chat": {"id": chat, "type": "private"}, **more}
    if text is not None:
        message["text"] = text
    return {"update_id": 1, "message": message}
