"""Test helpers: order-update payloads, a fake WebSocket proxy, a fake OpenAlgo /notify, and a
throw-away openalgo.db written with OpenAlgo's own models. Every key and name here is a fake."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from sudarshan.autologin.schedule import IST

OWNER = "owner"
API_KEY = "test-api-key-" + "0" * 40  # never a real key  # pragma: allowlist secret
NEW_API_KEY = "test-api-key-" + "1" * 40  # pragma: allowlist secret
OPTION = "NIFTY21OCT2625900CE"


def ist(y: int, mo: int, d: int, h: int = 0, mi: int = 0, s: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, s, tzinfo=IST)


class RunningClock:
    """Real time's pace from a fixed start, for a service that runs on its own in a test.

    Reconnects, pauses and the outbox pace still take real time, but the date the service reads is
    the test's. An alert names an expiry's year only when it is not the current one, so on the real
    clock a line expected as "NIFTY 21 OCT 25900 CE" turns into "NIFTY 21 OCT 2026 25900 CE" on
    1 January 2027.
    """

    def __init__(self, start: datetime):
        self.start, self._began = start, time.monotonic()

    def __call__(self) -> datetime:
        return self.start + timedelta(seconds=time.monotonic() - self._began)


def this_years_option() -> str:
    """OPTION expiring this year, for a service in another process, which reads the real clock."""
    return f"NIFTY21OCT{datetime.now(IST):%y}25900CE"


def update(order_id: str = "251021000000001", status: str = "complete", **fields) -> dict:
    """An order_update message as OpenAlgo's proxy relays it (subscribers/wsproxy_subscriber)."""
    message = {
        "type": "order_update",
        "user_id": OWNER,
        "mode": "live",
        "broker": "upstox",
        "orderid": order_id,
        "symbol": OPTION,
        "exchange": "NFO",
        "action": "BUY",
        "quantity": 65,
        "price": 0.0,
        "trigger_price": 0.0,
        "pricetype": "MARKET",
        "product": "MIS",
        "order_status": status,
        "filled_quantity": 65 if status == "complete" else 0,
        "pending_quantity": 0,
        "average_price": 123.45 if status == "complete" else 0.0,
        "rejection_reason": "",
    }
    message.update(fields)
    return message


async def wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


class FakeProxy:
    """OpenAlgo's WebSocket proxy as this service sees it (websocket_proxy/server.py).

    ``authenticate`` answers success for ``key``, AUTHENTICATION_ERROR otherwise, or the broker
    error in ``broker_error`` after mapping the client (as the real proxy does);
    ``subscribe_orders`` answers with an ack carrying the request id.
    """

    def __init__(self, key: str = API_KEY):
        self.key = key
        self.broker_error: str | None = None
        self.connections = 0
        self.keys_seen: list[str] = []
        self.subscribed: list = []
        self.close_codes: list = []
        self.refusing = False  # answer every new connection with 503 (OpenAlgo down)

    async def __aenter__(self) -> FakeProxy:
        self._server = await serve(self._handler, "127.0.0.1", 0, process_request=self._gate)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc) -> None:
        self._server.close()
        await self._server.wait_closed()

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    def _gate(self, connection, request):
        return connection.respond(503, "down") if self.refusing else None

    async def outage(self, service) -> None:
        """Cut every connection and refuse new ones until ``refusing`` is cleared."""
        self.refusing = True
        await self.drop_all()
        deadline = time.monotonic() + 5
        while service.state == "connected":
            if time.monotonic() > deadline:
                raise AssertionError("the service did not notice the drop")
            await asyncio.sleep(0.01)

    async def push(self, message: dict) -> None:
        for ws in list(self.subscribed):
            await ws.send(json.dumps(message))

    async def drop_all(self) -> None:
        """Cut every connection without a close frame (a crashed proxy)."""
        for ws in list(self.subscribed):
            ws.transport.abort()

    async def _handler(self, ws) -> None:
        self.connections += 1
        authenticated = False
        try:
            async for raw in ws:
                data = json.loads(raw)
                action = data.get("action")
                if action == "authenticate":
                    key = data.get("api_key", "")
                    self.keys_seen.append(key)
                    if key != self.key:
                        error = {"status": "error", "code": "AUTHENTICATION_ERROR",
                                 "message": "Invalid API key"}  # fmt: skip
                        await ws.send(json.dumps(error))
                        continue
                    authenticated = True
                    if self.broker_error:
                        error = {"status": "error", "code": self.broker_error,
                                 "message": "Failed to connect to broker"}  # fmt: skip
                        await ws.send(json.dumps(error))
                        continue
                    await ws.send(json.dumps({
                        "type": "auth", "status": "success", "message": "Authentication successful",
                        "broker": "upstox", "user_id": OWNER,
                        "supported_features": {"ltp": True, "quote": True, "depth": True},
                    }))  # fmt: skip
                elif action == "subscribe_orders":
                    if not authenticated:
                        error = {"status": "error", "code": "NOT_AUTHENTICATED",
                                 "message": "You must authenticate first",
                                 "request_id": data.get("request_id")}  # fmt: skip
                        await ws.send(json.dumps(error))
                        continue
                    self.subscribed.append(ws)
                    await ws.send(json.dumps({
                        "type": "subscribe_orders", "status": "success",
                        "message": "Subscribed to order updates",
                        "request_id": data.get("request_id"),
                    }))  # fmt: skip
        except ConnectionClosed:
            pass
        finally:
            if ws in self.subscribed:
                self.subscribed.remove(ws)
            self.close_codes.append(ws.close_code)


