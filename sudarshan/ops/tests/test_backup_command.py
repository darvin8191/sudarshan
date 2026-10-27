"""M18.11: the pieces of the nightly job one at a time, and the command that starts it.

Pieces: the SQLite copy and its check, the copy of a plain file, the read-back of an archive, the
settings (what the unit's environment may say, and what is refused). Command: the exit status
systemd reads, the notice a setting that cannot be used still gets, what a stop by the system
(SIGTERM, which is what a deploy's restart sends) leaves behind, and the real program started the
way the unit starts it.
"""

from __future__ import annotations

import dataclasses
import fcntl
import logging
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from backup_fakes import NIGHT, Archive, Server, damage, go, make_database, no_sleep, tar_bytes

from sudarshan.autologin.schedule import IST
from sudarshan.ops import backup
from sudarshan.ops.backup import (
    FAILED,
    OK,
    PARTIAL,
    SKIPPED,
    ConfigError,
    Failure,
    Result,
    Settings,
    SourceError,
)

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def server(tmp_path):
    pretend = Server(tmp_path / "server")
    yield pretend
    pretend.close()


# ---- the SQLite copy --------------------------------------------------------------------------


def test_a_snapshot_has_every_row_and_leaves_the_source_as_it_was(tmp_path):
    source = tmp_path / "a.db"
    con = make_database(source, rows=300, wal=True, keep_open=True)
    try:
        before = (source.read_bytes(), Path(f"{source}-wal").read_bytes())
        backup.snapshot(source, tmp_path / "copy.db")
        assert (source.read_bytes(), Path(f"{source}-wal").read_bytes()) == before
    finally:
        con.close()
    copy = sqlite3.connect(tmp_path / "copy.db")
    assert copy.execute("SELECT count(*) FROM t").fetchone() == (300,)
    copy.close()
    assert backup.quick_check(tmp_path / "copy.db") is True


def test_a_snapshot_never_creates_what_it_was_asked_to_read(tmp_path):
    sleeps = []
    with pytest.raises(SourceError):
        backup.snapshot(tmp_path / "gone.db", tmp_path / "copy.db", sleep=sleeps.append)
    assert not (tmp_path / "gone.db").exists()  # opened read-only: a typo makes no empty database
    assert not (tmp_path / "copy.db").exists()
    assert sleeps == [backup.SNAPSHOT_PAUSE_S] * (backup.SNAPSHOT_TRIES - 1)


def test_a_folder_with_spaces_and_url_characters_in_its_name_is_still_found(tmp_path):
    odd = tmp_path / "my db #1 ?x %41 &y"
    source = odd / "a.db"
    make_database(source, rows=4)
    backup.snapshot(source, tmp_path / "copy.db")
    copy = sqlite3.connect(tmp_path / "copy.db")
    assert copy.execute("SELECT count(*) FROM t").fetchone() == (4,)
    copy.close()


def test_the_check_of_a_copy_knows_a_sound_file_from_a_damaged_one_and_from_none(tmp_path):
    good = tmp_path / "good.db"
    make_database(good, rows=2000)
    broken = tmp_path / "broken.db"
    shutil.copy(good, broken)
    damage(broken)
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"not a database" * 100)
    assert backup.quick_check(good) is True
    assert backup.quick_check(broken) is False
    assert backup.quick_check(junk) is False
    assert backup.quick_check(tmp_path / "missing.db") is False


def test_a_damaged_database_that_makes_sqlite_run_out_of_memory_is_one_source_failing(tmp_path):
    """Python raises MemoryError, which is not an sqlite3.Error, for a page SQLite cannot read;
    it must not end the night."""
    source = tmp_path / "a.db"
    make_database(source, rows=2000)
    with open(source, "r+b") as handle:
        handle.seek(8192 + 2000)
        handle.write(b"\xff" * 300)
    try:
        backup.snapshot(source, tmp_path / "copy.db", sleep=no_sleep)
    except SourceError:
        return  # SQLite reported the damage while copying: a failed source, not a crashed night
    assert backup.quick_check(tmp_path / "copy.db") is False  # copied page for page: the check


