"""family.db's schema in numbered steps, run by the family service itself at start (design 4.3).

The shape of the portfolio's and the market store's: each step is idempotent (``CREATE ... IF NOT
EXISTS``; a column is added only when ``PRAGMA table_info`` lacks it; the audit triggers created
``IF NOT EXISTS``) and runs in one transaction with ``PRAGMA user_version = N``, so a step that
fails leaves the file as it was. A step's transaction begins IMMEDIATE, taking the write lock
before it reads user_version, so two openers of a new file queue and the second finds it done.

A file whose user_version is newer than this code (a release rolled back after a newer one had
migrated it) is refused (``SchemaTooNew``) and the family view stays off: an older release must not
write to a schema it does not know. A step is frozen once released; a later change is a new step.

V1 holds every table of the design (4.2), with three additions: ``audit.member_id`` (the member a
line is about, so the owner's filter by member is exact whoever acted), ``viewer_session.
revoke_reason``, and ``request_id`` on the rows a create may be retried for. Two rules are in the
schema itself: the audit refuses UPDATE and DELETE (triggers), and an outbox row that has been
sent or dropped keeps no payload (a CHECK: the masked text can hold the owner's figures, Y44).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from sqlalchemy.engine import Connection, Engine

from .errors import SchemaTooNew

#: An execution option the store's engine reads in its "begin" listener: this transaction writes,
#: so it begins IMMEDIATE and holds the write lock from its first read.
IMMEDIATE = "sudarshan_immediate"

V1 = (
    """CREATE TABLE IF NOT EXISTS member (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        handle TEXT UNIQUE,
        display_name TEXT NOT NULL,
        role TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'invited',
        privacy TEXT NOT NULL,
        language TEXT NOT NULL DEFAULT 'en',
        quiet TEXT NOT NULL DEFAULT '{}',
        notify TEXT NOT NULL DEFAULT '{}',
        request_id TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        version INTEGER NOT NULL DEFAULT 1
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS member_request
        ON member (request_id) WHERE request_id IS NOT NULL""",
    """CREATE TABLE IF NOT EXISTS credential (
        member_id INTEGER PRIMARY KEY,
        password_hash TEXT NOT NULL,
        totp_secret_enc TEXT NOT NULL,
        totp_last_counter INTEGER NOT NULL DEFAULT 0,
        failed_count INTEGER NOT NULL DEFAULT 0,
        locked_until INTEGER,
        lock_level INTEGER NOT NULL DEFAULT 0,
        password_changed_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS invite (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        member_id INTEGER NOT NULL,
        code_hash TEXT NOT NULL UNIQUE,
        totp_secret_enc TEXT NOT NULL,
        expires_at INTEGER NOT NULL,
        used_at INTEGER,
        revoked_at INTEGER,
        request_id TEXT,
        created_at INTEGER NOT NULL
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS invite_open
        ON invite (member_id) WHERE used_at IS NULL AND revoked_at IS NULL""",
    """CREATE TABLE IF NOT EXISTS viewer_session (
        id_hash TEXT PRIMARY KEY,
        member_id INTEGER NOT NULL,
        device_label TEXT NOT NULL,
        ua_summary TEXT NOT NULL,
        ip_hash TEXT,
        created_at INTEGER NOT NULL,
        last_seen_at INTEGER NOT NULL,
        absolute_expires_at INTEGER NOT NULL,
        idle_expires_at INTEGER NOT NULL,
        revoked_at INTEGER,
        revoke_reason TEXT
    )""",
    """CREATE INDEX IF NOT EXISTS viewer_session_member
        ON viewer_session (member_id, revoked_at)""",
    """CREATE TABLE IF NOT EXISTS push_subscription (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        member_id INTEGER NOT NULL,
        session_hash TEXT,
        endpoint TEXT NOT NULL UNIQUE,
        p256dh TEXT NOT NULL,
        auth TEXT NOT NULL,
        ua_summary TEXT,
        request_id TEXT,
        created_at INTEGER NOT NULL,
        last_ok_at INTEGER,
        failures INTEGER NOT NULL DEFAULT 0,
        backoff_until INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS telegram_link (
        member_id INTEGER PRIMARY KEY,
        chat_id_enc TEXT,
        linked_at INTEGER,
        link_code_hash TEXT,
        link_expires_at INTEGER
    )""",
    """CREATE TABLE IF NOT EXISTS share (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        ref TEXT NOT NULL,
        member_id INTEGER,
        role TEXT,
        level TEXT NOT NULL,
        expires_at INTEGER,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        version INTEGER NOT NULL DEFAULT 1
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS share_target
        ON share (kind, ref, IFNULL(member_id, -1), IFNULL(role, ''))""",
    """CREATE TABLE IF NOT EXISTS symbol_map (
        symbol TEXT NOT NULL,
        exchange TEXT NOT NULL,
        instrument_key TEXT NOT NULL,
        name TEXT NOT NULL,
        tick_size_u INTEGER,
        lot_size INTEGER,
        refreshed_at INTEGER NOT NULL,
        PRIMARY KEY (symbol, exchange)
    )""",
    """CREATE TABLE IF NOT EXISTS outbox (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        member_id INTEGER NOT NULL,
        channel TEXT NOT NULL,
        category TEXT NOT NULL,
        payload TEXT,
        urgency TEXT NOT NULL,
        topic TEXT,
        ttl_s INTEGER NOT NULL,
        due_at INTEGER NOT NULL,
        sent_at INTEGER,
        result TEXT,
        created_at INTEGER NOT NULL,
        CHECK (sent_at IS NULL OR payload IS NULL)
    )""",
    "CREATE INDEX IF NOT EXISTS outbox_due ON outbox (sent_at, due_at)",
    """CREATE TABLE IF NOT EXISTS audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        at INTEGER NOT NULL,
        actor TEXT NOT NULL,
        member_id INTEGER,
        session_hash16 TEXT,
        kind TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT '{}',
        ip_hash TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS audit_member ON audit (member_id, id)",
    "CREATE INDEX IF NOT EXISTS audit_kind ON audit (kind, id)",
    """CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit
        BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END""",
    """CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit
        BEGIN SELECT RAISE(ABORT, 'audit is append-only'); END""",
    """CREATE TABLE IF NOT EXISTS job_run (
        name TEXT NOT NULL,
        day INTEGER NOT NULL,
        status TEXT NOT NULL,
        detail TEXT,
        at INTEGER NOT NULL,
        PRIMARY KEY (name, day)
    )""",
)


def step_1(conn: Connection) -> None:
    for sql in V1:
        conn.exec_driver_sql(sql)


Step = tuple[int, Callable[[Connection], None]]
STEPS: tuple[Step, ...] = ((1, step_1),)
LATEST = STEPS[-1][0]


def version(conn: Connection) -> int:
    return int(conn.exec_driver_sql("PRAGMA user_version").scalar() or 0)


def pending(engine: Engine, steps: Sequence[Step] = STEPS) -> list[int]:
    """The steps that would run, without running them; SchemaTooNew for a newer file."""
    with engine.connect() as conn:
        found = version(conn)
    known = steps[-1][0]
    if found > known:
        raise SchemaTooNew(found, known)
    return [number for number, _ in steps if number > found]


def migrate(engine: Engine, steps: Sequence[Step] = STEPS) -> list[int]:
    """Run every step the file lacks, each in its own IMMEDIATE transaction; the steps run."""
    done = []
    writer = engine.execution_options(**{IMMEDIATE: True})
    for number in pending(engine, steps):
        step = dict(steps)[number]
        with writer.begin() as conn:
            found = version(conn)
            if found > steps[-1][0]:  # a newer release got here between the two reads
                raise SchemaTooNew(found, steps[-1][0])
            if found >= number:  # another opener got here first
                continue
            step(conn)
            conn.exec_driver_sql(f"PRAGMA user_version = {int(number)}")
        done.append(number)
    return done
