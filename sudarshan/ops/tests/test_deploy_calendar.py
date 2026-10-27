"""M0.1: the NSE calendar lines a release ships, merged into /etc/sudarshan by sudarshan-deploy.

``cal_sync``, ``cal_restore`` and their helpers are cut out of the deploy tool exactly as they ship
(the pattern of ``test_deploy_env.py``) and run under the script's own shell options; the list fetch
runs the real curl against a local server. Every shipped line is read with the parsers of the readers
of the day files. Then the whole tool runs on a fake server (fake curl, sudo, git, systemctl, uv,
chown, sleep and date first on PATH), with the reviews' scenarios: a holiday for today shipped in
market hours (A), a line the owner took out (B), a list cut off in transit (T), a special session
moved during the session (X), a list GitHub did not find once (S404), and a release that would make
today a trading day.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import pwd
import re
import shutil
import socketserver
import stat
import subprocess
import sys
import tarfile
import threading
import time
from datetime import date, datetime
from pathlib import Path

import pytest

from sudarshan.alerts.window import AlertWindow, parse_day_windows
from sudarshan.autologin.schedule import IST, TradingCalendar, parse_window
from sudarshan.autologin.schedule import parse_holidays as autologin_days

try:  # the feed's own parser of the holiday file
    from sudarshan.feed.window import parse_holidays as feed_days
except ImportError:  # a feed that reads the day files through auto-login's calendar has none
    feed_days = autologin_days

REPO = Path(__file__).resolve().parents[3]
DEPLOY_TOOL = REPO / "sudarshan" / "ops" / "deploy" / "sudarshan-deploy"
SHIPPED_LIST = REPO / "sudarshan" / "ops" / "deploy" / "nse-calendar.conf"
EXTRA, HOLIDAYS = "nse_extra_trading_days.txt", "nse_holidays.txt"
TODAY = "2026-10-21"  # the day the function tests run on: a Wednesday before every date below
# The line the shipped list carries: Muhurat 2026 as NSE announced it (circulars 149/2026, cash,
# and 142/2026, F&O).
SHIPPED = (
    b"2026-11-08 14:05-15:40  # Muhurat, NSE circulars 149/2026 (cash) and 142/2026 (F&O to 15:40)"
)
# The lines the tests below ship themselves: an earlier version without a window, then a window.
MUHURAT = b"2026-11-08  # Muhurat trading (NSE circular 212/2025); timings to follow"
WINDOW = b"2026-11-08 18:00-19:15  # Muhurat trading (NSE circular 212/2025)"
FIRST = b"[nse_extra_trading_days.txt]\nline " + MUHURAT + b"\n"  # the first release's list
LATER = b"[nse_extra_trading_days.txt]\nline " + WINDOW + b"\nwas  " + MUHURAT + b"\n"  # the window
BOTH = b"[nse_holidays.txt]\nline 2026-11-24  # Guru Nanak Jayanti\n\n" + LATER
CAL_FUNCTIONS = (
    "cal_day_ok",
    "cal_dir_ok",
    "cal_held",
    "cal_note",
    "cal_flush",
    "cal_sync",
    "cal_restore",
)

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _function(name: str) -> str:
    text = DEPLOY_TOOL.read_text()
    start = text.index(f"\n{name}() {{") + 1
    return text[start : text.index("\n}\n", start) + 3]


def _shim(folder: Path, name: str, body: str) -> None:
    """A fake command in ``folder``, which a test puts first on PATH."""
    (folder / name).write_text(f"#!/usr/bin/env bash\n{body}\n")
    (folder / name).chmod(0o755)


def _snapshot(*roots: Path) -> dict[str, tuple]:
    """Every file and folder under ``roots``: bytes, inode, mtime and mode."""
    return {
        str(p): (p.read_bytes() if p.is_file() else b"", p.stat().st_ino, p.stat().st_mtime_ns,
                 p.stat().st_mode)
        for root in roots for p in [root, *sorted(root.rglob("*"))]
    }  # fmt: skip


class Server:
    """A fake /etc/sudarshan with the day files, the tool's state, its private work dir and backups."""

    def __init__(
        self,
        root: Path,
        files: dict[str, bytes] | None = None,
        listing: bytes = FIRST,
        owner: str | None = None,
        path: Path | None = None,
    ):
        self.root, self.etc, self.state = root, root / "etc", root / "state"
        self.work, self.backup, self.list = root / "work", root / "bk", root / "list.conf"
        for folder in (self.etc, self.state, self.work, self.backup):
            folder.mkdir(parents=True)
        self.work.chmod(0o700)
        self.list.write_bytes(listing)
        for name, data in (files or {}).items():
            (self.etc / name).write_bytes(data)
        self.owner = owner or f"{os.geteuid()}:{os.getegid()}"
        self.path = path

    def day(self, name: str = EXTRA) -> Path:
        return self.etc / name

    def run(self, *args: str, trace: Path | None = None) -> subprocess.CompletedProcess:
        # As the script calls them: set -Eeuo pipefail, from an if (errexit off inside, nounset on),
        # in the UTF-8 locale a systemd service gets on Ubuntu.
        script = (
            f'set -Eeuo pipefail\nlog() {{ echo "$*"; }}\nCAL_OWNER="{self.owner}"; CAL_MODE=640\n'
            f'CAL_RECORD="{self.state}/calendar_applied"; CAL_NOTES="{self.state}/calendar_notes"\n'
            + "".join(_function(name) for name in CAL_FUNCTIONS)
            + 'if "$@"; then exit 0; else exit $?; fi\n'
        )
        env = {**os.environ, "LC_ALL": "C.UTF-8"}
        if self.path:
            env["PATH"] = f"{self.path}{os.pathsep}{env['PATH']}"
        command = ["bash", "-c", script, "deploy", *args]
        if trace:
            command = ["strace", "-f", "-qq", "-o", str(trace), "-e",
                       "trace=open,openat,creat,chown,lchown,fchownat", *command]  # fmt: skip
        return subprocess.run(
            command, capture_output=True, text=True, errors="replace", cwd=self.root, env=env
        )

    def sync(self, mode: str = "install", today: str = TODAY, **kw) -> subprocess.CompletedProcess:
        extra = [str(self.backup)] if mode == "install" else []
        args = (str(self.list), str(self.etc), str(self.work), today, *extra)
        return self.run("cal_sync", mode, *args, **kw)

    def restore(self) -> subprocess.CompletedProcess:
        return self.run("cal_restore", str(self.work), str(self.etc))

    def record(self) -> set[str]:
        path = self.state / "calendar_applied"
        return set(path.read_text().splitlines()) if path.exists() else set()


def _applied(
    tmp_path: Path, files: dict[str, bytes], listing: bytes = FIRST, mode: str = "install"
) -> tuple[Server, str]:
    server = Server(tmp_path, files, listing)
    done = server.sync(mode)
    assert done.returncode == 0, done.stdout + done.stderr
    return server, done.stdout


def _other_owner() -> tuple[int, int]:
    """Ids a test may give a file: any as root, else this user and one of their other groups."""
    if os.geteuid() == 0:
        return 4242, 4343
    groups = [g for g in os.getgroups() if g != os.getegid()]
    if not groups:
        pytest.skip("needs root or a second group to tell the group apart")
    return os.geteuid(), groups[0]


@pytest.mark.parametrize(
    ("original", "after"),
    [
        (b"", MUHURAT + b"\n"),
        (b"# special sessions\n2026-03-07  # a Saturday session\n",
         b"# special sessions\n2026-03-07  # a Saturday session\n" + MUHURAT + b"\n"),
        (b"# 2026-11-08 is only a comment\n", b"# 2026-11-08 is only a comment\n" + MUHURAT + b"\n"),
        (b"2026-03-07", b"2026-03-07\n" + MUHURAT + b"\n"),  # no newline at the end
        (b"# x\r\n2026-03-07\r\n", b"# x\r\n2026-03-07\r\n" + MUHURAT + b"\r\n"),  # Windows endings
        (b"# stray \xff byte\n", b"# stray \xff byte\n" + MUHURAT + b"\n"),
    ],
)  # fmt: skip
def test_a_missing_date_is_added_at_the_end(tmp_path, original, after):
    server, out = _applied(tmp_path, {EXTRA: original})
    assert server.day().read_bytes() == after
    assert f'calendar: {EXTRA}: added "{MUHURAT.decode()}"' in out
    assert (
        f"calendar: {server.day()} updated; the previous one is in {server.backup / EXTRA}" in out
    )
    assert server.record() == {f"{EXTRA}|{MUHURAT.decode()}"}  # what the tool put there


@pytest.mark.parametrize(
    ("before", "after"),
    [
        (b"# x\n" + MUHURAT + b"\n# y\n", b"# x\n" + WINDOW + b"\n# y\n"),
        (b"# x\n" + MUHURAT + b" \t\n2026-03-07\n", b"# x\n" + WINDOW + b"\n2026-03-07\n"),
        (b"# x\r\n" + MUHURAT + b"\r\n# y\r\n", b"# x\r\n" + WINDOW + b"\r\n# y\r\n"),
        (b"# x\n" + MUHURAT, b"# x\n" + WINDOW + b"\n"),  # the last line, with no newline
    ],
)
def test_a_line_a_release_shipped_is_replaced_where_it_stands(tmp_path, before, after):
    server, out = _applied(tmp_path, {EXTRA: before}, LATER)
    assert server.day().read_bytes() == after
    assert f'calendar: {EXTRA}: replaced "{MUHURAT.decode()}" with "{WINDOW.decode()}"' in out
    assert server.record() == {f"{EXTRA}|{WINDOW.decode()}"}


@pytest.mark.parametrize(
    ("line", "reason"),
    [
        (b"2026-11-08", 'the owner wrote it as "2026-11-08"'),
        (b"2026-11-08 17:45-19:30 # mine", 'the owner wrote it as "2026-11-08 17:45-19:30 # mine"'),
        (b"  " + MUHURAT, "the owner wrote it as"),  # indented: not the text a release shipped
        (MUHURAT.replace(b"  #", b"\t#"), "the owner wrote it as"),
        (MUHURAT + b"\n" + MUHURAT, "the file lists it 2 times"),
        (MUHURAT + b"\n2026-11-08 # mine", "the file lists it 2 times"),
    ],
)
def test_a_line_the_owner_wrote_is_left_alone(tmp_path, line, reason):
    original = b"# x\n" + line + b"\n# y\n"
    server = Server(tmp_path, {EXTRA: original}, LATER)
    before = server.day().stat()
    done = server.sync()
    assert done.returncode == 0, done.stdout + done.stderr
    assert server.day().read_bytes() == original
    after = server.day().stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)  # not rewritten
    assert f"calendar: {EXTRA}: 2026-11-08 left alone: {reason}" in done.stdout
    assert "nothing to change" in done.stdout
    assert list(server.backup.iterdir()) == [] and list(server.work.iterdir()) == []


