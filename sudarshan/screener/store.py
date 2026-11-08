"""screener.db, the screener's record (M9.1, design 6.1): screens, runs, membership, the outbox
and the shared data stores.

``$STATE_DIRECTORY/screener.db`` (directory 700, file 600, made before SQLite can touch them).
The engine is OpenAlgo's ``database.engine_factory.create_db_engine`` (NullPool: a connection per
transaction, closed at once); WAL and synchronous=NORMAL come from the listener in
``database/__init__.py``. This engine also emits its own BEGIN, so a migration's DDL and every
write are whole transactions, and a write begins IMMEDIATE: it holds the write lock from its
first read, so two writers queue instead of one failing on a snapshot the other's commit made
stale (cross-design X7).

One writer. Every ``read`` and ``write`` runs on the process's one store thread, a module-level
single-thread executor (``stop`` shuts it down); a call made from that thread runs in place, so
the service's loop never writes SQLite itself (S13). A caller that is not the service (the replay
check, ``migrate --status``) opens the file read-only (``open_read_only``) and never migrates.

The shape: ``ScreenerStore.read(who, fn)`` and ``write(who, fn)`` hand ``fn`` a ``Tx`` bound to
one principal (``owner`` in this release). Every query on an owner-data table (``migrations.
OWNER_TABLES``) filters by that principal; a structural test holds each method to it (6.5). The
shared tables (universes, today's bars, coverage, the daily OI and IV stores, pauses, meta) carry
no principal.

Versions. An owner edit names the version of the screen it was decided on; a mismatch raises
``VersionConflict`` and nothing is written. A run applies only to the screen version it was
evaluated on (``run_insert`` checks it). Failures reach the owner as ``StoreError`` sentences;
the system's own words are in ``.cause`` and the journal only.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote

from sqlalchemy import MetaData, Table, delete, event, func, insert, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from database.engine_factory import create_db_engine

from . import migrations
from .migrations import SchemaTooNew

log = logging.getLogger("sudarshan.screener")

DB_FILENAME = "screener.db"
STATE_DIR = Path("/var/lib/sudarshan-screener")
THREAD_PREFIX = "screener-store"
OWNER = "owner"
DAY_MS = 86_400_000

#: Screen statuses: active runs; paused keeps the definition and its list; the engine sets
#: ``waiting`` (warming, outside hours) and ``off`` (the switch) in words on the page, not here.
SCREEN_STATUSES = ("active", "paused")
RUN_STATUSES = ("done", "superseded", "failed")
OUTBOX_KINDS = ("list_sync", "digest", "push")
OUTBOX_STATUSES = ("pending", "done", "failed")
#: Retention (6.1): runs and changes 100 days (never under 90), values 7 days, done outbox rows 2.
KEEP_RUN_DAYS_MIN = 90
KEEP_VALUE_DAYS = 7
KEEP_OUTBOX_DAYS = 2
#: The outbox holds at most this many pending rows; past it the oldest pushes go, never list
#: syncs or digests (6.6).
OUTBOX_MAX_PENDING = 1_000

#: Columns that hold JSON text; the store converts at the boundary.
JSON_COLUMNS = frozenset({"spec", "marks", "values_json", "payload"})
FLAG_COLUMNS = frozenset(
    {"shared", "alerts_paused", "pinned", "added_by_screener", "in_list", "cas"}
)
#: Never leave the store: who owns a row, and the client's retry key.
HIDDEN_COLUMNS = ("principal", "request_id")

WORDS = {
    "opened": (
        "Your screens could not be opened. Nothing was changed; check the screener service on "
        "the server."
    ),
    "written": (
        "This could not be saved. Nothing was changed; try again in a minute. If it keeps "
        "failing, check that the server has free disk space."
    ),
    "read": (
        "Your screens could not be read just now. Try again in a minute; if it keeps failing, "
        "check the screener service on the server."
    ),
    "gone": "This screen is no longer there; it may have been deleted on another screen.",
    "version_conflict": (
        "This screen was changed on another screen. Nothing was saved; open it again and repeat "
        "the change."
    ),
    "stale_run": "This run judged an older version of the screen, so it was not kept.",
}


class StoreError(RuntimeError):
    """The record refused; ``str()`` is a sentence for the owner, ``cause`` the system's own
    words for the journal (never shown)."""

    def __init__(self, message: str, cause: str = ""):
        super().__init__(message)
        self.cause = cause


class NotFound(StoreError):
    def __init__(self, message: str = WORDS["gone"]) -> None:
        super().__init__(message)


class VersionConflict(StoreError):
    """Nothing was written: the screen is not at the version the change was decided on."""

    def __init__(self, message: str = WORDS["version_conflict"]) -> None:
        super().__init__(message)


class UnknownPrincipal(ValueError):
    """A caller asked for someone this store does not serve (a programming error)."""


# ---- the one writer thread -----------------------------------------------------------------
_pool_lock = threading.Lock()
_pool: concurrent.futures.ThreadPoolExecutor | None = None
_here = threading.local()


def _mark_writer() -> None:
    _here.writer = True


def _executor() -> concurrent.futures.ThreadPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix=THREAD_PREFIX, initializer=_mark_writer
            )
        return _pool


def stop() -> None:
    """Shut the store thread down after the work it has; the next call makes a new one."""
    global _pool
    with _pool_lock:
        pool, _pool = _pool, None
    if pool is not None:
        pool.shutdown(wait=True)


def _on_writer[T](fn: Callable[[], T]) -> T:
    if getattr(_here, "writer", False):
        return fn()
    return _executor().submit(fn).result()


# ---- files and engines ---------------------------------------------------------------------
def default_path() -> Path:
    base = os.environ.get("SUDARSHAN_SCREENER_STATE_DIR") or os.environ.get("STATE_DIRECTORY")
    return Path((base or str(STATE_DIR)).split(":")[0]) / DB_FILENAME


def _cause(exc: BaseException | None) -> str:
    """The failure in words: the system's or SQLite's own message, never a class name."""
    if exc is None:
        return "the database could not be used"
    if isinstance(exc, OSError):
        return (exc.strerror or "the file cannot be written").lower()
    original = getattr(exc, "orig", None)
    return str(original) if original else "the database refused the change"


def _private_file(path: Path) -> None:
    """The directory (700) and an empty file (600) before SQLite creates it with the umask."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with contextlib.suppress(FileExistsError):
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))


