"""The sudarshan-signals process (T5, design 3.5): one asyncio loop, the letterbox, signals.db and
the sealed local API on 127.0.0.1:5588. A ring service of its own: nothing runs inside OpenAlgo.

* **Switched off** (``SUDARSHAN_SIGNALS`` not on, the default in code): nothing is read or opened
  and every op answers ``Off`` with ``off``; ``/health`` says ``signals: off``.
* **Not set up** (on, but no publisher in ``/etc/sudarshan/signals.env``): the store is open,
  nothing is read, every op answers ``not_set_up``; the file is looked at again every 30 s, so a
  set-up run later needs no restart. A letterbox that is missing or not as the set-up leaves it
  answers ``letterbox_wrong`` the same way.
* **On**: once a second, one cycle on the store's thread: scan the letterbox, judge each file
  (``contract.read_document``), drop a repeat or an older one, work out the changes
  (``diff.apply``), store the document in one transaction, then remove the file; and record the
  lapses the clock has brought. Every blocking call (a file, SQLite) runs on that one thread; the
  loop does timers and the listener only. Tasks stop with ``sudarshan.tasks.cancel_and_wait``.
* **The hand-on seam.** Each cycle's new timeline rows and alerts go to every hook in
  ``HAND_ON`` (``@hand_on("name")``); the relay to the alerts' desk (``relay.py``, AL2) attaches
  there with one marked line, its retry loop with ``@job``. A hook that fails is logged and the
  service carries on.
* **Port taken**: an ERROR in plain words, the reading carries on, and the port is tried again
  every minute; never another port. Exit status 2 is for a setting it cannot use.
* **Logs** carry states and counts only: never TRINETRA's text, a key or a token.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sudarshan.autologin.schedule import TradingCalendar
from sudarshan.signals import contract as c
from sudarshan.signals import diff, inbox, notices
from sudarshan.signals.api import SignalsApi
from sudarshan.signals.config import (
    ConfigError,
    Settings,
    merged_env,
    read_openalgo_env,
    read_pepper,
    setup_logging,
)
from sudarshan.signals.migrations import SchemaTooNew
from sudarshan.signals.store import DB_FILENAME, SignalsStore, StoreError
from sudarshan.tasks import cancel_and_wait
from sudarshan.vault import SIGNALS_API_INFO, SIGNALS_JOURNAL_INFO, fernet_for

log = logging.getLogger("sudarshan.signals")

PORT_RETRY_S = 60.0
SCAN_S = c.INBOX["scan_ms"] / 1000
SETUP_EVERY_S = 30.0
PRUNE_EVERY_S = 3600.0
#: How often the same warning (strangers' files, files beyond the ceiling) may be logged.
WARN_EVERY_S = 3600.0
PURPOSE_KEYS = {"api": SIGNALS_API_INFO, "journal": SIGNALS_JOURNAL_INFO}


@dataclass
class Batch:
    """What one cycle stored and a hook may hand on: new timeline rows (the contract's
    ``Change`` with ``relay``), new alerts (``AlertRow`` with ``relay``), the owner's settings."""

    changes: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[dict[str, Any]] = field(default_factory=list)
    settings: dict[str, Any] = field(default_factory=dict)


HandOn = Callable[["Service", Batch], Awaitable[None]]
Job = Callable[["Service"], Awaitable[None]]
#: The hand-on seam: hooks that receive each stored batch (AL2's relay attaches here).
HAND_ON: dict[str, HandOn] = {}
#: Long-running coroutines started when the view is on (AL2's retry loop, T8's marks).
JOBS: dict[str, Job] = {}


def hand_on(name: str, registry: dict[str, HandOn] | None = None) -> Callable[[HandOn], HandOn]:
    """Register a hook that receives each stored batch of changes and alerts."""
    book = HAND_ON if registry is None else registry

    def register(function: HandOn) -> HandOn:
        if name in book:
            raise ValueError(f"the hook {name} is registered twice")
        book[name] = function
        return function

    return register


def job(name: str, registry: dict[str, Job] | None = None) -> Callable[[Job], Job]:
    """Register a long-running coroutine the service starts when the view is on."""
    book = JOBS if registry is None else registry

    def register(function: Job) -> Job:
        if name in book:
            raise ValueError(f"the job {name} is registered twice")
        book[name] = function
        return function

    return register