def test_the_owners_line_is_shown_safely(tmp_path):
    server, out = _applied(tmp_path, {EXTRA: b"2026-11-08 \x1b[31m\xe0\xa4\xae # mine\n"})
    assert 'left alone: the owner wrote it as "2026-11-08 ?[31m??? # mine"' in out


def test_a_second_run_changes_nothing_and_writes_nothing(tmp_path):
    server, _ = _applied(tmp_path, {EXTRA: b"# x\n" + MUHURAT + b"\n"}, BOTH)
    before = _snapshot(server.etc)
    for mode, says in (("install", f"calendar: nothing to change in {server.etc}\n"), ("run", "")):
        again = server.sync(mode)
        assert (again.returncode, again.stdout, again.stderr) == (0, says, "")
    assert _snapshot(server.etc) == before


def test_a_missing_file_is_created_like_the_other_files_there(tmp_path):
    """On the server root:sudarshan 640; here root and another group as root, else this user."""
    uid, gid = (0, 4343) if os.geteuid() == 0 else (os.geteuid(), os.getegid())
    server = Server(tmp_path, {HOLIDAYS: b"2026-10-20\n"}, owner=f"{uid}:{gid}")
    done = server.sync()
    assert done.returncode == 0, done.stdout + done.stderr
    made = server.day().stat()
    assert (made.st_uid, made.st_gid, stat.S_IMODE(made.st_mode)) == (uid, gid, 0o640)
    assert server.day().read_bytes() == MUHURAT + b"\n"
    assert f"calendar: {server.day()} created ({uid}:{gid} 640)" in done.stdout
    assert sorted(p.name for p in server.etc.iterdir()) == [EXTRA, HOLIDAYS]  # nothing else left
    assert server.day(HOLIDAYS).read_bytes() == b"2026-10-20\n"


def test_the_tool_creates_a_day_file_as_root_sudarshan_640():
    """The owner and mode of the other files in /etc/sudarshan (sudarshan/README.md)."""
    text = DEPLOY_TOOL.read_text()
    assert '\nCAL_DIR="/etc/sudarshan"\n' in text
    assert '\nCAL_OWNER="root:${SVC_USER}"; CAL_MODE=640\n' in text
    readme = (REPO / "sudarshan" / "README.md").read_text()
    assert "`/etc/sudarshan/<svc>.env` (root:sudarshan 640" in readme


def test_owner_group_and_mode_of_a_day_file_are_kept(tmp_path):
    uid, gid = _other_owner()
    original = b"# x\n" + MUHURAT + b"\n"
    server = Server(tmp_path, {EXTRA: original}, LATER)
    os.chown(server.day(), uid, gid)
    server.day().chmod(0o604)
    done = server.sync()
    assert done.returncode == 0, done.stdout + done.stderr
    for path in (server.day(), server.backup / EXTRA):
        got = path.stat()
        assert (got.st_uid, got.st_gid, stat.S_IMODE(got.st_mode)) == (uid, gid, 0o604)
    assert (server.backup / EXTRA).read_bytes() == original


def _moves(log: Path, target: Path) -> list[Path]:
    """The sources an mv shim renamed onto ``target``."""
    found = []
    for move in log.read_text().splitlines():
        *_, source, dest = move.split()
        if dest == str(target):
            found.append(Path(source))
    return found


def test_the_new_file_replaces_the_old_in_one_rename(tmp_path):
    """Written next to the old file and renamed over it: a reader has the whole old file or the new."""
    shims = tmp_path / "shims"
    shims.mkdir()
    _shim(shims, "mv", f'echo "$*" >> "{tmp_path}/mv.log"\nexec {shutil.which("mv")} "$@"')
    original = b"# x\n" + MUHURAT + b"\n"
    server = Server(tmp_path / "server", {EXTRA: original}, LATER, path=shims)
    with server.day().open("rb") as reader:  # a reader that opened the file before the merge
        done = server.sync("run")
        assert done.returncode == 0, done.stdout + done.stderr
        assert reader.read() == original  # still the whole old file
    (source,) = _moves(tmp_path / "mv.log", server.day())
    assert source.parent == server.etc and source.name.startswith(f".{EXTRA}.")
    assert sorted(p.name for p in server.etc.iterdir()) == [EXTRA]
    assert server.day().read_bytes() == b"# x\n" + WINDOW + b"\n"


@pytest.mark.skipif(shutil.which("strace") is None, reason="needs strace")
def test_the_new_file_is_written_through_one_descriptor(tmp_path):
    """Each temporary file: created by mktemp, then opened once to write it; its owner is set
    without following a symlink (chown -h)."""
    server = Server(tmp_path, {EXTRA: b"# x\n" + MUHURAT + b"\n2026-03-07\n"}, BOTH)
    done = server.sync(trace=tmp_path / "trace.log")
    assert done.returncode == 0, done.stdout + done.stderr
    calls = (tmp_path / "trace.log").read_text().splitlines()
    temp = re.compile(rf'"({re.escape(str(server.etc))}/\.nse_[a-z_]+\.txt\.[A-Za-z0-9]{{6}})"')
    paths = {m.group(1) for line in calls if (m := temp.search(line))}
    assert len(paths) == 2  # one per day file: the holiday file is created, the other rewritten
    for path in paths:
        opens = [c for c in calls if f'"{path}"' in c and "open" in c]
        assert len(opens) == 2, opens
        assert "O_EXCL" in opens[0] and "O_WRONLY" in opens[1] and "O_TRUNC" in opens[1]
        owners = [c for c in calls if f'"{path}"' in c and "chown" in c]
        assert owners and all("AT_SYMLINK_NOFOLLOW" in c or "lchown(" in c for c in owners), owners


@pytest.mark.parametrize(
    ("command", "body", "says"),
    [
        ("mktemp", "exit 1", "could not write"),
        ("tail", "exit 1", "could not write"),
        ("mv", 'case "${@: -1}" in */etc/*) exit 1 ;; esac\nexec MV "$@"', "could not write"),
        ("mv", "exit 1", "could not back up"),
    ],
)
def test_a_failed_write_leaves_the_old_file_in_place(tmp_path, command, body, says):
    shims = tmp_path / "shims"
    shims.mkdir()
    _shim(shims, command, body.replace("MV", shutil.which("mv") or "mv"))
    original = b"# x\n2026-03-07\n"
    server = Server(tmp_path / "server", {EXTRA: original}, FIRST, path=shims)
    before = server.day().stat()
    done = server.sync()
    assert done.returncode == 1
    assert f"calendar: {says} {server.day()}, so it is left as it was" in done.stdout
    assert "added" not in done.stdout  # a change is logged only once it is written
    assert server.day().read_bytes() == original and server.day().stat().st_ino == before.st_ino
    assert sorted(p.name for p in server.etc.iterdir()) == [EXTRA]  # no temporary file left
    assert list(server.work.iterdir()) == []  # nothing for a rollback to put back
    assert server.record() == set()


def test_a_write_that_keeps_failing_is_logged_once(tmp_path):
    """A group that does not exist on the server: the first run says so, the next ones do not."""
    server = Server(tmp_path, {}, FIRST, owner="root:sudarshan-zq-no-such-group")
    first = server.sync("run")
    assert first.returncode == 1 and first.stderr == ""
    assert first.stdout == f"calendar: could not write {server.day()}, so it is left as it was\n"
    again = server.sync("run")
    assert (again.returncode, again.stdout, again.stderr) == (1, "", "")
    assert sorted(p.name for p in server.etc.iterdir()) == []


@pytest.mark.parametrize("change", ["group may write", "others may write", "someone else's"])
def test_a_folder_others_can_write_is_left_alone(tmp_path, change):
    server = Server(tmp_path, {EXTRA: b"# x\n"})
    if change == "group may write":
        server.etc.chmod(0o775)
    elif change == "others may write":
        server.etc.chmod(0o757)
    elif os.geteuid() == 0:
        os.chown(server.etc, 4242, os.getegid())
    else:
        pytest.skip("needs root to give the folder to someone else")
    before = _snapshot(server.etc)
    for mode in ("install", "run"):
        done = server.sync(mode)
        assert done.returncode == 1
    assert _snapshot(server.etc) == before
    assert (
        f"calendar: {server.etc} is not a folder owned by root and closed to others"
        in (server.state / "calendar_notes").read_text()
    )


