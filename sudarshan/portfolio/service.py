"""The sudarshan-portfolio process (M7.18, design 2.1 and 7): one asyncio loop, the store, the jobs
and the sealed local API on 127.0.0.1:5583.

* **Switched off** (``SUDARSHAN_PORTFOLIO`` is not on): the store is not opened, no job runs, and
  the API answers every op with ``enabled: false`` (api.py). The service stays up so that the
  bridge learns the switch from the API and the page says so.
* **The store** is opened once at start and closed on stop, in ``open_store`` and ``close_store``
  below: the one place the store's merge fills (PB1, PB2). Until then they open nothing. The loop
  never does store work; the store owns its one writer thread and ``close_store`` ends it.
* **Jobs** register from their own modules with ``@job("name")`` (the closing record, the settle,
  the NAV fetch, the feed claims) and are started as tasks here; none exists yet.
* **Port taken** (cross-design P23): an ERROR in plain words, the jobs keep running, and the
  port is tried again every minute. The service never exits for this and never tries another
  port (X2). Exit status 2 is for a setting or key it cannot use, which a restart would not mend.
* **/health** is the ops document (the release, the version, the state) plus ``portfolio_state``
  (R14); no owner data.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable
from typing import Any

from sudarshan.ops.release import installed_release
from sudarshan.tasks import cancel_and_wait
from sudarshan.vault import PORTFOLIO_API_INFO, fernet_for

from . import __version__
from .api import PortfolioApi
from .config import ConfigError, Settings, merged_env, read_pepper, setup_logging

log = logging.getLogger("sudarshan.portfolio")

SERVICE = "sudarshan-portfolio"
#: How long the service waits before it tries a taken port again (cross-design P23).
PORT_RETRY_S = 60.0
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
    """THE SEAM, start: open portfolio.db under ``settings.state_dir`` (migrations, the writer
    thread) and return the store, or None. Filled by the store's merge; opens nothing yet."""
    return None


def close_store(store: Any) -> None:
    """THE SEAM, stop: end the writer thread and dispose the engines, so that no thread, file or
    connection outlives the service. Called once, with what ``open_store`` returned."""
    return None


class Service:
    """The process's state, and the Desk the API serves."""

    def __init__(
        self,
        settings: Settings,
        fernet,
        *,
        jobs: dict[str, Job] | None = None,
        opener: Callable[[Settings], Any] = open_store,
        closer: Callable[[Any], None] = close_store,
        port_retry_s: float = PORT_RETRY_S,
    ):
        self.settings = settings
        self.enabled = settings.enabled
        self.api = PortfolioApi(self, fernet)
        self.store: Any = None
        self._jobs = JOBS if jobs is None else jobs
        self._opener, self._closer, self._retry_s = opener, closer, port_retry_s
        self._state = "starting"
        self._tasks: dict[str, asyncio.Task] = {}
        self._stop = asyncio.Event()
        self._stop_reason = ""

    # ---- what /health shows ---------------------------------------------------------------
    @property
    def state(self) -> str:
        return self._state

    def health(self) -> dict[str, Any]:
        """The ops document plus the service's own ``portfolio_state``: states and counts only."""
        return {
            "service": SERVICE,
            "release": installed_release(self.settings.deployed_tag_file),
            "version": __version__,
            "state": "running" if self._state != "stopping" else "stopping",
            "portfolio_state": {
                "enabled": self.enabled,
                "state": self._state,
                "store": "open" if self.store is not None else "closed",
                "jobs": {
                    name: ("running" if not t.done() else "ended")
                    for name, t in self._tasks.items()
                },  # fmt: skip
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
                self.store = self._opener(self.settings)
                for name, function in self._jobs.items():
                    self._tasks[name] = asyncio.create_task(function(self), name=f"job-{name}")
            else:
                log.info("The portfolio is switched off (SUDARSHAN_PORTFOLIO): every request is "
                         "answered as off; nothing is read or written")  # fmt: skip
            api_task = asyncio.create_task(self._serve_api(), name="portfolio-api")
            await self._stop.wait()
        finally:
            self._set_state("stopping")
            await cancel_and_wait(api_task, *self._tasks.values())
            await self.api.close()
            store, self.store = self.store, None
            if store is not None:
                try:
                    self._closer(store)
                except Exception:
                    log.exception("the portfolio's store did not close cleanly")
            log.info("stopped (%s)", self._stop_reason or "ended")

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
                        "The portfolio cannot open its local API: port %d on this server is "
                        "taken (%s). The portfolio page cannot be used until it is free; "
                        "everything else the service does carries on. Free the port: this "
                        "service tries again every minute.", port, exc.strerror or exc,
                    )  # fmt: skip
                    told = True
                self._set_state("port_taken")
                await asyncio.sleep(self._retry_s)
                continue
            if told:
                log.info("port %d is free now; the portfolio's local API is open", port)
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


def main() -> int:
    setup_logging()
    try:
        settings = Settings.from_env()
        pepper = read_pepper(merged_env(None, _openalgo_env(settings)))
    except ConfigError as exc:
        log.error("sudarshan-portfolio cannot run: %s", exc)
        return 2
    setup_logging({"SUDARSHAN_PORTFOLIO_LOG_LEVEL": settings.log_level})
    fernet = fernet_for(pepper, PORTFOLIO_API_INFO)
    try:
        asyncio.run(_serve(Service(settings, fernet)))
    except Exception:  # a bug: systemd restarts it
        log.exception("sudarshan-portfolio stopped unexpectedly")
        return 1
    return 0


def _openalgo_env(settings: Settings) -> dict[str, str]:
    from .config import read_openalgo_env

    return read_openalgo_env(settings.env_file) if settings.env_file.exists() else {}
