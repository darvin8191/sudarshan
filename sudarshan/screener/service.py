"""The sudarshan-screener process (M9.1, design 3.1 and 6): one asyncio loop, the store, the jobs
and the sealed local API on 127.0.0.1:5584.

* **Switched off** (``SUDARSHAN_SCREENER`` is not on): the store is not opened, no job runs, and
  the API answers every op with ``enabled: false`` (api.py). The service stays up so that the
  bridge learns the switch from the API and the page says so (cross-design R3). Its one off-time
  job drops the feed claims an earlier run left, so nothing it no longer watches stays on the
  shared socket; a plain stop keeps them for the restart, as market alerts do.
* **The store** is opened once at start and closed on stop. The loop never does store work: the
  store owns its one writer thread and ``close_store`` ends it (S13).
* **Jobs** register from their own modules with ``@job("name")`` (the run scheduler, the outbox,
  the feed link) and are started as tasks here; a registry of components, one line per row (S17).
* **Port taken**: an ERROR in plain words, the jobs keep running, and the port is tried again
  every minute. The service never exits for this and never tries another port (X2). Exit status 2
  is for a setting or key it cannot use, which a restart would not mend.
* **/health** is the ops document (the release, the version, the state) plus ``screener_state``
  (R14): states and counts only, no owner data.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from collections.abc import Awaitable, Callable
from typing import Any

from sudarshan.ops.release import installed_release
from sudarshan.tasks import cancel_and_wait
from sudarshan.vault import (
    ALERTS_SCREENER_DIGEST_INFO,
    SCREENER_API_INFO,
    SCREENER_INTERNAL_INFO,
    fernet_for,
)

from . import __version__
from .api import ScreenerApi
from .config import ConfigError, Settings, merged_env, read_openalgo_env, read_pepper, setup_logging
from .migrations import SchemaTooNew
from .store import DB_FILENAME, OWNER, ScreenerStore, StoreError
from .store import stop as stop_store_thread

log = logging.getLogger("sudarshan.screener")

SERVICE = "sudarshan-screener"
#: How long the service waits before it tries a taken port again.
PORT_RETRY_S = 60.0
Job = Callable[["Service"], Awaitable[None]]
JOBS: dict[str, Job] = {}
#: What the service runs while the screener is off (switched off, or kept off): dropping the feed
#: claims an earlier run left (release review HIGH-2).
OFF_JOBS: dict[str, Job] = {}


def job(name: str, registry: dict[str, Job] | None = None) -> Callable[[Job], Job]:
    """Register a long-running coroutine as a job the service starts when it is switched on."""
    book = JOBS if registry is None else registry

    def register(function: Job) -> Job:
        if name in book:
            raise ValueError(f"the job {name} is registered twice")
        book[name] = function
        return function

    return register


def open_store(settings: Settings) -> Any:
    """Open screener.db under ``settings.state_dir``, migrating it to the code's version.
    ``SchemaTooNew`` and ``StoreError`` come out as they are: the service keeps the screener off."""
    settings.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    return ScreenerStore(settings.state_dir / DB_FILENAME)


def close_store(store: Any) -> None:
    """Dispose the engine and end the store's writer thread, so that no thread, file or
    connection outlives the service."""
    try:
        store.close()
    finally:
        stop_store_thread()


class Service:
    """The process's state, and the Desk the API serves."""

    def __init__(
        self,
        settings: Settings,
        fernet,
        *,
        jobs: dict[str, Job] | None = None,
        off_jobs: dict[str, Job] | None = None,
        opener: Callable[[Settings], Any] = open_store,
        closer: Callable[[Any], None] = close_store,
        port_retry_s: float = PORT_RETRY_S,
    ):
        self.settings = settings
        self.enabled = settings.enabled
        self.api = ScreenerApi(self, fernet)
        #: The pepper-derived keys of the service's own calls (None in a test without a pepper).
        self.pepper: str | None = None
        self.store: Any = None
        #: Why the screener is off when the switch is on (a newer file, a store that will not
        #: open), in the owner's words; the API answers every op with it.
        self.off_reason: str | None = None
        self._jobs = JOBS if jobs is None else jobs
        # a test that names its jobs runs no off-time job unless it names those too
        self._off_jobs = (OFF_JOBS if jobs is None else {}) if off_jobs is None else off_jobs
        #: Set by an op that changed which screens are active or what they look at; the feed job
        #: follows it at its next tick: universes and claims (release review HIGH-2).
        self._screens_moved = False
        self._opener, self._closer, self._retry_s = opener, closer, port_retry_s
        #: The clock the ops and jobs read (epoch milliseconds); a test sets its own.
        self.clock: Callable[[], int] = lambda: int(time.time() * 1000)
        #: Components the rows register (the feed link, the scheduler, the list sync), by name;
        #: /health shows each one's own state words.
        self.parts: dict[str, Any] = {}
        self._calendar: Any = None
        self._state = "starting"
        self._tasks: dict[str, asyncio.Task] = {}
        self._stop = asyncio.Event()
        self._stop_reason = ""

    def ensure_calendar(self) -> Any:
        """The exchange's trading calendar, from the files the other services read."""
        if self._calendar is None:
            from sudarshan.autologin.schedule import TradingCalendar

            self._calendar = TradingCalendar(
                self.settings.holidays_file, self.settings.extra_days_file, logger=log
            )
        return self._calendar

    def screens_changed(self) -> None:
        """A screen was made, edited, resumed, paused or deleted: the feed job rebuilds the
        universes the active screens use and the claims at the feed, at once."""
        self._screens_moved = True

    def take_screens_moved(self) -> bool:
        moved, self._screens_moved = self._screens_moved, False
        return moved

    # ---- what /health shows ---------------------------------------------------------------
    @property
    def state(self) -> str:
        return self._state

    def health(self) -> dict[str, Any]:
        """The ops document plus the service's own ``screener_state``: states and counts only."""
        counts: dict[str, int] | None = None
        if self.store is not None:
            try:
                counts = self.store.counts(OWNER)
            except StoreError:
                counts = None
        parts = {}
        for name, part in self.parts.items():
            status = getattr(part, "status", None)
            parts[name] = status() if callable(status) else status
        return {
            "service": SERVICE,
            "release": installed_release(self.settings.deployed_tag_file),
            "version": __version__,
            "state": "running" if self._state != "stopping" else "stopping",
            "screener_state": {
                "enabled": self.enabled,
                "state": self._state,
                "store": "open" if self.store is not None else "closed",
                "counts": counts,
                "jobs": {
                    name: ("running" if not t.done() else "ended")
                    for name, t in self._tasks.items()
                },
                "parts": parts,
                **({"reason": self.off_reason} if self.off_reason else {}),
            },
        }

    def _set_state(self, state: str) -> None:
        if state != self._state:
            log.info("state %s -> %s", self._state, state)
            self._state = state

    # ---- life cycle -----------------------------------------------------------------------
    def stop(self, reason: str = "stop") -> None:
        self._stop_reason = reason
        self._set_state("stopping")
        self._stop.set()

    async def run(self) -> None:
        """Start, serve until ``stop``, and close everything that was opened, on every path."""
        api_task = None
        try:
            if self.enabled:
                self._open()
            if self.enabled:
                self._link()
                for name, function in self._jobs.items():
                    self._tasks[name] = asyncio.create_task(function(self), name=f"job-{name}")
            else:
                if not self.settings.enabled:
                    log.info(
                        "The screener is switched off (SUDARSHAN_SCREENER): every request is "
                        "answered as off; nothing is read or written"
                    )
                for name, function in self._off_jobs.items():
                    self._tasks[name] = asyncio.create_task(function(self), name=f"off-{name}")
            api_task = asyncio.create_task(self._serve_api(), name="screener-api")
            await self._stop.wait()
        finally:
            self._set_state("stopping")
            await cancel_and_wait(api_task, *self._tasks.values())
            await self.api.close()
            for part in list(self.parts.values()):
                closer = getattr(part, "aclose", None)
                if closer is not None:
                    try:
                        await closer()
                    except Exception:
                        log.exception("a screener component did not close cleanly")
            store, self.store = self.store, None
            if store is not None:
                try:
                    self._closer(store)
                except Exception:
                    log.exception("the screener's store did not close cleanly")
            log.info("stopped (%s)", self._stop_reason or "ended")

    def _open(self) -> None:
        """Open the store. A file a newer release wrote, or one that will not open, keeps the
        screener off (X7) and the service up, with the owner's sentence."""
        try:
            self.store = self._opener(self.settings)
        except SchemaTooNew as exc:
            log.error(
                "screener.db was written by a newer release (file %s, this code %s): the "
                "screener stays off", exc.found, exc.known,
            )  # fmt: skip
            self._keep_off(str(exc))
        except StoreError as exc:
            log.error("screener.db could not be opened (%s): the screener stays off", exc.cause)
            self._keep_off(str(exc))

    def _link(self) -> None:
        """The sealed client to OpenAlgo's internal routes and the alerts API (outbox.py); only
        when the pepper is known, so a test without one runs no sender."""
        if self.pepper is None or "link" in self.parts:
            return
        from .link import Link

        self.parts["link"] = Link(
            self.settings.openalgo_url,
            self.settings.alerts_url,
            fernet_for(self.pepper, SCREENER_INTERNAL_INFO),
            fernet_for(self.pepper, ALERTS_SCREENER_DIGEST_INFO),
        )

    def _keep_off(self, reason: str) -> None:
        self.enabled, self.off_reason = False, reason

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
                        "The screener cannot open its local API: port %d on this server is "
                        "taken (%s). The screener page cannot be used until it is free; "
                        "everything else the service does carries on. Free the port: this "
                        "service tries again every minute.", port, exc.strerror or exc,
                    )  # fmt: skip
                    told = True
                self._set_state("port_taken")
                await asyncio.sleep(self._retry_s)
                continue
            if told:
                log.info("port %d is free now; the screener's local API is open", port)
            self._set_state("running" if self.enabled else "off")
            return