def test_a_rollback_puts_the_previous_files_back(tmp_path):
    uid, gid = _other_owner()
    original, holidays = b"# x\n" + MUHURAT + b"\n", b"2026-10-20\n"
    server = Server(tmp_path, {EXTRA: original, HOLIDAYS: holidays}, BOTH)
    os.chown(server.day(), uid, gid)
    server.day().chmod(0o640)
    os.utime(server.day(), ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    assert server.sync().returncode == 0
    assert server.day().read_bytes() != original and server.day(HOLIDAYS).read_bytes() != holidays
    assert (server.backup / EXTRA).read_bytes() == original
    assert (server.backup / HOLIDAYS).read_bytes() == holidays
    assert len(server.record()) == 2
    done = server.restore()
    assert done.returncode == 0, done.stdout + done.stderr
    assert server.day().read_bytes() == original and server.day(HOLIDAYS).read_bytes() == holidays
    got = server.day().stat()
    assert (got.st_uid, got.st_gid, stat.S_IMODE(got.st_mode)) == (uid, gid, 0o640)
    assert got.st_mtime_ns == 1_700_000_000_000_000_000  # exactly as it was
    assert f"calendar: {server.day()} put back as it was before the deploy" in done.stdout
    assert sorted(p.name for p in server.etc.iterdir()) == [EXTRA, HOLIDAYS]
    assert not (server.state / "calendar_applied").exists()  # the record goes back too


def test_a_rollback_removes_a_file_the_install_created(tmp_path):
    server = Server(tmp_path, {HOLIDAYS: b"2026-10-20\n"})
    assert server.sync().returncode == 0 and server.day().exists()
    assert list(server.backup.iterdir()) == []  # there was nothing to keep
    done = server.restore()
    assert done.returncode == 0 and not server.day().exists()
    assert f"calendar: {server.day()} removed: the deploy had created it" in done.stdout
    assert server.record() == set()


def test_the_rollback_renames_each_file_into_place(tmp_path):
    """A reader holding the installed file still reads all of it while the old one goes back."""
    shims = tmp_path / "shims"
    shims.mkdir()
    _shim(shims, "mv", f'echo "$*" >> "{tmp_path}/mv.log"\nexec {shutil.which("mv")} "$@"')
    original = b"# x\n" + MUHURAT + b"\n"
    server = Server(tmp_path / "server", {EXTRA: original}, LATER, path=shims)
    assert server.sync().returncode == 0
    installed = server.day().read_bytes()
    with server.day().open("rb") as reader:
        done = server.restore()
        assert done.returncode == 0, done.stdout + done.stderr
        assert reader.read() == installed
    assert server.day().read_bytes() == original
    restores = _moves(tmp_path / "mv.log", server.day())
    assert len(restores) == 2  # the install's rename, then the rollback's
    assert all(p.parent == server.etc and p.name.startswith(f".{EXTRA}.") for p in restores)


def test_a_rollback_without_a_calendar_step_changes_nothing(tmp_path):
    server = Server(tmp_path, {EXTRA: MUHURAT + b"\n"})
    before = _snapshot(server.etc)
    done = server.restore()
    assert (done.returncode, done.stdout) == (0, "")
    assert _snapshot(server.etc) == before


def test_a_run_keeps_nothing_for_a_rollback(tmp_path):
    server = Server(tmp_path, {EXTRA: b"# x\n"})
    assert server.sync("run").returncode == 0
    assert list(server.work.iterdir()) == [] and list(server.backup.iterdir()) == []


def test_check_changes_nothing(tmp_path):
    server = Server(tmp_path, {EXTRA: b"# x\n" + MUHURAT + b"\n"}, BOTH)
    before = _snapshot(server.etc, server.backup, server.state)
    done = server.sync("check")
    assert done.returncode == 0, done.stdout + done.stderr
    assert _snapshot(server.etc, server.backup, server.state) == before
    assert list(server.work.iterdir()) == []  # its private copies are gone again
    assert f'{EXTRA}: would replace "{MUHURAT.decode()}" with "{WINDOW.decode()}"' in done.stdout
    assert f'{HOLIDAYS}: would add "2026-11-24  # Guru Nanak Jayanti"' in done.stdout
    assert f"calendar: would create {server.day(HOLIDAYS)}" in done.stdout


@pytest.mark.parametrize(
    ("listing", "says"),
    [
        (b"line 2026-11-08\n", "list line 1 comes before the first [FILE] line"),
        (b"[nse_holidays.txt]\nadd 2026-11-08\n", "list line 2 is not a [FILE], line, was or drop line"),
        (b"[openalgo.env]\nline 2026-11-08\n", "list line 1 names a file other than"),
        (b"[../nse_holidays.txt]\n", "list line 1 names a file other than"),
        (b"[nse_holidays.txt]\n[nse_holidays.txt]\n", "list line 2 names nse_holidays.txt a second"),
        (b"[nse_holidays.txt]\nline 2026-02-29\n", "list line 2 has a date that does not exist"),
        (b"[nse_holidays.txt]\nline 2026-11-31\n", "list line 2 has a date that does not exist"),
        (b"[nse_holidays.txt]\nline 2026-04-31\n", "list line 2 has a date that does not exist"),
        (b"[nse_holidays.txt]\nline 2026-06-31\n", "list line 2 has a date that does not exist"),
        (b"[nse_holidays.txt]\nline 2026-09-31\n", "list line 2 has a date that does not exist"),
        (b"[nse_holidays.txt]\nline 08-11-2026\n", "list line 2 is not YYYY-MM-DD and"),
        (b"[nse_holidays.txt]\nline 2026-11-08 Diwali\n", "list line 2 is not YYYY-MM-DD and"),
        (b"[nse_holidays.txt]\nline 2026-11-08 18:00-19:15\n", "list line 2 is not YYYY-MM-DD and"),
        (b"[nse_holidays.txt]\nline 2026-11-10 # \xe0\xa4\xae\n",
         "list line 2 is not YYYY-MM-DD and an optional # comment, in plain ASCII"),
        (b"[nse_extra_trading_days.txt]\nline 2026-11-08 19:15-18:00\n", "list line 2 has a window"),
        (b"[nse_extra_trading_days.txt]\nline 2026-11-08 18:00-18:00\n", "list line 2 has a window"),
        (b"[nse_extra_trading_days.txt]\nline 2026-11-08 18:00-24:00\n", "list line 2 has a window"),
        (b"[nse_extra_trading_days.txt]\nline 2026-11-08 6:00-7:00\n", "list line 2 is not YYYY-MM-DD,"),
        (b"[nse_extra_trading_days.txt]\nline 2026-11-08 # \xe0\xa4\xae\n",
         "list line 2 is not YYYY-MM-DD, an optional HH:MM-HH:MM window and an optional # comment, "
         "in plain ASCII"),
        (b"[nse_extra_trading_days.txt]\nline 2026-11-08\nline 2026-11-08 # x\n",
         "list line 3 is a second line for 2026-11-08"),
        (b"[nse_extra_trading_days.txt]\nwas 2026-11-08\n", "list line 2 is a was with no line above"),
        (b"[nse_extra_trading_days.txt]\nline 2026-11-09\nwas 2026-11-08\n",
         "list line 3 is a was for another date than its line"),
        (b"[nse_extra_trading_days.txt]\nline 2026-11-09\nwas 2026-11-09 # a\nwas 2026-11-09 # a\n",
         "list line 4 repeats a line of nse_extra_trading_days.txt"),
        (b"[nse_extra_trading_days.txt]\nline 2026-11-09 # a\nwas 2026-11-09\nline 2026-11-09\n",
         "list line 4 repeats a line of nse_extra_trading_days.txt"),
        (b"[nse_extra_trading_days.txt]\nline 2026-11-09\ndrop 2026-11-09\n",
         "list line 3 repeats a line of nse_extra_trading_days.txt"),
        (b"[nse_extra_trading_days.txt]\ndrop 2026-11-09\nwas 2026-11-09 # x\n",
         "list line 3 is a was with no line above it"),
    ],
)  # fmt: skip
def test_a_bad_list_is_refused_and_changes_nothing(tmp_path, listing, says):
    server = Server(tmp_path, {EXTRA: b"# x\n", HOLIDAYS: b"2026-10-20\n"}, listing)
    before = _snapshot(server.etc)
    done = server.sync()
    assert done.returncode == 1
    assert f"calendar: {says}" in done.stdout
    assert _snapshot(server.etc) == before and list(server.backup.iterdir()) == []
    again = server.sync("run")  # the same list on the next run: said once already
    assert (again.returncode, again.stdout) == (1, "")


def test_a_symlinked_day_file_is_not_followed(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_bytes(b"# not a day file\n")
    server = Server(tmp_path / "server")
    server.day().symlink_to(elsewhere)
    for mode in ("check", "install"):
        (server.state / "calendar_notes").unlink(missing_ok=True)
        done = server.sync(mode)
        assert done.returncode == 0
        warning = f"WARNING: calendar: {server.day()} is not a plain file, so it is left alone"
        assert warning in done.stdout
    assert server.day().is_symlink() and elsewhere.read_bytes() == b"# not a day file\n"
    assert list(server.backup.iterdir()) == []


SUNDAY = "2026-11-08"  # Muhurat: the day the run-mode tests below take as today


@pytest.mark.parametrize(
    ("mode", "files", "listing", "after", "says"),
    [
        # A run that installs nothing adds no holiday for today: the gate would then let an install
        # through in the middle of the session (scenario A).
        ("run", {HOLIDAYS: b""}, b"[nse_holidays.txt]\nline 2026-11-08 # x\n", {HOLIDAYS: b""},
         "2026-11-08 not added: a holiday is added only before its day"),
        ("install", {HOLIDAYS: b""}, b"[nse_holidays.txt]\nline 2026-11-08 # x\n", {HOLIDAYS: b""},
         "2026-11-08 not added: a holiday is added only before its day"),
        ("run", {HOLIDAYS: b""}, b"[nse_holidays.txt]\nline 2026-11-09 # x\n",
         {HOLIDAYS: b"2026-11-09 # x\n"}, 'added "2026-11-09 # x"'),
        # ...but it adds a special session for today, which only makes the gate stricter.
        ("run", {EXTRA: b""}, FIRST, {EXTRA: MUHURAT + b"\n"}, f'added "{MUHURAT.decode()}"'),
        ("run", {EXTRA: b""}, b"[nse_extra_trading_days.txt]\nline 2026-11-07 # x\n", {EXTRA: b""},
         "2026-11-07 kept as it is: a run that installs nothing changes no line dated today or earlier"),
        # A special session for today keeps its line: no new window and no move during the day
        # (scenario X); an install, outside market hours, may change it.
        ("run", {EXTRA: MUHURAT + b"\n"}, LATER, {EXTRA: MUHURAT + b"\n"}, "2026-11-08 kept as it is"),
        ("install", {EXTRA: MUHURAT + b"\n"}, LATER, {EXTRA: WINDOW + b"\n"}, "replaced"),
        ("run", {EXTRA: MUHURAT + b"\n"}, b"[nse_extra_trading_days.txt]\ndrop " + MUHURAT + b"\n",
         {EXTRA: MUHURAT + b"\n"}, "2026-11-08 kept as it is"),
        ("install", {EXTRA: MUHURAT + b"\n"}, b"[nse_extra_trading_days.txt]\ndrop " + MUHURAT + b"\n",
         {EXTRA: b""}, f'removed "{MUHURAT.decode()}"'),
    ],
)  # fmt: skip
def test_what_may_change_on_the_day(tmp_path, mode, files, listing, after, says):
    server = Server(tmp_path, files, listing)
    if b"\ndrop " in listing:  # a release drops only a line this tool added
        (server.state / "calendar_applied").write_text(f"{EXTRA}|{MUHURAT.decode()}\n")
    done = server.sync(mode, today=SUNDAY)
    assert done.returncode == 0, done.stdout + done.stderr
    assert {name: server.day(name).read_bytes() for name in after} == after
    assert says in done.stdout


def test_a_line_the_owner_took_out_stays_out(tmp_path):
    """Scenario B: the owner takes out a line this tool added; neither it nor a later version of it
    comes back, and that is said once."""
    server, _ = _applied(tmp_path, {EXTRA: b"# special sessions\n"})
    taken_out = b"# special sessions\n# 2026-11-08 Muhurat: not trading it\n"
    server.day().write_bytes(taken_out)
    note = "calendar: nse_extra_trading_days.txt: 2026-11-08 not added again: this tool added it before"
    first = server.sync("run")
    assert first.returncode == 0 and note in first.stdout
    assert server.sync("run").stdout == ""  # said once
    server.list.write_bytes(LATER)  # a later release ships the window for that day: it stays out
    later = server.sync("install")
    assert (later.returncode, later.stdout) == (0, f"calendar: nothing to change in {server.etc}\n")
    assert server.day().read_bytes() == taken_out


@pytest.mark.parametrize(
    ("recorded", "after", "says"),
    [
        (True, b"# x\n", f'removed "{MUHURAT.decode()}"'),
        (False, b"# x\n" + MUHURAT + b"\n", "left alone: this tool did not add"),
    ],
)
def test_a_drop_takes_out_only_a_line_this_tool_added(tmp_path, recorded, after, says):
    server = Server(
        tmp_path, {EXTRA: b"# x\n" + MUHURAT + b"\n"},
        b"[nse_extra_trading_days.txt]\ndrop " + MUHURAT + b"\n",
    )  # fmt: skip
    if recorded:
        (server.state / "calendar_applied").write_text(f"{EXTRA}|{MUHURAT.decode()}\n")
    done = server.sync()
    assert done.returncode == 0, done.stdout + done.stderr
    assert server.day().read_bytes() == after
    assert says in done.stdout
    assert server.record() == set()


def test_a_refused_drop_leaves_the_owners_day_to_him(tmp_path):
    """The owner wrote the very text a drop names: it stays, and the release's new line for that
    day is not set beside it (in the extra-days file the later line's window would win)."""
    mine = b"2026-11-08 17:45-19:30  # Muhurat"
    listing = b"[nse_extra_trading_days.txt]\ndrop " + mine + b"\nline " + WINDOW + b"\n"
    server = Server(tmp_path, {EXTRA: mine + b"\n"}, listing)
    done = server.sync()
    assert done.returncode == 0, done.stdout + done.stderr
    assert server.day().read_bytes() == mine + b"\n"
    assert "this tool did not add" in done.stdout and "the owner wrote it as" in done.stdout


def test_a_drop_and_a_line_move_a_day(tmp_path):
    """A line shipped for the wrong day is taken out and the right one added, in one install."""
    wrong, right = b"2026-11-07  # Muhurat", b"2026-11-08  # Muhurat"
    listing = b"[nse_extra_trading_days.txt]\ndrop " + wrong + b"\nline " + right + b"\n"
    server = Server(tmp_path, {EXTRA: b"# x\n" + wrong + b"\n"}, listing)
    (server.state / "calendar_applied").write_text(f"{EXTRA}|{wrong.decode()}\n")
    done = server.sync()
    assert done.returncode == 0, done.stdout + done.stderr
    assert server.day().read_bytes() == b"# x\n" + right + b"\n"
    assert server.record() == {f"{EXTRA}|{right.decode()}"}


def test_a_note_is_logged_once_while_it_holds(tmp_path):
    server = Server(tmp_path, {EXTRA: b"2026-11-08 # mine\n"})
    assert "the owner wrote it as" in server.sync("run").stdout
    assert server.sync("run").stdout == ""
    server.day().write_bytes(b"2026-11-08 # mine, changed\n")  # a different note: said again
    assert '"2026-11-08 # mine, changed"' in server.sync("run").stdout
    server.day().write_bytes(b"")  # nothing to say any more: the note is forgotten
    assert "added" in server.sync("run").stdout
    assert (server.state / "calendar_notes").read_text() == ""


def test_an_empty_line_in_the_notes_is_read_past(tmp_path):
    server = Server(tmp_path, {EXTRA: b"2026-11-08 # mine\n"})
    (server.state / "calendar_notes").write_text("calendar: an old note\n\n")  # a hand edit
    done = server.sync("run")
    assert (done.returncode, done.stderr) == (0, ""), done.stdout + done.stderr
    assert "the owner wrote it as" in done.stdout
    assert (server.state / "calendar_notes").read_text().count("\n") == 1  # the note that holds


def test_today_is_the_date_in_india(tmp_path):
    """cal_today asks for the date in IST whatever the server's own zone: at 01:30 IST on Sunday
    8 Nov it is still Saturday in UTC."""
    if not Path("/usr/share/zoneinfo/Asia/Kolkata").exists():
        pytest.skip("needs the time zone database")
    instant = int(datetime(2026, 11, 8, 1, 30, tzinfo=IST).timestamp())
    _shim(tmp_path, "date", f'exec {shutil.which("date")} -d @{instant} "$@"')
    script = "set -Eeuo pipefail\n" + _function("cal_today") + "cal_today\n"
    env = {**os.environ, "PATH": f"{tmp_path}{os.pathsep}{os.environ['PATH']}", "TZ": "UTC"}
    done = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)
    assert done.stdout == "2026-11-08\n", done.stderr


def test_a_temporary_file_a_killed_run_left_is_cleared(tmp_path):
    names = [".nse_holidays.txt.AbC123", ".nse_extra_trading_days.txt.XyZ789",
             ".nse_holidays.txt.long-name", ".other.ABCDEF"]  # fmt: skip
    server = Server(tmp_path, {EXTRA: MUHURAT + b"\n"})
    for name in names:
        (server.etc / name).write_bytes(b"half\n")
    old = time.time() - 20 * 60
    for name in (names[0], names[2], names[3]):
        os.utime(server.etc / name, (old, old))
    assert server.sync("check").returncode == 0
    assert all((server.etc / name).exists() for name in names)  # check changes nothing
    done = server.sync("run")
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout == f"calendar: removed {server.etc / names[0]}, left by an interrupted run\n"
    assert sorted(p.name for p in server.etc.iterdir()) == sorted([EXTRA, *names[1:]])


def _shipped(listing: str) -> list[tuple[str | None, str]]:
    """(file, text) of every line, was and drop in a list, read as the list's header describes."""
    found, name = [], None
    for raw in listing.splitlines():
        if section := re.fullmatch(r"\s*\[(.+)\]\s*", raw):
            name = section.group(1)
        elif entry := re.fullmatch(r"\s*(?:line|was|drop)\s+(.*\S)\s*", raw):
            found.append((name, entry.group(1)))
    return found


def _deploy_gate_lists(folder: Path, day: str, text: str) -> bool:
    """The deploy tool's own listed(), cut out as it ships, on a file holding ``text``."""
    (folder / "day.txt").write_text(text + "\n")
    script = _function("listed") + f'listed "{day}" "{folder / "day.txt"}"\n'
    return subprocess.run(["bash", "-c", script]).returncode == 0


def test_every_shipped_line_is_read_by_every_reader(tmp_path, caplog):
    """Auto-login's calendar (the alerts use it too), the alerts' own window, the feed's parser and
    the market gate; the OpenAlgo auto-login page is the next test."""
    caplog.set_level(logging.WARNING)
    shipped = _shipped(SHIPPED_LIST.read_text())
    assert (EXTRA, SHIPPED.decode()) in shipped
    assert parse_day_windows(SHIPPED.decode()) == {date(2026, 11, 8): (14 * 60 + 5, 15 * 60 + 40)}
    for name, text in shipped:
        assert name in (HOLIDAYS, EXTRA)
        day = date.fromisoformat(text.split()[0])
        assert autologin_days(text) == ({day}, 0)
        assert feed_days(text) == ({day}, 0)  # in use the feed reads only the holiday file
        assert _deploy_gate_lists(tmp_path, day.isoformat(), text)
        if name == EXTRA:
            words = text.split("#", 1)[0].split()
            assert parse_day_windows(text) == ({day: parse_window(words[1])} if words[1:] else {})
    assert caplog.records == []  # no reader found a line it could not read


def test_the_shipped_list_is_whole_and_reads(tmp_path):
    """CI: the list in the repo is not empty, ends with a newline (the tool takes a list only when
    it arrived whole, so one without would never be taken) and reads with the tool's own parser."""
    data = SHIPPED_LIST.read_bytes()
    assert data.strip(), "the calendar list is empty"
    assert data.endswith(b"\n"), "the calendar list does not end with a newline"
    done = Server(tmp_path, {}, data).sync("check")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "list line" not in done.stdout


# The page's own calendar, as the blueprint builds it from its settings (the auto-login tests'
# conftest does the same before importing it): python-dotenv off, so no .env is read.
PAGE = """
import logging
from datetime import date, datetime
import dotenv
dotenv.load_dotenv = lambda *args, **kwargs: False
dotenv.main.load_dotenv = dotenv.load_dotenv
import blueprints.sudarshan_autologin as page
from sudarshan.autologin.schedule import IST
notes = []
class Keep(logging.Handler):
    def emit(self, record):
        notes.append(record.getMessage())
reader = logging.getLogger("sudarshan.autologin")
reader.setLevel(logging.WARNING)
reader.addHandler(Keep())
calendar = page._calendar()
print("PAGE trading", calendar.is_trading_day(date(2026, 11, 8)))
print("PAGE holiday", not calendar.is_trading_day(date(2026, 10, 20)))
print("PAGE next", calendar.next_slot(datetime(2026, 11, 7, 20, 0, tzinfo=IST)).isoformat())
print("PAGE notes", notes)
"""


def test_the_auto_login_page_reads_the_shipped_list(tmp_path):
    """The OpenAlgo auto-login page builds its own calendar from the day files (its next run, and
    when Test now is refused): the blueprint itself, with fake settings, over the shipped list."""
    server, _ = _applied(
        tmp_path, {HOLIDAYS: b"2026-10-20  # Dussehra\n"}, SHIPPED_LIST.read_bytes()
    )
    fake = tmp_path / "fake"
    fake.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
    env.update(
        API_KEY_PEPPER="f" * 64,  # fake, test only
        APP_KEY="test-only-app-key-" + "0" * 32,
        DATABASE_URL=f"sqlite:///{fake}/openalgo.db",
        LOG_DIR=str(fake / "log"),
        REDIRECT_URL="http://127.0.0.1:5000/upstox/callback",
        SUDARSHAN_VAULT_DB=str(fake / "vault.db"),
        SUDARSHAN_AUTOLOGIN_ENV_FILE=str(fake / "autologin.env"),
        SUDARSHAN_AUTOLOGIN_TEST_LOG=str(fake / "log" / "sudarshan_autologin.log"),
        SUDARSHAN_NSE_HOLIDAYS_FILE=str(server.day(HOLIDAYS)),
        SUDARSHAN_NSE_EXTRA_DAYS_FILE=str(server.day()),
    )
    done = subprocess.run(
        [sys.executable, "-c", PAGE], cwd=REPO, env=env, capture_output=True, text=True, timeout=120
    )
    assert done.returncode == 0, done.stdout + done.stderr
    said = dict(line.split(" ", 2)[1:] for line in done.stdout.splitlines() if line[:5] == "PAGE ")
    assert said == {
        "trading": "True",  # Sunday 8 November: the Muhurat session
        "holiday": "True",  # the page reads the holiday file too
        "next": "2026-11-08T08:35:00+05:30",  # the first wake-up that Sunday
        "notes": "[]",  # no line it could not read
    }, done.stdout + done.stderr


def _market_gate(etc: Path, now: str) -> int:
    """The deploy tool's listed() and market_open() on a fake clock: 0 = trading, no install."""
    text = DEPLOY_TOOL.read_text()
    start = text.index("listed() {")
    gate = text[start : text.index("\n}\n", text.index("market_open() {")) + 3]
    script = f'date() {{ echo "{now}"; }}\n' + gate.replace("/etc/sudarshan", str(etc))
    return subprocess.run(["bash", "-c", script + "market_open\n"]).returncode


@pytest.mark.parametrize(
    ("now", "trading"),
    [
        ("3 0829 2026-10-21", False),
        ("3 0830 2026-10-21", True),  # clear of auto-login's 08:35 and the alerts' 08:45
        ("3 1545 2026-10-21", True),
        ("3 1600 2026-10-21", True),  # through the alerts' 16:00 close, after F&O at 15:40
        ("3 1601 2026-10-21", False),
        ("6 1000 2026-10-24", False),  # a Saturday
    ],
)
def test_the_install_window_on_an_ordinary_trading_day(tmp_path, now, trading):
    """Wednesday 21 Oct 2026, an ordinary trading day: no install from 08:30 to 16:00 IST."""
    (tmp_path / EXTRA).write_bytes(b"")
    (tmp_path / HOLIDAYS).write_bytes(b"")
    assert (_market_gate(tmp_path, now) == 0) is trading


@pytest.mark.parametrize(
    ("then", "now", "trading"),
    [
        (MUHURAT, b"", True),  # the session as the run found it holds, though the file lost it
        (b"", MUHURAT, True),  # a session the run learned of holds too
        (b"", b"", False),
    ],
)
def test_the_gate_takes_the_stricter_of_the_two_views(tmp_path, then, now, trading):
    """Sunday 8 Nov 18:30: the day files as the run found them (GATE_VIEW) and as they are now."""
    for folder, extra in (("then", then), ("now", now)):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / EXTRA).write_bytes(extra + b"\n")
        (tmp_path / folder / HOLIDAYS).write_bytes(b"")
    text = DEPLOY_TOOL.read_text()
    gate = text[text.index("listed() {") : text.index("\n}\n", text.index("market_open() {")) + 3]
    script = (
        'date() { echo "7 1830 2026-11-08"; }\n'
        + f'GATE_VIEW="{tmp_path / "then"}"\n'
        + gate.replace("/etc/sudarshan", str(tmp_path / "now"))
        + "market_open\n"
    )
    assert (subprocess.run(["bash", "-c", script]).returncode == 0) is trading