def engine(path: Path) -> Engine:
    """screener.db's engine: NullPool from the factory, and a real BEGIN for every transaction,
    IMMEDIATE on a connection with the ``migrations.IMMEDIATE`` option (``writer``)."""
    made = create_db_engine(f"sqlite:///{path}")

    @event.listens_for(made, "connect")
    def _no_driver_begin(dbapi_connection, record):
        dbapi_connection.isolation_level = None  # SQLAlchemy's "begin" below opens them instead

    @event.listens_for(made, "begin")
    def _begin(conn):
        immediate = conn.get_execution_options().get(migrations.IMMEDIATE)
        conn.exec_driver_sql("BEGIN IMMEDIATE" if immediate else "BEGIN")

    return made


def writer(made: Engine) -> Engine:
    """The same engine for writes: each transaction begins IMMEDIATE."""
    return made.execution_options(**{migrations.IMMEDIATE: True})


def read_only_urls(path: Path) -> list[str]:
    """The two read-only ways to open a WAL database, the one that fits its state first."""
    target = quote(str(path))
    ro = f"sqlite:///file:{target}?mode=ro&uri=true"
    immutable = f"sqlite:///file:{target}?immutable=1&uri=true"
    return [ro, immutable] if Path(f"{path}-wal").exists() else [immutable, ro]


def open_read_only(path: Path) -> Engine:
    """An engine that cannot write, for ``--status`` and replay checks; never migrates."""
    if not path.exists():
        raise StoreError(WORDS["opened"], "no such file")
    last: Exception | None = None
    for url in read_only_urls(path):
        made = create_db_engine(url)
        try:
            with made.connect() as conn:
                conn.exec_driver_sql("PRAGMA user_version").scalar()
            return made
        except SQLAlchemyError as exc:
            made.dispose()
            last = exc
    raise StoreError(WORDS["opened"], _cause(last)) from last


def tables_of(conn: Connection) -> Iterator[str]:
    """The tables of an open file (the schema tests and ``--status``)."""
    for row in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'table'"):
        if not row[0].startswith("sqlite_"):
            yield row[0]


