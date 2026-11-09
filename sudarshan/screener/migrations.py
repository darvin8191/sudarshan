"""screener.db's schema in numbered steps, run by the screener service itself at start (M9.1, 6.1).

The same shape as market.db's and portfolio.db's: each step is idempotent (CREATE ... IF NOT
EXISTS; a column is added only when PRAGMA table_info lacks it) and runs in one IMMEDIATE
transaction with ``PRAGMA user_version = N`` at its end, so a step that fails leaves the file as
it was and two openers of a new file queue instead of colliding (cross-design X7). The helpers
that run the steps are the market alerts' (``sudarshan.alerts.market.migrations``); only the
steps and the owner's sentence for a newer file are this module's.

A file whose user_version is newer than this code (a release rolled back after a newer one had
migrated it) is refused with ``SchemaTooNew``: the screener stays off, in the owner's words, and
an older release never writes to a schema it does not know.

Schema 1 is frozen: its SQL never changes, and a later change is a new step. Every owner-data
table carries ``principal`` (``'owner'`` in this release) so a family read-only layer later adds
principals and no columns (6.5). Step 2 adds a sealed bar's running volume and last trade time
to ``bar_today`` (release review HIGH-1). Step 3 lets a pause name one instrument (re-review
MEDIUM-1). Step 4 adds the dashboard and its widgets (M9.16, dashboards design 3.3).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from sqlalchemy.engine import Connection, Engine

from sudarshan.alerts.market import migrations as shared

IMMEDIATE = shared.IMMEDIATE
Step = tuple[int, Callable[[Connection], None]]

#: Tables whose rows belong to the owner (every one carries ``principal``); the shared tables
#: (universes, bars, coverage, daily stores, pauses, meta, settings) belong to no one.
OWNER_TABLES = (
    "screen",
    "screen_state",
    "run",
    "run_change",
    "run_value",
    "membership",
    "alert_state",
    "outbox",
    "dashboard",
    "dashboard_widget",
)

V1 = (
    # ---- the owner's screens ----
    """CREATE TABLE IF NOT EXISTS screen (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        principal TEXT NOT NULL,
        name TEXT NOT NULL,
        spec TEXT NOT NULL,
        status TEXT NOT NULL,
        status_reason TEXT,
        shared INTEGER NOT NULL DEFAULT 0,
        request_id TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        version INTEGER NOT NULL DEFAULT 1
    )""",
    "CREATE INDEX IF NOT EXISTS screen_principal ON screen (principal, status, id)",
    """CREATE UNIQUE INDEX IF NOT EXISTS screen_request
        ON screen (principal, request_id) WHERE request_id IS NOT NULL""",
    """CREATE TABLE IF NOT EXISTS screen_state (
        screen_id INTEGER PRIMARY KEY,
        principal TEXT NOT NULL,
        last_run_key TEXT,
        last_run_at INTEGER,
        next_run_at INTEGER,
        alerts_paused INTEGER NOT NULL DEFAULT 0,
        list_id INTEGER,
        list_name TEXT,
        updated_at INTEGER NOT NULL
    )""",
    # ---- runs ----
    """CREATE TABLE IF NOT EXISTS run (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        principal TEXT NOT NULL,
        screen_id INTEGER NOT NULL,
        screen_version INTEGER NOT NULL,
        run_key TEXT NOT NULL UNIQUE,
        session INTEGER NOT NULL,
        candle_start INTEGER NOT NULL,
        candle_end INTEGER NOT NULL,
        ran_at INTEGER NOT NULL,
        matched INTEGER NOT NULL DEFAULT 0,
        entered INTEGER NOT NULL DEFAULT 0,
        exited INTEGER NOT NULL DEFAULT 0,
        missing INTEGER NOT NULL DEFAULT 0,
        not_checked INTEGER NOT NULL DEFAULT 0,
        marks TEXT,
        status TEXT NOT NULL,
        engine_version INTEGER NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS run_screen ON run (principal, screen_id, id)",
    "CREATE INDEX IF NOT EXISTS run_session ON run (session, id)",
    """CREATE TABLE IF NOT EXISTS run_change (
        run_id INTEGER NOT NULL,
        principal TEXT NOT NULL,
        key TEXT NOT NULL,
        symbol TEXT NOT NULL,
        exchange TEXT NOT NULL,
        change TEXT NOT NULL,
        rank INTEGER,
        PRIMARY KEY (run_id, key)
    )""",
    """CREATE TABLE IF NOT EXISTS run_value (
        run_id INTEGER NOT NULL,
        principal TEXT NOT NULL,
        key TEXT NOT NULL,
        values_json TEXT NOT NULL,
        PRIMARY KEY (run_id, key)
    )""",
    # ---- membership and the target list ----
    """CREATE TABLE IF NOT EXISTS membership (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        principal TEXT NOT NULL,
        screen_id INTEGER NOT NULL,
        list_id INTEGER NOT NULL,
        key TEXT NOT NULL,
        symbol TEXT NOT NULL,
        exchange TEXT NOT NULL,
        status TEXT NOT NULL,
        pinned INTEGER NOT NULL DEFAULT 0,
        added_by_screener INTEGER NOT NULL DEFAULT 1,
        in_list INTEGER NOT NULL DEFAULT 0,
        first_seen_at INTEGER NOT NULL DEFAULT 0,
        first_seen_run INTEGER NOT NULL DEFAULT 0,
        last_seen_at INTEGER NOT NULL DEFAULT 0,
        last_seen_run INTEGER NOT NULL DEFAULT 0,
        hits INTEGER NOT NULL DEFAULT 0,
        misses INTEGER NOT NULL DEFAULT 0,
        entries INTEGER NOT NULL DEFAULT 1,
        exited_at INTEGER,
        entry_price INTEGER,
        score TEXT,
        screen_version INTEGER NOT NULL DEFAULT 0,
        missing_session INTEGER NOT NULL DEFAULT 0,
        updated_at INTEGER NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        UNIQUE (screen_id, list_id, key)
    )""",
    "CREATE INDEX IF NOT EXISTS membership_list ON membership (principal, list_id, status)",
    """CREATE TABLE IF NOT EXISTS list_item (
        list_id INTEGER NOT NULL,
        key TEXT NOT NULL,
        added_by_screener INTEGER NOT NULL DEFAULT 0,
        openalgo_item_id INTEGER,
        seen_at INTEGER NOT NULL,
        PRIMARY KEY (list_id, key)
    )""",
    # ---- alerts ----
    """CREATE TABLE IF NOT EXISTS alert_state (
        principal TEXT NOT NULL,
        screen_id INTEGER NOT NULL,
        session INTEGER NOT NULL,
        key TEXT NOT NULL,
        told INTEGER NOT NULL DEFAULT 0,
        last_run INTEGER,
        PRIMARY KEY (screen_id, session, key)
    )""",
    """CREATE TABLE IF NOT EXISTS outbox (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        principal TEXT NOT NULL,
        kind TEXT NOT NULL,
        ref TEXT NOT NULL,
        payload TEXT NOT NULL,
        status TEXT NOT NULL,
        attempts INTEGER NOT NULL DEFAULT 0,
        next_at INTEGER NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS outbox_due ON outbox (status, next_at, id)",
    "CREATE INDEX IF NOT EXISTS outbox_ref ON outbox (kind, ref)",
    # ---- shared data: universes, bars, coverage, daily stores, pauses ----
    """CREATE TABLE IF NOT EXISTS universe (
        name TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        built_at INTEGER NOT NULL,
        note TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS universe_member (
        universe TEXT NOT NULL,
        key TEXT NOT NULL,
        symbol TEXT NOT NULL,
        exchange TEXT NOT NULL,
        segment TEXT NOT NULL,
        tick_size INTEGER,
        lot INTEGER,
        expiry INTEGER,
        strike INTEGER,
        opt_type TEXT,
        cas INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (universe, key)
    )""",
    """CREATE TABLE IF NOT EXISTS bar_today (
        key TEXT NOT NULL,
        start INTEGER NOT NULL,
        o INTEGER NOT NULL,
        h INTEGER NOT NULL,
        l INTEGER NOT NULL,
        c INTEGER NOT NULL,
        v INTEGER NOT NULL,
        oi INTEGER,
        atp INTEGER,
        src TEXT NOT NULL,
        flags INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (key, start)
    )""",
    """CREATE TABLE IF NOT EXISTS hist_coverage (
        key TEXT NOT NULL,
        tf TEXT NOT NULL,
        first_start INTEGER NOT NULL,
        last_start INTEGER NOT NULL,
        file TEXT NOT NULL,
        fetched_at INTEGER NOT NULL,
        PRIMARY KEY (key, tf)
    )""",
    """CREATE TABLE IF NOT EXISTS oi_daily (
        key TEXT NOT NULL,
        session INTEGER NOT NULL,
        oi INTEGER NOT NULL,
        close INTEGER,
        volume INTEGER,
        PRIMARY KEY (key, session)
    )""",
    """CREATE TABLE IF NOT EXISTS iv_daily (
        underlying TEXT NOT NULL,
        session INTEGER NOT NULL,
        atm_iv TEXT NOT NULL,
        straddle INTEGER,
        pcr TEXT,
        max_pain INTEGER,
        vix TEXT,
        PRIMARY KEY (underlying, session)
    )""",
    """CREATE TABLE IF NOT EXISTS pause (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session INTEGER NOT NULL,
        start INTEGER NOT NULL,
        end INTEGER,
        why TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS pause_session ON pause (session, start)",
    """CREATE TABLE IF NOT EXISTS meta (
        name TEXT PRIMARY KEY,
        value TEXT NOT NULL,
        updated_at INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS settings (
        principal TEXT NOT NULL,
        name TEXT NOT NULL,
        value TEXT NOT NULL,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (principal, name)
    )""",
)


def _v1(conn: Connection) -> None:
    for statement in V1:
        conn.exec_driver_sql(statement)


def _v2(conn: Connection) -> None:
    """A sealed bar keeps the day's running volume accounted for through it (``cum``) and the time
    of its last trade (``last_trade``, epoch ms), both lost on a restore before (release review
    HIGH-1): the first bar after a restart then carried the whole day's volume, and a pause before
    a restored bar was taken to touch every bar after it. Rows written before have neither (NULL)."""
    add_column(conn, "bar_today", "cum", "INTEGER")
    add_column(conn, "bar_today", "last_trade", "INTEGER")


def _v3(conn: Connection) -> None:
    """A pause may name one instrument (``key``): the stretch the screener did not follow a key it
    then followed again the same day (re-review MEDIUM-1), kept so a restart still marks that
    key's candles. NULL is a pause of every key, which is what every row written before is."""
    add_column(conn, "pause", "key", "TEXT")


V4 = (
    """CREATE TABLE IF NOT EXISTS dashboard (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        principal TEXT NOT NULL,
        name TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        version INTEGER NOT NULL DEFAULT 1
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS dashboard_name ON dashboard (principal, name)",
    # No CHECK constraints: kind, rows and sort_by are checked in code, because SQLite cannot
    # alter a CHECK in place and a later "50 rows" or a third kind would mean a table rebuild.
    """CREATE TABLE IF NOT EXISTS dashboard_widget (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        principal TEXT NOT NULL,
        dashboard_id INTEGER NOT NULL,
        screen_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        position INTEGER NOT NULL,
        rows INTEGER NOT NULL,
        sort_by TEXT,
        value_clauses TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )""",
    """CREATE UNIQUE INDEX IF NOT EXISTS dashboard_widget_one
        ON dashboard_widget (dashboard_id, screen_id, kind)""",
    "CREATE INDEX IF NOT EXISTS dashboard_widget_order ON dashboard_widget (dashboard_id, position)",
    "CREATE INDEX IF NOT EXISTS dashboard_widget_screen ON dashboard_widget (screen_id)",
)


def _v4(conn: Connection) -> None:
    """The owner's dashboard and its widgets (new tables, nothing to backfill: no row is seeded,
    the read answers a suggestion until his first change)."""
    for statement in V4:
        conn.exec_driver_sql(statement)


STEPS: Sequence[Step] = ((1, _v1), (2, _v2), (3, _v3), (4, _v4))
LATEST = STEPS[-1][0]


class SchemaTooNew(RuntimeError):
    """screener.db was migrated by a newer release; this one must not write to it."""

    WORDS = (
        "Your screens were saved by a newer SUDARSHAN release. The screener stays off until that "
        "release is installed again; nothing was changed."
    )

    def __init__(self, found: int, known: int):
        self.found, self.known = found, known
        self.cause = f"file version {found}, this release knows up to {known}"
        super().__init__(self.WORDS)


version = shared.version
columns = shared.columns
add_column = shared.add_column


def pending(engine: Engine, steps: Sequence[Step] | None = None) -> list[int]:
    """The steps that would run, without running them; SchemaTooNew for a newer file."""
    try:
        return shared.pending(engine, STEPS if steps is None else steps)
    except shared.SchemaTooNew as exc:
        raise SchemaTooNew(exc.found, exc.known) from None


def migrate(engine: Engine, steps: Sequence[Step] | None = None) -> list[int]:
    """Run every step the file lacks, each in its own IMMEDIATE transaction; the steps run."""
    try:
        return shared.migrate(engine, STEPS if steps is None else steps)
    except shared.SchemaTooNew as exc:
        raise SchemaTooNew(exc.found, exc.known) from None
