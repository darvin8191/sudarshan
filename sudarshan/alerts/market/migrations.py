"""market.db's schema in numbered steps, run by the alert service itself at start (M10.4).

Each step is idempotent (CREATE ... IF NOT EXISTS; a column is added only when PRAGMA table_info
lacks it) and runs in one transaction with ``PRAGMA user_version = N``, so a step that fails
leaves the file as it was. pysqlite issues no BEGIN before DDL on its own; the store's engine
emits one (store.engine), and without it a failed step would keep its tables.

Two processes may open a new file at once (the service and a replay check). Each step's
transaction begins IMMEDIATE, taking the write lock before it reads user_version: the second
waits for the first to commit, then finds the step done. A deferred BEGIN read the version on a
snapshot the first one's commit made stale, and its write could then only fail ("database is
locked") however long it waited.

A file whose user_version is newer than this code (a release rolled back after a newer one had
migrated it) is refused with one line in the owner's words, and market alerts stay off: an
older release must not write to a schema it does not know.

A step is frozen once released: its SQL never changes, and a later change is a new step.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from sqlalchemy.engine import Connection, Engine

#: An execution option the store's engine reads in its "begin" listener: this transaction
#: writes, so it begins IMMEDIATE and holds the write lock from its first read.
IMMEDIATE = "sudarshan_immediate"

#: Every owner-data table carries ``principal`` ("owner" in this release).
V1 = (
    """CREATE TABLE IF NOT EXISTS alert (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        principal TEXT NOT NULL,
        kind TEXT NOT NULL,
        symbol TEXT NOT NULL,
        exchange TEXT NOT NULL,
        instrument_key TEXT NOT NULL,
        tick_size TEXT NOT NULL,
        expiry TEXT,
        params TEXT NOT NULL,
        frequency TEXT NOT NULL,
        candle_min INTEGER,
        cooldown_s INTEGER NOT NULL DEFAULT 0,
        ends_at INTEGER,
        status TEXT NOT NULL,
        status_reason TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        channels TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        version INTEGER NOT NULL DEFAULT 1
    )""",
    "CREATE INDEX IF NOT EXISTS alert_principal_status ON alert (principal, status)",
    "CREATE INDEX IF NOT EXISTS alert_instrument ON alert (instrument_key)",
    """CREATE TABLE IF NOT EXISTS alert_state (
        alert_id INTEGER PRIMARY KEY,
        principal TEXT NOT NULL,
        session INTEGER NOT NULL DEFAULT 0,
        side INTEGER,
        ref INTEGER,
        seen_at INTEGER,
        last_fired_at INTEGER,
        last_fired_candle INTEGER,
        fires INTEGER NOT NULL DEFAULT 0,
        fires_session INTEGER NOT NULL DEFAULT 0,
        quota_base INTEGER NOT NULL DEFAULT 0,
        updated_at INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS alert_event (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        principal TEXT NOT NULL,
        alert_id INTEGER NOT NULL,
        session INTEGER NOT NULL,
        fired_at INTEGER NOT NULL,
        trade_time INTEGER,
        symbol TEXT NOT NULL,
        exchange TEXT NOT NULL,
        kind TEXT NOT NULL,
        title TEXT NOT NULL,
        line TEXT NOT NULL,
        value TEXT,
        level TEXT,
        found_later INTEGER NOT NULL DEFAULT 0,
        opening INTEGER NOT NULL DEFAULT 0,
        test INTEGER NOT NULL DEFAULT 0,
        claim TEXT NOT NULL UNIQUE
    )""",
    "CREATE INDEX IF NOT EXISTS alert_event_principal ON alert_event (principal, id)",
    "CREATE INDEX IF NOT EXISTS alert_event_alert ON alert_event (principal, alert_id, id)",
    "CREATE INDEX IF NOT EXISTS alert_event_fired ON alert_event (fired_at)",
    """CREATE TABLE IF NOT EXISTS alert_delivery (
        event_id INTEGER NOT NULL,
        channel TEXT NOT NULL,
        principal TEXT NOT NULL,
        status TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        attempts INTEGER NOT NULL DEFAULT 0,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (event_id, channel)
    )""",
    """CREATE TABLE IF NOT EXISTS prefs (
        principal TEXT PRIMARY KEY,
        telegram INTEGER NOT NULL DEFAULT 1,
        app INTEGER NOT NULL DEFAULT 1,
        updated_at INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS baseline (
        instrument_key TEXT NOT NULL,
        session INTEGER NOT NULL,
        days INTEGER NOT NULL,
        value REAL NOT NULL,
        fetched_at INTEGER NOT NULL,
        PRIMARY KEY (instrument_key, session)
    )""",
    """CREATE TABLE IF NOT EXISTS pause (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at INTEGER NOT NULL,
        ended_at INTEGER,
        cause TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS pause_started ON pause (started_at)",
    """CREATE TABLE IF NOT EXISTS meta (
        name TEXT PRIMARY KEY,
        value INTEGER NOT NULL
    )""",
)


def _v1(conn: Connection) -> None:
    for sql in V1:
        conn.exec_driver_sql(sql)


Step = tuple[int, Callable[[Connection], None]]
STEPS: tuple[Step, ...] = ((1, _v1),)
LATEST = STEPS[-1][0]


class SchemaTooNew(RuntimeError):
    """market.db was migrated by a newer release; this one must not write to it."""

    def __init__(self, found: int, known: int):
        self.found, self.known = found, known
        super().__init__(
            f"The market alert record was written by a newer SUDARSHAN release (version {found}; "
            f"this one knows up to {known}). Market alerts stay off until that release is "
            "installed again; order alerts carry on."
        )


def version(conn: Connection) -> int:
    return int(conn.exec_driver_sql("PRAGMA user_version").scalar() or 0)


def columns(conn: Connection, table: str) -> set[str]:
    return {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}


def add_column(conn: Connection, table: str, column: str, ddl: str) -> bool:
    """``ALTER TABLE table ADD COLUMN column ddl`` unless the column is there; True if added."""
    if column in columns(conn, table):
        return False
    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
    return True


def pending(engine: Engine, steps: Sequence[Step] | None = None) -> list[int]:
    """The steps that would run, without running them; SchemaTooNew for a newer file."""
    steps = STEPS if steps is None else steps
    with engine.connect() as conn:
        found = version(conn)
    known = steps[-1][0]
    if found > known:
        raise SchemaTooNew(found, known)
    return [number for number, _ in steps if number > found]


def migrate(engine: Engine, steps: Sequence[Step] | None = None) -> list[int]:
    """Run every step the file lacks, each in its own transaction; returns the steps run."""
    steps = STEPS if steps is None else steps
    done = []
    writer = engine.execution_options(**{IMMEDIATE: True})
    for number in pending(engine, steps):
        step = dict(steps)[number]
        with writer.begin() as conn:
            if version(conn) >= number:  # another process got here first
                continue
            step(conn)
            conn.exec_driver_sql(f"PRAGMA user_version = {int(number)}")
        done.append(number)
    return done
