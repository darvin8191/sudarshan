"""Shared pieces for the data plane's tests (F5 to F7): a fake portfolio service that speaks the
real sealed transport, a fake calendar, IST instants, sockets that record what they are sent, and
a member signed in straight in the store.

Everything is synthetic: the symbols are SYNTH*, the figures are round, the members are "Member A"
and the like, and every server binds an ephemeral port on 127.0.0.1 (never 5583, 5585 or 5581).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from cryptography.fernet import Fernet

from sudarshan.autologin.handoff import HandoffRejected, NonceBook, open_sealed
from sudarshan.autologin.schedule import IST
from sudarshan.family import sessions
from sudarshan.family.contract import COOKIE, INTERNAL_PATH

#: The portfolio's family ops and principal (a test holds them to sudarshan/portfolio/core/kinds).
FAMILY_OPS = ("state", "summary", "tab_get", "performance", "upstox_book", "upstox_history",
              "shares_for_viewer")  # fmt: skip
PRINCIPAL = re.compile(r"viewer:[A-Za-z0-9_-]{1,40}")
MONDAY = date(2026, 11, 16)


def ist(day: date, hour: int, minute: int = 0, second: int = 0) -> int:
    """Epoch milliseconds of an IST wall time."""
    at = datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=IST)
    return int(at.timestamp() * 1000)


class Clock:
    """A clock a test moves by hand (epoch ms)."""

    def __init__(self, at: int):
        self.at = at

    def __call__(self) -> int:
        return self.at


class Weekdays:
    """A calendar where Monday to Friday trade and no day has a window of its own."""

    def is_trading_day(self, day: date) -> bool:
        return day.weekday() < 5

    def day_window(self, day: date) -> None:
        return None


class Sock:
    """Stands in for a viewer socket: records what it is sent."""

    def __init__(self, member_id: int = 1, session: str = "ab" * 32):
        self.member_id, self.session = member_id, session
        self.sent: list[dict[str, Any]] = []
        self.closed: tuple[int, str] | None = None

    def send(self, message: dict[str, Any]) -> bool:
        self.sent.append(message)
        return True

    def close(self, code: int = 1000, reason: str = "") -> None:
        self.closed = (code, reason)

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [m for m in self.sent if m.get("type") == kind]


def position(n: int = 1, qty_u: int = 100_000, **more: Any) -> dict[str, Any]:
    """A position row as the portfolio's reader parses it (portfolio/upstox.py)."""
    return {
        "instrument_key": f"NSE_EQ|SYNTH{n:07d}", "exchange": "NSE", "symbol": f"SYNTH{n}",
        "name": f"SYNTH{n}", "product": "I", "qty_u": qty_u, "overnight_qty_u": 0,
        "day_buy_qty_u": max(qty_u, 0), "day_sell_qty_u": max(-qty_u, 0),
        "avg_price_u": 1_000_000, "buy_price_u": 1_000_000, "sell_price_u": 0,
        "last_price_u": 1_010_000, "close_price_u": 1_005_000, "multiplier": 1,
        "pnl_paise": 10_000, "realised_paise": 0, "unrealised_paise": 10_000,
        "value_paise": 100_000, **more,
    }  # fmt: skip


def holding(n: int = 1) -> dict[str, Any]:
    return {
        "instrument_key": f"NSE_EQ|SYNTH{n:07d}", "exchange": "NSE", "symbol": f"SYNTH{n}",
        "name": f"SYNTH{n} LTD", "isin": None, "qty_u": 50_000, "t1_qty_u": 0,
        "avg_price_u": 900_000, "last_price_u": 1_010_000, "close_price_u": 1_005_000,
        "pnl_paise": 5_500, "product": "D",
    }  # fmt: skip


def book_view(rows: Any, read_at: int, state: str = "ok") -> dict[str, Any]:
    return {"state": state, "read_at": read_at, "note": None, "data": rows}