def test_the_shipped_list_on_the_server(tmp_path):
    """Over the holiday file the server has, the Muhurat Sunday becomes a trading day with the
    afternoon session's own window."""
    holidays = b"# NSE trading holidays 2026\n2026-10-20  # Dussehra\n2026-11-10\n"
    server, out = _applied(tmp_path, {HOLIDAYS: holidays}, SHIPPED_LIST.read_bytes())
    assert server.day(HOLIDAYS).read_bytes() == holidays
    assert server.day().read_bytes() == SHIPPED + b"\n"
    sunday = date(2026, 11, 8)
    calendar = TradingCalendar(server.day(HOLIDAYS), server.day())
    assert calendar.is_trading_day(sunday)
    # auto-login: its usual morning wake-ups that Sunday; the token holds until 03:30 on Monday
    assert calendar.next_slot(datetime(2026, 11, 7, 20, 0, tzinfo=IST)) == datetime(
        2026, 11, 8, 8, 35, tzinfo=IST
    )
    # the order alerts: connected 15 minutes before the pre-open at 14:05 until 20 minutes after the
    # F&O close at 15:40, the margins of a normal day's 08:45-16:00 around 09:00-15:40
    alerts = AlertWindow(calendar, parse_window("08:45-16:00"), False, server.day())

    def at(hour: int, minute: int) -> datetime:
        return datetime(2026, 11, 8, hour, minute, tzinfo=IST)

    assert alerts.status(at(10, 0)) == (False, "before the alert window")
    assert alerts.status(at(13, 49))[0] is False and alerts.status(at(13, 50))[0] is True
    assert alerts.status(at(15, 59))[0] is True
    assert alerts.status(at(16, 0)) == (False, "after the alert window")
    assert alerts.next_open(at(9, 0)) == at(13, 50)
    # the deploy market gate: no install at any hour that Sunday
    for now in ("7 0300 2026-11-08", "7 1830 2026-11-08", "7 2330 2026-11-08"):
        assert _market_gate(server.etc, now) == 0
    assert _market_gate(server.etc, "7 1830 2026-11-15") == 1  # an ordinary Sunday stays open


