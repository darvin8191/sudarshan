"""family.db, the family view's record (design 4): members, sign-ins, invites, sessions, the audit.

``$STATE_DIRECTORY/family.db`` (folder 700, file 600, both made before SQLite can touch them, so
the umask never decides). The engine is OpenAlgo's ``database.engine_factory.create_db_engine``
(NullPool: a connection per transaction, closed at once); WAL and synchronous=NORMAL come from the
listener in ``database/__init__.py``. The engine emits its own BEGIN, so every write and every
migration step is a whole transaction, and a write begins IMMEDIATE: it holds the write lock from
its first read, so two writers queue instead of one failing on a snapshot the other made stale.

One writer. Every read and write runs on the process's one store thread, a module-level
single-thread executor (``stop`` ends it); the event loop awaits it (``read``, ``write``) and never
touches SQLite itself. A call made on that thread runs in place.

Secrets at rest (the TOTP secrets, later the Telegram chat ids) are sealed with a Fernet key and
the network addresses kept only as keyed hashes, both keys derived from ``store.key``
(``Secrets``); neither key enters the database. No figure of the owner's is ever stored here: the
books are served from the portfolio's memory, and an outbox row keeps no payload once it is sent
(cross-design Y44; a CHECK in the schema holds it).

Versions: a member row carries ``version``; the owner's write names the version it was decided on
and a mismatch raises ``VersionConflict`` with nothing written. A create that can be retried
carries a ``request_id`` and a retry returns the row already made. Failures reach a person as
sentences (``errors.py``); the system's own words go to the journal only.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import concurrent.futures
import contextlib
import hashlib
import hmac
import json
import logging
import os
import threading
import warnings
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import MetaData, Table, and_, delete, event, func, insert, or_, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SAWarning, SQLAlchemyError

from database.engine_factory import create_db_engine

from . import migrations
from .errors import NotFound, SchemaTooNew, StoreError, VersionConflict

log = logging.getLogger("sudarshan.family")

DB_FILENAME = "family.db"
STATE_DIR = Path("/var/lib/sudarshan-family")
THREAD_PREFIX = "family-store"
#: Columns that hold JSON text; the store converts at its boundary.
JSON_COLUMNS = frozenset({"privacy", "quiet", "notify", "detail"})
#: The two keys ``store.key`` opens, each for one use only.
SECRETS_INFO = b"sudarshan/family/store/secrets/v1"
HASHES_INFO = b"sudarshan/family/store/hashes/v1"
STORE_KEY_BYTES = 32


# ---- the store key ---------------------------------------------------------------------------
class Secrets:
    """What ``store.key`` opens: a Fernet for the secrets kept at rest and a key for the hashes
    kept of network addresses. Built from the key's 32 bytes; nothing of it is stored."""

    def __init__(self, raw: bytes):
        if len(raw) != STORE_KEY_BYTES:
            raise ValueError("the store key is 32 bytes")

        def derive(info: bytes) -> bytes:
            return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(raw)

        self._fernet = Fernet(base64.urlsafe_b64encode(derive(SECRETS_INFO)))
        self._hash_key = derive(HASHES_INFO)

    @classmethod
    def from_text(cls, text: str) -> Secrets:
        """From ``store.key`` as root writes it: standard base64 of 32 bytes and a newline."""
        try:
            raw = base64.b64decode(text.strip(), validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("the store key is not base64") from None
        return cls(raw)

    def seal(self, text: str) -> str:
        return self._fernet.encrypt(text.encode("utf-8")).decode("ascii")

    def open(self, token: str) -> str:
        """The secret back; StoreError when it was sealed under another key."""
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeError):
            raise StoreError("a secret at rest does not open with this store.key") from None

    def place(self, address: str) -> str:
        """A keyed hash of a network address: the same address gives the same hash, and the hash
        gives back nothing (design 2.2: for the device list and the audit only)."""
        return hmac.new(self._hash_key, address.encode("utf-8"), hashlib.sha256).hexdigest()

    def keyed(self, *parts: str) -> str:
        """A keyed hash of a purpose and its parts (the sign-in counts, the phone tokens, network
        areas). The parts are joined with a byte no address or name holds, so no two uses meet."""
        text = "\x1f".join(parts).encode("utf-8")
        return hmac.new(self._hash_key, text, hashlib.sha256).hexdigest()


