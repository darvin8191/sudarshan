"""WEBSOCKET_BROKER_LINGER_SECONDS: the broker connection outlives a brief client gap (M18.2).

A phone closes its live connection 5 s after the screen turns off. Without the linger the proxy
then disconnected the user's broker adapter at once, so every screen-on opened a new Upstox
socket. These tests drive the proxy's own client lifecycle (authenticate, subscribe, cleanup)
against a fake adapter, with the linger's sleep replaced by one the test releases, so time is
controlled and nothing waits on a real clock.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Any

import pytest

import websocket_proxy  # noqa: F401  (loads the adapter registry before anything else)
from websocket_proxy import broker_factory
from websocket_proxy import server as proxy_server
from websocket_proxy.server import WebSocketProxy

USER = "owner"
LINGER = 300.0


class _Adapter:
    """A broker adapter as the proxy uses it: idle once every subscription is released."""

    def __init__(self) -> None:
        self.subscriptions: dict[str, dict[str, Any]] = {}
        self.connected = True
        self.disconnects = 0
        self.unsubscribe_reply: dict[str, Any] = {"status": "success"}

    def initialize(self, broker_name: str, user_id: str, *args, **kwargs) -> dict[str, Any]:
        return {"status": "success"}

    def connect(self) -> dict[str, Any]:
        return {"status": "success"}

    def subscribe(self, symbol: str, exchange: str, mode: int, depth: int) -> dict[str, Any]:
        self.subscriptions[f"{symbol}_{exchange}_{mode}"] = {"symbol": symbol, "mode": mode}
        return {"status": "success"}

    def unsubscribe(self, symbol: str, exchange: str, mode: int) -> dict[str, Any]:
        if self.unsubscribe_reply.get("status") == "success":
            self.subscriptions.pop(f"{symbol}_{exchange}_{mode}", None)
        return self.unsubscribe_reply

    def disconnect(self) -> None:
        self.disconnects += 1
        self.connected = False

    def is_auth_error(self, _message: str) -> bool:
        return False


class _Browser:
    """A client's WebSocket as the proxy sees it."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send(self, text: str) -> None:
        self.sent.append(json.loads(text))


class _ControlledSleep:
    """Stands in for asyncio.sleep in the linger: returns only when the test says so."""

    def __init__(self) -> None:
        self.requested: list[float] = []
        self._gates: list[asyncio.Event] = []

    async def __call__(self, seconds: float) -> None:
        self.requested.append(seconds)
        gate = asyncio.Event()
        self._gates.append(gate)
        await gate.wait()

    async def elapse(self) -> None:
        """Let every pending linger sleep run out, then let the loop act on it."""
        await _settle()  # a linger started just now first has to reach its sleep
        for gate in self._gates:
            gate.set()
        await _settle()


async def _settle() -> None:
    for _ in range(10):
        await asyncio.sleep(0)


@pytest.fixture
def lingering_proxy(monkeypatch):
    """A proxy built without __init__ (no ports), wired to fakes, with the linger set."""
    created: list[_Adapter] = []

    def create(_broker_name: str) -> _Adapter:
        adapter = _Adapter()
        created.append(adapter)
        return adapter

    monkeypatch.setattr(proxy_server, "verify_api_key", lambda _key: USER)
    monkeypatch.setattr(proxy_server, "get_broker_name", lambda _key: "upstox")
    monkeypatch.setattr(proxy_server, "create_broker_adapter", create)

    def build(linger: float = LINGER) -> tuple[WebSocketProxy, list[_Adapter], _ControlledSleep]:
        proxy = WebSocketProxy.__new__(WebSocketProxy)
        proxy.clients = {}
        proxy.subscriptions = {}
        proxy.user_mapping = {}
        proxy.user_broker_mapping = {}
        proxy.broker_adapters = {}
        proxy.subscription_index = defaultdict(set)
        proxy.order_subscribers = defaultdict(set)
        proxy.broker_linger_seconds = linger
        sleep = _ControlledSleep()
        proxy._linger_sleep = sleep
        return proxy, created, sleep

    return build


async def _connect(proxy: WebSocketProxy, client_id: int) -> _Browser:
    """What handle_client does for a new browser, then its authenticate message."""
    browser = _Browser()
    proxy.clients[client_id] = browser
    proxy.subscriptions[client_id] = set()
    await proxy.authenticate_client(client_id, {"action": "authenticate", "api_key": "k"})
    assert browser.sent[-1]["status"] == "success", browser.sent[-1]
    return browser


async def _subscribe(proxy: WebSocketProxy, client_id: int, symbol: str = "RELIANCE") -> None:
    request = {"symbols": [{"symbol": symbol, "exchange": "NSE"}], "mode": "Quote"}
    await proxy.subscribe_client(client_id, request)


