"""sudarshan-alerts: the owner's order updates to his Telegram, as they happen (M6.8).

    Upstox Portfolio Stream Feed -> OpenAlgo's order adapter (in gunicorn) -> event bus -> ZMQ
      -> OpenAlgo's WebSocket proxy, ws://127.0.0.1:8765
      -> this service: authenticate (the owner's API key), subscribe_orders, order_update frames
      -> OrderTracker (orders.py): is this change worth a message? claimed once in alerts.db
      -> Outbox (outbox.py): merged and paced -> POST 127.0.0.1:5000/api/v1/telegram/notify
      -> OpenAlgo's Telegram bot -> the owner's linked chat

A standalone asyncio process, never inside the OpenAlgo app (no thread, scheduler or event loop is
added to gunicorn). It holds the proxy connection only on trading days inside the alert window
(08:45-16:00 IST). While any client of the owner is signed in, the proxy keeps one pooled Upstox
market-data connection for him open, so this service keeps it open through the window.

The proxy replays nothing on connect. So after every subscription the order book is read
(resync.py) and what changed while nothing was listening is told, marked as found later; a pause
longer than GAP_S inside the window is told too when anything was found, or when the book could
still not be read after the tries in RESYNC_RETRY_S, unless the pause ended before the market's
start (nothing trades before the pre-open). The last moment the stream was up is kept in
alerts.db, so a restart knows its pause.

Reconnects: a fresh read of the API key from openalgo.db for every attempt (a regenerated key is
picked up), then backoff with jitter (2 s doubling to 60 s), reset only after a connection stayed
up STABLE_AFTER_S, so an accept-then-drop loop can never become a tight storm. The key goes only
to a port this service's own user listens on (loopback.py), and the WebSocket client follows no
redirect.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import random
import signal
import time
from collections.abc import Callable
from datetime import datetime

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI

from sudarshan.autologin.schedule import IST, TradingCalendar, hhmm

from . import __version__, loopback, messages
from .config import ConfigError, Settings, setup_logging, switched_off
from .market.config import MarketSettings, feed_url_of, http_port_of, market_on  # M10.1
from .orders import Alert, OrderTracker
from .outbox import OpenAlgoTelegram, Outbox, Pacer, Throttle
from .owner import Owner, OwnerError, load_openalgo_modules, prepare_openalgo_env, read_owner
from .resync import Books, BookUnavailable, read_books
from .store import DB_FILENAME, Store
from .window import AlertWindow

log = logging.getLogger("sudarshan.alerts")

# Timings are module constants so the tests can shrink them.
BACKOFF_BASE_S, BACKOFF_CAP_S = 2.0, 60.0
STABLE_AFTER_S = 60.0  # a connection that lived this long resets the backoff ladder
KEY_REFUSED_S = 60.0  # the proxy refused the key: read it again after this
NOT_SUBSCRIBED_S = 30.0  # also after a refused listener
NO_OWNER_S = 60.0  # no user or no API key yet: look again after this
CLOSED_POLL_S = 30.0
WATCH_EVERY_S = 1.0
TICK_S = 0.25  # how often stop orders waiting out the settle time are checked
AUTH_TIMEOUT_S = 30.0  # the proxy connects its broker adapter before it answers
ACK_TIMEOUT_S = 10.0
STOP_FLUSH_S = 5.0
SEEN_EVERY_S = 10.0  # how often the moment the stream was last up is written down
GAP_S = 10.0  # a pause longer than this inside the window is told (see the module docstring)
#: Waits before reading the order book again after a failed read, while the stream stays up:
#: about three minutes in all, as OpenAlgo can take that long to answer after a restart.
RESYNC_RETRY_S = (5.0, 10.0, 20.0, 40.0, 60.0, 60.0)
REQUEST_ID = "sudarshan-alerts"
SEEN = "stream_seen"
WS_OPTIONS = {
    "proxy": None, "open_timeout": 10, "close_timeout": 5, "ping_interval": 20,
    "ping_timeout": 20, "max_size": 1 << 20, "max_queue": 64,
}  # fmt: skip
STOPPING, WINDOW_CLOSED = "service stopping", "alert window closed"

OWNER_PROBLEMS = {
    "no_user": "OpenAlgo has no user account yet. Order alerts wait for one.",
    "no_key": (
        "No OpenAlgo API key yet. Order alerts start once one is generated in OpenAlgo "
        "(profile menu, API Key); nothing is sent until then."
    ),
    "unreadable_key": (
        "The OpenAlgo API key cannot be decrypted with the pepper and salt in OpenAlgo's .env. "
        "If they were changed, restart sudarshan-alerts."
    ),
    "database": "OpenAlgo's database could not be read. Trying again.",
}


class _Connect(connect):
    """The WebSocket client without redirects: the key must not follow one anywhere."""

    def process_redirect(self, exc: Exception) -> Exception:
        return exc


class AlertService:
    """Dependencies are passed in so the tests drive it against fakes."""

    def __init__(
        self,
        settings: Settings,
        *,
        owner_reader: Callable[[], Owner],
        clock: Callable[[], datetime] | None = None,
        pacer: Pacer | None = None,  # M10.5: one pace for every Telegram lane
    ):
        self.settings = settings
        self.read_owner = owner_reader
        self.clock = clock or (lambda: datetime.now(IST))
        self.pacer = pacer
        calendar = TradingCalendar(settings.holidays_file, settings.extra_days_file)
        self.window = AlertWindow(
            calendar, settings.window, settings.always_on, settings.extra_days_file
        )
        self.owner: Owner | None = None
        self.store: Store | None = None
        self.tracker: OrderTracker | None = None
        self.outbox: Outbox | None = None
        self.client: httpx.AsyncClient | None = None
        self._stop = asyncio.Event()
        self._state, self._detail = "starting", ""
        self._owner_problem: str | None = None
        self._close_reason: str | None = None
        self._last_session_s = 0.0
        self._unchecked: datetime | None = None  # the start of a pause whose book read was cut off
        self._after_close: set[asyncio.Task] = set()  # book reads the window's close let finish
        self._throttle = Throttle(300.0)
        self.stats = {"connects": 0, "updates": 0, "alerts": 0}

    # ---- lifecycle -------------------------------------------------------------------------
    async def run(self) -> None:
        if not self.settings.enabled:
            log.info("Order alerts are off (SUDARSHAN_ORDER_ALERTS=off): nothing is sent")
            self._set_state("off")
            return
        self.store = Store(self.settings.state_dir / DB_FILENAME)
        if removed := self.store.prune(self.clock()):
            log.info("forgot %d alert records older than a week", removed)
        self.tracker = OrderTracker(
            self.store, cancelled=self.settings.cancelled, sandbox=self.settings.sandbox
        )
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
            self.client = client
            telegram = OpenAlgoTelegram(
                self.settings.openalgo_url, client, lambda: self.owner, self._reload_owner
            )
            self.outbox = Outbox(telegram.send, self.clock, self.pacer)
            log.info(
                "sudarshan-alerts %s: window %s, cancellations %s, sandbox %s",
                __version__, self._window_text(), self.settings.cancelled,
                "on" if self.settings.sandbox else "off",
            )  # fmt: skip
            sender = asyncio.create_task(self.outbox.run())
            ticker = asyncio.create_task(self._tick())
            try:
                await self._supervise()
            finally:
                ticker.cancel()
                unfinished = list(self._after_close)
                for task in unfinished:  # a pause still unread is told as such (_resync)
                    task.cancel()
                await asyncio.gather(*unfinished, return_exceptions=True)
                left = await self.outbox.flush(STOP_FLUSH_S)
                if left:
                    log.warning("%d order alerts were not sent before stopping", left)
                sender.cancel()
                await asyncio.gather(sender, ticker, return_exceptions=True)
                self.store.close()
                self._set_state("stopped")

    def stop(self, reason: str = "stop requested") -> None:
        if not self._stop.is_set():
            log.info("stopping: %s", reason)
            self._stop.set()

    async def _sleep(self, seconds: float) -> None:
        """Sleep, returning early when stop() is called."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stop.wait(), max(0.0, seconds))

    # ---- the connection supervisor -----------------------------------------------------------
    async def _supervise(self) -> None:
        failures = 0
        while not self._stop.is_set():
            now = self.clock()
            is_open, why = self.window.status(now)
            if not is_open:
                if self._state != "closed":
                    self._window_closed()
                self._set_state("closed", why)
                opens = self.window.next_open(now)
                wait = CLOSED_POLL_S if opens is None else (opens - now).total_seconds()
                await self._sleep(min(CLOSED_POLL_S, max(1.0, wait)))
                failures = 0
                continue
            owner = self._load_owner()
            if owner is None:
                self._set_state("waiting", self._owner_problem or "")
                await self._sleep(NO_OWNER_S)
                continue
            try:
                outcome = await self._session(owner)
            except Exception:  # a bug must cost one connection, never the service
                log.exception("the order stream failed unexpectedly")
                outcome = "error"
            if self._stop.is_set():
                break
            if outcome == "window":
                continue
            failures = failures_after(failures, self._last_session_s)
            delay = self._retry_delay(outcome, failures)
            self._set_state("backoff", f"next attempt in {delay:.0f}s")
            await self._sleep(delay)

    def _retry_delay(self, outcome: str, failures: int) -> float:
        if outcome == "key_refused":
            return KEY_REFUSED_S
        ceiling = min(BACKOFF_CAP_S, BACKOFF_BASE_S * 2 ** min(failures - 1, 16))
        delay = ceiling / 2 + random.uniform(0, ceiling / 2)  # "equal jitter"
        if outcome in ("not_subscribed", "refused"):
            delay = max(delay, NOT_SUBSCRIBED_S)
        return delay

    def _load_owner(self) -> Owner | None:
        """The owner and his key, read again for every connection attempt."""
        try:
            owner = self.read_owner()
        except OwnerError as exc:
            problem = exc.kind
        except Exception:
            log.exception("reading the OpenAlgo API key failed unexpectedly")
            problem = "database"
        else:
            self._use(owner)
            return owner
        if problem in ("database", "unreadable_key") and self.owner is not None:
            return self.owner  # a failed read: the key in use is the best one known
        if problem != self._owner_problem:
            log.warning(OWNER_PROBLEMS[problem])
        self.owner, self._owner_problem = None, problem
        return None

    def _use(self, owner: Owner) -> None:
        if self._owner_problem is not None or self.owner is None:
            log.info("OpenAlgo API key found")
        elif owner.api_key != self.owner.api_key:
            log.info("OpenAlgo has a new API key; using it")
        self.owner, self._owner_problem = owner, None

    def _reload_owner(self) -> Owner | None:
        """After OpenAlgo refused the key: read it again. A failed read keeps the key in use."""
        try:
            owner = self.read_owner()
        except Exception:  # OwnerError or worse: the session keeps what it has
            log.debug("reading the API key again failed", exc_info=True)
            return None
        self._use(owner)
        return owner

    async def _session(self, owner: Owner) -> str:
        """One connect -> authenticate -> subscribe -> read cycle; returns how it ended."""
        self._close_reason, self._last_session_s = None, 0.0
        holder = loopback.holder(self.settings.proxy_url)
        if holder == loopback.NOBODY:
            self._throttle(
                logging.WARNING, "unreachable",
                "OpenAlgo's WebSocket proxy is not reachable; retrying",
            )  # fmt: skip
            return "unreachable"
        if holder != loopback.OURS:
            self._throttle(
                logging.ERROR, "listener", "The API key is not sent to the WebSocket proxy: %s",
                loopback.refusal(self.settings.proxy_url),
            )  # fmt: skip
            return "refused"
        started: float | None = None
        resync: asyncio.Task | None = None
        self._set_state("connecting")
        try:
            async with _Connect(self.settings.proxy_url, **WS_OPTIONS) as ws:
                # From the first moment: a stop during the sign-in must not wait for its answer.
                watchdog = asyncio.create_task(self._watchdog(ws))
                try:
                    answer = await self._handshake(ws, owner)
                    if answer != "ok":
                        self._close_reason = answer
                        await ws.close(1000, "not subscribed")
                        return answer
                    started = time.monotonic()
                    self.stats["connects"] += 1
                    self._set_state("connected", "receiving order updates")
                    pause = self._pause(self.clock())
                    self._unchecked = pause[0] if pause is not None else None
                    self._mark_seen()
                    resync = asyncio.create_task(self._resync(owner, pause))
                    async for raw in ws:
                        self._on_message(raw, owner)
                finally:
                    tasks = [watchdog]
                    if resync is not None:
                        if self._close_reason == WINDOW_CLOSED and not resync.done():
                            self._finish_after_close(resync)
                        else:
                            tasks.append(resync)
                    for task in tasks:
                        task.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                return self._ended("closed by OpenAlgo")
        except ConnectionClosed:
            return self._ended("dropped")
        except (OSError, TimeoutError, InvalidHandshake, InvalidURI):
            self._throttle(
                logging.WARNING, "unreachable",
                "OpenAlgo's WebSocket proxy is not reachable; retrying",
            )  # fmt: skip
            log.debug("connecting to the proxy failed", exc_info=True)
            return "unreachable"
        finally:
            if started is not None:
                self._last_session_s = time.monotonic() - started
                self._mark_seen()

    def _ended(self, how: str) -> str:
        reason = self._close_reason
        if reason in (STOPPING, WINDOW_CLOSED):
            log.info("order stream closed: %s", reason)
            return "window" if reason == WINDOW_CLOSED else "stopped"
        log.warning("The order stream was %s; reconnecting", how)
        return "dropped"

    async def _handshake(self, ws, owner: Owner) -> str:
        await ws.send(json.dumps({"action": "authenticate", "api_key": owner.api_key}))
        answer = await self._answer(ws, AUTH_TIMEOUT_S, owner, ("auth",))
        if answer is None:
            log.warning("OpenAlgo's WebSocket proxy did not answer the sign-in; retrying")
            return "no_answer"
        if answer.get("status") != "success":
            if answer.get("code") == "AUTHENTICATION_ERROR":
                self._throttle(
                    logging.WARNING, "key", "OpenAlgo's WebSocket proxy refused the API key; "
                    "reading it again in %.0f s", KEY_REFUSED_S,
                )  # fmt: skip
                return "key_refused"
            # The key was accepted (the proxy maps this connection to the owner first) but its
            # market-data broker connection is not up: no Upstox login yet today, or an expired
            # one. Order updates do not need it; they start with the next login.
            self._throttle(
                logging.INFO, "broker",
                "OpenAlgo has no working Upstox connection yet; order alerts start with the login",
            )  # fmt: skip
        await ws.send(json.dumps({"action": "subscribe_orders", "request_id": REQUEST_ID}))
        ack = await self._answer(ws, ACK_TIMEOUT_S, owner, ("subscribe_orders",))
        if ack is None or ack.get("status") != "success":
            log.warning("OpenAlgo's WebSocket proxy did not subscribe this service to orders")
            return "not_subscribed"
        return "ok"

    async def _answer(self, ws, timeout: float, owner: Owner, types: tuple[str, ...]):
        """The first reply of one of ``types`` (or an error), handling anything else meanwhile."""
        deadline = time.monotonic() + timeout
        while (left := deadline - time.monotonic()) > 0:
            try:
                raw = await asyncio.wait_for(ws.recv(), left)
            except TimeoutError:
                return None
            message = _json(raw)
            if message is None:
                continue
            if message.get("type") in types or message.get("status") == "error":
                return message
            self._on_message(raw, owner)
        return None

    async def _watchdog(self, ws) -> None:
        """Closes the stream cleanly on stop and when the window ends; notes it is still up."""
        seen_at = time.monotonic()
        while True:
            await self._sleep(WATCH_EVERY_S)
            if self._stop.is_set():
                self._close_reason = STOPPING
            elif not self.window.status(self.clock())[0]:
                self._close_reason = WINDOW_CLOSED
            else:
                if self._state == "connected" and time.monotonic() - seen_at >= SEEN_EVERY_S:
                    self._mark_seen()
                    seen_at = time.monotonic()
                continue
            await ws.close(1000, self._close_reason)
            return

    # ---- after a pause: the order book -------------------------------------------------------
    def _pause(self, now: datetime) -> tuple[datetime, datetime] | None:
        """From when the stream was last known up, or from the start of a pause whose book read a
        drop cut off, but not before today's window opened, to now."""
        seen = self.store.get_meta(SEEN)
        known = [datetime.fromtimestamp(seen, IST)] if seen is not None else []
        if self._unchecked is not None:
            known.append(self._unchecked)
        starts = [min(known)] if known else []
        opened = self.window.opened_at(now)
        if opened is not None:
            starts.append(opened)
        if not starts:
            return None
        start = max(starts)
        return (start, now) if start < now else None

    def _mark_seen(self) -> None:
        self.store.set_meta(SEEN, self.clock().timestamp())

    def _finish_after_close(self, resync: asyncio.Task) -> None:
        """The window closed while the book was still being read after a pause. The tries go on
        without the stream (they only read OpenAlgo's REST books): nothing after the close would
        look for that pause again, the next day's catch-up included."""
        self._after_close.add(resync)
        resync.add_done_callback(self._after_close.discard)

    async def _resync(self, owner: Owner, pause: tuple[datetime, datetime] | None) -> None:
        """Tell what changed while nothing was listening. A book that cannot be read is tried
        again (RESYNC_RETRY_S) while the stream stays up, and on past the window's close; a drop
        ends the tries, and the pause then carries into the next connection's."""
        try:
            books = await self._read_books_after_pause(owner)
        except asyncio.CancelledError:
            if self._stop.is_set() and asyncio.current_task() in self._after_close:
                self._tell_pause(pause, None)  # stopping after the close: none will look again
            raise
        self._tell_pause(pause, books)

    async def _read_books_after_pause(self, owner: Owner) -> Books | None:
        """The order book, tried RESYNC_RETRY_S times more; None when every try failed."""
        tries = 1 + len(RESYNC_RETRY_S)
        for attempt, delay in enumerate((0.0, *RESYNC_RETRY_S), 1):
            if delay:
                await asyncio.sleep(delay)
            try:
                return await read_books(self.client, self.settings.openalgo_url, owner.api_key)
            except BookUnavailable as exc:
                log.info(
                    "The order book could not be read after the pause (try %d of %d): %s",
                    attempt, tries, exc,
                )  # fmt: skip
            except Exception:
                log.exception("reading the order book after the pause failed unexpectedly")
        return None

    def _tell_pause(self, pause: tuple[datetime, datetime] | None, books: Books | None) -> None:
        """What the book showed, and the pause itself when it is worth a line. No await from here
        to the queue: a cancelled session must not leave alerts claimed in the store but never
        queued."""
        found: list[Alert] = []
        if books is not None:
            now = self.clock()
            for message in books.messages:
                found += self.tracker.on_update(message, now, found_later=True)
        if books is None:
            book = "unreadable"
        elif books.live:
            book = "checked"
        else:
            book = "sandbox"
            log.info("OpenAlgo is in Analyze mode, so its order book is the sandbox's")
        told = found
        if pause is not None and (pause[1] - pause[0]).total_seconds() > GAP_S:
            text = messages.gap_line(pause[0], pause[1], book)
            opens = self.window.market_opens(pause[1])
            early = opens is not None and pause[1] <= opens  # nothing trades before the pre-open
            if found or (book != "checked" and not early):
                key = f"gap:{pause[1].timestamp():.0f}"
                told = [Alert(key, "gap", 0, text, pause[1]), *found]
                if book == "unreadable":
                    log.warning("Told the owner: %s", text)
            elif book == "checked":
                log.info("%s Nothing had changed.", text)
            else:
                log.info(
                    "%s Not sent: it ended before the market's start at %s.", text, hhmm(opens)
                )
        if pause is not None and self._unchecked == pause[0]:
            self._unchecked = None  # its pause is told; one a later connection began stays
        self._queue(told)

    # ---- the data path -----------------------------------------------------------------------
    def _on_message(self, raw, owner: Owner) -> None:
        message = _json(raw)
        if message is None:
            self._throttle(logging.WARNING, "junk", "OpenAlgo sent a message that is not JSON")
            return
        if message.get("type") != "order_update":
            if message.get("status") == "error":
                self._throttle(
                    logging.WARNING, "proxy-error", "OpenAlgo's WebSocket proxy reported: %s",
                    str(message.get("message", ""))[:160],
                )  # fmt: skip
            return
        if message.get("user_id") not in (None, "", owner.username):
            return  # not the owner's account (the proxy sends only his; this is a second check)
        self.stats["updates"] += 1
        try:
            alerts = self.tracker.on_update(message, self.clock())
        except Exception:  # one bad update must cost one message, never the stream
            self._throttle(logging.ERROR, "update", "an order update could not be handled")
            log.debug("order update handling failed", exc_info=True)
            return
        self._queue(alerts)

    async def _tick(self) -> None:
        while True:
            await asyncio.sleep(TICK_S)
            try:
                self._queue(self.tracker.due(self.clock()))
            except Exception:
                self._throttle(logging.ERROR, "tick", "checking stop-order triggers failed")
                log.debug("trigger check failed", exc_info=True)

    def _queue(self, alerts) -> None:
        for alert in alerts:
            log.info("order %s: %s", alert.key, alert.kind)
        if alerts:
            self.stats["alerts"] += sum(1 for alert in alerts if alert.kind != "gap")
            self.outbox.add(alerts)

    # ---- state ---------------------------------------------------------------------------------
    def _window_closed(self) -> None:
        """The day's summary (its own counts, then they start again), and the pruning."""
        if self._state not in ("starting", "off"):
            sent = self.outbox.stats if self.outbox else {}
            log.info(
                "alert window closed: %d updates, %d alerts, %d messages, %d not sent",
                self.stats["updates"], self.stats["alerts"], sent.get("messages", 0),
                sent.get("failed", 0) + sent.get("dropped", 0),
            )  # fmt: skip
            self.stats = dict.fromkeys(self.stats, 0)
            if self.outbox:
                self.outbox.stats = dict.fromkeys(self.outbox.stats, 0)
        with contextlib.suppress(Exception):
            self.store.prune(self.clock())

    def _set_state(self, state: str, detail: str = "") -> None:
        if (state, detail) != (self._state, self._detail):
            log.info("state %s -> %s%s", self._state, state, f" ({detail})" if detail else "")
            self._state, self._detail = state, detail

    @property
    def state(self) -> str:
        return self._state

    def _window_text(self) -> str:
        if self.settings.always_on:
            return "always on"
        start, end = self.settings.window
        return f"{start // 60:02d}:{start % 60:02d}-{end // 60:02d}:{end % 60:02d} IST"