# ---- one transaction's queries -------------------------------------------------------------
class Tx:
    """The queries of one transaction for one principal. Every statement on an owner-data
    table filters by ``principal`` (``_mine``); the shared tables carry none."""

    def __init__(self, conn: Connection, tables: Mapping[str, Table], who: str):
        self.conn, self._tables, self.who = conn, tables, who

    def _t(self, name: str) -> Table:
        return self._tables[name]

    def _mine(self, table: Table):
        return table.c.principal == self.who

    @staticmethod
    def _out(row: Mapping | None) -> dict[str, Any] | None:
        if row is None:
            return None
        out = {}
        for key, value in row.items():
            if key in HIDDEN_COLUMNS:
                continue
            if key in JSON_COLUMNS and value is not None:
                value = json.loads(value)
            elif key in FLAG_COLUMNS:
                value = bool(value)
            out["values" if key == "values_json" else key] = value
        return out

    @staticmethod
    def _in(values: Mapping[str, Any]) -> dict[str, Any]:
        out = {}
        for key, value in values.items():
            if key == "values":
                key = "values_json"
            if key in JSON_COLUMNS and value is not None:
                value = json.dumps(value, sort_keys=True, separators=(",", ":"))
            elif key in FLAG_COLUMNS and value is not None:
                value = int(bool(value))
            out[key] = value
        return out

    def _all(self, query) -> list[dict[str, Any]]:
        return [self._out(row) for row in self.conn.execute(query).mappings()]  # type: ignore

    # ---- screens ----
    def screens(self) -> list[dict[str, Any]]:
        t = self._t("screen")
        return self._all(select(t).where(self._mine(t)).order_by(t.c.id))

    def screen(self, screen_id: int) -> dict[str, Any] | None:
        t = self._t("screen")
        row = self.conn.execute(select(t).where(t.c.id == screen_id, self._mine(t))).mappings()
        return self._out(row.first())

    def screen_by_request(self, request_id: str) -> dict[str, Any] | None:
        """The screen an earlier attempt of the same create made, if any."""
        t = self._t("screen")
        row = self.conn.execute(
            select(t).where(t.c.request_id == request_id, self._mine(t))
        ).mappings()
        return self._out(row.first())

    def screen_count(self, status: str | None = None) -> int:
        t = self._t("screen")
        query = select(func.count()).select_from(t).where(self._mine(t))
        if status is not None:
            query = query.where(t.c.status == status)
        return int(self.conn.execute(query).scalar() or 0)

    def screen_insert(
        self,
        name: str,
        spec: Mapping[str, Any],
        status: str,
        now: int,
        request_id: str | None = None,
        status_reason: str | None = None,
    ) -> dict[str, Any]:
        if status not in SCREEN_STATUSES:
            raise ValueError(f"not a screen status: {status!r}")
        t = self._t("screen")
        key = self.conn.execute(
            insert(t).values(
                principal=self.who,
                name=name,
                status=status,
                status_reason=status_reason,
                request_id=request_id,
                created_at=now,
                updated_at=now,
                **self._in({"spec": spec}),
            )
        ).inserted_primary_key[0]
        self.conn.execute(
            insert(self._t("screen_state")).values(
                screen_id=key, principal=self.who, updated_at=now
            )
        )
        return self.screen(key)  # type: ignore[return-value]

    def screen_change(
        self, screen_id: int, version: int, values: Mapping[str, Any], now: int
    ) -> dict[str, Any]:
        """Update a screen only from ``version``; NotFound or VersionConflict, writing nothing."""
        if "status" in values and values["status"] not in SCREEN_STATUSES:
            raise ValueError(f"not a screen status: {values['status']!r}")
        t = self._t("screen")
        done = self.conn.execute(
            update(t)
            .where(t.c.id == screen_id, self._mine(t), t.c.version == version)
            .values(**self._in(values), updated_at=now, version=version + 1)
        ).rowcount
        if not done:
            if self.screen(screen_id) is None:
                raise NotFound()
            raise VersionConflict()
        return self.screen(screen_id)  # type: ignore[return-value]

    def screen_remove(self, screen_id: int) -> bool:
        """A screen and what hangs on it: its state, membership, alert marks and pending outbox
        rows. Its runs stay for the history until they are pruned."""
        for name, column in (
            ("screen_state", "screen_id"),
            ("membership", "screen_id"),
            ("alert_state", "screen_id"),
        ):
            t = self._t(name)
            self.conn.execute(delete(t).where(t.c[column] == screen_id, self._mine(t)))
        t = self._t("screen")
        return bool(self.conn.execute(delete(t).where(t.c.id == screen_id, self._mine(t))).rowcount)

    def state(self, screen_id: int) -> dict[str, Any] | None:
        t = self._t("screen_state")
        row = self.conn.execute(
            select(t).where(t.c.screen_id == screen_id, self._mine(t))
        ).mappings()
        return self._out(row.first())

    def state_put(self, screen_id: int, now: int, **fields: Any) -> None:
        t = self._t("screen_state")
        done = self.conn.execute(
            update(t)
            .where(t.c.screen_id == screen_id, self._mine(t))
            .values(**self._in(fields), updated_at=now)
        ).rowcount
        if not done:
            self.conn.execute(
                insert(t).values(
                    screen_id=screen_id, principal=self.who, updated_at=now, **self._in(fields)
                )
            )

    # ---- runs ----
    def run(self, run_id: int) -> dict[str, Any] | None:
        t = self._t("run")
        row = self.conn.execute(select(t).where(t.c.id == run_id, self._mine(t))).mappings()
        return self._out(row.first())

    def run_by_key(self, run_key: str) -> dict[str, Any] | None:
        t = self._t("run")
        row = self.conn.execute(select(t).where(t.c.run_key == run_key, self._mine(t))).mappings()
        return self._out(row.first())

    def run_insert(self, values: Mapping[str, Any]) -> dict[str, Any]:
        """Record a run of a screen at the version it judged. A ``done`` run of an older version
        is refused (``VersionConflict``): it must be recorded ``superseded`` instead, which is
        always accepted and changes nothing else. A run already recorded is returned as it is."""
        if values["status"] not in RUN_STATUSES:
            raise ValueError(f"not a run status: {values['status']!r}")
        have = self.run_by_key(values["run_key"])
        if have is not None:
            return have
        screen = self.screen(values["screen_id"])
        if screen is None:
            raise NotFound()
        if values["status"] == "done" and screen["version"] != values["screen_version"]:
            raise VersionConflict(WORDS["stale_run"])
        t = self._t("run")
        key = self.conn.execute(
            insert(t).values(principal=self.who, **self._in(values))
        ).inserted_primary_key[0]
        return self.run(key)  # type: ignore[return-value]

    def run_finish(self, run_id: int, marks: Mapping[str, Any] | None, **counts: int) -> None:
        """The counts and marks of a run once it is evaluated (the row is made first so the
        membership rows can name it)."""
        t = self._t("run")
        self.conn.execute(
            update(t)
            .where(t.c.id == run_id, self._mine(t))
            .values(**self._in({"marks": marks or {}}), **counts)
        )

    def run_mark(self, run_id: int, status: str) -> None:
        if status not in RUN_STATUSES:
            raise ValueError(f"not a run status: {status!r}")
        t = self._t("run")
        self.conn.execute(update(t).where(t.c.id == run_id, self._mine(t)).values(status=status))

    def latest_run(self, screen_id: int) -> dict[str, Any] | None:
        t = self._t("run")
        query = (
            select(t)
            .where(t.c.screen_id == screen_id, self._mine(t), t.c.status == "done")
            .order_by(t.c.id.desc())
            .limit(1)
        )
        return self._out(self.conn.execute(query).mappings().first())

    def runs_page(self, screen_id: int, before: int | None, limit: int) -> list[dict[str, Any]]:
        """A screen's runs, newest first; ``before`` is the id of the last run of the page before."""
        t = self._t("run")
        query = select(t).where(t.c.screen_id == screen_id, self._mine(t))
        if before is not None:
            query = query.where(t.c.id < before)
        return self._all(query.order_by(t.c.id.desc()).limit(limit))

    def runs_page_all(self, limit: int) -> list[dict[str, Any]]:
        """The newest runs of every screen (the page's catch-up mark)."""
        t = self._t("run")
        return self._all(select(t).where(self._mine(t)).order_by(t.c.id.desc()).limit(limit))

    def runs_session(self, session: int) -> list[dict[str, Any]]:
        """Every run of one trading day, oldest first."""
        t = self._t("run")
        return self._all(select(t).where(t.c.session == session, self._mine(t)).order_by(t.c.id))

    def runs_since(self, after: int, limit: int) -> list[dict[str, Any]]:
        """Every run recorded after run ``after``, oldest first (the page's catch-up)."""
        t = self._t("run")
        query = select(t).where(t.c.id > after, self._mine(t)).order_by(t.c.id).limit(limit)
        return self._all(query)

    def run_changes_put(self, run_id: int, rows: Iterable[Mapping[str, Any]]) -> int:
        t = self._t("run_change")
        values = [{"run_id": run_id, "principal": self.who, **row} for row in rows]
        if values:
            self.conn.execute(insert(t), values)
        return len(values)

    def run_changes(self, run_id: int) -> list[dict[str, Any]]:
        t = self._t("run_change")
        query = select(t).where(t.c.run_id == run_id, self._mine(t)).order_by(t.c.rank, t.c.key)
        return self._all(query)

    def run_values_put(self, run_id: int, values: Mapping[str, Mapping[str, Any]]) -> int:
        t = self._t("run_value")
        rows = [
            {"run_id": run_id, "principal": self.who, "key": key, **self._in({"values": value})}
            for key, value in values.items()
        ]
        if rows:
            self.conn.execute(insert(t), rows)
        return len(rows)

    def run_values(self, run_id: int) -> dict[str, Any]:
        t = self._t("run_value")
        found = self.conn.execute(select(t).where(t.c.run_id == run_id, self._mine(t))).mappings()
        return {row["key"]: json.loads(row["values_json"]) for row in found}

    # ---- membership ----
    def memberships(
        self, screen_id: int | None = None, list_id: int | None = None
    ) -> list[dict[str, Any]]:
        t = self._t("membership")
        query = select(t).where(self._mine(t))
        if screen_id is not None:
            query = query.where(t.c.screen_id == screen_id)
        if list_id is not None:
            query = query.where(t.c.list_id == list_id)
        return self._all(query.order_by(t.c.id))

    def membership(self, member_id: int) -> dict[str, Any] | None:
        t = self._t("membership")
        row = self.conn.execute(select(t).where(t.c.id == member_id, self._mine(t))).mappings()
        return self._out(row.first())

    def membership_put(
        self, screen_id: int, list_id: int, row: Mapping[str, Any], now: int
    ) -> dict[str, Any]:
        """Write one instrument's standing (the engine's ``membership.Row`` as a mapping) for a
        screen and its list: inserted the first time, replaced after, the version moved."""
        t = self._t("membership")
        values = self._in({k: v for k, v in row.items() if k in t.c and k != "id"})
        if "score" in values and values["score"] is not None:
            values["score"] = str(values["score"])
        done = self.conn.execute(
            update(t)
            .where(
                t.c.screen_id == screen_id,
                t.c.list_id == list_id,
                t.c.key == row["key"],
                self._mine(t),
            )
            .values(**values, updated_at=now, version=t.c.version + 1)
        ).rowcount
        if not done:
            self.conn.execute(
                insert(t).values(
                    principal=self.who,
                    screen_id=screen_id,
                    list_id=list_id,
                    updated_at=now,
                    **values,
                )
            )
        found = self.conn.execute(
            select(t).where(
                t.c.screen_id == screen_id,
                t.c.list_id == list_id,
                t.c.key == row["key"],
                self._mine(t),
            )
        ).mappings()
        return self._out(found.first())  # type: ignore[return-value]

    def membership_change(
        self, member_id: int, version: int, values: Mapping[str, Any], now: int
    ) -> dict[str, Any]:
        """An owner's pin, unpin, dismiss or restore, only from the version he saw."""
        t = self._t("membership")
        done = self.conn.execute(
            update(t)
            .where(t.c.id == member_id, self._mine(t), t.c.version == version)
            .values(**self._in(values), updated_at=now, version=version + 1)
        ).rowcount
        if not done:
            if self.membership(member_id) is None:
                raise NotFound()
            raise VersionConflict()
        return self.membership(member_id)  # type: ignore[return-value]

    def membership_relist(self, screen_id: int, old_list: int, new_list: int, now: int) -> int:
        """Move a screen's rows from one list to another (the list the screen asked for was just
        made: rows kept under 0 until then)."""
        t = self._t("membership")
        return int(
            self.conn.execute(
                update(t)
                .where(t.c.screen_id == screen_id, t.c.list_id == old_list, self._mine(t))
                .values(list_id=new_list, updated_at=now)
            ).rowcount
        )

    def membership_remove(self, member_id: int) -> bool:
        t = self._t("membership")
        return bool(self.conn.execute(delete(t).where(t.c.id == member_id, self._mine(t))).rowcount)

    # ---- the list as last seen (shared: OpenAlgo's lists carry no principal here) ----
    def list_items(self, list_id: int) -> dict[str, dict[str, Any]]:
        t = self._t("list_item")
        found = self.conn.execute(select(t).where(t.c.list_id == list_id)).mappings()
        return {row["key"]: self._out(row) for row in found}  # type: ignore[misc]

    def list_items_replace(self, list_id: int, items: Iterable[Mapping[str, Any]], now: int) -> int:
        t = self._t("list_item")
        self.conn.execute(delete(t).where(t.c.list_id == list_id))
        rows = [{"list_id": list_id, "seen_at": now, **self._in(item)} for item in items]
        if rows:
            self.conn.execute(insert(t), rows)
        return len(rows)

    # ---- alert marks ----
    def told(self, screen_id: int, session: int) -> dict[str, dict[str, Any]]:
        """What the owner has been told this session about a screen, by key: ``told`` is how
        many times, ``last_run`` the run number (k) that told it last."""
        t = self._t("alert_state")
        found = self.conn.execute(
            select(t).where(t.c.screen_id == screen_id, t.c.session == session, self._mine(t))
        ).mappings()
        return {row["key"]: self._out(row) for row in found}  # type: ignore[misc]

    def mark_told(
        self, screen_id: int, session: int, told: Mapping[str, tuple[int, int | None]]
    ) -> int:
        """Write the digest's marks: key -> (count, last run number)."""
        t = self._t("alert_state")
        for key, (count, last) in told.items():
            done = self.conn.execute(
                update(t)
                .where(
                    t.c.screen_id == screen_id,
                    t.c.session == session,
                    t.c.key == key,
                    self._mine(t),
                )
                .values(told=count, last_run=last)
            ).rowcount
            if not done:
                self.conn.execute(
                    insert(t).values(
                        principal=self.who,
                        screen_id=screen_id,
                        session=session,
                        key=key,
                        told=count,
                        last_run=last,
                    )
                )
        return len(told)

    # ---- the outbox ----
    def outbox_add(
        self, kind: str, ref: str, payload: Any, now: int, *, coalesce: bool = False
    ) -> dict[str, Any]:
        """Queue a message. ``coalesce`` replaces a pending row of the same kind and ref (a list
        sync: only the newest desired set matters). Past the bound the oldest pending pushes go."""
        if kind not in OUTBOX_KINDS:
            raise ValueError(f"not an outbox kind: {kind!r}")
        t = self._t("outbox")
        if coalesce:
            self.conn.execute(
                delete(t).where(
                    t.c.kind == kind, t.c.ref == ref, t.c.status == "pending", self._mine(t)
                )
            )
        key = self.conn.execute(
            insert(t).values(
                principal=self.who,
                kind=kind,
                ref=ref,
                status="pending",
                next_at=now,
                created_at=now,
                updated_at=now,
                **self._in({"payload": payload}),
            )
        ).inserted_primary_key[0]
        over = self.outbox_count("pending") - OUTBOX_MAX_PENDING
        if over > 0:
            old = self.conn.execute(
                select(t.c.id)
                .where(t.c.kind == "push", t.c.status == "pending", self._mine(t))
                .order_by(t.c.id)
                .limit(over)
            ).scalars()
            ids = list(old)
            if ids:
                self.conn.execute(delete(t).where(t.c.id.in_(ids)))
        row = self.conn.execute(select(t).where(t.c.id == key)).mappings().first()
        return self._out(row)  # type: ignore[return-value]

    def outbox_due(self, now: int, limit: int = 50) -> list[dict[str, Any]]:
        t = self._t("outbox")
        query = (
            select(t)
            .where(t.c.status == "pending", t.c.next_at <= now, self._mine(t))
            .order_by(t.c.next_at, t.c.id)
            .limit(limit)
        )
        return self._all(query)

    def outbox_settle(self, row_id: int, status: str, now: int, next_at: int | None = None) -> None:
        """Done, failed, or pending again at ``next_at`` with one more attempt counted."""
        if status not in OUTBOX_STATUSES:
            raise ValueError(f"not an outbox status: {status!r}")
        t = self._t("outbox")
        self.conn.execute(
            update(t)
            .where(t.c.id == row_id, self._mine(t))
            .values(
                status=status,
                attempts=t.c.attempts + 1,
                next_at=next_at if next_at is not None else now,
                updated_at=now,
            )
        )

    def outbox_count(self, status: str | None = None) -> int:
        t = self._t("outbox")
        query = select(func.count()).select_from(t).where(self._mine(t))
        if status is not None:
            query = query.where(t.c.status == status)
        return int(self.conn.execute(query).scalar() or 0)

    # ---- shared: universes ----
    def universe_replace(
        self, name: str, kind: str, members: Iterable[Mapping[str, Any]], now: int, note: str = ""
    ) -> int:
        u, m = self._t("universe"), self._t("universe_member")
        self.conn.execute(delete(m).where(m.c.universe == name))
        self.conn.execute(delete(u).where(u.c.name == name))
        self.conn.execute(insert(u).values(name=name, kind=kind, built_at=now, note=note))
        rows = [{"universe": name, **self._in(member)} for member in members]
        if rows:
            self.conn.execute(insert(m), rows)
        return len(rows)

    def universes(self) -> list[dict[str, Any]]:
        u = self._t("universe")
        return self._all(select(u).order_by(u.c.name))

    def universe_members(self, name: str) -> list[dict[str, Any]]:
        m = self._t("universe_member")
        return self._all(select(m).where(m.c.universe == name).order_by(m.c.key))

    # ---- shared: today's bars ----
    def bars_put(self, rows: Iterable[Mapping[str, Any]]) -> int:
        """Sealed 1-minute bars; a bar already there (a repair) is replaced."""
        t = self._t("bar_today")
        count = 0
        for row in rows:
            done = self.conn.execute(
                update(t)
                .where(t.c.key == row["key"], t.c.start == row["start"])
                .values(**{k: v for k, v in row.items() if k not in ("key", "start")})
            ).rowcount
            if not done:
                self.conn.execute(insert(t).values(**row))
            count += 1
        return count

    def bars(self, key: str | None = None) -> list[dict[str, Any]]:
        t = self._t("bar_today")
        query = select(t)
        if key is not None:
            query = query.where(t.c.key == key)
        return self._all(query.order_by(t.c.key, t.c.start))

    def bars_clear(self) -> int:
        return int(self.conn.execute(delete(self._t("bar_today"))).rowcount)

    # ---- shared: coverage, daily stores, pauses ----
    def coverage_put(self, row: Mapping[str, Any]) -> None:
        t = self._t("hist_coverage")
        self.conn.execute(delete(t).where(t.c.key == row["key"], t.c.tf == row["tf"]))
        self.conn.execute(insert(t).values(**row))

    def coverage(self, key: str | None = None) -> list[dict[str, Any]]:
        t = self._t("hist_coverage")
        query = select(t)
        if key is not None:
            query = query.where(t.c.key == key)
        return self._all(query.order_by(t.c.key, t.c.tf))

    def daily_put(self, table: str, rows: Iterable[Mapping[str, Any]]) -> int:
        """``oi_daily`` or ``iv_daily`` rows; a day already there is replaced."""
        if table not in ("oi_daily", "iv_daily"):
            raise ValueError(f"not a daily store: {table!r}")
        t = self._t(table)
        first = "key" if table == "oi_daily" else "underlying"
        count = 0
        for row in rows:
            self.conn.execute(
                delete(t).where(t.c[first] == row[first], t.c.session == row["session"])
            )
            self.conn.execute(insert(t).values(**row))
            count += 1
        return count

    def daily(self, table: str, name: str, limit: int = 400) -> list[dict[str, Any]]:
        """A key's (or underlying's) days, newest first."""
        if table not in ("oi_daily", "iv_daily"):
            raise ValueError(f"not a daily store: {table!r}")
        t = self._t(table)
        first = "key" if table == "oi_daily" else "underlying"
        query = select(t).where(t.c[first] == name).order_by(t.c.session.desc()).limit(limit)
        return self._all(query)

    def pause_open(self, session: int, start: int, why: str) -> int:
        t = self._t("pause")
        return self.conn.execute(
            insert(t).values(session=session, start=start, why=why)
        ).inserted_primary_key[0]

    def pause_close(self, pause_id: int, end: int) -> None:
        t = self._t("pause")
        self.conn.execute(update(t).where(t.c.id == pause_id).values(end=end))

    def pauses(self, session: int) -> list[dict[str, Any]]:
        t = self._t("pause")
        return self._all(select(t).where(t.c.session == session).order_by(t.c.start))

    # ---- shared: meta and the owner's settings ----
    def meta_get(self, name: str) -> str | None:
        t = self._t("meta")
        return self.conn.execute(select(t.c.value).where(t.c.name == name)).scalar()

    def meta_set(self, name: str, value: str, now: int) -> None:
        t = self._t("meta")
        done = self.conn.execute(
            update(t).where(t.c.name == name).values(value=value, updated_at=now)
        ).rowcount
        if not done:
            self.conn.execute(insert(t).values(name=name, value=value, updated_at=now))

    def setting_get(self, name: str) -> str | None:
        t = self._t("settings")
        return self.conn.execute(select(t.c.value).where(t.c.name == name, self._mine(t))).scalar()

    def setting_set(self, name: str, value: str, now: int) -> None:
        t = self._t("settings")
        done = self.conn.execute(
            update(t).where(t.c.name == name, self._mine(t)).values(value=value, updated_at=now)
        ).rowcount
        if not done:
            self.conn.execute(
                insert(t).values(principal=self.who, name=name, value=value, updated_at=now)
            )

    # ---- retention ----
    def prune(self, now: int, keep_run_days: int) -> dict[str, int]:
        """Runs and their changes past ``keep_run_days`` (never under 90), run values past 7
        days, done or failed outbox rows past 2 days. Screens, membership and the daily stores
        are never pruned."""
        days = max(keep_run_days, KEEP_RUN_DAYS_MIN)
        run, change, value, outbox = (
            self._t("run"),
            self._t("run_change"),
            self._t("run_value"),
            self._t("outbox"),
        )
        old_runs = select(run.c.id).where(run.c.ran_at < now - days * DAY_MS, self._mine(run))
        counts = {
            "run_change": self.conn.execute(
                delete(change).where(change.c.run_id.in_(old_runs), self._mine(change))
            ).rowcount
        }
        counts["run_value"] = self.conn.execute(
            delete(value).where(
                value.c.run_id.in_(
                    select(run.c.id).where(
                        run.c.ran_at < now - KEEP_VALUE_DAYS * DAY_MS, self._mine(run)
                    )
                ),
                self._mine(value),
            )
        ).rowcount
        counts["run"] = self.conn.execute(
            delete(run).where(run.c.ran_at < now - days * DAY_MS, self._mine(run))
        ).rowcount
        counts["outbox"] = self.conn.execute(
            delete(outbox).where(
                outbox.c.status != "pending",
                outbox.c.updated_at < now - KEEP_OUTBOX_DAYS * DAY_MS,
                self._mine(outbox),
            )
        ).rowcount
        return counts

    def user_version(self) -> int:
        return int(self.conn.exec_driver_sql("PRAGMA user_version").scalar() or 0)


