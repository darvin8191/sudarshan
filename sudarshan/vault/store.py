"""The vault database: ``db/sudarshan-vault/sudarshan_vault.db``, beside OpenAlgo's databases.

Tables:
    secrets             one row per secret: Fernet ciphertext under the vault key, never plaintext
    autologin_status    one row (id 1): the auto-login switch and the result of the last run
    autologin_attempts  one row per call to Upstox, for the rate limit and the history list

Two processes write it: OpenAlgo (secrets, the switch) and the auto-login worker (run results).
Every status write is a targeted UPDATE of the columns its writer owns, so neither can undo the
other's change. The directory is created with mode 700 and the file with mode 600; SQLite gives
its -wal/-shm files the same mode. The directory exists so that the auto-login unit can be
allowed to write the vault and nothing else (``ReadWritePaths``).
Engines come from OpenAlgo's ``database.engine_factory`` (NullPool, and inside OpenAlgo the
cooperative SQLite lock wait that keeps the eventlet hub running).
"""

from __future__ import annotations

import contextlib
import os
import stat
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography.fernet import InvalidToken
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text, delete, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import declarative_base, sessionmaker

from database.engine_factory import create_db_engine

from .keys import VAULT_INFO, fernet_for, key_fingerprint

REPO_ROOT = Path(__file__).resolve().parents[2]
DB_FILENAME = "sudarshan_vault.db"
#: The vault's own directory under db/ (mode 700): the auto-login unit may write only here.
VAULT_DIR = "sudarshan-vault"
#: Where the first version kept the vault, directly in db/ next to OpenAlgo's databases.
LEGACY_PATH = REPO_ROOT / "db" / DB_FILENAME
#: Rows kept in autologin_attempts; older ones are pruned after each run.
ATTEMPT_RETENTION = timedelta(days=30)

Base = declarative_base()


class SecretRow(Base):
    __tablename__ = "secrets"
    name = Column(String(64), primary_key=True)
    ciphertext = Column(Text, nullable=False)
    key_id = Column(String(32), nullable=False)
    updated_at = Column(DateTime, nullable=False)


class StatusRow(Base):
    __tablename__ = "autologin_status"
    id = Column(Integer, primary_key=True)
    enabled = Column(Boolean, nullable=False, default=False)
    paused_code = Column(String(40))
    cooldown_until = Column(DateTime)
    last_run_id = Column(String(40))
    last_run_at = Column(DateTime)
    last_run_kind = Column(String(16))
    last_outcome = Column(String(16))
    last_code = Column(String(40))
    last_message = Column(Text)
    last_success_at = Column(DateTime)
    active_session_id = Column(String(64))
    lease_owner = Column(String(40))
    lease_expires = Column(DateTime)
    updated_at = Column(DateTime)


class AttemptRow(Base):
    __tablename__ = "autologin_attempts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    at = Column(DateTime, nullable=False, index=True)
    kind = Column(String(16), nullable=False)
    outcome = Column(String(16), nullable=False)
    code = Column(String(40), nullable=False)


#: Status columns a caller may set through Vault.update_status.
STATUS_FIELDS = frozenset(
    {
        "enabled",
        "paused_code",
        "cooldown_until",
        "last_run_id",
        "last_run_at",
        "last_run_kind",
        "last_outcome",
        "last_code",
        "last_message",
        "last_success_at",
        "active_session_id",
    }
)


class SecretUnreadable(Exception):
    """A saved secret cannot be decrypted with the current key (the pepper changed)."""


@dataclass(frozen=True)
class SecretInfo:
    is_set: bool
    updated_at: datetime | None
    readable: bool


@dataclass(frozen=True)
class AutologinState:
    enabled: bool
    paused_code: str | None
    cooldown_until: datetime | None
    last_run_id: str | None
    last_run_at: datetime | None
    last_run_kind: str | None
    last_outcome: str | None
    last_code: str | None
    last_message: str | None
    last_success_at: datetime | None
    active_session_id: str | None
    lease_owner: str | None
    lease_expires: datetime | None


@dataclass(frozen=True)
class Attempt:
    id: int
    at: datetime
    kind: str
    outcome: str
    code: str


def default_vault_path() -> Path:
    """``db/sudarshan-vault/sudarshan_vault.db`` in the OpenAlgo checkout.

    ``SUDARSHAN_VAULT_DB`` overrides it (tests, or a vault kept elsewhere).
    """
    override = os.environ.get("SUDARSHAN_VAULT_DB")
    return Path(override) if override else REPO_ROOT / "db" / VAULT_DIR / DB_FILENAME


