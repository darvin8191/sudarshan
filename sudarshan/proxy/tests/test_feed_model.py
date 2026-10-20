"""Model check of who owns what, from a screen to Upstox (SUDARSHAN M18.2).

Screens subscribe, unsubscribe and close through the real proxy handlers, over the real
ConnectionPool and the real Upstox adapter and client, while the Upstox socket closes and
reopens. After every step the layers must agree with what the screens own:

- the pool tracks exactly the modes screens own, and the adapter no record beyond the pool's;
- Upstox streams no key that no screen owns;
- once the socket is open and no subscribe waits in the batch, Upstox streams every owned key,
  in exactly the mode its owners need (`full` for Quote or Depth, `ltpc` for LTP alone).

Built from the re-review's model checker. Its reconnect sequences found a key releasing its
last mode while the socket was closed: the release was refused, the pool kept the mode, and
the reconnect's replay streamed the key with no owner.
"""

from __future__ import annotations

import asyncio
import random

import pytest
from upstox_fakes import (
    ClientSocket,
    UpstoxServer,
    build_adapter,
    lingering_proxy,
    map_symbols,
    pooled,
)

from websocket_proxy.base_adapter import BaseBrokerWebSocketAdapter

KEYS = {
    ("AAA", "NFO"): "NSE_FO|1001",
    ("BBB", "NFO"): "NSE_FO|1002",
    ("CCC", "NSE"): "NSE_EQ|INE1",
}
A, B, C = ("AAA", "NFO"), ("BBB", "NFO"), ("CCC", "NSE")
MODE_NAME = {1: "LTP", 2: "Quote", 3: "Depth"}


class World:
    """Screens 0 to 3 on one proxy, over one pool, one adapter and one Upstox socket."""

    def __init__(self, monkeypatch, hold_batch: bool) -> None:
        map_symbols(monkeypatch, KEYS)
        self.adapter = build_adapter()
        if hold_batch:  # subscribes wait for an explicit flush, as for the 0.5 s timer
            self.adapter._start_batch_timer = lambda: None
        self.wrapper = pooled(self.adapter)
        self.pool = self.wrapper._pool
        self.proxy = lingering_proxy(monkeypatch, self.wrapper)
        self.history: list[tuple] = []

    # Steps --------------------------------------------------------------------------------

    async def open(self, client_id: int) -> None:
        self.proxy.clients[client_id] = ClientSocket()
        self.proxy.subscriptions[client_id] = set()
        await self.proxy.authenticate_client(client_id, {"api_key": "k"})

    async def step(self, step: tuple) -> None:
        kind, *args = step
        if kind in ("sub", "unsub"):
            client_id, (symbol, exchange), mode = args
            request = {"symbols": [{"symbol": symbol, "exchange": exchange}]}
            request |= {"mode": MODE_NAME[mode], "depth": 5}
            if kind == "sub":
                await self.proxy.subscribe_client(client_id, request)
            else:
                await self.proxy.unsubscribe_client(client_id, request | {"action": "unsubscribe"})
        elif kind == "close":  # the screen goes, and comes straight back owning nothing
            await self.proxy.cleanup_client(args[0])
            await self.open(args[0])
        elif kind == "flush":  # the batch timer fires
            self.adapter._process_batch_subscriptions()
        elif kind == "drop":  # the socket closes: Upstox forgets every key
            self.adapter.upstox.close_socket()
            self.adapter.ws_client._connected = False
        elif kind == "reopen":  # a new socket, and the adapter's replay on its handshake
            client = self.adapter.ws_client
            client.ws = self.adapter.upstox = UpstoxServer()
            client._reset_modes()
            client._subscriptions.clear()
            client._connected = True
            self.adapter._on_connect()

    # Invariants ---------------------------------------------------------------------------

    def check(self) -> None:
        owned = {key for key, owners in self.proxy.subscription_index.items() if owners}
        pool = set(self.pool.subscription_map)
        records = {
            (sub["symbol"], sub["exchange"], sub["mode"])
            for sub in self.adapter.subscriptions.values()
        }
        held = dict(self.adapter.upstox.held)
        top: dict[str, int] = {}
        for symbol, exchange, mode in owned:
            key = KEYS[(symbol, exchange)]
            top[key] = max(top.get(key, 0), mode)
        need = {key: "full" if mode >= 2 else "ltpc" for key, mode in top.items()}
        state = (
            f"\nsteps={self.history}\nowned={sorted(owned)}\npool={sorted(pool)}"
            f"\nrecords={sorted(records)}\nheld={held}\nneed={need}"
        )
        assert pool == owned, "the pool does not track exactly what screens own" + state
        assert records <= pool, "an adapter record no pool entry owns" + state
        assert set(held) <= set(need), "Upstox streams a key no screen owns" + state
        settled = self.adapter.ws_client._connected and not self.adapter.subscription_queue
        if settled:
            assert held == need, "Upstox does not stream exactly what screens need" + state


