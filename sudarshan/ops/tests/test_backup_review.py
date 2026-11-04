"""M18.11 review fixes in the nightly backup job: what a hostile or unlucky server does to the night.

What these pin (the review's names in brackets):

  A deploy lock the job cannot open is not a missing one: no backup is made in the middle of an
  install that cannot be seen, and the owner is told (M1).
  A night the job stepped aside from is told when the newest complete backup is more than a day and
  a quarter old, once a day (M3).
  A folder the job cannot list is named, the archive is partial, the owner is told; a folder that is
  not there is not an error (M4).
  The job keeps out of a special session with the margins the feed keeps (L1) and does not start an
  hour or less before the market hours begin (L12). ``--check`` says so, and names the files the job
  could not read (the first check on the server, L13).
"""

from __future__ import annotations

import errno
import fcntl
import os
import shutil
from datetime import date

import pytest
from backup_fakes import NIGHT, Archive, Server, go, ist

from sudarshan.alerts.notices import compose
from sudarshan.autologin.schedule import TradingCalendar
from sudarshan.ops import backup
from sudarshan.ops.backup import FAILED, LOCK, OK, PARTIAL, SKIPPED, SOURCE
from sudarshan.ops.events import Event

PARTIAL_ARCHIVE = "sudarshan-backup-20261027-163102-partial.tar.gz"
LONG_AGO = ist(2026, 10, 25, 16, 31)  # 48 hours before NIGHT


@pytest.fixture
def server(tmp_path):
    pretend = Server(tmp_path / "server")
    yield pretend
    pretend.close()


def deny_open(monkeypatch, *paths) -> None:
    """Opening any of these paths fails as it does for a user without the right. (The tests run
    as root, for whom a file mode denies nothing.)"""
    denied = {str(p) for p in paths}
    real = os.open

    def guarded(target, *args, **kwargs):
        if str(target) in denied:
            raise OSError(errno.EACCES, "Permission denied", str(target))
        return real(target, *args, **kwargs)

    monkeypatch.setattr(os, "open", guarded)


def unlistable(monkeypatch, *folders) -> None:
    """Listing any of these folders fails as it does for a user who may not read them."""
    denied = {str(f) for f in folders}
    real = os.scandir

    def guarded(path="."):
        if str(path) in denied:
            raise OSError(errno.EACCES, "Permission denied", str(path))
        return real(path)

    monkeypatch.setattr(os, "scandir", guarded)


