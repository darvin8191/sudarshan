"""Alerts waiting for Telegram, and the one path they take there: OpenAlgo's /notify.

Telegram: OpenAlgo's ``POST /api/v1/telegram/notify`` (restx_api/telegram_bot.py) with the
owner's API key and OpenAlgo username. OpenAlgo looks up the Telegram chat linked to that username
and sends with its own bot, so this service never sees the bot token or a chat id and cannot
message anyone else. ``wait_for_delivery`` makes OpenAlgo answer only after Telegram did. A
success that says "queued for delivery" means Telegram did not take it (nothing in OpenAlgo sends
that queue later), so it is tried again here a few times; any other success is a delivery.
OpenAlgo refuses while its bot is stopped (409): those messages are dropped, never held back for
later, because the owner stopped the bot. Before the key leaves, the port's listener must be this
service's own user (loopback.py).

Pace: Telegram asks for about one message per second per chat, and OpenAlgo allows /notify 30
times a minute per address. So: at least MIN_GAP_S between two messages and at most PER_MINUTE a
minute, counted across every lane of the process by one Pacer (M10.5; the class is
``sudarshan/common/pacer.py``, this module keeps the owner bot's lanes: orders, market, notices
and reports, in that order of service). The market alerts lane
(market/lane.py) takes at most MARKET_PER_MINUTE of those and no slot while order alerts wait,
so the order lane always keeps at least PER_MINUTE - MARKET_PER_MINUTE a minute, and a fill
during a market storm waits at most the burst pause plus one gap. Each lane sends from its own
task: a market message waiting on Telegram never holds a fill back. A burst is merged: a message
waits COALESCE_S after the first alert for the rest of the burst, then lists every order waiting
(one line per order, its newest state; up to MAX_LINES lines and MAX_CHARS characters, the rest
in the next message).

Bounded: at most MAX_PENDING orders wait. Beyond that the oldest are dropped, and the next message
says how many could not be listed. Messages OpenAlgo could not take are tried again with a growing
pause for up to MAX_AGE, then dropped with a line in the journal.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

import httpx

from sudarshan.common.pacer import Lane
from sudarshan.common.pacer import Pacer as _Pacer

from . import loopback, messages
from .orders import Alert

log = logging.getLogger("sudarshan.alerts")

NOTIFY_PATH = "/api/v1/telegram/notify"
#: OpenAlgo itself waits up to 30 s for Telegram when asked to wait for the delivery.
NOTIFY_TIMEOUT_S = 40.0
COALESCE_S = 0.3
MIN_GAP_S = 1.1
PER_MINUTE = 20
#: The market alerts lane's share of PER_MINUTE (M10.5).
MARKET_PER_MINUTE = 12
MINUTE_S = 60.0
#: How often the order lane looks again while it waits for a slot.
SLOT_POLL_S = 0.2
MAX_LINES = 15
MAX_CHARS = 3500
MAX_PENDING = 300
MAX_AGE = timedelta(minutes=30)
#: Pauses after a message OpenAlgo could not take, growing to the last one.
RETRY_S = (2.0, 5.0, 10.0, 30.0, 60.0)
#: Tries of a message Telegram did not confirm (OpenAlgo answered "queued").
UNCONFIRMED_TRIES = 3
#: The one success answer that is not a delivery (restx_api/telegram_bot.py; a test reads it).
QUEUED = "queued for delivery"
FLUSH_S = 5.0

# What one attempt came to.
SENT = "sent"  # Telegram took it
RETRY = "retry"  # OpenAlgo or Telegram could not take it now
UNCONFIRMED = "unconfirmed"  # OpenAlgo answered, Telegram did not confirm
KEY = "key"  # OpenAlgo refused the API key: read it again, then retry
DROP = "drop"  # retrying will not help (bot stopped, chat not linked)

DROP_REASONS = {
    404: "the owner's OpenAlgo account is not linked to a Telegram chat (OpenAlgo, Telegram Bot)",
    409: "OpenAlgo's Telegram bot is stopped (OpenAlgo, Telegram Bot, Start)",
}
#: A refusal that is neither of the above, in the owner's words.
REFUSED = "OpenAlgo refused the message"

# Lanes of the Pacer, in their order of service.
ORDERS, MARKET, NOTICES, REPORTS = "orders", "market", "notices", "reports"
#: Server notices (notices.py) and the journal's reports (a later change) share the 12 a minute
#: that are left after the order lane's 8: a notice is at most 1 a minute, a report at most 4
#: (two slots each: its text, then its file).
NOTICES_PER_MINUTE = 1
REPORTS_PER_MINUTE = 4
#: A server notice ready to go that has waited this long for a slot takes the next one before a
#: market alert (review L2): through a steady market storm the two would otherwise race for every
#: slot of the shared twelve, with no bound on the notice's wait. It costs the market lane at most
#: one slot a minute (the notice lane's cap).
NOTICE_WAIT_S = 60.0


def _notices_cap() -> int:
    """The notice lane's cap is ``notices.NOTICES_PER_MINUTE``, read when asked (its tests and the
    desk's own NoticePace read the same constant). Imported here, not at the top: notices.py
    imports this module."""
    from . import notices

    return notices.NOTICES_PER_MINUTE


class Pacer(_Pacer):
    """The owner bot's send slots: the lanes orders, market, notices and reports (Y18).

    A ``sudarshan.common.pacer.Pacer`` with the owner bot's numbers, read from this module when
    asked so tests can shrink them. The order lane is served first and always keeps at least
    PER_MINUTE - MARKET_PER_MINUTE a minute of its own: the other lanes together get the rest, and
    the market lane takes at most MARKET_PER_MINUTE of it. The market lane and the notices lane
    take no slot while order alerts wait; the reports lane takes none while order alerts wait or
    the market lane has messages waiting. The market lane also takes none while a notice ready to
    go has waited NOTICE_WAIT_S for a slot (the notice desk's LanePace says since when).
    """

    def __init__(self) -> None:
        super().__init__(
            (
                Lane(ORDERS),
                Lane(
                    MARKET,
                    lambda: MARKET_PER_MINUTE,
                    yields_to=(ORDERS,),
                    yields_after=((NOTICES, lambda: NOTICE_WAIT_S),),
                ),
                Lane(NOTICES, _notices_cap, yields_to=(ORDERS,)),
                Lane(REPORTS, lambda: REPORTS_PER_MINUTE, yields_to=(ORDERS, MARKET)),
            ),
            gap_s=lambda: MIN_GAP_S,
            per_minute=lambda: PER_MINUTE,
            minute_s=lambda: MINUTE_S,
            reserved={ORDERS: lambda: PER_MINUTE - MARKET_PER_MINUTE},
        )

    @property
    def orders_waiting(self) -> Callable[[], bool]:
        """Whether order alerts wait (the Outbox sets it; a test may set a stand-in)."""
        return lambda: self.waiting(ORDERS)

    @orders_waiting.setter
    def orders_waiting(self, waiting: Callable[[], bool]) -> None:
        self.set_waiting(ORDERS, waiting)

    @property
    def flushing_orders(self) -> bool:
        """Set while order alerts are flushed at a stop: what the other lanes sent in the last
        minute no longer counts against them, so they never lose to its share (review L10)."""
        return self.flushing_reserved

    @flushing_orders.setter
    def flushing_orders(self, value: bool) -> None:
        self.flushing_reserved = value


class Throttle:
    """Logs the first occurrence of each kind, then at most once per ``every`` seconds."""

    def __init__(self, every: float = 600.0):
        self.every, self._seen = every, {}

    def __call__(self, level: int, kind: str, message: str, *args) -> None:
        now = time.monotonic()
        last, quiet = self._seen.get(kind, (None, 0))
        if last is not None and now - last < self.every:
            self._seen[kind] = (last, quiet + 1)
            return
        suffix = f" ({quiet} similar suppressed)" if quiet else ""
        log.log(level, message + suffix, *args)
        self._seen[kind] = (now, 0)


class OpenAlgoTelegram:
    """One message to the owner's linked Telegram chat, through OpenAlgo on this server.

    Args:
        base_url: OpenAlgo on this server, ``http://127.0.0.1:5000``.
        client: The service's HTTP client (no proxies, no redirects).
        owner: Returns the Owner (username and key) in use, or None.
        reload_owner: Reads the owner from openalgo.db again (after a refused key).
    """

    def __init__(self, base_url: str, client: httpx.AsyncClient, owner, reload_owner):
        self.url = base_url.rstrip("/") + NOTIFY_PATH
        self.client = client
        self.owner = owner
        self.reload_owner = reload_owner
        self.throttle = Throttle()

    async def send(self, text: str) -> str:
        return (await self.send_with_reason(text))[0]

    async def send_with_reason(self, text: str) -> tuple[str, str]:
        """The outcome, and for DROP the reason in the owner's words (else "")."""
        owner = self.owner()
        if owner is None:
            return RETRY, ""
        refused = loopback.refusal(self.url)
        if refused:
            self.throttle(logging.ERROR, "listener", "The API key is not sent to OpenAlgo: %s",
                          refused)  # fmt: skip
            return RETRY, ""
        outcome, why = await self._post(owner, text)
        if outcome == KEY:
            fresh = self.reload_owner()
            if fresh is not None and fresh.api_key != owner.api_key:
                log.info("OpenAlgo has a new API key; using it")
                outcome, why = await self._post(fresh, text)
            if outcome == KEY:
                self.throttle(logging.ERROR, "key", "OpenAlgo refused the stored API key")
                outcome = RETRY
        return outcome, why

    async def _post(self, owner, text: str) -> tuple[str, str]:
        body = {
            "apikey": owner.api_key,
            "username": owner.username,
            "message": text,
            "wait_for_delivery": True,
        }
        try:
            answer = await self.client.post(self.url, json=body, timeout=NOTIFY_TIMEOUT_S)
        except httpx.HTTPError:
            self.throttle(logging.WARNING, "down", "OpenAlgo did not answer; the message waits")
            return RETRY, ""
        status = answer.status_code
        if status == 200:
            try:
                said = str(answer.json().get("message", ""))
            except (ValueError, AttributeError):
                said = ""
            if QUEUED not in said.lower():
                return SENT, ""
            self.throttle(logging.WARNING, "unconfirmed", "Telegram did not confirm a message")
            return UNCONFIRMED, ""
        if status in (401, 403):
            return KEY, ""
        if status in DROP_REASONS:
            reason = DROP_REASONS[status]
            self.throttle(logging.WARNING, f"drop-{status}", "No Telegram message: %s", reason)
            return DROP, reason
        if status == 429 or status >= 500:
            self.throttle(logging.WARNING, "busy", "OpenAlgo is busy or failing; the message waits")
            return RETRY, ""
        self.throttle(logging.ERROR, "refused", "OpenAlgo refused a message; it is not sent again")
        log.debug("OpenAlgo answered %d to a message", status)
        return DROP, REFUSED


class Outbox:
    """Pending alerts, one per order (the newest state), sent in merged, paced messages.

    Args:
        send: Sends one text; returns SENT, RETRY, UNCONFIRMED or DROP.
        clock: The service clock (aware datetimes), for the age limit.
        pacer: The process's shared send slots (a private one when not given).
    """

    def __init__(
        self,
        send: Callable[[str], Awaitable[str]],
        clock: Callable[[], datetime],
        pacer: Pacer | None = None,
    ):
        self.send = send
        self.clock = clock
        self._pending: OrderedDict[str, Alert] = OrderedDict()
        self._since: float | None = None  # monotonic time the oldest pending alert arrived
        self._dropped = 0
        self.pacer = pacer or Pacer()
        self.pacer.orders_waiting = lambda: bool(self._pending)  # the market lane gives way
        self._wake = asyncio.Event()
        self._hurry = False
        self._in_flight = 0  # lines in the message being sent now
        self.throttle = Throttle()
        self.stats = {"messages": 0, "lines": 0, "dropped": 0, "failed": 0}

    def __len__(self) -> int:
        return len(self._pending)

    def add(self, alerts: list[Alert]) -> None:
        for alert in alerts:
            if alert.key in self._pending:
                self._pending[alert.key] = alert  # same place, newest state
                continue
            if len(self._pending) >= MAX_PENDING:
                self._pending.popitem(last=False)
                self._dropped += 1
                self.stats["dropped"] += 1
            self._pending[alert.key] = alert
        if self._pending:
            if self._since is None:
                self._since = time.monotonic()
            self._wake.set()

    async def run(self) -> None:
        """Send until cancelled."""
        failures = unconfirmed = 0
        while True:
            if not self._pending:
                self._wake.clear()
                await self._wake.wait()
                continue
            if not self._hurry:
                await self._sleep_until(self._since + COALESCE_S)
            await self._slot()
            batch, dropped = self._take()
            self.pacer.took(ORDERS)
            self._in_flight = len(batch)
            try:
                outcome = await self.send(self._compose(batch, dropped))
            finally:
                self._in_flight = 0
            if outcome == SENT:
                failures = unconfirmed = 0
                self.stats["messages"] += 1
                self.stats["lines"] += len(batch)
                continue
            if outcome == DROP:
                self.stats["failed"] += len(batch)
                continue
            if outcome == UNCONFIRMED:
                unconfirmed += 1
                if unconfirmed >= UNCONFIRMED_TRIES:
                    unconfirmed = 0
                    self.stats["failed"] += len(batch)
                    self.throttle(
                        logging.ERROR, "gave-up",
                        "Telegram did not confirm a message after %d tries; it is not sent again",
                        UNCONFIRMED_TRIES,
                    )  # fmt: skip
                    continue
            self._put_back(batch, dropped)
            delay = RETRY_S[min(failures, len(RETRY_S) - 1)]
            failures += 1
            await asyncio.sleep(delay)

    async def flush(self, timeout: float = FLUSH_S) -> int:
        """Send what waits without the burst pause, for at most ``timeout``; returns what is left.
        Order alerts go first, and the market lane's share of the minute does not hold them.

        A message on its way counts as left until its answer: the caller cancels the sender next,
        and that would cut it off."""
        self._hurry = True
        self.pacer.flushing_orders = True
        self._wake.set()
        deadline = time.monotonic() + timeout
        try:
            while (self._pending or self._in_flight) and time.monotonic() < deadline:
                await asyncio.sleep(0.05)
        finally:
            self.pacer.flushing_orders = False
        return len(self._pending) + self._in_flight

    # -- internals ------------------------------------------------------------------------------

    async def _slot(self) -> None:
        """Until the pacer gives the order lane a slot, looking again often enough to take one a
        stop's flush frees (Pacer.flushing_orders)."""
        while (wait := self.pacer.allowed_at(ORDERS) - time.monotonic()) > 0:
            await asyncio.sleep(min(wait, SLOT_POLL_S))

    @staticmethod
    async def _sleep_until(when: float) -> None:
        delay = when - time.monotonic()
        if delay > 0:
            await asyncio.sleep(delay)

    def _take(self) -> tuple[list[Alert], int]:
        batch: list[Alert] = []
        dropped, self._dropped = self._dropped, 0
        for key in list(self._pending):
            alert = self._pending[key]
            full = len(batch) >= MAX_LINES
            if batch and (full or len(self._compose([*batch, alert], dropped)) > MAX_CHARS):
                break
            batch.append(alert)
            del self._pending[key]
        self._since = time.monotonic() if self._pending else None
        return batch, dropped

    def _put_back(self, batch: list[Alert], dropped: int) -> None:
        """Back to the front, unless a newer state of that order arrived meanwhile or it is old."""
        oldest_kept = self.clock() - MAX_AGE
        for alert in reversed(batch):
            if alert.key in self._pending:
                continue
            if alert.at < oldest_kept:
                self.stats["failed"] += 1
                age = int(MAX_AGE.total_seconds() // 60)
                self.throttle(
                    logging.ERROR, "too-old",
                    "An order alert could not be sent for %d minutes and is dropped", age,
                )  # fmt: skip
                continue
            self._pending[alert.key] = alert
            self._pending.move_to_end(alert.key, last=False)
        self._dropped += dropped
        if self._pending and self._since is None:
            self._since = time.monotonic()

    @staticmethod
    def _compose(batch: list[Alert], dropped: int) -> str:
        first = min(alert.at for alert in batch)
        last = max(alert.at for alert in batch)
        return messages.compose([alert.line for alert in batch], first, last, dropped)