def test_sqlite_reporting_no_memory_is_a_failed_copy_of_that_source(tmp_path, monkeypatch):
    class OutOfMemory:
        def backup(self, _target):
            raise MemoryError

        def close(self):
            return None

    real = sqlite3.connect
    monkeypatch.setattr(
        backup.sqlite3,
        "connect",
        lambda target, *a, **k: (
            OutOfMemory() if "mode=ro" in str(target) else real(target, *a, **k)
        ),
    )
    with pytest.raises(SourceError, match="MemoryError"):
        backup.snapshot(tmp_path / "a.db", tmp_path / "copy.db", sleep=no_sleep)


# ---- the copy of a plain file -----------------------------------------------------------------


def test_a_plain_file_is_copied_whole(tmp_path):
    source = tmp_path / "keys.env"
    source.write_bytes(os.urandom(3 << 20))
    backup.copy_stable(source, tmp_path / "copy")
    assert (tmp_path / "copy").read_bytes() == source.read_bytes()


def test_a_link_a_folder_and_a_pipe_are_not_plain_files(tmp_path):
    real = tmp_path / "real"
    real.write_text("x")
    (tmp_path / "link").symlink_to(real)
    (tmp_path / "folder").mkdir()
    os.mkfifo(tmp_path / "pipe")
    for name in ("link", "folder", "pipe", "missing"):
        with pytest.raises(SourceError):
            backup.copy_stable(tmp_path / name, tmp_path / f"copy-{name}")


def test_a_file_that_changes_while_it_is_copied_is_not_a_copy_worth_keeping(tmp_path, monkeypatch):
    source = tmp_path / "log.txt"
    source.write_text("a line\n")
    real = shutil.copyfileobj

    def copy_and_meanwhile_append(reader, writer, length=0):
        real(reader, writer, length)
        with open(source, "a") as other:
            other.write("another line\n")
        os.utime(source, ns=(1, 1))

    monkeypatch.setattr(backup.shutil, "copyfileobj", copy_and_meanwhile_append)
    with pytest.raises(SourceError, match="changed"):
        backup.copy_stable(source, tmp_path / "copy")


# ---- reading an archive back ------------------------------------------------------------------


def built_archive(tmp_path: Path):
    """A real archive of two databases and a file, with the record of what was packed."""
    found = backup.Found()
    make_database(tmp_path / "src" / "a.db", rows=50)
    make_database(tmp_path / "src" / "b.db", rows=60)
    (tmp_path / "src" / "c.env").write_text("K=V\n")
    for name, kind in (("a.db", "sqlite"), ("b.db", "sqlite"), ("c.env", "file")):
        path = tmp_path / "src" / name
        info = path.stat()
        found.sources.append(
            backup.Source(f"db/{name}", path, kind, info.st_size, info.st_mode, 0, 0)
        )
    settings = Settings.from_env(
        {
            "SUDARSHAN_DEPLOYED_TAG_FILE": str(tmp_path / "no-tag"),
            "SUDARSHAN_BACKUP_STATE_DIR": str(tmp_path / "state"),
        }
    )
    work = tmp_path / "work"
    work.mkdir()
    target = tmp_path / "out.tar.gz"
    built = backup.write_archive(settings, found, work, target, NIGHT, still_open=lambda: True)
    return settings, found, work, target, built


def test_an_archive_that_reads_back_exactly_as_packed_verifies(tmp_path):
    _, _, work, target, built = built_archive(tmp_path)
    assert backup.verify(target, built) is True
    assert list(work.iterdir()) == []  # every staged copy is gone
    assert [m.integrity for m in built.members] == ["ok", "ok", ""]


def test_an_archive_that_differs_in_any_way_does_not_verify(tmp_path):
    _, _, _, target, built = built_archive(tmp_path)
    members = dict(Archive(target).data)
    manifest = members.pop(backup.MANIFEST)
    variants = {
        "a changed member": {**members, "db/c.env": b"K=other\n", backup.MANIFEST: manifest},
        "a member missing": {k: v for k, v in members.items() if k != "db/a.db"}
        | {backup.MANIFEST: manifest},
        "an extra member": {**members, "db/extra.txt": b"x", backup.MANIFEST: manifest},
        "a changed manifest": {**members, backup.MANIFEST: manifest + b" "},
        "no manifest": dict(members),
    }
    for label, content in variants.items():
        broken = tmp_path / "broken.tar.gz"
        broken.write_bytes(tar_bytes(content))
        assert backup.verify(broken, built) is False, label