# ---- the one writer thread -------------------------------------------------------------------
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
    """End the store thread after the work it has; the next call makes a new one."""
    global _pool
    with _pool_lock:
        pool, _pool = _pool, None
    if pool is not None:
        pool.shutdown(wait=True)


def on_writer[T](fn: Callable[[], T]) -> T:
    """``fn()`` on the store thread, waited for (in place when called there)."""
    if getattr(_here, "writer", False):
        return fn()
    return _executor().submit(fn).result()


async def on_writer_async[T](fn: Callable[[], T]) -> T:
    """``fn()`` on the store thread, awaited: the event loop never waits on SQLite."""
    return await asyncio.wrap_future(_executor().submit(fn))


# ---- files and engines -----------------------------------------------------------------------
def default_path() -> Path:
    base = os.environ.get("SUDARSHAN_FAMILY_STATE_DIR") or os.environ.get("STATE_DIRECTORY")
    return Path((base or str(STATE_DIR)).split(":")[0]) / DB_FILENAME


def _cause(exc: BaseException) -> str:
    """The failure in words: the system's or SQLite's own message, never a class name."""
    if isinstance(exc, OSError):
        return (exc.strerror or "the file cannot be written").lower()
    original = getattr(exc, "orig", None)
    return str(original) if original else "the database refused the change"


def private_file(path: Path) -> None:
    """The folder (700) and an empty file (600) before SQLite creates it with the umask. A folder
    that exists is left as it is (systemd's StateDirectory makes it 700)."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with contextlib.suppress(FileExistsError):
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
        os.close(os.open(path, flags, 0o600))


def engine(path: Path) -> Engine:
    """family.db's engine: NullPool from the factory, and a real BEGIN for every transaction,
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


def open_read_only(path: Path) -> Engine:
    """An engine that cannot write, for ``--status``: never makes a file and never migrates. With
    a ``-wal`` file present ``mode=ro`` sees the newest data; without one the main file is whole
    and ``immutable=1`` needs no side file."""
    target = quote(str(path))
    ro, immutable = (f"sqlite:///file:{target}?{q}&uri=true" for q in ("mode=ro", "immutable=1"))
    last: Exception | None = None
    for url in [ro, immutable] if Path(f"{path}-wal").exists() else [immutable, ro]:
        made = create_db_engine(url)
        try:
            with made.connect() as conn:
                conn.exec_driver_sql("PRAGMA user_version").scalar()
            return made
        except SQLAlchemyError as exc:
            made.dispose()
            last = exc
    raise StoreError(_cause(last) if last else "", key="store_failed")


def _json_in(values: Mapping[str, Any]) -> dict[str, Any]:
    return {
        k: json.dumps(v, separators=(",", ":"), ensure_ascii=False) if k in JSON_COLUMNS else v
        for k, v in values.items()
    }


def _row(found: Any) -> dict[str, Any] | None:
    if found is None:
        return None
    out = dict(found)
    for name in JSON_COLUMNS & out.keys():
        try:
            out[name] = json.loads(out[name]) if out[name] else {}
        except ValueError:
            out[name] = {}
    return out


