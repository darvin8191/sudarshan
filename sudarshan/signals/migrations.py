"""signals.db's schema in numbered steps, run by the service itself at start (T3, design 3.5).

The ring's shape (``sudarshan/journal/migrations.py``): each step is idempotent (``CREATE ... IF
NOT EXISTS``) and runs in one IMMEDIATE transaction with ``PRAGMA user_version = N``, so a step
that fails leaves the file as it was and two openers of a new file queue. A file a newer release
wrote is refused with a sentence for the owner and the view stays off: an older release must not
write to a schema it does not know. A step is frozen once released; a later change is a new step.

Units: instants in epoch milliseconds (server clock unless named ``at`` of a document), a trading
day as YYYYMMDD (IST), prices as REAL index points, JSON as text.

    doc      one row per letterbox file read: run, seq, at, next_by, when it was received and
             written, its size, the verdict (contract.VERDICTS) and the rule that refused it; for
             an accepted one its published time, fresh window, offset, keys and stopping. 30 days.
    now      the current reading of each (module, instrument): the contract's Reading fields a
             document gives, as JSON, with its document's published time, seq, offset and lapse.
    change   the timeline, kept for ever (a few rows a day); ``relay`` (contract.RELAY_STATES).
    alert    TRINETRA's alerts, by their own id, kept for ever; ``relay`` as above.
    mark     index closes after a change, for the performance (T8), kept for ever.
    counter  per IST day and kind: refused, gaps, strangers, repeat, older, newer_format,
             overflow. 40 days.
    level    (step 2) each trading day's index open, previous close and close, for T8.
    meta     the last accepted document, the settings, the notices' memory.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from sqlalchemy.engine import Connection, Engine

#: An execution option the store's engine reads in its "begin" listener: this transaction
#: writes, so it begins IMMEDIATE and holds the write lock from its first read.
IMMEDIATE = "sudarshan_immediate"

V1 = (
    """CREATE TABLE IF NOT EXISTS doc (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        run TEXT,
        seq INTEGER,
        at INTEGER,
        next_by INTEGER,
        received_ms INTEGER NOT NULL,
        written_ms INTEGER,
        bytes INTEGER NOT NULL,
        verdict TEXT NOT NULL,
        reason TEXT,
        clock TEXT,
        published_ms INTEGER,
        fresh_until_ms INTEGER,
        offset_ms INTEGER,
        keys_json TEXT,
        stopping INTEGER NOT NULL DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS doc_received ON doc (received_ms)",
    "CREATE INDEX IF NOT EXISTS doc_published ON doc (verdict, published_ms)",
    """CREATE TABLE IF NOT EXISTS now (
        module TEXT NOT NULL,
        instrument TEXT NOT NULL,
        row_json TEXT NOT NULL,
        published_ms INTEGER NOT NULL,
        doc_seq INTEGER NOT NULL,
        offset_ms INTEGER NOT NULL DEFAULT 0,
        lapsed INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (module, instrument)
    )""",
    """CREATE TABLE IF NOT EXISTS change (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        at INTEGER NOT NULL,
        recorded_ms INTEGER NOT NULL,
        module TEXT NOT NULL,
        instrument TEXT NOT NULL,
        kind TEXT NOT NULL,
        from_state TEXT,
        from_label TEXT,
        from_bias TEXT,
        to_state TEXT NOT NULL,
        to_label TEXT NOT NULL,
        bias TEXT NOT NULL,
        score REAL,
        confidence REAL,
        quality TEXT NOT NULL,
        horizon TEXT NOT NULL,
        since_ms INTEGER,
        as_of_ms INTEGER,
        valid_until_ms INTEGER,
        levels_json TEXT,
        inputs_json TEXT NOT NULL,
        why_json TEXT NOT NULL,
        mark_px REAL,
        relay TEXT NOT NULL DEFAULT 'pending'
    )""",
    "CREATE INDEX IF NOT EXISTS change_pair ON change (module, instrument, id)",
    "CREATE INDEX IF NOT EXISTS change_at ON change (at)",
    "CREATE INDEX IF NOT EXISTS change_pending ON change (relay) WHERE relay = 'pending'",
    """CREATE TABLE IF NOT EXISTS alert (
        id TEXT PRIMARY KEY,
        at INTEGER NOT NULL,
        received_ms INTEGER NOT NULL,
        module TEXT NOT NULL,
        instrument TEXT,
        level TEXT NOT NULL,
        title TEXT NOT NULL,
        text TEXT NOT NULL,
        lang TEXT,
        relay TEXT NOT NULL DEFAULT 'pending'
    )""",
    "CREATE INDEX IF NOT EXISTS alert_at ON alert (at)",
    "CREATE INDEX IF NOT EXISTS alert_pending ON alert (relay) WHERE relay = 'pending'",
    """CREATE TABLE IF NOT EXISTS mark (
        change_id INTEGER NOT NULL,
        session_n INTEGER NOT NULL,
        day INTEGER NOT NULL,
        close_px REAL,
        open_px REAL,
        PRIMARY KEY (change_id, session_n)
    )""",
    """CREATE TABLE IF NOT EXISTS counter (
        day INTEGER NOT NULL,
        kind TEXT NOT NULL,
        n INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (day, kind)
    )""",
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
)


def _v1(conn: Connection) -> None:
    for sql in V1:
        conn.exec_driver_sql(sql)


#: Step 2 (T8): the index's level each trading day, as the feed's snapshot gave it: the open (read
#: just after 09:15), the previous close Upstox names with it, and the close (read after the
#: close). The performance is worked out from these and the timeline; kept for ever.
V2 = (
    """CREATE TABLE IF NOT EXISTS level (
        day INTEGER NOT NULL,
        instrument TEXT NOT NULL,
        open_px REAL,
        prev_close_px REAL,
        close_px REAL,
        open_ms INTEGER,
        close_ms INTEGER,
        PRIMARY KEY (day, instrument)
    )""",
)


def _v2(conn: Connection) -> None:
    for sql in V2:
        conn.exec_driver_sql(sql)


Step = tuple[int, Callable[[Connection], None]]
STEPS: tuple[Step, ...] = ((1, _v1), (2, _v2))
LATEST = STEPS[-1][0]


class SchemaTooNew(RuntimeError):
    """signals.db was migrated by a newer release; this one must not write to it. ``str()`` is
    the owner's sentence; the versions are for the log and ``--status``."""

    def __init__(self, found: int, known: int):
        from sudarshan.signals.contract import WORDS

        self.found, self.known = found, known
        self.cause = f"file version {found}, this release knows up to {known}"
        super().__init__(WORDS["store_newer"])


def version(conn: Connection) -> int:
    return int(conn.exec_driver_sql("PRAGMA user_version").scalar() or 0)


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
    """Run every step the file lacks, each in its own IMMEDIATE transaction; the steps run."""
    steps = STEPS if steps is None else steps
    done = []
    writer = engine.execution_options(**{IMMEDIATE: True})
    for number in pending(engine, steps):
        step = dict(steps)[number]
        with writer.begin() as conn:
            if version(conn) >= number:  # another opener got here first
                continue
            step(conn)
            conn.exec_driver_sql(f"PRAGMA user_version = {int(number)}")
        done.append(number)
    return done
