"""The risk database: the owner's settings, the kill switch, daily locks and the audit (M5.13).

The tables are in schema.py. Engines come from OpenAlgo's ``database.engine_factory``: NullPool,
and inside OpenAlgo the cooperative SQLite lock wait that keeps the eventlet hub running. The
store creates any missing table and its one-row defaults when first used, for a dev checkout;
deployments get them from upgrade/migrate_sudarshan_risk.py.

The settings and the kill switch's state are published as immutable objects, so a decision
reads one whole version and never half of an update. Both are cached in a ``LockedTTLCache``,
and each writer invalidates its entry after its commit, never before: a read that raced a write
is returned to its own caller but never cached (utils/thread_safe_cache.py).

The store's own lock is a real one from ``utils.real_threading``, held for reference work only,
never across a database call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from database.engine_factory import create_db_engine
from utils import real_threading
from utils.logging import get_logger
from utils.thread_safe_cache import LockedTTLCache

from .schema import (
    DEFAULTS,
    AuditRow,
    DailyLockRow,
    SettingsRow,
    StateRow,
    create_missing,
    default_path,
)

logger = get_logger(__name__)

IST = timezone(timedelta(hours=5, minutes=30), "IST")  # India keeps no daylight saving
AUDIT_KEEP = timedelta(days=90)
#: Refusals of the same kind, surface and symbol within this many seconds share one audit row.
REFUSAL_MERGE_S = 60.0
CACHE_TTL_S = 60.0


def utcnow() -> datetime:
    return datetime.now(UTC)


def ist_date(at: datetime) -> str:
    """The date in India for an aware moment, as YYYY-MM-DD."""
    return at.astimezone(IST).date().isoformat()


def _to_db(value: datetime | None) -> datetime | None:
    """Aware to naive UTC for storage: SQLite keeps no zone."""
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("risk timestamps must be timezone-aware")
    return value.astimezone(UTC).replace(tzinfo=None)


def _from_db(value: datetime | None) -> datetime | None:
    return None if value is None else value.replace(tzinfo=UTC)


def _clip(value: Any, length: int) -> str | None:
    return None if value is None else str(value)[:length]


_ANY = object()


class SettingsChanged(Exception):
    """The settings were saved by another change after this one was judged."""


@dataclass(frozen=True)
class RiskSettings:
    """The owner's risk settings, one whole version. Every guard is off by default."""

    daily_loss_limit: float | None = DEFAULTS["daily_loss_limit"]
    band_on: bool = DEFAULTS["band_on"]
    band_opt_pct: float = DEFAULTS["band_opt_pct"]
    band_opt_min_rs: float = DEFAULTS["band_opt_min_rs"]
    band_fut_pct: float = DEFAULTS["band_fut_pct"]
    band_cash_pct: float = DEFAULTS["band_cash_pct"]
    band_both_sides: bool = DEFAULTS["band_both_sides"]
    size_on: bool = DEFAULTS["size_on"]
    max_lots_fo: int = DEFAULTS["max_lots_fo"]
    max_qty_cash: int = DEFAULTS["max_qty_cash"]
    burst_on: bool = DEFAULTS["burst_on"]
    burst_n: int = DEFAULTS["burst_n"]
    burst_window_s: float = DEFAULTS["burst_window_s"]
    confirm_phrase: str = DEFAULTS["confirm_phrase"]
    revoke_mcp: bool = DEFAULTS["revoke_mcp"]
    updated_at: datetime | None = None
    updated_by: str | None = None

    @property
    def guards_on(self) -> bool:
        """Whether any order guard (daily loss, band, size, burst) is turned on."""
        return self.daily_loss_limit is not None or self.band_on or self.size_on or self.burst_on


SETTING_FIELDS = tuple(DEFAULTS)

#: KillSwitchState field -> risk_state column.
STATE_COLUMNS = {
    "on": "ks_on",
    "since": "ks_since",
    "surface": "ks_surface",
    "actor": "ks_actor",
    "activation_id": "ks_activation_id",
    "exit_all": "ks_exit_all",
    "running": "ks_running",
    "report_json": "ks_report",
    "agent_engaged_by_us": "agent_engaged_by_us",
}


@dataclass(frozen=True)
class KillSwitchState:
    """The kill switch, one whole version."""

    on: bool = False
    since: datetime | None = None
    surface: str | None = None
    actor: str | None = None
    activation_id: str | None = None
    exit_all: bool = False
    running: bool = False
    report_json: str | None = None
    agent_engaged_by_us: bool = False
    updated_at: datetime | None = None

    @property
    def report(self) -> dict | None:
        """What the last activation's steps did, as saved."""
        if not self.report_json:
            return None
        try:
            return json.loads(self.report_json)
        except ValueError:
            return None