def test_the_tool_runs_the_calendar_where_it_must():
    text = DEPLOY_TOOL.read_text()
    every_run = text.index("\n    if gate_view; then cal_run ")
    assert every_run < text.index("\njson=") < text.index('waiting for market close"; exit 0')
    fetch = text.index('cal_conf_fetch "${tag}" "${cal_conf}"')
    second_gate = text.index("the window opened during the download")
    stop = text.index('\nsystemctl stop "${SERVICE}"\n')
    env_apply = text.index("if ! env_sync apply ")
    cal_apply = text.index('cal_sync install "${cal_conf}" "${CAL_DIR}" "${tmp}/cal"')
    migrations = text.index('migrate_all.py"; then')
    start = text.index('\nsystemctl start "${SERVICE}"\n')
    assert fetch < second_gate < stop < env_apply < cal_apply < migrations < start
    begin = text.index("\nrollback() {")
    assert 'cal_restore "${tmp}/cal" "${CAL_DIR}"' in text[begin : text.index("\n}\n", begin)]


LIST_BODY = b"[nse_extra_trading_days.txt]\nline 2026-11-08  # whole\n"


@pytest.fixture
def lists_server():
    """A local stand-in for raw.githubusercontent.com; the tag in the path picks its answer."""
    release = threading.Event()

    class Answer(socketserver.BaseRequestHandler):
        def handle(self):
            path = self.request.recv(65536).decode(errors="replace").split(" ")[1]
            tag = path.split("/")[3]
            if tag == "hang":  # accepts and never answers
                release.wait(30)
                return
            if tag in ("missing", "error"):
                status = b"404 Not Found" if tag == "missing" else b"500 Internal Server Error"
                self.request.sendall(b"HTTP/1.1 " + status + b"\r\nContent-Length: 0\r\n\r\n")
                return
            body = {"nonl": LIST_BODY[:-1], "empty": b""}.get(tag, LIST_BODY)
            head = b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n" % len(body)
            self.request.sendall(head + (body[:20] if tag == "cut" else body))

    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True
        allow_reuse_address = True

    with Server(("127.0.0.1", 0), Answer) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        yield server.server_address[1]
        release.set()
        server.shutdown()


@pytest.mark.skipif(shutil.which("curl") is None, reason="needs curl")
@pytest.mark.parametrize("fetch", ["env_conf_fetch", "cal_conf_fetch"])
@pytest.mark.parametrize(
    ("tag", "code", "got"),
    [
        ("whole", 0, LIST_BODY),
        ("empty", 0, b""),  # a 200 with nothing in it: an empty list, whole
        ("missing", 3, b""),  # 404: no list (the settings take it for an empty one)
        ("cut", 1, None),  # curl sees 200, then a body shorter than promised
        ("nonl", 1, None),  # whole, but its last line has no newline
        ("error", 1, None),
        ("hang", 1, None),  # gives up after FETCH_TIMEOUT
    ],
)
def test_a_list_is_taken_only_when_it_arrived_whole(tmp_path, lists_server, fetch, tag, code, got):
    """The real curl, with the tool's own flags, against a local server."""
    if fetch == "env_conf_fetch" and code == 3:
        code = 0  # a release without a settings list sets nothing, and installs
    shims = tmp_path / "shims"
    shims.mkdir()
    real = shutil.which("curl")
    _shim(
        shims, "curl",
        'args=()\nfor a in "$@"; do args+=("${a/https:\\/\\/raw.githubusercontent.com\\//'
        f'http://127.0.0.1:{lists_server}/}}"); done\nexec {real} "${{args[@]}}"',
    )  # fmt: skip
    script = (
        "set -Eeuo pipefail\nOWNER_REPO=owner/sudarshan; FETCH_TIMEOUT=2\n"
        + _function("release_file")
        + "".join(line + "\n" for line in DEPLOY_TOOL.read_text().splitlines()
                  if line.startswith(("env_conf_fetch() {", "cal_conf_fetch() {")))
        + f'if {fetch} "$1" "$2"; then exit 0; else exit $?; fi\n'
    )  # fmt: skip
    env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
    env["PATH"] = f"{shims}{os.pathsep}{env['PATH']}"
    started = time.monotonic()
    done = subprocess.run(
        ["bash", "-c", script, "fetch", tag, str(tmp_path / "out")],
        capture_output=True, env=env, timeout=30,
    )  # fmt: skip
    assert done.returncode == code, done.stderr
    assert time.monotonic() - started < 15
    if got is not None:
        assert (tmp_path / "out").read_bytes() == got


