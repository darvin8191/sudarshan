"""The sudarshan-journal process (JB2, design 2.1 and 7): one asyncio loop, journal.db, the jobs and
the sealed local API on 127.0.0.1:5586.

* **Switched off** (``SUDARSHAN_JOURNAL`` is not on, the default in code): the store is not
  opened, no job runs, the feed is told to drop this service's claims, and the API answers every
  op with ``enabled: false``. The service stays up so that the bridge learns the switch from the
  API or from ``/health`` and the page says so.
* **The store** is opened once at start and closed on stop (``open_store``, ``close_store``). A
  file a newer release wrote, or one that will not open, keeps the journal off with the owner's
  sentence (the API and ``/health`` carry it); the service stays up.
* **Jobs** register from their own modules with ``@job("name")`` and are started as tasks here.
  Every blocking call (SQLite, a file read) runs off the loop; the loop does I/O and timers only.
  Tasks are stopped with ``sudarshan.tasks.cancel_and_wait``.
* **Port taken** (cross-design P23): an ERROR in plain words, the jobs keep running, and the
  port is tried again every minute. The service never exits for this and never tries another
  port (X2). Exit status 2 is for a setting or key it cannot use, which a restart would not mend.
* **/health** is the ops document (the release, the version, the state) plus ``journal_state``
  (R14): states and counts only, nothing secret and no money.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

import httpx

from sudarshan.feed.client_auth import with_auth
from sudarshan.ops.release import installed_release
from sudarshan.tasks import cancel_and_wait
from sudarshan.vault import (
    JOURNAL_API_INFO,
    JOURNAL_DELIVERY_INFO,
    JOURNAL_FAMILY_INFO,
    JOURNAL_NUDGE_INFO,
    PORTFOLIO_JOURNAL_API_INFO,
    fernet_for,
)

from . import __version__
from .api import JournalApi
from .config import (
    ConfigError,
    Settings,
    merged_env,
    read_openalgo_env,
    read_pepper,
    setup_logging,
)
from .core.kinds import SERVICE
from .migrations import SchemaTooNew
from .store import DB_FILENAME, JournalStore, StoreError
from .store import stop as stop_store_thread

log = logging.getLogger("sudarshan.journal")

#: How long the service waits before it tries a taken port again (cross-design P23).
PORT_RETRY_S = 60.0
#: The feed's id for this client: its claims and its quota (50, cross-design Y12) carry it.
FEED_CLIENT = SERVICE
#: Waits between tries at releasing the claims while the journal is off; then it gives up (the
#: feed lets a claim lapse by itself after 7 days).
RELEASE_DELAYS_S = (0.0, 5.0, 15.0, 30.0, 60.0, 60.0, 60.0, 60.0)
FEED_TIMEOUT_S = 5.0
#: The seal purposes this service serves, each with its key's ``info`` (sudarshan/vault/keys.py).
PURPOSE_KEYS = {
    "api": JOURNAL_API_INFO,
    "delivery": JOURNAL_DELIVERY_INFO,
    "nudge": JOURNAL_NUDGE_INFO,
    "family": JOURNAL_FAMILY_INFO,
}
Job = Callable[["Service"], Awaitable[None]]
JOBS: dict[str, Job] = {}


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
    """Open journal.db under ``settings.state_dir``: the migrations to the code's version and the
    first-start seeds, each in one transaction. ``SchemaTooNew`` and ``StoreError`` come out as
    they are: the service keeps the journal off."""
    settings.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    return JournalStore(settings.state_dir / DB_FILENAME)


def close_store(store: Any) -> None:
    """Dispose the engines and end the store's writer thread, so that no thread, file or
    connection outlives the service. Called once, with what ``open_store`` returned."""
    try:
        store.close()
    finally:
        stop_store_thread()


async def release_claims(
    feed_url: str,
    delays: Iterable[float] = RELEASE_DELAYS_S,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> bool:
    """Drop this service's claims at the feed, trying again while the feed does not answer.
    True when the feed holds no set for this client any more. Used while the journal is off; a
    plain stop or restart keeps them."""
    url = f"{feed_url.rstrip('/')}/claims/{FEED_CLIENT}"
    async with httpx.AsyncClient(timeout=FEED_TIMEOUT_S, trust_env=False) as client:
        for wait in delays:
            if wait:
                await sleep(wait)
            try:
                answer = await client.delete(url, headers=with_auth(FEED_CLIENT))
            except httpx.HTTPError:
                continue
            if answer.status_code in (200, 204, 404):
                return True
    log.warning("The journal is off but the price feed still holds its claims.")
    return False


class Service:
    """The process's state, and the Desk the API serves."""

    def __init__(
        self,
        settings: Settings,
        keys: dict[str, Any],
        *,
        portfolio_key: Any = None,
        jobs: dict[str, Job] | None = None,
        opener: Callable[[Settings], Any] = open_store,
        closer: Callable[[Any], None] = close_store,
        port_retry_s: float = PORT_RETRY_S,
    ):
        self.settings = settings
        self.enabled = settings.enabled
        self.api = JournalApi(self, keys)
        #: The key of the portfolio's journal purpose (cross-design Y4): every account read.
        self.portfolio_key = portfolio_key
        self.store: Any = None
        #: Why the journal is off when the switch is on, in the owner's words.
        self.off_reason: str | None = None
        self._jobs = JOBS if jobs is None else jobs
        self._opener, self._closer, self._retry_s = opener, closer, port_retry_s
        #: The clock the jobs and ops read (epoch milliseconds); a test sets its own.
        self.clock: Callable[[], int] = lambda: int(time.time() * 1000)
        #: Parts built on first use by the modules that own them (a test puts its own here).
        self.parts: dict[str, Any] = {}
        #: What /health shows of the journal: counts kept current by the jobs, never read here.
        self.counts: dict[str, Any] = {
            "fills_today": None,
            "open_trips": None,
            "last_reconcile_ms": None,
            "claims": 0,
        }
        self._state = "starting"
        self._tasks: dict[str, asyncio.Task] = {}
        self._stop = asyncio.Event()
        self._stop_reason = ""

    def part(self, name: str, make: Callable[[Service], Any]) -> Any:
        """The service object ``name`` (a reader, a client), built once."""
        have = self.parts.get(name)
        if have is None:
            have = self.parts[name] = make(self)
        return have

    # ---- what /health shows ---------------------------------------------------------------
    @property
    def state(self) -> str:
        return self._state

    def health(self) -> dict[str, Any]:
        """The ops document plus the service's own ``journal_state``: states and counts only."""
        return {
            "service": SERVICE,
            "release": installed_release(self.settings.deployed_tag_file),
            "version": __version__,
            "state": "running" if self._state != "stopping" else "stopping",
            "journal_state": {
                "enabled": self.enabled,
                "state": self._state,
                "store": "open" if self.store is not None else "closed",
                "jobs": {
                    name: ("running" if not t.done() else "ended")
                    for name, t in self._tasks.items()
                },
                **self.counts,
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
                for name, function in self._jobs.items():
                    self._tasks[name] = asyncio.create_task(function(self), name=f"job-{name}")
            else:
                self._tasks["release_claims"] = asyncio.create_task(
                    release_claims(self.settings.feed_url), name="job-release-claims"
                )
                if not self.settings.enabled:
                    log.info("The journal is switched off (SUDARSHAN_JOURNAL): every request is "
                             "answered as off; nothing is read or written")  # fmt: skip
            api_task = asyncio.create_task(self._serve_api(), name="journal-api")
            await self._stop.wait()
        finally:
            self._set_state("stopping")
            await cancel_and_wait(api_task, *self._tasks.values())
            await self.api.close()
            for name, held in list(self.parts.items()):
                closer = getattr(held, "aclose", None)
                if closer is not None:
                    try:
                        await closer()
                    except Exception:
                        log.exception("the journal's %s did not close cleanly", name)
            self.parts.clear()
            store, self.store = self.store, None
            if store is not None:
                try:
                    self._closer(store)
                except Exception:
                    log.exception("the journal's store did not close cleanly")
            log.info("stopped (%s)", self._stop_reason or "ended")

    def _open(self) -> None:
        """Open the store. A file a newer release wrote, or one that will not open, keeps the
        journal off (X7) and the service up, with the owner's sentence: a restart loop would only
        repeat it, and a newer file must not be written to."""
        try:
            self.store = self._opener(self.settings)
        except SchemaTooNew as exc:
            log.error("journal.db was written by a newer release (file %s, this code %s): the "
                      "journal stays off", exc.found, exc.known)  # fmt: skip
            self._keep_off(str(exc))
        except StoreError as exc:
            log.error("journal.db could not be opened (%s): the journal stays off", exc.cause)
            self._keep_off(str(exc))

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
                        "The journal cannot open its local API: port %d on this server is taken "
                        "(%s). The Journal and Reports pages cannot be used until it is free; "
                        "everything else the journal does carries on. Free the port: this "
                        "service tries again every minute.", port, exc.strerror or exc,
                    )  # fmt: skip
                    told = True
                self._set_state("port_taken")
                await asyncio.sleep(self._retry_s)
                continue
            if told:
                log.info("port %d is free now; the journal's local API is open", port)
            self._set_state("running" if self.enabled else "off")
            return


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
    """The four purposes' keys, derived from OpenAlgo's pepper (sudarshan/vault/keys.py)."""
    return {name: fernet_for(pepper, info) for name, info in PURPOSE_KEYS.items()}


def main() -> int:
    setup_logging()
    try:
        settings = Settings.from_env()
        openalgo = read_openalgo_env(settings.env_file) if settings.env_file.exists() else {}
        pepper = read_pepper(merged_env(None, openalgo))
    except ConfigError as exc:
        log.error("sudarshan-journal cannot run: %s", exc)
        return 2
    setup_logging({"SUDARSHAN_JOURNAL_LOG_LEVEL": settings.log_level})
    try:
        service = Service(
            settings,
            keys_for(pepper),
            portfolio_key=fernet_for(pepper, PORTFOLIO_JOURNAL_API_INFO),
        )
        asyncio.run(_serve(service))
    except Exception:  # a bug: systemd restarts it
        log.exception("sudarshan-journal stopped unexpectedly")
        return 1
    return 0
