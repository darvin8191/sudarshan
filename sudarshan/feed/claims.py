"""Subscription claims per client on the local API (M18.1), mixed into FeedService.

PUT /claims/<client> {"keys": {KEY: MODE}} replaces that client's whole set in one step and
renews it; DELETE /claims/<client> drops it; GET /claims lists every client. The effective set
is computed in subscriptions.py; this sends Upstox only what changed, on the live socket, under
the same lock as /subscribe and the resubscribe after a connect: ``sub`` for keys new to the
socket, ``change_mode`` (and a fresh snapshot entry) for keys whose effective mode changed, and
``unsub`` for keys nobody holds any more. Without a live socket the change applies at the next
connect, which sends the whole effective set.
"""

from __future__ import annotations

import logging

from websockets.exceptions import ConnectionClosed

from .subscriptions import Change, validate_claims
from .window import iso

log = logging.getLogger("sudarshan.feed")


class ClaimsMixin:
    async def put_claims(self, client, keys) -> dict:
        client, keys = validate_claims(client, keys)
        async with self._send_lock:
            change = self.subs.put_claims(client, keys, self.clock())  # CapError: no change
            live = await self._apply_change(change)
        log.info("claims of %s: %d keys (%s; live: %s)", client, len(keys), _said(change), live)
        return {"ok": True, "claimed": len(keys), "subscribed": self.subs.counts(),
                "applied_live": live}  # fmt: skip

    async def drop_claims(self, client) -> dict:
        client, _ = validate_claims(client, {})
        async with self._send_lock:
            change = self.subs.drop_claims(client)
            live = await self._apply_change(change)
        log.info("claims of %s dropped (%s; live: %s)", client, _said(change), live)
        return {"ok": True, "subscribed": self.subs.counts(), "applied_live": live}

    def claims_view(self) -> dict:
        clients = {}
        for client, claim in sorted(self.subs.claims.items()):
            modes: dict[str, int] = {}
            for mode in claim["keys"].values():
                modes[mode] = modes.get(mode, 0) + 1
            clients[client] = {"renewed_at": iso(claim["renewed_at"]), "keys": len(claim["keys"]),
                               "by_mode": modes}  # fmt: skip
        return {"clients": clients, "subscribed": self.subs.counts()}

    async def _apply_change(self, change: Change) -> bool:
        """Tell the live socket what changed (caller holds _send_lock); False: at next connect."""
        for key in change.moved:
            self.store.reset(key)  # do not carry the old mode's fields into the new one
        ws = self._ws
        if ws is None or not self._live:
            return False
        try:
            for method, keys, mode in change.batches():
                await self._send(ws, method, keys, mode)
        except ConnectionClosed:
            return False
        return True


def _said(change: Change) -> str:
    return f"{len(change.new)} new, {len(change.moved)} re-moded, {len(change.gone)} gone"