async def run(world: World, steps: list[tuple]) -> None:
    for client_id in (0, 1, 2, 3):
        await world.open(client_id)
    for step in steps:
        world.history.append(step)
        await world.step(step)
        world.check()


@pytest.fixture
def instances():
    count = BaseBrokerWebSocketAdapter._instance_count
    yield
    BaseBrokerWebSocketAdapter._instance_count = count


NAMED = {
    "LTP then Depth, both closed": [
        ("sub", 1, A, 1), ("sub", 2, A, 3), ("flush",), ("close", 1), ("close", 2), ("flush",),
    ],
    "Depth then LTP, Depth closed": [
        ("sub", 1, A, 3), ("flush",), ("sub", 2, A, 1), ("close", 1), ("flush",),
    ],
    "Quote then Depth, released one by one": [
        ("sub", 1, A, 2), ("flush",), ("sub", 2, A, 3), ("flush",), ("close", 2), ("flush",),
        ("close", 1), ("flush",),
    ],
    "two screens on one key across reconnects": [
        ("sub", 1, A, 1), ("sub", 2, A, 3), ("flush",), ("drop",), ("reopen",), ("flush",),
        ("close", 2), ("flush",), ("drop",), ("reopen",), ("close", 1), ("flush",),
    ],
    "downgrade while the socket is closed": [
        ("sub", 1, A, 3), ("flush",), ("sub", 2, A, 1), ("drop",), ("close", 1), ("reopen",),
        ("flush",),
    ],
    "last mode released while the socket is closed": [
        ("sub", 1, A, 1), ("flush",), ("drop",), ("unsub", 1, A, 1), ("reopen",), ("flush",),
    ],
    "last screen closed while the socket is closed": [
        ("sub", 1, B, 3), ("sub", 2, C, 1), ("flush",), ("drop",), ("close", 1), ("close", 2),
        ("reopen",), ("flush",),
    ],
}  # fmt: skip


@pytest.mark.parametrize("hold_batch", [False, True], ids=["batch at once", "batch held"])
@pytest.mark.parametrize("steps", NAMED.values(), ids=NAMED.keys())
def test_named_sequences(monkeypatch, instances, steps, hold_batch) -> None:
    world = World(monkeypatch, hold_batch)
    asyncio.run(run(world, steps))


def random_steps(rng: random.Random, count: int, reconnects: bool) -> list[tuple]:
    def screen_step(kinds: tuple[str, ...]) -> tuple:
        kind, client_id = rng.choice(kinds), rng.randint(1, 3)
        if kind == "close":
            return ("close", client_id)
        return (kind, client_id, rng.choice((A, B, C)), rng.randint(1, 3))

    steps: list[tuple] = []
    for _ in range(count):
        kind = rng.choice(("screen", "screen", "screen", "screen", "flush", "flush", "reconnect"))
        if kind == "screen":
            steps.append(screen_step(("sub", "sub", "sub", "unsub", "unsub", "close")))
        elif kind == "flush":
            steps.append(("flush",))
        elif reconnects:  # up to two screen steps while the socket is closed
            steps.append(("drop",))
            steps += [screen_step(("sub", "unsub", "close")) for _ in range(rng.randint(0, 2))]
            steps.append(("reopen",))
    steps.append(("flush",))
    return steps


@pytest.mark.parametrize("hold_batch", [False, True], ids=["batch at once", "batch held"])
@pytest.mark.parametrize("reconnects", [False, True], ids=["socket open", "reconnects"])
def test_random_sequences(instances, hold_batch, reconnects) -> None:
    rng = random.Random(20261020 + 7 * hold_batch + 13 * reconnects)
    for _ in range(150):
        with pytest.MonkeyPatch.context() as monkeypatch:
            world = World(monkeypatch, hold_batch)
            asyncio.run(run(world, random_steps(rng, 25, reconnects)))
