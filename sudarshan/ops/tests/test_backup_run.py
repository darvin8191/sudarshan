"""M18.11: one night's backup, end to end, on a pretend server (real folders, real SQLite files).

What the owner relies on, and what these tests pin:

  The archive holds every database as SQLite's own backup of it, rows still in the write-ahead log
  included, the sealed vault folder with its modes, /etc/sudarshan, the ring's databases and the
  app's .env; it is checked by reading it back; it is published whole or not at all.
  The sources are never changed.
  A source that cannot be copied costs that source, not the night: the archive is made without it,
  is called partial, and the owner is told which file.
  The job stays out of market hours, out of the deploy tool's way and out of a second run's.
  Old archives go by the counts and the cap, never the newest good one, and a disk too full for
  the archive is said so before anything is written.
"""

from __future__ import annotations

import errno
import json
import os
import shutil
import sqlite3
import stat
import tarfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from backup_fakes import (
    NIGHT,
    Archive,
    Server,
    Sleeps,
    count_rows,
    damage,
    go,
    ist,
    make_database,
    no_sleep,
    open_files,
    sha,
)

from sudarshan.autologin.schedule import IST, TradingCalendar
from sudarshan.ops import backup, retention
from sudarshan.ops.backup import CAP, FAILED, INTEGRITY, OK, PARTIAL, SKIPPED, SOURCE, SPACE, WRITE

ARCHIVE = "sudarshan-backup-20261027-163102.tar.gz"
PARTIAL_ARCHIVE = "sudarshan-backup-20261027-163102-partial.tar.gz"


@pytest.fixture
def server(tmp_path):
    pretend = Server(tmp_path / "server")
    yield pretend
    pretend.close()


def the_archive(server: Server, name: str = ARCHIVE) -> Archive:
    return Archive(server.archive_dir / name)


def count_tables(content: bytes, scratch: Path) -> int:
    scratch.parent.mkdir(parents=True, exist_ok=True)
    scratch.write_bytes(content)
    con = sqlite3.connect(f"file:{scratch}?mode=ro&immutable=1", uri=True)
    try:
        return con.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
    finally:
        con.close()


# ---- what goes in -----------------------------------------------------------------------------


def test_a_plain_copy_of_a_live_database_would_lose_its_latest_rows(server):
    """The defect the online backup exists for: the app's newest rows are in the write-ahead log,
    not in the database file, so copying the file alone gives a database that is days behind."""
    server.populate()
    only_the_file = server.app / "db" / "openalgo.db"
    log = Path(f"{only_the_file}-wal")
    assert log.stat().st_size > only_the_file.stat().st_size
    try:
        rows = count_rows(only_the_file.read_bytes(), server.root / "scratch" / "plain.db")
    except sqlite3.OperationalError:  # the table itself is still only in the log
        rows = 0
    assert rows < 40


def test_a_night_makes_one_archive_of_everything_that_cannot_be_rebuilt(server):
    plain = server.populate()
    before = {
        path: path.read_bytes()
        for path in (
            server.app / "db" / "openalgo.db",
            Path(f"{server.app / 'db' / 'openalgo.db'}-wal"),
            server.app / "db" / "sandbox.db",
            server.app / "db" / "sudarshan-vault" / "sudarshan_vault.db",
            server.var / "sudarshan-alerts" / "alerts.db",
        )
    }

    result = go(server)

    assert (result.outcome, result.reason, result.archive) == (OK, "", ARCHIVE)
    assert result.failures == [] and result.exit_code == 0 and result.offsite == "off"
    assert server.archives() == [ARCHIVE]
    archive = the_archive(server)
    databases = {
        "db/openalgo.db",
        "db/sandbox.db",
        "db/logs.db",
        "db/sudarshan-vault/sudarshan_vault.db",
        "db/sudarshan-books/books.db",
        "ring/sudarshan-alerts/alerts.db",
        "ring/sudarshan-alerts/market.db",
        "ring/sudarshan-feed/feed.db",
    }
    assert archive.files == databases | set(plain) | {backup.MANIFEST}
    # Not taken: market history, loose files and folders that are not ours, the job's own state,
    # a link, a database's side files.
    assert not {n for n in archive.files if "duckdb" in n or "notes" in n or "not-ours" in n}
    assert not {n for n in archive.files if "other-system" in n or "own.db" in n or "link" in n}
    assert not {n for n in archive.files if n.endswith(("-wal", "-shm"))}

    # Plain files are the bytes that were on disk.
    for name, content in plain.items():
        assert archive.data[name] == content, name

    # The rows still in the write-ahead log are in the copy.
    scratch = server.root / "scratch"
    assert count_rows(archive.data["db/openalgo.db"], scratch / "a.db") == 40
    assert count_rows(archive.data["db/sandbox.db"], scratch / "b.db") == 5
    assert count_rows(archive.data["ring/sudarshan-alerts/alerts.db"], scratch / "c.db") == 6

    # Nothing it read was changed.
    for path, content in before.items():
        assert path.read_bytes() == content, path


