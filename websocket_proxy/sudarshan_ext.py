"""सुदर्शन / SUDARSHAN additions to the WebSocket proxy (M18.2).

Kept out of server.py so the upstream file carries only short, marked hooks:

- the broker linger: WEBSOCKET_BROKER_LINGER_SECONDS keeps a user's idle broker
  connection open for a while after their last screen closes, so a phone that
  turns its screen off and on again reuses it instead of opening a new one;
- client outboxes: each client's messages go through its own queue and sender
  task, so one slow client never holds back another.

WebSocketProxy inherits SudarshanProxyMixin. Everything here runs on the proxy's
event loop, except _on_user_pools_cleaned, which may be called from any thread
and hands its work to the loop.
"""

from __future__ import annotations

import asyncio as aio
import json
import math
import os
import time
from collections import deque
from typing import Any, NamedTuple

from websockets.exceptions import ConnectionClosed

from utils.logging import get_logger

from .broker_factory import add_pool_cleanup_listener, remove_pool_cleanup_listener

# The proxy's own logger, so these lines read exactly as they did in server.py.
logger = get_logger("websocket_proxy")

# How long a user's broker connection stays open after their last client leaves.
# A phone closes its live connection 5 s after the screen turns off, and without
# this every screen-on opened a new broker socket.
BROKER_LINGER_ENV = "WEBSOCKET_BROKER_LINGER_SECONDS"


def broker_linger_seconds() -> float:
    """WEBSOCKET_BROKER_LINGER_SECONDS in seconds; 0 (close at once) unless set."""
    raw = os.getenv(BROKER_LINGER_ENV, "0").strip().strip("'\"")
    try:
        seconds = float(raw or "0")
    except ValueError:
        seconds = math.nan
    if not math.isfinite(seconds) or seconds < 0:
        logger.warning(
            f"{BROKER_LINGER_ENV}={raw!r} in .env is not a number of seconds, so the "
            f"broker connection closes as soon as the last screen does. Set it to a "
            f"number such as 300, or to 0."
        )
        return 0.0
    return seconds


def holds_no_symbol(adapter) -> bool:
    """True only when the adapter provably holds no subscription at the broker.

    A pooled adapter's own map is the pool's view. The broker adapters behind it
    keep records of their own, and a record the pool has lost track of still
    streams its symbol, so each of them must be empty too.
    """
    held = getattr(adapter, "subscriptions", None)
    if held is None or len(held) > 0:
        return False
    pool = getattr(adapter, "_pool", None)
    for inner in list(getattr(pool, "adapters", None) or ()):
        inner_held = getattr(inner, "subscriptions", None)
        if inner_held is None or len(inner_held) > 0:
            return False
    return True


class BrokerLinger(NamedTuple):
    """An idle adapter kept connected, and the task ending it."""

    adapter: Any
    task: aio.Task
    loop: aio.AbstractEventLoop


def merge_ticks(older: dict, newer: dict) -> dict:
    """The newer tick of a stream, carrying what only the older one said.

    A payload leaves out the values its frame did not carry (oi, iv, greeks, the
    average price, the book totals, depth), so a newer tick replacing a waiting
    one whole would lose them: the client would keep showing old open interest
    as current. Merged, the client holds what a consumer applying both ticks in
    order holds (MarketDataManager merges each tick into the last): newer values
    win, and a nested value such as `depth` or `greeks` is replaced whole.
    """
    old_data, new_data = older.get("data"), newer.get("data")
    if not (isinstance(old_data, dict) and isinstance(new_data, dict)):
        return newer
    return {**newer, "data": {**old_data, **new_data}}


