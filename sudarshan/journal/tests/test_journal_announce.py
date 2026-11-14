"""A scheduled report reaches the report desk even when the Build button already made its row,
and the same build never goes twice (review M2): a build is announced when the report was never
announced, or was announced as a part period and this build is the whole one; the row is claimed
before the line is written and released when the line could not be, so a fault after the build
announces on the retry. Against the journal's rig; every value is synthetic."""

from __future__ import annotations

import pytest
from test_journal_analytics import book as book  # noqa: F401  (a fixture)
from test_journal_capture import DAY, ist_ms, scenario
from test_journal_capture import files as files  # noqa: F401  (a fixture)
from test_journal_capture import rig as rig  # noqa: F401  (a fixture)
from test_journal_reports import call, close_the_day, spool

from sudarshan.journal import periods, reports
from sudarshan.journal.store import Scope, StoreError

OWNER = Scope.owner()


def month_row(rig):
    return rig.svc.store.read(OWNER, lambda tx: tx.rows("report", kind="monthly"))[0]


def job(rig, kind, today):
    return scenario(rig, lambda: periods.period_job(rig.svc, kind, today, rig.now))


def test_a_month_built_on_request_is_still_sent_whole_by_the_job_on_the_first(rig, book):  # noqa: F811
    rig.now = ist_ms(20261120, "11:00:00")
    call(rig, "report_build", kind="monthly")  # the 20th: November so far
    first = month_row(rig)
    assert first["rows"]["partial"] is True and len(spool(rig)) == 1
    rig.now = ist_ms(20261201, "07:00:00")
    job(rig, "monthly", 20261201)
    whole = month_row(rig)
    assert whole["id"] == first["id"] and whole["rows"]["partial"] is False
    lines = spool(rig)
    assert len(lines) == 2  # before, the row existed and nothing went
    assert lines[1].id == f"report-{whole['id']}-{whole['built_ms']}" != lines[0].id
    assert lines[1].fact("sha256") == whole["file_sha256"]


def test_the_same_build_is_never_announced_twice(rig, book):  # noqa: F811
    rig.now = ist_ms(20261201, "07:00:00")
    job(rig, "monthly", 20261201)
    job(rig, "monthly", 20261201)  # a second run of the job
    rig.now = ist_ms(20261203, "10:00:00")
    call(rig, "report_build", kind="monthly", period="202611")  # the whole month, on request
    call(rig, "report_build", kind="monthly")  # December so far: a report of its own
    kinds = [(e.fact("report_kind"), e.fact("report")) for e in spool(rig)]
    assert len(kinds) == 2 and kinds[0][1] != kinds[1][1]


def test_a_fault_after_the_row_is_made_announces_on_the_retry(rig, book, monkeypatch):  # noqa: F811
    real = periods.keep_file
    failures = []

    async def failing(*args, **kwargs):
        if not failures:
            failures.append(1)
            raise StoreError("This could not be saved.", "disk full")
        return await real(*args, **kwargs)

    monkeypatch.setattr(periods, "keep_file", failing)
    rig.now = ist_ms(20261201, "07:00:00")
    with pytest.raises(StoreError):
        job(rig, "monthly", 20261201)
    assert spool(rig) == [] and month_row(rig)["announced_ms"] is None  # the row, not announced
    rig.now = ist_ms(20261201, "07:15:00")
    job(rig, "monthly", 20261201)  # the job's retry 15 minutes later
    (line,) = spool(rig)
    assert line.fact("report") == str(month_row(rig)["id"])


def test_a_line_that_could_not_be_written_is_written_by_the_next_build(rig, book, monkeypatch):  # noqa: F811
    real = reports.record
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        return False if len(calls) == 1 else real(*args, **kwargs)

    monkeypatch.setattr(reports, "record", flaky)
    rig.now = ist_ms(20261201, "07:00:00")
    job(rig, "monthly", 20261201)
    assert spool(rig) == [] and month_row(rig)["announced_ms"] is None  # released
    rig.now = ist_ms(20261201, "07:15:00")
    job(rig, "monthly", 20261201)
    assert len(spool(rig)) == 1


def test_a_day_report_built_on_request_before_the_close_is_sent_again_at_the_close(rig):
    rig.now = ist_ms(DAY, "15:00:00")
    close_the_day(rig)  # the 15:45 job's work, made at the close
    eod = rig.svc.store.read(OWNER, lambda tx: tx.rows("report", kind="eod"))[0]
    assert len(spool(rig)) == 1
    # A Build before the close (another day, 14:00): a part; the job's build at its time goes.
    tue = 20261110
    rig.svc.store.write(OWNER, lambda tx: tx.day_close_put("live", tue, {"read_at": 1}, 1))
    rig.now = ist_ms(tue, "14:00:00")
    call(rig, "report_build", kind="eod", period=str(tue))
    rig.now = ist_ms(tue, "15:45:00")
    scenario(rig, lambda: reports.build_eod(rig.svc, tue, rig.now))
    tue_lines = [e for e in spool(rig) if e.fact("report") != str(eod["id"])]
    assert len(tue_lines) == 2
    # and a Build after the close changes nothing: that build was announced whole
    rig.now = ist_ms(tue, "18:00:00")
    call(rig, "report_build", kind="eod", period=str(tue))
    assert len([e for e in spool(rig) if e.fact("report") != str(eod["id"])]) == 2


def test_step_four_backfills_each_kept_report_as_announced_when_it_was_made(tmp_path):
    """journal.db forced back to step 3 with reports in it: each was announced when its row was
    made (the rule until now), as a part period when its rows say so."""
    import json
    import sqlite3

    from sudarshan.journal import migrations, store
    from sudarshan.journal.store import JournalStore

    path = tmp_path / store.DB_FILENAME
    engine = store.engine(path)
    try:
        migrations.migrate(engine, migrations.STEPS[:3])
    finally:
        engine.dispose()
    rows = [("monthly", "202611", {"partial": True}, 111), ("eod", "20261109", {}, 222),
            ("weekly", "2026W45", {"partial": False}, 333)]  # fmt: skip
    with sqlite3.connect(path) as conn:
        for kind, key, extra, made in rows:
            conn.execute(
                "INSERT INTO report (principal, kind, period_key, built_ms, text, rows, "
                "created_at, updated_at) VALUES ('owner', ?, ?, ?, 'x', ?, ?, ?)",
                (kind, key, made, json.dumps(extra), made, made),
            )
    opened = JournalStore(path, now=1)
    try:
        assert opened.applied == [n for n, _ in migrations.STEPS if n > 3]
        got = opened.read(OWNER, lambda tx: [(r["kind"], r["announced_ms"], r["announced_partial"])
                                             for r in tx.rows("report", order="id")])  # fmt: skip
        assert got == [("monthly", 111, True), ("eod", 222, False), ("weekly", 333, False)]
    finally:
        opened.close()
        store.stop()
