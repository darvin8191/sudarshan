"""Fakes for the tests of the Upstox stream adapter, the connection pool and the proxy.

The real UpstoxWebSocketAdapter and UpstoxWebSocketClient run against UpstoxServer, a model of
Upstox's end of one market-data socket, inside a real ConnectionPool and a WebSocketProxy built
without __init__: no port, no ZeroMQ, no broker and no database.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections import defaultdict
from typing import Any

# The proxy package imports every broker adapter when it loads, so it has to load before any
# adapter module does, or the adapter's own import of it is circular.
import websocket_proxy  # noqa: F401
from websocket_proxy import broker_factory, connection_manager
from websocket_proxy import server as proxy_server
from websocket_proxy.connection_manager import ConnectionPool
from websocket_proxy.server import WebSocketProxy

# isort: split
from broker.upstox.streaming import upstox_adapter
from broker.upstox.streaming.upstox_adapter import UpstoxWebSocketAdapter
from broker.upstox.streaming.upstox_client import UpstoxWebSocketClient

USER = "owner"


class NoZmq:
    """Stands in for the shared publisher, so building an adapter opens no ZeroMQ socket."""

    socket = None
    zmq_port = 0
    context = None


class Socket:
    """websocket-client's socket as the client reads it (WebSocketApp.sock): open until closed."""

    def __init__(self) -> None:
        self.connected = True


class UpstoxServer:
    """Upstox's end of one market-data socket, written to by the real client.

    Applies each request as Upstox does: `sub` streams its keys in its mode (a key
    already streamed moves to it), `change_mode` moves keys already streamed, `unsub`
    stops them. `held` is what Upstox is sending: instrument key -> mode. `sock` is the
    socket as websocket-client exposes it; closing it (close_socket) makes Upstox forget
    every key, as the real one does.
    """

    def __init__(self) -> None:
        self.held: dict[str, str] = {}
        self.sent: list[tuple[str, list[str], str | None]] = []
        self.sock: Socket | None = Socket()

    def send(self, data: bytes, opcode: int | None = None) -> None:
        request = json.loads(data)
        method, keys = request["method"], request["data"]["instrumentKeys"]
        mode = request["data"].get("mode")
        self.sent.append((method, list(keys), mode))
        for key in keys:
            if method == "sub" or (method == "change_mode" and key in self.held):
                self.held[key] = mode
            elif method == "unsub":
                self.held.pop(key, None)

    @property
    def unsubscribed(self) -> list[list[str]]:
        return [keys for method, keys, _mode in self.sent if method == "unsub"]

    def close_socket(self) -> None:
        """The socket closed: Upstox forgets every key, and WebSocketApp clears `sock`."""
        self.held.clear()
        self.sock = None

    def close(self) -> None:
        self.close_socket()


class SocketThread:
    """Stands in for the client's socket thread: alive until a test ends it."""

    def __init__(self) -> None:
        self.alive = True

    def is_alive(self) -> bool:
        return self.alive


def connected_client() -> UpstoxWebSocketClient:
    """The production client, its socket replaced by an UpstoxServer."""
    client = UpstoxWebSocketClient("not-a-real-token")
    client.ws = UpstoxServer()
    client._connected = client.running = True
    client._ws_thread = SocketThread()
    return client


def map_symbols(monkeypatch, keys: dict[tuple[str, str], str]) -> None:
    """SymbolMapper answers from `keys`, (symbol, exchange) -> instrument key."""
    monkeypatch.setattr(
        upstox_adapter.SymbolMapper,
        "get_token_from_symbol",
        staticmethod(
            lambda symbol, exchange: {
                "token": keys[(symbol, exchange)],
                "brexchange": keys[(symbol, exchange)].split("|")[0],
            }
        ),
    )


def build_adapter() -> UpstoxWebSocketAdapter:
    """A real adapter with no ZeroMQ publisher, its client talking to `adapter.upstox`.

    Batched subscribes go out at once instead of on a 0.5 s timer thread; a test that
    holds the batch replaces `_start_batch_timer`.
    """
    context = connection_manager._pooled_creation_context
    context.active, context.shared_publisher = True, NoZmq()
    try:
        adapter = UpstoxWebSocketAdapter()
    finally:
        context.active, context.shared_publisher = False, None
    adapter.published = []
    adapter.publish_market_data = lambda topic, data: adapter.published.append((topic, data))
    adapter.ws_client = connected_client()
    adapter.upstox = adapter.ws_client.ws
    adapter._start_batch_timer = adapter._process_batch_subscriptions
    adapter.connected = True
    return adapter


def pool_around(adapter) -> ConnectionPool:
    """A ConnectionPool around one adapter, built without a ZeroMQ publisher."""
    pool = ConnectionPool.__new__(ConnectionPool)
    pool.lock = threading.RLock()
    pool.logger = logging.getLogger("test.sudarshan.pool")
    pool.broker_name, pool.user_id = "upstox", USER
    pool.adapters, pool.adapter_symbol_counts = [adapter], [0]
    pool.max_symbols, pool.max_connections = 1000, 2
    pool.subscription_map, pool.subscription_depths = {}, {}
    pool.peak_total_symbols, pool.peak_connections_used, pool.peak_symbol_counts = 0, 0, []
    pool.initialized = pool.connected = True
    return pool


def pooled(adapter: UpstoxWebSocketAdapter):
    """The proxy's pooled adapter wrapper around a pool of `adapter`."""
    wrapper = broker_factory._PooledAdapterWrapper(UpstoxWebSocketAdapter, "upstox")
    wrapper._pool, wrapper._user_id = pool_around(adapter), USER
    return wrapper


class ClientSocket:
    """A browser's WebSocket as the proxy sees it."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, text: str) -> None:
        self.sent.append(json.loads(text))


class HeldSleep:
    """The linger's sleep, held until the test lets it run out."""

    def __init__(self) -> None:
        self.gates: list[asyncio.Event] = []

    async def __call__(self, _seconds: float) -> None:
        gate = asyncio.Event()
        self.gates.append(gate)
        await gate.wait()


def lingering_proxy(monkeypatch, wrapper) -> WebSocketProxy:
    """A proxy built without __init__, whose broker adapter is `wrapper`, lingering 300 s."""
    monkeypatch.setattr(proxy_server, "verify_api_key", lambda _key: USER)
    monkeypatch.setattr(proxy_server, "get_broker_name", lambda _key: "upstox")
    monkeypatch.setattr(proxy_server, "create_broker_adapter", lambda _broker: wrapper)
    proxy = WebSocketProxy.__new__(WebSocketProxy)
    proxy.clients, proxy.subscriptions, proxy.user_mapping = {}, {}, {}
    proxy.user_broker_mapping, proxy.broker_adapters = {}, {}
    proxy.subscription_index = defaultdict(set)
    proxy.order_subscribers = defaultdict(set)
    proxy.broker_linger_seconds = 300.0
    proxy._linger_sleep = HeldSleep()
    return proxy