def test_an_archive_cut_short_or_with_a_flipped_byte_does_not_verify(tmp_path):
    _, _, _, target, built = built_archive(tmp_path)
    raw = target.read_bytes()
    cut = tmp_path / "cut.tar.gz"
    cut.write_bytes(raw[: len(raw) // 2])
    flipped = tmp_path / "flipped.tar.gz"
    middle = len(raw) // 2
    flipped.write_bytes(raw[:middle] + bytes([raw[middle] ^ 0xFF]) + raw[middle + 1 :])
    not_gzip = tmp_path / "text.tar.gz"
    not_gzip.write_bytes(b"this is not an archive")
    for path in (cut, flipped, not_gzip, tmp_path / "missing.tar.gz"):
        assert backup.verify(path, built) is False, path.name


def test_the_archive_is_not_started_when_the_market_has_already_opened(tmp_path):
    settings, found, work, target, _ = built_archive(tmp_path)
    again = tmp_path / "again.tar.gz"
    with pytest.raises(backup.Deferred):
        backup.write_archive(settings, found, work, again, NIGHT, still_open=lambda: False)
    assert list(work.iterdir()) == []


def test_an_archive_is_never_written_over_a_file_that_is_already_there(tmp_path):
    settings, found, work, target, _ = built_archive(tmp_path)
    before = target.read_bytes()
    with pytest.raises(backup.WriteFailed) as stopped:
        backup.write_archive(settings, found, work, target, NIGHT, still_open=lambda: True)
    assert stopped.value.space is False and target.read_bytes() == before


# ---- checking an archive on its own -----------------------------------------------------------


def test_a_sound_archive_checks_out_against_the_record_it_carries(tmp_path):
    _, _, _, target, _ = built_archive(tmp_path)
    problems, manifest = backup.check_archive(target)
    assert problems == []
    assert [m["name"] for m in manifest["members"]] == ["db/a.db", "db/b.db", "db/c.env"]


def test_an_archive_that_is_not_what_its_record_says_is_called_out_by_file(tmp_path):
    _, _, _, target, _ = built_archive(tmp_path)
    members = dict(Archive(target).data)
    manifest = members.pop(backup.MANIFEST)
    changed = tmp_path / "changed.tar.gz"
    changed.write_bytes(
        tar_bytes(
            {**members, "db/c.env": b"K=other\n", "extra.txt": b"x", backup.MANIFEST: manifest}
        )
    )
    del members["db/a.db"]
    gone = tmp_path / "gone.tar.gz"
    gone.write_bytes(tar_bytes({**members, backup.MANIFEST: manifest}))
    assert backup.check_archive(changed)[0] == [
        "db/c.env is not what was saved",
        "extra.txt is in the archive but not in its record",
    ]
    assert backup.check_archive(gone)[0] == ["db/a.db is missing"]


@pytest.mark.parametrize(
    "manifest",
    [
        None,
        b"not json",
        b"[]",
        b'{"format": 1}',
        b'{"format": 1, "members": [{"name": "a"}]}',
        b'{"format": 1, "members": "a"}',
        b'{"format": 99, "members": []}',
    ],
)
def test_an_archive_without_a_usable_record_is_not_vouched_for(tmp_path, manifest):
    content = {"db/a.db": b"x"} | ({backup.MANIFEST: manifest} if manifest is not None else {})
    path = tmp_path / "odd.tar.gz"
    path.write_bytes(tar_bytes(content))
    problems, record = backup.check_archive(path)
    assert problems and record == {}


def test_a_file_that_is_not_an_archive_is_not_vouched_for_either(tmp_path):
    path = tmp_path / "text.tar.gz"
    path.write_text("hello")
    assert backup.check_archive(path) == (["the file cannot be read as a backup archive"], {})
    assert backup.check_archive(tmp_path / "missing.tar.gz")[1] == {}


def test_the_verify_command_summarises_an_archive_and_says_how_it_ends(tmp_path, capsys):
    _, _, _, target, _ = built_archive(tmp_path)
    assert backup.main(["--verify", str(target)], {}) == 0
    assert capsys.readouterr().out.splitlines() == [
        "archive: out.tar.gz",
        "made: 2026-10-27T16:31:02+05:30",
        "release: not recorded",
        "files: 3",
        "complete: yes",
        "check: ok",
    ]
    members = dict(Archive(target).data)
    members["db/c.env"] = b"changed"
    broken = tmp_path / "broken.tar.gz"
    broken.write_bytes(tar_bytes(members))
    assert backup.main(["--verify", str(broken)], {}) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[-2:] == ["check: 1 problem(s)", "problem: db/c.env is not what was saved"]
    junk = tmp_path / "junk.tar.gz"
    junk.write_text("not an archive")
    assert backup.main(["--verify", str(junk)], {}) == 2
    assert "problem: the file cannot be read as a backup archive" in capsys.readouterr().out


def test_the_verify_command_names_what_a_partial_archive_lacks(server, capsys):
    server.populate()
    (server.app / "db" / "junk.db").write_bytes(b"not a database" * 50)
    result = go(server)
    path = server.archive_dir / result.archive
    assert backup.main(["--verify", str(path)], {"SUDARSHAN_BACKUP_MAX_GB": "lots"}) == 0
    out = capsys.readouterr().out
    assert "complete: no, not saved: db/junk.db (source)" in out
    assert "check: ok" in out and "release: v0.3.4" in out
    assert len(server.events()) == 1  # the partial night's own notice, nothing from the check


# ---- the settings -----------------------------------------------------------------------------


def test_with_nothing_set_the_defaults_are_the_documented_ones():
    settings = Settings.from_env({})
    assert settings.state_dir == Path("/var/lib/sudarshan-backup")
    assert settings.etc_dir == Path("/etc/sudarshan")
    assert settings.ring_var == Path("/var/lib")
    assert settings.deploy_lock == Path("/var/lib/sudarshan/deploy.lock")
    assert settings.deployed_tag_file == Path("/var/lib/sudarshan/deployed_tag")
    assert settings.holidays_file == Path("/etc/sudarshan/nse_holidays.txt")
    assert settings.extra_days_file == Path("/etc/sudarshan/nse_extra_trading_days.txt")
    assert settings.app_dir == REPO  # the program runs from the app it backs up
    assert (settings.keep_daily, settings.keep_monthly) == (30, 12)
    assert (settings.max_bytes, settings.min_free_bytes) == (10 << 30, 2 << 30)
    assert (settings.min_gap_s, settings.deploy_wait_s) == (8 * 3600.0, 20 * 60.0)
    assert (settings.offsite_cmd, settings.offsite_timeout_s) == ((), 3600.0)
    assert settings.archive_dir == settings.state_dir / "archive"
    assert settings.spool == settings.state_dir / "events.jsonl"
    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.keep_daily = 1  # type: ignore[misc]


def test_the_state_folder_is_the_one_systemd_made_for_the_unit():
    assert backup.state_dir_from({"STATE_DIRECTORY": "/var/lib/sudarshan-backup"}) == Path(
        "/var/lib/sudarshan-backup"
    )
    assert backup.state_dir_from({"STATE_DIRECTORY": "/a/one:/b/two"}) == Path("/a/one")
    both = {"STATE_DIRECTORY": "/a", "SUDARSHAN_BACKUP_STATE_DIR": "/b"}
    assert backup.state_dir_from(both) == Path("/b")
    assert backup.state_dir_from({"STATE_DIRECTORY": ""}) == Path(backup.STATE_DIR)


def test_every_setting_can_be_changed_and_a_blank_one_is_the_default():
    env = {
        "SUDARSHAN_BACKUP_KEEP_DAILY": "7",
        "SUDARSHAN_BACKUP_KEEP_MONTHLY": "0",
        "SUDARSHAN_BACKUP_MAX_GB": "2.5",
        "SUDARSHAN_BACKUP_MIN_FREE_GB": "0",
        "SUDARSHAN_BACKUP_MIN_GAP_HOURS": "1.5",
        "SUDARSHAN_BACKUP_DEPLOY_WAIT_MIN": "0",
        "SUDARSHAN_BACKUP_OFFSITE_TIMEOUT_MIN": "5",
        "SUDARSHAN_OPENALGO_DIR": "/srv/app",
        "SUDARSHAN_ETC_DIR": "/srv/etc",
        "SUDARSHAN_RING_VAR": "/srv/var",
    }
    settings = Settings.from_env(env)
    assert (settings.keep_daily, settings.keep_monthly) == (7, 0)
    assert (settings.max_bytes, settings.min_free_bytes) == (int(2.5 * (1 << 30)), 0)
    assert (settings.min_gap_s, settings.deploy_wait_s) == (5400.0, 0.0)
    assert settings.offsite_timeout_s == 300.0
    assert (settings.app_dir, settings.etc_dir, settings.ring_var) == (
        Path("/srv/app"), Path("/srv/etc"), Path("/srv/var"),
    )  # fmt: skip
    blank = Settings.from_env(dict.fromkeys(env, "  "))
    assert (blank.keep_daily, blank.keep_monthly, blank.max_bytes) == (30, 12, 10 << 30)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("SUDARSHAN_BACKUP_KEEP_DAILY", "many"),
        ("SUDARSHAN_BACKUP_KEEP_DAILY", "0"),  # at least one night is kept
        ("SUDARSHAN_BACKUP_KEEP_DAILY", "400"),
        ("SUDARSHAN_BACKUP_KEEP_MONTHLY", "-1"),
        ("SUDARSHAN_BACKUP_KEEP_MONTHLY", "121"),
        ("SUDARSHAN_BACKUP_MAX_GB", "0"),
        ("SUDARSHAN_BACKUP_MAX_GB", "nan"),
        ("SUDARSHAN_BACKUP_MAX_GB", "inf"),
        ("SUDARSHAN_BACKUP_MIN_FREE_GB", "-2"),
        ("SUDARSHAN_BACKUP_MIN_GAP_HOURS", "100"),
        ("SUDARSHAN_BACKUP_DEPLOY_WAIT_MIN", "601"),
        ("SUDARSHAN_BACKUP_OFFSITE_TIMEOUT_MIN", "0"),
        ("SUDARSHAN_BACKUP_OFFSITE_CMD", "rclone copy somewhere"),  # names no file to send
        ("SUDARSHAN_BACKUP_OFFSITE_CMD", "rclone copy '{archive} remote:"),  # an open quote
    ],
)
def test_a_setting_that_cannot_be_used_is_refused_by_name(name, value):
    with pytest.raises(ConfigError, match=name):
        Settings.from_env({name: value})