def move_legacy_vault(path: Path | None = None, legacy: Path = LEGACY_PATH) -> bool:
    """Move a vault left at ``db/sudarshan_vault.db`` into its own directory; True if moved.

    OpenAlgo calls this when it starts: the worker cannot, because the only place its unit may
    write is the vault directory. It acts only when the old file exists and the new one does not,
    never on a vault chosen with ``SUDARSHAN_VAULT_DB``, and nothing has the old file open then.
    The write-ahead log and the shared-memory file go first and the database file last, so a move
    cut short is finished by the next start and nothing committed is left behind.
    """
    if path is None:
        if os.environ.get("SUDARSHAN_VAULT_DB"):
            return False
        path = default_vault_path()
    if path == legacy or path.exists() or not legacy.exists():
        return False
    ensure_private_dir(path.parent)
    for suffix in ("-wal", "-shm"):
        side = Path(f"{legacy}{suffix}")
        if side.exists():
            os.replace(side, Path(f"{path}{suffix}"))
    os.replace(legacy, path)
    ensure_private_file(path)
    return True


def utcnow() -> datetime:
    return datetime.now(UTC)


def _to_db(value: datetime | None) -> datetime | None:
    """Aware -> naive UTC for storage (SQLite keeps no zone)."""
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("vault timestamps must be timezone-aware")
    return value.astimezone(UTC).replace(tzinfo=None)


def _from_db(value: datetime | None) -> datetime | None:
    return None if value is None else value.replace(tzinfo=UTC)


def ensure_private_dir(directory: Path) -> None:
    """Create ``directory`` with mode 700 if missing; narrow the vault's own directory to 700.

    Only a directory named ``VAULT_DIR`` is narrowed: a vault kept elsewhere by
    ``SUDARSHAN_VAULT_DB`` may sit in a directory that is not ours to change.
    """
    directory.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(FileExistsError):
        directory.mkdir(mode=0o700)
    if directory.name == VAULT_DIR and stat.S_IMODE(directory.stat().st_mode) & 0o077:
        with contextlib.suppress(OSError):
            os.chmod(directory, 0o700)


def ensure_private_file(path: Path) -> None:
    """Create ``path`` empty with mode 600 if missing; narrow it to 600 if it is wider."""
    ensure_private_dir(path.parent)
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        pass
    else:
        os.close(fd)
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        with contextlib.suppress(OSError):  # not ours to change (another owner): leave it
            os.chmod(path, 0o600)


