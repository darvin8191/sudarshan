"""signals.db, the TRINETRA view's records (T3, design 3.5 and 6.2).

``$STATE_DIRECTORY/signals.db`` (folder 750, file 640, made before SQLite can touch them). The
engine is OpenAlgo's ``database.engine_factory.create_db_engine`` (NullPool: a connection per
transaction, closed at once; WAL and synchronous=NORMAL from ``database/__init__.py``), and it
emits its own BEGIN, IMMEDIATE for a write, as the journal's does.

One writer. Every call runs on the store's one thread (``SignalsStore.call`` from the service's
event loop; a method called directly runs on the caller's thread, as tests and ``check`` do). The
service is the only writer; ``migrate --status`` opens the file read-only.

What leaves the store is the contract's shapes (``Change``, ``AlertRow``, ``Settings``) as dicts.
Failures reach the owner as ``StoreError`` sentences; SQLite's own words are in ``.cause`` and the
log only.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import json
import logging
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import SQLAlchemyError

from database.engine_factory import create_db_engine
from sudarshan.alerts.owner import database_urls
from sudarshan.signals import contract as c
from sudarshan.signals import migrations
from sudarshan.signals.diff import ChangeIn, Current, Key, ist_day

log = logging.getLogger("sudarshan.signals")

DB_FILENAME = c.DB_FILENAME
STATE_DIR = Path(c.STATE_DIR)
THREAD_PREFIX = "signals-store"
DOC_KEEP_MS = 30 * 86_400_000
COUNTER_KEEP_DAYS = 40
COUNTERS = ("refused", "gaps", "strangers", "repeat", "older", "newer_format", "overflow")
DEFAULT_SETTINGS = {"lang": c.DEFAULT_LANG, "relay": True, "night": True, "version": 1}
CHANGE_FIELDS = (
    "id", "at", "recorded_ms", "module", "instrument", "kind", "from_state", "from_label",
    "from_bias", "to_state", "to_label", "bias", "score", "confidence", "quality", "horizon",
    "since_ms", "as_of_ms", "valid_until_ms", "levels_json", "inputs_json", "why_json",
)  # fmt: skip
WORDS = {
    "opened": c.WORDS["store_failed"],
    "written": "The TRINETRA view could not save this. Nothing was changed; try again in a "
    "minute. If it keeps failing, check that the server has free disk space.",
    "read": "The TRINETRA view's records could not be read just now. Try again in a minute.",
}


class StoreError(RuntimeError):
    """The record refused; ``str()`` is a sentence for the owner, ``cause`` the system's words."""

    def __init__(self, message: str, cause: str = ""):
        super().__init__(message)
        self.cause = cause


class VersionConflict(StoreError):
    def __init__(self) -> None:
        super().__init__(c.WORDS["version_conflict"])


def default_path() -> Path:
    """signals.db under the service's state directory (systemd's StateDirectory=, or the
    setting, else /var/lib/sudarshan-signals); a list of directories takes its first."""
    base = os.environ.get("SUDARSHAN_SIGNALS_STATE_DIR") or os.environ.get("STATE_DIRECTORY")
    return Path((base or str(STATE_DIR)).split(":")[0]) / DB_FILENAME


def cause_of(exc: BaseException | None) -> str:
    """The failure in words: the system's or SQLite's own message, never a class name."""
    if isinstance(exc, OSError):
        return (exc.strerror or "the file cannot be written").lower()
    original = getattr(exc, "orig", None)
    return str(original) if original else "the database refused the change"


def _make_file(path: Path) -> None:
    """The folder (750) and an empty file (640) before SQLite creates it with the umask."""
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    with contextlib.suppress(FileExistsError):
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o640))