def book_row(order_id: str, status: str = "complete", **fields) -> dict:
    """An order book row as OpenAlgo's REST order book gives it for Upstox.

    No fill fields (they come from the trade book); a rejected row may also carry
    rejection_reason, when Upstox sent a message with it.
    """
    row = {
        "action": "BUY",
        "symbol": OPTION,
        "exchange": "NFO",
        "orderid": order_id,
        "product": "MIS",
        "quantity": 65,
        "price": 0.0,
        "pricetype": "MARKET",
        "order_status": status,
        "trigger_price": 0.0,
        "timestamp": "21-Oct-2026 10:42:05",
    }
    row.update(fields)
    return row


def trade_row(order_id: str, quantity: int = 65, price: float = 123.45) -> dict:
    """A trade book row as OpenAlgo's REST trade book gives it (one per trade)."""
    return {
        "symbol": OPTION,
        "exchange": "NFO",
        "product": "MIS",
        "action": "BUY",
        "quantity": quantity,
        "average_price": price,
        "trade_value": quantity * price,
        "orderid": order_id,
        "timestamp": "10:42:05",
    }


class FakeOpenAlgo:
    """OpenAlgo's REST on 127.0.0.1 as this service uses it.

    ``/api/v1/telegram/notify`` records each body in ``bodies`` and answers as configured;
    ``/api/v1/orderbook`` and ``/api/v1/tradebook`` serve ``orders`` and ``trades`` (with
    ``"mode": "analyze"`` when ``analyze``), or fail with ``book_status``. The market alerts'
    push (M10.7) is opened with the push key, its items recorded in ``pushes``, and answered
    with ``screens`` (or ``push_status``).
    """

    SENT = (200, {"status": "success", "message": "Notification sent successfully"})
    QUEUED = (200, {"status": "success", "message": "Notification queued for delivery"})
    STOPPED = (409, {"status": "error", "message": "Telegram bot is stopped."})
    NOT_LINKED = (404, {"status": "error", "message": "User not found or not linked"})
    BAD_KEY = (401, {"status": "error", "message": "Invalid or missing API key"})
    DOWN = (500, {"status": "error", "message": "Failed to send notification"})

    def __init__(self, key: str = API_KEY):
        self.key = key
        self.bodies: list[dict] = []
        self.paths: list[str] = []
        self.answers: list[tuple[int, dict]] = []  # used first, one per notify request
        self.default = self.SENT
        self.orders: list[dict] = []
        self.trades: list[dict] = []
        self.analyze = False
        self.book_status = 200
        self.pushes: list[list[dict]] = []
        self.screens = 1
        self.push_status = 200
        self._writers: set = set()

    async def __aenter__(self) -> FakeOpenAlgo:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc) -> None:
        # wait_closed() waits for every client connection (Python 3.12+): one its client never
        # closes would hang the run, so they get a moment to finish and are then cut.
        deadline = time.monotonic() + 2
        while self._writers and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        self._server.close()
        for writer in list(self._writers):
            writer.transport.abort()
        await asyncio.wait_for(self._server.wait_closed(), 5)

    @property
    def open_connections(self) -> int:
        """Client connections this server still holds open."""
        return len(self._writers)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def messages(self) -> list[str]:
        return [body["message"] for body in self.bodies]

    def book_reads(self) -> int:
        return self.paths.count("/api/v1/orderbook")

    def _answer(self, path: str, payload: dict) -> tuple[int, dict]:
        if path == "/api/v1/telegram/notify":
            self.bodies.append(payload)
            status, answer = self.answers.pop(0) if self.answers else self.default
        elif path in ("/api/v1/orderbook", "/api/v1/tradebook"):
            if self.book_status != 200:
                return self.book_status, {"status": "error", "message": "broker error"}
            data = {"orders": self.orders, "statistics": {}} if "order" in path else self.trades
            status, answer = 200, {"status": "success", "data": data}
            if self.analyze:
                answer["mode"] = "analyze"
        else:
            return 404, {"status": "error", "message": "not found"}
        if status == 200 and payload.get("apikey") != self.key:
            return self.BAD_KEY
        return status, answer

    def _push(self, body: bytes) -> tuple[int, dict]:
        import os

        from sudarshan.autologin.handoff import HandoffRejected, open_sealed
        from sudarshan.vault import ALERTS_PUSH_INFO, fernet_for

        fernet = fernet_for(os.environ["API_KEY_PEPPER"], ALERTS_PUSH_INFO)
        try:
            message = open_sealed(fernet, body, op="push", seen={}, max_bytes=128 * 1024)
        except HandoffRejected:
            return 403, {"status": "error", "message": "Forbidden"}
        self.pushes.append(message["items"])
        if self.push_status != 200:
            return self.push_status, {"status": "error", "message": "refused"}
        return 200, {"status": "success", "screens": self.screens}

    async def _serve(self, reader, writer) -> None:
        self._writers.add(writer)
        try:
            await self._exchange(reader, writer)
        finally:
            self._writers.discard(writer)

    async def _exchange(self, reader, writer) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            request_line, *lines = head.decode("latin-1").split("\r\n")
            headers = {}
            for line in lines:
                name, _, value = line.partition(":")
                headers[name.strip().lower()] = value.strip()
            body = await reader.readexactly(int(headers.get("content-length") or 0))
        except (asyncio.IncompleteReadError, ConnectionError):
            writer.close()  # a client that went away before asking, or a cut at the end
            return
        path = request_line.split(" ")[1]
        self.paths.append(path)
        if path == "/sudarshan/alerts/internal/push":
            status, answer = self._push(body)
        else:
            status, answer = self._answer(path, json.loads(body))
        data = json.dumps(answer).encode()
        try:
            writer.write(
                f"HTTP/1.1 {status} X\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode()
                + data
            )
            await writer.drain()
        except ConnectionError:
            pass  # the client went away before the answer
        writer.close()


