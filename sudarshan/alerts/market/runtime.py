"""Market alerts at run time (M10.1, M10.9): the feed's ticks through the engine to the owner.

    sudarshan-feed -> FeedLink (ZMQ ticks and health, HTTP claims and snapshots)
      -> Book (the pure engine) -> market.db (one writer thread) -> MarketLane (Telegram, behind
      the order alerts' fills) and, with M10.7, the app's owner room

It runs in sudarshan-alerts' own asyncio loop, beside the order alerts and never inside OpenAlgo,
only while ``SUDARSHAN_MARKET_ALERTS=on``. A failure here turns market alerts off with words in
the journal; it never takes the order alerts down.

What it owes the engine (the independent review's A7, cross-design X6, X7):

- The day (A7.a-c): ``days.Days`` builds Market.sessions and the trading day before from the
  calendar files, asked again every 30 s and at midnight; the CAS keys come from the master
  contract each morning (X6a).
- Feed health (A7.d, e): used as it arrives (the feed pushes it at once on a change of the
  exchange's status). A pause is the feed down, unheard for 12 s, or silent: connected and live
  but no message on the whole socket for more than GAP_S. Only the part inside a session is
  recorded; one stock's quiet (a CAS key waiting for its auction) is never a pause.
- At start (A7.f, o): a "service" pause from market.db's last ``market_seen`` to now; deliveries
  still pending more than 5 minutes after their event are marked not sent, never sent late.
- The clock (A7.g, j): ``Book.on_clock`` every second; ``now`` is the wall clock when a value is
  judged.
- One writer (A7.k, l, n): every market.db write goes through one thread, in the order the
  decisions were made: a fire's record before any flush that carries its state; the flush every
  second and on a clean stop. A fire whose record raised NotFound (the alert was deleted) is
  never told; one whose write failed is looked up by its claim before it is told, so no fire is
  told twice.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import logging
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

import httpx
from cryptography.fernet import Fernet

from sudarshan.alerts.config import Settings
from sudarshan.alerts.market import engine, sessions, spec, words
from sudarshan.alerts.market.book import Book
from sudarshan.alerts.market.clock import ARRIVAL_GRACE_MS, GAP_S, HEALTH_MAX_AGE_MS, Feed, Market
from sudarshan.alerts.market.clock import Pause as Paused
from sudarshan.alerts.market.config import MarketSettings
from sudarshan.alerts.market.days import MASTER_AT, Days, MasterUnavailable, cas_keys, fno_stocks
from sudarshan.alerts.market.feedlink import (
    HEALTH,
    FeedLink,
    FeedRefused,
    FeedUnreachable,
    Health,
    parse_health,
)
from sudarshan.alerts.market.lane import Line, MarketLane
from sudarshan.alerts.market.push import (
    ALERT_EVENT,
    DELIVERY_EVENT,
    NONE_OPEN,
    NOT_ANSWERED,
    STATE_EVENT,
    Pusher,
    shown,
)
from sudarshan.alerts.market.store import DB_FILENAME, MarketStore, NotFound, StoreError
from sudarshan.alerts.outbox import OpenAlgoTelegram, Pacer, Throttle
from sudarshan.alerts.owner import Owner, OwnerError
from sudarshan.autologin.schedule import IST, TradingCalendar
from sudarshan.tasks import cancel_and_wait

log = logging.getLogger("sudarshan.alerts.market")

OWNER = "owner"
TICK_S = 1.0
FLUSH_MS = 1_000
SEEN_EVERY_MS = 10_000
CLAIM_RENEW_S = 600.0
CLAIM_RETRY_S = (2.0, 5.0, 10.0, 30.0, 60.0)
#: A fire whose record failed is looked up, or recorded again, after each of these waits.
RECORD_RETRY_S = (0.5, 1.0, 2.0)
#: A delivery still pending this long after its event when the service starts is not sent.
LATE_MS = 5 * 60_000
STOP_FLUSH_S = 5.0
PUSH_FLUSH_S = 1.0
RESTARTED = "The alert service restarted before it was sent; it is in the alert history"


class MarketRuntime:
    """Market alerts in the alert service's loop. Dependencies come in so tests drive it."""

    def __init__(
        self,
        alerts: Settings,
        market: MarketSettings,
        *,
        owner_reader: Callable[[], Owner],
        pacer: Pacer,
        openalgo_db: Path | None = None,
        clock: Callable[[], datetime] | None = None,
        push_fernet: Fernet | None = None,
    ):
        self.settings, self.config = alerts, market
        #: The key that seals pushes to OpenAlgo's owner room (M10.7); None: no app channel.
        self.push_fernet = push_fernet
        self.read_owner, self.pacer, self.openalgo_db = owner_reader, pacer, openalgo_db
        #: A replay (tests and the evening check only) runs on its ticks' own clock.
        self._replayed = TickClock() if market.replay and clock is None else None
        self.clock = clock or self._replayed or (lambda: datetime.now(IST))
        calendar = TradingCalendar(alerts.holidays_file, alerts.extra_days_file, logger=log)
        self.days = Days(calendar)
        self.book = Book()
        self.store: MarketStore | None = None
        self.link: FeedLink | None = None
        self.lane: MarketLane | None = None
        self.client: httpx.AsyncClient | None = None
        self.owner: Owner | None = None
        #: Called with each recorded fire for the app's owner room (M10.7); None until then.
        self.push: Callable | None = None
        self.pusher: Pusher | None = None
        #: The owner's desk listens here (desk.py): an alert that stopped or paused itself, with
        #: its reason, and every second, for the page's line (both pushed to the open apps).
        self.on_change: Callable[[int, str], None] | None = None
        self.on_second: Callable[[int], None] | None = None
        self.state, self.detail = "starting", ""
        self.stats = {"frames": 0, "judged": 0, "fires": 0}
        self._stop = asyncio.Event()
        self._writer = ThreadPoolExecutor(1, thread_name_prefix="market-db")
        self._alerts: dict[int, spec.Alert] = {}
        self._channels: dict[int, dict | None] = {}
        self._prefs = {"telegram": True, "app": True}
        #: Alert id -> its latest note (a candle not checked, a base changed) and when it came.
        self._notes: dict[int, tuple[engine.Reason, int]] = {}
        self._last: dict[str, dict] = {}
        self._to_seed: set[int] = set()
        self._tasks: set[asyncio.Task] = set()
        self._today: date | None = None
        self._sessions: dict = {}
        self._previous = 0
        self._fno: frozenset[str] = frozenset()
        self._master_on: date | None = None
        self._health: Health | None = None
        self._feed_started: str | None = None
        self._pauses: list[Paused] = []
        self._down_since: int | None = None
        self._open: tuple[int, int, str] | None = None  # (row id, start, cause)
        self._told_open = False
        self._line_ids = 0
        self._checked_at = 0.0
        self._started: int | None = None
        self._flushed = self._seen = 0
        self._pruned_on: date | None = None
        self._claims_wake = asyncio.Event()
        #: Held while the claims are put at the feed, and by an owner's change from its claim to
        #: its place in the Book, so a renewal never drops a key being added (desk.py).
        self._claims_lock = asyncio.Lock()
        #: (event id, channel) -> a future its answer resolves (the Settings sheet's test).
        self._waiters: dict[tuple[int, str], asyncio.Future] = {}
        self._throttle = Throttle(300.0)
        self.market = Market(0, {})

    # ---- lifecycle -------------------------------------------------------------------------
    async def run(self) -> None:
        """Until stop(); never raises: a failure turns market alerts off, order alerts go on."""
        try:
            if await self._open_store():
                await self._serve()
        except Exception:
            log.exception("market alerts stopped unexpectedly; order alerts carry on")
            self._set_state("off", words.STOPPED_UNEXPECTEDLY)
        finally:
            if self.store is not None:
                with contextlib.suppress(Exception):
                    await self._db(self.store.close)
            await asyncio.to_thread(self._writer.shutdown, True)

    def stop(self, reason: str = "stop requested") -> None:
        if not self._stop.is_set():
            log.info("market alerts stopping: %s", reason)
            self._stop.set()

    async def _open_store(self) -> bool:
        path = self.settings.state_dir / DB_FILENAME
        try:
            self.store = await self._db(MarketStore, path, (OWNER,), self.config.keep_days)
        except StoreError as exc:  # its words for the owner; the cause only in the journal
            log.error("%s (%s)", exc, exc.cause)
            self._set_state("off", str(exc))
            return False
        except Exception as exc:  # SchemaTooNew: its own words
            log.error("%s", exc)
            self._set_state("off", str(exc))
            return False
        return True

    async def _serve(self) -> None:
        now = self._started = self._now()
        if removed := await self._db(self.store.prune, now):
            log.info("forgot %d market alert records older than %d days", removed,
                     self.store.keep_days)  # fmt: skip
        rows, states = (
            await self._db(self.store.alerts, OWNER),
            await self._db(self.store.states, OWNER),
        )
        self._prefs = await self._db(self.store.prefs, OWNER)
        for row in rows:
            self._load(row, states.get(row["id"]))
        self._roll(now, force=True)
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
            self.client = client
            telegram = OpenAlgoTelegram(
                self.settings.openalgo_url, client, self._owner, self._reload_owner
            )
            self.lane = MarketLane(telegram.send_with_reason, self.clock, self.pacer,
                                   self._delivered)  # fmt: skip
            sender = asyncio.create_task(self._guard("telegram", self.lane.run()),
                                         name="market alerts: Telegram")  # fmt: skip
            pushing = None
            if self.push_fernet is not None:  # M10.7: the app's owner room
                self.pusher = Pusher(self.settings.openalgo_url, client, self.push_fernet,
                                     self._shown)  # fmt: skip
                self.push = self._push_fire
                pushing = asyncio.create_task(self._guard("push", self.pusher.run()),
                                              name="market alerts: app push")  # fmt: skip
            jobs: list[asyncio.Task] = []
            try:
                await self._resend_pending(now)
                await self._service_pause(now)
                self.link = FeedLink(self.config.feed_zmq, self.config.feed_url, client)
                self._set_state("running", f"{len(self.book.alerts())} alerts checking")
                for name, job in (("ticks", self._frames()), ("clock", self._ticks()),
                                  ("claims", self._claims())):  # fmt: skip
                    jobs.append(asyncio.create_task(self._guard(name, job),
                                                    name=f"market alerts: {name}"))  # fmt: skip
                await self._stop.wait()
            finally:
                # A cancel can be lost inside httpx (sudarshan/tasks.py): sent again until each
                # task has ended, or the stop would wait for ever.
                try:
                    await cancel_and_wait(*jobs)
                    await self._shutdown()  # Telegram's queue goes while its sender still runs
                finally:
                    await cancel_and_wait(sender, pushing)

    async def _guard(self, name: str, job) -> None:
        """A job that fails stops market alerts in words; order alerts carry on."""
        try:
            await job
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("market alerts' %s job failed; market alerts stop", name)
            self._set_state("off", words.STOPPED_UNEXPECTEDLY)
            self.stop(f"the {name} job failed")

    async def _shutdown(self) -> None:
        """A clean stop: the Book flushed, the pause closed, Telegram's queue sent (A7.n)."""
        now = self._now()
        with contextlib.suppress(StoreError):
            await self._flush(now, wait=True)
            self._close_pause(now)
            await self._db(self.store.set_seen, OWNER, now)
        try:
            pending = [task for task in self._tasks if not task.done()]
            if pending:
                await asyncio.wait(pending, timeout=STOP_FLUSH_S)
            if self.lane is not None and (left := await self.lane.flush(STOP_FLUSH_S)):
                log.warning("%d market alerts were not sent to Telegram before stopping", left)
            if self.pusher is not None and (left := await self.pusher.flush(PUSH_FLUSH_S)):
                log.info("%d market alert pushes to the app were left at the stop", left)
        finally:
            if self.link is not None:
                self.link.close()
        if self.state != "off":
            self._set_state("stopped")

    # ---- alerts ----------------------------------------------------------------------------
    def _load(self, row: dict, state: engine.State | None) -> None:
        try:
            alert = spec.Alert.of(row)
        except Exception:  # a row this release cannot read: skipped, never the service
            log.exception("market alert %s could not be read; it is not checked", row.get("id"))
            return
        self._alerts[alert.id], self._channels[alert.id] = alert, row.get("channels")
        self.book.put(alert, state)
        if alert.status == "active":
            self._to_seed.add(alert.id)

    # ---- the owner's changes, from the local API (desk.py, review A7.m) --------------------------
    def owner_put(self, row: dict, *, reset: bool = False, resume: bool = False) -> spec.Alert:
        """An owner's change the store has accepted, into the Book: its state starts again when
        the condition changed (``reset``), resumes on a resume or restart, and is kept otherwise.
        A new, reset or resumed Active alert takes its side from the latest snapshot, through the
        claims job, which follows the change at once."""
        alert = spec.Alert.of(row)
        fresh = alert.id not in self._alerts
        state = None
        if reset:
            state = engine.State()
        elif resume:
            state = engine.resume(self.book.state(alert.id) or engine.State())
        self._alerts[alert.id], self._channels[alert.id] = alert, row.get("channels")
        self._notes.pop(alert.id, None)
        self.book.put(alert, state)
        if alert.status == "active" and (fresh or reset or resume):
            self._to_seed.add(alert.id)
        self._claims_wake.set()
        self._rebuild()
        return alert

    def owner_drop(self, alert_id: int) -> None:
        """A deleted alert leaves the Book at once, and its key the claims."""
        self.book.drop(alert_id)
        for kept in (self._alerts, self._channels, self._notes):
            kept.pop(alert_id, None)
        self._to_seed.discard(alert_id)
        self._claims_wake.set()
        self._rebuild()

    def wake_claims(self) -> None:
        """Put the claims again now: a claim made for a change that was then refused goes."""
        self._claims_wake.set()

    @contextlib.asynccontextmanager
    async def claims_held(self):
        """No renewal puts the claims while an owner's change claims its key and takes its place
        in the Book, so the new key is never dropped and claimed again a moment later."""
        async with self._claims_lock:
            yield

    def use_prefs(self, prefs: dict[str, bool]) -> None:
        self._prefs = {"telegram": bool(prefs["telegram"]), "app": bool(prefs["app"])}

    def tell(self, event_id: int, text: str, at: int) -> asyncio.Future:
        """One line on Telegram through the market lane (the Settings sheet's test); the future
        is resolved with the lane's answer, once it is recorded."""
        waiter = self._waiter(event_id, "telegram")
        self.lane.add([Line(event_id, text, _at(at))])
        return waiter

    def push_test(self, event_id: int, at: int) -> asyncio.Future:
        """The test in the open apps (M10.7); the future is resolved with the push's answer."""
        waiter = self._waiter(event_id, "app")
        self.pusher.add(ALERT_EVENT, {
            "event_id": event_id, "alert_id": 0, "symbol": "", "exchange": "",
            "title": words.TEST_TITLE, "detail": words.TEST_DETAIL, "note": "",
            "at": _at(at).isoformat(timespec="seconds"), "at_ms": at, "found_later": False,
            "opening": False, "auction": False, "test": True,
        }, event_id)  # fmt: skip
        return waiter

    def push_state(self, data: dict) -> None:
        """A change the open apps apply to their Alerts page (the desk builds it)."""
        if self.pusher is not None:
            self.pusher.add(STATE_EVENT, data)

    def forget_waiter(self, event_id: int) -> None:
        for channel in ("telegram", "app"):
            self._waiters.pop((event_id, channel), None)

    def _waiter(self, event_id: int, channel: str) -> asyncio.Future:
        waiter = asyncio.get_running_loop().create_future()
        self._waiters[(event_id, channel)] = waiter
        return waiter

    def _resolve(self, event_id: int, channel: str, status: str) -> None:
        waiter = self._waiters.pop((event_id, channel), None)
        if waiter is not None and not waiter.done():
            waiter.set_result(status)  # after its write: the writer runs in order

    # ---- what the owner's desk reads -----------------------------------------------------------
    @property
    def prefs(self) -> dict[str, bool]:
        return dict(self._prefs)

    def alert(self, alert_id: int) -> spec.Alert | None:
        """The alert as the runtime holds it now (its status follows the engine's own stops)."""
        return self._alerts.get(alert_id)

    @property
    def down_since(self) -> int | None:
        """Since when market alerts are not checking (the feed down, unheard or silent)."""
        return self._down_since

    def last_frame(self, key: str) -> dict | None:
        return self._last.get(key)

    def note_of(self, alert_id: int) -> tuple[engine.Reason, int] | None:
        """The alert's latest note (a candle not checked, its base changed) and when it came."""
        return self._notes.get(alert_id)

    def now_ms(self) -> int:
        return self._now()

    async def write(self, fn, *args, **kwargs):
        """One market.db call on the writer thread, in order with the fires (review A7.k)."""
        return await self._db(fn, *args, **kwargs)

    def _channels_of(self, alert_id: int) -> dict[str, bool]:
        """Where a fire goes: the alert's own choice, else the settings (M10.14)."""
        own = self._channels.get(alert_id) or {}
        telegram = own.get("telegram", self._prefs["telegram"])
        channels = {"telegram": bool(telegram)}
        if self.push is not None:  # the app's room (M10.7)
            channels["app"] = bool(own.get("app", self._prefs["app"]))
        return channels

    # ---- the feed --------------------------------------------------------------------------
    async def _frames(self) -> None:
        while True:
            for topic, payload in await self.link.receive():
                now = self._now()
                if topic == HEALTH:
                    if (health := parse_health(payload, now)) is not None:
                        self._on_health(health, now)
                    continue
                frame = _frame(payload)
                if self._replayed is not None and frame is not None:
                    now = self._replay_step(frame)
                if frame is None:
                    self._throttle(
                        logging.WARNING, "junk", "The price feed sent a frame that is not JSON"
                    )
                    continue
                self.stats["frames"] += 1
                self._last[topic] = frame
                try:
                    result = self.book.on_tick(frame, now, self.market)
                    if result.check == "ok":
                        self.stats["judged"] += 1
                    if result.outcomes:
                        self._handle(result.outcomes, now)
                except Exception:  # one bad frame costs that frame, never the stream
                    self._throttle(logging.ERROR, "tick", "a price frame could not be judged")
                    log.debug("judging a frame failed", exc_info=True)
            await asyncio.sleep(0)

    def _on_health(self, health: Health, now: int) -> None:
        """Used as it arrives: the exchange's status and the feed's state (A7.d)."""
        restarted = (
            self._health is not None
            and health.started_at is not None
            and health.started_at != self._feed_started
        )
        self._health, self._feed_started = health, health.started_at or self._feed_started
        if restarted:  # its claims are on disk, but claim again at once (design 2.3)
            log.info("the price feed restarted; claiming market alerts' instruments again")
            self._claims_wake.set()
        self._watch(now)
        self._rebuild()

    def _feed_up(self, now: int) -> tuple[bool, int | None]:
        """(up, the moment it went down if it is down)."""
        health = self._health
        if health is None:  # nothing heard yet: a pause only once health is overdue
            started = self._started
            return (started is not None and now - started <= HEALTH_MAX_AGE_MS), started
        heard = now - health.heard_at <= HEALTH_MAX_AGE_MS
        state = health.state == "connected" or (self.config.replay and health.state == "replay")
        if not heard:
            return False, health.heard_at
        if not (state and self._live(health)):
            return False, None
        age = health.message_age_s
        if age is not None and age > GAP_S:  # silence on the whole socket (A7.e)
            return False, health.heard_at - int(age * 1000)
        return True, None

    def _watch(self, now: int) -> None:
        """Open, close and tell pauses, as far as they fall inside today's sessions."""
        up, since = self._feed_up(now)
        if not up:
            if self._down_since is None:
                self._down_since = since if since is not None else now
        else:
            self._down_since = None
        inside = self._inside(now)
        if self._down_since is not None and inside and self._open is None:
            start = max(self._down_since, min(s.start for s in self._sessions.values()))
            self._open_pause(start, "feed")
        elif self._open is not None and (self._down_since is None or not inside):
            ends = [s.end for s in self._sessions.values()]
            self._close_pause(now if inside or not ends else min(now, max(ends)))
        if self._open is not None and not self._told_open:
            _, start, cause = self._open
            if words.tells_pause(start, now, len(self.book.alerts())):
                self._told_open = True
                self._tell_line(words.pause_started(start, cause), now)

    def _replay_step(self, frame: dict) -> int:
        """A replay's time moves with its ticks, and its health is as fresh as they are."""
        self._replayed.see(frame.get("ts_recv"))
        now = self._now()
        self._roll(now)
        health = self._health
        if health is not None and health.state == "replay" and health.heard_at != now:
            self._health = dataclasses.replace(health, heard_at=now)
            self._rebuild()
        return now

    def _live(self, health: Health) -> bool:
        """Subscriptions live; a replay's health does not say so, and its ticks are all there is."""
        return health.live or (self.config.replay and health.state == "replay")

    def _inside(self, now: int) -> bool:
        return any(s.start <= now < s.end + ARRIVAL_GRACE_MS for s in self._sessions.values())

    # ---- what the engine decided -------------------------------------------------------------
    def _handle(self, outcomes, now: int) -> None:
        for out in outcomes:
            alert = self._alerts.get(out.alert_id)
            if alert is None:
                continue
            if out.note is not None:
                self._notes[alert.id] = (out.note, now)
            if out.fire is not None:
                self._fire(alert, out, now)
            elif out.status is not None:
                reason = words.status_reason(out.reason)
                self._submit(self.store.set_status, OWNER, alert.id, out.status, reason, now,
                             version=alert.version, was=alert.status)  # fmt: skip
            if out.status is not None:
                log.info("market alert %d %s: %s", alert.id, out.status, out.reason.code)
                self._alerts[alert.id] = dataclasses.replace(alert, status=out.status)
                self._claims_wake.set()
                self._rebuild()
                if self.on_change is not None:  # the open apps' cards (M10.7)
                    self.on_change(alert.id, words.status_reason(out.reason))

    def _fire(self, alert: spec.Alert, out: engine.Outcome, now: int) -> None:
        fire, today = out.fire, self._today
        title, detail = words.describe(alert, fire, today)
        line = words.line(alert, fire, today)
        reason = words.status_reason(out.reason) if out.status else ""
        channels = self._channels_of(alert.id)
        record = (self.store.record_fire, OWNER, fire, out.state)
        options = {"title": title, "detail": detail, "note": alert.note, "line": line,
                   "value": words.amount(alert, fire.value),
                   "level": words.amount(alert, fire.level), "channels": channels,
                   "status": out.status, "status_reason": reason}  # fmt: skip
        future = self._submit(*record, **options)  # in the order decided: before any flush
        self._spawn(self._tell(future, record, options, alert, fire, line, channels))

    async def _tell(self, future, record, options, alert, fire, line, channels) -> None:
        try:
            event_id = await future
        except NotFound:  # deleted between the decision and its record: never told
            log.info("market alert %d fired as it was deleted; it is not told", alert.id)
            return
        except StoreError as exc:
            log.error("a market alert's fire could not be recorded (%s); checking", exc.cause)
            event_id = await self._recover(record, options, fire)
        if event_id is None:  # told before (its claim is recorded)
            return
        self.stats["fires"] += 1
        log.info("market alert %d fired: %s", alert.id, line)
        if channels.get("telegram"):
            self.lane.add([Line(event_id, line, _at(fire.at))])
        if self.push is not None and channels.get("app"):
            self.push(event_id, alert, fire, options)

    async def _recover(self, record, options, fire) -> int | None:
        """After a failed write: the claim's record if it committed after all, else the fire
        recorded again; a negative id when it never could be (told on Telegram, never twice)."""
        for delay in RECORD_RETRY_S:
            await asyncio.sleep(delay)
            try:
                event = await self._db(self.store.event_by_claim, OWNER, fire.claim)
                if event is not None:
                    delivery = event["deliveries"].get("telegram", {})
                    return event["id"] if delivery.get("status") == "pending" else None
                return await self._db(*record, **options)
            except NotFound:
                return None
            except StoreError:
                continue
        log.error("a market alert's fire could not be recorded; it is sent without a record")
        return self._next_line_id()

    def _delivered(self, ids: list[int], status: str, reason: str) -> None:
        """The market lane's answer for each message's fires (M10.5)."""
        now = self._now()
        for event_id in ids:
            if event_id >= 0:
                self._submit(self.store.set_delivery, OWNER, event_id, "telegram", status, reason,
                             now)  # fmt: skip
            self._resolve(event_id, "telegram", status)
        self._push_settled([event_id for event_id in ids if event_id >= 0], "telegram")

    def _push_settled(self, ids: list[int], channel: str) -> None:
        """Deliveries that left pending, to the open apps' History, as market.db holds them: read
        on the writer after their writes, so in order (contract v3). Never retried."""
        if self.pusher is None or not ids:
            return
        read = self._submit(self.store.deliveries, OWNER, ids)
        read.add_done_callback(lambda done: self._settled(done, channel))

    def _settled(self, done: asyncio.Future, channel: str) -> None:
        if done.cancelled() or done.exception() is not None or self.pusher is None:
            return
        for event_id, found in done.result().items():
            delivery = found.get(channel)
            if delivery is not None and delivery["status"] != "pending":
                self.pusher.add(DELIVERY_EVENT, {"event_id": event_id, "channel": channel,
                                                 "delivery": delivery})  # fmt: skip

    def _push_fire(self, event_id: int, alert: spec.Alert, fire: engine.Fire, said: dict) -> None:
        """A recorded fire to the open apps (M10.7): the toast's words as the event keeps them
        (``said``, the record's options), and its marks."""
        self.pusher.add(ALERT_EVENT, {
            "event_id": event_id, "alert_id": alert.id, "symbol": alert.symbol,
            "exchange": alert.exchange, "title": said["title"], "detail": said["detail"],
            "note": said["note"],
            "at": _at(fire.at).isoformat(timespec="seconds"), "at_ms": fire.at,
            "found_later": fire.found_later is not None, "opening": fire.opening,
            "auction": fire.auction, "test": False,
        }, event_id if event_id >= 0 else None)  # fmt: skip

    def _shown(self, ids: list[int], screens: int | None) -> None:
        """The app delivery of each fire a push carried: the screens it was shown on (M10.7)."""
        now = self._now()
        if screens:
            status, reason = "sent", shown(screens)
        else:
            status, reason = "not_sent", NONE_OPEN if screens == 0 else NOT_ANSWERED
        for event_id in ids:
            self._submit(self.store.set_delivery, OWNER, event_id, "app", status, reason, now)
            self._resolve(event_id, "app", status)

    def _tell_line(self, text: str, at: int) -> None:
        """A line that is no fire (a pause): no record, so no delivery to write."""
        if self._prefs["telegram"]:
            self.lane.add([Line(self._next_line_id(), text, _at(at))])

    def _next_line_id(self) -> int:
        self._line_ids -= 1
        return self._line_ids

    # ---- the clock ---------------------------------------------------------------------------
    async def _ticks(self) -> None:
        while True:
            now = self._now()
            self._roll(now)
            self._watch(now)
            outcomes = self.book.on_clock(now, self.market)
            if outcomes:
                self._handle(outcomes, now)
            if self.on_second is not None:
                self.on_second(now)
            if now - self._flushed >= FLUSH_MS:
                await self._flush(now)
            if now - self._seen >= SEEN_EVERY_MS:
                self._seen = now
                self._submit(self.store.set_seen, OWNER, now)
            self._read_master(now)
            self._prune(now)
            await asyncio.sleep(TICK_S)

    def _roll(self, now: int, force: bool = False) -> None:
        """Today's sessions, asked again at midnight and every 30 s (A7.c)."""
        today = datetime.fromtimestamp(now / 1000, IST).date()
        if not force and today == self._today and time.monotonic() - self._checked_at < 30:
            return
        self._checked_at = time.monotonic()
        found, previous = self.days.of(today)
        if today != self._today:
            if self._today is not None:
                self._day_summary()
            self._today, self._pauses = today, []
        elif found == self._sessions and previous == self._previous:
            return
        else:
            log.info("the trading calendar changed; today's sessions were read again")
        self._sessions, self._previous = found, previous
        self._rebuild()

    def _rebuild(self) -> None:
        """The Market as the runtime knows it now: what every judgement reads."""
        health = self._health
        feed = Feed(health.state, self._live(health), health.heard_at) if health else Feed()
        pauses = tuple(self._pauses)
        if self._open is not None:
            pauses += (Paused(self._open[1], None, self._open[2]),)
        auction = (
            cas_keys(self.book.alerts(), self._today, self._fno) if self._today else frozenset()
        )
        if auction != self.market.auction:  # a CAS key is claimed in full mode: its volume (X6i)
            self._claims_wake.set()
        self.market = Market(
            sessions.day_key(self._today) if self._today else 0,
            self._sessions,
            self._previous,
            health.status if health else {},
            feed,
            pauses,
            self.config.replay,
            auction,
        )

    def closes_by_auction(self, alert: spec.Alert) -> bool:
        """Whether ``alert``'s stock closes by auction today (a CAS key), as the Market will say
        once it is in the Book: the desk claims such a key in full mode before it saves."""
        return bool(self._today) and alert.key in cas_keys([alert], self._today, self._fno)

    def _read_master(self, now: int) -> None:
        """The CAS keys' source, read at start and each day at 09:00 (X6a)."""
        if self.openalgo_db is None or self._master_on == self._today:
            return
        moment = datetime.fromtimestamp(now / 1000, IST)
        if self._master_on is not None and moment.hour * 60 + moment.minute < MASTER_AT:
            return
        self._master_on = self._today
        self._spawn(self._load_master())

    async def _load_master(self) -> None:
        try:
            self._fno = await asyncio.to_thread(fno_stocks, self.openalgo_db)
        except MasterUnavailable:
            log.warning("OpenAlgo's master contract could not be read; stocks keep yesterday's "
                        "closing-auction list")  # fmt: skip
            return
        log.info("%d companies with stock futures close by auction today", len(self._fno))
        self._rebuild()

    async def _flush(self, now: int, wait: bool = False) -> None:
        self._flushed = now
        changed = self.book.changed()
        if not changed:
            return
        future = self._submit(self.store.save_states, OWNER, changed, now)
        if wait:
            await future
        else:
            future.add_done_callback(lambda done: self._unsaved(done, changed))

    def _unsaved(self, done, changed) -> None:
        if not done.cancelled() and done.exception() is not None:
            self._throttle(logging.ERROR, "flush", "market alerts' state could not be saved: %s",
                           getattr(done.exception(), "cause", ""))  # fmt: skip
            self.book.restore(changed)

    def _prune(self, now: int) -> None:
        """Once a day, after the last session."""
        if self._pruned_on == self._today or not self._sessions:
            return
        if now >= max(s.end for s in self._sessions.values()) + 5 * 60_000:
            self._pruned_on = self._today
            self._submit(self.store.prune, now)

    # ---- claims ------------------------------------------------------------------------------
    async def _claims(self) -> None:
        """This service's keys at the feed: at start, on a change, after the feed restarts, and
        renewed every 10 minutes; the ZMQ topics follow at once."""
        failures = 0
        while True:
            self._claims_wake.clear()
            try:
                async with self._claims_lock:
                    keys = self.book.modes(full=self.market.auction)  # a CAS key's volume (X6i)
                    self.link.follow(keys)
                    self._last = {key: frame for key, frame in self._last.items() if key in keys}
                    answer = await self.link.put_claims(keys)
            except FeedRefused as exc:
                log.error("The price feed refused market alerts' instruments: %s", exc)
            except FeedUnreachable:
                delay = CLAIM_RETRY_S[min(failures, len(CLAIM_RETRY_S) - 1)]
                failures += 1
                self._throttle(logging.WARNING, "feed", "The price feed is not answering; "
                               "market alerts ask again for their instruments")  # fmt: skip
                await self._sleep(delay)
                continue
            else:
                failures = 0
                log.debug(
                    "claimed %d instruments (live: %s)", len(keys), answer.get("applied_live")
                )
            await self._seed()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._claims_wake.wait(), CLAIM_RENEW_S)
            await asyncio.sleep(0.2)  # a burst of changes is one claim

    async def _seed(self) -> None:
        """New and resumed alerts take their side from the latest snapshot (design 2.3)."""
        if not self._to_seed:
            return
        wanted = {self._alerts[i].key for i in self._to_seed if i in self._alerts}
        missing = sorted(key for key in wanted if key not in self._last)
        frames = dict(self._last)
        if missing:
            with contextlib.suppress(FeedUnreachable, FeedRefused):
                frames |= await self.link.snapshot(missing)
        for alert_id in sorted(self._to_seed):
            alert = self._alerts.get(alert_id)
            frame = frames.get(alert.key) if alert else None
            if frame is not None:
                self.book.seed(alert_id, frame, self.market)
        self._to_seed.clear()

    # ---- pauses ------------------------------------------------------------------------------
    async def _service_pause(self, now: int) -> None:
        """From the last moment market alerts were known checking to now (A7.f)."""
        seen = await self._db(self.store.seen, OWNER)
        if seen is None or now - seen <= GAP_S * 1000 or not self._sessions:
            return
        start = max(seen, min(s.start for s in self._sessions.values()))
        end = min(now, max(s.end for s in self._sessions.values()))
        if end - start <= GAP_S * 1000:
            return
        pause_id = await self._db(self.store.open_pause, OWNER, start, "service")
        await self._db(self.store.close_pause, OWNER, pause_id, end)
        self._pauses.append(Paused(start, end, "service"))
        self._rebuild()
        if words.tells_pause(start, end, len(self.book.alerts())):
            self._tell_line(words.pause_ended(start, end, "service"), now)

    def _open_pause(self, start: int, cause: str) -> None:
        """Recorded at once on the writer thread; its row id is that call's result."""
        opened = self._writer.submit(self.store.open_pause, OWNER, start, cause)
        self._open, self._told_open = (opened, start, cause), False
        log.warning("market alerts paused since %s: %s", _hms(start), _CAUSES.get(cause, cause))
        self._rebuild()

    def _close_pause(self, end: int) -> asyncio.Future | None:
        if self._open is None:
            return None
        opened, start, cause = self._open
        self._open = None

        def close() -> None:  # on the writer thread, after the open (one thread, in order)
            if opened.exception() is None:
                self.store.close_pause(OWNER, opened.result(), end)

        closed = self._submit(close)
        self._pauses.append(Paused(start, end, cause))
        log.info("market alerts checking again: paused %s to %s", _hms(start), _hms(end))
        if self._told_open:
            self._tell_line(words.pause_ended(start, end, cause), end)
        self._rebuild()
        return closed

    async def _resend_pending(self, now: int) -> None:
        """Telegram lines a stop left pending: sent if fresh, else not sent (A7.o). A push to
        the app is never sent late: the app reads what it missed when it reconnects."""
        for event in await self._db(self.store.pending, OWNER, "telegram"):
            if now - event["fired_at"] > LATE_MS:
                self._submit(self.store.set_delivery, OWNER, event["id"], "telegram",
                             "not_sent", RESTARTED, now)  # fmt: skip
            else:
                self.lane.add([Line(event["id"], event["line"], _at(event["fired_at"]))])
        for event in await self._db(self.store.pending, OWNER, "app"):
            self._submit(self.store.set_delivery, OWNER, event["id"], "app", "not_sent",
                         RESTARTED, now)  # fmt: skip

    # ---- small pieces ------------------------------------------------------------------------
    def _owner(self) -> Owner | None:
        if self.owner is None:
            self._reload_owner()
        return self.owner

    def _reload_owner(self) -> Owner | None:
        try:
            self.owner = self.read_owner()
        except OwnerError as exc:
            self._throttle(logging.WARNING, "owner", "market alerts cannot reach Telegram yet: %s",
                           exc.kind)  # fmt: skip
        except Exception:
            log.debug("reading the API key failed", exc_info=True)
        return self.owner

    def _submit(self, fn, *args, **kwargs) -> asyncio.Future:
        """Run one market.db call on the writer thread, after every call submitted before it."""
        return asyncio.wrap_future(self._writer.submit(fn, *args, **kwargs))

    async def _db(self, fn, *args, **kwargs):
        return await self._submit(fn, *args, **kwargs)

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _sleep(self, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._stop.wait(), max(0.0, seconds))

    def _now(self) -> int:
        return int(self.clock().timestamp() * 1000)

    def _day_summary(self) -> None:
        sent = self.lane.stats if self.lane else {}
        log.info(
            "market alerts' day: %d frames, %d fires, %d messages, %d not sent",
            self.stats["frames"], self.stats["fires"], sent.get("messages", 0),
            sent.get("failed", 0) + sent.get("dropped", 0),
        )  # fmt: skip
        self.stats = dict.fromkeys(self.stats, 0)

    def _set_state(self, state: str, detail: str = "") -> None:
        if (state, detail) != (self.state, self.detail):
            log.info("market alerts %s%s", state, f" ({detail})" if detail else "")
            self.state, self.detail = state, detail


