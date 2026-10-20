"""सुदर्शन / SUDARSHAN: the Upstox market-data socket as SUDARSHAN tracks it (M18.2).

One Upstox key per symbol, in the mode its subscribers need. Upstox streams each
instrument key in one mode per connection, while OpenAlgo subscribes a symbol in
up to three modes (LTP, Quote, Depth), each with its own record in the adapter.
LTP needs `ltpc`; Quote and Depth need `full`. The key must be streamed in the
highest mode any record needs, switched down when the last record needing more
goes, and released only with its last record.

ClientSocketMixin gives UpstoxWebSocketClient the per-key mode it streams and
Upstox's `change_mode`. AdapterSocketMixin gives UpstoxWebSocketAdapter the mode
each key needs, declares to the connection pool that every mode it was given
must be handed back (see ConnectionPool.unsubscribe), and says whether its feed
has stopped for good (feed_alive).
"""

from __future__ import annotations

import json
from collections.abc import Iterable

import websocket

# How much each Upstox mode carries; a key streamed in a higher mode serves a lower one.
MODE_RANK = {"ltpc": 1, "option_greeks": 1, "full": 2, "full_d30": 3}


class ClientSocketMixin:
    """For UpstoxWebSocketClient: the mode each key is streamed in, and change_mode.

    `_keys_by_mode` is read and changed from three threads (the proxy's loop
    switching keys down, the batch timer subscribing, the socket thread on a
    handshake), so every use of it holds `_modes_lock`, an in-memory critical
    section with no network call inside. A mode left with no key is dropped from
    it, so the combined cap, which applies only while two modes are in use, does
    not count it.
    """

    def _move_keys(self, instrument_keys: list[str], mode: str) -> None:
        """Record that these keys now stream in `mode`, and in no other.

        A sub in another mode, or a change_mode, moves a key, so the per-mode
        sets follow it: the subscription caps count each key once, and
        mode_of() can tell the adapter what Upstox is sending for it.
        """
        with self._modes_lock:
            self._drop_from_modes(instrument_keys)
            self._keys_by_mode.setdefault(mode, set()).update(instrument_keys)

    def _forget_keys(self, instrument_keys: list[str]) -> None:
        """These keys stream in no mode any more."""
        with self._modes_lock:
            self._drop_from_modes(instrument_keys)

    def _reset_modes(self) -> None:
        """No key streams: the socket closed, or a new one opened."""
        with self._modes_lock:
            self._keys_by_mode.clear()

    def _drop_from_modes(self, instrument_keys: list[str]) -> None:
        """Caller holds _modes_lock."""
        for held in self._keys_by_mode.values():
            held.difference_update(instrument_keys)
        for empty in [other for other, held in self._keys_by_mode.items() if not held]:
            del self._keys_by_mode[empty]

    def mode_of(self, instrument_key: str) -> str | None:
        """The Upstox mode this connection streams the key in, or None."""
        with self._modes_lock:
            for mode, held in self._keys_by_mode.items():
                if instrument_key in held:
                    return mode
        return None

    def change_mode(self, instrument_keys: list[str], mode: str) -> bool:
        """Switch keys this connection already streams to `mode`.

        Upstox's `change_mode`, which it documents for exactly this: it
        "switches the mode for already subscribed instrument keys", with
        full -> ltpc as its example. Only ever sent for keys Upstox holds.
        """
        if not self._connected or not self.ws:
            return False
        try:
            message = self._create_subscription_message(instrument_keys, mode, "change_mode")
            self.ws.send(json.dumps(message).encode("utf-8"), opcode=websocket.ABNF.OPCODE_BINARY)
        except Exception as e:
            self.logger.error(f"Change mode error: {e}")
            return False
        self._move_keys(instrument_keys, mode)
        return True


class AdapterSocketMixin:
    """For UpstoxWebSocketAdapter: each key streamed in the mode its records need."""

    # Read by ConnectionPool.unsubscribe. The pool hands every mode it subscribed
    # here back to unsubscribe(), a lower one too while a higher one stays, and on
    # a downgrade subscribes the lower mode before it releases the higher one.
    # Without that, a record the pool had forgotten kept the key streaming at
    # Upstox after its last subscriber left, and a downgrade dropped the key and
    # sent it again.
    RELEASE_EVERY_MODE = True

    def _needed_modes(self) -> dict[str, str]:
        """{instrument key: the Upstox mode its records need}. Caller holds self.lock."""
        needed: dict[str, str] = {}
        for sub in self.subscriptions.values():
            key = sub.get("instrument_key")
            mode = self._get_upstox_mode(sub["mode"], sub.get("depth_level", 0))
            if MODE_RANK.get(mode, 2) > MODE_RANK.get(needed.get(key), 0):
                needed[key] = mode
        return needed

    def _keys_by_needed_mode(self, keys: Iterable[str]) -> dict[str, list[str]]:
        """{Upstox mode: [keys]}, each key once, in the mode its records need now.

        Caller holds self.lock. A lower mode subscribed beside a higher one
        therefore never switches the key down under the higher one, and a key
        whose records all went before its subscribe was sent is not sent at all.
        """
        needed = self._needed_modes()
        grouped: dict[str, list[str]] = {}
        for key in dict.fromkeys(keys):
            if key in needed:
                grouped.setdefault(needed[key], []).append(key)
        return grouped

    @property
    def feed_alive(self) -> bool:
        """False once this adapter's Upstox feed has stopped for good.

        That is when the client has given up reconnecting (its socket thread has
        ended), or Upstox refuses the stored login, as it does from 03:30 until
        the next login. The proxy then rebuilds the adapter with the current
        login instead of reusing it (see _PooledAdapterWrapper.connected); a
        reused one would accept subscriptions and never send a tick. A socket
        that is merely reconnecting is alive.
        """
        client = self.ws_client
        if client is None or not getattr(client, "running", False):
            return False
        thread = getattr(client, "_ws_thread", None)
        if thread is None or not thread.is_alive():
            return False
        return not getattr(client, "_authorize_refusals", 0)

    def _narrow_key(self, instrument_key: str) -> None:
        """A record of this key has gone: switch it down if Upstox sends more than the rest need.

        Nothing is sent for a key Upstox does not hold yet (its subscribe is
        still queued) or while the socket is down: the batch and the replay
        after a reconnect already send each key in the mode its records need.
        """
        with self.lock:
            needed = self._needed_modes().get(instrument_key)
        client = self.ws_client
        mode_of = getattr(client, "mode_of", None)
        current = mode_of(instrument_key) if mode_of else None
        if needed is None or current is None:
            return
        if MODE_RANK.get(current, 2) > MODE_RANK.get(needed, 2):
            if client.change_mode([instrument_key], needed):
                self.logger.info(
                    f"Switched {instrument_key} from {current} to {needed}: "
                    f"no subscriber needs {current} any more"
                )
