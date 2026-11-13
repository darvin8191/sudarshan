"""journal.db, the owner's trade journal and report archive (JB1, M14.1; design 4.1-4.3).

``$STATE_DIRECTORY/journal.db`` (directory 700, file 600, made before SQLite can touch them). The
engine is OpenAlgo's ``database.engine_factory.create_db_engine`` (NullPool: a connection per
transaction, closed at once); WAL and synchronous=NORMAL come from the listener in
``database/__init__.py``. This engine also emits its own BEGIN, so a migration's DDL and every
write are whole transactions, and a write begins IMMEDIATE: it holds the write lock from its
first read, so two writers queue instead of one failing on a snapshot the other's commit made
stale (cross-design X7). The engine is the portfolio store's, copied rather than imported (the
portfolio's package is its own lane's; a shared ``sudarshan/common/sqlite.py`` is a later chore).

One writer. Every ``read`` and ``write`` runs on the process's one store thread, a module-level
single-thread executor (``stop`` shuts it down); a call made from that thread runs in place. A
caller that is not the service (``migrate --status``, a check) opens the file read-only
(``open_read_only``).

The shape: ``JournalStore.read(scope, fn)`` and ``write(scope, fn)`` hand ``fn`` a ``Tx`` bound to
one principal. ``Tx`` filters every query by principal; a structural test holds each method to it.
The job log is the one table that belongs to no one.

Versions. A row the owner writes (a trip's own fields, its note, a tag, a playbook, the settings,
a report) names the version it was decided on; a mismatch raises ``VersionConflict`` and nothing
is written. Rows the service writes (fills, the pairing, excursions) never move a version, so the
owner's open sheet is never called stale by a fill. Failures reach the owner as ``StoreError``
sentences; the system's own words are in ``.cause`` and the log only.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, Table, delete, event, func, insert, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from database.engine_factory import create_db_engine
from sudarshan.alerts.owner import database_urls
from sudarshan.journal import migrations

log = logging.getLogger("sudarshan.journal")

DB_FILENAME = "journal.db"
STATE_DIR = Path("/var/lib/sudarshan-journal")
THREAD_PREFIX = "journal-store"
OWNER = "owner"

#: Columns that hold JSON (decoded at the boundary), and columns that are flags. ``checklist`` is
#: JSON too but leaves the store as the text it was saved as (the contract's ``checklist:string``).
JSON_COLUMNS = frozenset(
    {"mistakes", "screenshots", "rules", "positions_overnight", "rows", "delivery", "value"}
)
FLAG_COLUMNS = frozenset(
    {"archived", "system", "own_expiry_day", "index_expiry_day", "approximate"}
)
#: Never leave the store in a row: who owns it, the client's retry key, and a report's file
#: (its bytes leave only through ``Tx.report_file``).
HIDDEN_COLUMNS = ("principal", "request_id", "file_bytes")
#: Tables whose rows carry a ``version`` the owner's write must name.
VERSIONED = frozenset({"round_trip", "trade_note", "tag", "playbook", "settings", "report"})

WORDS = {
    "opened": (
        "Your trade journal could not be opened. Check that the server has free disk space; "
        "nothing was changed."
    ),
    "written": (
        "This could not be saved. Nothing was changed; try again in a minute. If it keeps "
        "failing, check that the server has free disk space."
    ),
    "read": (
        "Your trade journal could not be read just now. Try again in a minute; if it keeps "
        "failing, check the journal service on the server."
    ),
    "gone": "This item is no longer there; it may have been deleted on another screen.",
    "trip_gone": "This trade is not in the journal any more. Open the list again.",
    "version_conflict": (
        "This item was changed on another screen. Nothing was saved; open it again and repeat "
        "the change."
    ),
    "version_conflict_trip": (
        "This trade's notes were changed on another screen. Nothing was saved; open it again."
    ),
}


class StoreError(RuntimeError):
    """The record refused; ``str()`` is a sentence for the owner, ``cause`` the system's own
    words for the log (never shown: "database is locked" reads as the owner's fault)."""

    def __init__(self, message: str, cause: str = ""):
        super().__init__(message)
        self.cause = cause


class NotFound(StoreError):
    def __init__(self, message: str = WORDS["gone"]) -> None:
        super().__init__(message)


class VersionConflict(StoreError):
    """Nothing was written: the row is not at the version the change was decided on."""

    def __init__(self, word: str = "version_conflict") -> None:
        super().__init__(WORDS[word])


class Invalid(StoreError):
    """The request is not acceptable as sent; the sentence says what to change. ``field`` names
    the field the page should point at, when there is one."""

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


class Refused(StoreError):
    """A rule says no (a name that is taken); the sentence says why."""

    def __init__(self, message: str, field: str | None = None) -> None:
        super().__init__(message)
        self.field = field


class UnknownPrincipal(ValueError):
    """A caller asked for someone this store does not serve (a programming error)."""


@dataclass(frozen=True)
class Scope:
    """Whose rows a read may see (design 6.6): the principal, and the fields masked out of what
    leaves the service (applied in one place, ``serialize.py``, never here). v1 builds the owner's
    only; P6 adds a viewer principal and its mask."""

    principal: str
    mask: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def owner(cls) -> Scope:
        return cls(OWNER)


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


def on_writer[T](fn: Callable[[], T]) -> T:
    """``fn()`` on the store thread (in place when already there), its result or its exception."""
    if getattr(_here, "writer", False):
        return fn()
    return _executor().submit(fn).result()


# ---- files and engines ---------------------------------------------------------------------
def default_path() -> Path:
    """journal.db under the service's state directory (systemd's StateDirectory=, or the
    setting, else /var/lib/sudarshan-journal); a list of directories takes its first."""
    base = os.environ.get("SUDARSHAN_JOURNAL_STATE_DIR") or os.environ.get("STATE_DIRECTORY")
    return Path((base or str(STATE_DIR)).split(":")[0]) / DB_FILENAME


def cause_of(exc: BaseException | None) -> str:
    """The failure in words: the system's or SQLite's own message, never a class name."""
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
    """journal.db's engine: NullPool from the factory, and a real BEGIN for every transaction,
    IMMEDIATE on a connection with the ``migrations.IMMEDIATE`` option (``writer``)."""
    made = create_db_engine(f"sqlite:///{path}")

    @event.listens_for(made, "connect")
    def _no_driver_begin(dbapi_connection, record):
        dbapi_connection.isolation_level = None  # the "begin" listener below opens them instead

    @event.listens_for(made, "begin")
    def _begin(conn):
        immediate = conn.get_execution_options().get(migrations.IMMEDIATE)
        conn.exec_driver_sql("BEGIN IMMEDIATE" if immediate else "BEGIN")

    return made


def writer(made: Engine) -> Engine:
    """The same engine for writes: each transaction begins IMMEDIATE."""
    return made.execution_options(**{migrations.IMMEDIATE: True})


def open_read_only(path: Path) -> Engine:
    """An engine that cannot write, for ``--status`` and checks; never migrates. The two
    read-only ways to open a WAL file are tried, the one that fits its state first."""
    last: Exception | None = None
    for url in database_urls(path):
        made = create_db_engine(url)
        try:
            with made.connect() as conn:
                conn.exec_driver_sql("PRAGMA user_version").scalar()
            return made
        except SQLAlchemyError as exc:
            made.dispose()
            last = exc
    raise StoreError(WORDS["opened"], cause_of(last))


# ---- one transaction's queries -------------------------------------------------------------
def _decode(key: str, value: Any) -> Any:
    if key in JSON_COLUMNS and value is not None:
        return json.loads(value)
    if key in FLAG_COLUMNS:
        return bool(value)
    return value


class Tx:
    """The queries of one transaction for one principal. Every statement on an owner table
    filters by ``principal`` (``_mine``) or names it when it inserts (``self.who``)."""

    def __init__(self, conn: Connection, tables: Mapping[str, Table], scope: Scope):
        self.conn, self._tables, self.scope, self.who = conn, tables, scope, scope.principal

    def _t(self, name: str) -> Table:
        return self._tables[name]

    def _mine(self, table: Table):
        return table.c.principal == self.who

    @staticmethod
    def out(row: Mapping | None) -> dict[str, Any] | None:
        """A row as it leaves the store: hidden columns dropped, JSON decoded, flags as bool."""
        if row is None:
            return None
        return {k: _decode(k, v) for k, v in row.items() if k not in HIDDEN_COLUMNS}

    @staticmethod
    def encode(values: Mapping[str, Any]) -> dict[str, Any]:
        return {
            k: (json.dumps(v, sort_keys=True) if k in JSON_COLUMNS and v is not None else v)
            for k, v in values.items()
        }

    # ---- rows of any owner table ----
    def get(self, table: str, row_id: int) -> dict[str, Any] | None:
        t = self._t(table)
        row = self.conn.execute(select(t).where(t.c.id == row_id, self._mine(t))).mappings().first()
        return self.out(row)

    def rows(self, table: str, order: str = "id", **equal: Any) -> list[dict[str, Any]]:
        t = self._t(table)
        query = select(t).where(self._mine(t), *(t.c[k] == v for k, v in equal.items()))
        found = self.conn.execute(query.order_by(*(t.c[c] for c in order.split(",")))).mappings()
        return [self.out(row) for row in found]

    def count(self, table: str, **equal: Any) -> int:
        t = self._t(table)
        query = select(func.count()).select_from(t).where(self._mine(t))
        for key, value in equal.items():
            query = query.where(t.c[key] == value)
        return int(self.conn.execute(query).scalar() or 0)

    def max_of(self, table: str, column: str, **equal: Any) -> Any:
        """The largest value of ``column`` among the principal's rows (None when there are none)."""
        t = self._t(table)
        query = select(func.max(t.c[column])).where(
            self._mine(t), *(t.c[k] == v for k, v in equal.items())
        )
        return self.conn.execute(query).scalar()

    def by_request(self, table: str, request_id: str) -> dict[str, Any] | None:
        """The row an earlier attempt of the same create made, if any."""
        t = self._t(table)
        row = (
            self.conn.execute(select(t).where(t.c.request_id == request_id, self._mine(t)))
            .mappings()
            .first()
        )
        return self.out(row)

    def insert(
        self, table: str, values: Mapping[str, Any], now: int, request_id: str | None = None
    ) -> dict[str, Any]:
        t = self._t(table)
        row = {**self.encode(values), "principal": self.who, "created_at": now, "updated_at": now}
        if request_id is not None:
            row["request_id"] = request_id
        key = self.conn.execute(insert(t).values(**row)).inserted_primary_key
        return self.get(table, key[0])  # type: ignore[return-value]

    def change(
        self,
        table: str,
        row_id: int,
        version: int | None,
        values: Mapping[str, Any],
        now: int,
        word: str = "version_conflict",
    ) -> dict[str, Any]:
        """The owner's change of a row, only from ``version``; it moves the version by one.
        NotFound or VersionConflict, writing nothing."""
        t = self._t(table)
        where = [t.c.id == row_id, self._mine(t)]
        extra: dict[str, Any] = {}
        if table in VERSIONED:
            where.append(t.c.version == version)
            extra["version"] = (version or 0) + 1
        done = self.conn.execute(
            update(t).where(*where).values(**self.encode(values), updated_at=now, **extra)
        ).rowcount
        if not done:
            if self.get(table, row_id) is None:
                raise NotFound()
            raise VersionConflict(word)
        return self.get(table, row_id)  # type: ignore[return-value]

    def touch(self, table: str, row_id: int, values: Mapping[str, Any], now: int) -> bool:
        """The service's own change of a row: no version is asked for or moved. True if done."""
        t = self._t(table)
        return bool(
            self.conn.execute(
                update(t)
                .where(t.c.id == row_id, self._mine(t))
                .values(**self.encode(values), updated_at=now)
            ).rowcount
        )

    def remove(self, table: str, row_id: int) -> bool:
        t = self._t(table)
        return bool(self.conn.execute(delete(t).where(t.c.id == row_id, self._mine(t))).rowcount)

    # ---- the settings: one document per principal, one version ----
    def settings(self) -> dict[str, Any]:
        """The principal's report settings with their version (the seeded defaults filled in
        for any key a newer release added)."""
        t = self._t("settings")
        row = self.conn.execute(select(t).where(self._mine(t))).mappings().first()
        value = dict(migrations.DEFAULT_SETTINGS)
        if row is None:
            return {**value, "version": 0}
        value.update(json.loads(row["value"]))
        return {**value, "version": row["version"]}

    def settings_save(self, values: Mapping[str, Any], version: int, now: int) -> dict[str, Any]:
        """Replace the settings document, only from ``version``; VersionConflict otherwise."""
        t = self._t("settings")
        current = self.settings()
        if current["version"] != version:
            raise VersionConflict()
        document = {k: current[k] for k in migrations.DEFAULT_SETTINGS} | dict(values)
        text = json.dumps(document, sort_keys=True)
        if version == 0:
            self.conn.execute(
                insert(t).values(principal=self.who, value=text, created_at=now, updated_at=now)
            )
        else:
            self.conn.execute(
                update(t)
                .where(self._mine(t), t.c.version == version)
                .values(value=text, updated_at=now, version=version + 1)
            )
        return self.settings()

    # ---- the job log (shared: belongs to no one) ----
    def job_get(self, name: str, day: int) -> dict[str, Any] | None:
        t = self._t("job_run")
        row = (
            self.conn.execute(select(t).where(t.c.name == name, t.c.day == day)).mappings().first()
        )
        return None if row is None else dict(row)

    def job_put(self, name: str, day: int, status: str, detail: str | None, at: int) -> None:
        """The one record of a job on a day; a later run of the same day replaces it."""
        t = self._t("job_run")
        self.conn.execute(delete(t).where(t.c.name == name, t.c.day == day))
        self.conn.execute(insert(t).values(name=name, day=day, status=status, detail=detail, at=at))

    def job_runs(self, since_day: int = 0) -> list[dict[str, Any]]:
        t = self._t("job_run")
        found = self.conn.execute(
            select(t).where(t.c.day >= since_day).order_by(t.c.day, t.c.name)
        ).mappings()
        return [dict(row) for row in found]

    def job_forget(self, before_day: int) -> int:
        """Retention (design 4.2): job records older than ``before_day`` go."""
        t = self._t("job_run")
        return int(self.conn.execute(delete(t).where(t.c.day < before_day)).rowcount or 0)

    def user_version(self) -> int:
        return int(self.conn.exec_driver_sql("PRAGMA user_version").scalar() or 0)


# ---- the store -----------------------------------------------------------------------------
class JournalStore:
    """journal.db for the principals it was opened for. ``applied`` is the migration steps this
    opening ran, ``seeded`` what each principal's first start made."""

    def __init__(
        self,
        path: Path | str,
        principals: tuple[str, ...] = (OWNER,),
        now: int | None = None,
    ):
        self.path = Path(path)
        self.principals = frozenset(principals)
        self.engine: Engine | None = None
        try:
            _private_file(self.path)
            self.engine = engine(self.path)
            self.writer = writer(self.engine)
            self.applied = migrations.migrate(self.engine)
            stamp = int(time.time() * 1000) if now is None else now
            self.seeded = on_writer(lambda: self._seed(sorted(self.principals), stamp))
            meta = MetaData()
            meta.reflect(bind=self.engine)
            self._tables = dict(meta.tables)
        except (OSError, SQLAlchemyError) as exc:
            self.close()
            log.error("the journal's store could not be opened: %s", cause_of(exc))
            raise StoreError(WORDS["opened"], cause_of(exc)) from exc
        except BaseException:  # SchemaTooNew, or a step's own failure: nothing is kept open
            self.close()
            raise

    def _seed(self, who: list[str], now: int) -> dict[str, list[str]]:
        with self.writer.begin() as conn:
            return {p: migrations.seed(conn, p, now) for p in who}

    def close(self) -> None:
        if self.engine is not None:
            self.engine.dispose()

    def _who(self, scope: Scope) -> Scope:
        if scope.principal not in self.principals:
            raise UnknownPrincipal(scope.principal)
        return scope

    def read[T](self, scope: Scope, fn: Callable[[Tx], T]) -> T:
        """``fn(tx)`` in a read transaction on the store thread."""
        scope = self._who(scope)

        def run() -> T:
            try:
                with self.engine.connect() as conn:  # type: ignore[union-attr]
                    return fn(Tx(conn, self._tables, scope))
            except (OSError, SQLAlchemyError) as exc:
                log.error("a journal read failed: %s", cause_of(exc))
                raise StoreError(WORDS["read"], cause_of(exc)) from exc

        return on_writer(run)

    def write[T](self, scope: Scope, fn: Callable[[Tx], T]) -> T:
        """``fn(tx)`` in one IMMEDIATE transaction on the store thread: all of it, or none."""
        scope = self._who(scope)
        return on_writer(lambda: self._write_here(scope, fn))

    def _write_here[T](self, scope: Scope, fn: Callable[[Tx], T]) -> T:
        """The transaction itself, on the calling thread (``write`` and the concurrency tests)."""
        try:
            with self.writer.begin() as conn:
                return fn(Tx(conn, self._tables, scope))
        except (OSError, SQLAlchemyError) as exc:
            log.error("a journal write failed: %s", cause_of(exc))
            raise StoreError(WORDS["written"], cause_of(exc)) from exc

    def counts(self, scope: Scope) -> dict[str, int]:
        """Rows of the main tables (the health page and the tests)."""
        names = ("fill", "round_trip", "tag", "playbook", "report")
        return self.read(scope, lambda tx: {n: tx.count(n) for n in names})


def tables_of(conn: Connection) -> Iterator[str]:
    """The tables of an open file (the schema tests and ``--status``)."""
    for row in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'table'"):
        if not row[0].startswith("sqlite_"):
            yield row[0]


def chunks[T](items: Iterable[T], size: int = 500) -> Iterator[list[T]]:
    """``items`` in lists of at most ``size`` (SQLite's bound-parameter limit stays far off)."""
    batch: list[T] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch
