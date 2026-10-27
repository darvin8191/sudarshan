"""market.db, the market alerts' record (M10.4): definitions, state, the log, settings, pauses.

``$STATE_DIRECTORY/market.db`` (directory 700, file 600), beside the order alerts' alerts.db and
not inside it: a definition must never silently become memory-only (alerts.db falls back to
memory on a write failure; here a failed save is refused in the owner's words while the engine
keeps checking from memory), the two keep their history for different times, and this file
migrates by PRAGMA user_version (migrations.py).

The engine comes from OpenAlgo's database.engine_factory (NullPool: a connection per
transaction, closed at once); WAL and synchronous=NORMAL come from the listener in
database/__init__.py. This engine also emits its own BEGIN, so a migration's DDL and every write
are whole transactions.

Once only: a fire is recorded under its claim (engine.claim), which is UNIQUE. A claim already
there means the fire was told before, and ``record_fire`` says so instead of recording it twice;
the fire's state and any stop it causes are written in the same transaction.

Principal: every public method takes ``principal`` and refuses one the store was not opened for;
every owner-data table carries it and every read filters on it. A structural test holds each
public method to this.

Retention: events with their deliveries, and pauses, older than ``keep_days`` (100, never under
90) go at start and after each session; baselines after 10 days; definitions never.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Column,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    and_,
    delete,
    event,
    insert,
    or_,
    select,
    update,
)
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from database.engine_factory import create_db_engine
from sudarshan.alerts.market import migrations
from sudarshan.alerts.market.clock import Pause
from sudarshan.alerts.market.engine import Fire, State
from sudarshan.alerts.store import _cause, _private_file

DB_FILENAME = "market.db"
KEEP_DAYS, MIN_KEEP_DAYS, BASELINE_DAYS = 100, 90, 10
DAY_MS = 86_400_000
STATUSES = ("active", "paused", "stopped")
DELIVERY = ("pending", "sent", "not_sent", "off")
OFF_REASON = "Off in your alert settings"
DEFINITION = (
    "kind", "symbol", "exchange", "instrument_key", "tick_size", "expiry", "params", "frequency",
    "candle_min", "cooldown_s", "ends_at", "note", "channels",
)  # fmt: skip

meta = MetaData()
alert = Table(
    "alert", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    *(Column(n, String) for n in ("principal", "kind", "symbol", "exchange", "instrument_key")),
    Column("tick_size", String), Column("expiry", String), Column("params", String),
    Column("frequency", String), Column("candle_min", Integer), Column("cooldown_s", Integer),
    Column("ends_at", Integer), Column("status", String), Column("status_reason", String),
    Column("note", String), Column("channels", String), Column("created_at", Integer),
    Column("updated_at", Integer), Column("version", Integer),
)  # fmt: skip
STATE_FIELDS = State._fields[:9]
alert_state = Table(
    "alert_state", meta,
    Column("alert_id", Integer, primary_key=True), Column("principal", String),
    *(Column(name, Integer) for name in STATE_FIELDS), Column("updated_at", Integer),
)  # fmt: skip
alert_event = Table(
    "alert_event", meta,
    Column("id", Integer, primary_key=True, autoincrement=True), Column("principal", String),
    Column("alert_id", Integer), Column("session", Integer), Column("fired_at", Integer),
    Column("trade_time", Integer),
    *(Column(n, String) for n in ("symbol", "exchange", "kind", "title", "line", "value", "level")),
    Column("found_later", Integer), Column("opening", Integer), Column("test", Integer),
    Column("claim", String, unique=True),
)  # fmt: skip
alert_delivery = Table(
    "alert_delivery", meta,
    Column("event_id", Integer, primary_key=True), Column("channel", String, primary_key=True),
    Column("principal", String), Column("status", String), Column("reason", String),
    Column("attempts", Integer), Column("updated_at", Integer),
)  # fmt: skip
prefs_table = Table(
    "prefs", meta,
    Column("principal", String, primary_key=True), Column("telegram", Integer),
    Column("app", Integer), Column("updated_at", Integer),
)  # fmt: skip
baseline_table = Table(
    "baseline", meta,
    Column("instrument_key", String, primary_key=True), Column("session", Integer, primary_key=True),
    Column("days", Integer), Column("value", Float), Column("fetched_at", Integer),
)  # fmt: skip
pause_table = Table(
    "pause", meta,
    Column("id", Integer, primary_key=True, autoincrement=True), Column("started_at", Integer),
    Column("ended_at", Integer), Column("cause", String),
)  # fmt: skip
meta_table = Table("meta", meta, Column("name", String, primary_key=True), Column("value", Integer))


class StoreError(RuntimeError):
    """The record refused; ``str()`` is a sentence for the owner."""


class NotFound(StoreError):
    def __init__(self) -> None:
        super().__init__(
            "This alert is no longer there; it may have been deleted on another screen."
        )


class VersionConflict(StoreError):
    def __init__(self) -> None:
        super().__init__("This alert was changed on another screen. Open it again, then edit it.")


class UnknownPrincipal(ValueError):
    """A caller asked for someone this store does not serve (a programming error)."""


def engine(path: Path) -> Engine:
    """market.db's engine: NullPool from the factory, and a real BEGIN for every transaction."""
    made = create_db_engine(f"sqlite:///{path}")

    @event.listens_for(made, "connect")
    def _no_driver_begin(dbapi_connection, record):
        dbapi_connection.isolation_level = None  # SQLAlchemy's "begin" below opens them instead

    @event.listens_for(made, "begin")
    def _begin(conn):
        conn.exec_driver_sql("BEGIN")

    return made