# ---- one transaction's queries -----------------------------------------------------------------
class Tx:
    """The queries of one transaction. ``now`` is always the caller's (epoch milliseconds)."""

    def __init__(self, conn: Connection, tables: Mapping[str, Table], secrets: Secrets | None):
        self.conn, self._tables, self.secrets = conn, tables, secrets

    def t(self, name: str) -> Table:
        return self._tables[name]

    def _where(self, table: Table, where: Mapping[str, Any]):
        return and_(*(table.c[k] == v for k, v in where.items())) if where else True

    def row(self, table: str, **where: Any) -> dict[str, Any] | None:
        t = self.t(table)
        return _row(self.conn.execute(select(t).where(self._where(t, where))).mappings().first())

    def rows(self, table: str, order: str = "", **where: Any) -> list[dict[str, Any]]:
        t = self.t(table)
        query = select(t).where(self._where(t, where))
        if order:
            query = query.order_by(*(t.c[name] for name in order.split(",")))
        return [_row(r) for r in self.conn.execute(query).mappings()]  # type: ignore[misc]

    def count(self, table: str, **where: Any) -> int:
        t = self.t(table)
        query = select(func.count()).select_from(t).where(self._where(t, where))
        return int(self.conn.execute(query).scalar() or 0)

    def insert(self, table: str, values: Mapping[str, Any]) -> Any:
        """Insert one row; its primary key (the new id of a table with one)."""
        done = self.conn.execute(insert(self.t(table)).values(**_json_in(values)))
        return done.inserted_primary_key[0] if done.inserted_primary_key else None

    def update(self, table: str, where: Mapping[str, Any], values: Mapping[str, Any]) -> int:
        t = self.t(table)
        done = self.conn.execute(update(t).where(self._where(t, where)).values(**_json_in(values)))
        return int(done.rowcount)

    def delete(self, table: str, **where: Any) -> int:
        t = self.t(table)
        return int(self.conn.execute(delete(t).where(self._where(t, where))).rowcount)

    # ---- members ----
    def member(self, member_id: int) -> dict[str, Any] | None:
        return self.row("member", id=member_id)

    def member_by_handle(self, handle: str) -> dict[str, Any] | None:
        return self.row("member", handle=handle)

    def members_live(self) -> list[dict[str, Any]]:
        """Every member who is not removed, oldest first."""
        t = self.t("member")
        found = self.conn.execute(select(t).where(t.c.status != "removed").order_by(t.c.id))
        return [_row(r) for r in found.mappings()]  # type: ignore[misc]

    def member_add(self, values: Mapping[str, Any], now: int) -> dict[str, Any]:
        new = self.insert("member", {**values, "created_at": now, "updated_at": now})
        return self.member(new)  # type: ignore[return-value]

    def member_change(
        self, member_id: int, version: Any, values: Mapping[str, Any], now: int
    ) -> dict[str, Any]:
        """Change a member at ``version``; the row after. NotFound, VersionConflict."""
        if not isinstance(version, int) or isinstance(version, bool):
            raise VersionConflict()
        t = self.t("member")
        done = self.conn.execute(
            update(t)
            .where(t.c.id == member_id, t.c.version == version)
            .values(**_json_in(values), updated_at=now, version=t.c.version + 1)
        )
        if done.rowcount == 0:
            if self.member(member_id) is None:
                raise NotFound("member_gone")
            raise VersionConflict()
        return self.member(member_id)  # type: ignore[return-value]

    def handle_taken(self, handle: str, member_id: int | None = None) -> bool:
        found = self.member_by_handle(handle)
        return found is not None and found["id"] != member_id

    # ---- sessions ----
    def sessions_live(self, member_id: int, now: int) -> list[dict[str, Any]]:
        """A member's sessions that still sign them in, oldest first."""
        t = self.t("viewer_session")
        found = self.conn.execute(
            select(t)
            .where(
                t.c.member_id == member_id,
                t.c.revoked_at.is_(None),
                t.c.absolute_expires_at > now,
                t.c.idle_expires_at > now,
            )
            .order_by(t.c.created_at, t.c.id_hash)
        )
        return [dict(r) for r in found.mappings()]

    def session_revoke(self, id_hash: str, now: int, reason: str) -> bool:
        t = self.t("viewer_session")
        done = self.conn.execute(
            update(t)
            .where(t.c.id_hash == id_hash, t.c.revoked_at.is_(None))
            .values(revoked_at=now, revoke_reason=reason)
        )
        return done.rowcount > 0

    def phone_seen(self, member_id: int, summary: str, area: str | None, since: int) -> bool:
        """Whether this member used, since ``since``, a browser with this fixed summary (a rename
        changes only the label) and signed in from this network area (review L3); an area that is
        not known (None) is not held against the sign-in."""
        t = self.t("viewer_session")
        recent = and_(t.c.member_id == member_id, t.c.last_seen_at >= since)
        seen = [t.c.ua_summary == summary] + ([t.c.area_hash == area] if area else [])
        found = (select(func.count()).where(recent, cond) for cond in seen)
        return all(self.conn.execute(query).scalar() for query in found)

    def sessions_prune(self, before: int) -> int:
        """Forget sessions that ended (revoked or expired) before ``before``."""
        t = self.t("viewer_session")
        lapsed = or_(t.c.absolute_expires_at < before, t.c.idle_expires_at < before)
        ended = or_(t.c.revoked_at < before, and_(t.c.revoked_at.is_(None), lapsed))
        return int(self.conn.execute(delete(t).where(ended)).rowcount)

    # ---- the job log ----
    def job(self, job: str, day: int) -> dict[str, Any] | None:
        return self.row("job_run", name=job, day=day)

    def job_put(self, job: str, day: int, status: str, detail: str | None, at: int) -> None:
        """The one record of a job on a day; a later run of the same day replaces it."""
        self.delete("job_run", name=job, day=day)
        self.insert("job_run", {"name": job, "day": day, "status": status, "detail": detail,
                                "at": at})  # fmt: skip

    def user_version(self) -> int:
        return migrations.version(self.conn)


