"""M18.11 review fixes in the deploy tool: what a hostile or unlucky server can do to a root install.

The whole tool runs on the fake server of ``test_deploy_calendar.py``. What these pin:

  The frontend is replaced by the service user, not by root (review H1: the folder is the service
  user's to plant links in).
  The deploy lock is readable by the ring whatever the unit's umask, and the state folder can be
  walked (M1), so the nightly backup can see an install is running.
  A notice is never glued to a line a crash cut off, and a cut-off line is no record (M2).
  No download and no ``git fetch`` can hold an install for ever (M3).
  The log writer does not hold the lock (L3); a signal during an install rolls it back (L4); the
  backup copies are never readable by others, not even for a moment (L10); old backups are pruned
  by the tool's own names only (L11).
"""

from __future__ import annotations

import os
import pwd
import re
import shutil
import stat
from pathlib import Path

import pytest
from test_deploy_calendar import DEPLOY_TOOL, Box, _shim, whole_tool
from test_deploy_notices import EVENTS, install_box, told
from test_deploy_ring_databases import ring_box

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def sudo_lines(box: Box) -> list[str]:
    log = box.web / "sudo"
    return log.read_text().splitlines() if log.exists() else []


# ---- the frontend (H1) ------------------------------------------------------------------------


@whole_tool
def test_the_frontend_is_replaced_by_the_service_user(tmp_path):
    """Root extracting into a folder the service user can write is root writing through whatever
    that user plants there while it runs: the user who owns the folder does it."""
    box = install_box(tmp_path)
    me = pwd.getpwuid(os.geteuid()).pw_name
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert (box.app / "frontend" / "dist" / "index.html").read_text() == "new"
    puts = [line for line in sudo_lines(box) if "tar -xzf -" in line]
    assert len(puts) == 1 and puts[0].startswith(f"-u {me} -H -- sh -c ")
    assert puts[0].endswith(f" _ {box.app}/frontend")


@whole_tool
def test_the_previous_frontend_is_put_back_by_the_service_user(tmp_path):
    box = install_box(tmp_path)
    box.migrations = "1"
    done = box.run()
    assert done.returncode == 1 and "rolling back" in done.stdout, done.stdout + done.stderr
    assert (box.app / "frontend" / "dist" / "index.html").read_text() == "old"
    assert len([line for line in sudo_lines(box) if "tar -xzf -" in line]) == 2  # in, and back


# ---- the lock and the state folder (M1) -------------------------------------------------------


@whole_tool
@pytest.mark.parametrize("lock_before", [None, 0o600])
def test_the_lock_is_readable_and_the_folder_walkable_whatever_the_umask(tmp_path, lock_before):
    """The nightly backup runs as the ring's user and opens the lock to wait for an install; a lock
    it cannot open was read as "no tool here", and the backup ran in the middle of an install."""
    box = Box(tmp_path)  # v1.0.0 installed and the latest: nothing to install
    box.calendar("v1.0.0", b"")
    (box.state / "notice_announced").write_text("v1.0.0\n")  # and nothing to tell
    box.state.chmod(0o700)
    lock = box.state / "deploy.lock"
    if lock_before is not None:
        lock.write_text("")
        lock.chmod(lock_before)
    old = os.umask(0o077)
    try:
        done = box.run()
    finally:
        os.umask(old)
    assert done.returncode == 0, done.stdout + done.stderr
    assert stat.S_IMODE(lock.stat().st_mode) == 0o644
    assert box.state.stat().st_mode & stat.S_IXOTH


# ---- the notice file (M2) ---------------------------------------------------------------------

CUT = '{"v":"1","src":"deploy","kind":"installed","id":"deploy-catchup-v1.0.0","at":"17'


@whole_tool
def test_a_notice_is_not_glued_to_a_line_a_crash_cut_off(tmp_path):
    box = install_box(tmp_path)
    (box.state / EVENTS).write_text(CUT.replace("catchup-v1.0.0", "from-before"))  # no newline
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert [e.kind for e in told(box)] == ["installed"]  # on a line of its own, so it reads
    text = (box.state / EVENTS).read_text()
    assert text.startswith(CUT.replace("catchup-v1.0.0", "from-before") + "\n")
    assert text.count("\n") == 2


@whole_tool
def test_a_line_that_is_not_a_whole_record_does_not_count_as_told(tmp_path):
    """The check for an event already recorded looked for its id anywhere in the file, and a cut-off
    line that held the id kept the real notice from ever being recorded."""
    box = Box(tmp_path)  # the first run tells the install it finds, once, under this id
    box.calendar("v1.0.0", b"")
    (box.state / EVENTS).write_text(CUT)
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert [(e.id, e.kind) for e in told(box)] == [("deploy-catchup-v1.0.0", "installed")]


# ---- time limits (M3) -------------------------------------------------------------------------