def test_the_vault_keeps_its_folder_and_file_modes_inside_the_archive(server):
    server.populate()
    go(server)
    archive = the_archive(server)

    assert stat.S_IMODE(archive.info["db/sudarshan-vault"].mode) == 0o700
    assert stat.S_IMODE(archive.info["db/sudarshan-vault/sudarshan_vault.db"].mode) == 0o600
    assert stat.S_IMODE(archive.info["db/sudarshan-vault/key.salt"].mode) == 0o600
    assert stat.S_IMODE(archive.info["app/openalgo.env"].mode) == 0o600
    assert stat.S_IMODE(archive.info["etc/sudarshan/feed.env"].mode) == 0o640
    assert archive.info["db/sudarshan-vault"].isdir()
    assert stat.S_IMODE(archive.info["db/sudarshan-books"].mode) == 0o755
    assert archive.folders >= {"db", "db/sudarshan-vault", "etc/sudarshan", "etc/sudarshan/sub"}
    assert archive.folders >= {"ring", "ring/sudarshan-alerts", "ring/sudarshan-feed", "app"}


@pytest.mark.skipif(os.geteuid() != 0, reason="needs the right to give a file to another user")
def test_the_owner_and_group_of_a_source_are_recorded_even_when_no_such_user_exists(server):
    server.populate()
    os.chown(server.etc / "feed.env", 4242, 4343)
    go(server)
    item = the_archive(server).info["etc/sudarshan/feed.env"]
    assert (item.uid, item.gid, item.uname, item.gname) == (4242, 4343, "", "")
    root = the_archive(server).info["etc/sudarshan/sub/inner.txt"]
    assert (root.uid, root.gid, root.uname, root.gname) == (0, 0, "root", "root")


def test_the_manifest_says_what_is_in_the_archive_and_how_to_check_it(server):
    server.populate()
    go(server)
    archive = the_archive(server)
    manifest = archive.manifest

    assert manifest["format"] == 1 and manifest["partial"] is False
    assert manifest["not_saved"] == []
    assert manifest["release"] == "v0.3.4"
    assert manifest["created"] == "2026-10-27T16:31:02+05:30"
    listed = {m["name"]: m for m in manifest["members"]}
    assert set(listed) == archive.files - {backup.MANIFEST}
    for name, entry in listed.items():
        assert entry["size"] == len(archive.data[name]), name
        assert entry["sha256"] == sha(archive.data[name]), name
        assert entry["kind"] == ("sqlite" if name.endswith(".db") else "file"), name
        assert entry.get("integrity", "") == ("ok" if entry["kind"] == "sqlite" else ""), name
    assert archive.info[backup.MANIFEST].isreg()
    assert list(archive.info)[-1] == backup.MANIFEST  # written last, once everything is in


def test_the_name_and_the_manifest_tell_indian_time_whatever_zone_the_clock_is_in(server):
    server.populate()
    result = go(server, NIGHT.astimezone(UTC))  # 11:01:02 UTC is 16:31:02 in India
    assert result.archive == ARCHIVE
    assert the_archive(server).manifest["created"] == "2026-10-27T16:31:02+05:30"
    assert server.status()["at"] == "2026-10-27T16:31:02+05:30"
    assert go(server, ist(2026, 10, 27, 22, 30).astimezone(UTC)).reason == "fresh"
    # 03:00 in India the next morning is 21:30 UTC the evening before: 10.5 hours on, not 5.
    assert go(server, ist(2026, 10, 28, 3, 0).astimezone(UTC)).outcome == OK


