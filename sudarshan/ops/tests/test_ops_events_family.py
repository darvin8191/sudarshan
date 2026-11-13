"""E1: the notice file takes the journal's and the family service's lines (cross-design Y8).

Two new sources and the kinds of both designs; a spool another account reads is written 0640 and
stays 0640 through compaction; a folder that exists is never touched; a report line carries ids
and a digest, never text.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from sudarshan.ops import events
from sudarshan.ops.events import parse_line, read_events, record

NOW = 1_792_000_000.0
FAMILY_KINDS = (
    "lockout",
    "new_device",
    "invite_used",
    "member_removed",
    "push_failing",
    "bot_token_rejected",
    "store_newer",
)


def line(**fields) -> str:
    base = {"v": "1", "id": "x-1", "src": "family", "kind": "lockout", "at": "1792000000"}
    return json.dumps(base | fields)


@pytest.mark.parametrize("kind", FAMILY_KINDS)
def test_every_family_kind_parses_and_is_recorded(tmp_path, kind):
    assert parse_line(line(kind=kind)).kind == kind
    spool = tmp_path / "events.jsonl"
    assert record(spool, kind=kind, event_id=f"f-{kind}", at=NOW, run="r", src="family")
    assert [e.kind for e in read_events(spool)] == [kind]


def test_the_deploy_check_kind_is_a_deploy_event(tmp_path):
    assert parse_line(line(kind="family_check_failed", src="deploy")).src == "deploy"
    spool = tmp_path / "events.jsonl"
    assert record(spool, kind="family_check_failed", event_id="d1", at=NOW, run="r", src="deploy")
    assert read_events(spool)[0].kind == "family_check_failed"


def test_a_report_line_carries_ids_and_a_digest_and_no_text(tmp_path):
    spool = tmp_path / "events.jsonl"
    digest = "a" * 64
    assert record(
        spool, kind="report", event_id="rep-1", at=NOW, run="rep-1", src="journal",
        report="rep-1", report_kind="weekly", sha256=digest,
    )  # fmt: skip
    raw = json.loads(spool.read_text())
    assert set(raw) == {"v", "id", "src", "kind", "at", "run", "report", "report_kind", "sha256"}
    event = read_events(spool)[0]
    assert (event.src, event.kind) == ("journal", "report")
    assert event.fact("sha256") == digest and event.fact("report") == "rep-1"


def test_a_source_or_kind_that_is_not_listed_is_refused(tmp_path):
    spool = tmp_path / "events.jsonl"
    assert not record(spool, kind="lockout", event_id="a", at=NOW, run="r", src="portfolio")
    assert not record(spool, kind="nonsense", event_id="b", at=NOW, run="r", src="family")
    assert parse_line(line(src="portfolio")) is None


def test_the_spool_paths_and_the_notice_desks_default():
    assert str(events.FAMILY_SPOOL) == "/var/lib/sudarshan-family-export/events.jsonl"
    assert str(events.JOURNAL_SPOOL) == "/var/lib/sudarshan-journal/events.jsonl"
    assert events.DEFAULT_SPOOLS == (events.DEPLOY_SPOOL, events.BACKUP_SPOOL, events.FAMILY_SPOOL)
    assert events.JOURNAL_SPOOL not in events.DEFAULT_SPOOLS  # the report desk reads it, not this


@pytest.fixture(autouse=True)
def _umask():
    old = os.umask(0o022)
    yield
    os.umask(old)


def mode_of(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_a_new_file_is_private_unless_a_mode_is_asked_for(tmp_path):
    private, shared = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    assert record(private, kind="lockout", event_id="a", at=NOW, run="r", src="family")
    assert record(shared, kind="lockout", event_id="a", at=NOW, run="r", src="family",
                  file_mode=0o640)  # fmt: skip
    assert mode_of(private) == 0o600
    assert mode_of(shared) == 0o640


def test_compaction_keeps_the_files_mode(tmp_path):
    spool = tmp_path / "events.jsonl"
    for number in range(events.COMPACT_ABOVE + 5):
        assert record(spool, kind="lockout", event_id=f"e{number}", at=NOW, run="r", src="family",
                      file_mode=0o640)  # fmt: skip
    kept = spool.read_text().splitlines()
    assert len(kept) <= events.COMPACT_ABOVE  # compaction ran
    assert mode_of(spool) == 0o640
    assert not list(tmp_path.glob(".*.new"))


def test_compaction_does_not_widen_a_private_file(tmp_path):
    spool = tmp_path / "events.jsonl"
    for number in range(events.COMPACT_ABOVE + 5):
        record(spool, kind="lockout", event_id=f"e{number}", at=NOW, run="r", src="family")
    assert mode_of(spool) == 0o600


def test_a_folder_that_exists_is_neither_made_nor_changed(tmp_path):
    folder = tmp_path / "export"
    folder.mkdir(mode=0o750)
    folder.chmod(0o2750)
    before = folder.stat()
    spool = folder / "events.jsonl"
    assert record(spool, kind="lockout", event_id="a", at=NOW, run="r", src="family",
                  file_mode=0o640)  # fmt: skip
    after = folder.stat()
    assert stat.S_IMODE(after.st_mode) == stat.S_IMODE(before.st_mode)
    assert after.st_ino == before.st_ino


def test_a_missing_family_file_reads_as_no_events(tmp_path):
    assert read_events(tmp_path / "missing" / "events.jsonl") == []