class FakePortfolio:
    """The portfolio's sealed API as the family purpose sees it: the seal opened with the family
    key (60 s, once), principal ``viewer:<id>``, only the family's read ops (403 for anything
    else, as the real one answers). It masks nothing by default, so a test can see what the
    family's own second pass removes. ``answer(op, member_id, args)`` gives (status, envelope)."""

    def __init__(self, fernet: Fernet, answer: Callable[[str, int, dict], tuple[int, Any]]):
        self.fernet, self.answer = fernet, answer
        self.calls: list[tuple[str, int, dict[str, Any]]] = []
        self.nonces = NonceBook(4096)
        self.server: asyncio.Server | None = None
        self.hold: asyncio.Event | None = None  # set: answers wait until it is set

    async def start(self) -> str:
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        return f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"

    async def close(self) -> None:
        if self.server is not None:
            self.server.close()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.server.wait_closed(), 2)

    def ops(self) -> list[str]:
        return [op for op, _, _ in self.calls]

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            path = lines[0].split(" ")[1]
            length = next(int(v) for k, _, v in (line.partition(":") for line in lines[1:])
                          if k.strip().lower() == "content-length")  # fmt: skip
            body = await reader.readexactly(length)
            status, payload = self._judge(path[len("/v1/") :], body)
            if self.hold is not None:
                await self.hold.wait()
            data = json.dumps(payload).encode()
            writer.write(f"HTTP/1.1 {status} X\r\nContent-Type: application/json\r\n"
                         f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode() + data)  # fmt: skip
            await writer.drain()
        finally:
            writer.close()

    def _judge(self, op: str, body: bytes) -> tuple[int, Any]:
        refused = {"status": "error", "code": "forbidden", "message": "refused"}
        try:
            message = open_sealed(self.fernet, body, op=op, seen=self.nonces)
        except HandoffRejected:
            return 403, refused
        principal = message.get("principal")
        if not isinstance(principal, str) or not PRINCIPAL.fullmatch(principal):
            return 403, {**refused, "code": "principal"}
        if op not in FAMILY_OPS:
            return 403, refused
        args = message.get("args") or {}
        member_id = int(principal.split(":", 1)[1])
        self.calls.append((op, member_id, args))
        return self.answer(op, member_id, args)


def ok(data: Any) -> tuple[int, Any]:
    return 200, {"status": "success", "data": data}


def books_answer(rows: dict[str, Any], clock: Callable[[], int]):
    """An ``answer`` that gives each asked part its rows, read now."""

    def answer(op: str, member_id: int, args: dict) -> tuple[int, Any]:
        if op != "upstox_book":
            return ok({})
        return ok({part: book_view(rows.get(part, []), clock()) for part in args.get("parts", [])})

    return answer


async def signed_in(service: Any, privacy: dict[str, bool], now: int, *,
                    role: str = "viewer") -> tuple[dict[str, Any], str]:  # fmt: skip
    """An active member and a session for them, made in the running service's store: the member
    row and the Cookie header that carries the session."""

    def make(tx):
        member = tx.member_add({"display_name": "Member A", "role": role, "status": "active",
                                "privacy": privacy, "language": "en"}, now)  # fmt: skip
        device = sessions.Device("Android 15 · Chrome 130", "Android 15 · Chrome 130")
        session_id, _ = sessions.mint(tx, member["id"], device, now)
        return member, session_id

    member, session_id = await service.store.write(make)
    return member, f"{COOKIE}={session_id}"


VIEWER = {"pnl": True, "qty": True, "funds": False, "orders": True}
CHILD = {"pnl": False, "qty": False, "funds": False, "orders": False}


class FakeFeed:
    """sudarshan-feed as the family service sees it, on ephemeral ports (never 5580 or 5581):
    PUT/DELETE /claims/<client> (recorded with the Authorization header), GET /health, GET
    /snapshot over HTTP, and a ZMQ PUB the test publishes ticks and health on."""

    def __init__(self):
        self.calls: list[tuple[str, str, dict | None, str | None]] = []
        self.health: dict[str, Any] = {"state": "connected", "subscriptions_live": True,
                                       "started_at": "2026-11-16T08:58:00+05:30",
                                       "subscribed": {"ltpc": 60, "full": 40, "total": 100}}  # fmt: skip
        self.snapshots: dict[str, dict] = {}
        self.status: dict[str, int] = {"PUT": 200, "DELETE": 200}
        self.server: asyncio.Server | None = None
        self.ctx: Any = None
        self.pub: Any = None

    async def start(self) -> tuple[str, str]:
        import zmq

        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.ctx = zmq.Context()
        self.pub = self.ctx.socket(zmq.PUB)
        self.pub.setsockopt(zmq.LINGER, 0)
        self.pub.bind("tcp://127.0.0.1:*")
        endpoint = self.pub.getsockopt_string(zmq.LAST_ENDPOINT)
        return f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}", endpoint

    def claims(self, method: str = "PUT") -> list[tuple[dict | None, str | None]]:
        return [(body, auth) for m, path, body, auth in self.calls
                if m == method and path.startswith("/claims/")]  # fmt: skip

    def publish(self, key: str, frame: dict) -> None:
        import zmq

        self.pub.send_multipart((key.encode(), json.dumps(frame).encode()), zmq.NOBLOCK)

    def publish_health(self) -> None:
        self.publish("__health__", self.health)

    async def close(self) -> None:
        if self.pub is not None:
            self.pub.close(0)
            self.ctx.term()
            self.pub = None
        if self.server is not None:
            self.server.close()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.server.wait_closed(), 2)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        from urllib.parse import parse_qs, urlsplit

        try:
            head = await reader.readuntil(b"\r\n\r\n")
            lines = head.decode("latin-1").split("\r\n")
            method, target, _ = lines[0].split(" ", 2)
            headers = {k.strip().lower(): v.strip()
                       for k, _, v in (line.partition(":") for line in lines[1:]) if k}  # fmt: skip
            length = int(headers.get("content-length") or 0)
            raw = await reader.readexactly(length) if length else b""
            url = urlsplit(target)
            body = json.loads(raw) if raw else None
            self.calls.append((method, url.path, body, headers.get("authorization")))
            status, payload = 200, {"ok": True}
            if url.path.startswith("/claims/"):
                status = self.status.get(method, 200)
                payload = {"ok": True} if status == 200 else {"error": "over the quota"}
            elif url.path == "/health":
                payload = self.health
            elif url.path == "/snapshot":
                keys = ",".join(parse_qs(url.query).get("keys", [])).split(",")
                payload = {"ticks": {k: self.snapshots.get(k) for k in keys if k}}
            data = json.dumps(payload).encode()
            writer.write(f"HTTP/1.1 {status} X\r\nContent-Type: application/json\r\n"
                         f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode() + data)  # fmt: skip
            await writer.drain()
        finally:
            writer.close()


