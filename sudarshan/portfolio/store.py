"""portfolio.db, the owner's portfolio record (M7.19): members, accounts, tabs, assets, entries.

``$STATE_DIRECTORY/portfolio.db`` (directory 700, file 600, made before SQLite can touch them). The
engine is OpenAlgo's ``database.engine_factory.create_db_engine`` (NullPool: a connection per
transaction, closed at once); WAL and synchronous=NORMAL come from the listener in
``database/__init__.py``. This engine also emits its own BEGIN, so a migration's DDL and every
write are whole transactions, and a write begins IMMEDIATE: it holds the write lock from its
first read, so two writers queue instead of one failing with "database is locked" on a snapshot
the other's commit made stale (cross-design X7).

One writer. Every ``read`` and ``write`` runs on the process's one store thread, a module-level
single-thread executor (``stop`` shuts it down); a call made from that thread runs in place. A
caller that is not the service (a replay check) opens the file read-only (``open_read_only``).

The shape: ``PortfolioStore.read(scope, fn)`` and ``write(scope, fn)`` hand ``fn`` a ``Tx`` bound
to one principal; ``book.py`` composes ledger-checked writes from its methods, so a check and the
write it guards share one transaction. ``Tx`` filters every query by principal (and by the scope's
tabs); a structural test holds each method to that.

Versions. An owner write names the version of the row (or, for entries, of the tab) it was decided
on; a mismatch raises ``VersionConflict`` and nothing is written. Failures reach the owner as
``StoreError`` sentences; the system's own words are in ``.cause`` and the journal only.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from sqlalchemy import MetaData, Table, and_, delete, event, func, insert, or_, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from database.engine_factory import create_db_engine
from sudarshan.portfolio import migrations
from sudarshan.portfolio.core.kinds import WORDS as SHARED_WORDS

log = logging.getLogger("sudarshan.portfolio")

DB_FILENAME = "portfolio.db"
STATE_DIR = Path("/var/lib/sudarshan-portfolio")
THREAD_PREFIX = "portfolio-store"

#: Columns that hold JSON text, and columns that are flags; the store converts at the boundary.
JSON_COLUMNS = frozenset({"tags", "charges_detail", "spec", "preview", "funds", "payload"})
FLAG_COLUMNS = frozenset(
    {
        "system",
        "pinned",
        "archived",
        "is_default",
        "late",
        "show_pnl",
        "show_qty",
        "show_funds",
        "show_orders",
    }
)
#: Never leave the store: who owns a row, and the client's retry key.
HIDDEN_COLUMNS = ("principal", "request_id")
#: Tables whose rows carry a ``version`` the owner's write must name.
VERSIONED = frozenset(
    {"member", "account", "tab", "asset", "tab_layout", "saved_view", "tab_share", "member_privacy"}
)

WORDS = {
    "opened": SHARED_WORDS["store_failed"],
    "newer": SHARED_WORDS["store_newer"],
    "written": (
        "This could not be saved. Nothing was changed; try again in a minute. If it keeps "
        "failing, check that the server has free disk space."
    ),
    "read": (
        "Your portfolio could not be read just now. Try again in a minute; if it keeps failing, "
        "check the service on the server."
    ),
    "gone": "This item is no longer there; it may have been deleted on another screen.",
}


class StoreError(RuntimeError):
    """The record refused; ``str()`` is a sentence for the owner, ``cause`` the system's own
    words for the journal (never shown: "database is locked" reads as the owner's fault)."""

    def __init__(self, message: str, cause: str = ""):
        super().__init__(message)
        self.cause = cause


class NotFound(StoreError):
    def __init__(self, message: str = WORDS["gone"]) -> None:
        super().__init__(message)


class VersionConflict(StoreError):
    """Nothing was written: the row or tab is not at the version the change was decided on."""

    def __init__(self, word: str = "version_conflict") -> None:
        super().__init__(SHARED_WORDS[word])


class Invalid(StoreError):
    """The request is not acceptable as sent; the sentence says what to change."""


class Refused(StoreError):
    """The ledger or a rule says no. ``entry_id`` names the entry in the way (or None)."""

    def __init__(self, message: str, *, entry_id: int | None = None, reason: str = "", **facts):
        super().__init__(message)
        self.entry_id, self.reason, self.facts = entry_id, reason, facts


class UnknownPrincipal(ValueError):
    """A caller asked for someone this store does not serve (a programming error)."""


@dataclass(frozen=True)
class Scope:
    """Whose rows a read may see (6.5): the principal, the tabs (None: all) and the fields masked
    out of what leaves the service (applied in serialize.py, not here). v1 builds only owner."""

    principal: str
    tab_ids: frozenset[int] | None = None
    mask: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def owner(cls) -> Scope:
        return cls("owner")


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
    base = os.environ.get("SUDARSHAN_PORTFOLIO_STATE_DIR") or os.environ.get("STATE_DIRECTORY")
    return Path(base or STATE_DIR) / DB_FILENAME


def _cause(exc: BaseException) -> str:
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
    """portfolio.db's engine: NullPool from the factory, and a real BEGIN for every transaction,
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
    """The two read-only ways to open a WAL database, the one that fits its state first: with a
    ``-wal`` file present ``mode=ro`` sees the newest data; without one the main file is
    complete and ``immutable=1`` needs no side files."""
    target = quote(str(path))
    ro = f"sqlite:///file:{target}?mode=ro&uri=true"
    immutable = f"sqlite:///file:{target}?immutable=1&uri=true"
    return [ro, immutable] if Path(f"{path}-wal").exists() else [immutable, ro]


def open_read_only(path: Path) -> Engine:
    """An engine that cannot write, for ``--status`` and replay checks; never migrates."""
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
    raise StoreError(WORDS["opened"], _cause(last)) from last  # type: ignore[arg-type]


# ---- one transaction's queries -------------------------------------------------------------
class Tx:
    """The queries of one transaction for one principal. Every statement filters by
    ``principal`` (``_mine``); a scope with tabs also limits tabs and entries to them."""

    def __init__(self, conn: Connection, tables: Mapping[str, Table], scope: Scope):
        self.conn, self._tables, self.scope, self.who = conn, tables, scope, scope.principal

    def _t(self, name: str) -> Table:
        return self._tables[name]

    def _mine(self, table: Table):
        clause = table.c.principal == self.who
        ids = self.scope.tab_ids
        if ids is not None and table.name in ("tab", "activity"):
            clause = and_(clause, (table.c.id if table.name == "tab" else table.c.tab_id).in_(ids))
        return clause

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
            out[key] = value
        return out

    @staticmethod
    def _in(values: Mapping[str, Any]) -> dict[str, Any]:
        return {
            k: (json.dumps(v, sort_keys=True) if k in JSON_COLUMNS and v is not None else v)
            for k, v in values.items()
        }

    def get(self, table: str, row_id: int) -> dict[str, Any] | None:
        t = self._t(table)
        row = self.conn.execute(select(t).where(t.c.id == row_id, self._mine(t))).mappings().first()
        return self._out(row)

    def rows(self, table: str, order: str = "id", **equal: Any) -> list[dict[str, Any]]:
        t = self._t(table)
        query = select(t).where(self._mine(t), *(t.c[k] == v for k, v in equal.items()))
        found = self.conn.execute(query.order_by(*(t.c[c] for c in order.split(",")))).mappings()
        return [self._out(row) for row in found]

    def count(self, table: str, **equal: Any) -> int:
        t = self._t(table)
        query = select(func.count()).select_from(t).where(self._mine(t))
        for key, value in equal.items():
            query = query.where(t.c[key] == value)
        return int(self.conn.execute(query).scalar() or 0)

    def by_request(self, table: str, request_id: str) -> dict[str, Any] | None:
        """The row an earlier attempt of the same create made, if any."""
        t = self._t(table)
        row = (
            self.conn.execute(select(t).where(t.c.request_id == request_id, self._mine(t)))
            .mappings()
            .first()
        )
        return self._out(row)

    def insert(
        self, table: str, values: Mapping[str, Any], now: int, request_id: str | None = None
    ) -> dict[str, Any]:
        t = self._t(table)
        row = {**self._in(values), "principal": self.who, "created_at": now, "updated_at": now}
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
        word: str = "version_conflict_item",
    ) -> dict[str, Any]:
        """Update a row only from ``version`` (None: a table without one); NotFound or
        VersionConflict, writing nothing."""
        t = self._t(table)
        where = [t.c.id == row_id, self._mine(t)]
        extra: dict[str, Any] = {}
        if table in VERSIONED:
            where.append(t.c.version == version)
            extra["version"] = (version or 0) + 1
        done = self.conn.execute(
            update(t).where(*where).values(**self._in(values), updated_at=now, **extra)
        ).rowcount
        if not done:
            if self.get(table, row_id) is None:
                raise NotFound()
            raise VersionConflict(word)
        return self.get(table, row_id)  # type: ignore[return-value]

    def remove(self, table: str, row_id: int) -> bool:
        t = self._t(table)
        return bool(self.conn.execute(delete(t).where(t.c.id == row_id, self._mine(t))).rowcount)

    # ---- tabs and entries ----
    def bump_tab(self, tab_id: int, version: int, now: int) -> dict[str, Any]:
        """Every entry write moves the tab's version, and only from the one it was decided on."""
        return self.change("tab", tab_id, version, {}, now, "version_conflict")

    def reposition(self, tab_id: int, position: int) -> None:
        """Set a tab's place in the strip without moving its version: a tab pushed aside by a
        move did not change, so a screen editing it is not told it did."""
        t = self._t("tab")
        self.conn.execute(
            update(t).where(t.c.id == tab_id, self._mine(t)).values(position=position)
        )

    def next_position(self) -> int:
        t = self._t("tab")
        top = self.conn.execute(select(func.max(t.c.position)).where(self._mine(t))).scalar()
        return (top or 0) + 1

    def next_seq(self, tab_id: int) -> int:
        t = self._t("activity")
        top = self.conn.execute(
            select(func.max(t.c.seq)).where(t.c.tab_id == tab_id, self._mine(t))
        ).scalar()
        return (top or 0) + 1

    def entries(self, tab_id: int) -> list[dict[str, Any]]:
        """Every entry of a tab, posted and draft, in the order they were made."""
        return self.rows("activity", "seq,id", tab_id=tab_id)

    def entries_page(
        self, tab_id: int, before: tuple[int, int, int] | None, limit: int, asset_id: int | None
    ) -> list[dict[str, Any]]:
        """Newest first: by date, then entry order; ``before`` is the last row of the page before."""
        t = self._t("activity")
        query = select(t).where(t.c.tab_id == tab_id, self._mine(t))
        if asset_id is not None:
            query = query.where(t.c.asset_id == asset_id)
        if before is not None:
            day, seq, row_id = before
            query = query.where(
                (t.c.trade_date < day)
                | ((t.c.trade_date == day) & (t.c.seq < seq))
                | ((t.c.trade_date == day) & (t.c.seq == seq) & (t.c.id < row_id))
            )
        query = query.order_by(t.c.trade_date.desc(), t.c.seq.desc(), t.c.id.desc()).limit(limit)
        return [self._out(row) for row in self.conn.execute(query).mappings()]

    def remove_tab(self, tab_id: int) -> None:
        """A tab and what hangs on it (entries, layout, saved views, import batches, the links
        to its loans; a loan's asset and balances stay, as every asset does)."""
        for name, column in (
            ("activity", "tab_id"),
            ("tab_layout", "tab_id"),
            ("saved_view", "tab_id"),
            ("import_batch", "tab_id"),
            ("tab_liability", "tab_id"),
            ("tab_share", "tab_id"),
        ):
            t = self._t(name)
            self.conn.execute(delete(t).where(t.c[column] == tab_id, self._mine(t)))
        self.remove("tab", tab_id)

    # ---- Upstox's reports (M7.6, M7.7, V3) ----
    def report_get(self, kind: str, fy: int, segment: str) -> dict[str, Any] | None:
        t = self._t("report_cache")
        row = (
            self.conn.execute(
                select(t).where(
                    t.c.kind == kind, t.c.fy == fy, t.c.segment == segment, self._mine(t)
                )
            )
            .mappings()
            .first()
        )
        return self._out(row)

    def report_list(self, kind: str) -> list[dict[str, Any]]:
        t = self._t("report_cache")
        found = self.conn.execute(
            select(t).where(t.c.kind == kind, self._mine(t)).order_by(t.c.fy, t.c.segment)
        ).mappings()
        return [self._out(row) for row in found]

    def report_put(
        self,
        kind: str,
        fy: int,
        segment: str,
        state: str,
        read_at: int | None,
        note: str | None,
        payload: Any,
        now: int,
    ) -> None:
        """The one record of a report; a later read replaces it whole."""
        t = self._t("report_cache")
        self.conn.execute(
            delete(t).where(t.c.kind == kind, t.c.fy == fy, t.c.segment == segment, self._mine(t))
        )
        self.conn.execute(
            insert(t).values(
                principal=self.who,
                kind=kind,
                fy=fy,
                segment=segment,
                state=state,
                read_at=read_at,
                note=note,
                payload=None if payload is None else json.dumps(payload, sort_keys=True),
                created_at=now,
                updated_at=now,
            )
        )

    # ---- a tab's loans (M7.19 V2) ----
    def liabilities_of(self, tab_id: int) -> list[int]:
        """The ids of the liability assets linked to a tab, oldest link first."""
        t = self._t("tab_liability")
        found = self.conn.execute(
            select(t.c.asset_id)
            .where(t.c.tab_id == tab_id, self._mine(t))
            .order_by(t.c.created_at, t.c.asset_id)
        )
        return [int(row[0]) for row in found]

    def tabs_of_liability(self, asset_id: int) -> list[int]:
        t = self._t("tab_liability")
        found = self.conn.execute(select(t.c.tab_id).where(t.c.asset_id == asset_id, self._mine(t)))
        return [int(row[0]) for row in found]

    def link_liability(self, tab_id: int, asset_id: int, now: int) -> bool:
        """Link a loan to a tab; False when it was linked already."""
        if tab_id in self.tabs_of_liability(asset_id):
            return False
        t = self._t("tab_liability")
        self.conn.execute(
            insert(t).values(
                tab_id=tab_id, asset_id=asset_id, principal=self.who, created_at=now, updated_at=now
            )
        )
        return True

    def unlink_liability(self, tab_id: int, asset_id: int) -> bool:
        t = self._t("tab_liability")
        return bool(
            self.conn.execute(
                delete(t).where(t.c.tab_id == tab_id, t.c.asset_id == asset_id, self._mine(t))
            ).rowcount
        )

    def price_points(self, asset_id: int) -> list[dict[str, Any]]:
        """The owner's own values of an asset, oldest day first."""
        return self.rows("price_point", "day", asset_id=asset_id)

    # ---- assets ----
    def aliases(self, asset_id: int) -> list[dict[str, Any]]:
        return self.rows("asset_alias", "id", asset_id=asset_id)

    def find_alias(self, kind: str, value: str) -> dict[str, Any] | None:
        t = self._t("asset_alias")
        row = (
            self.conn.execute(select(t).where(t.c.kind == kind, t.c.value == value, self._mine(t)))
            .mappings()
            .first()
        )
        return self._out(row)

    def add_alias(self, asset_id: int, kind: str, value: str, now: int) -> None:
        self.conn.execute(
            insert(self._t("asset_alias")).values(
                principal=self.who,
                asset_id=asset_id,
                kind=kind,
                value=value,
                created_at=now,
                updated_at=now,
            )
        )

    def set_price_point(self, asset_id: int, day: int, price_u: int, now: int) -> None:
        """The owner's own value of a manual asset on a day; one per day, the last one stands."""
        t = self._t("price_point")
        self.conn.execute(delete(t).where(t.c.asset_id == asset_id, t.c.day == day, self._mine(t)))
        self.conn.execute(
            insert(t).values(
                asset_id=asset_id,
                day=day,
                principal=self.who,
                price_u=price_u,
                created_at=now,
                updated_at=now,
            )
        )

    # ---- a tab's layout (keyed by the tab, not by an id) ----
    def layout_row(self, tab_id: int) -> dict[str, Any] | None:
        t = self._t("tab_layout")
        row = self.conn.execute(select(t).where(t.c.tab_id == tab_id, self._mine(t))).mappings()
        return self._out(row.first())

    def layout_save(self, tab_id: int, version: int, spec: Mapping[str, Any], now: int) -> None:
        """Write a layout only from the version it was decided on (0: none saved yet);
        VersionConflict otherwise."""
        t = self._t("tab_layout")
        if self.layout_row(tab_id) is None:
            if version != 0:
                raise VersionConflict("version_conflict_item")
            self.conn.execute(
                insert(t).values(
                    tab_id=tab_id, principal=self.who, created_at=now, updated_at=now, version=1,
                    **self._in({"spec": spec}),
                )
            )  # fmt: skip
            return
        done = self.conn.execute(
            update(t)
            .where(t.c.tab_id == tab_id, self._mine(t), t.c.version == version)
            .values(**self._in({"spec": spec}), updated_at=now, version=version + 1)
        ).rowcount
        if not done:
            raise VersionConflict("version_conflict_item")

    # ---- prices and the job log (readers; shared tables carry no principal) ----
    def index_closes(self) -> dict[str, list[tuple[int, int]]]:
        t = self._t("benchmark_close")
        out: dict[str, list[tuple[int, int]]] = {}
        for key, day, close in self.conn.execute(
            select(t.c.index_key, t.c.day, t.c.close_u).order_by(t.c.day)
        ):
            out.setdefault(key, []).append((day, close))
        return out

    def price_stamp(self) -> tuple:
        """Changes whenever a price a history reads does (counts and newest stamps)."""
        parts: list[Any] = []
        for table in ("price_point", "price_eod"):
            t = self._t(table)
            parts.append(
                tuple(
                    self.conn.execute(
                        select(func.count(), func.max(t.c.updated_at)).where(self._mine(t))
                    ).one()
                )
            )
        t = self._t("benchmark_close")
        parts.append(tuple(self.conn.execute(select(func.count(), func.max(t.c.day))).one()))
        return tuple(parts)

    def job_runs(self) -> list[dict[str, Any]]:
        t = self._t("job_run")
        found = self.conn.execute(select(t).order_by(t.c.day, t.c.at)).mappings()
        return [dict(row) for row in found]

    # ---- closing records, market data and the job log (writers; PB6, PB7) ----
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

    def price_eod_get(self, asset_id: int, day: int) -> dict[str, Any] | None:
        found = self.rows("price_eod", "day", asset_id=asset_id, day=day)
        return found[0] if found else None

    def price_eod_put(self, asset_id: int, day: int, values: Mapping[str, Any], now: int) -> None:
        """A closing record of an asset for a day (price_u, prev_close_u, basis, source, late,
        at); the day's earlier record, if any, is replaced."""
        t = self._t("price_eod")
        self.conn.execute(delete(t).where(t.c.asset_id == asset_id, t.c.day == day, self._mine(t)))
        self.conn.execute(
            insert(t).values(
                asset_id=asset_id,
                day=day,
                principal=self.who,
                created_at=now,
                updated_at=now,
                **{
                    k: values[k]
                    for k in ("price_u", "prev_close_u", "basis", "source", "late", "at")
                },
            )
        )

    def index_close_put(self, index_key: str, day: int, close_u: int, source: str) -> None:
        t = self._t("benchmark_close")
        self.conn.execute(delete(t).where(t.c.index_key == index_key, t.c.day == day))
        self.conn.execute(
            insert(t).values(index_key=index_key, day=day, close_u=close_u, source=source)
        )

    def index_close_get(self, index_key: str, day: int) -> dict[str, Any] | None:
        t = self._t("benchmark_close")
        row = self.conn.execute(
            select(t).where(t.c.index_key == index_key, t.c.day == day)
        ).mappings().first()  # fmt: skip
        return None if row is None else dict(row)

    def upstox_eod_put(
        self, day: int, values: Mapping[str, Any], holdings: list[Mapping[str, Any]], now: int
    ) -> None:
        """The Upstox account's closing record of a day and its holding lines, replaced whole."""
        head, lines = self._t("upstox_eod"), self._t("upstox_eod_holding")
        for t in (head, lines):
            self.conn.execute(delete(t).where(t.c.day == day, self._mine(t)))
        self.conn.execute(
            insert(head).values(
                day=day, principal=self.who, created_at=now, updated_at=now,
                **self._in({k: values.get(k) for k in (
                    "read_at", "holdings_value_paise", "holdings_cost_paise",
                    "positions_pnl_paise", "funds")}),
            )
        )  # fmt: skip
        for line in holdings:
            self.conn.execute(
                insert(lines).values(
                    day=day, principal=self.who, created_at=now, updated_at=now, **line
                )
            )

    def mf_put_many(self, schemes: list[Mapping[str, Any]], now: int) -> int:
        """Replace the NAV list: each scheme keeps the NAV it had as ``prev`` when a newer one
        comes. Returns how many schemes were written."""
        t = self._t("mf_scheme")
        have = {
            row["amfi_code"]: row
            for row in self.conn.execute(
                select(t.c.amfi_code, t.c.nav_u, t.c.nav_date, t.c.prev_nav_u, t.c.prev_nav_date)
            ).mappings()
        }
        rows = []
        for new in schemes:
            old = have.get(new["amfi_code"])
            prev_u, prev_date = new.get("prev_nav_u"), new.get("prev_nav_date")
            if old is not None and new["nav_date"] is not None:
                if old["nav_date"] is not None and old["nav_date"] < new["nav_date"]:
                    prev_u, prev_date = old["nav_u"], old["nav_date"]
                elif old["nav_date"] == new["nav_date"]:
                    prev_u, prev_date = old["prev_nav_u"], old["prev_nav_date"]
                elif old["nav_date"] is not None and old["nav_date"] > new["nav_date"]:
                    continue  # an older file never replaces a newer NAV
            rows.append(
                {**new, "prev_nav_u": prev_u, "prev_nav_date": prev_date, "fetched_at": now}
            )
        for start in range(0, len(rows), 500):
            batch = rows[start : start + 500]
            self.conn.execute(delete(t).where(t.c.amfi_code.in_([r["amfi_code"] for r in batch])))
            self.conn.execute(insert(t), batch)
        return len(rows)

    def mf_fetched_at(self) -> int | None:
        return self.conn.execute(select(func.max(self._t("mf_scheme").c.fetched_at))).scalar()

    def mf_get(self, amfi_code: str) -> dict[str, Any] | None:
        t = self._t("mf_scheme")
        row = self.conn.execute(select(t).where(t.c.amfi_code == amfi_code)).mappings().first()
        return None if row is None else dict(row)

    def mf_count(self) -> int:
        return int(
            self.conn.execute(select(func.count()).select_from(self._t("mf_scheme"))).scalar()
        )

    def mf_search(self, words: list[str], limit: int) -> list[dict[str, Any]]:
        """Schemes whose name, category, AMC or code holds every word, a code match first."""
        t = self._t("mf_scheme")
        query = select(t)
        for word in words:
            like = f"%{word.replace('%', '').replace('_', '')}%"
            query = query.where(
                or_(
                    t.c.name.ilike(like),
                    t.c.amfi_code == word,
                    t.c.amc.ilike(like),
                    t.c.category.ilike(like),
                    t.c.isin_growth == word.upper(),
                )
            )
        found = self.conn.execute(query.order_by(t.c.name).limit(limit)).mappings()
        return [dict(row) for row in found]

    def nav_day(self) -> int | None:
        return self.conn.execute(select(func.max(self._t("mf_scheme").c.nav_date))).scalar()

    def user_version(self) -> int:
        return int(self.conn.exec_driver_sql("PRAGMA user_version").scalar() or 0)


# ---- the store -----------------------------------------------------------------------------
class PortfolioStore:
    def __init__(
        self,
        path: Path | str,
        principals: tuple[str, ...] = ("owner",),
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
            self.seeded = _on_writer(lambda: self._seed(sorted(self.principals), stamp))
            meta = MetaData()
            meta.reflect(bind=self.engine)
            self._tables = dict(meta.tables)
        except (OSError, SQLAlchemyError) as exc:
            self.close()
            log.error("portfolio store could not be opened: %s", _cause(exc))
            raise StoreError(WORDS["opened"], _cause(exc)) from exc
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
                log.error("portfolio read failed: %s", _cause(exc))
                raise StoreError(WORDS["read"], _cause(exc)) from exc

        return _on_writer(run)

    def write[T](self, scope: Scope, fn: Callable[[Tx], T]) -> T:
        """``fn(tx)`` in one IMMEDIATE transaction on the store thread: all of it, or none."""
        scope = self._who(scope)
        return _on_writer(lambda: self._write_here(scope, fn))

    def _write_here[T](self, scope: Scope, fn: Callable[[Tx], T]) -> T:
        """The transaction itself, on the calling thread (``write`` and the concurrency tests)."""
        try:
            with self.writer.begin() as conn:
                return fn(Tx(conn, self._tables, scope))
        except (OSError, SQLAlchemyError) as exc:
            log.error("portfolio write failed: %s", _cause(exc))
            raise StoreError(WORDS["written"], _cause(exc)) from exc

    def counts(self, scope: Scope) -> dict[str, int]:
        return self.read(scope, lambda tx: {n: tx.count(n) for n in ("tab", "asset", "activity")})


def tables_of(conn: Connection) -> Iterator[str]:
    """The tables of an open file (the schema tests and ``--status``)."""
    for row in conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'table'"):
        if not row[0].startswith("sqlite_"):
            yield row[0]