class Vault:
    """Secrets and the auto-login status, in one small SQLite file.

    Args:
        path: The database file; ``default_vault_path()`` when omitted.
        pepper: OpenAlgo's ``API_KEY_PEPPER``; the vault key is derived from it (keys.py).
    """

    def __init__(self, path: Path | str | None = None, *, pepper: str):
        self.path = Path(path) if path else default_vault_path()
        self._fernet = fernet_for(pepper, VAULT_INFO)
        self._key_id = key_fingerprint(pepper, VAULT_INFO)
        self._lock = threading.Lock()
        self._sessionmaker = None

    # -- plumbing -------------------------------------------------------------------------------

    def _session(self):
        factory = self._sessionmaker
        if factory is None:
            with self._lock:
                if self._sessionmaker is None:
                    self._sessionmaker = self._prepare()
                factory = self._sessionmaker
        return factory()

    def _prepare(self):
        ensure_private_file(self.path)
        engine = create_db_engine(f"sqlite:///{self.path}")
        try:
            Base.metadata.create_all(engine)
        except OperationalError:
            # Another process created the tables between the existence check and the CREATE.
            Base.metadata.create_all(engine)
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with factory() as session:
            if session.get(StatusRow, 1) is None:
                session.add(StatusRow(id=1, enabled=False, updated_at=_to_db(utcnow())))
                try:
                    session.commit()
                except IntegrityError:
                    session.rollback()  # the other process inserted it first
        return factory

    # -- secrets ----------------------------------------------------------------------------------

    def set_secret(self, name: str, value: str) -> datetime:
        """Encrypt and store ``value`` under ``name``; returns the time it was saved."""
        if not name or not isinstance(value, str) or not value:
            raise ValueError("a secret needs a name and a non-empty value")
        now = utcnow()
        values = {
            "name": name,
            "ciphertext": self._fernet.encrypt(value.encode("utf-8")).decode("ascii"),
            "key_id": self._key_id,
            "updated_at": _to_db(now),
        }
        stmt = sqlite_insert(SecretRow).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[SecretRow.name],
            set_={k: stmt.excluded[k] for k in ("ciphertext", "key_id", "updated_at")},
        )
        with self._session() as session:
            session.execute(stmt)
            session.commit()
        return now

    def clear_secret(self, name: str) -> bool:
        """Delete ``name``; True when it existed."""
        with self._session() as session:
            result = session.execute(delete(SecretRow).where(SecretRow.name == name))
            session.commit()
            return bool(result.rowcount)

    def get_secret(self, name: str) -> str | None:
        """The plaintext of ``name``, or None when it is not set.

        Raises:
            SecretUnreadable: it was saved under another key, or the ciphertext is damaged.
        """
        with self._session() as session:
            row = session.get(SecretRow, name)
            if row is None:
                return None
            ciphertext, key_id = row.ciphertext, row.key_id
        if key_id != self._key_id:
            raise SecretUnreadable(name)
        try:
            return self._fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeError) as exc:
            raise SecretUnreadable(name) from exc

    def secret_info(self, name: str) -> SecretInfo:
        """Whether ``name`` is set, when, and whether the current key can read it (no decrypt)."""
        with self._session() as session:
            row = session.get(SecretRow, name)
            if row is None:
                return SecretInfo(is_set=False, updated_at=None, readable=False)
            return SecretInfo(
                is_set=True,
                updated_at=_from_db(row.updated_at),
                readable=row.key_id == self._key_id,
            )

    # -- auto-login status ------------------------------------------------------------------------

    def state(self) -> AutologinState:
        with self._session() as session:
            row = session.get(StatusRow, 1)
            return AutologinState(
                enabled=bool(row.enabled),
                paused_code=row.paused_code,
                cooldown_until=_from_db(row.cooldown_until),
                last_run_id=row.last_run_id,
                last_run_at=_from_db(row.last_run_at),
                last_run_kind=row.last_run_kind,
                last_outcome=row.last_outcome,
                last_code=row.last_code,
                last_message=row.last_message,
                last_success_at=_from_db(row.last_success_at),
                active_session_id=row.active_session_id,
                lease_owner=row.lease_owner,
                lease_expires=_from_db(row.lease_expires),
            )

    def update_status(self, **fields) -> None:
        """Set only the named status columns (see STATUS_FIELDS)."""
        unknown = set(fields) - STATUS_FIELDS
        if unknown:
            raise ValueError(f"unknown status fields: {sorted(unknown)}")
        values = {
            key: _to_db(value) if isinstance(value, datetime) else value
            for key, value in fields.items()
        }
        values["updated_at"] = _to_db(utcnow())
        with self._session() as session:
            session.execute(update(StatusRow).where(StatusRow.id == 1).values(**values))
            session.commit()

    def set_enabled(self, enabled: bool) -> None:
        self.update_status(enabled=bool(enabled))

    # -- attempts (one per call to Upstox) ----------------------------------------------------------

    def start_attempt(self, *, kind: str, at: datetime) -> int:
        """Record a call to Upstox before it is made, so even a crash counts for the rate limit."""
        with self._session() as session:
            row = AttemptRow(at=_to_db(at), kind=kind, outcome="started", code="started")
            session.add(row)
            session.commit()
            return row.id

    def finish_attempt(self, attempt_id: int, *, outcome: str, code: str) -> None:
        with self._session() as session:
            session.execute(
                update(AttemptRow)
                .where(AttemptRow.id == attempt_id)
                .values(outcome=outcome, code=code)
            )
            session.commit()

    def attempts_since(self, since: datetime) -> list[Attempt]:
        with self._session() as session:
            rows = session.scalars(
                select(AttemptRow).where(AttemptRow.at >= _to_db(since)).order_by(AttemptRow.at)
            ).all()
            return [self._attempt(row) for row in rows]

    def recent_attempts(self, limit: int = 5) -> list[Attempt]:
        with self._session() as session:
            rows = session.scalars(
                select(AttemptRow).order_by(AttemptRow.at.desc(), AttemptRow.id.desc()).limit(limit)
            ).all()
            return [self._attempt(row) for row in rows]

    def prune_attempts(self, now: datetime | None = None) -> int:
        cutoff = (now or utcnow()) - ATTEMPT_RETENTION
        with self._session() as session:
            result = session.execute(delete(AttemptRow).where(AttemptRow.at < _to_db(cutoff)))
            session.commit()
            return int(result.rowcount or 0)

    @staticmethod
    def _attempt(row: AttemptRow) -> Attempt:
        return Attempt(
            id=row.id, at=_from_db(row.at), kind=row.kind, outcome=row.outcome, code=row.code
        )

    # -- one run at a time, across processes -------------------------------------------------------

    def acquire_lease(self, owner: str, *, now: datetime, ttl: timedelta) -> bool:
        """Take the run lease unless a live one is held. One UPDATE, so two takers cannot both win.

        A lease left by a crashed run expires after ``ttl``.
        """
        stale = _to_db(now)
        with self._session() as session:
            result = session.execute(
                update(StatusRow)
                .where(StatusRow.id == 1)
                .where(
                    (StatusRow.lease_owner.is_(None))
                    | (StatusRow.lease_expires.is_(None))
                    | (StatusRow.lease_expires < stale)
                )
                .values(lease_owner=owner, lease_expires=_to_db(now + ttl))
            )
            session.commit()
            return result.rowcount == 1

    def release_lease(self, owner: str) -> None:
        with self._session() as session:
            session.execute(
                update(StatusRow)
                .where(StatusRow.id == 1)
                .where(StatusRow.lease_owner == owner)
                .values(lease_owner=None, lease_expires=None)
            )
            session.commit()

    def lease_active(self, now: datetime) -> bool:
        state = self.state()
        return bool(
            state.lease_owner and state.lease_expires is not None and state.lease_expires >= now
        )