@dataclass
class Cycle:
    """What one cycle found and did."""

    state: str = "ok"
    batch: Batch = field(default_factory=Batch)
    altered: bool = False
    lapsed: bool = False
    waiting: int = 0
    accepted: int = 0
    refused: int = 0
    strangers: int = 0
    overflow: int = 0
    #: A counter of today's was written this cycle (any verdict but nothing at all).
    counted: bool = False


def open_store(settings: Settings) -> SignalsStore:
    settings.state_dir.mkdir(mode=0o750, parents=True, exist_ok=True)
    return SignalsStore(settings.state_dir / DB_FILENAME)


class Service:
    """The process's state, and the desk the API serves."""

    def __init__(
        self,
        settings: Settings,
        keys: dict[str, Any],
        *,
        opener: Callable[[Settings], SignalsStore] = open_store,
        hooks: dict[str, HandOn] | None = None,
        jobs: dict[str, Job] | None = None,
        letterbox: Callable[[int], inbox.Letterbox] | None = None,
        port_retry_s: float = PORT_RETRY_S,
        scan_s: float = SCAN_S,
        setup_every_s: float = SETUP_EVERY_S,
    ):
        from sudarshan.signals import ops  # the op handlers (T6)

        self.settings = settings
        self.enabled = settings.enabled
        self.api = SignalsApi(self, keys, registry=ops.REGISTRY)
        self.store: SignalsStore | None = None
        #: Why the view is off although the switch is on (the store would not open), in words.
        self.off_reason: str | None = None
        self.publisher: int | None = None
        self.letterbox: inbox.Letterbox | None = None
        self.letterbox_state = "unknown"
        self.current: dict[diff.Key, diff.Current] = {}
        self.last: dict[str, Any] | None = None
        self.prefs: dict[str, Any] = {}
        self.calendar = TradingCalendar(
            settings.holidays_file, settings.extra_days_file, logger=log
        )
        #: The clock every decision reads (epoch milliseconds); a test sets its own.
        self.clock: Callable[[], int] = lambda: int(time.time() * 1000)
        self._opener, self._hooks = opener, HAND_ON if hooks is None else hooks
        self._jobs = JOBS if jobs is None else jobs
        self._make_letterbox = letterbox or (lambda uid: inbox.Letterbox(settings.letterbox, uid))
        self._retry_s, self._scan_s, self._setup_s = port_retry_s, scan_s, setup_every_s
        self._state = "starting"
        self._tasks: dict[str, asyncio.Task] = {}
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._warned: dict[str, float] = {}
        self._setup_at = -1e18
        self._pruned_at = -1e18
        #: Today's counters (refused, gaps, ...), read on the store's thread when they may have
        #: moved, so /health never waits for SQLite.
        self.today: dict[str, int] = {}
        self._today_day = None
        #: When a document of a newer format than this release reads was last seen.
        self.newer_format_ms: int | None = None
        #: What the notices told (T9; signals.db meta "notices"), so a restart tells nothing twice.
        self.notice_memory: dict[str, Any] | None = None
        self.events_path = settings.state_dir / c.EVENTS_FILE

    # ---- what the ops and /health see ----------------------------------------------------------
    @property
    def switch_state(self) -> str:
        """``off``, ``not_set_up`` (no publisher, or a letterbox not as the set-up leaves it) or
        ``on``."""
        if not self.enabled:
            return "off"
        if self.publisher is None or self.letterbox_state in ("missing", "wrong"):
            return "not_set_up"
        return "on"

    def off_message(self) -> str | None:
        """The sentence every op answers instead of its own, or None when the view is on."""
        if not self.settings.enabled:
            return c.WORDS["off"]
        if self.off_reason is not None or self.store is None:
            return self.off_reason or c.WORDS["store_failed"]
        if self.publisher is None:
            return c.WORDS["not_set_up"]
        if self.letterbox_state in ("missing", "wrong"):
            return c.WORDS["letterbox_wrong"]
        return None

    def health(self) -> dict[str, Any]:
        """The contract's ``Health``: states and counts only, never TRINETRA's text."""
        from sudarshan.signals.health import document

        return document(self)

    @property
    def state(self) -> str:
        return self._state

    def _set_state(self, state: str) -> None:
        if state != self._state:
            log.info("state %s -> %s", self._state, state)
            self._state = state

    # ---- life cycle ----------------------------------------------------------------------------
    def stop(self, reason: str = "stop") -> None:
        log.info("stopping (%s)", reason)
        self._set_state("stopping")
        self._stop.set()
        self._wake.set()

    async def run(self) -> None:
        """Start, serve until ``stop``, and close everything that was opened, on every path."""
        api_task = None
        try:
            if self.enabled:
                await self._open()
            else:
                log.info("The TRINETRA view is switched off (SUDARSHAN_SIGNALS): nothing is read "
                         "and every request is answered as off")  # fmt: skip
            api_task = asyncio.create_task(self._serve_api(), name="signals-api")
            if self.store is not None:
                self._tasks["scan"] = asyncio.create_task(self._scanner(), name="signals-scan")
                for name, function in self._jobs.items():
                    self._tasks[name] = asyncio.create_task(function(self), name=f"job-{name}")
            await self._stop.wait()
        finally:
            self._set_state("stopping")
            await cancel_and_wait(api_task, *self._tasks.values())
            await self.api.close()
            store, self.store = self.store, None
            if store is not None:
                try:
                    store.close()
                except Exception:
                    log.exception("the TRINETRA view's store did not close cleanly")
            log.info("stopped")

    async def _open(self) -> None:
        """Open the store and load what the cycles keep in memory. A file a newer release wrote,
        or one that will not open, keeps the view off with the owner's sentence."""
        try:
            self.store = await asyncio.to_thread(self._opener, self.settings)
        except SchemaTooNew as exc:
            log.error("signals.db was written by a newer release (%s): the view stays off",
                      exc.cause)  # fmt: skip
            self.off_reason = str(exc)
            return
        except StoreError as exc:
            log.error("signals.db could not be opened (%s): the view stays off", exc.cause)
            self.off_reason = str(exc)
            return
        self.current = await self.store.call(self.store.current)
        self.last = await self.store.call(self.store.last)
        self.prefs = await self.store.call(self.store.settings)
        self.newer_format_ms = await self.store.call(self.store.meta_get, "newer_format_ms")
        self.notice_memory = await self.store.call(self.store.meta_get, "notices")

    async def _serve_api(self) -> None:
        """Bind the port; while it is taken say so and try again every minute; then hold it."""
        port = self.settings.http_port
        told = False
        while True:
            try:
                await self.api.start(port)
            except OSError as exc:
                if not told:
                    log.error(
                        "The TRINETRA reader cannot open its local API: port %d on this server "
                        "is taken (%s). The TRINETRA page cannot be used until it is free; the "
                        "letterbox is still read. Free the port: this service tries again every "
                        "minute.", port, exc.strerror or exc,
                    )  # fmt: skip
                    told = True
                self._set_state("port_taken")
                await asyncio.sleep(self._retry_s)
                continue
            if told:
                log.info("port %d is free now; the TRINETRA reader's local API is open", port)
            self._set_state("running" if self.enabled else "off")
            return

    # ---- the letterbox ------------------------------------------------------------------------
    async def _scanner(self) -> None:
        while not self._stop.is_set():
            now = self.clock()
            waiting = 0
            if self._refresh_setup(now) and self.letterbox is not None:
                try:
                    cycle = await self.store.call(self.cycle, now)
                except StoreError as exc:
                    log.error("A TRINETRA document could not be stored (%s); it is read again "
                              "at the next look", exc.cause)  # fmt: skip
                    cycle = None
                if cycle is not None:
                    waiting = cycle.waiting
                    await self._after(cycle)
            if now - self._pruned_at >= PRUNE_EVERY_S * 1000:
                self._pruned_at = now
                try:
                    await self.store.call(self.store.prune, now)
                except StoreError as exc:
                    log.warning("Old TRINETRA records could not be pruned (%s)", exc.cause)
            if waiting:
                await asyncio.sleep(0)
                continue
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), self._scan_s)
            except TimeoutError:
                pass

    def _refresh_setup(self, now: int) -> bool:
        """The publisher's number, looked at again every 30 s while it is missing (a set-up run
        later needs no restart); True when the letterbox may be read."""
        if self.publisher is None and now - self._setup_at >= self._setup_s * 1000:
            self._setup_at = now
            self.publisher = inbox.publisher_uid(self.settings.setup_file)
            if self.publisher is not None:
                self.letterbox = self._make_letterbox(self.publisher)
                log.info("The TRINETRA letterbox is set up; reading it from now on")
        if self.publisher is None:
            self.letterbox_state = "unknown"
            return False
        return True

    def cycle(self, now: int) -> Cycle:
        """One look at the letterbox, on the store's thread: every file judged and stored, each
        removed after its verdict is stored, then the clock's lapses."""
        store, box = self.store, self.letterbox
        scan = box.scan()
        out = Cycle(state=scan.state, waiting=scan.waiting, overflow=scan.overflow)
        self.letterbox_state = scan.state
        out.batch.settings = dict(self.prefs)
        if scan.strangers:
            store.count("strangers", now, scan.strangers)
            out.strangers = scan.strangers
            self._warn("strangers", "%d files from other accounts were removed from the TRINETRA "
                       "letterbox unread", scan.strangers)  # fmt: skip
        if scan.overflow:
            store.count("overflow", now, scan.overflow)
            self._warn("overflow", "More than %d TRINETRA documents were waiting; the oldest %d were "
                       "removed unread", c.INBOX["waiting"], scan.overflow)  # fmt: skip
        for name, reason in scan.refused:
            store.record(name, received_ms=now, size=0, verdict="refused", reason=reason,
                         counter="refused")  # fmt: skip
            out.refused += 1
            log.info("A TRINETRA file was refused before reading (%s)", reason)
        for found in scan.found:
            out.counted = True  # every file read leaves a verdict, and most a count
            try:
                self._judge(found, now, out)
            except StoreError:
                raise  # nothing removed: the file is read again at the next look
            except Exception:
                # a fault of ours on this one document must not hold the letterbox up behind it
                log.exception("A TRINETRA document could not be handled; it is set aside")
                store.record(found.name, received_ms=now, size=found.size, verdict="refused",
                             reason="error", written_ms=found.written_ms,
                             counter="refused")  # fmt: skip
                box.remove(found)
                out.refused += 1
        current, lapses = diff.lapses(self.current, now)
        if lapses:
            store.store_lapses(current, lapses, now)
            self.current = current
            out.lapsed = True
        day = diff.ist_day(now)
        if out.counted or out.strangers or out.overflow or out.refused or day != self._today_day:
            self.today, self._today_day = store.counts(now), day
        self._tell(now)
        return out

    def _tell(self, now: int) -> None:
        """T9: the notices due now, recorded for the alerts' notice desk (on the store's thread),
        only while the view is on and set up."""
        if self.switch_state != "on":
            return
        memory = notices.Memory.of(self.notice_memory)
        due, after = notices.due(
            memory,
            now_ms=now,
            last=self.last,
            newer_format_ms=self.newer_format_ms,
            is_trading_day=self.calendar.is_trading_day,
            in_market_hours=self.calendar.in_market_hours,
        )
        if due:
            told = notices.tell(self.events_path, due, now)
            for notice in told:
                log.info("TRINETRA notice recorded for Telegram: %s", notice.kind)
            if len(told) != len(due):
                return  # tried again at the next look; one already in the file is not doubled
        if after != memory:
            self.store.meta_set("notices", after.as_dict())
            self.notice_memory = after.as_dict()

    def _judge(self, found: inbox.Found, now: int, out: Cycle) -> None:
        store, box = self.store, self.letterbox
        got = c.read_document(found.raw, now_ms=now, written_ms=found.written_ms, name=found.name)
        if isinstance(got, c.Refusal):
            store.record(found.name, received_ms=now, size=found.size, verdict="refused",
                         reason=str(got), written_ms=found.written_ms,
                         counter="refused")  # fmt: skip
            box.remove(found)
            out.refused += 1
            log.info("A TRINETRA document was refused (%s)", got)
            return
        if isinstance(got, c.NewerFormat):
            store.record(found.name, received_ms=now, size=found.size, verdict="newer_format",
                         written_ms=found.written_ms, counter="newer_format")  # fmt: skip
            store.meta_set("newer_format_ms", now)
            self.newer_format_ms = now
            box.keep(found)
            self._warn("newer", "TRINETRA publishes a newer format (version %d) than this release "
                       "reads; the files are left for a later release", got.v)  # fmt: skip
            return
        verdict, gap = self._order(got)
        if verdict != "accepted":
            store.record(found.name, received_ms=now, size=found.size, verdict=verdict,
                         written_ms=found.written_ms, snap=got, counter=verdict)  # fmt: skip
            box.remove(found)
            return
        outcome = diff.apply(self.current, got, now)
        stored = store.accept(got, name=found.name, received_ms=now, current=outcome.current,
                              changes=outcome.changes, gap=gap,
                              relay_on=bool(self.prefs.get("relay", True)))  # fmt: skip
        self.current, self.last = outcome.current, store.last()
        box.remove(found)
        out.accepted += 1
        out.altered = out.altered or outcome.altered
        out.batch.changes += stored.changes
        out.batch.alerts += stored.alerts
        if gap:
            log.info("%d TRINETRA documents did not arrive (a gap in its numbering)", gap)

    def _order(self, snap: c.Snapshot) -> tuple[str, int]:
        """``accepted`` with the gap before it, or ``repeat`` or ``older`` (dropped). Within a
        run the number decides; a new run is accepted unless it is older than the last document
        by more than the clock's allowance."""
        last = self.last
        if last is None:
            return "accepted", 0
        if snap.run == last.get("run"):
            if snap.seq == last.get("seq"):
                return "repeat", 0
            if snap.seq < int(last.get("seq") or 0):
                return "older", 0
            return "accepted", max(0, snap.seq - int(last.get("seq") or 0) - 1)
        if snap.at < int(last.get("at") or 0) - c.CLOCK_AHEAD_MS:
            return "older", 0
        return "accepted", 0

    async def _after(self, cycle: Cycle) -> None:
        """What follows a cycle on the loop: the hand-on hooks, then the update (B2)."""
        batch = cycle.batch
        if batch.changes or batch.alerts:
            for name, hook in list(self._hooks.items()):
                try:
                    await hook(self, batch)
                except Exception:
                    log.exception("the TRINETRA hand-on hook %s failed; the service carries on",
                                  name)  # fmt: skip
        if cycle.altered or cycle.lapsed:
            self.updated("lapse" if cycle.lapsed and not cycle.altered else "document")

    def updated(self, cause: str) -> None:
        """The current readings or the settings changed (``contract.UPDATE_CAUSES``): the place
        the push to the owner's screens (B2) starts from."""

    def _warn(self, key: str, message: str, *args: Any) -> None:
        now = time.monotonic()
        if now - self._warned.get(key, -1e9) >= WARN_EVERY_S:
            self._warned[key] = now
            log.warning(message, *args)


async def _serve(service: Service) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, service.stop, f"signal {sig.name}")
    try:
        await service.run()
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)


def keys_for(pepper: str) -> dict[str, Any]:
    """The two served purposes' keys, derived from OpenAlgo's pepper (sudarshan/vault/keys.py)."""
    return {name: fernet_for(pepper, info) for name, info in PURPOSE_KEYS.items()}


def main() -> int:
    setup_logging()
    try:
        settings = Settings.from_env()
        openalgo = read_openalgo_env(settings.env_file) if settings.env_file.exists() else {}
        pepper = read_pepper(merged_env(None, openalgo))
    except ConfigError as exc:
        log.error("sudarshan-signals cannot run: %s", exc)
        return 2
    setup_logging({"SUDARSHAN_SIGNALS_LOG_LEVEL": settings.log_level})
    try:
        asyncio.run(_serve(Service(settings, keys_for(pepper))))
    except Exception:  # a bug: systemd restarts it
        log.exception("sudarshan-signals stopped unexpectedly")
        return 1
    return 0