def frame(ltp: float, close: float = 100.0, ltt: int = 1_763_265_600_000) -> dict:
    """A feed frame in ltpc mode."""
    return {"ltp": ltp, "cp": close, "ltt": ltt, "ltq": 1, "ts_recv": ltt + 5}


# ---- F7: OpenAlgo's internal watchlist route ------------------------------------------------------
def list_row(n: int, exchange: str = "NSE") -> dict[str, Any]:
    """A row of a shared list as OpenAlgo's internal route answers it (synthetic)."""
    return {"symbol": f"LIST{n}", "exchange": exchange, "instrument_key": f"NSE_EQ|LIST{n:07d}",
            "name": f"List Stock {n}", "tick_size": 0.05, "lot_size": 1}  # fmt: skip


class FakeOpenAlgo:
    """OpenAlgo's internal watchlist route as the family service meets it: the seal opened with the
    internal key (60 s, once), op ``watchlists``, principal ``sudarshan-family``, the member it is
    for named. Like the real one (review M3), it answers only the lists its own note (``shared``:
    ``("member", id, list)`` or ``("role", role, list)``) gives that member or role, and every
    other id as gone (``("all", None, list)``: every member's). ``down``: each request is answered
    500. ``calls`` holds what each asked."""

    def __init__(self, fernet: Fernet):
        self.fernet = fernet
        self.lists: dict[int, dict[str, Any]] = {}
        self.shared: set[tuple[str, Any, int]] = set()
        self.calls: list[dict[str, Any]] = []
        self.refused: list[str] = []
        self.down = False
        self.nonces = NonceBook(4096)
        self.server: asyncio.Server | None = None

    def add(self, list_id: int, name: str, rows: list[dict[str, Any]]) -> None:
        self.lists[list_id] = {"name": name, "created_at": "2026-11-02 09:15:00", "rows": rows}

    async def start(self) -> str:
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        return f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"

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
            length = next(int(v) for k, _, v in (line.partition(":") for line in lines[1:])
                          if k.strip().lower() == "content-length")  # fmt: skip
            body = await reader.readexactly(length)
            status, payload = self._judge(body) if path == INTERNAL_PATH else (404, {})
            data = json.dumps(payload).encode()
            writer.write(f"HTTP/1.1 {status} X\r\nContent-Type: application/json\r\n"
                         f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode() + data)  # fmt: skip
            await writer.drain()
        finally:
            writer.close()

    def _judge(self, body: bytes) -> tuple[int, Any]:
        refused = {"status": "error", "code": "forbidden", "message": "refused"}
        try:
            message = open_sealed(self.fernet, body, op="watchlists", seen=self.nonces)
        except HandoffRejected as exc:
            self.refused.append(str(exc))
            return 403, refused
        if message.get("principal") != "sudarshan-family":
            self.refused.append("principal")
            return 403, refused
        member_id, role, ids = message.get("member_id"), message.get("role"), message["list_ids"]
        self.calls.append({"list_ids": ids, "member_id": member_id, "role": role})
        if self.down:
            return 500, {"status": "error", "code": "failed", "message": "Not now."}
        found, gone = [], []
        for list_id in ids:
            mine = {("member", member_id, list_id), ("role", role, list_id),
                    ("all", None, list_id)} & self.shared  # fmt: skip
            if list_id in self.lists and mine:
                found.append({"id": list_id, **self.lists[list_id]})
            else:
                gone.append(list_id)
        return 200, {"status": "success", "data": {"lists": found, "gone": gone}}
