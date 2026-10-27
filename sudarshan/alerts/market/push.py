"""Market alerts to the open apps (M10.7): sealed pushes to OpenAlgo's owner room.

    runtime / desk -> Pusher (batched every 50 ms) -> POST 127.0.0.1:5000/sudarshan/alerts/internal/push
      -> OpenAlgo emits each item to the Socket.IO room sudarshan:alerts:owner, nothing broadcast,
         and answers with the room's size: the screens the alert was shown on

Each request is one Fernet token under ALERTS_PUSH_INFO (60 s, once): at most 50 items and
128 KiB (cross-design X9), so a batch is cut well below it. A fire's app delivery is what the
answer says: "Shown on 2 open screens", "No app was open", or not sent when OpenAlgo did not
answer. A push is never retried: a late toast is no use, and an app that reconnects reads what it
missed (events since). The key goes only to a port this service's own user listens on
(loopback.py), as the Telegram lines do; nothing waits here for long: a batch that cannot go is
told as not sent and dropped.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from collections.abc import Callable

import httpx
from cryptography.fernet import Fernet

from sudarshan.alerts import loopback
from sudarshan.alerts.outbox import Throttle
from sudarshan.autologin.handoff import seal

log = logging.getLogger("sudarshan.alerts.market")

PUSH_PATH = "/sudarshan/alerts/internal/push"
ALERT_EVENT = "sudarshan_market_alert"
STATE_EVENT = "sudarshan_market_alerts_state"
#: A batch waits this long for more items before it goes.
BATCH_S = 0.05
MAX_ITEMS = 50
#: The route takes 128 KiB of token (X9); a Fernet token is about 4/3 of its text plus 76 bytes.
MAX_TEXT = 90_000
#: Items waiting while OpenAlgo does not answer; the oldest go first, told as not sent.
MAX_PENDING = 500
TIMEOUT_S = 3.0
CONNECT_TIMEOUT_S = 1.0
NOT_ANSWERED = "OpenAlgo did not answer, so no app was told; it is in the alert history"
NONE_OPEN = "No app was open"

#: Called with (event ids of the fires a request carried, screens it reached or None when it
#: was not answered).
Result = Callable[[list[int], int | None], None]


def shown(screens: int) -> str:
    """The app delivery's words: 'Shown on 1 open screen', 'Shown on 2 open screens'."""
    return f"Shown on {screens} open screen{'' if screens == 1 else 's'}"


class Pusher:
    def __init__(
        self,
        base_url: str,
        client: httpx.AsyncClient,
        fernet: Fernet,
        on_result: Result,
    ):
        self.url = base_url.rstrip("/") + PUSH_PATH
        self.client, self.fernet, self.on_result = client, fernet, on_result
        self._pending: deque[tuple[dict, int | None]] = deque()
        self._wake = asyncio.Event()
        self.throttle = Throttle()
        self.stats = {"requests": 0, "items": 0, "failed": 0, "dropped": 0}

    def add(self, event: str, data: dict, event_id: int | None = None) -> None:
        """One item for the room; ``event_id`` names a fire whose app delivery it is."""
        if len(self._pending) >= MAX_PENDING:
            _, dropped = self._pending.popleft()
            self.stats["dropped"] += 1
            if dropped is not None:
                self.on_result([dropped], None)
        self._pending.append(({"event": event, "data": data}, event_id))
        self._wake.set()

    async def run(self) -> None:
        while True:
            await self._wake.wait()
            await asyncio.sleep(BATCH_S)  # the burst's other fires join this request
            self._wake.clear()
            while self._pending:
                await self._send(self._take())

    async def flush(self, timeout_s: float) -> int:
        """At a stop: send what waits, for at most ``timeout_s``; returns what was left."""
        deadline = time.monotonic() + timeout_s
        while self._pending and time.monotonic() < deadline:
            await self._send(self._take())
        return len(self._pending)

    def _take(self) -> list[tuple[dict, int | None]]:
        batch, size = [], 0
        while self._pending and len(batch) < MAX_ITEMS:
            item = self._pending[0]
            text = len(json.dumps(item[0], separators=(",", ":"), ensure_ascii=False))
            if batch and size + text > MAX_TEXT:
                break
            self._pending.popleft()
            if text > MAX_TEXT:  # one item larger than a request: never sent
                self.stats["dropped"] += 1
                log.error("a market alerts push of %d bytes was too large to send", text)
                if item[1] is not None:
                    self.on_result([item[1]], None)
                continue
            batch.append(item)
            size += text
        return batch

    async def _send(self, batch: list[tuple[dict, int | None]]) -> None:
        if not batch:
            return
        fires = [event_id for _, event_id in batch if event_id is not None]
        screens = await self._post([item for item, _ in batch])
        self.stats["requests"] += 1
        self.stats["items"] += len(batch)
        if screens is None:
            self.stats["failed"] += 1
        if fires:
            self.on_result(fires, screens)

    async def _post(self, items: list[dict]) -> int | None:
        refused = loopback.refusal(self.url)
        if refused:
            self.throttle(logging.ERROR, "listener", "Market alerts are not pushed to the app: %s",
                          refused)  # fmt: skip
            return None
        body = seal(self.fernet, "push", items=items)
        try:
            answer = await self.client.post(
                self.url,
                content=body,
                headers={"Content-Type": "application/octet-stream", "Accept": "application/json"},
                timeout=httpx.Timeout(TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
            )
            payload = answer.json()
        except (httpx.HTTPError, ValueError):
            self.throttle(
                logging.WARNING, "down", "OpenAlgo did not take market alerts for the app"
            )
            return None
        screens = payload.get("screens") if isinstance(payload, dict) else None
        if answer.status_code != 200 or not isinstance(screens, int) or isinstance(screens, bool):
            self.throttle(logging.ERROR, "refused", "OpenAlgo refused market alerts for the app "
                          "(it answered %d)", answer.status_code)  # fmt: skip
            return None
        return max(0, screens)