async def _leave(proxy: WebSocketProxy, client_id: int) -> None:
    """What handle_client's finally does when a browser goes away."""
    await proxy.cleanup_client(client_id)


def test_the_last_client_leaving_keeps_the_broker_connection(lingering_proxy) -> None:
    proxy, created, sleep = lingering_proxy()

    async def scenario() -> None:
        await _connect(proxy, 7)
        await _subscribe(proxy, 7)
        await _leave(proxy, 7)
        await _settle()

        (adapter,) = created
        assert adapter.disconnects == 0
        assert proxy.broker_adapters[USER] is adapter
        assert proxy.user_broker_mapping[USER] == "upstox"
        assert sleep.requested == [LINGER]
        # Subscriptions were released as before: the idle socket holds no symbol.
        assert adapter.subscriptions == {}
        assert proxy.subscription_index == {}

    asyncio.run(scenario())


def test_a_client_returning_in_the_window_reuses_the_connection(lingering_proxy) -> None:
    proxy, created, sleep = lingering_proxy()

    async def scenario() -> None:
        await _connect(proxy, 7)
        await _subscribe(proxy, 7)
        await _leave(proxy, 7)
        await _settle()

        await _connect(proxy, 8)  # the phone's screen comes back on
        await _subscribe(proxy, 8)
        await sleep.elapse()  # the old window runs out with the screen back

        (adapter,) = created  # no second adapter, no second broker socket
        assert adapter.disconnects == 0
        assert proxy.broker_adapters[USER] is adapter
        assert "RELIANCE_NSE_2" in adapter.subscriptions

    asyncio.run(scenario())


def test_no_client_coming_back_disconnects_exactly_as_before(lingering_proxy) -> None:
    proxy, created, sleep = lingering_proxy()

    async def scenario() -> None:
        await _connect(proxy, 7)
        await _subscribe(proxy, 7)
        await _leave(proxy, 7)
        await sleep.elapse()

        (adapter,) = created
        assert adapter.disconnects == 1
        assert USER not in proxy.broker_adapters
        assert USER not in proxy.user_broker_mapping

        await _connect(proxy, 9)  # a later screen gets a fresh connection
        assert len(created) == 2

    asyncio.run(scenario())


def test_a_second_screen_leaving_while_one_stays_keeps_everything_as_before(
    lingering_proxy,
) -> None:
    proxy, created, sleep = lingering_proxy()

    async def scenario() -> None:
        await _connect(proxy, 7)
        await _connect(proxy, 8)
        await _leave(proxy, 7)
        await _settle()

        (adapter,) = created
        assert sleep.requested == []  # another screen is still live: no linger at all
        assert adapter.disconnects == 0

        await _leave(proxy, 8)
        await sleep.elapse()
        assert adapter.disconnects == 1

    asyncio.run(scenario())


def test_a_token_change_or_logout_during_the_window_tears_down_at_once(
    lingering_proxy,
) -> None:
    proxy, created, sleep = lingering_proxy()

    async def scenario() -> None:
        await _connect(proxy, 7)
        await _leave(proxy, 7)
        await _settle()
        (adapter,) = created

        # What upsert_auth's ZeroMQ CACHE_INVALIDATE_ALL message does in the proxy.
        proxy._handle_cache_invalidation(
            f"CACHE_INVALIDATE_ALL_{USER}", json.dumps({"user_id": USER, "cache_type": "ALL"})
        )
        assert adapter.disconnects == 1
        assert USER not in proxy.broker_adapters

        await sleep.elapse()  # the cancelled window does nothing more
        assert adapter.disconnects == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("caller", ["proxy loop", "another thread"])
def test_cleanup_pools_for_user_during_the_window_tears_down_at_once(
    lingering_proxy, caller: str
) -> None:
    proxy, created, sleep = lingering_proxy()
    broker_factory.add_pool_cleanup_listener(proxy._on_user_pools_cleaned)

    async def scenario() -> None:
        await _connect(proxy, 7)
        await _leave(proxy, 7)
        await _settle()
        (adapter,) = created

        if caller == "proxy loop":
            broker_factory.cleanup_pools_for_user(USER)
        else:  # the development server calls it from a request thread
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, broker_factory.cleanup_pools_for_user, USER)
        await _settle()

        assert adapter.disconnects == 1
        assert USER not in proxy.broker_adapters
        await sleep.elapse()
        assert adapter.disconnects == 1

    try:
        asyncio.run(scenario())
    finally:
        broker_factory.remove_pool_cleanup_listener(proxy._on_user_pools_cleaned)


