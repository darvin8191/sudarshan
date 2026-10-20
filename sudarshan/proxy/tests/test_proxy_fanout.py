"""One slow client must not delay the others (SUDARSHAN M18.2).

The proxy used to await every client's send for a tick before reading the next one, and a send
waits whenever that client's socket buffer is full. One phone on a bad network therefore held
back every screen's ticks until keepalive gave up on it. Each client now has its own outbox and
sender; the fan-out only queues. A client that falls behind gets the newest tick per stream
instead of a backlog, and is dropped only once a message to it has waited the stall limit.

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
from websocket_proxy.sudarshan_ext import ClientOutbox, merge_ticks

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


def _tick(ltp: float, symbol: str = "RELIANCE") -> tuple[str, dict[str, Any]]:
    return f"NSE_{symbol}_QUOTE", {"symbol": symbol, "exchange": "NSE", "ltp": ltp}


def _ltps(browser: _Browser, symbol: str = "RELIANCE") -> list[float]:
    return [m["data"]["ltp"] for m in browser.sent if m.get("symbol") == symbol]


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


async def _serve(
    proxy: WebSocketProxy, *browsers: _Browser, symbols: tuple[str, ...] = ("RELIANCE",)
) -> list[asyncio.Task]:
    """Connect each browser through handle_client, signed in and subscribed (Quote)."""
    handlers = [asyncio.create_task(proxy.handle_client(browser)) for browser in browsers]
    await _settle()
    for browser in browsers:
        client_id = id(browser)
        assert client_id in proxy.clients
        proxy.user_mapping[client_id] = USER
        proxy.subscriptions[client_id] = {json.dumps(ROW | {"symbol": s}) for s in symbols}
        for symbol in symbols:
            proxy.subscription_index[(symbol, "NSE", 2)].add(client_id)
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
        proxy.CLIENT_SEND_STALL_SECONDS = 0.5
        healthy, stalled = _Browser(), _Browser(stall=True)
        handlers = await _serve(proxy, healthy, stalled)
        outbox = proxy.__dict__.get("_client_outboxes", {}).get(id(stalled))

        took = await _listen(proxy, [_tick(n) for n in range(1, 21)])
        await _settle()

        # The healthy client keeps up, so nothing of its waits to be merged.
        assert _ltps(healthy) == list(range(1, 21))
        assert all(m["mode"] == 2 for m in healthy.sent)
        assert took < 0.5  # the listener never waited for the stalled client
        # The stalled one holds one tick in flight and the newest one waiting.
        (waiting,) = outbox._queue
        assert waiting[1]["data"]["ltp"] == 20  # waits as a dict, JSON once sent

        await asyncio.sleep(0.8)
        # Dropped without a closing handshake, so its browser reconnects and
        # resubscribes; its sender ended by itself, with no error left behind.
        assert stalled.aborted == 1
        assert id(stalled) not in proxy.clients  # cleanup_client ran for it
        assert id(stalled) not in proxy.subscription_index.get(KEY, set())
        assert outbox.task.done() and not outbox.task.cancelled()
        assert outbox.task.exception() is None

        await _hang_up(proxy, handlers, healthy)

    asyncio.run(scenario())


def test_a_client_leaving_mid_send_stops_its_sender_cleanly() -> None:
    """Cancelled, not failed: no task exception is left unretrieved."""

    async def scenario() -> None:
        proxy = _bare_proxy()
        stalled = _Browser(stall=True)
        handlers = await _serve(proxy, stalled)
        outbox = proxy.__dict__["_client_outboxes"][id(stalled)]

        await _listen(proxy, [_tick(1.0)])
        await _hang_up(proxy, handlers, stalled)
        await _settle()

        assert outbox.task.cancelled()
        assert stalled.aborted == 0

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


def test_a_client_that_falls_behind_gets_the_newest_tick_of_each_stream() -> None:
    async def scenario() -> None:
        proxy = _bare_proxy()
        slow = _Browser(delay=0.01)
        handlers = await _serve(proxy, slow, symbols=("RELIANCE", "SBIN"))

        frames = []
        for n in range(1, 51):
            frames += [_tick(n), _tick(1000 + n, "SBIN")]
        await _listen(proxy, frames)
        await asyncio.sleep(0.5)

        for symbol, last in (("RELIANCE", 50), ("SBIN", 1050)):
            ltps = _ltps(slow, symbol)
            assert ltps[-1] == last  # nothing newer is ever lost
            assert ltps == sorted(set(ltps))  # in order, none twice
            assert len(ltps) < 50  # the backlog was merged, not queued
        assert slow.aborted == 0

        await _hang_up(proxy, handlers, slow)

    asyncio.run(scenario())


def test_a_consumer_pausing_briefly_is_not_dropped_however_much_arrives() -> None:
    """The limit is the age of the oldest waiting message, not a count: a local consumer
    that stops reading for a moment while thousands of ticks arrive stays connected."""

    async def scenario() -> None:
        proxy = _bare_proxy()
        proxy.CLIENT_SEND_STALL_SECONDS = 1.0
        symbols = tuple(f"S{i}" for i in range(30))
        paused = _Browser()
        handlers = await _serve(proxy, paused, symbols=symbols)
        paused_once = asyncio.Event()  # its first send takes 0.3 s, the rest none

        async def send(text: str) -> None:
            if not paused_once.is_set():
                paused_once.set()
                await asyncio.sleep(0.3)
            paused.sent.append(json.loads(text))

        paused.send = send
        await _listen(proxy, [_tick(n, s) for n in range(1, 101) for s in symbols])
        await asyncio.sleep(0.5)

        assert paused.aborted == 0
        assert {s: _ltps(paused, s)[-1] for s in symbols} == dict.fromkeys(symbols, 100)
        assert len(paused.sent) < 3000

        await _hang_up(proxy, handlers, paused)

    asyncio.run(scenario())


def test_a_client_whose_oldest_message_waits_too_long_is_dropped() -> None:
    """A socket that takes a little, but so slowly that what it shows is older than the
    stall limit, is dropped like one that takes nothing."""

    async def scenario() -> None:
        proxy = _bare_proxy()
        proxy.CLIENT_SEND_STALL_SECONDS = 0.3
        symbols = tuple(f"S{i}" for i in range(20))
        trickle = _Browser(delay=0.05)
        handlers = await _serve(proxy, trickle, symbols=symbols)

        await _listen(proxy, [_tick(n, s) for n in range(1, 4) for s in symbols])
        await asyncio.sleep(1.2)

        assert trickle.aborted == 1
        assert 0 < len(trickle.sent) < 20

        await _hang_up(proxy, handlers)

    asyncio.run(scenario())


class _SlowBrowser(_Browser):
    """A browser on a slow link: each send takes 50 ms."""

    def __init__(self) -> None:
        super().__init__(delay=0.05)


class _SubWithReply(_Sub):
    """Replays frames; just before frame `at`, the proxy sends the client a reply."""

    def __init__(self, proxy, frames, client_id: int, at: int):
        super().__init__(proxy, frames)
        self.client_id, self.at, self.served = client_id, at, 0

    async def recv_multipart(self):
        if self.served == self.at:
            reply = {"type": "subscribe", "status": "success"}
            await self.proxy.send_message(self.client_id, reply)
        self.served += 1
        return await super().recv_multipart()


def test_a_reply_does_not_overtake_ticks_queued_before_it() -> None:
    """The review's slow-client ordering case: five ticks wait behind a slow send, then a
    reply is sent. The waiting ticks merge into the newest, and the reply comes last."""

    async def scenario() -> None:
        proxy = _bare_proxy()
        browser = _SlowBrowser()
        handlers = await _serve(proxy, browser)

        await _listen(proxy, [_tick(float(n)) for n in range(1, 6)])
        await proxy.send_message(id(browser), {"type": "subscribe", "status": "success"})
        await asyncio.sleep(0.4)

        assert [m.get("type") for m in browser.sent] == ["market_data", "market_data", "subscribe"]
        assert _ltps(browser) == [1.0, 5.0]
        await _hang_up(proxy, handlers, browser)

    asyncio.run(scenario())


def test_a_tick_never_merges_across_a_reply() -> None:
    """Ticks 1 and 2, a reply, ticks 3 and 4: tick 3 must not take tick 2's place ahead of
    the reply, so the client sees 1, 2, the reply, then the newest tick, 4."""

    async def scenario() -> None:
        proxy = _bare_proxy()
        browser = _SlowBrowser()
        handlers = await _serve(proxy, browser)

        proxy.running = True
        frames = [_tick(float(n)) for n in range(1, 5)]
        proxy.socket = _SubWithReply(proxy, frames, id(browser), at=2)
        await asyncio.wait_for(proxy.zmq_listener(), 5)
        await asyncio.sleep(0.4)

        sequence = [
            m["data"]["ltp"] if m["type"] == "market_data" else m["type"] for m in browser.sent
        ]
        assert sequence == [1.0, 2.0, "subscribe", 4.0]
        await _hang_up(proxy, handlers, browser)

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


def test_an_outbox_whose_connection_closed_stops_queueing() -> None:
    """Between the close and cleanup_client the fan-out still offers; nothing may pile up."""
    from websockets.exceptions import ConnectionClosedOK
    from websockets.frames import Close

    class _Closed:
        transport = None

        async def send(self, _text: str) -> None:
            raise ConnectionClosedOK(Close(1000, ""), Close(1000, ""), True)

    async def scenario() -> None:
        outbox = ClientOutbox(1, _Closed(), 10.0)
        outbox.offer("{}", ("NSE_RELIANCE_QUOTE", 2))
        await _settle()
        assert outbox.task.done() and outbox.task.exception() is None

        for n in range(100):
            outbox.offer("{}", (f"NSE_S{n}_QUOTE", 2))
            outbox.offer('{"type": "pong"}')
        assert len(outbox._queue) == 0

    asyncio.run(scenario())


def test_a_merged_tick_keeps_what_only_the_older_one_said() -> None:
    """Newer values win, nested ones (depth, greeks) are replaced whole, and the payloads,
    which the fan-out shares between clients, are never changed."""
    older = {
        "type": "market_data",
        "mode": 3,
        "data": {
            "ltp": 1.0,
            "oi": 10,
            "depth": {"buy": [1], "sell": [2]},
            "greeks": {"delta": 0.5},
        },
    }
    newer = {"type": "market_data", "mode": 3, "data": {"ltp": 2.0, "depth": {"buy": [3]}}}

    merged = merge_ticks(older, newer)

    assert merged == {
        "type": "market_data",
        "mode": 3,
        "data": {"ltp": 2.0, "oi": 10, "depth": {"buy": [3]}, "greeks": {"delta": 0.5}},
    }
    assert newer["data"] == {"ltp": 2.0, "depth": {"buy": [3]}}
    assert older["data"]["ltp"] == 1.0