FAKE_CURL = r"""
# A fake curl: the answer to a URL is web/KEY.code and web/KEY.body (KEY from the URL's sha256), else
# 404. web/KEY.cut: the body is cut off in transit (curl reports the code, then fails with 18).
# web/KEY.later: serving that URL takes long enough for the clock to pass FAKE_NOW_LATER.
out="" write="" url="" fail=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        -o) out="$2"; shift 2 ;;
        -w) write="$2"; shift 2 ;;
        -H|--max-time|--connect-timeout) shift 2 ;;
        --*) shift ;;
        -*) if [[ "$1" == *f* ]]; then fail=1; fi; shift ;;
        *) url="$1"; shift ;;
    esac
done
echo "${url}" >> "${FAKE_WEB}/requests"
key="$(printf '%s' "${url}" | sha256sum | cut -c1-16)"
if [[ -f "${FAKE_WEB}/${key}.later" ]]; then : > "${FAKE_WEB}/later"; fi
code=404
if [[ -f "${FAKE_WEB}/${key}.code" ]]; then code="$(cat "${FAKE_WEB}/${key}.code")"; fi
if [[ "${code}" == "000" ]]; then [[ -z "${write}" ]] || printf '000'; exit 7; fi
if [[ ${fail} -eq 1 && "${code}" != 2* ]]; then exit 22; fi
body="${FAKE_WEB}/${key}.body"; [[ -f "${body}" ]] || body=/dev/null
if [[ -n "${out}" ]]; then cat "${body}" > "${out}"; else cat "${body}"; fi
[[ -z "${write}" ]] || printf '%s' "${code}"
if [[ -f "${FAKE_WEB}/${key}.cut" ]]; then exit 18; fi
exit 0
"""

FAKES = {
    "curl": FAKE_CURL,
    "sudo": 'while [[ $# -gt 0 && "$1" != "--" ]]; do shift; done\nshift\nexec "$@"',
    "git": 'if [[ " $* " == *" rev-parse "* ]]; then echo 0123456789abcdef0123456789abcdef01234567; fi',
    "systemctl": 'echo "$*" >> "${FAKE_WEB}/systemctl"\nif [[ "$1" == "is-active" ]]; then echo active; fi',
    "uv": "exit 0",
    "sleep": "exit 0",
    # chown -R of the app and backup folders needs root; any other chown is the real one
    "chown": 'if [[ "$1" == "-R" ]]; then exit 0; fi\nexec CHOWN "$@"',
    "date": (
        'if [[ "$1" == "+%u %H%M %F" ]]; then\n'
        '    if [[ -f "${FAKE_WEB}/later" ]]; then echo "${FAKE_NOW_LATER}"; else echo "${FAKE_NOW}"; fi\n'
        '    exit 0\nfi\nexec DATE "$@"'
    ),
}


class Box:
    """A server for the whole tool: /etc/sudarshan, its state, backups and app, fakes on PATH."""

    OWNER_REPO = "owner/sudarshan"

    def __init__(self, root: Path, *, latest: str = "v1.0.0", now: str = "6 1200 2026-10-24"):
        self.root, self.now, self.later, self.migrations = root, now, now, "0"
        self.etc, self.state, self.backups = root / "etc", root / "state", root / "backups"
        self.app, self.bin, self.web, self.tmp = (
            root / "app",
            root / "bin",
            root / "web",
            root / "tmp",
        )
        self.units = root / "units"  # where the ring's unit files would go (/etc/systemd/system)
        for folder in (
            self.etc,
            self.state,
            self.backups,
            self.bin,
            self.web,
            self.tmp,
            self.units,
        ):
            folder.mkdir()
        (self.app / "frontend" / "dist").mkdir(parents=True)
        (self.app / "frontend" / "dist" / "index.html").write_text("old")
        (self.app / "upgrade").mkdir()
        (self.app / ".venv" / "bin").mkdir(parents=True)
        _shim(self.app / ".venv" / "bin", "python", 'exit "${FAKE_MIGRATIONS}"')  # migrate_all.py
        for name, body in FAKES.items():
            body = body.replace("CHOWN", shutil.which("chown") or "chown")
            _shim(self.bin, name, body.replace("DATE", shutil.which("date") or "date"))
        user = pwd.getpwuid(os.geteuid()).pw_name
        (self.etc / "deploy.env").write_text(
            f"REPO=https://github.com/{self.OWNER_REPO}\nASSET=frontend-dist.tar.gz\n"
            f"APP_DIR={self.app}\nSVC_USER={user}\nSERVICE=openalgo\n"
        )
        self.day(HOLIDAYS).write_bytes(b"2026-10-20  # Dussehra\n")
        self.day().write_bytes(b"# special sessions\n")
        (self.state / "deployed_tag").write_text("v1.0.0\n")
        asset = io.BytesIO()
        with tarfile.open(fileobj=asset, mode="w:gz") as tar:
            page = tarfile.TarInfo("dist/index.html")
            page.size = 3
            tar.addfile(page, io.BytesIO(b"new"))
        dist = asset.getvalue()
        assets = [
            {"name": "frontend-dist.tar.gz", "browser_download_url": "https://example.invalid/d"},
            {
                "name": "frontend-dist.tar.gz.sha256",
                "browser_download_url": "https://example.invalid/s",
            },
        ]
        api = f"https://api.github.com/repos/{self.OWNER_REPO}/releases/latest"
        self.serve(api, json.dumps({"tag_name": latest, "assets": assets}).encode())
        self.serve("https://example.invalid/d", dist)
        self.serve("https://example.invalid/s", f"{hashlib.sha256(dist).hexdigest()}  x\n".encode())
        self.serve("http://127.0.0.1:5000/", b"")  # the health check

    def key(self, url: str) -> str:
        return hashlib.sha256(url.encode()).hexdigest()[:16]

    def serve(self, url: str, body: bytes, code: str = "200", cut: bool = False) -> None:
        key = self.key(url)
        (self.web / f"{key}.code").write_text(code)
        (self.web / f"{key}.body").write_bytes(body)
        (self.web / f"{key}.cut").unlink(missing_ok=True)
        if cut:
            (self.web / f"{key}.cut").write_text("")

    def list_url(self, tag: str) -> str:
        path = f"{tag}/sudarshan/ops/deploy/nse-calendar.conf"
        return f"https://raw.githubusercontent.com/{self.OWNER_REPO}/{path}"

    def calendar(self, tag: str, listing: bytes, code: str = "200", cut: bool = False) -> None:
        self.serve(self.list_url(tag), listing, code, cut)

    def clock_moves_on(self, url: str, later: str) -> None:
        """While ``url`` is fetched the clock passes ``later``."""
        (self.web / f"{self.key(url)}.later").write_text("")
        self.later = later

    def day(self, name: str = EXTRA) -> Path:
        return self.etc / name

    def deployed(self) -> str:
        return (self.state / "deployed_tag").read_text().strip()

    def requests(self) -> list[str]:
        log = self.web / "requests"
        return log.read_text().splitlines() if log.exists() else []

    def run(self, *args: str, timeout: float = 120) -> subprocess.CompletedProcess:
        script = (
            DEPLOY_TOOL.read_text()
            .replace("/etc/sudarshan", str(self.etc))
            .replace('STATE="/var/lib/sudarshan"', f'STATE="{self.state}"')
            .replace("/opt/sudarshan/backups", str(self.backups))
            .replace("/usr/local/bin/uv", str(self.bin / "uv"))
            .replace('UNIT_DIR="/etc/systemd/system"', f'UNIT_DIR="{self.units}"')
        )
        (self.web / "later").unlink(missing_ok=True)
        return subprocess.run(
            ["bash", "-c", script, "sudarshan-deploy", *args],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            env={
                **os.environ,
                "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
                "TMPDIR": str(self.tmp),
                "LC_ALL": "C.UTF-8",
                "FAKE_WEB": str(self.web),
                "FAKE_NOW": self.now,
                "FAKE_NOW_LATER": self.later,
                "FAKE_MIGRATIONS": self.migrations,
            },
        )


whole_tool = pytest.mark.skipif(
    shutil.which("jq") is None or shutil.which("flock") is None, reason="needs jq and flock"
)


@whole_tool
def test_a_run_that_installs_nothing_applies_the_installed_list(tmp_path):
    box = Box(tmp_path)  # v1.0.0 is installed and is the latest release
    box.calendar("v1.0.0", FIRST)
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert box.day().read_bytes() == b"# special sessions\n" + MUHURAT + b"\n"
    assert f'calendar: {EXTRA}: added "{MUHURAT.decode()}"' in done.stdout
    assert f"calendar: {box.day()} updated\n" in done.stdout
    assert box.deployed() == "v1.0.0" and list(box.backups.iterdir()) == []  # nothing installed
    before = _snapshot(box.etc)
    again = box.run()  # the next run: nothing to change, nothing written, nothing logged
    assert (again.returncode, again.stdout) == (0, "")
    assert _snapshot(box.etc) == before
    assert box.requests().count(box.list_url("v1.0.0")) == 1  # fetched once, then kept


@whole_tool
def test_a_line_left_alone_is_logged_by_the_first_run_only(tmp_path):
    box = Box(tmp_path)
    box.day().write_bytes(b"2026-11-08 18:15-19:15  # the owner's own\n")
    box.calendar("v1.0.0", FIRST)
    first = box.run()
    assert first.returncode == 0, first.stdout + first.stderr
    assert f"calendar: {EXTRA}: 2026-11-08 left alone: the owner wrote it as" in first.stdout
    assert box.run().stdout == ""
    assert box.day().read_bytes() == b"2026-11-08 18:15-19:15  # the owner's own\n"


@whole_tool
def test_a_run_in_market_hours_applies_the_installed_list_only(tmp_path):
    box = Box(tmp_path, latest="v1.0.1", now="3 1000 2026-10-21")  # a Wednesday, 10:00 IST
    box.calendar("v1.0.0", FIRST)
    box.calendar("v1.0.1", LATER)
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert "release v1.0.1 waiting for market close" in done.stdout
    assert box.day().read_bytes() == b"# special sessions\n" + MUHURAT + b"\n"  # v1.0.0's line
    assert box.deployed() == "v1.0.0" and list(box.backups.iterdir()) == []
    assert not [url for url in box.requests() if "/v1.0.1/" in url]


@whole_tool
def test_scenario_a_a_holiday_for_today_never_opens_the_gate(tmp_path):
    """Wednesday 10:00 IST, a session running. The installed release ships a holiday for today
    that the file lacks, and a new release waits: the line is not added and nothing installs."""
    box = Box(tmp_path, latest="v1.0.1", now="3 1000 2026-10-21")
    shipped = b"[nse_holidays.txt]\nline 2026-10-21  # shipped by mistake\n"
    box.calendar("v1.0.0", shipped)
    box.calendar("v1.0.1", shipped)
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert "2026-10-21 not added: a holiday is added only before its day" in done.stdout
    assert "release v1.0.1 waiting for market close" in done.stdout
    assert box.day(HOLIDAYS).read_bytes() == b"2026-10-20  # Dussehra\n"
    assert box.deployed() == "v1.0.0" and not (box.web / "systemctl").exists()  # nothing stopped