# ---- the store -----------------------------------------------------------------------------
class ScreenerStore:
    """screener.db, open for the service: migrated to this code's version at open."""

    def __init__(self, path: Path | str, principals: Sequence[str] = (OWNER,)):
        self.path = Path(path)
        self.principals = frozenset(principals)
        self.engine: Engine | None = None
        try:
            _private_file(self.path)
            self.engine = engine(self.path)
            self.writer = writer(self.engine)
            self.applied = _on_writer(lambda: migrations.migrate(self.engine))
            meta = MetaData()
            meta.reflect(bind=self.engine)
            self._tables = dict(meta.tables)
        except (OSError, SQLAlchemyError) as exc:
            self.close()
            log.error("the screener's store could not be opened: %s", _cause(exc))
            raise StoreError(WORDS["opened"], _cause(exc)) from exc
        except BaseException:  # SchemaTooNew, or a step's own failure: nothing is kept open
            self.close()
            raise

    def close(self) -> None:
        if self.engine is not None:
            self.engine.dispose()

    def _who(self, who: str) -> str:
        if who not in self.principals:
            raise UnknownPrincipal(who)
        return who

    def read[T](self, who: str, fn: Callable[[Tx], T]) -> T:
        """``fn(tx)`` in a read transaction on the store thread."""
        who = self._who(who)

        def run() -> T:
            try:
                with self.engine.connect() as conn:  # type: ignore[union-attr]
                    return fn(Tx(conn, self._tables, who))
            except (OSError, SQLAlchemyError) as exc:
                log.error("a screener read failed: %s", _cause(exc))
                raise StoreError(WORDS["read"], _cause(exc)) from exc

        return _on_writer(run)

    def write[T](self, who: str, fn: Callable[[Tx], T]) -> T:
        """``fn(tx)`` in one IMMEDIATE transaction on the store thread: all of it, or none."""
        who = self._who(who)
        return _on_writer(lambda: self._write_here(who, fn))

    def _write_here[T](self, who: str, fn: Callable[[Tx], T]) -> T:
        try:
            with self.writer.begin() as conn:
                return fn(Tx(conn, self._tables, who))
        except (OSError, SQLAlchemyError) as exc:
            log.error("a screener write failed: %s", _cause(exc))
            raise StoreError(WORDS["written"], _cause(exc)) from exc

    def counts(self, who: str = OWNER) -> dict[str, int]:
        """What /health shows: counts only."""
        return self.read(
            who,
            lambda tx: {
                "screens": tx.screen_count(),
                "active": tx.screen_count("active"),
                "outbox_pending": tx.outbox_count("pending"),
            },
        )


def clock_ms() -> int:
    return int(time.time() * 1000)


__all__ = [
    "DB_FILENAME",
    "OWNER",
    "NotFound",
    "SchemaTooNew",
    "ScreenerStore",
    "StoreError",
    "Tx",
    "UnknownPrincipal",
    "VersionConflict",
    "default_path",
    "open_read_only",
    "stop",
    "tables_of",
]