def engine(path: Path) -> Engine:
    """NullPool from the factory, and a real BEGIN for every transaction (IMMEDIATE on a
    connection with the ``migrations.IMMEDIATE`` option)."""
    made = create_db_engine(f"sqlite:///{path}")

    @event.listens_for(made, "connect")
    def _no_driver_begin(dbapi_connection, record):
        dbapi_connection.isolation_level = None

    @event.listens_for(made, "begin")
    def _begin(conn):
        immediate = conn.get_execution_options().get(migrations.IMMEDIATE)
        conn.exec_driver_sql("BEGIN IMMEDIATE" if immediate else "BEGIN")

    return made


def open_read_only(path: Path) -> Engine:
    """An engine that cannot write, for ``--status``; never migrates."""
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


def _dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _loads(text: str | None, default: Any = None) -> Any:
    if text is None:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def change_record(row: Mapping[str, Any]) -> dict[str, Any]:
    """A ``change`` row as the contract's ``Change``."""
    return {
        "id": row["id"],
        "at_ms": row["at"],
        "recorded_ms": row["recorded_ms"],
        "module": row["module"],
        "instrument": row["instrument"],
        "kind": row["kind"],
        "from_state": row["from_state"],
        "from_label": row["from_label"],
        "from_bias": row["from_bias"],
        "to_state": row["to_state"],
        "to_label": row["to_label"],
        "bias": row["bias"],
        "score": row["score"],
        "confidence": row["confidence"],
        "quality": row["quality"],
        "horizon": row["horizon"],
        "since_ms": row["since_ms"],
        "as_of_ms": row["as_of_ms"],
        "valid_until_ms": row["valid_until_ms"],
        "levels": _loads(row["levels_json"]),
        "inputs": _loads(row["inputs_json"], []),
        "why": _loads(row["why_json"], dict.fromkeys(c.LANGS)),
    }


def alert_record(row: Mapping[str, Any]) -> dict[str, Any]:
    """An ``alert`` row as the contract's ``AlertRow``."""
    return {k: row[k] for k in ("id", "module", "instrument", "level", "title", "text", "lang")} | {
        "at_ms": row["at"]
    }


@dataclass
class Stored:
    """What one accepted document wrote: its new timeline rows and new alerts (contract shapes,
    with their relay state), so the service can hand them on."""

    changes: list[dict[str, Any]] = field(default_factory=list)
    alerts: list[dict[str, Any]] = field(default_factory=list)


