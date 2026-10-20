"""One slow client must not delay the others (SUDARSHAN M18.2).

The proxy used to await every client's send for a tick before reading the next one, and a send
waits whenever that client's socket buffer is full. One phone on a bad network therefore held
back every screen's ticks until keepalive gave up on it. Each client now has its own outbox and
sender; the fan-out only queues.

These tests run the proxy's real handle_client and zmq_listener against fake browser
connections and a fake ZeroMQ socket, so they hold against the old fan-out too: there the
stalled client blocks the listener and the run times out.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from typing import Any

import pytest

import websocket_proxy  # noqa: F401  (loads the adapter registry before anything else)
from websocket_proxy import server as proxy_server
from websocket_proxy.server import WebSocketProxy

USER = "owner"
KEY = ("RELIANCE", "NSE", 2)
ROW = {"symbol": "RELIANCE", "exchange": "NSE", "mode": 2, "depth_level": 5, "broker": "upstox"}


class _Browser:
    """A browser connection as the proxy sees it: messages in, sends out, a transport."""

    path = "/"

    def __init__(self, *, stall: bool = False, delay: float = 0.0) -> None:
        self.sent: list[dict[str, Any]] = []
        self.stall = stall
        self.delay = delay
        self.aborted = 0
        self.inbox: asyncio.Queue[str] = asyncio.Queue()
        self.gone = asyncio.Event()
        self.transport = self  # websockets keeps abort() on the transport

    def abort(self) -> None:
        """The connection dropped without a closing handshake."""
        self.aborted += 1
        self.gone.set()

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.gone.set()

    async def send(self, text: str) -> None:
        if self.stall:
            await asyncio.Event().wait()  # a socket that takes nothing, ever
        if self.delay:
            await asyncio.sleep(self.delay)
        self.sent.append(json.loads(text))

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        getter = asyncio.ensure_future(self.inbox.get())
        gone = asyncio.ensure_future(self.gone.wait())
        await asyncio.wait({getter, gone}, return_when=asyncio.FIRST_COMPLETED)
        for pending in (getter, gone):
            pending.cancel()
        if getter.done() and not getter.cancelled():
            return getter.result()
        raise StopAsyncIteration


class _Sub:
    """The proxy's ZeroMQ SUB: replays frames, then ends the listener."""

    def __init__(self, proxy: WebSocketProxy, frames: list[tuple[str, dict[str, Any]]]):
        self.proxy = proxy
        self.frames = [[t.encode(), json.dumps(p).encode()] for t, p in frames]

    async def recv_multipart(self):
        if self.frames:
            return self.frames.pop(0)
        self.proxy.running = False
        raise TimeoutError


class _MarketDataServiceStub:
    def process_market_data(self, _data: dict[str, Any]) -> bool:
        return True


def _tick(ltp: float) -> tuple[str, dict[str, Any]]:
    return "NSE_RELIANCE_QUOTE", {"symbol": "RELIANCE", "exchange": "NSE", "ltp": ltp}


def _bare_proxy() -> WebSocketProxy:
    """A proxy built without __init__: no ports, no ZeroMQ, no broker."""
    proxy = WebSocketProxy.__new__(WebSocketProxy)
    proxy.running = True
    proxy.clients = {}
    proxy.subscriptions = {}
    proxy.user_mapping = {}
    proxy.user_broker_mapping = {USER: "upstox"}
    proxy.broker_adapters = {}
    proxy.subscription_index = defaultdict(set)
    proxy.order_subscribers = defaultdict(set)
    proxy.last_message_time = {}
    proxy.last_tick_time = {}
    proxy._last_stale_warn = {}
    proxy._stale_tick_warn_seconds = 120
    proxy._last_stale_check = proxy._last_cleanup_time = time.time()
    proxy._stale_check_interval = 30
    proxy._cleanup_interval = 300
    proxy._throttle_entry_max_age = 60
    proxy._messages_processed = 0
    return proxy


async def _settle(rounds: int = 20) -> None:
    for _ in range(rounds):
        await asyncio.sleep(0)


async def _serve(proxy: WebSocketProxy, *browsers: _Browser) -> list[asyncio.Task]:
    """Connect each browser through handle_client, signed in and subscribed to KEY."""
    handlers = [asyncio.create_task(proxy.handle_client(browser)) for browser in browsers]
    await _settle()
    for browser in browsers:
        client_id = id(browser)
        assert client_id in proxy.clients
        proxy.user_mapping[client_id] = USER
        proxy.subscriptions[client_id] = {json.dumps(ROW)}
        proxy.subscription_index[KEY].add(client_id)
    return handlers


async def _listen(proxy: WebSocketProxy, frames, within: float = 5.0) -> float:
    """Run the listener over `frames`; fail if it cannot finish within `within` seconds."""
    proxy.running = True
    proxy.socket = _Sub(proxy, frames)
    started = time.perf_counter()
    await asyncio.wait_for(proxy.zmq_listener(), within)
    return time.perf_counter() - started


async def _hang_up(proxy: WebSocketProxy, handlers, *browsers: _Browser) -> None:
    for browser in browsers:
        browser.gone.set()
    await asyncio.wait_for(asyncio.gather(*handlers), 5)


