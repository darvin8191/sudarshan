"""The sudarshan-family process (design 2.1, 2.8): one asyncio loop, the store, the accounts, the
one listener on 127.0.0.1:5585 and the jobs.

* **Switched off** (``SUDARSHAN_FAMILY`` is not on, the default): the store is not opened, no job
  runs, every viewer request and WebSocket answers 503 "The family view is switched off on the
  server.", and every admin op answers ``enabled: false`` once its seal is good. The service stays
  up so that the owner's page learns the switch from it.
* **A store a newer release wrote**, or one that will not open, keeps the view off the same way with
  its own sentence (a restart loop would only repeat it, and a newer file must not be written to).
* **Port taken** (cross-design P23): an ERROR in plain words, the jobs keep running, and the port is
  tried again every minute; never another port (X2). Exit status 2 is for a setting or key it
  cannot use, which a restart would not mend.
* **Stop**: every task is stopped with ``sudarshan.tasks.cancel_and_wait``; the listener closes, the
  sockets are closed, the store and its thread end, and the password thread ends.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import time
from collections.abc import Awaitable, Callable
from typing import Any

from sudarshan.tasks import cancel_and_wait

from . import accounts as accounts_module
from . import health, sessions, ws
from . import store as store_module
from .accounts import Accounts, Hasher
from .admin_api import SealedDoors
from .config import ConfigError, Keys, Settings, setup_logging
from .errors import FamilyError, SchemaTooNew, StoreError
from .listener import Listener, Request, Response, Router
from .store import DB_FILENAME, FamilyStore
from .viewer_api import ViewerApi

log = logging.getLogger("sudarshan.family")

#: How long the service waits before it tries a taken port again (cross-design P23).
PORT_RETRY_S = 60.0
#: Ended sessions older than 90 days are forgotten this often.
PRUNE_EVERY_S = 6 * 3600.0
#: The open sockets' sessions are checked this often: one that ended by the clock, or by a change
#: made elsewhere, closes within this (a revoke made here closes its sockets at once).
SWEEP_EVERY_S = 2.0
Job = Callable[["FamilyService"], Awaitable[None]]


class Viewer:
    """The viewer API's seat (``viewer_api.py`` takes it): until then every route of a viewer is
    not ready, and no WebSocket is let in."""

    async def http(self, request: Request) -> Response:
        raise FamilyError("not_ready")

    async def authorize(self, request: Request) -> sessions.Session:
        raise FamilyError("signed_out")


class FamilyService(Router):
    """The process's state; the Router the listener asks."""

    def __init__(
        self,
        settings: Settings,
        keys: Keys,
        *,
        clock: Callable[[], int] | None = None,
        port_retry_s: float = PORT_RETRY_S,
        hasher: Hasher | None = None,
        dialect: ws.Dialect | None = None,
        jobs: dict[str, Job] | None = None,
    ):
        self.settings, self.keys = settings, keys
        self.enabled = settings.enabled
        #: Why viewers are refused while the view is off: the switch, or the store's sentence.
        self.off_error: FamilyError | None = None if self.enabled else FamilyError("off")
        self.clock: Callable[[], int] = clock or (lambda: int(time.time() * 1000))
        self.hasher = hasher or Hasher()
        self.dialect: ws.Dialect = dialect or ws.Plain()
        self.registry = ws.Registry()
        self.store: FamilyStore | None = None
        self.accounts: Accounts | None = None
        self.viewer: Any = Viewer()
        self.listener = Listener(self)
        self.doors = SealedDoors(
            admin_key=keys.admin,
            nudge_key=keys.nudge,
            ops=self.admin_ops(),
            nudged=self.nudged,
            enabled=lambda: self.enabled,
            off_words=lambda: str(self.off_error or FamilyError("off")),
        )
        self.jobs: dict[str, asyncio.Task] = {}
        self._job_makers: dict[str, Job] = (
            {"prune": _prune, "sockets": _sweep} if jobs is None else jobs
        )
        self._retry_s = port_retry_s
        self._state = "starting"
        self._stop = asyncio.Event()
        self._stop_reason = ""

    # ---- what the doors call ------------------------------------------------------------------
    def admin_ops(self) -> dict[str, Callable[[dict[str, Any]], Awaitable[Any]]]:
        """The admin ops built so far, by contract name; the rest answer not_ready."""

        def via(method: str) -> Callable[[dict[str, Any]], Awaitable[Any]]:
            async def call(args: dict[str, Any]) -> Any:
                if self.accounts is None:
                    raise self.off_error or FamilyError("off")
                found = await getattr(self.accounts, method)(args)
                return found if found is not None else {"ok": True, "words": None}

            return call

        names = ("member_create", "member_update", "member_status", "member_remove",
                 "invite_create", "invite_revoke", "signin_reset", "device_list",
                 "device_revoke")  # fmt: skip
        built = {name: via(name) for name in names}
        built["member_list"] = self._member_list
        built["state"] = self._admin_state
        return built

    async def _member_list(self, args: dict[str, Any]) -> Any:
        if self.accounts is None:
            raise self.off_error or FamilyError("off")
        return await self.accounts.member_list()

    async def _admin_state(self, args: dict[str, Any]) -> dict[str, Any]:
        counts = await self.counts()
        return {"enabled": self.enabled, "message": None,
                "store_version": self.store.version if self.store else None,
                "members": counts["members"], "sessions": counts["sessions"],
                "sockets": len(self.registry)}  # fmt: skip

    def nudged(self, kind: str, sandbox: bool, at: int) -> bool:
        """An "orders changed" nudge: whether it made a book read due. The data plane takes it (F5);
        until then none is taken, and a sandbox one never will be (the books are the real account)."""
        return False

    def revoked(self, hashes: list[str]) -> None:
        """Sessions just revoked: their sockets are closed now (4401, "Signed out on this device.")."""
        self.registry.close_sessions(hashes)

    async def counts(self) -> dict[str, int]:
        if self.store is None:
            return {"members": 0, "sessions": 0}
        now = self.clock()

        def read(tx: store_module.Tx) -> dict[str, int]:
            live = tx.members_live()
            return {"members": len(live),
                    "sessions": sum(len(tx.sessions_live(m["id"], now)) for m in live)}  # fmt: skip

        return await self.store.read(read)

    # ---- the Router ---------------------------------------------------------------------------
    async def http(self, request: Request) -> Response:
        door = request.door
        if door == "health":
            if request.method != "GET":
                raise FamilyError("not_found")
            return Response(200, await self.health())
        if door == "admin":
            return await self.doors.admin(request)
        if door == "nudge":
            return await self.doors.nudge(request)
        if door == "viewer":
            if not self.enabled:
                raise self.off_error or FamilyError("off")
            return await self.viewer.http(request)
        raise FamilyError("not_found")  # /family/ws without an upgrade

    async def upgrade(self, request: Request, reader, writer) -> Response | None:
        if not self.enabled:
            return Response.refused(self.off_error or FamilyError("off"))
        if self.registry.full():
            return Response.refused(FamilyError("busy"))
        try:
            session = await self.viewer.authorize(request)
        except FamilyError as exc:
            return Response.refused(exc)
        sock = ws.ViewerSocket(reader, writer, session=session.id_hash,
                               member_id=session.member_id, dialect=self.dialect,
                               registry=self.registry)  # fmt: skip
        await sock.run(request.head)
        return None

    # ---- what /health shows ---------------------------------------------------------------------
    @property
    def state(self) -> str:
        return self._state

    async def health(self) -> dict[str, Any]:
        try:
            counts = await self.counts()
        except StoreError:
            counts = {}
        return health.document(self, counts)

    def _set_state(self, state: str) -> None:
        if state != self._state:
            log.info("state %s -> %s", self._state, state)
            self._state = state

    # ---- life cycle -------------------------------------------------------------------------------
    def stop(self, reason: str = "stop") -> None:
        self._stop_reason = reason
        self._set_state("stopping")
        self._stop.set()

    async def run(self) -> None:
        """Start, serve until ``stop``, and close everything that was opened, on every path."""
        serving = None
        try:
            if self.enabled:
                await self._open()
            if self.enabled:
                await self.hasher.warm()
                for job, make in self._job_makers.items():
                    self.jobs[job] = asyncio.create_task(make(self), name=f"family-job-{job}")
            elif not self.settings.enabled:
                log.info("The family view is switched off (SUDARSHAN_FAMILY): every viewer request "
                         "is answered as off; nothing is read or written")  # fmt: skip
            serving = asyncio.create_task(self._serve(), name="family-listener")
            await self._stop.wait()
        finally:
            self._set_state("stopping")
            self.registry.close_sessions(self.registry.sessions(), 1001, "")
            await cancel_and_wait(serving, *self.jobs.values())
            await self.listener.close()
            opened, self.store, self.accounts = self.store, None, None
            if opened is not None:
                opened.close()
            store_module.stop()
            accounts_module.stop_hasher()
            log.info("stopped (%s)", self._stop_reason or "ended")

    async def _open(self) -> None:
        try:
            self.store = await store_module.on_writer_async(
                lambda: FamilyStore(self.settings.state_dir / DB_FILENAME, self.keys.store)
            )
        except SchemaTooNew as exc:
            log.error("family.db was written by a newer release (%s): the family view stays off",
                      exc.cause)  # fmt: skip
            self._keep_off(exc)
            return
        except StoreError as exc:
            log.error("family.db could not be opened (%s): the family view stays off", exc.cause)
            self._keep_off(exc)
            return
        self.accounts = Accounts(self.store, clock=self.clock, hasher=self.hasher,
                                 revoked=self.revoked, max_members=self.settings.max_members,
                                 max_devices=self.settings.max_devices)  # fmt: skip
        if isinstance(self.viewer, Viewer):  # a test may have seated its own
            self.viewer = ViewerApi(self.accounts, self.keys.store)

    def _keep_off(self, error: FamilyError) -> None:
        self.enabled, self.off_error = False, error

    async def _serve(self) -> None:
        """Bind the port; while it is taken say so and try again every minute; then hold it."""
        port = self.settings.http_port
        told = False
        while True:
            try:
                await self.listener.start(port)
            except OSError as exc:
                if not told:
                    log.error(
                        "The family view cannot open its port: %d on this server is taken (%s). "
                        "Family phones and the owner's Family page cannot reach it until it is "
                        "free; everything else the service does carries on. This service tries "
                        "again every minute.", port, exc.strerror or exc,
                    )  # fmt: skip
                    told = True
                self._set_state("port_taken")
                await asyncio.sleep(self._retry_s)
                continue
            if told:
                log.info("port %d is free now; the family view's listener is open", port)
            self._set_state("running" if self.enabled else "off")
            return