# One line per row that adds a job (S17); each module registers with @job at import:
from . import outbox as _outbox  # noqa: E402  (M9.15: the outbox sender)
from . import runner as _runner  # noqa: E402  (M9.14: the run loop)
from . import runtime as _runtime  # noqa: E402  (M9.1: the feed link and live bars)
from . import screen_ops as _screen_ops  # noqa: E402  (M9.3: the engine line for an open page)

job("feed")(_runtime.run_feed)
job("runner")(_runner.run_runner)
job("outbox")(_outbox.run_sender)
job("engine")(_screen_ops.run_engine_push)
job("claims", OFF_JOBS)(_runtime.release_claims)


async def _serve(service: Service) -> None:
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, service.stop, f"signal {sig.name}")
    try:
        await service.run()
    finally:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(sig)


def main() -> int:
    setup_logging()
    try:
        settings = Settings.from_env()
        openalgo = read_openalgo_env(settings.env_file) if settings.env_file.exists() else {}
        pepper = read_pepper(merged_env(None, openalgo))
    except ConfigError as exc:
        log.error("sudarshan-screener cannot run: %s", exc)
        return 2
    setup_logging({"SUDARSHAN_SCREENER_LOG_LEVEL": settings.log_level})
    fernet = fernet_for(pepper, SCREENER_API_INFO)
    service = Service(settings, fernet)
    service.pepper = pepper
    try:
        asyncio.run(_serve(service))
    except Exception:  # a bug: systemd restarts it
        log.exception("sudarshan-screener stopped unexpectedly")
        return 1
    return 0