def test_the_manifest_names_no_release_when_the_server_has_none_on_record(server):
    server.populate()
    server.tag.unlink()
    go(server)
    assert the_archive(server).manifest["release"] is None


def test_the_archive_and_its_folders_are_private_and_nothing_is_left_behind(server):
    server.populate()
    go(server)
    assert stat.S_IMODE((server.archive_dir / ARCHIVE).stat().st_mode) == 0o600
    assert stat.S_IMODE(server.archive_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(server.state.stat().st_mode) == 0o700
    assert not list(server.archive_dir.glob("*.partial"))
    assert list((server.state / "work").iterdir()) == []  # the staging folder is removed


def test_a_server_with_nothing_to_back_up_says_so_instead_of_making_an_empty_archive(server):
    shutil.rmtree(server.etc)  # the bare server's only files were the calendar's, in /etc
    result = go(server)
    assert result.outcome == FAILED and [f.why for f in result.failures] == ["other"]
    assert server.archives() == []
    assert [e.fact("why") for e in server.events()] == ["other"]


def test_an_empty_database_file_is_copied_as_the_empty_database_it_is(server):
    server.populate()
    (server.app / "db" / "fresh.db").write_bytes(b"")
    result = go(server)
    assert result.outcome == OK
    copy = the_archive(server).data["db/fresh.db"]
    assert copy.startswith(b"SQLite format 3\x00")  # SQLite's copy of an empty database is a
    assert count_tables(copy, server.root / "scratch" / "fresh.db") == 0  # valid, empty one
    entries = {m["name"]: m for m in the_archive(server).manifest["members"]}
    assert entries["db/fresh.db"]["integrity"] == "ok"


def test_a_second_database_in_a_state_folder_of_the_ring_is_found_without_being_named(server):
    server.populate()
    make_database(server.var / "sudarshan-screener" / "screens.db", rows=9)
    go(server)
    assert "ring/sudarshan-screener/screens.db" in the_archive(server).files


# ---- when a source cannot be copied -----------------------------------------------------------


def test_a_file_that_is_not_a_database_makes_a_partial_archive_and_the_owner_is_told_which(server):
    server.populate()
    (server.app / "db" / "junk.db").write_bytes(b"this is not a database at all" * 50)
    pauses = Sleeps()

    result = go(server, sleep=pauses)

    assert (result.outcome, result.archive, result.exit_code) == (PARTIAL, PARTIAL_ARCHIVE, 1)
    assert [(f.why, f.names) for f in result.failures] == [(SOURCE, ("db/junk.db",))]
    assert pauses.asked == [backup.SNAPSHOT_PAUSE_S, backup.SNAPSHOT_PAUSE_S]  # three tries
    assert server.archives() == [PARTIAL_ARCHIVE]
    archive = the_archive(server, PARTIAL_ARCHIVE)
    assert "db/junk.db" not in archive.files and "db/openalgo.db" in archive.files
    assert archive.manifest["partial"] is True
    assert archive.manifest["not_saved"] == [{"name": "db/junk.db", "why": "source"}]
    (event,) = server.events()
    assert (event.kind, event.src, event.fact("why"), event.fact("names")) == (
        "backup_failed", "backup", "source", "junk.db",
    )  # fmt: skip
    assert event.id == "backup-failed-20261027-source"
    assert server.status()["outcome"] == "partial"


def test_a_file_that_vanishes_between_the_listing_and_the_copy_is_one_missing_file(
    server, monkeypatch
):
    server.populate()
    real = backup.discover

    def discover_then_lose_a_file(settings):
        found = real(settings)
        (server.etc / "feed.env").unlink()
        return found

    monkeypatch.setattr(backup, "discover", discover_then_lose_a_file)
    result = go(server)
    assert result.outcome == PARTIAL
    assert [(f.why, f.names) for f in result.failures] == [(SOURCE, ("etc/sudarshan/feed.env",))]
    assert "etc/sudarshan/feed.env" not in the_archive(server, PARTIAL_ARCHIVE).files


def test_a_source_that_breaks_the_reader_in_an_unexpected_way_costs_only_that_source(
    server, monkeypatch
):
    server.populate()
    real = backup.copy_stable

    def explode_on_one_file(source, dest):
        if source.name == "feed.env":
            raise RuntimeError("something nobody planned for")
        return real(source, dest)

    monkeypatch.setattr(backup, "copy_stable", explode_on_one_file)
    result = go(server)
    assert result.outcome == PARTIAL
    assert [f.names for f in result.failures] == [("etc/sudarshan/feed.env",)]
    assert len(the_archive(server, PARTIAL_ARCHIVE).files) > 10  # everything else is there


def test_a_database_that_fails_its_own_check_is_kept_but_never_counts_as_a_good_backup(server):
    server.populate()
    make_database(server.app / "db" / "broken.db", rows=2000)
    damage(server.app / "db" / "broken.db")

    result = go(server)

    assert (result.outcome, result.archive) == (PARTIAL, PARTIAL_ARCHIVE)
    assert [(f.why, f.names) for f in result.failures] == [(INTEGRITY, ("db/broken.db",))]
    archive = the_archive(server, PARTIAL_ARCHIVE)
    assert "db/broken.db" in archive.files  # a damaged copy is still kept for a look
    entries = {m["name"]: m for m in archive.manifest["members"]}
    assert entries["db/broken.db"]["integrity"] == "failed"
    assert entries["db/openalgo.db"]["integrity"] == "ok"
    (event,) = server.events()
    assert (event.fact("why"), event.fact("names")) == ("integrity", "broken.db")
    # And the archive is not what the next run mistakes for last night's good one.
    again = go(server, ist(2026, 10, 27, 22, 30))
    assert again.outcome == PARTIAL  # not skipped as fresh: a partial archive does not count


def test_the_job_waits_for_a_busy_database_and_tries_again(server, monkeypatch):
    server.populate()
    real = backup.sqlite3.connect
    refused = {"left": 2}

    def locked_twice_for_one_file(target, *args, **kwargs):
        if str(target).endswith("sandbox.db?mode=ro") and refused["left"]:
            refused["left"] -= 1
            raise backup.sqlite3.OperationalError("database is locked")
        return real(target, *args, **kwargs)

    monkeypatch.setattr(backup.sqlite3, "connect", locked_twice_for_one_file)
    pauses = Sleeps()
    result = go(server, sleep=pauses)
    assert result.outcome == OK and refused["left"] == 0
    assert pauses.asked == [backup.SNAPSHOT_PAUSE_S, backup.SNAPSHOT_PAUSE_S]
    assert "db/sandbox.db" in the_archive(server).files


# ---- when a file is there and is not taken ----------------------------------------------------
#
# The places the job takes whole (the sealed folders, /etc/sudarshan, the .env) and the databases
# must never lose a file quietly: a key with a stray space in its name that is skipped without a
# word is found on the day it is needed. What is not taken is named, the archive is partial and
# the owner is told. Links are the one thing passed over without a word, because they are never
# followed (a link could lead into another system's files).


def sparse(path: Path, size: int) -> Path:
    """A file of ``size`` bytes that takes no room on the disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as handle:
        handle.truncate(size)
    return path


#: Where a file can be too big: its name in the archive, where it lives, and the largest size taken.
PLACES = {
    "etc/sudarshan/huge.bin": (lambda s: s.etc / "huge.bin", backup.MAX_PLAIN_FILE),
    "db/sudarshan-vault/dump.bin": (
        lambda s: s.app / "db" / "sudarshan-vault" / "dump.bin",
        backup.MAX_PLAIN_FILE,
    ),
    "app/openalgo.env": (lambda s: s.app / ".env", backup.MAX_ENV_FILE),
}


@pytest.mark.parametrize("arc", PLACES)
def test_a_file_too_big_to_be_a_key_or_a_setting_is_told_not_left_out_quietly(server, arc):
    where, limit = PLACES[arc]
    server.populate()
    sparse(where(server), limit + 1)

    result = go(server)

    assert (result.outcome, result.archive) == (PARTIAL, PARTIAL_ARCHIVE)
    assert [(f.why, f.names) for f in result.failures] == [(SOURCE, (arc,))]
    archive = the_archive(server, PARTIAL_ARCHIVE)
    assert arc not in archive.files and len(archive.files) > 10  # all the rest is there
    assert archive.manifest["partial"] is True
    assert archive.manifest["not_saved"] == [{"name": arc, "why": "source"}]
    (event,) = server.events()
    assert (event.fact("why"), event.fact("names")) == ("source", arc.rsplit("/", 1)[-1])
    assert server.status()["failures"] == [{"why": "source", "names": [arc]}]


@pytest.mark.parametrize("arc", PLACES)
def test_a_file_exactly_as_big_as_the_limit_is_still_taken(server, arc):
    where, limit = PLACES[arc]
    server.populate()
    sparse(where(server), limit)

    result = go(server)

    assert (result.outcome, result.failures) == (OK, [])
    assert len(the_archive(server).data[arc]) == limit


def test_the_limits_the_readme_states_are_the_ones_in_the_code():
    readme = " ".join((Path(__file__).resolve().parents[2] / "README.md").read_text().split())
    assert (backup.MAX_PLAIN_FILE, backup.MAX_ENV_FILE) == (32 << 20, 1 << 20)
    assert (backup.MAX_PLAIN_TOTAL, backup.MAX_DEPTH) == (64 << 20, 4)
    for phrase in ("over 32 MB", "over 1 MB", "more than 64 MB", "more than four deep"):
        assert phrase in readme, phrase


def test_a_file_with_a_name_the_archive_does_not_take_is_told_not_left_out_quietly(server):
    server.populate()
    fits, too_long = "m" * 128, "n" * 129
    server.plain(server.etc / fits, "fits")
    server.plain(server.etc / too_long, "does not")
    server.plain(server.etc / "feed env (old)", "K=V\n")
    server.plain(server.etc / "cl\u00e9.env", "K=V\n")
    server.plain(server.app / "db" / "sudarshan-vault" / "key.salt~", "x", 0o600)

    result = go(server)

    assert result.outcome == PARTIAL
    assert [(f.why, f.names) for f in result.failures] == [
        (
            SOURCE,
            (
                "db/sudarshan-vault/key.salt~",
                "etc/sudarshan/cl\u00e9.env",
                "etc/sudarshan/feed env (old)",
                f"etc/sudarshan/{too_long}",
            ),
        )
    ]
    archive = the_archive(server, PARTIAL_ARCHIVE)
    assert archive.data[f"etc/sudarshan/{fits}"] == b"fits"  # 128 characters is still a name
    assert not {n for n in archive.files if too_long in n or " " in n or "~" in n or "\u00e9" in n}


def test_a_folder_nested_deeper_than_the_walk_goes_is_told(server):
    server.populate()
    deepest_taken = server.etc / "a" / "b" / "c" / "d"
    server.plain(deepest_taken / "kept.txt", "four folders down is still taken")
    server.plain(deepest_taken / "e" / "lost.txt", "one folder further is not")

    result = go(server)

    assert [(f.why, f.names) for f in result.failures] == [(SOURCE, ("etc/sudarshan/a/b/c/d/e",))]
    archive = the_archive(server, PARTIAL_ARCHIVE)
    assert "etc/sudarshan/a/b/c/d/kept.txt" in archive.files
    assert not {n for n in archive.files if n.endswith("lost.txt")}


@pytest.mark.parametrize(
    ("budget", "told"), [(24, []), (23, ["etc/sudarshan/sub/inner.txt"])], ids=["fits", "one over"]
)
def test_the_files_of_etc_are_taken_up_to_a_total_and_the_rest_are_told(
    server, monkeypatch, budget, told
):
    """feed.env 7 bytes, the two day files 11 and 0, sub/inner.txt 6: 24 in all."""
    server.populate()
    monkeypatch.setattr(backup, "MAX_PLAIN_TOTAL", budget)
    assert backup.discover(server.settings()).skipped == told


def test_a_database_with_a_name_the_archive_does_not_take_and_a_folder_in_a_sealed_one_are_told(
    server,
):
    server.populate()
    make_database(server.app / "db" / "my data.db")
    make_database(server.var / "sudarshan-alerts" / "second copy.db")
    make_database(server.app / "db" / "sudarshan-vault" / ".hidden.db")  # not copied as a file
    (server.app / "db" / "sudarshan-vault" / "keys").mkdir()
    told = [
        "db/my data.db",
        "db/sudarshan-vault/.hidden.db",
        "db/sudarshan-vault/keys",
        "ring/sudarshan-alerts/second copy.db",
    ]

    assert backup.discover(server.settings()).skipped == told
    result = go(server)

    assert [(f.why, f.names) for f in result.failures] == [(SOURCE, tuple(told))]
    assert len(the_archive(server, PARTIAL_ARCHIVE).files) > 10


def test_links_pipes_and_folders_that_are_not_files_are_not_followed_and_are_not_told(server):
    server.populate()
    vault = server.app / "db" / "sudarshan-vault"
    (server.app / "db" / "folder.db").mkdir()  # a folder named like a database is not one
    (server.var / "sudarshan-alerts" / "folder.db").mkdir()
    (server.etc / "link.env").symlink_to(server.app / ".env")
    (server.etc / "linked-folder").symlink_to(server.var / "other-system")
    os.mkfifo(server.etc / "pipe")
    (vault / "link.key").symlink_to(server.etc / "feed.env")
    os.mkfifo(vault / "pipe")
    (server.app / "db" / "linked.db").symlink_to(server.app / "db" / "sandbox.db")
    (server.var / "sudarshan-alerts" / "linked.db").symlink_to(server.app / "db" / "logs.db")

    assert backup.discover(server.settings()).skipped == []
    result = go(server)

    assert (result.outcome, result.failures) == (OK, [])
    archive = the_archive(server)
    assert not {n for n in archive.files if "link" in n or "pipe" in n}
    assert server.events() == []


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="needs /proc")
def test_a_night_leaves_no_file_open_whatever_went_wrong_in_it(server):
    server.populate()
    (server.app / "db" / "junk.db").write_bytes(b"this is not a database at all" * 50)
    sparse(server.etc / "huge.bin", backup.MAX_PLAIN_FILE + 1)
    go(server, force=True)  # the first night may open what the process then keeps for good
    before = open_files()

    assert go(server, NIGHT + timedelta(minutes=1), force=True).outcome == PARTIAL
    (server.app / "db" / "junk.db").unlink()
    (server.etc / "huge.bin").unlink()
    assert go(server, NIGHT + timedelta(minutes=2), force=True).outcome == OK
    assert go(server, ist(2026, 10, 28, 10, 0)).outcome == SKIPPED  # the market is open
    assert go(server, NIGHT + timedelta(minutes=3)).outcome == SKIPPED  # and a fresh archive
    damage_target = server.app / "db" / "broken.db"
    make_database(damage_target, rows=2000)
    damage(damage_target)
    assert go(server, NIGHT + timedelta(minutes=4), force=True).outcome == PARTIAL

    assert open_files() == before


def test_a_night_with_a_file_not_taken_is_a_partial_one_the_evening_retry_does_not_mistake(server):
    server.populate()
    sparse(server.etc / "huge.bin", backup.MAX_PLAIN_FILE + 1)
    assert go(server).outcome == PARTIAL
    assert go(server, ist(2026, 10, 27, 22, 30)).outcome == PARTIAL  # a partial night is not done
    assert server.archives() == ["sudarshan-backup-20261027-223000-partial.tar.gz"]
    sparse(server.etc / "huge.bin", 10)  # the owner fixes it
    assert go(server, ist(2026, 10, 28, 16, 30)).outcome == OK


# ---- when the archive cannot be written -------------------------------------------------------


@pytest.mark.parametrize(
    ("number", "why"), [(errno.ENOSPC, SPACE), (errno.EIO, WRITE), (errno.EDQUOT, WRITE)]
)
def test_a_disk_that_fills_during_the_write_leaves_no_archive_and_tells_why(
    server, monkeypatch, number, why
):
    server.populate()
    earlier = server.seed(NIGHT - timedelta(days=1), 500)
    real = tarfile.TarFile.addfile
    calls = {"n": 0}

    def fail_on_the_fifth_member(self, info, fileobj=None):
        calls["n"] += 1
        if calls["n"] == 5:
            raise OSError(number, os.strerror(number))
        return real(self, info, fileobj)

    monkeypatch.setattr(tarfile.TarFile, "addfile", fail_on_the_fifth_member)
    result = go(server)

    assert (result.outcome, result.archive, result.exit_code) == (FAILED, "", 1)
    assert [f.why for f in result.failures] == [why]
    assert server.archives() == [earlier.name]  # last night's is untouched, nothing new is there
    assert list((server.state / "work").iterdir()) == []
    assert [e.fact("why") for e in server.events()] == [why]
    assert server.status()["outcome"] == "failed"


def test_an_archive_that_does_not_read_back_whole_is_removed_not_published(server, monkeypatch):
    server.populate()
    monkeypatch.setattr(backup, "verify", lambda path, built: False)
    result = go(server)
    assert (result.outcome, [f.why for f in result.failures]) == (FAILED, [WRITE])
    assert server.archives() == []


# ---- the hours --------------------------------------------------------------------------------


def calendar(server: Server) -> TradingCalendar:
    return TradingCalendar(server.holidays, server.extra)


@pytest.mark.parametrize(
    ("when", "blocked"),
    [
        (ist(2026, 10, 21, 8, 29, 59), False),  # a Wednesday: the minute before
        (ist(2026, 10, 21, 8, 30, 0), True),
        (ist(2026, 10, 21, 12, 0), True),
        (ist(2026, 10, 21, 15, 59, 59), True),
        (ist(2026, 10, 21, 16, 0, 0), False),
        (ist(2026, 10, 21, 22, 30), False),
        (ist(2026, 10, 21, 3, 0), False),
        (ist(2026, 10, 24, 12, 0), False),  # Saturday
        (ist(2026, 10, 25, 12, 0), False),  # Sunday
        (ist(2026, 11, 9, 12, 0), False),  # a listed holiday
        (datetime(2026, 10, 21, 4, 30, tzinfo=UTC), True),  # 10:00 in India
        (datetime(2026, 10, 21, 10, 30, tzinfo=UTC), False),  # 16:00 in India
    ],
)
def test_the_job_keeps_out_of_the_trading_day(server, when, blocked):
    assert backup.market_blocks(calendar(server), when) is blocked


def test_a_listed_special_session_blocks_its_own_hours_and_no_others(server):
    server.extra.write_text("2026-11-08 14:05-15:40  # Muhurat\n")
    cal = calendar(server)
    assert backup.market_blocks(cal, ist(2026, 11, 8, 14, 4)) is False
    assert backup.market_blocks(cal, ist(2026, 11, 8, 14, 5)) is True
    assert backup.market_blocks(cal, ist(2026, 11, 8, 15, 39)) is True
    assert backup.market_blocks(cal, ist(2026, 11, 8, 15, 40)) is False
    assert backup.market_blocks(cal, ist(2026, 11, 8, 12, 0)) is False  # a Sunday before it
    assert backup.market_blocks(cal, ist(2026, 11, 8, 16, 30)) is False  # the timer's hour


def test_an_evening_session_on_a_weekday_is_added_to_the_usual_hours(server):
    server.extra.write_text("2026-10-21 18:00-19:40\n")
    cal = calendar(server)
    assert backup.market_blocks(cal, ist(2026, 10, 21, 12, 0)) is True
    assert backup.market_blocks(cal, ist(2026, 10, 21, 17, 0)) is False
    assert backup.market_blocks(cal, ist(2026, 10, 21, 18, 30)) is True
    assert backup.market_blocks(cal, ist(2026, 10, 21, 22, 30)) is False


def test_a_listed_day_without_hours_keeps_the_usual_ones(server):
    server.extra.write_text("2026-11-08  # a date alone\n")
    cal = calendar(server)
    assert backup.market_blocks(cal, ist(2026, 11, 8, 12, 0)) is True
    assert backup.market_blocks(cal, ist(2026, 11, 8, 16, 30)) is False


def test_a_run_in_market_hours_stops_before_it_touches_anything(server):
    server.populate()
    result = go(server, ist(2026, 10, 21, 10, 0))
    assert (result.outcome, result.reason, result.exit_code) == (SKIPPED, "market", 0)
    assert sorted(p.name for p in server.state.iterdir()) == ["own.db"]  # it made nothing
    assert server.events() == []


def test_a_run_that_is_still_going_when_the_market_opens_cleans_up_and_steps_aside(server):
    server.populate()
    earlier = server.seed(ist(2026, 10, 20, 16, 30), 500)
    asked = {"n": 0}

    def clock():
        asked["n"] += 1
        # 08:29:50 until the fifth look; the market opens (08:30) while the sources are copied.
        return ist(2026, 10, 21, 8, 29, 50) if asked["n"] <= 5 else ist(2026, 10, 21, 8, 30, 5)

    result = backup.run(server.settings(), clock=clock, sleep=no_sleep)

    assert (result.outcome, result.reason) == (SKIPPED, "market")
    assert asked["n"] > 5
    assert server.archives() == [earlier.name]
    assert list((server.state / "work").iterdir()) == []
    assert server.events() == [] and not (server.state / "status.json").exists()


# ---- once a night -----------------------------------------------------------------------------


def test_the_second_timer_of_the_day_finds_the_night_done(server):
    server.populate()
    first = go(server)
    status = (server.state / "status.json").read_text()
    second = go(server, ist(2026, 10, 27, 22, 30))
    assert (first.outcome, second.outcome, second.reason) == (OK, SKIPPED, "fresh")
    assert server.archives() == [ARCHIVE]
    assert (server.state / "status.json").read_text() == status  # a skipped run writes nothing


def test_an_archive_older_than_the_gap_is_not_fresh(server):
    server.populate()
    go(server)
    later = go(server, ist(2026, 10, 28, 3, 0))  # 10.5 hours on, before the market opens
    assert later.outcome == OK
    assert len(server.archives()) == 2


def test_force_makes_another_archive_and_the_day_keeps_only_the_newest(server):
    server.populate()
    go(server)
    forced = go(server, ist(2026, 10, 27, 22, 30), force=True)
    assert forced.outcome == OK
    assert server.archives() == ["sudarshan-backup-20261027-223000.tar.gz"]


def test_a_retry_after_a_partial_night_replaces_the_partial_archive(server):
    server.populate()
    junk = server.app / "db" / "junk.db"
    junk.write_bytes(b"not a database" * 100)
    first = go(server)
    junk.unlink()  # the cause is gone
    second = go(server, ist(2026, 10, 27, 22, 30))
    assert (first.outcome, second.outcome) == (PARTIAL, OK)
    assert server.archives() == ["sudarshan-backup-20261027-223000.tar.gz"]


def test_what_a_killed_run_left_behind_is_cleared_by_the_next_one(server):
    server.populate()
    server.archive_dir.mkdir(mode=0o700, parents=True)
    leftover = server.archive_dir / "sudarshan-backup-20261026-163102.tar.gz.partial"
    leftover.write_bytes(b"cut off")
    stale = server.state / "work" / "run-abc123"
    stale.mkdir(parents=True)
    (stale / "stage-3").write_bytes(b"a copy of a database")
    go(server)
    assert server.archives() == [ARCHIVE]
    assert list((server.state / "work").iterdir()) == []


def test_files_in_the_archive_folder_that_are_not_ours_are_left_alone(server):
    server.populate()
    server.archive_dir.mkdir(mode=0o700, parents=True)
    notes = server.archive_dir / "README.txt"
    notes.write_text("kept by hand")
    by_hand = server.archive_dir / "notes.tar.gz"
    by_hand.write_bytes(b"x" * 10)
    go(server)
    assert notes.read_text() == "kept by hand" and by_hand.exists()
    assert server.archives() == sorted([ARCHIVE, "README.txt", "notes.tar.gz"])
