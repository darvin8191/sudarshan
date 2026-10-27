"""M18.11: what the nightly job keeps, what it refuses to do, and how it tells.

Retention itself is tested on its own (``test_backup_retention.py``); here it is seen at work on a
real folder: the counts, the size cap, the space check before a write. Then the job's manners: the
deploy tool's lock, a second run of itself, the offsite hook, the files it leaves for the owner's
Telegram and for a look, and the end of the road, the message the owner reads.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import os
import re
import shlex
import stat
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from backup_fakes import NIGHT, Archive, Server, go, ist, make_database, no_sleep, open_files

from sudarshan.alerts import notices
from sudarshan.alerts.notices import NoticeDesk
from sudarshan.alerts.outbox import SENT
from sudarshan.alerts.store import Store
from sudarshan.ops import backup
from sudarshan.ops.backup import (
    CAP,
    FAILED,
    OFFSITE,
    OK,
    PARTIAL,
    SKIPPED,
    SPACE,
)

ARCHIVE = "sudarshan-backup-20261027-163102.tar.gz"
GIB = 1 << 30


@pytest.fixture
def server(tmp_path):
    pretend = Server(tmp_path / "server")
    yield pretend
    pretend.close()


def sparse(path: Path, size: int) -> Path:
    """A file that claims ``size`` bytes and takes no disk."""
    os.truncate(path, size)
    return path


def guess_for(server: Server) -> int:
    return int(backup.discover(server.settings()).total * backup.COMPRESSED_GUESS)


# ---- the counts at work -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("monthly", "kept_days"),
    [
        (1, {"20261027", "20261026", "20261025"}),
        (2, {"20261027", "20261026", "20261025", "20260930"}),
        (3, {"20261027", "20261026", "20261025", "20260930", "20260831"}),
    ],
)
def test_old_nights_go_by_the_counts_and_the_months_are_kept(server, monthly, kept_days):
    server.populate()
    for day in (21, 22, 23, 24, 25, 26):
        server.seed(ist(2026, 10, day), 100)
    server.seed(ist(2026, 9, 14), 100)
    server.seed(ist(2026, 9, 30), 100)  # September's last night
    server.seed(ist(2026, 8, 31), 100)  # August's
    settings = server.settings(keep_daily=3, keep_monthly=monthly)

    result = go(server, settings=settings)

    assert result.outcome == OK
    assert {n.split("-")[2] for n in server.archives()} == kept_days
    assert result.kept == len(kept_days)


def test_what_is_outside_the_counts_is_removed_even_when_the_night_then_fails(server, monkeypatch):
    server.populate()
    for n in range(40):
        server.seed(ist(2026, 9, 1) + timedelta(days=n), 100)
    monkeypatch.setattr(backup, "free_bytes", lambda path: 0)
    result = go(server, settings=server.settings(keep_monthly=0))
    assert [f.why for f in result.failures] == [SPACE]
    names = server.archives()
    assert len(names) == 30  # the 10 oldest of 40 are not wanted whatever happens next
    assert names[0] == "sudarshan-backup-20260911-163000.tar.gz"


# ---- the cap ----------------------------------------------------------------------------------


def test_the_cap_makes_room_by_removing_the_oldest_nights_first(server):
    server.populate()
    for day in range(22, 27):
        server.seed(ist(2026, 10, day), 100_000)
    settings = server.settings(max_bytes=350_000 + guess_for(server) + 1)

    result = go(server, settings=settings)

    assert (result.outcome, result.failures) == (OK, [])
    days = [n.split("-")[2] for n in server.archives()]
    assert days == ["20261024", "20261025", "20261026", "20261027"]  # 22nd and 23rd gave way


def test_a_cap_the_untouchable_archives_already_fill_stops_the_night_and_says_so(server):
    server.populate()
    for day in (24, 25, 26):
        sparse(server.seed(ist(2026, 10, day), 1), 4 * GIB)  # three nights of 4 GiB, cap 10 GiB
    old = server.seed(ist(2026, 6, 1), 100)  # outside the three nights and the one month kept

    result = go(server, settings=server.settings(keep_daily=3, keep_monthly=1))

    assert (result.outcome, result.archive, result.exit_code) == (FAILED, "", 1)
    assert [f.why for f in result.failures] == [CAP]
    assert result.failures[0].facts["cap_gb"] == "10.0"
    assert re.fullmatch(r"12\.\d", result.failures[0].facts["used_gb"])
    assert ARCHIVE not in server.archives()
    assert not old.exists()  # what the counts do not want went anyway
    assert len(server.archives()) == 3  # nothing wanted was removed to make a promise it can't keep
    (event,) = server.events()
    assert (event.fact("why"), event.fact("cap_gb")) == ("cap", "10.0")


def test_an_archive_bigger_than_the_guess_that_breaks_the_cap_is_kept_and_the_owner_told(server):
    server.populate()
    make_database(server.app / "db" / "big.db", rows=50, blob=20_000)  # random: hardly compresses
    for day in (24, 25, 26):
        server.seed(ist(2026, 10, day), 1000)
    guess = guess_for(server)
    settings = server.settings(max_bytes=3000 + guess + 100)  # it fits the guess, not the file

    result = go(server, settings=settings)

    assert (server.archive_dir / ARCHIVE).stat().st_size > guess  # the precondition
    assert (result.outcome, result.archive) == (OK, ARCHIVE)  # the archive is made and kept
    assert [f.why for f in result.failures] == [CAP] and result.exit_code == 1
    assert [e.fact("why") for e in server.events()] == ["cap"]
    assert ARCHIVE in server.archives()


# ---- the space --------------------------------------------------------------------------------


def test_a_disk_without_room_is_told_before_anything_is_written(server, monkeypatch):
    server.populate()
    earlier = server.seed(ist(2026, 10, 26), 100)
    settings = server.settings()
    found = backup.discover(settings)
    need = (
        int(found.total * backup.COMPRESSED_GUESS)
        + found.largest_database
        + settings.min_free_bytes
    )
    asked = []

    def free(path):
        asked.append(path)
        return need - 1

    monkeypatch.setattr(backup, "free_bytes", free)
    result = go(server, settings=settings)

    assert (result.outcome, result.archive) == (FAILED, "")
    assert [f.why for f in result.failures] == [SPACE]
    assert asked == [server.archive_dir]  # the disk the archive would be written to
    assert re.fullmatch(r"\d+\.\d\d", result.failures[0].facts["free_gb"])
    assert re.fullmatch(r"\d+\.\d\d", result.failures[0].facts["need_gb"])
    assert server.archives() == [earlier.name]
    assert not (server.state / "work").exists() or not list((server.state / "work").iterdir())
    (event,) = server.events()
    assert (event.kind, event.fact("why")) == ("backup_failed", "space")

    monkeypatch.setattr(backup, "free_bytes", lambda path: need)  # exactly enough is enough
    assert go(server, settings=settings, force=True).outcome == OK


def test_the_room_asked_for_covers_the_largest_database_staged_beside_the_archive(server):
    server.populate()
    make_database(server.app / "db" / "big.db", rows=50, blob=20_000)
    found = backup.discover(server.settings())
    biggest = max(s.size for s in found.sources if s.kind == "sqlite")
    assert found.largest_database == biggest > 1_000_000


# ---- the deploy tool's lock -------------------------------------------------------------------


def hold(path: Path, how: int) -> int:
    fd = os.open(path, os.O_RDONLY)
    fcntl.flock(fd, how)
    return fd


def test_the_job_takes_the_deploy_tools_lock_shared_for_as_long_as_it_works(server, monkeypatch):
    server.populate()
    tried = []
    real = backup._write

    def peek(*args, **kwargs):
        fd = os.open(server.lock, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # what the deploy tool does
            tried.append("took it")
        except BlockingIOError:
            tried.append("refused")
        finally:
            os.close(fd)
        return real(*args, **kwargs)

    monkeypatch.setattr(backup, "_write", peek)
    assert go(server).outcome == OK
    assert tried == ["refused"]  # while the job copies, the tool cannot start an install
    os.close(hold(server.lock, fcntl.LOCK_EX | fcntl.LOCK_NB))  # and afterwards it can


def test_an_install_in_progress_holds_the_job_back_until_it_ends(server):
    server.populate()
    installing = hold(server.lock, fcntl.LOCK_EX)
    waits = []

    def sleep(seconds):
        waits.append(seconds)
        if len(waits) == 3:  # the install finishes
            os.close(installing)

    clock = iter(range(0, 10_000, 10))
    result = backup.run(
        server.settings(deploy_wait_s=600.0),
        clock=lambda: NIGHT,
        sleep=sleep,
        monotonic=lambda: float(next(clock)),
    )

    assert result.outcome == OK and len(waits) == 3
    assert all(0 < w <= backup.LOCK_POLL_S for w in waits)


def test_a_job_that_waits_too_long_for_an_install_steps_aside_without_alarm(server):
    server.populate()
    installing = hold(server.lock, fcntl.LOCK_EX)
    try:
        ticks = iter(range(0, 100_000, 400))
        result = backup.run(
            server.settings(deploy_wait_s=1200.0),
            clock=lambda: NIGHT,
            sleep=no_sleep,
            monotonic=lambda: float(next(ticks)),
        )
    finally:
        os.close(installing)
    assert (result.outcome, result.reason, result.exit_code) == (SKIPPED, "deploy", 0)
    assert server.archives() == [] and server.events() == []  # the next timer does it


def test_with_no_deploy_tool_lock_to_wait_for_the_job_goes_on(server):
    server.populate()
    server.lock.unlink()
    assert go(server).outcome == OK
    with backup.with_deploy_tool(server.lock, 0) as state:
        assert state == "absent"


def test_the_shared_lock_does_not_keep_a_second_shared_holder_out(server):
    first = hold(server.lock, fcntl.LOCK_SH)
    try:
        with backup.with_deploy_tool(server.lock, 0) as state:
            assert state == "held"
    finally:
        os.close(first)


def test_a_second_run_while_one_is_going_stops_at_once(server):
    server.populate()
    other = os.open(server.state / "backup.lock", os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(other, fcntl.LOCK_EX)  # a run of the job is going
    try:
        result = go(server)
    finally:
        os.close(other)
    assert (result.outcome, result.reason) == (SKIPPED, "busy")
    assert server.archives() == []
    assert go(server).outcome == OK  # and when the other has finished, this one does the work


# ---- the offsite hook -------------------------------------------------------------------------


RECORD = """\
import fcntl, json, os, sys
from pathlib import Path
out = Path(sys.argv[1])
lock = os.open(sys.argv[2], os.O_RDONLY)
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    free = True
except BlockingIOError:
    free = False