async def _prune(service: FamilyService) -> None:
    """Forget sessions that ended more than 90 days ago, now and every six hours."""
    while True:
        if service.store is not None:
            try:
                gone = await service.store.write(lambda tx: sessions.prune(tx, service.clock()))
                if gone:
                    log.info("forgot %d sessions that ended more than 90 days ago", gone)
            except StoreError as exc:
                log.error("old sessions could not be forgotten this time (%s)", exc.cause)
        await asyncio.sleep(PRUNE_EVERY_S)


async def _sweep(service: FamilyService) -> None:
    """Close the sockets of sessions that no longer sign their member in, every two seconds."""
    while True:
        await asyncio.sleep(SWEEP_EVERY_S)
        hashes = service.registry.sessions()
        if not hashes or service.store is None:
            continue
        now = service.clock()

        try:
            gone = await service.store.write(
                lambda tx, hashes=hashes, now=now: _ended(tx, hashes, now)
            )
        except StoreError as exc:
            log.error("the open sockets could not be checked this time (%s)", exc.cause)
            continue
        if gone:
            service.registry.close_sessions(gone)


def _ended(tx: store_module.Tx, hashes: list[str], now: int) -> list[str]:
    """Which of these sessions no longer sign their member in (an ended one is marked, a used
    one touched, as any check does)."""
    return [h for h in hashes if isinstance(sessions.judge(tx, h, now), FamilyError)]


async def _run_with_signals(service: FamilyService) -> None:
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
        setup_logging(settings.log_level)
        keys = Keys.load(settings)
    except ConfigError as exc:
        log.error("sudarshan-family cannot run: %s", exc)
        return 2
    try:
        asyncio.run(_run_with_signals(FamilyService(settings, keys)))
    except Exception:  # a bug: systemd restarts it
        log.exception("sudarshan-family stopped unexpectedly")
        return 1
    return 0