class ClientOutbox:
    """One client's outgoing messages, sent in order by its own task.

    The fan-out and send_message only queue, so they never wait for a client: one
    slow phone no longer holds back another screen's ticks.

    A client that falls behind gets the newest tick, not a backlog. While a tick
    for one stream (topic and the client's mode) still waits, a newer one for that
    stream is merged into it in its place (merge_ticks), which is the
    trailing-edge coalescing server.py's fan-out comment asks for. A client that
    keeps up has nothing waiting and so gets every tick. Ticks wait as dicts and
    become JSON when sent. Replies, errors and order updates are never merged or
    dropped, and keep their order relative to ticks: a tick is merged only into
    one queued after the last of them.

    The one drop is the stall backstop: a message that has waited `stall_seconds`
    unsent, which a socket taking nothing, or so little that the oldest news it
    has not been given is that old, both come to. It is measured in time, not in
    messages, so a local consumer that pauses for a second or two loses nothing
    but merged ticks. The connection is then aborted without a closing handshake;
    the browser reconnects, resubscribes and starts again from fresh prices.

    Memory: one waiting tick per stream between two replies, plus the replies,
    and nothing older than the stall limit. A closed connection queues nothing.
    """

    def __init__(self, client_id, websocket, stall_seconds: float):
        self.client_id = client_id
        self.websocket = websocket
        self.stall_seconds = stall_seconds
        self.dropped = False
        # Waiting messages, oldest first: [stream or None, frame, queued at, replies so far].
        self._queue: deque[list] = deque()
        self._waiting_ticks: dict[Any, list] = {}  # stream -> its tick still in _queue
        self._replies = 0  # replies (anything not a tick) queued so far
        self._wake = aio.Event()
        self.task = aio.get_running_loop().create_task(
            self._run(), name=f"ws-client-outbox-{client_id}"
        )

    def offer(self, message: dict | str, stream: Any = None) -> None:
        """Queue one message; never waits. `stream` marks a tick a newer one merges into."""
        if self.dropped:
            return
        if stream is not None:
            waiting = self._waiting_ticks.get(stream)
            if waiting is not None and waiting[3] == self._replies:
                older = waiting[1]
                both = isinstance(older, dict) and isinstance(message, dict)
                waiting[1] = merge_ticks(older, message) if both else message
                return
            entry = [stream, message, time.monotonic(), self._replies]
            self._waiting_ticks[stream] = entry
        else:
            self._replies += 1
            frame = message if isinstance(message, str) else json.dumps(message)
            entry = [None, frame, time.monotonic(), self._replies]
        self._queue.append(entry)
        self._wake.set()

    async def _run(self) -> None:
        while True:
            if not self._queue:
                self._wake.clear()
                await self._wake.wait()
                continue
            entry = self._queue.popleft()
            stream, message, queued_at = entry[0], entry[1], entry[2]
            if stream is not None and self._waiting_ticks.get(stream) is entry:
                del self._waiting_ticks[stream]
            try:
                frame = message if isinstance(message, str) else json.dumps(message)
            except (TypeError, ValueError):
                logger.exception(f"Could not encode a message for client {self.client_id}")
                continue
            left = self.stall_seconds - (time.monotonic() - queued_at)
            try:
                if left <= 0:
                    raise TimeoutError
                await aio.wait_for(self.websocket.send(frame), left)
            except TimeoutError:
                self.drop(f"a message to it waited {self.stall_seconds:g}s unsent")
                return
            except ConnectionClosed:
                self._forget()  # nothing more can be sent; stop queueing
                return
            except Exception:
                logger.exception(f"Sending to client {self.client_id} failed")
                self.drop("a send to it failed")
                return

    def drop(self, reason: str) -> None:
        """Abort the connection, with no closing handshake, so the browser reconnects."""
        if self.dropped:
            return
        self._forget()
        logger.warning(
            f"Disconnecting client {self.client_id} because {reason}. Its screen "
            f"reconnects and resubscribes by itself."
        )
        transport = getattr(self.websocket, "transport", None)
        if transport is None:
            return
        try:
            transport.abort()
        except Exception:
            logger.exception(f"Could not drop client {self.client_id}")

    def close(self) -> None:
        """The client has gone: stop the sender and forget what is queued."""
        self._forget()
        self.task.cancel()

    def _forget(self) -> None:
        self.dropped = True
        self._queue.clear()
        self._waiting_ticks.clear()