def test_the_offsite_command_is_words_with_the_places_for_what_to_send():
    assert Settings.from_env({}).offsite_cmd == ()
    assert Settings.from_env({"SUDARSHAN_BACKUP_OFFSITE_CMD": "   "}).offsite_cmd == ()
    one = Settings.from_env({"SUDARSHAN_BACKUP_OFFSITE_CMD": "rclone copyto {archive} r:b/{name}"})
    assert one.offsite_cmd == ("rclone", "copyto", "{archive}", "r:b/{name}")
    folder = Settings.from_env(
        {"SUDARSHAN_BACKUP_OFFSITE_CMD": "rclone copy --immutable {dir} 'my remote:bucket'"}
    )
    assert folder.offsite_cmd == ("rclone", "copy", "--immutable", "{dir}", "my remote:bucket")


# ---- the command ------------------------------------------------------------------------------


@pytest.fixture
def command(monkeypatch):
    """main() as the unit runs it, without its effects on this process: the nice level, the
    SIGTERM handler and the logging set-up are recorded, not applied."""
    done = {"nice": [], "signals": []}
    monkeypatch.setattr(backup.os, "nice", lambda n: done["nice"].append(n) or n)
    monkeypatch.setattr(
        backup.signal, "signal", lambda signum, handler: done["signals"].append((signum, handler))
    )
    monkeypatch.setattr(backup.logging, "basicConfig", lambda **kwargs: None)
    return done