@whole_tool
@pytest.mark.parametrize(
    "shipped",
    [
        # a was of another date: the list is refused
        b"[nse_extra_trading_days.txt]\nline 2026-11-09  # moved\nwas  " + MUHURAT + b"\n",
        # the same move as a valid list: the drop waits for an install
        b"[nse_extra_trading_days.txt]\ndrop " + MUHURAT + b"\nline 2026-11-09  # moved\n",
    ],
)
def test_scenario_x_a_session_is_not_moved_during_the_session(tmp_path, shipped):
    """Sunday 8 Nov, 18:30 IST, during the special session: the installed release's list moves the
    day to the 9th, and a new release waits. The day stays, and nothing installs."""
    box = Box(tmp_path, latest="v1.0.1", now="7 1830 2026-11-08")
    box.day().write_bytes(b"# special sessions\n" + MUHURAT + b"\n")
    (box.state / "calendar_applied").write_text(f"{EXTRA}|{MUHURAT.decode()}\n")
    box.calendar("v1.0.0", shipped)
    box.calendar("v1.0.1", shipped)
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert MUHURAT in box.day().read_bytes()
    assert "release v1.0.1 waiting for market close" in done.stdout
    assert box.deployed() == "v1.0.0" and not (box.web / "systemctl").exists()


@whole_tool
def test_scenario_y_a_session_learned_during_the_run_closes_its_gate(tmp_path):
    """Sunday 8 Nov, 18:30 IST: the day file lacks the session, which this run's calendar step
    adds. The gate takes the stricter of the files before and after that step: no install."""
    box = Box(tmp_path, latest="v1.0.1", now="7 1830 2026-11-08")
    box.calendar("v1.0.0", FIRST)
    box.calendar("v1.0.1", FIRST)
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert f'added "{MUHURAT.decode()}"' in done.stdout
    assert "release v1.0.1 waiting for market close" in done.stdout
    assert box.deployed() == "v1.0.0" and not (box.web / "systemctl").exists()


@whole_tool
def test_the_gate_reads_the_day_files_as_the_run_found_them(tmp_path):
    """Sunday 8 Nov, 18:30 IST, during the session. Should the calendar step ever take the session
    out of the file (here a fault does it while the step runs), the gate still holds the release."""
    box = Box(tmp_path, latest="v1.0.1", now="7 1830 2026-11-08")
    box.day().write_bytes(b"# special sessions\n" + MUHURAT + b"\n")
    real = shutil.which("mktemp")
    _shim(box.bin, "mktemp", f'if [[ "$1" == "-d" && ! -e "{box.root}/once" ]]; then : > "{box.root}/once"; '
          f'printf "# special sessions\\n" > "{box.day()}"; fi\nexec {real} "$@"')  # fmt: skip
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert box.day().read_bytes() == b"# special sessions\n"  # the fault happened
    assert "release v1.0.1 waiting for market close" in done.stdout
    assert box.deployed() == "v1.0.0" and not (box.web / "systemctl").exists()


@whole_tool
def test_scenario_b_a_line_the_owner_took_out_stays_out(tmp_path):
    box = Box(tmp_path)
    box.calendar("v1.0.0", FIRST)
    assert "added" in box.run().stdout
    taken_out = b"# special sessions\n# 2026-11-08 Muhurat: not trading it, owner\n"
    box.day().write_bytes(taken_out)
    first = box.run()
    assert first.returncode == 0, first.stdout + first.stderr
    assert "2026-11-08 not added again: this tool added it before" in first.stdout
    assert box.run().stdout == ""  # said once
    assert box.day().read_bytes() == taken_out


@whole_tool
def test_scenario_t_a_list_cut_off_in_transit_is_not_taken(tmp_path):
    full = SHIPPED_LIST.read_bytes()
    box = Box(tmp_path)
    box.calendar("v1.0.0", full[: len(full) - 38], cut=True)  # curl reports 200, then fails
    first = box.run()
    assert first.returncode == 0, first.stdout + first.stderr
    assert (
        "calendar: could not fetch the list of v1.0.0 whole; the next run tries again"
        in first.stdout
    )
    assert box.day().read_bytes() == b"# special sessions\n"
    assert not (box.state / "calendar-lists" / "v1.0.0").exists()  # nothing kept
    assert box.run().stdout == ""  # said once; fetched again
    box.calendar("v1.0.0", full)
    whole = box.run()
    assert f'added "{SHIPPED.decode()}"' in whole.stdout
    assert box.day().read_bytes() == b"# special sessions\n" + SHIPPED + b"\n"
    assert box.requests().count(box.list_url("v1.0.0")) == 3
    assert (box.state / "calendar-lists" / "v1.0.0").read_bytes() == full


@whole_tool
def test_scenario_s404_a_list_not_found_is_asked_for_again(tmp_path):
    """GitHub answers 404 for the installed release's list once, then serves it: nothing is kept
    from the 404, and the next run, the next timer tick, adds the line."""
    full = SHIPPED_LIST.read_bytes()
    box = Box(tmp_path)
    box.calendar("v1.0.0", b"", code="404")
    first = box.run()
    assert first.returncode == 0, first.stdout + first.stderr
    assert "calendar: no list was found for v1.0.0; the next run asks again" in first.stdout
    assert box.day().read_bytes() == b"# special sessions\n"
    assert not (box.state / "calendar-lists").exists()  # nothing kept
    assert box.run().stdout == ""  # said once while it holds; asked again
    box.calendar("v1.0.0", full)
    found = box.run()
    assert f'calendar: {EXTRA}: added "{SHIPPED.decode()}"' in found.stdout
    assert box.day().read_bytes() == b"# special sessions\n" + SHIPPED + b"\n"
    assert (box.state / "calendar-lists" / "v1.0.0").read_bytes() == full
    assert box.run().stdout == ""
    assert box.requests().count(box.list_url("v1.0.0")) == 3  # then kept


@whole_tool
def test_an_install_whose_list_was_not_found_takes_it_on_the_next_run(tmp_path):
    """The install's own fetch of the new release's list answers 404: the install goes on with the
    day files as they are and keeps no list, and the next run fetches it and adds the line."""
    full = SHIPPED_LIST.read_bytes()
    box = Box(tmp_path, latest="v1.0.1")
    box.calendar("v1.0.0", b"")  # the installed release's list: empty, and whole
    box.calendar("v1.0.1", b"", code="404")
    first = box.run()
    assert first.returncode == 0, first.stdout + first.stderr
    assert "calendar: no list was found for v1.0.1; the day files stay as they are" in first.stdout
    assert "deployed v1.0.1 OK" in first.stdout and box.deployed() == "v1.0.1"
    assert not (box.state / "calendar-lists" / "v1.0.1").exists()
    box.calendar("v1.0.1", full)
    second = box.run()
    assert f'calendar: {EXTRA}: added "{SHIPPED.decode()}"' in second.stdout
    assert box.day().read_bytes() == b"# special sessions\n" + SHIPPED + b"\n"
    assert sorted(p.name for p in (box.state / "calendar-lists").iterdir()) == ["v1.0.1"]


@whole_tool
@pytest.mark.parametrize(
    ("body", "installs"),
    [(b"", True), (b"# settings\nWEBSOCKET_BROKER_LINGER_SECONDS  300  0", False)],
)
def test_an_empty_settings_list_is_an_empty_list(tmp_path, body, installs):
    """A settings list that arrives empty with a 200 sets nothing, and the release installs; one
    whose last line has no newline may have been cut off, and stops the install as before."""
    box = Box(tmp_path, latest="v1.0.1")
    path = "v1.0.1/sudarshan/ops/deploy/openalgo-env.conf"
    box.serve(f"https://raw.githubusercontent.com/{box.OWNER_REPO}/{path}", body)
    done = box.run()
    assert done.returncode == (0 if installs else 1), done.stdout + done.stderr
    assert box.deployed() == ("v1.0.1" if installs else "v1.0.0")
    stopped = "could not fetch the settings list of v1.0.1 - not deploying" in done.stdout
    assert stopped is not installs


@whole_tool
def test_an_empty_line_in_the_notes_never_stops_a_run(tmp_path):
    box = Box(tmp_path, latest="v1.0.1")
    box.calendar("v1.0.0", FIRST)
    box.calendar("v1.0.1", FIRST)
    (box.state / "calendar_notes").write_text("calendar: something\n\n")  # a hand edit
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert "bad array subscript" not in done.stderr
    assert "deployed v1.0.1 OK" in done.stdout and box.deployed() == "v1.0.1"


TODAYS_SESSION = b"[nse_extra_trading_days.txt]\nline 2026-10-24 09:00-15:30  # special session\n"
WAITS = "release v1.0.1 waiting for market close (its calendar list makes today a trading day)"


@whole_tool
def test_a_release_that_adds_todays_session_waits(tmp_path):
    """Saturday 24 Oct 12:00 IST: a release published during a special session announces it. Its
    list would make today a trading day, so the release waits, as the gate will once the line is
    in, instead of stopping OpenAlgo during the session."""
    box = Box(tmp_path, latest="v1.0.1")  # Saturday 24 Oct, 12:00 IST
    box.calendar("v1.0.0", b"")
    box.calendar("v1.0.1", TODAYS_SESSION)
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert WAITS in done.stdout
    assert "stopping openalgo" not in done.stdout and not (box.web / "systemctl").exists()
    assert box.deployed() == "v1.0.0" and list(box.backups.iterdir()) == []
    assert box.day().read_bytes() == b"# special sessions\n"
    forced = box.run("--force")  # the owner's override still installs, with the session
    assert "deployed v1.0.1 OK" in forced.stdout, forced.stdout + forced.stderr
    assert box.day().read_bytes() == b"# special sessions\n" + TODAYS_SESSION.split(b"line ")[1]