code = int(sys.argv[3])
args = sys.argv[4:]
out.write_text(json.dumps({
    "args": args, "cwd": os.getcwd(), "env": sorted(os.environ), "lock_free": free,
    "archive_there": [Path(a).exists() for a in args if a.startswith("/") and a.endswith(".gz")],
    "stdin": sys.stdin.read(),
}))
print("sent", args[1])
sys.exit(code)
"""


def offsite(server: Server, tmp_path: Path, exit_code: int = 0):
    """The environment that sets an offsite command, and the file the command writes what it saw
    to. The command is a small Python program: the real thing will be rclone."""
    script = tmp_path / "offsite.py"
    script.write_text(RECORD)
    record = tmp_path / "offsite.json"
    command = (
        f"{shlex.quote(sys.executable)} {script} {record} {server.lock} {exit_code} "
        "{archive} {name} {dir}"
    )
    return server.env(SUDARSHAN_BACKUP_OFFSITE_CMD=command), record


def test_with_no_command_set_nothing_is_sent_and_nothing_is_written(server):
    server.populate()
    result = go(server)
    assert result.offsite == "off" and not (server.state / "offsite.log").exists()
    assert server.status()["offsite"] == "off"


def test_the_command_is_run_for_each_good_archive_with_the_file_the_name_and_the_folder(
    server, tmp_path, monkeypatch
):
    server.populate()
    monkeypatch.setenv("SUDARSHAN_TEST_SECRET", "must-not-reach-the-command")
    env, record = offsite(server, tmp_path)

    result = go(server, settings=server.settings(env))

    assert (result.outcome, result.offsite, result.failures) == (OK, "ok", [])
    seen = json.loads(record.read_text())
    assert seen["args"] == [str(server.archive_dir / ARCHIVE), ARCHIVE, str(server.archive_dir)]
    assert seen["archive_there"] == [True]
    assert Path(seen["cwd"]).resolve() == server.state.resolve()
    assert "SUDARSHAN_TEST_SECRET" not in seen["env"]
    assert set(seen["env"]) <= {"PATH", "HOME", "LANG", "LC_CTYPE"}
    assert seen["stdin"] == ""
    log = server.state / "offsite.log"
    assert log.read_text().strip() == f"sent {ARCHIVE}"
    assert stat.S_IMODE(log.stat().st_mode) == 0o600
    assert server.status()["offsite"] == "ok"


def test_the_command_runs_after_the_deploy_tools_lock_is_let_go(server, tmp_path):
    """Sending a file touches no database; the tool is not kept waiting for an upload."""
    server.populate()
    env, record = offsite(server, tmp_path)
    go(server, settings=server.settings(env))
    assert json.loads(record.read_text())["lock_free"] is True


def test_a_command_that_fails_keeps_the_archive_and_tells_the_owner(server, tmp_path):
    server.populate()
    env, record = offsite(server, tmp_path, exit_code=3)

    result = go(server, settings=server.settings(env))

    assert (result.outcome, result.archive, result.offsite) == (OK, ARCHIVE, "failed")
    assert [f.why for f in result.failures] == [OFFSITE] and result.exit_code == 1
    assert (server.archive_dir / ARCHIVE).exists()
    assert [e.fact("why") for e in server.events()] == ["offsite"]
    assert server.status()["offsite"] == "failed"
    assert (server.state / "offsite.log").read_text().strip() == f"sent {ARCHIVE}"
    assert record.exists()


def test_a_command_that_is_not_there_is_a_failed_upload_not_a_crashed_night(server, tmp_path):
    server.populate()
    env = server.env(SUDARSHAN_BACKUP_OFFSITE_CMD="/no/such/program {archive}")
    result = go(server, settings=server.settings(env))
    assert (result.outcome, result.offsite) == (OK, "failed")
    assert [f.why for f in result.failures] == [OFFSITE]


def test_a_command_that_hangs_is_stopped_at_its_time_limit(server, tmp_path):
    server.populate()
    hang = "import time; time.sleep(60)"
    env = server.env(
        SUDARSHAN_BACKUP_OFFSITE_CMD=f"{shlex.quote(sys.executable)} -c {shlex.quote(hang)} {{archive}}"
    )
    result = go(server, settings=server.settings(env, offsite_timeout_s=0.5))
    assert (result.outcome, result.offsite) == (OK, "failed")


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="needs /proc")
def test_the_offsite_command_leaves_no_file_open_however_it_ends(server, tmp_path):
    server.populate()
    hang = "import time; time.sleep(60)"
    hanging = server.env(
        SUDARSHAN_BACKUP_OFFSITE_CMD=f"{shlex.quote(sys.executable)} -c {shlex.quote(hang)} {{archive}}"
    )
    missing = server.env(SUDARSHAN_BACKUP_OFFSITE_CMD="/no/such/program {archive}")
    ends = {"works": 0, "fails": 3}
    sends = {name: offsite_in(server, tmp_path / name, code) for name, code in ends.items()}
    go(server, force=True, settings=server.settings(sends["works"]))  # the first use may keep one
    before = open_files()

    for number, (env, limit) in enumerate(
        [(sends["works"], 3600.0), (sends["fails"], 3600.0), (missing, 3600.0), (hanging, 0.5)]
    ):
        when = NIGHT + timedelta(minutes=number + 1)
        go(server, when, force=True, settings=server.settings(env, offsite_timeout_s=limit))

    assert open_files() == before


def offsite_in(server: Server, folder: Path, exit_code: int) -> dict[str, str]:
    folder.mkdir()
    return offsite(server, folder, exit_code)[0]


def test_a_partial_archive_is_sent_too(server, tmp_path):
    server.populate()
    (server.app / "db" / "junk.db").write_bytes(b"not a database" * 50)
    env, record = offsite(server, tmp_path)
    result = go(server, settings=server.settings(env))
    assert (result.outcome, result.offsite) == (PARTIAL, "ok")
    assert json.loads(record.read_text())["args"][1].endswith("-partial.tar.gz")
    assert sorted(f.why for f in result.failures) == ["source"]


def test_nothing_is_sent_when_no_archive_was_made(server, tmp_path, monkeypatch):
    server.populate()
    env, record = offsite(server, tmp_path)
    monkeypatch.setattr(backup, "free_bytes", lambda path: 0)
    assert go(server, settings=server.settings(env)).outcome == FAILED
    assert not record.exists()
    assert go(server, ist(2026, 10, 21, 10, 0), settings=server.settings(env)).outcome == SKIPPED
    assert not record.exists()


# ---- what the job records and tells -----------------------------------------------------------


def test_status_json_says_how_the_last_night_went(server):
    server.populate()
    go(server)
    status = server.status()
    assert status["at"] == "2026-10-27T16:31:02+05:30"
    assert (status["outcome"], status["archive"], status["offsite"]) == ("ok", ARCHIVE, "off")
    assert status["files"] == len(Archive(server.archive_dir / ARCHIVE).files) - 1 == 14
    assert status["kept"] == 1 and status["failures"] == []
    assert status["size"] == (server.archive_dir / ARCHIVE).stat().st_size
    assert status["used_bytes"] == status["size"]
    assert stat.S_IMODE((server.state / "status.json").stat().st_mode) == 0o600


def test_the_same_failure_on_the_same_day_is_one_notice_and_a_new_day_is_a_new_one(server):
    server.populate()
    (server.app / "db" / "junk.db").write_bytes(b"not a database" * 50)
    go(server)
    go(server, ist(2026, 10, 27, 22, 30))  # the evening retry fails the same way
    ids = [e.id for e in server.events()]
    assert ids == ["backup-failed-20261027-source"]
    go(server, ist(2026, 10, 28, 16, 30))
    assert [e.id for e in server.events()] == [
        "backup-failed-20261027-source",
        "backup-failed-20261028-source",
    ]


def test_two_different_causes_in_one_night_are_two_events(server, tmp_path):
    server.populate()
    (server.app / "db" / "junk.db").write_bytes(b"not a database" * 50)
    env, _ = offsite(server, tmp_path, exit_code=1)
    go(server, settings=server.settings(env))
    assert sorted(e.fact("why") for e in server.events()) == ["offsite", "source"]
    assert {e.run for e in server.events()} == {"backup-20261027-163102"}  # told as one run


def test_a_failure_is_recorded_where_the_alert_service_looks_for_the_backup_job(server):
    from sudarshan.ops.events import BACKUP_SPOOL, DEFAULT_SPOOLS

    assert BACKUP_SPOOL in DEFAULT_SPOOLS
    assert BACKUP_SPOOL == Path(backup.STATE_DIR) / "events.jsonl"
    assert server.settings().spool == server.state / "events.jsonl"


def what_the_owner_reads(server: Server, tmp_path: Path) -> list[str]:
    """The whole road from the job's record to the Telegram message: the alert service looks at
    the state folder's file, waits for the rest of the run, sends, and looks again."""
    sent: list[str] = []
    tmp_path.mkdir(parents=True, exist_ok=True)

    async def send(text: str) -> str:
        sent.append(text)
        return SENT

    class Clock:
        at = ist(2026, 10, 27, 16, 35)

        def __call__(self) -> datetime:
            return self.at

    clock = Clock()
    desk = NoticeDesk([server.state / "events.jsonl"], Store(tmp_path / "alerts.db"), send,
                      lambda: True, clock)  # fmt: skip

    async def looks():
        await desk.sweep()  # sees it; waits for the rest of the run to arrive
        assert sent == []
        clock.at += timedelta(seconds=notices.SETTLE_S + 1)
        await desk.sweep()
        clock.at += timedelta(minutes=10)
        await desk.sweep()  # and never again

    asyncio.run(looks())
    return sent