class MarketStore:
    def __init__(self, path: Path | str, principals=("owner",), keep_days: int = KEEP_DAYS):
        self.path = Path(path)
        self.principals = frozenset(principals)
        self.keep_days = max(MIN_KEEP_DAYS, int(keep_days))
        try:
            _private_file(self.path)
            self.engine = engine(self.path)
        except OSError as exc:
            raise StoreError(_words("opened", exc)) from exc
        try:
            self.applied = migrations.migrate(self.engine)
        except (OSError, SQLAlchemyError) as exc:
            self.engine.dispose()
            raise StoreError(_words("opened", exc)) from exc
        except BaseException:  # SchemaTooNew, or a step's own failure: nothing is kept open
            self.engine.dispose()
            raise

    # ---- definitions -----------------------------------------------------------------------
    def create(self, principal: str, fields: Mapping[str, Any], now: int) -> dict[str, Any]:
        """A new Active alert from spec.check's fields; returns the stored row."""
        values = _definition(fields) | {"principal": self._who(principal), "status": "active",
                                        "status_reason": "", "created_at": now,
                                        "updated_at": now, "version": 1}  # fmt: skip
        with self._tx() as conn:
            alert_id = conn.execute(insert(alert).values(**values)).inserted_primary_key[0]
        return self.alert(principal, alert_id)

    def update(
        self,
        principal: str,
        alert_id: int,
        version: int,
        fields: Mapping[str, Any],
        now: int,
        *,
        status: str,
        status_reason: str = "",
        reset_state: bool = False,
    ) -> dict[str, Any]:
        """An edit, a pause or a resume by the owner, only from ``version`` (else a conflict).

        ``reset_state`` (spec.changes_condition) forgets the alert's state in the same step."""
        _status(status)
        who = self._who(principal)
        mine = and_(alert.c.id == alert_id, alert.c.principal == who)
        with self._tx() as conn:
            changed = conn.execute(
                update(alert)
                .where(mine, alert.c.version == version)
                .values(
                    **_definition(fields),
                    status=status,
                    status_reason=status_reason,
                    updated_at=now,
                    version=version + 1,
                )  # fmt: skip
            ).rowcount
            if not changed:
                there = conn.execute(select(alert.c.id).where(mine)).first()
                raise VersionConflict() if there else NotFound()
            if reset_state:
                conn.execute(delete(alert_state).where(alert_state.c.alert_id == alert_id))
        return self.alert(principal, alert_id)

    def set_status(self, principal: str, alert_id: int, status: str, reason: str, now: int) -> bool:
        """The engine stopped or paused an alert itself (fired once, ended, 50 fires)."""
        _status(status)
        with self._tx() as conn:
            return _set_status(conn, self._who(principal), alert_id, status, reason, now)

    def delete(self, principal: str, alert_id: int) -> bool:
        """The alert and its state go; its history stays in the log."""
        who = self._who(principal)
        with self._tx() as conn:
            gone = conn.execute(
                delete(alert).where(alert.c.id == alert_id, alert.c.principal == who)
            ).rowcount
            conn.execute(
                delete(alert_state).where(
                    alert_state.c.alert_id == alert_id, alert_state.c.principal == who
                )
            )
        return bool(gone)

    def alert(self, principal: str, alert_id: int) -> dict[str, Any] | None:
        query = select(alert).where(
            alert.c.id == alert_id, alert.c.principal == self._who(principal)
        )
        with self._read() as conn:
            row = conn.execute(query).mappings().first()
        return _alert_row(row) if row else None

    def alerts(self, principal: str, status: str | None = None) -> list[dict[str, Any]]:
        query = select(alert).where(alert.c.principal == self._who(principal))
        if status is not None:
            query = query.where(alert.c.status == _status(status))
        with self._read() as conn:
            rows = conn.execute(query.order_by(alert.c.id)).mappings().all()
        return [_alert_row(row) for row in rows]

    # ---- state -----------------------------------------------------------------------------
    def states(self, principal: str) -> dict[int, State]:
        query = select(alert_state).where(alert_state.c.principal == self._who(principal))
        with self._read() as conn:
            rows = conn.execute(query).mappings().all()
        return {row["alert_id"]: State(*(row[name] for name in STATE_FIELDS)) for row in rows}

    def save_states(self, principal: str, states: Mapping[int, State], now: int) -> int:
        """The 1 s flush: each changed state, for the alerts that still exist."""
        who = self._who(principal)
        with self._tx() as conn:
            known = set(conn.execute(select(alert.c.id).where(alert.c.principal == who)).scalars())
            saved = 0
            for alert_id, state in states.items():
                if alert_id in known:
                    _put_state(conn, who, alert_id, state, now)
                    saved += 1
        return saved

    # ---- the log ---------------------------------------------------------------------------
    def record_fire(
        self,
        principal: str,
        fire: Fire,
        state: State,
        *,
        title: str,
        line: str,
        value: str,
        level: str,
        channels: Mapping[str, bool],
        status: str | None = None,
        status_reason: str = "",
    ) -> int | None:
        """One fire with its state (and its stop), once: the event id, or None when its claim
        was already told."""
        who = self._who(principal)
        with self._tx() as conn:
            row = conn.execute(
                select(alert.c.symbol, alert.c.exchange, alert.c.kind).where(
                    alert.c.id == fire.alert_id, alert.c.principal == who
                )
            ).first()
            if row is None:
                raise NotFound()
            event_id = _log(conn, who, fire.claim, channels, fire.at, {
                "alert_id": fire.alert_id, "session": fire.session, "fired_at": fire.at,
                "trade_time": fire.trade_time, "symbol": row.symbol, "exchange": row.exchange,
                "kind": row.kind, "title": title, "line": line, "value": value, "level": level,
                "found_later": int(fire.found_later is not None), "opening": int(fire.opening),
                "test": 0,
            })  # fmt: skip
            _put_state(conn, who, fire.alert_id, state, fire.at)
            if status is not None:
                _set_status(conn, who, fire.alert_id, _status(status), status_reason, fire.at)
        return event_id

    def record_test(
        self, principal: str, title: str, line: str, now: int, channels: Mapping[str, bool]
    ) -> int:
        """A test from the Settings sheet, logged like a fire so its deliveries show."""
        who = self._who(principal)
        with self._tx() as conn:
            return _log(conn, who, f"test:{uuid.uuid4().hex}", channels, now, {
                "alert_id": 0, "session": 0, "fired_at": now, "trade_time": None,
                "symbol": "", "exchange": "", "kind": "test", "title": title, "line": line,
                "value": None, "level": None, "found_later": 0, "opening": 0, "test": 1,
            })  # fmt: skip

    def set_delivery(
        self,
        principal: str,
        event_id: int,
        channel: str,
        status: str,
        reason: str,
        now: int,
        attempts: int | None = None,
    ) -> bool:
        """A channel's result for one event (the outbox's on_result, the push's answer)."""
        if status not in DELIVERY:
            raise ValueError(f"not a delivery status: {status!r}")
        values = {"status": status, "reason": reason, "updated_at": now}
        if attempts is not None:
            values["attempts"] = attempts
        key = alert_delivery.c
        with self._tx() as conn:
            return bool(
                conn.execute(
                    update(alert_delivery)
                    .where(
                        key.event_id == event_id,
                        key.channel == channel,
                        key.principal == self._who(principal),
                    )  # fmt: skip
                    .values(**values)
                ).rowcount
            )

    def events(
        self,
        principal: str,
        *,
        before: int | None = None,
        limit: int = 50,
        alert_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """The log, newest first, a page at a time, with each event's deliveries."""
        query = select(alert_event).where(alert_event.c.principal == self._who(principal))
        if before is not None:
            query = query.where(alert_event.c.id < before)
        if alert_id is not None:
            query = query.where(alert_event.c.alert_id == alert_id)
        query = query.order_by(alert_event.c.id.desc()).limit(max(1, min(int(limit), 200)))
        return self._with_deliveries(principal, query)

    def events_since(self, principal: str, after: int, limit: int = 200) -> list[dict[str, Any]]:
        """What fired after event ``after``, oldest first: the app catching up on reconnect."""
        query = (
            select(alert_event)
            .where(alert_event.c.principal == self._who(principal), alert_event.c.id > after)
            .order_by(alert_event.c.id)
            .limit(max(1, min(int(limit), 500)))
        )
        return self._with_deliveries(principal, query)

    def pending(self, principal: str, channel: str) -> list[dict[str, Any]]:
        """Events whose delivery on ``channel`` is still pending (after a restart)."""
        who = self._who(principal)
        query = (
            select(alert_event)
            .join(alert_delivery, alert_delivery.c.event_id == alert_event.c.id)
            .where(
                alert_event.c.principal == who,
                alert_delivery.c.channel == channel,
                alert_delivery.c.status == "pending",
            )  # fmt: skip
            .order_by(alert_event.c.id)
        )
        return self._with_deliveries(principal, query)

    # ---- settings and market facts ---------------------------------------------------------
    def prefs(self, principal: str) -> dict[str, bool]:
        query = select(prefs_table).where(prefs_table.c.principal == self._who(principal))
        with self._read() as conn:
            row = conn.execute(query).mappings().first()
        return {"telegram": bool(row["telegram"]), "app": bool(row["app"])} if row else {
            "telegram": True, "app": True}  # fmt: skip

    def set_prefs(self, principal: str, *, telegram: bool, app: bool, now: int) -> dict[str, bool]:
        values = {"telegram": int(bool(telegram)), "app": int(bool(app)), "updated_at": now}
        stmt = sqlite_insert(prefs_table).values(principal=self._who(principal), **values)
        with self._tx() as conn:
            conn.execute(stmt.on_conflict_do_update(index_elements=["principal"], set_=values))
        return self.prefs(principal)

    def put_baseline(
        self, principal: str, key: str, session: int, days: int, value: float, now: int
    ) -> None:
        """An instrument's N-day average volume for a session (volume alerts)."""
        self._who(principal)
        values = {"days": days, "value": value, "fetched_at": now}
        stmt = sqlite_insert(baseline_table).values(instrument_key=key, session=session, **values)
        with self._tx() as conn:
            conn.execute(
                stmt.on_conflict_do_update(
                    index_elements=["instrument_key", "session"], set_=values
                )
            )

    def baseline(self, principal: str, key: str, session: int) -> tuple[int, float] | None:
        self._who(principal)
        query = select(baseline_table.c.days, baseline_table.c.value).where(
            baseline_table.c.instrument_key == key, baseline_table.c.session == session
        )
        with self._read() as conn:
            row = conn.execute(query).first()
        return (row.days, row.value) if row else None

    def open_pause(self, principal: str, started_at: int, cause: str) -> int:
        self._who(principal)
        with self._tx() as conn:
            stmt = insert(pause_table).values(started_at=started_at, cause=cause)
            return conn.execute(stmt).inserted_primary_key[0]

    def close_pause(self, principal: str, pause_id: int, ended_at: int) -> None:
        self._who(principal)
        with self._tx() as conn:
            conn.execute(
                update(pause_table).where(pause_table.c.id == pause_id).values(ended_at=ended_at)
            )

    def pauses(self, principal: str, since: int) -> tuple[Pause, ...]:
        """Pauses that reach past ``since`` (the session's start), oldest first."""
        self._who(principal)
        p = pause_table.c
        query = select(p.started_at, p.ended_at, p.cause).where(
            or_(p.started_at >= since, p.ended_at.is_(None), p.ended_at > since)
        )
        with self._read() as conn:
            rows = conn.execute(query.order_by(p.started_at, p.id)).all()
        return tuple(Pause(row.started_at, row.ended_at, row.cause) for row in rows)

    def seen(self, principal: str) -> int | None:
        """The last moment market alerts were known to be checking (written every 10 s)."""
        self._who(principal)
        query = select(meta_table.c.value).where(meta_table.c.name == "market_seen")
        with self._read() as conn:
            return conn.execute(query).scalar()

    def set_seen(self, principal: str, at: int) -> None:
        self._who(principal)
        stmt = sqlite_insert(meta_table).values(name="market_seen", value=at)
        with self._tx() as conn:
            conn.execute(stmt.on_conflict_do_update(index_elements=["name"], set_={"value": at}))

    # ---- upkeep ----------------------------------------------------------------------------
    def prune(self, now: int) -> int:
        """Drop events, deliveries and pauses older than keep_days, baselines over 10 days."""
        cutoff = now - self.keep_days * DAY_MS
        old = select(alert_event.c.id).where(alert_event.c.fired_at < cutoff)
        with self._tx() as conn:
            removed = conn.execute(
                delete(alert_delivery).where(alert_delivery.c.event_id.in_(old))
            ).rowcount
            removed += conn.execute(
                delete(alert_event).where(alert_event.c.fired_at < cutoff)
            ).rowcount
            removed += conn.execute(
                delete(pause_table).where(pause_table.c.started_at < cutoff)
            ).rowcount
            removed += conn.execute(
                delete(baseline_table).where(
                    baseline_table.c.fetched_at < now - BASELINE_DAYS * DAY_MS
                )
            ).rowcount
        return removed

    def close(self) -> None:
        self.engine.dispose()

    # ---- inside ----------------------------------------------------------------------------
    def _who(self, principal: str) -> str:
        if principal not in self.principals:
            raise UnknownPrincipal(f"this store does not serve {principal!r}")
        return principal

    def _with_deliveries(self, principal: str, query) -> list[dict[str, Any]]:
        who = self._who(principal)
        with self._read() as conn:
            rows = [dict(row) for row in conn.execute(query).mappings().all()]
            ids = [row["id"] for row in rows]
            found: dict[int, dict] = {i: {} for i in ids}
            if ids:
                d = alert_delivery.c
                query = select(alert_delivery).where(d.event_id.in_(ids), d.principal == who)
                for row in conn.execute(query).mappings():
                    found[row["event_id"]][row["channel"]] = {
                        name: row[name] for name in ("status", "reason", "attempts", "updated_at")
                    }
        for row in rows:
            for flag in ("found_later", "opening", "test"):
                row[flag] = bool(row[flag])
            row["deliveries"] = found[row["id"]]
        return rows

    @contextlib.contextmanager
    def _tx(self) -> Iterator:
        try:
            with self.engine.begin() as conn:
                yield conn
        except (OSError, SQLAlchemyError) as exc:
            raise StoreError(_words("written", exc)) from exc

    @contextlib.contextmanager
    def _read(self) -> Iterator:
        try:
            with self.engine.connect() as conn:
                yield conn
        except (OSError, SQLAlchemyError) as exc:
            raise StoreError(_words("read", exc)) from exc


def _words(done: str, exc: BaseException) -> str:
    return (
        f"The market alert record could not be {done} ({_cause(exc)}). Your alerts are still "
        "checked; changes cannot be saved until this is fixed."
    )


def _status(status: str) -> str:
    if status not in STATUSES:
        raise ValueError(f"not an alert status: {status!r}")
    return status


def _definition(fields: Mapping[str, Any]) -> dict[str, Any]:
    values = {name: fields.get(name) for name in DEFINITION}
    values["params"] = json.dumps(fields["params"], sort_keys=True, separators=(",", ":"))
    if values["channels"] is not None:
        values["channels"] = json.dumps(values["channels"], sort_keys=True)
    values["note"] = values["note"] or ""
    values["cooldown_s"] = values["cooldown_s"] or 0
    return values


def _alert_row(row) -> dict[str, Any]:
    out = dict(row)
    out["params"] = json.loads(out["params"])
    out["channels"] = json.loads(out["channels"]) if out["channels"] else None
    return out


def _set_status(conn, who: str, alert_id: int, status: str, reason: str, now: int) -> bool:
    return bool(
        conn.execute(
            update(alert)
            .where(alert.c.id == alert_id, alert.c.principal == who)
            .values(
                status=status, status_reason=reason, updated_at=now, version=alert.c.version + 1
            )  # fmt: skip
        ).rowcount
    )


def _put_state(conn, who: str, alert_id: int, state: State, now: int) -> None:
    values = dict(zip(STATE_FIELDS, state[:9], strict=True)) | {"updated_at": now}
    stmt = sqlite_insert(alert_state).values(alert_id=alert_id, principal=who, **values)
    conn.execute(stmt.on_conflict_do_update(index_elements=["alert_id"], set_=values))


def _log(conn, who: str, claim: str, channels: Mapping[str, bool], now: int, values) -> int | None:
    """The event under its claim, with a delivery row per channel; None if already told."""
    stmt = sqlite_insert(alert_event).values(principal=who, claim=claim, **values)
    result = conn.execute(stmt.on_conflict_do_nothing(index_elements=["claim"]))
    if result.rowcount != 1:
        return None
    event_id = result.lastrowid
    for channel, on in channels.items():
        conn.execute(
            insert(alert_delivery).values(
                event_id=event_id,
                channel=channel,
                principal=who,
                status="pending" if on else "off",
                reason="" if on else OFF_REASON,
                attempts=0,
                updated_at=now,
            )  # fmt: skip
        )
    return event_id
