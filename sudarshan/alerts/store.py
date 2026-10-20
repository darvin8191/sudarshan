"""The service's memory: ``$STATE_DIRECTORY/alerts.db`` (directory 700, file 600).

Tables:
    sent    one row per alert queued for Telegram: (order id, kind, filled quantity). The
            primary key is the once-only rule; a claim is made before the alert is queued, so a
            replay after a reconnect or a restart never sends it again.
    orders  the last state seen per order (working, trigger_pending, complete, ...), its filled
            quantity and whether it is final; used to spot a stop order leaving trigger pending
            and to ignore anything that arrives for an order after its final state.

Rows older than KEEP are pruned when the service starts and whenever the trading window closes, so
the file stays small (a busy day is a few thousand rows). Engines come from OpenAlgo's
``database.engine_factory`` (NullPool: a connection per transaction, closed at once), as the
vault does. Only this process uses the file.
"""

from __future__ import annotations

import contextlib
import os
import time
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

from database.engine_factory import create_db_engine

DB_FILENAME = "alerts.db"
KEEP = timedelta(days=7)

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
    Column("at", Float, nullable=False, index=True),
)


@dataclass(frozen=True)
class OrderState:
    state: str
    filled: int
    final: bool


class _Tx:
    """The calls the tracker makes, all inside one transaction."""

    def __init__(self, conn):
        self.conn = conn

    def order(self, key: str) -> OrderState | None:
        row = self.conn.execute(
            select(orders.c.state, orders.c.filled, orders.c.final).where(orders.c.order_id == key)
        ).first()
        return None if row is None else OrderState(row.state, row.filled, bool(row.final))

    def save_order(self, key: str, state: str, filled: int, final: bool, at: datetime) -> None:
        values = {"state": state, "filled": filled, "final": final, "at": at.timestamp()}
        stmt = sqlite_insert(orders).values(order_id=key, **values)
        self.conn.execute(stmt.on_conflict_do_update(index_elements=["order_id"], set_=values))

    def claim(self, key: str, kind: str, filled: int, at: datetime) -> bool:
        """True if this alert was not claimed before (and now is)."""
        stmt = sqlite_insert(sent).values(order_id=key, kind=kind, filled=filled, at=at.timestamp())
        return self.conn.execute(stmt.on_conflict_do_nothing()).rowcount == 1

    def most_filled(self, key: str, kind: str) -> int:
        query = select(func.max(sent.c.filled)).where(sent.c.order_id == key, sent.c.kind == kind)
        return self.conn.execute(query).scalar() or 0


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        _private_file(self.path)
        self.engine = create_db_engine(f"sqlite:///{self.path}")
        metadata.create_all(self.engine)

    @contextlib.contextmanager
    def transaction(self) -> Iterator[_Tx]:
        with self.engine.begin() as conn:
            yield _Tx(conn)

    def prune(self, now: datetime | None = None) -> int:
        cutoff = (now.timestamp() if now else time.time()) - KEEP.total_seconds()
        with self.engine.begin() as conn:
            removed = conn.execute(delete(sent).where(sent.c.at < cutoff)).rowcount
            removed += conn.execute(delete(orders).where(orders.c.at < cutoff)).rowcount
        return removed

    def close(self) -> None:
        self.engine.dispose()


def _private_file(path: Path) -> None:
    """The directory (700) and an empty file (600) before SQLite creates it with the umask."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with contextlib.suppress(FileExistsError):
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