def test_a_failed_night_reaches_the_owners_telegram_in_plain_words_once(server, tmp_path):
    server.populate()
    (server.app / "db" / "junk.db").write_bytes(b"not a database at all" * 50)
    go(server)

    sent = what_the_owner_reads(server, tmp_path)

    assert len(sent) == 1
    assert "The nightly backup is incomplete: junk.db could not be saved." in sent[0]
    assert "Everything else was saved." in sent[0]
    for word in ("Traceback", "sqlite", "SQLite", "errno", "Error", "tar.gz", "status"):
        assert word not in sent[0], word


@pytest.mark.parametrize(("count", "named"), [(10, True), (11, False)])
def test_ten_files_are_named_in_the_message_and_eleven_are_not(tmp_path, count, named):
    """All or none: a list cut short would read as the whole of it."""
    server = Server(tmp_path / "server")
    try:
        server.populate()
        for number in range(count):
            big = server.plain(server.etc / f"big-{number:02d}.bin", "")
            sparse(big, backup.MAX_PLAIN_FILE + 1)
        go(server)
        (event,) = server.events()
        (message,) = what_the_owner_reads(server, tmp_path / "desk")
    finally:
        server.close()
    listed = ", ".join(f"big-{number:02d}.bin" for number in range(count))
    assert (event.fact("names") == listed) is named
    assert (listed in message) is named
    assert ("incomplete: some files could not be saved." in message) is not named