class ReleasedClaims:
    """Market alerts are off: the feed may still hold the keys an earlier run claimed (design 8),
    so they are dropped, asked again with growing waits until the feed answers."""

    WAITS_S = (0.0, 5.0, 30.0, 120.0, 600.0)

    def __init__(self, feed_url: str):
        self.feed_url = feed_url.rstrip("/")
        self._stop = asyncio.Event()

    def stop(self, reason: str = "") -> None:
        self._stop.set()

    async def run(self) -> None:
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False) as client:
            link = f"{self.feed_url}/claims/sudarshan-alerts"
            for wait in self.WAITS_S:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), wait)
                if self._stop.is_set():
                    return
                try:
                    answer = await client.delete(link, timeout=5.0)
                except httpx.HTTPError:
                    continue
                if answer.status_code == 200:
                    log.info("market alerts are off; the price feed no longer keeps their keys")
                    return


class TickClock:
    """A replay's clock: the newest frame's arrival time as it was recorded, so a recorded day
    is judged as it happened (the gate's freshness, its sessions, its candles)."""

    def __init__(self) -> None:
        self.ms: int | None = None

    def see(self, ts_recv) -> None:
        if isinstance(ts_recv, int) and (self.ms is None or ts_recv > self.ms):
            self.ms = ts_recv

    def __call__(self) -> datetime:
        if self.ms is None:
            return datetime.now(IST)
        return datetime.fromtimestamp(self.ms / 1000, IST)


#: A pause's cause, as the journal says it.
_CAUSES = {"feed": "the price feed is reconnecting or silent", "service": "not running"}


def _frame(payload: bytes) -> dict | None:
    try:
        frame = json.loads(payload)
    except ValueError:
        return None
    return frame if isinstance(frame, dict) else None


def _at(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, IST)


def _hms(ms: int) -> str:
    return _at(ms).strftime("%H:%M:%S")