def installing(server: Server) -> int:
    """What the deploy tool does for the length of an install: the lock, exclusively."""
    fd = os.open(server.lock, os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


# ---- the deploy lock (M1) ---------------------------------------------------------------------


def test_a_lock_that_cannot_be_opened_is_not_a_missing_one(server, monkeypatch):
    deny_open(monkeypatch, server.lock)
    with backup.with_deploy_tool(server.lock, 0) as state:
        assert state == "unreadable"
    server.lock.unlink()
    monkeypatch.undo()
    with backup.with_deploy_tool(server.lock, 0) as state:
        assert state == "absent"  # no such file is the one case that goes on without it


def test_no_backup_is_made_while_the_lock_cannot_be_read_and_the_owner_is_told(server, monkeypatch):
    """The lock was created with the unit's umask and could be unreadable for the ring's user: every
    error opening it was read as "no tool here", and the job ran in the middle of an install."""
    server.populate()
    deny_open(monkeypatch, server.lock)

    result = go(server)

    assert (result.outcome, [f.why for f in result.failures], result.exit_code) == (
        FAILED,
        [LOCK],
        1,
    )
    assert server.archives() == []
    (event,) = server.events()
    assert (event.kind, event.fact("why")) == ("backup_failed", "lock")
    assert server.status()["outcome"] == "failed"
    text = compose([event], NIGHT)
    assert "could not check whether a server update is running" in text and "lock" not in text


# ---- a night that was not made (M3) -----------------------------------------------------------


def overdue(server: Server) -> list[Event]:
    return [e for e in server.events() if e.fact("why") == "overdue"]


def test_a_night_the_job_stepped_aside_from_is_told_when_the_last_backup_is_old(server):
    server.populate()
    server.seed(LONG_AGO)
    lock = installing(server)  # an install that does not end
    try:
        first = go(server)
        again = go(server, ist(2026, 10, 27, 22, 30))
        next_day = go(server, ist(2026, 10, 28, 3, 0))
    finally:
        os.close(lock)

    assert [(r.outcome, r.reason) for r in (first, again, next_day)] == [(SKIPPED, "deploy")] * 3
    found = overdue(server)
    assert [e.id for e in found] == [  # once a day, never once a run
        "backup-failed-20261027-overdue",
        "backup-failed-20261028-overdue",
    ]
    assert [(e.kind, e.src, e.fact("hours"), e.fact("because")) for e in found] == [
        ("backup_failed", "backup", "48", "deploy"),
        ("backup_failed", "backup", "58", "deploy"),
    ]
    assert not (server.state / "status.json").exists()  # a run that did nothing changes nothing


@pytest.mark.parametrize(
    ("seeded", "told"),
    [
        (ist(2026, 10, 26, 11, 30), False),  # 29 hours: a late timer, not a missed night
        (ist(2026, 10, 26, 10, 31, 2), False),  # 30 hours exactly (NIGHT is 16:31:02)
        (ist(2026, 10, 26, 10, 31, 1), True),  # a second more
        (ist(2026, 10, 26, 9, 30), True),
    ],
)
def test_a_backup_is_overdue_after_thirty_hours_and_not_before(server, seeded, told):
    server.populate()
    server.seed(seeded)
    lock = installing(server)
    try:
        go(server)
    finally:
        os.close(lock)
    assert bool(overdue(server)) is told


def test_only_a_complete_archive_counts_as_a_backup_made(server):
    server.populate()
    server.seed(LONG_AGO)
    server.seed(ist(2026, 10, 27, 10, 0), partial=True)  # a partial one an hour ago
    lock = installing(server)
    try:
        go(server)
    finally:
        os.close(lock)
    (event,) = overdue(server)
    assert event.fact("hours") == "48"


def test_a_server_that_has_never_made_a_backup_is_not_late_against_anything(server):
    server.populate()
    lock = installing(server)
    try:
        go(server)
    finally:
        os.close(lock)
    assert server.events() == []


def test_a_recent_backup_ends_the_run_without_a_word(server):
    server.populate()
    server.seed(ist(2026, 10, 27, 10, 0))
    assert go(server).reason == "fresh"
    assert server.events() == []


@pytest.mark.parametrize(
    ("reason", "when"),
    [
        ("market", ist(2026, 10, 27, 10, 0)),  # a Tuesday morning
        ("soon", ist(2026, 10, 27, 7, 45)),
        ("busy", NIGHT),
        ("deploy", NIGHT),
    ],
)
def test_every_way_a_run_steps_aside_can_tell_the_night_is_overdue(server, reason, when):
    server.populate()
    server.seed(LONG_AGO)
    other = lock = None
    if reason == "busy":  # another run of the job is going
        other = os.open(server.state / "backup.lock", os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(other, fcntl.LOCK_EX)
    if reason == "deploy":
        lock = installing(server)
    try:
        result = go(server, when)
    finally:
        for fd in (other, lock):
            if fd is not None:
                os.close(fd)
    assert (result.outcome, result.reason) == (SKIPPED, reason)
    (event,) = overdue(server)
    assert event.fact("because") == reason
    assert "last complete backup is" in compose([event], when)


# ---- a folder the job cannot list (M4) --------------------------------------------------------


def names_of(result) -> list[tuple[str, ...]]:
    return [f.names for f in result.failures if f.why == SOURCE]


def test_a_sealed_folder_the_job_cannot_list_is_told_and_not_left_out_in_silence(
    server, monkeypatch
):
    """Under the owner vault's rule the nightly archive is the vault's only copy: a folder that
    could not be listed gave a "complete" archive without its files."""
    server.populate()
    unlistable(monkeypatch, server.app / "db" / "sudarshan-vault")

    result = go(server)

    assert (result.outcome, result.archive) == (PARTIAL, PARTIAL_ARCHIVE)
    assert names_of(result) == [("db/sudarshan-vault/",)]
    assert result.exit_code == 1
    archive = Archive(server.archive_dir / PARTIAL_ARCHIVE)
    assert not [n for n in archive.files if n.startswith("db/sudarshan-vault/")]
    assert "db/openalgo.db" in archive.files  # everything else was saved
    assert archive.manifest["not_saved"] == [{"name": "db/sudarshan-vault/", "why": "source"}]
    assert archive.manifest["partial"] is True
    (event,) = server.events()
    assert (event.fact("why"), event.fact("names")) == ("source", "db/sudarshan-vault/")
    assert "db/sudarshan-vault/ could not be saved" in compose([event], NIGHT)


@pytest.mark.parametrize(
    ("where", "arc"),
    [
        (lambda s: s.etc, "etc/sudarshan/"),
        (lambda s: s.etc / "sub", "etc/sudarshan/sub/"),
        (lambda s: s.var / "sudarshan-alerts", "ring/sudarshan-alerts/"),
        (lambda s: s.app / "db", "db/"),
        (lambda s: s.var, "ring/"),
    ],
)
def test_any_folder_the_job_cannot_list_makes_a_partial_night(server, monkeypatch, where, arc):
    server.populate()
    unlistable(monkeypatch, where(server))

    result = go(server)

    assert result.outcome == PARTIAL
    assert names_of(result) == [(arc,)]
    assert server.status()["failures"] == [{"why": "source", "names": [arc]}]


def test_a_folder_that_is_not_there_is_not_an_error(server):
    server.populate()
    shutil.rmtree(server.app / "db" / "sudarshan-books")
    shutil.rmtree(server.var / "sudarshan-feed")
    found = backup.discover(server.settings())
    assert found.skipped == []
    assert go(server).outcome == OK


def test_check_names_the_folder_it_could_not_list(server, monkeypatch):
    server.populate()
    unlistable(monkeypatch, server.etc)
    text = backup.check(server.settings(), NIGHT)
    assert "not taken: etc/sudarshan/\n" in text


# ---- the first check on the server (L13) ------------------------------------------------------


def test_check_names_the_files_the_job_would_not_be_able_to_read(server, monkeypatch):
    """A file only root can read is found by the first night, as a partial archive. The owner
    finds it before: the README's first check runs this as the job's own user."""
    server.populate()
    deny_open(monkeypatch, server.etc / "feed.env", server.app / ".env")

    text = backup.check(server.settings(), NIGHT)

    lines = text.splitlines()
    assert "cannot be read: app/openalgo.env, etc/sudarshan/feed.env" in lines
    assert lines.index("cannot be read: app/openalgo.env, etc/sudarshan/feed.env") == 2
    assert backup.check(server.settings(), NIGHT).count("cannot be read") == 1
    monkeypatch.undo()
    assert "cannot be read" not in backup.check(server.settings(), NIGHT)


# ---- the hours (L1, L12) ----------------------------------------------------------------------


def calendar(server: Server) -> TradingCalendar:
    return TradingCalendar(server.holidays, server.extra)


@pytest.mark.parametrize(
    ("when", "ahead"),
    [
        (ist(2026, 10, 21, 7, 29, 59), None),  # an hour and a second to go
        (ist(2026, 10, 21, 7, 30, 0), None),  # an hour: not less than an hour
        (ist(2026, 10, 21, 7, 31, 0), 59),
        (ist(2026, 10, 21, 8, 0, 0), 30),
        (ist(2026, 10, 21, 8, 29, 59), 1),
        (ist(2026, 10, 21, 8, 30, 0), None),  # it has begun: that is the block itself
        (ist(2026, 10, 21, 17, 0, 0), None),
        (ist(2026, 10, 24, 7, 45, 0), None),  # a Saturday: nothing begins
        (ist(2026, 11, 9, 7, 45, 0), None),  # a listed holiday
    ],
)
def test_the_job_does_not_start_in_the_last_hour_before_the_market_hours(server, when, ahead):
    assert backup.block_soon(calendar(server), when) == ahead


def test_a_special_session_has_its_last_hour_before_it_too(server):
    server.extra.write_text("2026-11-08 14:05-15:40  # Muhurat\n")
    cal = calendar(server)
    assert backup.block_soon(cal, ist(2026, 11, 8, 12, 35)) is None
    assert backup.block_soon(cal, ist(2026, 11, 8, 12, 36)) == 59
    assert backup.block_soon(cal, ist(2026, 11, 8, 13, 34)) == 1


def test_a_run_started_just_before_the_market_hours_steps_aside_before_it_touches_anything(server):
    """An install between 06:30 and 08:30 restarts the job; a full backup started at 08:00 was
    stopped by the 08:30 block, after an hour of the shared machine's two cores."""
    server.populate()
    result = go(server, ist(2026, 10, 21, 7, 45))
    assert (result.outcome, result.reason, result.exit_code) == (SKIPPED, "soon", 0)
    assert sorted(p.name for p in server.state.iterdir()) == ["own.db"]
    assert server.events() == []


def test_a_run_an_hour_before_the_market_hours_goes_ahead(server):
    server.populate()
    assert go(server, ist(2026, 10, 21, 7, 30)).outcome == OK


def test_a_weekend_morning_run_is_not_held_back(server):
    server.populate()
    assert go(server, ist(2026, 10, 24, 8, 0)).outcome == OK  # a Saturday


def test_check_says_when_the_job_would_not_start(server):
    text = backup.check(server.settings(), ist(2026, 10, 21, 7, 45))
    assert text.splitlines()[0] == (
        "hours: not started this close to the market (its hours begin in 45 minutes)"
    )
    assert backup.check(server.settings(), ist(2026, 10, 21, 7, 30)).startswith(
        "hours: open for a backup\n"
    )
    assert backup.check(server.settings(), ist(2026, 10, 21, 9, 0)).startswith(
        "hours: blocked (market)\n"
    )


def test_the_spans_of_an_ordinary_day_and_of_a_session_with_its_margins(server):
    server.extra.write_text("2026-11-08 14:05-15:40\n2026-10-21 18:00-19:40\n")
    cal = calendar(server)
    assert backup.block_spans(cal, date(2026, 10, 22)) == ((510, 960),)
    assert backup.block_spans(cal, date(2026, 11, 8)) == ((815, 960),)
    assert backup.block_spans(cal, date(2026, 10, 21)) == ((510, 960), (1050, 1200))