class SudarshanProxyMixin:
    """The broker linger and the client outboxes, for WebSocketProxy."""

    # Class-level defaults, so a proxy built without __init__ (as the tests build
    # one) keeps upstream's behaviour: no linger at all.
    broker_linger_seconds: float = 0.0
    _linger_sleep = staticmethod(aio.sleep)

    # Per-client outboxes (see ClientOutbox): a client is dropped once a message to
    # it has waited this long, well inside the 40 s keepalive would otherwise need.
    CLIENT_SEND_STALL_SECONDS = 10.0

    # ------------------------------------------------------------------------
    # Hooks called from server.py
    # ------------------------------------------------------------------------

    def _sudarshan_init(self) -> None:
        """From __init__: read the linger setting and listen for pool teardowns.

        A pool teardown from anywhere (a login, the daily token change) also ends
        a pending linger at once, through the listener.
        """
        self.broker_linger_seconds = broker_linger_seconds()
        self._broker_lingers: dict[str, BrokerLinger] = {}
        add_pool_cleanup_listener(self._on_user_pools_cleaned)
        if self.broker_linger_seconds > 0:
            logger.info(
                f"Broker connections stay open {self.broker_linger_seconds:g}s after a "
                f"user's last screen closes ({BROKER_LINGER_ENV})"
            )

    def _close_all_outboxes(self) -> None:
        """From stop(): the client outboxes' sender tasks end with the server."""
        for client_id in list(self._outboxes()):
            self._close_outbox(client_id)

    def _end_all_lingers(self) -> None:
        """From stop(): no linger outlives the server.

        Its adapters are still in broker_adapters, and stop() disconnects them.
        """
        self._cancel_all_broker_lingers()
        remove_pool_cleanup_listener(self._on_user_pools_cleaned)

    def _keep_adapter_lingering(self, user_id, adapter, broker_name, release_failed) -> bool:
        """From cleanup_client, for the user's last client: True keeps the adapter.

        With WEBSOCKET_BROKER_LINGER_SECONDS above 0, an idle adapter (every
        subscription released) stays connected for that long, so a screen that
        comes back reuses it. Anything not provably idle is torn down at once,
        exactly as before. Flattrade and Shoonya keep their own handling.
        """
        if release_failed or broker_name in ("flattrade", "shoonya"):
            return False
        return self._begin_broker_linger(user_id, adapter, broker_name)

    def _offer_to_outbox(self, client_id, message, stream=None) -> bool:
        """From send_message and the fan-out: queue in the client's outbox.

        A reply goes in order with the client's ticks. A tick names its `stream`
        (the topic and the client's mode), so a newer tick merges into one still
        waiting. Returns False for a client without an outbox (a proxy driven
        without handle_client, as the tests do), which is then sent to directly.
        """
        outbox = self._outboxes().get(client_id)
        if outbox is None:
            return False
        outbox.offer(message, stream)
        return True

    # ------------------------------------------------------------------------
    # Broker linger
    # ------------------------------------------------------------------------

    def _lingers(self) -> dict:
        """The pending lingers, user_id -> BrokerLinger (made on first use)."""
        lingers = self.__dict__.get("_broker_lingers")
        if lingers is None:
            lingers = self._broker_lingers = {}
        return lingers

    def _user_has_live_client(self, user_id) -> bool:
        return any(
            client_id in self.clients and mapped == user_id
            for client_id, mapped in self.user_mapping.items()
        )

    def _begin_broker_linger(self, user_id, adapter, broker_name) -> bool:
        """Keep an idle adapter connected for broker_linger_seconds.

        Returns True when it does; False (disconnect at once, as before) when
        the setting is 0 or the adapter cannot be shown to hold no symbols.
        """
        seconds = self.broker_linger_seconds
        if seconds <= 0 or not holds_no_symbol(adapter):
            return False
        self._cancel_broker_linger(user_id)
        loop = aio.get_running_loop()
        task = loop.create_task(
            self._broker_linger(user_id, adapter, seconds), name=f"ws-broker-linger-{user_id}"
        )
        self._lingers()[user_id] = BrokerLinger(adapter, task, loop)
        logger.info(
            "Last client for user %s disconnected. Keeping the %s connection open "
            "for %gs, with no symbols, in case a screen comes back.",
            user_id,
            broker_name or "broker",
            seconds,
        )
        return True

    async def _broker_linger(self, user_id, adapter, seconds) -> None:
        await self._linger_sleep(seconds)  # a cancel ends the task here
        entry = self._lingers().get(user_id)
        if entry is None or entry.adapter is not adapter:
            return
        del self._lingers()[user_id]
        self._close_lingering_adapter(user_id, adapter, f"no screen came back within {seconds:g}s")

    def _close_lingering_adapter(self, user_id, adapter, reason) -> None:
        """Disconnect exactly as the last client's departure did before the linger."""
        if self.broker_adapters.get(user_id) is not adapter or self._user_has_live_client(user_id):
            return  # already replaced or torn down, or a screen is back
        broker_name = self.user_broker_mapping.get(user_id)
        logger.info(
            "Disconnecting %s adapter for user %s: %s.",
            broker_name or "unknown broker",
            user_id,
            reason,
        )
        try:
            adapter.disconnect()
        except Exception:
            logger.exception(
                "Error disconnecting %s adapter for user %s",
                broker_name or "unknown broker",
                user_id,
            )
        finally:
            self.broker_adapters.pop(user_id, None)
            self.user_broker_mapping.pop(user_id, None)

    def _cancel_broker_linger(self, user_id):
        """Stop a pending linger without disconnecting; returns its adapter or None."""
        entry = self._lingers().pop(user_id, None)
        if entry is None:
            return None
        entry.task.cancel()
        return entry.adapter

    def _cancel_all_broker_lingers(self) -> None:
        for user_id in list(self._lingers()):
            self._cancel_broker_linger(user_id)

    def _end_broker_linger(self, user_id, reason) -> None:
        """A teardown elsewhere: stop the linger and close its adapter now."""
        adapter = self._cancel_broker_linger(user_id)
        if adapter is not None:
            self._close_lingering_adapter(user_id, adapter, reason)

    def _on_user_pools_cleaned(self, user_id) -> None:
        """broker_factory.cleanup_pools_for_user tore this user's pools down.

        Called from whichever thread ran the cleanup: the proxy's loop in the
        proxy process, a request thread on the development server.
        """
        entry = (self.__dict__.get("_broker_lingers") or {}).get(user_id)
        if entry is None:
            return
        reason = "its broker connections were reset (new login or token change)"
        try:
            on_loop = aio.get_running_loop() is entry.loop
        except RuntimeError:
            on_loop = False
        if on_loop:
            self._end_broker_linger(user_id, reason)
        elif not entry.loop.is_closed():
            entry.loop.call_soon_threadsafe(self._end_broker_linger, user_id, reason)

    # ------------------------------------------------------------------------
    # Client outboxes
    # ------------------------------------------------------------------------

    def _outboxes(self) -> dict:
        """client_id -> ClientOutbox (made on first use)."""
        outboxes = self.__dict__.get("_client_outboxes")
        if outboxes is None:
            outboxes = self._client_outboxes = {}
        return outboxes

    def _open_outbox(self, client_id, websocket) -> None:
        """From handle_client: everything this client is sent goes through its outbox."""
        self._close_outbox(client_id)
        self._outboxes()[client_id] = ClientOutbox(
            client_id, websocket, self.CLIENT_SEND_STALL_SECONDS
        )

    def _close_outbox(self, client_id) -> None:
        """From cleanup_client: the client has gone."""
        outbox = self._outboxes().pop(client_id, None)
        if outbox is not None:
            outbox.close()
