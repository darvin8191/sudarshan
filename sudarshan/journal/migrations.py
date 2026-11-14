"""journal.db's schema in numbered steps, run by the journal service itself at start (JB1, design 4.3).

The same shape as portfolio.db's (``sudarshan/portfolio/migrations.py``): each step is idempotent
(``CREATE ... IF NOT EXISTS``; a column is added only when ``PRAGMA table_info`` lacks it) and runs
in one transaction with ``PRAGMA user_version = N``, so a step that fails leaves the file as it
was. The store's engine emits its own BEGIN (``store.engine``); a step's transaction begins
IMMEDIATE, taking the write lock before it reads user_version, so two openers of a new file queue:
the second waits for the first to commit, then finds the step done.

A file whose user_version is newer than this code (a release rolled back after a newer one had
migrated it) is refused with a sentence for the owner, and only the journal stays off: an older
release must not write to a schema it does not know.

A step is frozen once released: its SQL never changes, and a later change is a new step.

Units (cross-design X3, Y31): money in integer paise, quantities in integer units, every price in
``price_u`` (rupees x 10^4: currency derivatives tick at 0.0025), instants in epoch milliseconds,
a trading day as ``YYYYMMDD``. Upstox's report rows keep the portfolio's ``qty_u`` (x 10^6), as
the portfolio's reader hands them over.

First-start seeds (``seed``): the default mistake tags and the default checklist (a playbook
named "General" whose rules are the checklist for a trade with no other playbook), each made only
when its table holds nothing of the principal's, so a tag the owner renamed or deleted is never
made again; and the report settings, made only when the principal has none.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence

from sqlalchemy.engine import Connection, Engine

#: An execution option the store's engine reads in its "begin" listener: this transaction
#: writes, so it begins IMMEDIATE and holds the write lock from its first read.
IMMEDIATE = "sudarshan_immediate"

#: Every owner-data table carries ``principal`` ("owner" in this release), ``created_at`` and
#: ``updated_at`` (epoch ms); the job log belongs to no one. Rows the owner writes carry a
#: ``version``; ``request_id`` makes a retried create return the row already made. There is no
#: ``broker_latency_ms`` (cross-design Y29: latency.db keeps no broker answer).
V1 = (
    # ---- what Upstox and the sandbox reported: one row per execution ----
    """CREATE TABLE IF NOT EXISTS fill (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        principal TEXT NOT NULL,
        mode TEXT NOT NULL,
        day INTEGER NOT NULL,
        trade_id TEXT NOT NULL,
        order_id TEXT NOT NULL,
        exchange TEXT NOT NULL,
        symbol TEXT NOT NULL,
        instrument_key TEXT NOT NULL DEFAULT '',
        product TEXT NOT NULL,
        action TEXT NOT NULL,
        qty INTEGER NOT NULL,
        price_u INTEGER NOT NULL,
        value_paise INTEGER NOT NULL,
        exch_ts_ms INTEGER NOT NULL,
        order_ts_ms INTEGER,
        source TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS fill_trade ON fill (principal, mode, trade_id)",
    "CREATE INDEX IF NOT EXISTS fill_day ON fill (principal, mode, day)",
    "CREATE INDEX IF NOT EXISTS fill_key ON fill (principal, instrument_key, exch_ts_ms)",
    "CREATE INDEX IF NOT EXISTS fill_order ON fill (principal, mode, order_id)",
    # ---- the placement behind each order: strategy label, surface, latency (M12.12) ----
    """CREATE TABLE IF NOT EXISTS order_ref (
        principal TEXT NOT NULL,
        mode TEXT NOT NULL,
        order_id TEXT NOT NULL,
        first_fill_ms INTEGER NOT NULL,
        placed_ms INTEGER,
        api_type TEXT,
        strategy TEXT,
        surface TEXT NOT NULL DEFAULT 'unknown',
        pricetype TEXT,
        product TEXT,
        limit_price_u INTEGER,
        trigger_price_u INTEGER,
        rtt_ms INTEGER,
        total_latency_ms INTEGER,
        status TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (principal, mode, order_id)
    )""",
    "CREATE INDEX IF NOT EXISTS order_ref_surface ON order_ref (principal, mode, surface)",
    # ---- round trips: the pairing's output, with the owner's own fields ----
    """CREATE TABLE IF NOT EXISTS round_trip (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        principal TEXT NOT NULL,
        mode TEXT NOT NULL,
        exchange TEXT NOT NULL,
        symbol TEXT NOT NULL,
        instrument_key TEXT NOT NULL DEFAULT '',
        product TEXT NOT NULL,
        product_class TEXT NOT NULL,
        side TEXT NOT NULL,
        qty_max INTEGER NOT NULL,
        entry_qty INTEGER NOT NULL,
        exit_qty INTEGER NOT NULL,
        entry_value_u INTEGER NOT NULL,
        exit_value_u INTEGER NOT NULL,
        entry_avg_u INTEGER NOT NULL,
        exit_avg_u INTEGER,
        opened_ms INTEGER NOT NULL,
        closed_ms INTEGER,
        open_day INTEGER NOT NULL,
        close_day INTEGER,
        status TEXT NOT NULL,
        gross_paise INTEGER,
        charges_est_paise INTEGER,
        charges_actual_paise INTEGER,
        net_paise INTEGER,
        hold_s INTEGER,
        strategy TEXT NOT NULL DEFAULT 'Manual',
        strategy_override TEXT,
        setup_id INTEGER,
        own_expiry_day INTEGER NOT NULL DEFAULT 0,
        index_expiry_day INTEGER NOT NULL DEFAULT 0,
        risk_paise INTEGER,
        mae_paise INTEGER,
        mfe_paise INTEGER,
        mae_ms INTEGER,
        mfe_ms INTEGER,
        excursion_source TEXT NOT NULL DEFAULT 'none',
        excursion_note TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        version INTEGER NOT NULL DEFAULT 1
    )""",
    "CREATE INDEX IF NOT EXISTS round_trip_status ON round_trip (principal, mode, status, opened_ms)",
    "CREATE INDEX IF NOT EXISTS round_trip_close ON round_trip (principal, mode, close_day)",
    """CREATE INDEX IF NOT EXISTS round_trip_key
        ON round_trip (principal, mode, exchange, symbol, product_class, opened_ms)""",
    """CREATE TABLE IF NOT EXISTS fill_leg (
        fill_id INTEGER NOT NULL,
        round_trip_id INTEGER NOT NULL,
        principal TEXT NOT NULL,
        role TEXT NOT NULL,
        qty INTEGER NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (fill_id, round_trip_id)
    )""",
    "CREATE INDEX IF NOT EXISTS fill_leg_trip ON fill_leg (principal, round_trip_id)",
    # ---- the price path seen live from the feed, kept for the recording's fallback (Y21) ----
    """CREATE TABLE IF NOT EXISTS trip_track (
        round_trip_id INTEGER PRIMARY KEY,
        principal TEXT NOT NULL,
        from_ms INTEGER NOT NULL,
        to_ms INTEGER,
        low_u INTEGER,
        low_ms INTEGER,
        high_u INTEGER,
        high_ms INTEGER,
        ticks INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )""",
    # ---- the owner's own words on a trade, tags and playbooks ----
    """CREATE TABLE IF NOT EXISTS trade_note (
        round_trip_id INTEGER PRIMARY KEY,
        principal TEXT NOT NULL,
        notes TEXT NOT NULL DEFAULT '',
        mistakes TEXT NOT NULL DEFAULT '[]',
        checklist TEXT NOT NULL DEFAULT '{}',
        mood TEXT,
        screenshots TEXT NOT NULL DEFAULT '[]',
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        version INTEGER NOT NULL DEFAULT 1
    )""",
    """CREATE TABLE IF NOT EXISTS tag (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        principal TEXT NOT NULL,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        colour TEXT NOT NULL DEFAULT 'neutral',
        archived INTEGER NOT NULL DEFAULT 0,
        system INTEGER NOT NULL DEFAULT 0,
        request_id TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        version INTEGER NOT NULL DEFAULT 1
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS tag_name ON tag (principal, kind, name COLLATE NOCASE)",
    """CREATE UNIQUE INDEX IF NOT EXISTS tag_request
        ON tag (principal, request_id) WHERE request_id IS NOT NULL""",
    """CREATE TABLE IF NOT EXISTS playbook (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        principal TEXT NOT NULL,
        name TEXT NOT NULL,
        name_alt TEXT,
        description TEXT NOT NULL DEFAULT '',
        rules TEXT NOT NULL DEFAULT '[]',
        default_risk_paise INTEGER,
        archived INTEGER NOT NULL DEFAULT 0,
        system INTEGER NOT NULL DEFAULT 0,
        request_id TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        version INTEGER NOT NULL DEFAULT 1
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS playbook_name ON playbook (principal, name COLLATE NOCASE)",
    """CREATE UNIQUE INDEX IF NOT EXISTS playbook_request
        ON playbook (principal, request_id) WHERE request_id IS NOT NULL""",
    # ---- a trading day's figures, taken at the day report and corrected by the close check ----
    """CREATE TABLE IF NOT EXISTS day_close (
        principal TEXT NOT NULL,
        mode TEXT NOT NULL,
        day INTEGER NOT NULL,
        realised_paise INTEGER,
        unrealised_paise INTEGER,
        approximate INTEGER NOT NULL DEFAULT 0,
        charges_est_paise INTEGER NOT NULL DEFAULT 0,
        trades_closed INTEGER NOT NULL DEFAULT 0,
        wins INTEGER NOT NULL DEFAULT 0,
        losses INTEGER NOT NULL DEFAULT 0,
        flats INTEGER NOT NULL DEFAULT 0,
        fills_live INTEGER NOT NULL DEFAULT 0,
        positions_overnight TEXT NOT NULL DEFAULT '[]',
        kill_switch TEXT,
        daily_lock TEXT,
        read_at INTEGER,
        reconciled_at INTEGER,
        reconcile_note TEXT,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (principal, mode, day)
    )""",
    # ---- what Upstox's profit and loss report said, kept as it said it (reconciliation) ----
    """CREATE TABLE IF NOT EXISTS upstox_pair (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        principal TEXT NOT NULL,
        segment TEXT NOT NULL,
        financial_year INTEGER NOT NULL,
        buy_date INTEGER,
        sell_date INTEGER,
        scrip_name TEXT NOT NULL,
        isin TEXT,
        trade_type TEXT,
        qty_u INTEGER NOT NULL,
        buy_avg_u INTEGER NOT NULL,
        sell_avg_u INTEGER NOT NULL,
        buy_amount_paise INTEGER NOT NULL,
        sell_amount_paise INTEGER NOT NULL,
        realised_paise INTEGER NOT NULL,
        raw_hash TEXT NOT NULL,
        read_at INTEGER NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS upstox_pair_hash ON upstox_pair (principal, raw_hash)",
    """CREATE TABLE IF NOT EXISTS upstox_charges (
        principal TEXT NOT NULL,
        segment TEXT NOT NULL,
        financial_year INTEGER NOT NULL,
        period_from INTEGER NOT NULL,
        period_to INTEGER NOT NULL,
        total_paise INTEGER,
        brokerage_paise INTEGER,
        gst_paise INTEGER,
        stt_paise INTEGER,
        stamp_paise INTEGER,
        transaction_paise INTEGER,
        clearing_paise INTEGER,
        ipft_paise INTEGER,
        others_paise INTEGER,
        read_at INTEGER NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (principal, segment, period_from, period_to)
    )""",
    # ---- the report archive (M15.10) and the owner's report settings ----
    """CREATE TABLE IF NOT EXISTS report (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        principal TEXT NOT NULL,
        kind TEXT NOT NULL,
        period_key TEXT NOT NULL,
        built_ms INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'ready',
        text TEXT NOT NULL,
        rows TEXT NOT NULL DEFAULT '{}',
        file_name TEXT,
        file_mime TEXT,
        file_sha256 TEXT,
        file_bytes BLOB,
        delivery TEXT NOT NULL DEFAULT '[]',
        reconciled_at INTEGER,
        told_ms INTEGER,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        version INTEGER NOT NULL DEFAULT 1
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS report_period ON report (principal, kind, period_key)",
    "CREATE INDEX IF NOT EXISTS report_built ON report (principal, built_ms)",
    """CREATE TABLE IF NOT EXISTS settings (
        principal TEXT NOT NULL PRIMARY KEY,
        value TEXT NOT NULL DEFAULT '{}',
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        version INTEGER NOT NULL DEFAULT 1
    )""",
    # ---- the job log: one row per job and day (design 3.5) ----
    """CREATE TABLE IF NOT EXISTS job_run (
        name TEXT NOT NULL,
        day INTEGER NOT NULL,
        status TEXT NOT NULL,
        detail TEXT,
        at INTEGER NOT NULL,
        PRIMARY KEY (name, day)
    )""",
)

#: The tables that belong to no one: the job log. Every other table has ``principal`` (a
#: structural test holds the schema to it).
SHARED_TABLES = ("job_run",)


def _v1(conn: Connection) -> None:
    for sql in V1:
        conn.exec_driver_sql(sql)


#: Step 2 (review B1): what the account held of an instrument before the journal's first fill of
#: it, read once from Upstox's overnight positions (derivatives) or holdings (shares) at first
#: sight. ``qty`` is signed (a short is negative) and describes the position at the start of
#: ``day``, the IST day of the read. A trip's two new states (``unmatched``, ``expired``) need no
#: change: ``round_trip.status`` is plain text with no CHECK.
V2 = (
    """CREATE TABLE IF NOT EXISTS key_opening (
        principal TEXT NOT NULL,
        mode TEXT NOT NULL,
        exchange TEXT NOT NULL,
        symbol TEXT NOT NULL,
        product_class TEXT NOT NULL,
        qty INTEGER NOT NULL,
        day INTEGER NOT NULL,
        source TEXT NOT NULL,
        read_ms INTEGER NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        PRIMARY KEY (principal, mode, exchange, symbol, product_class)
    )""",
)


def _v2(conn: Connection) -> None:
    for sql in V2:
        conn.exec_driver_sql(sql)


Step = tuple[int, Callable[[Connection], None]]
STEPS: tuple[Step, ...] = ((1, _v1), (2, _v2))
LATEST = STEPS[-1][0]


class SchemaTooNew(RuntimeError):
    """journal.db was migrated by a newer release; this one must not write to it.

    ``str()`` is the sentence for the owner; the versions are for the log and ``--status``.
    """

    WORDS = (
        "Your trade journal was written by a newer release. Install that release again; "
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
#: Design 4.3: the mistakes a trader names most often, in the owner's words.
MISTAKE_TAGS = (
    "FOMO",
    "Moved stop",
    "Early exit",
    "Late entry",
    "Oversized",
    "No plan",
    "Revenge",
    "Chased",
    "Held through news",
)
#: The checklist for a trade with no other playbook (design 4.3), as the rules of "General".
GENERAL = "General"
GENERAL_DESCRIPTION = "The checklist for a trade that follows no other playbook."
DEFAULT_CHECKLIST = (
    "Setup matches a playbook",
    "Stop placed",
    "Size within limit",
    "Not on expiry day unless planned",
    "No news in the next 15 minutes",
)
#: The report settings a new journal starts with (design 6.3): every report on, no trading
#: capital until the owner types one, English, and the plan the charges estimate assumes
#: (cross-design owner decision 7: Upstox Plus until he says otherwise).
DEFAULT_SETTINGS = {
    "capital_base_paise": None,
    "eod_on": True,
    "brief_on": True,
    "weekly_on": True,
    "monthly_on": True,
    "fy_on": True,
    "report_language": "en",
    "charges_plan": "plus",
}


def seed(conn: Connection, principal: str, now: int) -> list[str]:
    """Make the rows a new journal starts with; only what is missing, in one transaction.

    The mistake tags when the principal has no tag at all; the "General" playbook (the default
    checklist) when he has no playbook; the report settings when he has none. Returns what it
    made. Call inside an IMMEDIATE transaction.
    """
    made = []
    if not conn.exec_driver_sql(
        "SELECT 1 FROM tag WHERE principal = ? LIMIT 1", (principal,)
    ).first():
        for name in MISTAKE_TAGS:
            conn.exec_driver_sql(
                "INSERT INTO tag (principal, kind, name, colour, system, created_at, updated_at) "
                "VALUES (?, 'mistake', ?, 'neutral', 1, ?, ?)",
                (principal, name, now, now),
            )
        made.append("tags")
    if not conn.exec_driver_sql(
        "SELECT 1 FROM playbook WHERE principal = ? LIMIT 1", (principal,)
    ).first():
        conn.exec_driver_sql(
            "INSERT INTO playbook (principal, name, description, rules, system, created_at, "
            "updated_at) VALUES (?, ?, ?, ?, 1, ?, ?)",
            (
                principal,
                GENERAL,
                GENERAL_DESCRIPTION,
                json.dumps(list(DEFAULT_CHECKLIST)),
                now,
                now,
            ),
        )
        made.append("checklist")
    if not conn.exec_driver_sql("SELECT 1 FROM settings WHERE principal = ?", (principal,)).first():
        conn.exec_driver_sql(
            "INSERT INTO settings (principal, value, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (principal, json.dumps(DEFAULT_SETTINGS, sort_keys=True), now, now),
        )
        made.append("settings")
    return made