def open_today(server: Server) -> None:
    """List the days around now as holidays, so that whatever the real clock says, it is open."""
    today = datetime.now(IST).date()
    days = (today + timedelta(days=n) for n in (-1, 0, 1))
    server.holidays.write_text("".join(f"{day.isoformat()}\n" for day in days))


def test_the_command_runs_the_night_and_reports_its_status_to_systemd(server, command):
    server.populate()
    open_today(server)
    assert backup.main([], server.env()) == 0
    (name,) = [n for n in server.archives() if n.endswith(".tar.gz")]
    assert name.startswith("sudarshan-backup-")
    assert command["nice"] == [19]
    assert [signum for signum, _ in command["signals"]] == [signal.SIGTERM]


@pytest.mark.parametrize(
    ("result", "code"),
    [
        (Result(OK), 0),
        (Result(SKIPPED, "fresh"), 0),
        (Result(PARTIAL, archive="x", failures=[Failure("source")]), 1),
        (Result(FAILED, failures=[Failure("space")]), 1),
        (Result(OK, archive="x", failures=[Failure("offsite")]), 1),
        (Result(FAILED), 1),
    ],
)
def test_the_exit_status_is_zero_for_a_good_night_and_one_for_any_trouble(
    server, command, monkeypatch, result, code
):
    monkeypatch.setattr(backup, "run", lambda settings, force=False: result)
    assert backup.main([], server.env()) == code
    assert result.exit_code == code