def failures_after(failures: int, lived_s: float) -> int:
    """The backoff ladder's step after a connection that lived ``lived_s`` seconds."""
    return 1 if lived_s >= STABLE_AFTER_S else failures + 1


def _json(raw) -> dict | None:
    try:
        message = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return message if isinstance(message, dict) else None


async def _serve(service: AlertService, market=None, api=None, port: int = 0) -> None:
    loop = asyncio.get_running_loop()
    if api is not None:  # M10.1: the market alerts' sealed local API, before anything else
        market, api = await _open_api(api, port, market)
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _stop, service, market, f"signal {sig.name}")
    try:
        await asyncio.gather(service.run(), *([market.run()] if market else []))  # M10.1
    finally:
        if api is not None:
            await api.close()


async def _open_api(api, port: int, market):
    """M10.1: the API on 127.0.0.1:<port>. A port that is taken keeps market alerts off (their
    claims are dropped at the feed) and order alerts carry on; no other port is tried."""
    from .market.runtime import MarketRuntime, ReleasedClaims

    try:
        await api.start(port)
    except OSError as exc:
        log.error(
            "Market alerts cannot open their local API: port %d on this server is taken (%s). "
            "Market alerts stay off; order alerts carry on. Free the port, then restart "
            "sudarshan-alerts.", port, exc.strerror or exc,
        )  # fmt: skip
        if isinstance(market, MarketRuntime):
            market = ReleasedClaims(market.config.feed_url)
        return market, None
    return market, api