def test_every_download_the_tool_makes_has_a_time_limit():
    code = [
        line
        for line in DEPLOY_TOOL.read_text().splitlines()
        if re.search(r"\bcurl ", line) and not line.lstrip().startswith("#")
    ]
    assert len(code) >= 8, code  # the release, its asset and checksum, the lists, the units, health
    assert [line for line in code if "--max-time" not in line] == []


@whole_tool
def test_the_fetch_of_the_code_cannot_hold_an_install_for_ever(tmp_path):
    box = install_box(tmp_path)
    _shim(box.bin, "timeout", 'echo "$*" >> "${FAKE_WEB}/timeout"\nshift\nexec "$@"')
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    fetches = [line for line in (box.web / "timeout").read_text().splitlines() if " fetch " in line]
    assert len(fetches) == 1 and re.fullmatch(r"\d+ git -C \S+ fetch .*", fetches[0]), fetches
    assert int(fetches[0].split()[0]) <= 900


# ---- the log writer and the lock (L3) ---------------------------------------------------------


@whole_tool
def test_the_log_writer_does_not_hold_the_deploy_lock(tmp_path):
    """``tee`` was started with the lock's descriptor open and could outlive the tool, which kept the
    next run out (one run in two hundred, and once a failed test)."""
    box = install_box(tmp_path)
    real = shutil.which("tee") or "tee"
    _shim(box.bin, "tee", f'ls -l /proc/$$/fd > "${{FAKE_WEB}}/tee_fds" 2>&1\nexec {real} "$@"')
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    held = (box.web / "tee_fds").read_text()
    assert " 0 -> " in held  # the listing is of the writer's own descriptors
    assert "deploy.lock" not in held


# ---- a signal during an install (L4) ----------------------------------------------------------

KILL_THE_TOOL = """
pid=$PPID
while [[ ${pid} -gt 1 ]]; do
    if tr '\\0' ' ' < "/proc/${pid}/cmdline" | grep -q 'bring the live OpenAlgo'; then
        kill -TERM "${pid}"; break
    fi
    pid="$(awk '{print $4}' "/proc/${pid}/stat")"
done
exit 0
"""


@whole_tool
def test_a_signal_in_the_middle_of_an_install_rolls_it_back_and_starts_the_service(tmp_path):
    """Only a failing command rolled back; a stop by the system between the stop of OpenAlgo and the
    health check left it, and the ring, stopped until the next timer run."""
    box = install_box(tmp_path)
    _shim(box.app / ".venv" / "bin", "python", KILL_THE_TOOL)  # the migration step is killed
    done = box.run()
    assert done.returncode == 1, done.stdout + done.stderr
    assert "stopped by a signal" in done.stdout and "rollback done" in done.stdout
    (log,) = box.state.glob("deploy-*.log")  # and the lines of the rollback are in the log too
    assert "rollback done" in log.read_text()
    assert box.deployed() == "v1.0.0"
    assert (box.app / "frontend" / "dist" / "index.html").read_text() == "old"
    started = (box.web / "systemctl").read_text().splitlines()
    assert started.count("start openalgo") == 1  # and it was started again
    assert [e.kind for e in told(box)] == ["rolled_back"]


# ---- the backup copies (L10) ------------------------------------------------------------------


@whole_tool
def test_no_backup_copy_is_readable_by_others_even_for_a_moment(tmp_path):
    box = ring_box(tmp_path)
    (box.app / "db" / "historify.duckdb").write_bytes(b"market history")
    real = shutil.which("chmod") or "chmod"
    _shim(  # what the folder looks like when the tool is about to close it
        box.bin,
        "chmod",
        'if [[ "$1" == -R ]]; then find "${@: -1}" -printf "%m %p\\n" >> "${FAKE_WEB}/modes"; fi\n'
        f'exec {real} "$@"',
    )
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    seen = [line.split(" ", 1) for line in (box.web / "modes").read_text().splitlines()]
    assert len(seen) >= 10, seen  # the folder, OpenAlgo's databases, the ring's, the rest
    assert [path for mode, path in seen if int(mode, 8) & 0o077] == []


# ---- the old backups (L11) --------------------------------------------------------------------


@whole_tool
def test_old_backups_are_pruned_by_the_tools_own_names_and_nothing_else(tmp_path):
    box = install_box(tmp_path)
    ours = [f"202610{day:02d}-120000-v0.9.{day}" for day in range(1, 13)]
    for name in ours:
        (box.backups / name).mkdir()
    others = [box.backups / "keep me", box.backups / "notes", box.backups / "20261001-120000.bak"]
    for folder in others:
        folder.mkdir()
        (folder / "x").write_text("x")
        os.utime(folder, (1_000_000, 1_000_000))  # the oldest of all, which `ls -t` takes first
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    left = sorted(p.name for p in box.backups.iterdir())
    tool_made = [n for n in left if re.fullmatch(r"\d{8}-\d{6}-v\d+\.\d+\.\d+", n)]
    (new,) = [n for n in tool_made if n.endswith("-v1.0.1")]
    assert tool_made == sorted([*ours[3:], new])  # the ten newest: the new one and nine before it
    assert all(folder.exists() for folder in others)