def test_a_failed_release_disconnects_at_once_so_no_idle_socket_holds_a_symbol(
    lingering_proxy,
) -> None:
    proxy, created, sleep = lingering_proxy()

    async def scenario() -> None:
        await _connect(proxy, 7)
        await _subscribe(proxy, 7)
        (adapter,) = created
        adapter.unsubscribe_reply = {"status": "error", "message": "broker refused"}

        await _leave(proxy, 7)
        await _settle()

        assert sleep.requested == []
        assert adapter.disconnects == 1
        assert USER not in proxy.broker_adapters

    asyncio.run(scenario())


def test_an_adapter_still_holding_symbols_is_not_kept(lingering_proxy) -> None:
    proxy, created, sleep = lingering_proxy()

    async def scenario() -> None:
        await _connect(proxy, 7)
        (adapter,) = created
        adapter.subscriptions["ORPHAN_NSE_1"] = {"symbol": "ORPHAN", "mode": 1}

        await _leave(proxy, 7)
        await _settle()

        assert sleep.requested == []
        assert adapter.disconnects == 1

    asyncio.run(scenario())


def test_zero_disconnects_immediately_exactly_as_before(lingering_proxy) -> None:
    proxy, created, sleep = lingering_proxy(linger=0.0)

    async def scenario() -> None:
        await _connect(proxy, 7)
        await _subscribe(proxy, 7)
        await _leave(proxy, 7)

        (adapter,) = created
        assert adapter.disconnects == 1  # before any await: at once, as before
        assert USER not in proxy.broker_adapters
        assert USER not in proxy.user_broker_mapping
        assert sleep.requested == []
        assert proxy.__dict__.get("_broker_lingers", {}) == {}

    asyncio.run(scenario())


def test_a_proxy_built_without_init_defaults_to_no_linger() -> None:
    proxy = WebSocketProxy.__new__(WebSocketProxy)
    assert proxy.broker_linger_seconds == 0


@pytest.mark.parametrize(
    ("raw", "seconds"),
    [(None, 0.0), ("0", 0.0), ("300", 300.0), ("'90'", 90.0), ("1.5", 1.5), ("-5", 0.0),
     ("abc", 0.0), ("nan", 0.0), ("inf", 0.0)],
)  # fmt: skip
def test_the_setting_is_read_safely(monkeypatch, raw: str | None, seconds: float) -> None:
    if raw is None:
        monkeypatch.delenv("WEBSOCKET_BROKER_LINGER_SECONDS", raising=False)
    else:
        monkeypatch.setenv("WEBSOCKET_BROKER_LINGER_SECONDS", raw)
    assert proxy_server._broker_linger_seconds() == seconds


def test_many_screen_off_and_on_cycles_hold_one_connection_and_no_stray_tasks(
    lingering_proxy,
) -> None:
    """The resource check: 200 gaps cost one adapter, one pending linger, flat task count."""
    proxy, created, sleep = lingering_proxy()

    async def scenario() -> None:
        baseline = len(asyncio.all_tasks())
        for cycle in range(200):
            await _connect(proxy, 1000 + cycle)
            await _subscribe(proxy, 1000 + cycle, symbol=f"SYM{cycle % 7}")
            await _leave(proxy, 1000 + cycle)
            await _settle()
            assert len(proxy._broker_lingers) == 1
            assert len(asyncio.all_tasks()) == baseline + 1  # the one pending linger

        (adapter,) = created
        assert adapter.disconnects == 0
        assert adapter.subscriptions == {}
        assert proxy.clients == {} and proxy.user_mapping == {}

        await sleep.elapse()
        assert proxy._broker_lingers == {}
        assert len(asyncio.all_tasks()) == baseline
        assert adapter.disconnects == 1

    asyncio.run(scenario())


def test_a_discarded_proxy_is_not_kept_alive_by_its_listener() -> None:
    import gc
    import weakref

    proxy = WebSocketProxy.__new__(WebSocketProxy)
    before = len(broker_factory._POOL_CLEANUP_LISTENERS)
    broker_factory.add_pool_cleanup_listener(proxy._on_user_pools_cleaned)
    broker_factory.add_pool_cleanup_listener(proxy._on_user_pools_cleaned)  # once only
    assert len(broker_factory._POOL_CLEANUP_LISTENERS) == before + 1
    alive = weakref.ref(proxy)

    del proxy
    gc.collect()
    broker_factory.cleanup_pools_for_user(USER)

    assert alive() is None
    assert len(broker_factory._POOL_CLEANUP_LISTENERS) == before


def test_stop_cancels_a_pending_linger(lingering_proxy) -> None:
    proxy, created, sleep = lingering_proxy()

    async def scenario() -> None:
        await _connect(proxy, 7)
        await _leave(proxy, 7)
        await _settle()
        (task,) = [entry.task for entry in proxy._broker_lingers.values()]

        proxy._cancel_all_broker_lingers()
        await _settle()

        assert task.cancelled()
        assert proxy._broker_lingers == {}

    asyncio.run(scenario())