@dataclass(frozen=True)
class DayLock:
    pipe: str
    ist_date: str
    at: datetime
    figure: float
    loss_limit: float


@dataclass(frozen=True)
class AuditEntry:
    id: int
    at: datetime
    last_at: datetime
    kind: str
    pipe: str | None
    surface: str | None
    actor: str | None
    activation_id: str | None
    count: int
    summary: str
    detail: dict | None


class RiskStore:
    """The risk database at ``path`` (``schema.default_path()`` when omitted)."""

    def __init__(self, path: Path | str | None = None):
        self._path = Path(path) if path else None
        self._engine = None
        self._factory = None
        self._lock = real_threading.Lock()
        self._cache = LockedTTLCache(maxsize=4, ttl=CACHE_TTL_S)
        self._pruned_on: str | None = None

    @property
    def path(self) -> Path:
        return self._path or default_path()

    # -- plumbing -----------------------------------------------------------------------------

    def _session(self):
        factory = self._factory
        if factory is None:
            engine, built = self._prepare()  # database work, outside any lock
            with self._lock:
                if self._factory is None:
                    self._engine, self._factory, engine = engine, built, None
                factory = self._factory
            if engine is not None:  # another caller prepared it first
                engine.dispose()
        return factory()

    def _prepare(self):
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_db_engine(f"sqlite:///{path}")
        try:
            create_missing(engine)  # safe beside another first use (a thread, a process)
            factory = sessionmaker(bind=engine, expire_on_commit=False)
            with factory() as session:
                for model in (SettingsRow, StateRow):
                    if session.get(model, 1) is None:
                        session.add(model(id=1))
                        try:
                            session.commit()
                        except IntegrityError:
                            session.rollback()  # the other process inserted it first
        except BaseException:
            engine.dispose()  # the next use prepares afresh
            raise
        return engine, factory

    def dispose(self) -> None:
        """Close the engine (tests; the app keeps one for its life)."""
        with self._lock:
            engine, self._engine, self._factory = self._engine, None, None
        self._cache.clear()
        if engine is not None:
            engine.dispose()

    # -- settings -----------------------------------------------------------------------------

    def settings(self) -> RiskSettings:
        """The owner's settings, one whole version."""
        return self._cache.get_or_load("settings", self._load_settings)

    def _load_settings(self) -> RiskSettings:
        with self._session() as session:
            row = session.get(SettingsRow, 1)
            if row is None:
                return RiskSettings()
            return RiskSettings(
                **{name: getattr(row, name) for name in SETTING_FIELDS},
                updated_at=_from_db(row.updated_at),
                updated_by=row.updated_by,
            )

    def save_settings(self, by: str, *, since: Any = _ANY, **changes: Any) -> RiskSettings:
        """Change some settings; returns the version saved.

        ``since`` is the ``updated_at`` of the version a change was judged against (whether it
        loosens anything). The change is then saved only while that is still the saved version,
        checked in the one UPDATE that writes it, so two changes judged against the same version
        cannot both be saved; the second raises ``SettingsChanged``.
        """
        unknown = set(changes) - set(SETTING_FIELDS)
        if unknown:
            raise ValueError(f"not a risk setting: {sorted(unknown)}")
        values = {**changes, "updated_at": _to_db(utcnow()), "updated_by": _clip(by, 80)}
        statement = update(SettingsRow).where(SettingsRow.id == 1)
        if since is not _ANY:
            judged = _to_db(since)
            statement = statement.where(
                SettingsRow.updated_at.is_(None)
                if judged is None
                else SettingsRow.updated_at == judged
            )
        try:
            with self._session() as session:
                done = session.execute(statement.values(**values))
                if done.rowcount == 0:
                    if since is not _ANY and session.get(SettingsRow, 1) is not None:
                        raise SettingsChanged
                    session.add(SettingsRow(id=1, **values))
                session.commit()
        finally:
            self._cache.invalidate("settings")  # after the commit, never before
        return self.settings()

    # -- the kill switch ----------------------------------------------------------------------

    def state(self) -> KillSwitchState:
        """The kill switch as saved, one whole version."""
        return self._cache.get_or_load("state", self._load_state)

    def _load_state(self) -> KillSwitchState:
        with self._session() as session:
            row = session.get(StateRow, 1)
            if row is None:
                return KillSwitchState()
            values = {name: getattr(row, column) for name, column in STATE_COLUMNS.items()}
            values["since"] = _from_db(values["since"])
            return KillSwitchState(**values, updated_at=_from_db(row.updated_at))

    def save_state(self, **changes: Any) -> KillSwitchState:
        """Change some of the kill switch's fields; returns the version saved."""
        unknown = set(changes) - set(STATE_COLUMNS)
        if unknown:
            raise ValueError(f"not a kill switch field: {sorted(unknown)}")
        values = {STATE_COLUMNS[name]: value for name, value in changes.items()}
        if "ks_since" in values:
            values["ks_since"] = _to_db(values["ks_since"])
        values["updated_at"] = _to_db(utcnow())
        with self._session() as session:
            done = session.execute(update(StateRow).where(StateRow.id == 1).values(**values))
            if done.rowcount == 0:
                session.add(StateRow(id=1, **values))
            session.commit()
        self._cache.invalidate("state")  # after the commit, never before
        return self.state()

    # -- daily locks --------------------------------------------------------------------------

    def day_lock(self, pipe: str, day: str) -> DayLock | None:
        """The lock written for ``pipe`` on ``day`` (YYYY-MM-DD, India), if any."""
        with self._session() as session:
            row = session.get(DailyLockRow, (pipe, day))
            if row is None:
                return None
            return DayLock(row.pipe, row.ist_date, _from_db(row.at), row.figure, row.loss_limit)

    def lock_day(self, pipe: str, day: str, figure: float, loss_limit: float) -> DayLock:
        """Write the lock for ``pipe`` on ``day`` unless one is there; returns the one kept."""
        stmt = sqlite_insert(DailyLockRow).values(
            pipe=pipe, ist_date=day, at=_to_db(utcnow()), figure=figure, loss_limit=loss_limit
        )
        with self._session() as session:
            session.execute(stmt.on_conflict_do_nothing(index_elements=["pipe", "ist_date"]))
            session.commit()
        return self.day_lock(pipe, day)

    # -- the audit ----------------------------------------------------------------------------

    def audit(
        self,
        kind: str,
        summary: str,
        *,
        pipe: str | None = None,
        surface: str | None = None,
        actor: str | None = None,
        activation_id: str | None = None,
        detail: dict | None = None,
        merge_key: str | None = None,
        count: int = 1,
    ) -> None:
        """Append what the risk layer did, ``count`` times over. With ``merge_key``, a row written
        for the same key in the last ``REFUSAL_MERGE_S`` seconds is counted up instead."""
        now = utcnow()
        with self._session() as session:
            row = None
            if merge_key:
                since = _to_db(now - timedelta(seconds=REFUSAL_MERGE_S))
                row = session.execute(
                    select(AuditRow)
                    .where(AuditRow.merge_key == _clip(merge_key, 200), AuditRow.last_at >= since)
                    .order_by(AuditRow.id.desc())
                    .limit(1)
                ).scalar_one_or_none()
            if row is not None:
                row.count += count
                row.last_at = _to_db(now)
                row.summary = summary
            else:
                session.add(
                    AuditRow(
                        at=_to_db(now),
                        last_at=_to_db(now),
                        kind=kind,
                        pipe=pipe,
                        surface=_clip(surface, 120),
                        actor=_clip(actor, 80),
                        activation_id=activation_id,
                        count=count,
                        merge_key=_clip(merge_key, 200),
                        summary=summary,
                        detail=None if detail is None else json.dumps(detail, default=str),
                    )
                )
            session.commit()
        self._prune(now)

    def _prune(self, now: datetime) -> None:
        """Drop audit rows older than ``AUDIT_KEEP``, at most once a day."""
        today = ist_date(now)
        with self._lock:
            if self._pruned_on == today:
                return
            self._pruned_on = today
        try:
            with self._session() as session:
                session.execute(delete(AuditRow).where(AuditRow.last_at < _to_db(now - AUDIT_KEEP)))
                session.commit()
        except Exception:
            logger.exception("Old risk audit rows could not be pruned")
            with self._lock:
                self._pruned_on = None

    def history(self, limit: int = 50) -> list[AuditEntry]:
        """The newest audit rows first."""
        with self._session() as session:
            rows = session.execute(
                select(AuditRow).order_by(AuditRow.id.desc()).limit(max(1, min(int(limit), 500)))
            ).scalars()
            return [
                AuditEntry(
                    id=row.id,
                    at=_from_db(row.at),
                    last_at=_from_db(row.last_at),
                    kind=row.kind,
                    pipe=row.pipe,
                    surface=row.surface,
                    actor=row.actor,
                    activation_id=row.activation_id,
                    count=row.count,
                    summary=row.summary,
                    detail=_detail(row.detail),
                )
                for row in rows
            ]


def _detail(text: str | None) -> dict | None:
    if not text:
        return None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    return value if isinstance(value, dict) else {"value": value}


STORE = RiskStore()