def test_force_reaches_the_run(server, command, monkeypatch):
    seen = []
    monkeypatch.setattr(
        backup, "run", lambda settings, force=False: seen.append(force) or Result(SKIPPED)
    )
    backup.main([], server.env())
    backup.main(["--force"], server.env())
    assert seen == [False, True]


def test_check_says_what_a_run_would_do_and_changes_nothing(server, command, capsys):
    server.populate()
    server.seed(NIGHT - timedelta(days=1), 1000)
    before = sorted(p.name for p in server.state.rglob("*"))
    assert backup.main(["--check"], server.env()) == 0
    out = capsys.readouterr().out.splitlines()
    assert [line.split(":")[0] for line in out] == [
        "hours",
        "sources",
        "archives",
        "cap",
        "offsite",
    ]
    assert out[0] in ("hours: blocked (market)", "hours: open for a backup")
    assert out[1].startswith("sources: 14 files")
    assert out[2] == "archives: 1 now, 1 kept, 0 to remove"
    assert out[3] == "cap: 10.0 GB, over: no"
    assert out[4] == "offsite: not set"
    assert sorted(p.name for p in server.state.rglob("*")) == before
    assert command["signals"] == []


def test_check_names_what_a_run_would_not_take(server, command, capsys):
    server.populate()
    server.plain(server.etc / "feed env (old)", "K=V\n")
    server.plain(server.app / "db" / "sudarshan-vault" / "key.salt~", "x", 0o600)
    assert backup.main(["--check"], server.env()) == 0
    out = capsys.readouterr().out.splitlines()
    assert [line.split(":")[0] for line in out] == [
        "hours",
        "sources",
        "not taken",
        "archives",
        "cap",
        "offsite",
    ]
    assert out[2] == "not taken: db/sudarshan-vault/key.salt~, etc/sudarshan/feed env (old)"


def test_check_on_a_bare_server_does_not_create_the_state_folder(server, command):
    assert backup.main(["--check"], server.env()) == 0
    assert not server.state.exists()


def test_a_setting_that_cannot_be_used_stops_the_job_and_the_owner_is_told_why(
    server, command, caplog
):
    with caplog.at_level(logging.ERROR, logger="sudarshan.backup"):
        code = backup.main([], server.env(SUDARSHAN_BACKUP_KEEP_DAILY="0"))
    assert code == 2
    (event,) = server.events()
    assert (event.kind, event.src, event.fact("why")) == ("backup_failed", "backup", "settings")
    assert "SUDARSHAN_BACKUP_KEEP_DAILY" in caplog.text
    assert server.archives() == []
    again = backup.main(["--check"], server.env(SUDARSHAN_BACKUP_KEEP_DAILY="0"))
    assert again == 2 and len(server.events()) == 1  # the same day: the same single notice


