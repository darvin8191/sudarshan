"""The service's memory: ``$STATE_DIRECTORY/alerts.db`` (directory 700, file 600).

Tables:
    sent    one row per alert queued for Telegram: (order id, kind, filled quantity). The
            primary key is the once-only rule; a claim is made before the alert is queued, so a
            replay after a reconnect or a restart never sends it again. A stop order's trigger is
            claimed per episode (the number of times the order went into trigger pending), so a
            slow modify cannot use up the record a later real trigger needs.
    orders  the last state seen per order (working, trigger_pending, complete, ...), its filled
            quantity, whether it is final, and its trigger-pending episodes; used to spot a stop
            order leaving trigger pending and to ignore anything after an order's final state.
    meta    ``stream_seen``: the last moment the order stream was known to be up, so the pause
            after a restart can be told (service.py).

Fail open: if the file cannot be opened or written (a full disk, a read-only state directory),
the service keeps going on a bounded copy in memory of what this run wrote. Alerts are still
sent and repeats within the run are still suppressed; only a restart may repeat earlier ones.
The cause is logged at ERROR once.

Rows older than KEEP are pruned when the service starts and whenever the trading window closes.
Engines come from OpenAlgo's ``database.engine_factory`` (NullPool: a connection per transaction,
closed at once), as the vault does. Only this process uses the file.
"""

from __future__ import annotations

