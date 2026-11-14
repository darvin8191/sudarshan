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
from sudarshan.journal.core import kinds as core_kinds

log = logging.getLogger("sudarshan.journal")

DB_FILENAME = "journal.db"
STATE_DIR = Path("/var/lib/sudarshan-journal")
THREAD_PREFIX = "journal-store"
OWNER = "owner"

#: Columns that hold JSON (decoded at the boundary), and columns that are flags. ``checklist`` is
#: JSON too but leaves the store as the text it was saved as (the contract's ``checklist:string``).
JSON_COLUMNS = frozenset(
    {
        "mistakes",
        "screenshots",
        "rules",
        "positions_overnight",
        "rows",
        "delivery",
        "value",
        "parts",
        "charges_actual_parts",
    }
)
FLAG_COLUMNS = frozenset(
    {
        "archived",
        "system",
        "own_expiry_day",
        "index_expiry_day",
        "approximate",
        "charges_modelled",
        "announced_partial",
    }
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

    def min_of(self, table: str, column: str, **equal: Any) -> Any:
        """The smallest value of ``column`` among the principal's rows (None when none)."""
        t = self._t(table)
        query = select(func.min(t.c[column])).where(
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

    # ---- fills: what Upstox and the sandbox reported (JB3') ----
    #: What a later read of the same trade may correct in its row (the instrument named once the
    #: master is in, the exchange's time once Upstox gives it); the rest is the trade itself.
    FILL_FIXABLE = ("exchange", "symbol", "instrument_key", "product", "exch_ts_ms", "order_ts_ms",
                    "day")  # fmt: skip

    def fills_upsert(self, fills: Iterable[Mapping[str, Any]], now: int) -> dict[str, int]:
        """Write each fill once, by (principal, mode, trade_id) (mutation target 22). A trade seen
        again keeps its first source and row, takes what ``FILL_FIXABLE`` corrects, and comes back
        from ``withdrawn`` if Upstox lists it again. Returns counts: new, fixed, back."""
        t = self._t("fill")
        counts = {"new": 0, "fixed": 0, "back": 0}
        for fill in fills:
            have = (
                self.conn.execute(
                    select(t).where(
                        self._mine(t), t.c.mode == fill["mode"], t.c.trade_id == fill["trade_id"]
                    )
                )
                .mappings()
                .first()
            )
            if have is None:
                values = {**fill, "principal": self.who, "created_at": now, "updated_at": now}
                self.conn.execute(insert(t).values(**values))
                counts["new"] += 1
                continue
            fixes = {k: fill[k] for k in self.FILL_FIXABLE if k in fill and fill[k] != have[k]}
            values = dict(fixes)
            if have["source"] == "withdrawn":
                values["source"] = fill["source"]
                counts["back"] += 1
            counts["fixed"] += 1 if fixes else 0
            if values:
                self.conn.execute(
                    update(t)
                    .where(t.c.id == have["id"], self._mine(t))
                    .values(**values, updated_at=now)
                )
        return counts

    def fills_of(self, mode: str, *, day: int | None = None, since_ms: int | None = None,
                 key: tuple[str, str] | None = None,
                 before_ms: int | None = None) -> list[dict[str, Any]]:  # fmt: skip
        """Fills of a mode, oldest first: of one day, from an instant on, before an instant, and
        of one instrument (exchange, symbol) when asked."""
        t = self._t("fill")
        query = select(t).where(self._mine(t), t.c.mode == mode)
        if day is not None:
            query = query.where(t.c.day == day)
        if since_ms is not None:
            query = query.where(t.c.exch_ts_ms >= since_ms)
        if before_ms is not None:
            query = query.where(t.c.exch_ts_ms < before_ms)
        if key is not None:
            query = query.where(t.c.exchange == key[0], t.c.symbol == key[1])
        found = self.conn.execute(query.order_by(t.c.exch_ts_ms, t.c.trade_id)).mappings()
        return [self.out(row) for row in found]

    def fills_of_orders(self, mode: str, order_ids: Iterable[str]) -> list[dict[str, Any]]:
        """Every fill of the named orders (withdrawn ones aside), oldest first: an order is
        priced whole, whichever trades hold its fills (review M1)."""
        t = self._t("fill")
        out: list[dict[str, Any]] = []
        for batch in chunks(sorted(set(order_ids))):
            query = select(t).where(
                self._mine(t), t.c.mode == mode, t.c.order_id.in_(batch),
                t.c.source != "withdrawn",
            )  # fmt: skip
            out += [self.out(r) for r in self.conn.execute(query).mappings()]
        return sorted(out, key=lambda f: (f["exch_ts_ms"], f["trade_id"]))

    def order_legs(self, mode: str, order_ids: Iterable[str]) -> list[dict[str, Any]]:
        """The legs of the named orders' fills in every trade that holds one: the trade's id and
        opening, the fill's order and day, the leg's quantity (a trade's share of an order)."""
        t, legs, fills = self._t("round_trip"), self._t("fill_leg"), self._t("fill")
        out: list[dict[str, Any]] = []
        for batch in chunks(sorted(set(order_ids))):
            query = (
                select(t.c.id.label("trip_id"), t.c.opened_ms, fills.c.order_id, fills.c.day,
                       legs.c.qty)
                .select_from(legs.join(fills, fills.c.id == legs.c.fill_id)
                             .join(t, t.c.id == legs.c.round_trip_id))
                .where(self._mine(legs), fills.c.principal == self.who, t.c.principal == self.who,
                       t.c.mode == mode, fills.c.order_id.in_(batch))
            )  # fmt: skip
            out += [dict(r) for r in self.conn.execute(query).mappings()]
        return out

    def fill_before(self, mode: str, exchange: str, symbol: str, product_class: str,
                    before_ms: int) -> bool:  # fmt: skip
        """Whether the journal holds a fill of the instrument in that product class before an
        instant (withdrawn ones aside): a window that starts later is not the key's first."""
        t = self._t("fill")
        products = kinds_products(product_class)
        row = self.conn.execute(
            select(t.c.id)
            .where(
                self._mine(t), t.c.mode == mode, t.c.exchange == exchange, t.c.symbol == symbol,
                t.c.exch_ts_ms < before_ms, t.c.source != "withdrawn", t.c.product.in_(products),
            )
            .limit(1)
        ).first()  # fmt: skip
        return row is not None

    def fills_withdraw(self, mode: str, day: int, listed: Iterable[str], now: int) -> int:
        """Mark the day's fills of ``mode`` that Upstox's whole list for the day no longer holds
        as withdrawn: kept, and left out of the pairing (design 4.2). Returns how many."""
        t = self._t("fill")
        keep = set(listed)
        gone = [
            row["id"]
            for row in self.conn.execute(
                select(t.c.id, t.c.trade_id).where(
                    self._mine(t), t.c.mode == mode, t.c.day == day, t.c.source != "withdrawn"
                )
            ).mappings()
            if row["trade_id"] not in keep
        ]
        for batch in chunks(gone):
            self.conn.execute(
                update(t)
                .where(self._mine(t), t.c.id.in_(batch))
                .values(source="withdrawn", updated_at=now)
            )
        return len(gone)

    # ---- order references: the label behind each order (JB3', M12.12) ----
    def order_refs(self, mode: str, order_ids: Iterable[str] | None = None,
                   surface: str | None = None) -> dict[str, dict]:  # fmt: skip
        """The order references of a mode, by order id (all of them, those named, or those of
        one surface: ``unknown`` lists the orders still waiting for their label)."""
        t = self._t("order_ref")
        query = select(t).where(self._mine(t), t.c.mode == mode)
        if surface is not None:
            query = query.where(t.c.surface == surface)
        found: dict[str, dict] = {}
        if order_ids is None:
            for row in self.conn.execute(query).mappings():
                found[row["order_id"]] = self.out(row)
            return found
        for batch in chunks(sorted(set(order_ids))):
            for row in self.conn.execute(query.where(t.c.order_id.in_(batch))).mappings():
                found[row["order_id"]] = self.out(row)
        return found

    def order_ref_note(self, mode: str, order_id: str, first_fill_ms: int, now: int) -> bool:
        """An order seen for the first time gets a reference, its label still unknown; an order
        already known keeps its row (its first fill moves earlier when an earlier one appears).
        True when the row was made."""
        t = self._t("order_ref")
        where = (self._mine(t), t.c.mode == mode, t.c.order_id == order_id)
        have = self.conn.execute(select(t.c.first_fill_ms).where(*where)).first()
        if have is None:
            self.conn.execute(
                insert(t).values(
                    principal=self.who, mode=mode, order_id=order_id, first_fill_ms=first_fill_ms,
                    surface="unknown", created_at=now, updated_at=now,
                )
            )  # fmt: skip
            return True
        if first_fill_ms < have[0]:
            self.conn.execute(
                update(t).where(*where).values(first_fill_ms=first_fill_ms, updated_at=now)
            )
        return False

    def order_ref_set(self, mode: str, order_id: str, values: Mapping[str, Any], now: int) -> bool:
        """The label of an order once it is known (strategy, surface, type, latency)."""
        t = self._t("order_ref")
        return bool(
            self.conn.execute(
                update(t)
                .where(self._mine(t), t.c.mode == mode, t.c.order_id == order_id)
                .values(**values, updated_at=now)
            ).rowcount
        )

    # ---- round trips, their legs and notes (JB4) ----
    def trips_of_key(
        self, mode: str, exchange: str, symbol: str, product_class: str, from_ms: int
    ) -> list[dict[str, Any]]:
        """The trips of one instrument and product class that reach ``from_ms`` or later: not
        ended (open, expired, an unmatched one still holding), orphaned, opened on or after it,
        or ended on or after it."""
        t = self._t("round_trip")
        query = select(t).where(
            self._mine(t),
            t.c.mode == mode,
            t.c.exchange == exchange,
            t.c.symbol == symbol,
            t.c.product_class == product_class,
            t.c.closed_ms.is_(None)
            | (t.c.status == "orphan")
            | (t.c.opened_ms >= from_ms)
            | (t.c.closed_ms >= from_ms),
        )
        return [self.out(r) for r in self.conn.execute(query.order_by(t.c.opened_ms)).mappings()]

    def left_out(self, mode: str, start_day: int, end_day: int) -> dict[str, int]:
        """What a period's figures leave out (review B1): ``unmatched``, the positions the
        journal did not see opened that have a fill in the period; ``expired``, the contracts
        that ended at expiry in it (an expired trip's ``close_day`` is its expiry day);
        ``unclosed``, the trades the journal did not see closed, counted again in the period
        (verification N2; ``pairing.unclosed``: ended holding more than its fills took off)."""
        t, legs, fills = self._t("round_trip"), self._t("fill_leg"), self._t("fill")
        cut = t.c.closed_ms.is_not(None) & (t.c.entry_qty > t.c.exit_qty)
        joined = t.join(legs, legs.c.round_trip_id == t.c.id).join(
            fills, fills.c.id == legs.c.fill_id
        )
        unmatched = self.conn.execute(
            select(func.count(func.distinct(t.c.id)))
            .select_from(joined)
            .where(
                self._mine(t),
                legs.c.principal == self.who,
                fills.c.principal == self.who,
                t.c.mode == mode,
                t.c.status == "unmatched",
                ~cut,
                fills.c.day >= start_day,
                fills.c.day <= end_day,
            )
        ).scalar()
        unclosed = self.conn.execute(
            select(func.count())
            .select_from(t)
            .where(
                self._mine(t),
                t.c.mode == mode,
                t.c.status == "unmatched",
                cut,
                t.c.close_day >= start_day,
                t.c.close_day <= end_day,
            )
        ).scalar()
        expired = self.conn.execute(
            select(func.count())
            .select_from(t)
            .where(
                self._mine(t),
                t.c.mode == mode,
                t.c.status == "expired",
                t.c.close_day >= start_day,
                t.c.close_day <= end_day,
            )
        ).scalar()
        return {"unmatched": int(unmatched or 0), "expired": int(expired or 0),
                "unclosed": int(unclosed or 0)}  # fmt: skip

    def trips_sold_in(self, mode: str, start_day: int, end_day: int) -> list[dict[str, Any]]:
        """The carry trades (open or closed) with an exit leg on a day from ``start_day`` to
        ``end_day``: the tax pack books each such sale in its own year (review H1)."""
        t, legs, fills = self._t("round_trip"), self._t("fill_leg"), self._t("fill")
        sold = (
            select(legs.c.round_trip_id)
            .select_from(legs.join(fills, fills.c.id == legs.c.fill_id))
            .where(
                self._mine(legs), fills.c.principal == self.who, legs.c.role == "exit",
                fills.c.day >= start_day, fills.c.day <= end_day,
            )
        )  # fmt: skip
        query = select(t).where(
            self._mine(t), t.c.mode == mode, t.c.product_class == "carry",
            t.c.status.in_(("open", "closed")), t.c.id.in_(sold),
        )  # fmt: skip
        return [self.out(r) for r in self.conn.execute(query.order_by(t.c.id)).mappings()]

    # ---- what was held before the journal's first fill of an instrument (review B1) ----
    def openings_of(self, mode: str) -> dict[tuple[str, str, str], dict[str, Any]]:
        """The kept openings of a mode by (exchange, symbol, product class)."""
        t = self._t("key_opening")
        found = self.conn.execute(select(t).where(self._mine(t), t.c.mode == mode)).mappings()
        return {(r["exchange"], r["symbol"], r["product_class"]): self.out(r) for r in found}

    def opening_put(self, mode: str, key: tuple[str, str, str], qty: int, day: int, source: str,
                    read_ms: int, now: int) -> bool:  # fmt: skip
        """Keep the opening of a key read at first sight; one already kept is never replaced
        (the first read is the one that saw the account before the journal). True if kept."""
        t = self._t("key_opening")
        exchange, symbol, pclass = key
        have = self.conn.execute(
            select(t.c.qty).where(
                self._mine(t), t.c.mode == mode, t.c.exchange == exchange, t.c.symbol == symbol,
                t.c.product_class == pclass,
            )
        ).first()  # fmt: skip
        if have is not None:
            return False
        self.conn.execute(
            insert(t).values(
                principal=self.who, mode=mode, exchange=exchange, symbol=symbol,
                product_class=pclass, qty=int(qty), day=int(day), source=source,
                read_ms=int(read_ms), created_at=now, updated_at=now,
            )
        )  # fmt: skip
        return True

    # ---- what the account held, counted again later (verification N2) ----
    def recounts_of(self, mode: str) -> dict[tuple[str, str, str], list[tuple[int, int]]]:
        """Each key's recounts as (day, quantity held at the start of that day), oldest first."""
        t = self._t("key_recount")
        found = self.conn.execute(
            select(t).where(self._mine(t), t.c.mode == mode).order_by(t.c.day)
        ).mappings()
        out: dict[tuple[str, str, str], list[tuple[int, int]]] = {}
        for r in found:
            key = (r["exchange"], r["symbol"], r["product_class"])
            out.setdefault(key, []).append((int(r["day"]), int(r["qty"])))
        return out

    def recount_put(self, mode: str, key: tuple[str, str, str], day: int, qty: int, source: str,
                    read_ms: int, now: int) -> bool:  # fmt: skip
        """Keep what the account held of a key at the start of ``day``; a later read of the same
        day replaces the quantity (a read that was wrong is put right). True if it changed."""
        t = self._t("key_recount")
        exchange, symbol, pclass = key
        mine = (
            self._mine(t), t.c.mode == mode, t.c.exchange == exchange, t.c.symbol == symbol,
            t.c.product_class == pclass, t.c.day == int(day),
        )  # fmt: skip
        have = self.conn.execute(select(t.c.qty).where(*mine)).first()
        if have is None:
            self.conn.execute(
                insert(t).values(
                    principal=self.who, mode=mode, exchange=exchange, symbol=symbol,
                    product_class=pclass, day=int(day), qty=int(qty), source=source,
                    read_ms=int(read_ms), created_at=now, updated_at=now,
                )
            )  # fmt: skip
            return True
        if int(have[0]) == int(qty):
            return False
        self.conn.execute(
            update(t)
            .where(*mine)
            .values(qty=int(qty), source=source, read_ms=int(read_ms), updated_at=now)  # fmt: skip
        )
        return True

    def last_fill_day(self, mode: str, exchange: str, symbol: str, product_class: str,
                      before_ms: int | None = None) -> int | None:  # fmt: skip
        """The day of the journal's latest fill of the instrument in that product class
        (withdrawn ones aside), before an instant when one is given; None when it holds none."""
        t = self._t("fill")
        query = select(func.max(t.c.day)).where(
            self._mine(t), t.c.mode == mode, t.c.exchange == exchange, t.c.symbol == symbol,
            t.c.source != "withdrawn", t.c.product.in_(kinds_products(product_class)),
        )  # fmt: skip
        if before_ms is not None:
            query = query.where(t.c.exch_ts_ms < before_ms)
        found = self.conn.execute(query).scalar()
        return None if found is None else int(found)

    def trip_insert(self, values: Mapping[str, Any], now: int) -> int:
        """A new trip the pairing made; returns its id."""
        t = self._t("round_trip")
        row = {**self.encode(values), "principal": self.who, "created_at": now, "updated_at": now}
        return int(self.conn.execute(insert(t).values(**row)).inserted_primary_key[0])

    def legs_replace(self, trip_id: int, legs: Iterable[tuple[int, str, int]], now: int) -> None:
        """A trip's legs as the pairing made them: (fill id, role, qty)."""
        t = self._t("fill_leg")
        self.conn.execute(delete(t).where(self._mine(t), t.c.round_trip_id == trip_id))
        rows = [
            {"fill_id": fill_id, "round_trip_id": trip_id, "principal": self.who, "role": role,
             "qty": qty, "created_at": now, "updated_at": now}
            for fill_id, role, qty in legs
        ]  # fmt: skip
        if rows:
            self.conn.execute(insert(t), rows)

    def legs_of(self, trip_id: int) -> list[dict[str, Any]]:
        """A trip's legs with their fills, in time order."""
        legs, fills = self._t("fill_leg"), self._t("fill")
        query = (
            select(legs.c.role.label("leg_role"), legs.c.qty.label("leg_qty"), fills)
            .join(fills, fills.c.id == legs.c.fill_id)
            .where(self._mine(legs), legs.c.round_trip_id == trip_id, fills.c.principal == self.who)
            .order_by(fills.c.exch_ts_ms, fills.c.trade_id)
        )
        return [self.out(row) for row in self.conn.execute(query).mappings()]

    def trip_forget(self, trip_id: int) -> None:
        """A trip the pairing no longer makes and nobody wrote on: it goes with its legs and
        its price path."""
        for name, column in (("fill_leg", "round_trip_id"), ("trip_track", "round_trip_id"),
                             ("trade_note", "round_trip_id")):  # fmt: skip
            t = self._t(name)
            self.conn.execute(delete(t).where(self._mine(t), t.c[column] == trip_id))
        self.remove("round_trip", trip_id)

    def note_of(self, trip_id: int) -> dict[str, Any] | None:
        t = self._t("trade_note")
        row = self.conn.execute(
            select(t).where(self._mine(t), t.c.round_trip_id == trip_id)
        ).mappings().first()  # fmt: skip
        return self.out(row)

    def notes_of(self, trip_ids: Iterable[int]) -> dict[int, dict[str, Any]]:
        t = self._t("trade_note")
        out: dict[int, dict[str, Any]] = {}
        for batch in chunks(sorted(set(trip_ids))):
            for row in self.conn.execute(
                select(t).where(self._mine(t), t.c.round_trip_id.in_(batch))
            ).mappings():
                out[row["round_trip_id"]] = self.out(row)
        return out

    def note_put(self, trip_id: int, values: Mapping[str, Any], now: int) -> None:
        """The owner's note on a trip, made or changed (the trip's own version guards it)."""
        t = self._t("trade_note")
        if self.note_of(trip_id) is None:
            row = {"round_trip_id": trip_id, **self.encode(values), "principal": self.who,
                   "created_at": now, "updated_at": now}  # fmt: skip
            self.conn.execute(insert(t).values(**row))
            return
        self.conn.execute(
            update(t)
            .where(self._mine(t), t.c.round_trip_id == trip_id)
            .values(**self.encode(values), updated_at=now, version=t.c.version + 1)
        )

    def trips_page(
        self, mode: str, *, status: str | None = None, before_ms: int | None = None,
        from_day: int | None = None, to_day: int | None = None, limit: int = 100,
        setup_id: int | None = None,
    ) -> list[dict[str, Any]]:  # fmt: skip
        """Trips newest first: open ones by their opening, closed ones by their close; ``before``
        an instant the page ends before. A day range is the close day of a closed trip and the
        opening day of an open one."""
        t = self._t("round_trip")
        moment = func.coalesce(t.c.closed_ms, t.c.opened_ms)
        day = func.coalesce(t.c.close_day, t.c.open_day)
        query = select(t).where(self._mine(t), t.c.mode == mode)
        if status is not None:
            query = query.where(t.c.status == status)
        if before_ms is not None:
            query = query.where(moment < before_ms)
        if from_day is not None:
            query = query.where(day >= from_day)
        if to_day is not None:
            query = query.where(day <= to_day)
        if setup_id is not None:
            query = query.where(t.c.setup_id == setup_id)
        query = query.order_by((t.c.status != "open"), moment.desc(), t.c.id.desc()).limit(limit)
        return [self.out(r) for r in self.conn.execute(query).mappings()]

    def fills_by_trade(self, mode: str, trade_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Fills of a mode by trade id."""
        t = self._t("fill")
        out: dict[str, dict[str, Any]] = {}
        for batch in chunks(sorted(set(trade_ids))):
            for row in self.conn.execute(
                select(t).where(self._mine(t), t.c.mode == mode, t.c.trade_id.in_(batch))
            ).mappings():
                out[row["trade_id"]] = self.out(row)
        return out

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

    # ---- a trip's tracked price span (JB6) ----
    def trip_track_get(self, trip_id: int) -> dict[str, Any] | None:
        t = self._t("trip_track")
        row = (
            self.conn.execute(select(t).where(self._mine(t), t.c.round_trip_id == trip_id))
            .mappings()
            .first()
        )
        return self.out(row)

    def trip_track_put(self, trip_id: int, values: Mapping[str, Any], now: int) -> None:
        """The tracked span of a trip: its first point, its low and high with their times."""
        t = self._t("trip_track")
        self.conn.execute(delete(t).where(self._mine(t), t.c.round_trip_id == trip_id))
        self.conn.execute(
            insert(t).values(
                **values, round_trip_id=trip_id, principal=self.who, created_at=now, updated_at=now
            )  # fmt: skip
        )

    # ---- a report's file (JB7): its bytes never leave through ``out`` ----
    def report_bytes(self, report_id: int) -> bytes | None:
        t = self._t("report")
        return self.conn.execute(
            select(t.c.file_bytes).where(t.c.id == report_id, self._mine(t))
        ).scalar()

    # ---- the day's close and Upstox's own P&L report (JB5) ----
    def day_close_get(self, mode: str, day: int) -> dict[str, Any] | None:
        t = self._t("day_close")
        row = (
            self.conn.execute(select(t).where(self._mine(t), t.c.mode == mode, t.c.day == day))
            .mappings()
            .first()
        )
        return self.out(row)

    def day_close_put(
        self, mode: str, day: int, values: Mapping[str, Any], now: int
    ) -> dict[str, Any]:
        """Write the day's close; a column not named keeps what an earlier write put."""
        t = self._t("day_close")
        if self.day_close_get(mode, day) is None:
            self.conn.execute(
                insert(t).values(
                    **self.encode(values),
                    principal=self.who,
                    mode=mode,
                    day=day,
                    created_at=now,
                    updated_at=now,
                )  # fmt: skip
            )
        else:
            self.conn.execute(
                update(t)
                .where(self._mine(t), t.c.mode == mode, t.c.day == day)
                .values(**self.encode(values), updated_at=now)
            )
        return self.day_close_get(mode, day)  # type: ignore[return-value]

    def upstox_pairs_put(self, rows: Iterable[Mapping[str, Any]], now: int) -> int:
        """Upstox's matched pairs, each once by its ``raw_hash``; returns how many were new."""
        t = self._t("upstox_pair")
        added = 0
        for row in rows:
            seen = self.conn.execute(
                select(t.c.id).where(self._mine(t), t.c.raw_hash == row["raw_hash"])
            ).first()
            if seen is None:
                self.conn.execute(
                    insert(t).values(**row, principal=self.who, created_at=now, updated_at=now)
                )
                added += 1
        return added

    def upstox_charges_put(self, row: Mapping[str, Any], now: int) -> None:
        """Upstox's charges for one segment and period; a later read of the period replaces it."""
        t = self._t("upstox_charges")
        where = (self._mine(t), t.c.segment == row["segment"],
                 t.c.period_from == row["period_from"], t.c.period_to == row["period_to"])  # fmt: skip
        self.conn.execute(delete(t).where(*where))
        row = {"basis": migrations.basis_of(row["period_from"], row["period_to"]), **row}
        self.conn.execute(
            insert(t).values(**row, principal=self.who, created_at=now, updated_at=now)
        )

    def upstox_charges_get(self, segment: str, period_from: int,
                           period_to: int) -> dict[str, Any] | None:  # fmt: skip
        t = self._t("upstox_charges")
        row = self.conn.execute(
            select(t).where(self._mine(t), t.c.segment == segment,
                            t.c.period_from == period_from, t.c.period_to == period_to)
        ).mappings().first()  # fmt: skip
        return self.out(row)

    # ---- Upstox's actual charges, order by order (M14.2) ----
    def fill_days(self, mode: str) -> list[tuple[int, str]]:
        """Each (day, exchange) the mode holds a fill of, withdrawn ones aside, oldest first."""
        t = self._t("fill")
        query = (
            select(t.c.day, t.c.exchange)
            .where(self._mine(t), t.c.mode == mode, t.c.source != "withdrawn")
            .distinct()
            .order_by(t.c.day, t.c.exchange)
        )
        return [(int(day), str(exchange)) for day, exchange in self.conn.execute(query)]

    def charges_orders_put(self, mode: str, day: int, segment: str,
                           rows: Iterable[Mapping[str, Any]], now: int) -> int:  # fmt: skip
        """A day's and segment's orders with their share of Upstox's charges (``order_id``,
        ``parts``, ``total_paise``, ``basis``), in place of what was kept of them."""
        t = self._t("charges_order")
        self.conn.execute(
            delete(t).where(self._mine(t), t.c.mode == mode, t.c.day == day,
                            t.c.segment == segment)
        )  # fmt: skip
        made = [
            {**self.encode(row), "principal": self.who, "mode": mode, "day": day,
             "segment": segment, "created_at": now, "updated_at": now}
            for row in rows
        ]  # fmt: skip
        if made:
            self.conn.execute(insert(t), made)
        return len(made)

    def charges_orders_of(self, mode: str,
                          order_ids: Iterable[str]) -> dict[tuple[str, int], dict[str, Any]]:  # fmt: skip
        """The named orders' shares of Upstox's charges, by (order id, day)."""
        t = self._t("charges_order")
        out: dict[tuple[str, int], dict[str, Any]] = {}
        for batch in chunks(sorted(set(order_ids))):
            for row in self.conn.execute(
                select(t).where(self._mine(t), t.c.mode == mode, t.c.order_id.in_(batch))
            ).mappings():
                out[(row["order_id"], int(row["day"]))] = self.out(row)
        return out

    def charges_order_days(self, mode: str, segment: str, start: int,
                           end: int) -> dict[int, str]:  # fmt: skip
        """The days of ``start`` to ``end`` whose orders of a segment carry an actual, each
        with its basis."""
        t = self._t("charges_order")
        query = (
            select(t.c.day, t.c.basis)
            .where(self._mine(t), t.c.mode == mode, t.c.segment == segment,
                   t.c.day >= start, t.c.day <= end)
            .distinct()
        )  # fmt: skip
        return {int(day): str(basis) for day, basis in self.conn.execute(query)}

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


def kinds_products(product_class: str) -> tuple[str, ...]:
    """The products a fill of a product class is written with (the pairing's classes)."""
    if product_class == "intraday":
        return core_kinds.INTRADAY_PRODUCTS
    return core_kinds.CARRY_PRODUCTS


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