@pytest.fixture(autouse=True)
def _no_market_data_service(monkeypatch):
    monkeypatch.setattr(proxy_server, "get_market_data_service", _MarketDataServiceStub)


def test_a_stalled_client_does_not_delay_a_healthy_one() -> None:
    async def scenario() -> None:
        proxy = _bare_proxy()
        proxy.CLIENT_OUTBOX_LIMIT = 4
        proxy.CLIENT_SEND_STALL_SECONDS = 30.0
        healthy, stalled = _Browser(), _Browser(stall=True)
        handlers = await _serve(proxy, healthy, stalled)
        outbox = proxy.__dict__.get("_client_outboxes", {}).get(id(stalled))

        took = await _listen(proxy, [_tick(n) for n in range(1, 21)])
        await _settle()

        assert [m["data"]["ltp"] for m in healthy.sent] == list(range(1, 21))
        assert all(m["mode"] == 2 for m in healthy.sent)
        assert took < 2.0
        # Twenty ticks behind with room for four: dropped without a closing
        # handshake, so its browser reconnects and resubscribes.
        assert stalled.aborted == 1
        assert id(stalled) not in proxy.clients  # cleanup_client ran for it
        assert id(stalled) not in proxy.subscription_index.get(KEY, set())
        # Its sender was stopped mid-send, cleanly: cancelled, not failed.
        assert outbox is not None and outbox.task.cancelled()

        await _hang_up(proxy, handlers, healthy)

    asyncio.run(scenario())


def test_a_socket_that_takes_nothing_is_dropped_after_the_stall_limit() -> None:
    async def scenario() -> None:
        proxy = _bare_proxy()
        proxy.CLIENT_SEND_STALL_SECONDS = 0.2
        healthy, stalled = _Browser(), _Browser(stall=True)
        handlers = await _serve(proxy, healthy, stalled)

        took = await _listen(proxy, [_tick(101.5)])
        assert took < 0.2  # the listener never waited for the stalled socket
        assert [m["data"]["ltp"] for m in healthy.sent] == [101.5]
        assert stalled.aborted == 0

        await asyncio.sleep(0.4)
        assert stalled.aborted == 1

        await _hang_up(proxy, handlers, healthy)

    asyncio.run(scenario())


def test_a_slow_client_that_keeps_up_gets_every_tick_in_order() -> None:
    async def scenario() -> None:
        proxy = _bare_proxy()
        proxy.CLIENT_OUTBOX_LIMIT = 64
        slow = _Browser(delay=0.002)
        handlers = await _serve(proxy, slow)

        await _listen(proxy, [_tick(n) for n in range(1, 51)])
        await asyncio.sleep(0.5)

        assert [m["data"]["ltp"] for m in slow.sent] == list(range(1, 51))
        assert slow.aborted == 0

        await _hang_up(proxy, handlers, slow)

    asyncio.run(scenario())


def test_replies_and_ticks_reach_a_client_in_the_order_they_were_sent() -> None:
    async def scenario() -> None:
        proxy = _bare_proxy()
        browser = _Browser()
        handlers = await _serve(proxy, browser)

        await _listen(proxy, [_tick(1.0)])
        await proxy.send_message(id(browser), {"type": "subscribe", "status": "success"})
        await _listen(proxy, [_tick(2.0)])
        await _settle()

        assert [m.get("type") for m in browser.sent] == ["market_data", "subscribe", "market_data"]

        await _hang_up(proxy, handlers, browser)

    asyncio.run(scenario())


def test_a_control_reply_still_reaches_the_client() -> None:
    async def scenario() -> None:
        proxy = _bare_proxy()
        browser = _Browser()
        handlers = await _serve(proxy, browser)

        await browser.inbox.put(json.dumps({"action": "ping", "timestamp": 1}))
        await _settle()

        assert browser.sent[-1]["type"] == "pong"
        await _hang_up(proxy, handlers, browser)

    asyncio.run(scenario())


def test_a_client_leaving_ends_its_outbox() -> None:
    async def scenario() -> None:
        proxy = _bare_proxy()
        browser = _Browser()
        handlers = await _serve(proxy, browser)
        outboxes = proxy.__dict__.get("_client_outboxes", {})
        assert set(outboxes) == {id(browser)}
        sender = outboxes[id(browser)].task

        await _hang_up(proxy, handlers, browser)
        await _settle()

        assert outboxes == {}
        assert sender.done()

    asyncio.run(scenario())


def test_many_clients_coming_and_going_leave_no_outbox_or_task_behind() -> None:
    async def scenario() -> None:
        proxy = _bare_proxy()
        baseline = len(asyncio.all_tasks())
        for _ in range(100):
            browser = _Browser()
            handlers = await _serve(proxy, browser)
            await _listen(proxy, [_tick(1.0)])
            await _hang_up(proxy, handlers, browser)
        await _settle()

        assert proxy.__dict__.get("_client_outboxes", {}) == {}
        assert proxy.clients == {}
        assert len(asyncio.all_tasks()) == baseline

    asyncio.run(scenario())