import contextlib
import logging
import os
import time
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import (
    Boolean,
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    delete,
    func,
    select,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import SQLAlchemyError

from database.engine_factory import create_db_engine

log = logging.getLogger("sudarshan.alerts")

DB_FILENAME = "alerts.db"
KEEP = timedelta(days=7)
#: The in-memory copy's bounds: far above a trading day's orders, so they only bound a failure.
MEMORY_ORDERS = 10_000
MEMORY_CLAIMS = 50_000

metadata = MetaData()
sent = Table(
    "sent",
    metadata,
    Column("order_id", String(80), primary_key=True),
    Column("kind", String(20), primary_key=True),
    Column("filled", Integer, primary_key=True),
    Column("at", Float, nullable=False, index=True),
)
orders = Table(
    "orders",
    metadata,
    Column("order_id", String(80), primary_key=True),
    Column("state", String(20), nullable=False),
    Column("filled", Integer, nullable=False),
    Column("final", Boolean, nullable=False),
    Column("pending_seen", Integer, nullable=False, default=0),
    Column("at", Float, nullable=False, index=True),
)
meta = Table(
    "meta",
    metadata,
    Column("name", String(40), primary_key=True),
    Column("value", Float, nullable=False),
)


@dataclass(frozen=True)
class OrderState:
    state: str
    filled: int
    final: bool
    pending_seen: int = 0


class StoreFailed(Exception):
    """The file refused a transaction; from now on the store answers from memory."""


class _Memory:
    """What this run wrote, bounded (oldest first out)."""

    def __init__(self):
        self.orders: OrderedDict[str, OrderState] = OrderedDict()
        self.claims: OrderedDict[tuple[str, str, int], None] = OrderedDict()
        self.meta: dict[str, float] = {}

    def save_order(self, key: str, state: OrderState) -> None:
        self.orders[key] = state
        self.orders.move_to_end(key)
        while len(self.orders) > MEMORY_ORDERS:
            self.orders.popitem(last=False)

    def claim(self, claim: tuple[str, str, int]) -> bool:
        if claim in self.claims:
            return False
        self.claims[claim] = None
        while len(self.claims) > MEMORY_CLAIMS:
            self.claims.popitem(last=False)
        return True

    def most_filled(self, key: str, kind: str) -> int:
        return max((c[2] for c in self.claims if c[0] == key and c[1] == kind), default=0)


class _MemoryTx:
    """The tracker's calls, answered from memory."""

    def __init__(self, memory: _Memory):
        self.memory = memory

    def order(self, key: str) -> OrderState | None:
        return self.memory.orders.get(key)

    def save_order(self, key, state, filled, final, pending_seen, at) -> None:
        self.memory.save_order(key, OrderState(state, filled, final, pending_seen))

    def claim(self, key: str, kind: str, filled: int, at: datetime) -> bool:
        return self.memory.claim((key, kind, filled))

    def most_filled(self, key: str, kind: str) -> int:
        return self.memory.most_filled(key, kind)


class _Tx(_MemoryTx):
    """The tracker's calls inside one transaction.

    Every write is copied into memory too, but only once the transaction has committed: a retry
    from memory after a failed commit must not find the claim it failed to make.
    """

    def __init__(self, conn, memory: _Memory):
        super().__init__(memory)
        self.conn = conn
        self.committed_writes: list = []

    def order(self, key: str) -> OrderState | None:
        row = self.conn.execute(
            select(orders.c.state, orders.c.filled, orders.c.final, orders.c.pending_seen).where(
                orders.c.order_id == key
            )
        ).first()
        if row is None:
            return None
        return OrderState(row.state, row.filled, bool(row.final), row.pending_seen or 0)

    def save_order(self, key, state, filled, final, pending_seen, at) -> None:
        values = {
            "state": state,
            "filled": filled,
            "final": final,
            "pending_seen": pending_seen,
            "at": at.timestamp(),
        }
        stmt = sqlite_insert(orders).values(order_id=key, **values)
        self.conn.execute(stmt.on_conflict_do_update(index_elements=["order_id"], set_=values))
        state_now = OrderState(state, filled, final, pending_seen)
        self.committed_writes.append(lambda: self.memory.save_order(key, state_now))

    def claim(self, key: str, kind: str, filled: int, at: datetime) -> bool:
        """True if this alert was not claimed before (and now is)."""
        stmt = sqlite_insert(sent).values(order_id=key, kind=kind, filled=filled, at=at.timestamp())
        fresh = self.conn.execute(stmt.on_conflict_do_nothing()).rowcount == 1
        self.committed_writes.append(lambda: self.memory.claim((key, kind, filled)))
        return fresh

    def most_filled(self, key: str, kind: str) -> int:
        query = select(func.max(sent.c.filled)).where(sent.c.order_id == key, sent.c.kind == kind)
        return self.conn.execute(query).scalar() or 0


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.memory = _Memory()
        self.engine = None
        try:
            _private_file(self.path)
            engine = create_db_engine(f"sqlite:///{self.path}")
            metadata.create_all(engine)
            self.engine = engine
        except (OSError, SQLAlchemyError) as exc:
            self._fail(exc)

    @property
    def in_memory(self) -> bool:
        return self.engine is None

    @contextlib.contextmanager
    def transaction(self) -> Iterator[_MemoryTx]:
        """One transaction; raises StoreFailed once if the file fails (repeat the work then)."""
        if self.engine is None:
            yield _MemoryTx(self.memory)
            return
        try:
            with self.engine.begin() as conn:
                tx = _Tx(conn, self.memory)
                yield tx
        except (OSError, SQLAlchemyError) as exc:
            self._fail(exc)
            raise StoreFailed from exc
        for write in tx.committed_writes:
            write()

    def get_meta(self, name: str) -> float | None:
        if self.engine is not None:
            try:
                with self.engine.begin() as conn:
                    value = conn.execute(select(meta.c.value).where(meta.c.name == name)).scalar()
                return value if value is not None else self.memory.meta.get(name)
            except (OSError, SQLAlchemyError) as exc:
                self._fail(exc)
        return self.memory.meta.get(name)

    def set_meta(self, name: str, value: float) -> None:
        self.memory.meta[name] = value
        if self.engine is None:
            return
        try:
            with self.engine.begin() as conn:
                stmt = sqlite_insert(meta).values(name=name, value=value)
                conn.execute(
                    stmt.on_conflict_do_update(index_elements=["name"], set_={"value": value})
                )
        except (OSError, SQLAlchemyError) as exc:
            self._fail(exc)

    def prune(self, now: datetime | None = None) -> int:
        if self.engine is None:
            return 0
        cutoff = (now.timestamp() if now else time.time()) - KEEP.total_seconds()
        try:
            with self.engine.begin() as conn:
                removed = conn.execute(delete(sent).where(sent.c.at < cutoff)).rowcount
                removed += conn.execute(delete(orders).where(orders.c.at < cutoff)).rowcount
        except (OSError, SQLAlchemyError) as exc:
            self._fail(exc)
            return 0
        return removed

    def close(self) -> None:
        if self.engine is not None:
            self.engine.dispose()

    def _fail(self, exc: BaseException) -> None:
        if self.engine is not None:
            with contextlib.suppress(Exception):
                self.engine.dispose()
        self.engine = None
        if not getattr(self, "_told", False):
            self._told = True
            log.error(
                "The alert record %s cannot be used (%s). Alerts are still sent, and repeats "
                "are held back in memory until the service restarts.",
                self.path,
                _cause(exc),
            )
            log.debug("alert record failure", exc_info=exc)


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
