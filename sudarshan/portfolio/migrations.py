"""portfolio.db's schema in numbered steps, run by the portfolio service itself at start (M7.19).

The same shape as market.db's (sudarshan/alerts/market/migrations.py): each step is idempotent
(CREATE ... IF NOT EXISTS; a column is added only when PRAGMA table_info lacks it) and runs in one
transaction with ``PRAGMA user_version = N``, so a step that fails leaves the file as it was. The
store's engine emits its own BEGIN (store.engine); a step's transaction begins IMMEDIATE, taking
the write lock before it reads user_version, so two openers of a new file queue: the second waits
for the first to commit, then finds the step done.

A file whose user_version is newer than this code (a release rolled back after a newer one had
migrated it) is refused with a sentence for the owner, and only the portfolio stays off: an older
release must not write to a schema it does not know.

A step is frozen once released: its SQL never changes, and a later change is a new step.

First-start seeds (``seed``): member "Me", the Upstox account and the Upstox tab, each made only
when nothing of its kind is there, so a row the owner has renamed or kept is never touched and a
row he has deleted is never made again while others remain.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from sqlalchemy.engine import Connection, Engine

#: An execution option the store's engine reads in its "begin" listener: this transaction
#: writes, so it begins IMMEDIATE and holds the write lock from its first read.
IMMEDIATE = "sudarshan_immediate"

#: Every owner-data table carries ``principal`` ("owner" in this release), ``created_at`` and
#: ``updated_at`` (epoch ms). Market data (``benchmark_close``, ``mf_scheme``) and the job log
#: belong to no one. ``request_id`` makes a retried create return the row already made.
V1 = (
    """CREATE TABLE IF NOT EXISTS member (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        principal TEXT NOT NULL,
        name TEXT NOT NULL,
        relation TEXT,
        request_id TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        version INTEGER NOT NULL DEFAULT 1
    )""",
    "CREATE INDEX IF NOT EXISTS member_principal ON member (principal, id)",
    """CREATE UNIQUE INDEX IF NOT EXISTS member_request
        ON member (principal, request_id) WHERE request_id IS NOT NULL""",
    """CREATE TABLE IF NOT EXISTS account (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        principal TEXT NOT NULL,
        member_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        kind TEXT NOT NULL,
        broker TEXT,
        note TEXT,
        system INTEGER NOT NULL DEFAULT 0,
        request_id TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        version INTEGER NOT NULL DEFAULT 1
    )""",
    "CREATE INDEX IF NOT EXISTS account_principal ON account (principal, member_id)",
    """CREATE UNIQUE INDEX IF NOT EXISTS account_request
        ON account (principal, request_id) WHERE request_id IS NOT NULL""",
    """CREATE TABLE IF NOT EXISTS tab (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        principal TEXT NOT NULL,
        name TEXT NOT NULL,
        name_alt TEXT,
        kind TEXT NOT NULL,
        icon TEXT NOT NULL,
        colour TEXT NOT NULL,
        position INTEGER NOT NULL DEFAULT 0,
        pinned INTEGER NOT NULL DEFAULT 0,
        archived INTEGER NOT NULL DEFAULT 0,
        system INTEGER NOT NULL DEFAULT 0,
        manual_old_days INTEGER NOT NULL DEFAULT 30,
        request_id TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        version INTEGER NOT NULL DEFAULT 1
    )""",
    "CREATE INDEX IF NOT EXISTS tab_order ON tab (principal, archived, position)",
    """CREATE UNIQUE INDEX IF NOT EXISTS tab_request
        ON tab (principal, request_id) WHERE request_id IS NOT NULL""",
    """CREATE TABLE IF NOT EXISTS tab_layout (
        tab_id INTEGER PRIMARY KEY,
        principal TEXT NOT NULL,
        spec TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        version INTEGER NOT NULL DEFAULT 1
    )""",
    """CREATE TABLE IF NOT EXISTS saved_view (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        principal TEXT NOT NULL,
        tab_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        spec TEXT NOT NULL,
        is_default INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        version INTEGER NOT NULL DEFAULT 1
    )""",
    "CREATE INDEX IF NOT EXISTS saved_view_tab ON saved_view (principal, tab_id)",
    """CREATE TABLE IF NOT EXISTS asset (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        principal TEXT NOT NULL,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        exchange TEXT,
        symbol TEXT,
        isin TEXT,
        instrument_key TEXT,
        tick_size_u INTEGER,
        amfi_code TEXT,
        unit_label TEXT,
        asset_class TEXT NOT NULL,
        sector TEXT,
        mcap TEXT,
        tags TEXT NOT NULL DEFAULT '[]',
        resolved_at INTEGER,
        request_id TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        version INTEGER NOT NULL DEFAULT 1
    )""",
    "CREATE INDEX IF NOT EXISTS asset_principal ON asset (principal, kind)",
    """CREATE UNIQUE INDEX IF NOT EXISTS asset_isin
        ON asset (principal, isin) WHERE isin IS NOT NULL""",
    """CREATE UNIQUE INDEX IF NOT EXISTS asset_amfi
        ON asset (principal, amfi_code) WHERE amfi_code IS NOT NULL""",
    """CREATE UNIQUE INDEX IF NOT EXISTS asset_request
        ON asset (principal, request_id) WHERE request_id IS NOT NULL""",
    """CREATE TABLE IF NOT EXISTS asset_alias (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        principal TEXT NOT NULL,
        asset_id INTEGER NOT NULL,
        kind TEXT NOT NULL,
        value TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS asset_alias_value ON asset_alias (principal, kind, value)",
    "CREATE INDEX IF NOT EXISTS asset_alias_asset ON asset_alias (asset_id)",
    """CREATE TABLE IF NOT EXISTS activity (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        principal TEXT NOT NULL,
        tab_id INTEGER NOT NULL,
        seq INTEGER NOT NULL,
        kind TEXT NOT NULL,
        trade_date INTEGER NOT NULL,
        account_id INTEGER,
        to_account_id INTEGER,
        asset_id INTEGER,
        qty_u INTEGER,
        price_u INTEGER,
        amount_paise INTEGER,
        charges_paise INTEGER NOT NULL DEFAULT 0,
        charges_detail TEXT,
        tds_paise INTEGER NOT NULL DEFAULT 0,
        value_paise INTEGER,
        ratio_num INTEGER,
        ratio_den INTEGER,
        lot_date INTEGER,
        status TEXT NOT NULL DEFAULT 'posted',
        note TEXT,
        source TEXT NOT NULL DEFAULT 'manual',
        batch_id INTEGER,
        import_key TEXT,
        request_id TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS activity_order ON activity (principal, tab_id, trade_date, seq)",
    "CREATE INDEX IF NOT EXISTS activity_asset ON activity (principal, asset_id)",
    """CREATE UNIQUE INDEX IF NOT EXISTS activity_import
        ON activity (tab_id, import_key) WHERE import_key IS NOT NULL""",
    """CREATE UNIQUE INDEX IF NOT EXISTS activity_request
        ON activity (principal, request_id) WHERE request_id IS NOT NULL""",
    """CREATE TABLE IF NOT EXISTS price_point (
        asset_id INTEGER NOT NULL,
        day INTEGER NOT NULL,
        principal TEXT NOT NULL,
        price_u INTEGER NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (asset_id, day)
    )""",
    """CREATE TABLE IF NOT EXISTS price_eod (
        asset_id INTEGER NOT NULL,
        day INTEGER NOT NULL,
        principal TEXT NOT NULL,
        price_u INTEGER NOT NULL,
        prev_close_u INTEGER,
        basis TEXT NOT NULL,
        source TEXT NOT NULL,
        late INTEGER NOT NULL DEFAULT 0,
        at INTEGER NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (asset_id, day)
    )""",
    """CREATE TABLE IF NOT EXISTS upstox_eod (
        day INTEGER NOT NULL,
        principal TEXT NOT NULL,
        read_at INTEGER NOT NULL,
        holdings_value_paise INTEGER,
        holdings_cost_paise INTEGER,
        positions_pnl_paise INTEGER,
        funds TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (principal, day)
    )""",
    """CREATE TABLE IF NOT EXISTS upstox_eod_holding (
        day INTEGER NOT NULL,
        instrument_key TEXT NOT NULL,
        principal TEXT NOT NULL,
        asset_id INTEGER,
        qty INTEGER NOT NULL,
        t1_qty INTEGER NOT NULL DEFAULT 0,
        avg_price_u INTEGER,
        price_u INTEGER,
        basis TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (principal, day, instrument_key)
    )""",
    """CREATE TABLE IF NOT EXISTS import_batch (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        principal TEXT NOT NULL,
        tab_id INTEGER NOT NULL,
        format TEXT NOT NULL,
        rows_total INTEGER NOT NULL DEFAULT 0,
        rows_new INTEGER NOT NULL DEFAULT 0,
        rows_dup INTEGER NOT NULL DEFAULT 0,
        rows_bad INTEGER NOT NULL DEFAULT 0,
        preview TEXT,
        status TEXT NOT NULL,
        tab_version INTEGER,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS import_batch_tab ON import_batch (principal, tab_id)",
    """CREATE TABLE IF NOT EXISTS benchmark_close (
        index_key TEXT NOT NULL,
        day INTEGER NOT NULL,
        close_u INTEGER NOT NULL,
        source TEXT NOT NULL,
        PRIMARY KEY (index_key, day)
    )""",
    """CREATE TABLE IF NOT EXISTS mf_scheme (
        amfi_code TEXT PRIMARY KEY,
        isin_growth TEXT,
        isin_reinvest TEXT,
        name TEXT NOT NULL,
        category TEXT,
        amc TEXT,
        nav_u INTEGER,
        nav_date INTEGER,
        prev_nav_u INTEGER,
        prev_nav_date INTEGER,
        fetched_at INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS job_run (
        name TEXT NOT NULL,
        day INTEGER NOT NULL,
        status TEXT NOT NULL,
        detail TEXT,
        at INTEGER NOT NULL,
        PRIMARY KEY (name, day)
    )""",
)

#: The tables that belong to no one: market data and the job log. Every other table has
#: ``principal`` (a structural test holds the schema to it).
SHARED_TABLES = ("benchmark_close", "mf_scheme", "job_run")


def _v1(conn: Connection) -> None:
    for sql in V1:
        conn.exec_driver_sql(sql)


#: V2 (M7.19, after v0.5.0): a loan or liability belongs to one tab, so that tab shows net worth
#: (design 4.2 and 4.5). Additive: a link table; a liability made before this step has no row
#: here and is linked the first time its balance is set from a tab.
V2 = (
    """CREATE TABLE IF NOT EXISTS tab_liability (
        tab_id INTEGER NOT NULL,
        asset_id INTEGER NOT NULL,
        principal TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (tab_id, asset_id)
    )""",
    "CREATE INDEX IF NOT EXISTS tab_liability_asset ON tab_liability (principal, asset_id)",
)


def _v2(conn: Connection) -> None:
    for sql in V2:
        conn.exec_driver_sql(sql)


Step = tuple[int, Callable[[Connection], None]]
STEPS: tuple[Step, ...] = ((1, _v1), (2, _v2))
LATEST = STEPS[-1][0]


class SchemaTooNew(RuntimeError):
    """portfolio.db was migrated by a newer release; this one must not write to it.

    ``str()`` is the sentence for the owner (kinds.WORDS["store_newer"]); the versions are for
    the journal and ``--status``.
    """

    WORDS = (
        "Your portfolio data was written by a newer release. Install that release again; "
        "nothing was changed."
    )

    def __init__(self, found: int, known: int):
        self.found, self.known = found, known
        self.cause = f"file version {found}, this release knows up to {known}"
        super().__init__(self.WORDS)


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


# ---- first-start seeds ---------------------------------------------------------------------
ME = "Me"
UPSTOX_NAME = "Upstox"


def seed(conn: Connection, principal: str, now: int) -> list[str]:
    """Make the rows a new portfolio starts with; only what is missing, in one transaction.

    Member "Me" when the principal has no member at all; the Upstox account when no account is
    the system one (it belongs to the first member); the Upstox tab, first, when no tab is the
    system one. Returns what it made. Call inside an IMMEDIATE transaction.
    """
    made = []
    member = conn.exec_driver_sql(
        "SELECT id FROM member WHERE principal = ? ORDER BY id LIMIT 1", (principal,)
    ).scalar()
    if member is None:
        member = conn.exec_driver_sql(
            "INSERT INTO member (principal, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (principal, ME, now, now),
        ).lastrowid
        made.append("member")
    if not conn.exec_driver_sql(
        "SELECT 1 FROM account WHERE principal = ? AND system = 1", (principal,)
    ).first():
        conn.exec_driver_sql(
            "INSERT INTO account (principal, member_id, name, kind, broker, system, created_at, "
            "updated_at) VALUES (?, ?, ?, 'upstox', ?, 1, ?, ?)",
            (principal, member, UPSTOX_NAME, UPSTOX_NAME, now, now),
        )
        made.append("account")
    if not conn.exec_driver_sql(
        "SELECT 1 FROM tab WHERE principal = ? AND system = 1", (principal,)
    ).first():
        conn.exec_driver_sql(
            "INSERT INTO tab (principal, name, kind, icon, colour, position, pinned, system, "
            "created_at, updated_at) VALUES (?, ?, 'upstox', 'landmark', 'brand', 0, 1, 1, ?, ?)",
            (principal, UPSTOX_NAME, now, now),
        )
        made.append("tab")
    return made