def test_something_nobody_planned_for_is_logged_in_full_and_told_in_one_line(
    server, command, monkeypatch, caplog
):
    def explode(settings, force=False):
        raise RuntimeError("something nobody planned for")

    monkeypatch.setattr(backup, "run", explode)
    with caplog.at_level(logging.ERROR, logger="sudarshan.backup"):
        assert backup.main([], server.env()) == 1
    assert "something nobody planned for" in caplog.text and "Traceback" in caplog.text
    (event,) = server.events()
    assert event.fact("why") == "other"


def test_a_stop_by_the_system_ends_the_run_quietly_and_without_a_notice(
    server, command, monkeypatch, caplog
):
    def stopped(settings, force=False):
        raise backup._Terminated(143)

    monkeypatch.setattr(backup, "run", stopped)
    with caplog.at_level(logging.WARNING, logger="sudarshan.backup"):
        assert backup.main([], server.env()) == 1
    assert "stopped by the system" in caplog.text
    assert server.events() == []  # a deploy restarting the unit is not a failure to report
    ((signum, handler),) = command["signals"]
    assert signum == signal.SIGTERM
    with pytest.raises(backup._Terminated) as raised:
        handler(signal.SIGTERM, None)
    assert isinstance(raised.value, SystemExit) and raised.value.code == 143


# ---- the real program -------------------------------------------------------------------------


def program(server: Server, *args: str, **env: str) -> subprocess.Popen:
    """``python -m sudarshan.ops.backup`` as the unit starts it: its own process, its own
    environment, nothing of the tests' but the interpreter and the import path."""
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONPATH": str(REPO),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        **server.env(**env),
    }
    with open(server.root / "program.log", "ab") as log:
        return subprocess.Popen(
            [sys.executable, "-m", "sudarshan.ops.backup", *args],
            env=environment,
            cwd=server.root,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
        )


def program_log(server: Server) -> str:
    path = server.root / "program.log"
    return path.read_text() if path.exists() else ""


def test_the_program_runs_a_whole_night_as_its_own_process(server):
    server.populate()
    open_today(server)
    process = program(server)
    assert process.wait(timeout=60) == 0
    (name,) = [n for n in server.archives() if n.endswith(".tar.gz")]
    assert len(Archive(server.archive_dir / name).files) == 15
    assert server.status()["outcome"] == "ok"
    assert "backup sudarshan-backup-" in program_log(server)


def test_the_program_says_what_it_would_do_when_asked_to_check(server):
    server.populate()
    process = program(server, "--check")
    assert process.wait(timeout=60) == 0
    assert "sources: 14 files" in program_log(server)
    assert server.archives() == []


def test_the_program_exits_with_status_two_for_a_setting_it_cannot_use(server):
    process = program(server, SUDARSHAN_BACKUP_MAX_GB="lots")
    assert process.wait(timeout=60) == 2
    assert "SUDARSHAN_BACKUP_MAX_GB" in program_log(server)
    assert [e.fact("why") for e in server.events()] == ["settings"]


def test_a_stop_while_waiting_for_an_install_leaves_nothing_behind(server):
    """A deploy that restarts the unit sends SIGTERM to a job still waiting for the lock."""
    server.populate()
    open_today(server)
    installing = os.open(server.lock, os.O_RDONLY)
    fcntl.flock(installing, fcntl.LOCK_EX)
    process = program(server, SUDARSHAN_BACKUP_DEPLOY_WAIT_MIN="20")
    try:
        deadline = time.monotonic() + 30
        while "waiting for it to finish" not in program_log(server):
            assert process.poll() is None and time.monotonic() < deadline, program_log(server)
            time.sleep(0.05)
        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=15) == 1
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(installing)
    assert "stopped by the system" in program_log(server)
    assert server.archives() == [] and server.events() == []
    # And the next run, once the install is over, is not kept out by the one that was stopped.
    again = program(server)
    assert again.wait(timeout=60) == 0
    assert len([n for n in server.archives() if n.endswith(".tar.gz")]) == 1