# ---- the store ---------------------------------------------------------------------------------
class FamilyStore:
    """family.db, opened and migrated. ``read`` and ``write`` are awaited from the event loop;
    ``read_now`` and ``write_now`` wait (the command line and the tests)."""

    def __init__(self, path: Path | str, secrets: Secrets | None = None):
        self.path = Path(path)
        self.secrets = secrets
        self.engine: Engine | None = None
        try:
            private_file(self.path)
            self.engine = engine(self.path)
            self.writer = writer(self.engine)
            self.applied = on_writer(lambda: migrations.migrate(self.engine))  # type: ignore[arg-type]
            meta = MetaData()
            with warnings.catch_warnings():  # the share index is an expression: SQLite keeps it
                warnings.filterwarnings("ignore", "Skipped unsupported reflection", SAWarning)
                meta.reflect(bind=self.engine)
            self._tables = dict(meta.tables)
            self.version = migrations.LATEST
        except SchemaTooNew:
            self.close()
            raise
        except (OSError, SQLAlchemyError) as exc:
            self.close()
            log.error("the family store could not be opened: %s", _cause(exc))
            raise StoreError(_cause(exc), key="store_failed") from exc
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        if self.engine is not None:
            self.engine.dispose()

    def _read_here[T](self, fn: Callable[[Tx], T]) -> T:
        try:
            with self.engine.connect() as conn:  # type: ignore[union-attr]
                return fn(Tx(conn, self._tables, self.secrets))
        except (OSError, SQLAlchemyError) as exc:
            log.error("a family store read failed: %s", _cause(exc))
            raise StoreError(_cause(exc)) from exc

    def _write_here[T](self, fn: Callable[[Tx], T]) -> T:
        try:
            with self.writer.begin() as conn:
                return fn(Tx(conn, self._tables, self.secrets))
        except (OSError, SQLAlchemyError) as exc:
            log.error("a family store write failed: %s", _cause(exc))
            raise StoreError(_cause(exc)) from exc

    def read_now[T](self, fn: Callable[[Tx], T]) -> T:
        return on_writer(lambda: self._read_here(fn))

    def write_now[T](self, fn: Callable[[Tx], T]) -> T:
        """``fn(tx)`` in one IMMEDIATE transaction: all of it, or none."""
        return on_writer(lambda: self._write_here(fn))

    async def read[T](self, fn: Callable[[Tx], T]) -> T:
        return await on_writer_async(lambda: self._read_here(fn))

    async def write[T](self, fn: Callable[[Tx], T]) -> T:
        return await on_writer_async(lambda: self._write_here(fn))


def tables_of(conn: Connection) -> list[str]:
    """The tables of an open file (the schema tests and ``--status``)."""
    found = conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'table'")
    return sorted(row[0] for row in found if not row[0].startswith("sqlite_"))
