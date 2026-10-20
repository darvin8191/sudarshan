"""Test helpers: order-update payloads, a fake WebSocket proxy, a fake OpenAlgo /notify, and a
throw-away openalgo.db written with OpenAlgo's own models. Every key and name here is a fake."""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
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

    async def __aenter__(self) -> FakeProxy:
        self._server = await serve(self._handler, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc) -> None:
        self._server.close()
        await self._server.wait_closed()

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

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


class FakeNotify:
    """OpenAlgo's POST /api/v1/telegram/notify: records each body, answers as configured."""

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
        self.answers: list[tuple[int, dict]] = []  # used first, one per request
        self.default = self.SENT

    async def __aenter__(self) -> FakeNotify:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc) -> None:
        self._server.close()
        await self._server.wait_closed()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def messages(self) -> list[str]:
        return [body["message"] for body in self.bodies]

    async def _serve(self, reader, writer) -> None:
        head = await reader.readuntil(b"\r\n\r\n")
        request_line, *lines = head.decode("latin-1").split("\r\n")
        headers = {}
        for line in lines:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
        body = await reader.readexactly(int(headers.get("content-length") or 0))
        self.paths.append(request_line.split(" ")[1])
        payload = json.loads(body)
        self.bodies.append(payload)
        status, answer = self.answers.pop(0) if self.answers else self.default
        if status == 200 and payload.get("apikey") != self.key:
            status, answer = self.BAD_KEY
        data = json.dumps(answer).encode()
        writer.write(
            f"HTTP/1.1 {status} X\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode()
            + data
        )
        await writer.drain()
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
