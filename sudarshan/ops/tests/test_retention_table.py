"""README, "What is kept, and for how long" (M18.12, GA5.4): each number in the table is the
one the code enforces. A limit changed in the code without the table, or the other way round,
fails here."""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

README = Path(__file__).resolve().parents[2] / "README.md"
REPO = README.parents[1]


def _table() -> dict[str, list[str]]:
    text = README.read_text(encoding="utf-8")
    section = text.split("## What is kept, and for how long (M18.12)", 1)[1].split("\n## ", 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("| ")][1:]
    return {cells[0] + " / " + cells[1]: cells for cells in
            ([c.strip() for c in row.strip("|").split("|")] for row in rows)}  # fmt: skip


def _row(table: dict[str, list[str]], *words: str) -> str:
    found = [" | ".join(cells) for key, cells in table.items() if all(w in key for w in words)]
    assert len(found) == 1, (words, found)
    return found[0]


def test_every_number_in_the_table_is_the_codes_own():
    from sudarshan.alerts import store as alerts_store
    from sudarshan.alerts.market import store as market
    from sudarshan.automation import store as automation
    from sudarshan.family import outbox as family_outbox
    from sudarshan.family import sessions, symbol_map
    from sudarshan.feed import service as feed
    from sudarshan.guard import store as guard
    from sudarshan.journal.jobs import catchup
    from sudarshan.ops import events
    from sudarshan.ops.backup import Settings as BackupSettings
    from sudarshan.ops.watch import checks, diary
    from sudarshan.screener import store as screener
    from sudarshan.signals import store as signals
    from sudarshan.vault import store as vault

    table = _table()
    day_ms = 86_400_000
    assert automation.KEEP_DAYS == 90 and automation.KEEP_ROWS == 20_000
    assert "90 days, and at most 20,000 lines" in _row(table, "sudarshan_automation")
    assert guard.AUDIT_KEEP == timedelta(days=90)
    assert "| 90 days |" in _row(table, "sudarshan_risk.db", "history")
    assert vault.ATTEMPT_RETENTION == timedelta(days=30)
    assert "auto-login tries 30 days" in _row(table, "sudarshan-vault")
    assert feed.SNAPSHOT_KEEP_DAYS == 30 and "30 days" in _row(table, "feed snapshot")
    assert "7 days, and at most 1,024 MB a day" in _row(table, "feed recording")
    assert alerts_store.KEEP == timedelta(days=7)
    assert alerts_store.KEEP_NOTICES == timedelta(days=60)
    sent = _row(table, "`alerts.db`")
    assert "| 7 days;" in sent and "60 days" in sent
    assert (market.KEEP_DAYS, market.MIN_KEEP_DAYS, market.BASELINE_DAYS) == (100, 90, 10)
    assert (market.FY_START_MONTH, market.FY_HELD_THROUGH_MONTH) == (4, 4)
    history = _row(table, "market.db", "history")
    assert "100 days (never under 90)" in history and "the April after it" in history
    assert "| 10 days |" in _row(table, "market.db", "baselines")
    assert (screener.KEEP_RUN_DAYS_MIN, screener.KEEP_VALUE_DAYS, screener.KEEP_OUTBOX_DAYS) == (
        90, 7, 2,
    )  # fmt: skip
    runs = _row(table, "screener.db", "runs")
    assert "runs 100 days (never under 90); values 7 days; sent messages 2 days" in runs
    assert catchup.KEEP_JOB_DAYS == 400 and "job log 400 days" in _row(table, "journal.db")
    assert sessions.KEEP_ENDED_MS == 90 * day_ms and family_outbox.KEEP_SENT_MS == 7 * day_ms
    assert symbol_map.KEEP_MS == 30 * day_ms
    family = _row(table, "family.db")
    for words in ("ended sign-ins 90 days", "sent messages 7 days", "instrument names 30 days"):
        assert words in family
    assert signals.DOC_KEEP_MS == 30 * day_ms and signals.COUNTER_KEEP_DAYS == 40
    kept = _row(table, "signals.db")
    assert "as received 30 days" in kept and "counters 40 days" in kept
    assert events.KEEP_LINES == 200 and "newest 200 lines" in _row(table, "notice files")
    assert diary.KEEP_YEARS == 8 and "| 8 years |" in _row(table, "server diary")
    assert checks.HISTORIFY_WARN_GB == 3.0 and "from 3 GB" in _row(table, "historify")
    assert (checks.DISK_WARN_GB, checks.DISK_FAIL_GB) == (15.0, 5.0)
    defaults = BackupSettings.from_env({})
    assert (defaults.keep_daily, defaults.keep_monthly, defaults.keep_yearly) == (30, 12, 8)
    assert defaults.max_bytes == 10 << 30
    assert "30 daily, 12 monthly, one for each of 8 financial years, inside a 10 GB cap" in _row(
        table, "backups"
    )


def test_the_settings_the_table_names_have_the_defaults_it_says():
    """Read from the code that reads them, never imported: OpenAlgo's modules start services."""
    table = _table()

    def source(path: str) -> str:
        return (REPO / path).read_text(encoding="utf-8")

    assert 'os.getenv("TRAFFIC_LOG_RETENTION_DAYS", "30")' in source("database/traffic_db.py")
    assert "| 30 days, cleared when OpenAlgo starts |" in _row(table, "logs.db")
    assert 'os.getenv("HEALTH_RETENTION_DAYS", "7")' in source("utils/health_monitor.py")
    assert "| 7 days |" in _row(table, "health.db")
    assert "purge_old_data_logs(days=7)" in source("utils/latency_monitor.py")
    assert "7 days (orders: for ever)" in _row(table, "latency.db")
    logging_source = source("utils/logging.py")
    assert 'os.getenv("LOG_RETENTION", "14")' in logging_source
    assert "lines[-1000:]" in logging_source
    assert "14 days; the newest 1,000 errors" in _row(table, "`log/`")
    feed_config = source("sudarshan/feed/config.py")
    assert '"SUDARSHAN_FEED_RECORD_KEEP_DAYS", "7"' in feed_config
    assert '"SUDARSHAN_FEED_RECORD_MAX_MB", "1024"' in feed_config
    assert '"SUDARSHAN_SCREENER_KEEP_DAYS", 100' in source("sudarshan/screener/config.py")
    assert '"SUDARSHAN_MARKET_ALERTS_KEEP_DAYS", 100, 90' in source(
        "sudarshan/alerts/market/config.py"
    )


def test_what_is_kept_for_ever_has_nothing_that_removes_it():
    """The order log is the order record: no purge of it exists in OpenAlgo's two log modules."""
    for path in ("database/apilog_db.py", "database/analyzer_db.py"):
        text = (REPO / path).read_text(encoding="utf-8")
        assert not re.search(r"\.delete\(|DELETE FROM|def purge", text), path
    table = _table()
    assert "nothing removes order log rows" in _row(table, "openalgo.db")
    # the portfolio names a 400-day figure for its job rows and nothing enforces it: the table
    # says what happens, which is that they stay
    portfolio = (REPO / "sudarshan/portfolio").rglob("*.py")
    uses = [p for p in portfolio if "tests" not in p.parts and "JOB_KEEP_DAYS" in p.read_text()]
    assert [p.name for p in uses] == ["kinds.py"]
    assert "no rule removes job rows" in _row(table, "portfolio.db")