@whole_tool
@pytest.mark.parametrize(
    ("now", "installs"), [("2 1100 2026-10-20", False), ("2 1601 2026-10-20", True)]
)
def test_a_release_that_takes_back_todays_holiday_waits_for_the_close(tmp_path, now, installs):
    """Tuesday 20 Oct, a holiday this tool added; a release published that day drops it. At 11:00
    today would be a trading day in its window, so the release waits; at 16:01 it installs and
    takes the line out."""
    box = Box(tmp_path, latest="v1.0.1", now=now)
    (box.state / "calendar_applied").write_text(f"{HOLIDAYS}|2026-10-20  # Dussehra\n")
    box.calendar("v1.0.0", b"")
    box.calendar("v1.0.1", b"[nse_holidays.txt]\ndrop 2026-10-20  # Dussehra\n")
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert (WAITS in done.stdout) is not installs
    assert box.deployed() == ("v1.0.1" if installs else "v1.0.0")
    assert box.day(HOLIDAYS).read_bytes() == (b"" if installs else b"2026-10-20  # Dussehra\n")


@whole_tool
def test_a_kept_list_that_is_a_symlink_is_not_used(tmp_path):
    """The list kept for the installed release must be this tool's own file: a symlink there, to a
    list someone planted, is passed over; the list is fetched again and kept as a plain file."""
    box = Box(tmp_path)
    planted = box.root / "planted.conf"
    planted.write_bytes(b"[nse_holidays.txt]\nline 2026-11-24  # planted\n")
    (box.state / "calendar-lists").mkdir()
    (box.state / "calendar-lists" / "v1.0.0").symlink_to(planted)
    box.calendar("v1.0.0", FIRST)
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert box.day(HOLIDAYS).read_bytes() == b"2026-10-20  # Dussehra\n"  # nothing planted
    assert box.day().read_bytes() == b"# special sessions\n" + MUHURAT + b"\n"
    kept = box.state / "calendar-lists" / "v1.0.0"
    assert not kept.is_symlink() and kept.read_bytes() == FIRST
    assert planted.read_bytes() == b"[nse_holidays.txt]\nline 2026-11-24  # planted\n"


@whole_tool
def test_a_rollback_puts_the_record_back_as_it_was(tmp_path):
    """The record held a line before the install; the install adds a holiday, then rolls back. The
    record goes back to what it held, so the next try adds the holiday again and records it."""
    box = Box(tmp_path, latest="v1.0.1")
    box.day().write_bytes(b"# special sessions\n" + MUHURAT + b"\n")
    record = box.state / "calendar_applied"
    record.write_text(f"{EXTRA}|{MUHURAT.decode()}\n")
    holiday = b"2026-11-24  # Guru Nanak Jayanti"
    box.calendar("v1.0.0", FIRST)
    box.calendar("v1.0.1", b"[nse_holidays.txt]\nline " + holiday + b"\n\n" + FIRST)
    box.migrations = "1"
    failed = box.run()
    assert "rolling back" in failed.stdout and box.deployed() == "v1.0.0", failed.stdout
    assert record.read_text() == f"{EXTRA}|{MUHURAT.decode()}\n"
    assert box.day(HOLIDAYS).read_bytes() == b"2026-10-20  # Dussehra\n"
    box.migrations = "0"
    again = box.run()
    assert "deployed v1.0.1 OK" in again.stdout, again.stdout + again.stderr
    assert box.day(HOLIDAYS).read_bytes() == b"2026-10-20  # Dussehra\n" + holiday + b"\n"
    assert set(record.read_text().splitlines()) == {
        f"{EXTRA}|{MUHURAT.decode()}",
        f"{HOLIDAYS}|{holiday.decode()}",
    }


@whole_tool
def test_the_gate_is_asked_again_before_the_stop(tmp_path):
    """Wednesday 08:28 IST: the first gate lets v1.0.1 in, the download takes until 08:31, and the
    install waits instead of stopping OpenAlgo as the trading morning starts."""
    box = Box(tmp_path, latest="v1.0.1", now="3 0828 2026-10-21")
    box.clock_moves_on("https://example.invalid/d", "3 0831 2026-10-21")
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert (
        "release v1.0.1 waiting for market close (the window opened during the download)"
        in done.stdout
    )
    assert "stopping openalgo" not in done.stdout and not (box.web / "systemctl").exists()
    assert box.deployed() == "v1.0.0" and list(box.backups.iterdir()) == []


@whole_tool
def test_a_fifo_in_place_of_a_day_file_never_hangs_the_tool(tmp_path):
    box = Box(tmp_path, latest="v1.0.1")
    box.day().unlink()
    os.mkfifo(box.day())
    box.calendar("v1.0.0", FIRST)
    box.calendar("v1.0.1", FIRST)
    try:
        done = box.run(timeout=30)
    finally:
        try:  # free a reader a broken tool may have left waiting on the FIFO
            os.close(os.open(box.day(), os.O_WRONLY | os.O_NONBLOCK))
        except OSError:
            pass
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"{box.day()} is not a plain file, so it is left alone" in done.stdout
    assert box.deployed() == "v1.0.1"


@whole_tool
def test_a_calendar_step_that_fails_never_stops_the_run(tmp_path):
    """The every-run step cannot make its private folder: logged, and the run still installs."""
    box = Box(tmp_path, latest="v1.0.1")
    real = shutil.which("mktemp")
    _shim(box.bin, "mktemp", f'if [[ "$1" == "-d" && ! -e "{box.root}/once" ]]; then : > "{box.root}/once"; '
          f'exit 1; fi\nexec {real} "$@"')  # fmt: skip
    box.calendar("v1.0.0", FIRST)
    box.calendar("v1.0.1", FIRST)
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert "WARNING: calendar: the day files could not be checked" in done.stdout
    assert "deployed v1.0.1 OK" in done.stdout and box.deployed() == "v1.0.1"


@whole_tool
def test_an_install_applies_its_list_with_a_backup(tmp_path):
    box = Box(tmp_path, latest="v1.0.1")
    box.day().write_bytes(b"# special sessions\n" + MUHURAT + b"\n")  # v1.0.0's line is there
    box.calendar("v1.0.0", FIRST)
    box.calendar("v1.0.1", LATER)
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert "deployed v1.0.1 OK" in done.stdout and box.deployed() == "v1.0.1"
    assert box.day().read_bytes() == b"# special sessions\n" + WINDOW + b"\n"
    (bdir,) = box.backups.iterdir()
    assert (bdir / EXTRA).read_bytes() == b"# special sessions\n" + MUHURAT + b"\n"
    assert f"calendar: {box.day()} updated; the previous one is in {bdir / EXTRA}" in done.stdout
    (log,) = box.state.glob("deploy-*.log")
    assert f'replaced "{MUHURAT.decode()}" with "{WINDOW.decode()}"' in log.read_text()
    again = box.run()  # v1.0.1 installed and its list kept: the next run has nothing to say
    assert (again.returncode, again.stdout) == (0, "")
    assert box.requests().count(box.list_url("v1.0.1")) == 1
    assert sorted(p.name for p in (box.state / "calendar-lists").iterdir()) == ["v1.0.1"]


@whole_tool
@pytest.mark.parametrize(
    ("code", "listing", "cut", "says"),
    [
        ("200", b"[nse_extra_trading_days.txt]\nline 2026-11-08 19:15-18:00\n", False, "list line 2 has a window"),
        ("200", b"[/etc/passwd]\nline 2026-11-08\n", False, "list line 1 names a file other than"),
        ("000", b"", False, "could not fetch the list of v1.0.1 whole; the day files stay as they are"),
        ("200", FIRST, True, "could not fetch the list of v1.0.1 whole; the day files stay as they are"),
        ("404", b"", False, "no list was found for v1.0.1; the day files stay as they are"),
    ],
)  # fmt: skip
def test_a_broken_list_never_blocks_an_install(tmp_path, code, listing, cut, says):
    box = Box(tmp_path, latest="v1.0.1")
    box.calendar("v1.0.1", listing, code, cut)
    before = _snapshot(box.etc)
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"calendar: {says}" in done.stdout
    assert "deployed v1.0.1 OK" in done.stdout and box.deployed() == "v1.0.1"
    assert _snapshot(box.etc) == before
    kept = box.state / "calendar-lists" / "v1.0.1"
    assert kept.exists() is (code == "200" and not cut)  # only a list that arrived whole is kept


@whole_tool
def test_a_rollback_puts_the_day_files_back(tmp_path):
    box = Box(tmp_path, latest="v1.0.1")
    box.migrations = "1"  # the install fails after the day files are written, and rolls back
    box.day().write_bytes(b"# special sessions\n" + MUHURAT + b"\n")
    box.calendar("v1.0.0", FIRST)
    box.calendar("v1.0.1", BOTH)
    first = box.run()  # no wait: the every-run step has nothing to do, the install is tried
    assert first.returncode == 1
    assert "migrations failed" in first.stdout and "rolling back" in first.stdout
    assert f'replaced "{MUHURAT.decode()}" with "{WINDOW.decode()}"' in first.stdout  # written...
    assert (
        f"calendar: {box.day()} put back as it was before the deploy" in first.stdout
    )  # ...undone
    assert f"calendar: {box.day(HOLIDAYS)} put back as it was before the deploy" in first.stdout
    assert box.day().read_bytes() == b"# special sessions\n" + MUHURAT + b"\n"
    assert box.day(HOLIDAYS).read_bytes() == b"2026-10-20  # Dussehra\n"
    assert not (box.state / "calendar_applied").exists()  # its record went back too
    assert not (box.state / "calendar-lists" / "v1.0.1").exists()
    assert box.deployed() == "v1.0.0"


@whole_tool
def test_the_check_option_shows_what_the_latest_list_would_change(tmp_path):
    box = Box(tmp_path, latest="v1.0.1")
    box.calendar("v1.0.1", FIRST)
    before = _snapshot(box.etc)
    done = box.run("--check")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "latest release: v1.0.1 | deployed: v1.0.0" in done.stdout
    assert f'calendar: {EXTRA}: would add "{MUHURAT.decode()}"' in done.stdout
    assert _snapshot(box.etc) == before
    assert sorted(p.name for p in box.state.iterdir()) == ["deploy.lock", "deployed_tag"]
    assert list(box.tmp.iterdir()) == []


@whole_tool
def test_the_check_option_says_when_no_list_was_found(tmp_path):
    box = Box(tmp_path, latest="v1.0.1")
    box.calendar("v1.0.1", b"", code="404")
    done = box.run("--check")
    assert done.returncode == 0, done.stdout + done.stderr
    assert "calendar: no list was found for v1.0.1" in done.stdout
    assert "could not fetch" not in done.stdout