@pytest.mark.parametrize(("extra", "named"), [(0, True), (1, False)])
def test_a_list_that_fills_what_a_fact_holds_exactly_is_named_and_a_character_more_is_not(
    tmp_path, extra, named
):
    """Four names of 48, 48, 48 and 50 (+ extra) characters, with their commas: 200 (+ extra)."""
    server = Server(tmp_path / "server")
    try:
        server.populate()
        lengths = (48, 48, 48, 50 + extra)
        files = [f"{letter * (size - 4)}.bin" for letter, size in zip("abcd", lengths, strict=True)]
        for name in files:
            sparse(server.plain(server.etc / name, ""), backup.MAX_PLAIN_FILE + 1)
        go(server)
        (event,) = server.events()
        (message,) = what_the_owner_reads(server, tmp_path / "desk")
    finally:
        server.close()
    listed = ", ".join(files)
    assert len(listed) == 200 + extra
    assert event.fact("names") == (listed if named else "")  # never the list cut at 200
    assert (listed in message) is named
    assert ("incomplete: some files could not be saved." in message) is not named


def test_a_list_of_names_too_long_for_one_fact_is_not_sent_cut_in_half(server, tmp_path):
    server.populate()
    for letter in "abcdefgh":  # eight names of 29 characters: 246 with their commas, over 200
        sparse(server.plain(server.etc / f"{letter * 25}.bin", ""), backup.MAX_PLAIN_FILE + 1)
    go(server)
    (event,) = server.events()
    assert event.fact("names") == ""
    (message,) = what_the_owner_reads(server, tmp_path)
    assert "incomplete: some files could not be saved." in message
    assert "aaaa" not in message and "hhhh" not in message  # not a half name either


def test_a_clean_night_leaves_only_the_documented_files_in_the_state_folder(server):
    server.populate()
    go(server)
    assert sorted(p.name for p in server.state.iterdir()) == [
        "archive", "backup.lock", "own.db", "status.json", "work",
    ]  # fmt: skip
    assert sorted(p.name for p in server.archive_dir.iterdir()) == [ARCHIVE]