class FakeOpenAlgoDb:
    """openalgo.db with OpenAlgo's users and api_keys tables, written as OpenAlgo writes them.

    The rows are written through a separate, writable engine; the service under test reads the
    file read-only. The admin user is ``OWNER``; ``set_key`` stores a key the way
    ``auth_db.upsert_api_key`` does (hash for checking, Fernet ciphertext for reading back).
    """

    def __init__(self, directory: Path, *, with_user: bool = True):
        from database import auth_db, user_db
        from database.engine_factory import create_db_engine

        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "openalgo.db"
        self._auth, self._users = auth_db, user_db
        self.engine = create_db_engine(f"sqlite:///{self.path}")
        user_db.Base.metadata.create_all(self.engine, tables=[user_db.User.__table__])
        auth_db.Base.metadata.create_all(self.engine, tables=[auth_db.ApiKeys.__table__])
        if with_user:
            self._write(
                user_db.User(
                    username=OWNER,
                    email="owner@example.invalid",
                    password_hash="not-a-real-hash",  # pragma: allowlist secret
                    totp_secret="not-a-real-secret",  # pragma: allowlist secret
                    is_admin=True,
                )
            )

    def _write(self, row) -> None:
        from sqlalchemy.orm import Session

        with Session(self.engine) as session:
            session.merge(row)
            session.commit()

    def set_key(self, key: str, *, ciphertext: str | None = None) -> None:
        from sqlalchemy.orm import Session

        encrypted = ciphertext or self._auth.encrypt_token(key)
        with Session(self.engine) as session:
            row = session.query(self._auth.ApiKeys).filter_by(user_id=OWNER).first()
            if row is None:
                row = self._auth.ApiKeys(user_id=OWNER, api_key_hash="not-a-real-hash")
                session.add(row)
            row.api_key_encrypted = encrypted
            session.commit()

    def close(self) -> None:
        self.engine.dispose()