class SignalsStore:
    """signals.db, opened (and migrated) once by the service."""

    def __init__(self, path: Path):
        self.path = Path(path)
        try:
            _make_file(self.path)
            self.engine = engine(self.path)
        except OSError as exc:
            raise StoreError(WORDS["opened"], cause_of(exc)) from exc
        self.writer = self.engine.execution_options(**{migrations.IMMEDIATE: True})
        self._pool: concurrent.futures.ThreadPoolExecutor | None = None
        try:
            self.applied = migrations.migrate(self.engine)
            self._seed()
        except migrations.SchemaTooNew:
            self.engine.dispose()
            raise
        except SQLAlchemyError as exc:
            self.engine.dispose()
            raise StoreError(WORDS["opened"], cause_of(exc)) from exc

    # ---- the one thread --------------------------------------------------------------------
    async def call(self, fn: Callable[..., Any], *args: Any) -> Any:
        """``fn(*args)`` on the store's one thread, awaited from the event loop."""
        if self._pool is None:
            self._pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=1, thread_name_prefix=THREAD_PREFIX
            )
        return await asyncio.get_running_loop().run_in_executor(self._pool, fn, *args)

    def close(self) -> None:
        """End the thread after its work and dispose the engine: nothing outlives the store."""
        pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown(wait=True)
        self.engine.dispose()

    def _read(self, fn: Callable[[Connection], Any]) -> Any:
        try:
            with self.engine.connect() as conn:
                return fn(conn)
        except SQLAlchemyError as exc:
            raise StoreError(WORDS["read"], cause_of(exc)) from exc

    def _write(self, fn: Callable[[Connection], Any]) -> Any:
        try:
            with self.writer.begin() as conn:
                return fn(conn)
        except SQLAlchemyError as exc:
            raise StoreError(WORDS["written"], cause_of(exc)) from exc

    # ---- meta ------------------------------------------------------------------------------
    @staticmethod
    def _meta(conn: Connection, key: str, default: Any = None) -> Any:
        row = conn.exec_driver_sql("SELECT value FROM meta WHERE key = ?", (key,)).first()
        return _loads(row[0], default) if row else default

    @staticmethod
    def _meta_put(conn: Connection, key: str, value: Any) -> None:
        conn.exec_driver_sql(
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, _dumps(value)),
        )

    def _seed(self) -> None:
        def seed(conn: Connection) -> None:
            if self._meta(conn, "settings") is None:
                self._meta_put(conn, "settings", DEFAULT_SETTINGS)

        self._write(seed)

    def meta_get(self, key: str, default: Any = None) -> Any:
        return self._read(lambda conn: self._meta(conn, key, default))

    def meta_set(self, key: str, value: Any) -> None:
        self._write(lambda conn: self._meta_put(conn, key, value))

    # ---- what the service keeps in memory -----------------------------------------------------
    def last(self) -> dict[str, Any] | None:
        """The last accepted document: run, seq, at, next_by, published_ms, fresh_until_ms,
        stopping, clock_ok, clock_diff_ms, received_ms."""
        return self.meta_get("last")

    def current(self) -> dict[Key, Current]:
        def read(conn: Connection) -> dict[Key, Current]:
            out = {}
            for row in conn.exec_driver_sql("SELECT * FROM now").mappings():
                reading = _loads(row["row_json"])
                if not isinstance(reading, dict):
                    continue
                entry = Current(reading, row["published_ms"], row["doc_seq"], row["offset_ms"],
                                bool(row["lapsed"]))  # fmt: skip
                out[entry.key] = entry
            return out

        return self._read(read)

    # ---- documents ---------------------------------------------------------------------------
    @staticmethod
    def _count(conn: Connection, day: int, kind: str, n: int = 1) -> None:
        conn.exec_driver_sql(
            "INSERT INTO counter (day, kind, n) VALUES (?, ?, ?) "
            "ON CONFLICT (day, kind) DO UPDATE SET n = n + excluded.n",
            (day, kind, n),
        )

    def count(self, kind: str, received_ms: int, n: int = 1) -> None:
        day = int(ist_day(received_ms).strftime("%Y%m%d"))
        self._write(lambda conn: self._count(conn, day, kind, n))

    def counts(self, now_ms: int) -> dict[str, int]:
        """Today's counters (IST day of ``now_ms``), every kind present."""
        day = int(ist_day(now_ms).strftime("%Y%m%d"))

        def read(conn: Connection) -> dict[str, int]:
            found = dict.fromkeys(COUNTERS, 0)
            for kind, n in conn.exec_driver_sql(
                "SELECT kind, n FROM counter WHERE day = ?", (day,)
            ):
                found[kind] = int(n)
            return found

        return self._read(read)

    def record(
        self,
        name: str,
        *,
        received_ms: int,
        size: int,
        verdict: str,
        reason: str | None = None,
        written_ms: int | None = None,
        snap: c.Snapshot | None = None,
        counter: str | None = None,
    ) -> None:
        """A file that was not accepted (refused, a repeat, older, a newer format): its row, and
        today's counter of ``counter`` when one is named."""
        day = int(ist_day(received_ms).strftime("%Y%m%d"))

        def write(conn: Connection) -> None:
            conn.exec_driver_sql(
                "INSERT INTO doc (name, run, seq, at, next_by, received_ms, written_ms, bytes, "
                "verdict, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (name, snap.run if snap else None, snap.seq if snap else None,
                 snap.at if snap else None, snap.next_by if snap else None, received_ms,
                 written_ms, size, verdict, reason),
            )  # fmt: skip
            if counter:
                self._count(conn, day, counter)

        self._write(write)

    def accept(
        self,
        snap: c.Snapshot,
        *,
        name: str,
        received_ms: int,
        current: Mapping[Key, Current],
        changes: Iterable[ChangeIn],
        gap: int = 0,
        relay_on: bool = True,
    ) -> Stored:
        """One accepted document in one transaction: its row, the current readings it leaves,
        its new timeline rows and alerts (an alert id seen before is ignored), the last-document
        note, and today's gap count."""
        day = int(ist_day(received_ms).strftime("%Y%m%d"))
        stored = Stored()

        def write(conn: Connection) -> Stored:
            offset = snap.published_ms - snap.at
            keys = [f"{r.module}/{r.instrument}" for r in snap.readings]
            conn.exec_driver_sql(
                "INSERT INTO doc (name, run, seq, at, next_by, received_ms, written_ms, bytes, "
                "verdict, clock, published_ms, fresh_until_ms, offset_ms, keys_json, stopping) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'accepted', ?, ?, ?, ?, ?, ?)",
                (name, snap.run, snap.seq, snap.at, snap.next_by, received_ms, snap.written_ms,
                 snap.size, snap.clock, snap.published_ms, snap.fresh_until_ms, offset,
                 _dumps(keys), int(snap.stopping)),
            )  # fmt: skip
            self._put_current(conn, current)
            for change in changes:
                stored.changes.append(self._insert_change(conn, change, received_ms, relay_on))
            for alert in snap.alerts:
                relay = "pending" if relay_on and alert.level in c.RELAYED_LEVELS else "skipped"
                made = conn.exec_driver_sql(
                    "INSERT OR IGNORE INTO alert (id, at, received_ms, module, instrument, level, "
                    "title, text, lang, relay) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (alert.id, snap.published_ms, received_ms, alert.module, alert.instrument,
                     alert.level, alert.title, alert.text, alert.lang, relay),
                ).rowcount  # fmt: skip
                if made:
                    stored.alerts.append(
                        {
                            "id": alert.id,
                            "at_ms": snap.published_ms,
                            "module": alert.module,
                            "instrument": alert.instrument,
                            "level": alert.level,
                            "title": alert.title,
                            "text": alert.text,
                            "lang": alert.lang,
                            "relay": relay,
                        }  # fmt: skip
                    )
            self._meta_put(conn, "last", {
                "run": snap.run, "seq": snap.seq, "at": snap.at, "next_by": snap.next_by,
                "published_ms": snap.published_ms, "fresh_until_ms": snap.fresh_until_ms,
                "stopping": snap.stopping, "clock_ok": snap.clock_ok,
                "clock_diff_ms": snap.clock_diff_ms, "received_ms": received_ms,
            })  # fmt: skip
            if gap > 0:
                self._count(conn, day, "gaps", gap)
            return stored

        return self._write(write)

    @staticmethod
    def _put_current(conn: Connection, current: Mapping[Key, Current]) -> None:
        conn.exec_driver_sql("DELETE FROM now")
        for (module, instrument), entry in current.items():
            conn.exec_driver_sql(
                "INSERT INTO now (module, instrument, row_json, published_ms, doc_seq, offset_ms, "
                "lapsed) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (module, instrument, _dumps(dict(entry.reading)), entry.published_ms,
                 entry.doc_seq, entry.offset_ms, int(entry.lapsed)),
            )  # fmt: skip

    @staticmethod
    def _insert_change(
        conn: Connection, change: ChangeIn, recorded_ms: int, relay_on: bool
    ) -> dict[str, Any]:
        row = change.as_row()
        relay = "pending" if relay_on and change.kind in c.RELAYED_KINDS else "skipped"
        new_id = conn.exec_driver_sql(
            "INSERT INTO change (at, recorded_ms, module, instrument, kind, from_state, "
            "from_label, from_bias, to_state, to_label, bias, score, confidence, quality, "
            "horizon, since_ms, as_of_ms, valid_until_ms, levels_json, inputs_json, why_json, "
            "relay) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (row["at_ms"], recorded_ms, row["module"], row["instrument"], row["kind"],
             row["from_state"], row["from_label"], row["from_bias"], row["to_state"],
             row["to_label"], row["bias"], row["score"], row["confidence"], row["quality"],
             row["horizon"], row["since_ms"], row["as_of_ms"], row["valid_until_ms"],
             None if row["levels"] is None else _dumps(row["levels"]), _dumps(row["inputs"]),
             _dumps(row["why"]), relay),
        ).lastrowid  # fmt: skip
        return {"id": new_id, "recorded_ms": recorded_ms, **row, "relay": relay}

    def store_lapses(
        self, current: Mapping[Key, Current], changes: Iterable[ChangeIn], recorded_ms: int
    ) -> list[dict[str, Any]]:
        """Lapses the clock brought: their rows (never relayed) and the readings marked."""

        def write(conn: Connection) -> list[dict[str, Any]]:
            made = [self._insert_change(conn, ch, recorded_ms, False) for ch in changes]
            self._put_current(conn, current)
            return made

        return self._write(write)

    # ---- reads for the ops -------------------------------------------------------------------
    def timeline(
        self,
        *,
        module: str | None = None,
        instrument: str | None = None,
        before: int | None = None,
        after_ms: int | None = None,
        limit: int = c.TIMELINE["page"],
    ) -> dict[str, Any]:
        """The contract's ``TimelinePage``: newest first, ``limit`` rows, and the id to ask
        ``before`` for the next page (None when there is no older row)."""
        where, args = [], []
        for column, value in (("module", module), ("instrument", instrument)):
            if value is not None:
                where.append(f"{column} = ?")
                args.append(value)
        if before is not None:
            where.append("id < ?")
            args.append(int(before))
        if after_ms is not None:
            where.append("at >= ?")
            args.append(int(after_ms))
        sql = "SELECT * FROM change" + (" WHERE " + " AND ".join(where) if where else "")
        sql += " ORDER BY id DESC LIMIT ?"
        args.append(int(limit) + 1)

        def read(conn: Connection) -> dict[str, Any]:
            rows = [change_record(r) for r in conn.exec_driver_sql(sql, tuple(args)).mappings()]
            more = len(rows) > limit
            rows = rows[:limit]
            return {"changes": rows, "next_before": rows[-1]["id"] if more and rows else None}

        return self._read(read)

    def change_get(self, change_id: int) -> dict[str, Any] | None:
        def read(conn: Connection) -> dict[str, Any] | None:
            row = (
                conn.exec_driver_sql("SELECT * FROM change WHERE id = ?", (int(change_id),))
                .mappings()
                .first()
            )
            return change_record(row) if row else None

        return self._read(read)

    def alerts_recent(self, since_ms: int, limit: int = c.STATE_ALERTS["max"]) -> list[dict]:
        def read(conn: Connection) -> list[dict]:
            rows = conn.exec_driver_sql(
                "SELECT * FROM alert WHERE received_ms >= ? ORDER BY received_ms DESC, at DESC "
                "LIMIT ?",
                (int(since_ms), int(limit)),
            ).mappings()
            return [alert_record(r) for r in rows]

        return self._read(read)

    def state_at(self, ts_ms: int) -> dict[str, Any]:
        """The contract's ``StateAt``: the readings in effect at ``ts_ms``, only when an accepted
        document stood then (its fresh window covered ``ts_ms``)."""

        def read(conn: Connection) -> dict[str, Any]:
            doc = (
                conn.exec_driver_sql(
                    "SELECT * FROM doc WHERE verdict = 'accepted' AND published_ms <= ? "
                    "ORDER BY published_ms DESC, id DESC LIMIT 1",
                    (int(ts_ms),),
                )
                .mappings()
                .first()
            )
            if doc is None or ts_ms >= (doc["fresh_until_ms"] or 0):
                return {"ts_ms": ts_ms, "fresh": False, "readings": []}
            readings = []
            for key in _loads(doc["keys_json"], []):
                module, _, instrument = key.partition("/")
                row = (
                    conn.exec_driver_sql(
                        "SELECT * FROM change WHERE module = ? AND instrument = ? AND at <= ? "
                        "ORDER BY id DESC LIMIT 1",
                        (module, instrument, int(ts_ms)),
                    )
                    .mappings()
                    .first()
                )
                if row is None:
                    continue
                until = row["valid_until_ms"]
                offset = doc["offset_ms"] or 0
                lapsed = row["kind"] == "lapsed" or (until is not None and until + offset <= ts_ms)
                readings.append({
                    "module": module, "instrument": instrument, "state": row["to_state"],
                    "label": row["to_label"], "bias": row["bias"],
                    "confidence": row["confidence"], "since_ms": row["since_ms"],
                    "lapsed": lapsed,
                })  # fmt: skip
            return {"ts_ms": ts_ms, "fresh": True, "readings": readings}

        return self._read(read)

    # ---- settings ----------------------------------------------------------------------------
    def settings(self) -> dict[str, Any]:
        found = self.meta_get("settings", DEFAULT_SETTINGS)
        return {**DEFAULT_SETTINGS, **(found if isinstance(found, dict) else {})}

    def settings_set(self, version: int, changes: Mapping[str, Any]) -> dict[str, Any]:
        """The owner's change, only from ``version``; it moves the version by one."""

        def write(conn: Connection) -> dict[str, Any]:
            have = {**DEFAULT_SETTINGS, **(self._meta(conn, "settings") or {})}
            if have["version"] != version:
                raise VersionConflict()
            new = {**have, **changes, "version": have["version"] + 1}
            self._meta_put(conn, "settings", new)
            return new

        try:
            with self.writer.begin() as conn:
                return write(conn)
        except SQLAlchemyError as exc:
            raise StoreError(WORDS["written"], cause_of(exc)) from exc

    # ---- the relay's seam (AL2's relay.py reads and marks these) ------------------------------
    def pending_relay(self, limit: int = 50) -> tuple[list[dict], list[dict]]:
        """The changes and alerts still to hand to the alerts' desk, oldest first."""

        def read(conn: Connection) -> tuple[list[dict], list[dict]]:
            changes = [
                change_record(r)
                for r in conn.exec_driver_sql(
                    "SELECT * FROM change WHERE relay = 'pending' ORDER BY id LIMIT ?", (limit,)
                ).mappings()
            ]
            alerts = [
                {**alert_record(r), "received_ms": r["received_ms"]}
                for r in conn.exec_driver_sql(
                    "SELECT * FROM alert WHERE relay = 'pending' ORDER BY received_ms LIMIT ?",
                    (limit,),
                ).mappings()
            ]
            return changes, alerts

        return self._read(read)

    def mark_relay(self, table: str, ids: Iterable[Any], state: str) -> int:
        """Set the relay state of rows of ``change`` or ``alert``; how many were changed."""
        if table not in ("change", "alert") or state not in c.RELAY_STATES:
            raise ValueError("a relay state of a change or an alert")
        ids = list(ids)

        def write(conn: Connection) -> int:
            return sum(
                conn.exec_driver_sql(
                    f"UPDATE {table} SET relay = ? WHERE id = ?", (state, ident)
                ).rowcount
                for ident in ids
            )

        return self._write(write) if ids else 0

    # ---- the score (T8) ----------------------------------------------------------------------
    def levels_put(self, found: Iterable[Mapping[str, Any]]) -> None:
        """Today's open (with the previous close) or close of each index; a figure already there
        is kept when the new reading has none."""

        def write(conn: Connection) -> None:
            for row in found:
                conn.exec_driver_sql(
                    "INSERT INTO level (day, instrument, open_px, prev_close_px, close_px, open_ms, "
                    "close_ms) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (day, instrument) DO UPDATE "
                    "SET open_px = COALESCE(excluded.open_px, open_px), prev_close_px = "
                    "COALESCE(excluded.prev_close_px, prev_close_px), close_px = "
                    "COALESCE(excluded.close_px, close_px), open_ms = COALESCE(excluded.open_ms, "
                    "open_ms), close_ms = COALESCE(excluded.close_ms, close_ms)",
                    (row["day"], row["instrument"], row.get("open_px"), row.get("prev_close_px"),
                     row.get("close_px"), row["at_ms"] if row.get("open_px") else None,
                     row["at_ms"] if row.get("close_px") else None),
                )  # fmt: skip

        self._write(write)

    def levels(self) -> dict[tuple[int, str], dict[str, Any]]:
        def read(conn: Connection) -> dict[tuple[int, str], dict[str, Any]]:
            return {
                (row["day"], row["instrument"]): dict(row)
                for row in conn.exec_driver_sql("SELECT * FROM level").mappings()
            }

        return self._read(read)

    def scored(self, module: str | None = None) -> list[dict[str, Any]]:
        """The timeline's changes the score counts: id, when, module, instrument, kind, bias."""
        sql = "SELECT id, at, module, instrument, kind, bias FROM change WHERE kind != 'lapsed'"
        args: tuple = ()
        if module is not None:
            sql, args = sql + " AND module = ?", (module,)

        def read(conn: Connection) -> list[dict[str, Any]]:
            return [
                {**dict(row), "at_ms": row["at"]}
                for row in conn.exec_driver_sql(sql + " ORDER BY id", args).mappings()
            ]

        return self._read(read)

    def fill_marks(self, is_trading_day: Callable[[Any], bool]) -> int:
        """Each scored change's index closes (session 0 its own day; 1, 3, 5 after) as the levels
        have them, and its ``mark_px`` (its day's close). How many marks were written."""
        from sudarshan.signals import perf

        changes, levels = self.scored(), self.levels()

        def write(conn: Connection) -> int:
            made = 0
            for ch in changes:
                if ch["kind"] not in perf.SCORED_KINDS:
                    continue
                start = perf.day0(ch["at_ms"], is_trading_day)
                days = {0: start} | {
                    k: perf.after(start, k, is_trading_day) for k in c.PERF["sessions"]
                }
                for session, day in days.items():
                    level = levels.get((perf.day_key(day), ch["instrument"])) if day else None
                    if not level or level.get("close_px") is None:
                        continue
                    made += conn.exec_driver_sql(
                        "INSERT OR IGNORE INTO mark (change_id, session_n, day, close_px, open_px) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (ch["id"], session, perf.day_key(day), level["close_px"],
                         level.get("open_px")),
                    ).rowcount  # fmt: skip
                    if session == 0:
                        conn.exec_driver_sql(
                            "UPDATE change SET mark_px = ? WHERE id = ? AND mark_px IS NULL",
                            (level["close_px"], ch["id"]),
                        )
            return made

        return self._write(write)

    # ---- keeping it small --------------------------------------------------------------------
    def prune(self, now_ms: int) -> dict[str, int]:
        """Documents older than 30 days and counters older than 40 days; the timeline, alerts and
        marks are kept for ever."""
        oldest_day = int(ist_day(now_ms - COUNTER_KEEP_DAYS * 86_400_000).strftime("%Y%m%d"))

        def write(conn: Connection) -> dict[str, int]:
            docs = conn.exec_driver_sql(
                "DELETE FROM doc WHERE received_ms < ?", (now_ms - DOC_KEEP_MS,)
            ).rowcount
            counters = conn.exec_driver_sql(
                "DELETE FROM counter WHERE day < ?", (oldest_day,)
            ).rowcount
            return {"docs": docs, "counters": counters}

        return self._write(write)
