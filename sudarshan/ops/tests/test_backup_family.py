"""O1b (cross-design Y9): the nightly backup and the family view's folders.

The family service keeps its store in its own state folder, which belongs to its own account and is
closed (0700) to the nightly job, which runs as ``sudarshan``. A folder the job cannot list is
recorded as a failure (the archive is partial and the owner is told), so the family's folder is
passed over by name instead; the family service leaves a copy of its store for the job in its export
folder (``family-copy.db``, cross-design Y9 and P6 F11), and that one file is taken from there.

Run as root, a test sees every folder; so the folder the job could not list is made so here by
refusing its listing (a PermissionError, as the job's own account would get).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from backup_fakes import Archive, Server, go, make_database

from sudarshan.ops import backup
from sudarshan.ops.backup import OK, PARTIAL

ARCHIVE = "sudarshan-backup-20261027-163102.tar.gz"


@pytest.fixture
def server(tmp_path):
    pretend = Server(tmp_path / "server")
    yield pretend
    pretend.close()


@pytest.fixture
def foreign(monkeypatch):
    """Folders the job's account cannot list, by path: listing one raises as for a 0700 folder of
    another account (also when the tests run as root, who could list it)."""
    closed: set[str] = set()
    real = os.scandir

    def scandir(path="."):
        if not isinstance(path, int) and os.fspath(path) in closed:  # an int is an open folder
            raise PermissionError(13, "Permission denied", os.fspath(path))
        return real(path)

    monkeypatch.setattr(backup.os, "scandir", scandir)
    return closed


def family_server(server: Server, foreign: set[str]) -> tuple[Path, Path]:
    server.populate()
    own = server.var / "sudarshan-family"
    make_database(own / "family.db", rows=4)
    own.chmod(0o700)
    foreign.add(str(own))
    copy = server.var / "sudarshan-family-export" / "family-copy.db"
    make_database(copy, rows=4)
    return own, copy


def test_the_family_folder_is_passed_over_and_its_store_copy_is_found(server, foreign):
    own, copy = family_server(server, foreign)
    found = backup.discover(server.settings())
    assert found.skipped == []  # no failure for the folder the job cannot read
    arcs = {source.arc: source for source in found.sources}
    assert "ring/sudarshan-family-export/family-copy.db" in arcs
    assert arcs["ring/sudarshan-family-export/family-copy.db"].kind == "sqlite"
    assert not [arc for arc in arcs if arc.startswith("ring/sudarshan-family/")]


def test_a_folder_the_job_cannot_list_under_any_other_name_is_still_told(server, foreign):
    """The control: the pass-over is by name, so the rule that nothing is left out quietly holds."""
    server.populate()
    other = server.var / "sudarshan-other"
    make_database(other / "other.db")
    foreign.add(str(other))
    assert backup.discover(server.settings()).skipped == ["ring/sudarshan-other/"]


def test_only_the_store_copy_is_taken_from_the_export_folder(server, foreign):
    """The folder is written by the family account, which takes input from outside the server."""
    _own, copy = family_server(server, foreign)
    export = copy.parent
    make_database(export / "planted.db")
    (export / "notes.db").write_text("not a database")
    (export / "events.jsonl").write_text("{}\n")
    found = backup.discover(server.settings())
    taken = [s.arc for s in found.sources if s.arc.startswith("ring/sudarshan-family-export/")]
    assert taken == ["ring/sudarshan-family-export/family-copy.db"]
    assert found.skipped == []


def test_a_store_copy_that_is_a_link_is_not_followed(server, foreign):
    server.populate()
    export = server.var / "sudarshan-family-export"
    export.mkdir()
    (export / "family-copy.db").symlink_to(server.app / "db" / "openalgo.db")
    found = backup.discover(server.settings())
    assert not [s for s in found.sources if "family" in s.arc] and found.skipped == []


def test_a_night_with_the_family_folders_makes_a_whole_archive_holding_the_copy(server, foreign):
    family_server(server, foreign)
    result = go(server)
    assert (result.outcome, result.failures) == (OK, [])
    archive = Archive(server.archive_dir / ARCHIVE)
    assert "ring/sudarshan-family-export/family-copy.db" in archive.files
    assert not [name for name in archive.files if name.startswith("ring/sudarshan-family/")]
    assert server.events() == []  # nothing to tell the owner


def test_a_store_copy_the_job_cannot_read_is_told_as_any_other_source(server, foreign, monkeypatch):
    """The family service must leave it readable by the group (0640): if not, the owner hears of it."""
    _own, copy = family_server(server, foreign)
    real = backup.snapshot

    def snapshot(source, dest, **kwargs):
        if Path(source) == copy:
            raise backup.SourceError("unable to open database file")
        return real(source, dest, **kwargs)

    monkeypatch.setattr(backup, "snapshot", snapshot)
    result = go(server)
    assert result.outcome == PARTIAL
    assert [f.names for f in result.failures] == [("ring/sudarshan-family-export/family-copy.db",)]