def _stop(service: AlertService, market, reason: str) -> None:
    service.stop(reason)
    if market is not None:  # M10.1
        market.stop(reason)


def _market(settings: Settings, market: MarketSettings | None, db_path, pacer: Pacer):
    """M10.1: market alerts and their local API, or, switched off, the API saying so while the
    feed drops the keys an earlier run claimed."""
    from sudarshan.vault import ALERTS_API_INFO, fernet_for, pepper_from

    from .market.api import MarketApi, OffDesk
    from .market.desk import OwnerDesk
    from .market.runtime import MarketRuntime, ReleasedClaims

    fernet = fernet_for(pepper_from(os.environ), ALERTS_API_INFO)
    if market is None:
        return ReleasedClaims(feed_url_of()), MarketApi(OffDesk(), fernet), http_port_of()
    runtime = MarketRuntime(settings, market, owner_reader=lambda: read_owner(db_path),
                            pacer=pacer, openalgo_db=db_path)  # fmt: skip
    return runtime, MarketApi(OwnerDesk(runtime), fernet), market.http_port


def main() -> int:
    setup_logging()
    try:
        market_wanted = market_on()  # M10.1: the process ends only when both are off
        if switched_off() and not market_wanted:
            log.info("Order alerts are off (SUDARSHAN_ORDER_ALERTS=off): nothing is sent")
            return 0  # the unit restarts on failure only, so it stays stopped
        settings = Settings.from_env()
        market = MarketSettings.from_env() if market_wanted else None  # M10.1
        db_path = prepare_openalgo_env(settings.env_file, settings.app_dir)
        load_openalgo_modules()
        setup_logging()  # OpenAlgo's logging module replaced the handlers on import
    except ConfigError as exc:
        log.error("sudarshan-alerts cannot run: %s", exc)
        return 2
    except Exception:  # OpenAlgo's modules refused their settings at import
        log.exception("sudarshan-alerts cannot load OpenAlgo's database modules")
        return 2
    pacer = Pacer()  # M10.5: fills first, market alerts in what is left
    service = AlertService(settings, owner_reader=lambda: read_owner(db_path), pacer=pacer)
    runtime, api, port = _market(settings, market, db_path, pacer)  # M10.1
    try:
        asyncio.run(_serve(service, runtime, api, port))
    except Exception:  # a bug: systemd restarts it
        log.exception("sudarshan-alerts stopped unexpectedly")
        return 1
    return 0
