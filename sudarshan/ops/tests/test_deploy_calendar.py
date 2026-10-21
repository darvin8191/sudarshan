"""M0.1: the NSE calendar lines a release ships, merged into /etc/sudarshan by sudarshan-deploy.

``cal_sync`` and ``cal_restore`` are cut out of the deploy tool exactly as they ship (the pattern of
``test_deploy_env.py``) and run under the script's own shell options. Every shipped line is read with
the parsers of the readers of the day files. Then the whole tool runs on a fake server (fake curl,
sudo, git, systemctl, uv, chown, sleep and date first on PATH): a run that installs nothing, a run in
market hours, an install, an install whose list is broken, a rollback and ``--check``.
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
import stat
import subprocess
import tarfile
from datetime import date, datetime
from pathlib import Path

import pytest

from sudarshan.alerts.window import AlertWindow, parse_day_windows
from sudarshan.autologin.schedule import IST, TradingCalendar, parse_window
from sudarshan.autologin.schedule import parse_holidays as autologin_days
from sudarshan.feed.window import parse_holidays as feed_days

REPO = Path(__file__).resolve().parents[3]
DEPLOY_TOOL = REPO / "sudarshan" / "ops" / "deploy" / "sudarshan-deploy"
SHIPPED_LIST = REPO / "sudarshan" / "ops" / "deploy" / "nse-calendar.conf"
EXTRA, HOLIDAYS = "nse_extra_trading_days.txt", "nse_holidays.txt"
MUHURAT = b"2026-11-08  # Muhurat trading (NSE circular 212/2025); timings to follow"
WINDOW = b"2026-11-08 18:00-19:15  # Muhurat trading (NSE circular 212/2025)"
FIRST = b"[nse_extra_trading_days.txt]\nline " + MUHURAT + b"\n"  # the first release's list
LATER = b"[nse_extra_trading_days.txt]\nline " + WINDOW + b"\nwas  " + MUHURAT + b"\n"  # the window
BOTH = b"[nse_holidays.txt]\nline 2026-11-24  # Guru Nanak Jayanti\n\n" + LATER

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
    """A fake /etc/sudarshan with the day files, the private work dir and an install's backups."""

    def __init__(
        self,
        root: Path,
        files: dict[str, bytes] | None = None,
        listing: bytes = FIRST,
        owner: str | None = None,
        path: Path | None = None,
    ):
        self.root, self.etc, self.list = root, root / "etc", root / "list.conf"
        self.work, self.backup = root / "work", root / "bk"
        for folder in (self.etc, self.work, self.backup):
            folder.mkdir(parents=True)
        self.work.chmod(0o700)
        self.list.write_bytes(listing)
        for name, data in (files or {}).items():
            (self.etc / name).write_bytes(data)
        self.owner = owner or f"{os.geteuid()}:{os.getegid()}"
        self.path = path

    def day(self, name: str = EXTRA) -> Path:
        return self.etc / name

    def run(self, *args: str) -> subprocess.CompletedProcess:
        # As the script calls them: set -Eeuo pipefail, from an if (errexit off inside, nounset on),
        # in the UTF-8 locale a systemd service gets on Ubuntu.
        script = (
            f'set -Eeuo pipefail\nlog() {{ echo "$*"; }}\nCAL_OWNER="{self.owner}"; CAL_MODE=640\n'
            + _function("cal_day_ok")
            + _function("cal_sync")
            + _function("cal_restore")
            + 'if "$@"; then exit 0; else exit $?; fi\n'
        )
        env = {**os.environ, "LC_ALL": "C.UTF-8"}
        if self.path:
            env["PATH"] = f"{self.path}{os.pathsep}{env['PATH']}"
        return subprocess.run(
            ["bash", "-c", script, "deploy", *args],
            capture_output=True,
            text=True,
            errors="replace",
            cwd=self.root,
            env=env,
        )

    def sync(self, mode: str = "apply", backup: bool = True) -> subprocess.CompletedProcess:
        extra = [str(self.backup)] if mode == "apply" and backup else []
        return self.run("cal_sync", mode, str(self.list), str(self.etc), str(self.work), *extra)

    def restore(self) -> subprocess.CompletedProcess:
        return self.run("cal_restore", str(self.work), str(self.etc))


def _applied(tmp_path: Path, files: dict[str, bytes], listing: bytes = FIRST) -> tuple[Server, str]:
    server = Server(tmp_path, files, listing)
    done = server.sync()
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
    before = {p.name: (p.stat().st_ino, p.stat().st_mtime_ns) for p in server.etc.iterdir()}
    for mode, says in (("apply", f"calendar: nothing to change in {server.etc}\n"), ("quiet", "")):
        again = server.sync(mode, backup=False)
        assert (again.returncode, again.stdout) == (0, says)
    after = {p.name: (p.stat().st_ino, p.stat().st_mtime_ns) for p in server.etc.iterdir()}
    assert after == before


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
    assert (
        "`/etc/sudarshan/<svc>.env` (root:sudarshan 640"
        in (REPO / "sudarshan" / "README.md").read_text()
    )


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


def test_the_new_file_replaces_the_old_in_one_rename(tmp_path):
    """Written next to the old file and renamed over it: a reader has the whole old file or the new."""
    shims = tmp_path / "shims"
    shims.mkdir()
    _shim(shims, "mv", f'echo "$*" >> "{tmp_path}/mv.log"\nexec {shutil.which("mv")} "$@"')
    original = b"# x\n" + MUHURAT + b"\n"
    server = Server(tmp_path / "server", {EXTRA: original}, LATER, path=shims)
    with server.day().open("rb") as reader:  # a reader that opened the file before the merge
        done = server.sync(backup=False)
        assert done.returncode == 0, done.stdout + done.stderr
        assert reader.read() == original  # still the whole old file
    (move,) = (tmp_path / "mv.log").read_text().splitlines()
    *_, source, target = move.split()
    assert target == str(server.day())
    assert Path(source).parent == server.etc and Path(source).name.startswith(f".{EXTRA}.")
    assert sorted(p.name for p in server.etc.iterdir()) == [EXTRA]
    assert server.day().read_bytes() == b"# x\n" + WINDOW + b"\n"


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
    assert server.day().read_bytes() == original and server.day().stat().st_ino == before.st_ino
    assert sorted(p.name for p in server.etc.iterdir()) == [EXTRA]  # no temporary file left
    assert list(server.work.iterdir()) == []  # nothing for a rollback to put back


def test_a_rollback_puts_the_previous_files_back(tmp_path):
    uid, gid = _other_owner()
    original, holidays = b"# x\n" + MUHURAT + b"\n", b"2026-10-20\n"
    server = Server(tmp_path, {EXTRA: original, HOLIDAYS: holidays}, BOTH)
    os.chown(server.day(), uid, gid)
    server.day().chmod(0o640)
    assert server.sync().returncode == 0
    assert server.day().read_bytes() != original and server.day(HOLIDAYS).read_bytes() != holidays
    assert (server.backup / EXTRA).read_bytes() == original
    assert (server.backup / HOLIDAYS).read_bytes() == holidays
    done = server.restore()
    assert done.returncode == 0, done.stdout + done.stderr
    assert server.day().read_bytes() == original and server.day(HOLIDAYS).read_bytes() == holidays
    got = server.day().stat()
    assert (got.st_uid, got.st_gid, stat.S_IMODE(got.st_mode)) == (uid, gid, 0o640)
    assert f"calendar: {server.day()} put back as it was before the deploy" in done.stdout
    assert sorted(p.name for p in server.etc.iterdir()) == [EXTRA, HOLIDAYS]


def test_a_rollback_removes_a_file_the_install_created(tmp_path):
    server = Server(tmp_path, {HOLIDAYS: b"2026-10-20\n"})
    assert server.sync().returncode == 0 and server.day().exists()
    assert list(server.backup.iterdir()) == []  # there was nothing to keep
    done = server.restore()
    assert done.returncode == 0 and not server.day().exists()
    assert f"calendar: {server.day()} removed: the deploy had created it" in done.stdout


def test_a_rollback_without_a_calendar_step_changes_nothing(tmp_path):
    server = Server(tmp_path, {EXTRA: MUHURAT + b"\n"})
    before = _snapshot(server.etc)
    done = server.restore()
    assert (done.returncode, done.stdout) == (0, "")
    assert _snapshot(server.etc) == before


def test_a_run_without_a_backup_folder_keeps_nothing_for_a_rollback(tmp_path):
    server = Server(tmp_path, {EXTRA: b"# x\n"})
    assert server.sync(backup=False).returncode == 0
    assert list(server.work.iterdir()) == [] and list(server.backup.iterdir()) == []


def test_check_changes_nothing(tmp_path):
    server = Server(tmp_path, {EXTRA: b"# x\n" + MUHURAT + b"\n"}, BOTH)
    before = _snapshot(server.etc, server.backup)
    done = server.sync("check")
    assert done.returncode == 0, done.stdout + done.stderr
    assert _snapshot(server.etc, server.backup) == before
    assert list(server.work.iterdir()) == []  # its private copies are gone again
    assert f'{EXTRA}: would replace "{MUHURAT.decode()}" with "{WINDOW.decode()}"' in done.stdout
    assert f'{HOLIDAYS}: would add "2026-11-24  # Guru Nanak Jayanti"' in done.stdout
    assert f"calendar: would create {server.day(HOLIDAYS)}" in done.stdout


@pytest.mark.parametrize(
    ("listing", "says"),
    [
        (b"line 2026-11-08\n", "list line 1 comes before the first [FILE] line"),
        (b"[nse_holidays.txt]\nadd 2026-11-08\n", "list line 2 is not a [FILE] line"),
        (b"[openalgo.env]\nline 2026-11-08\n", "list line 1 names a file other than"),
        (b"[../nse_holidays.txt]\n", "list line 1 names a file other than"),
        (b"[nse_holidays.txt]\n[nse_holidays.txt]\n", "list line 2 names nse_holidays.txt a second"),
        (b"[nse_holidays.txt]\nline 2026-02-29\n", "list line 2 has a date that does not exist"),
        (b"[nse_holidays.txt]\nline 08-11-2026\n", "list line 2 is not YYYY-MM-DD and"),
        (b"[nse_holidays.txt]\nline 2026-11-08 Diwali\n", "list line 2 is not YYYY-MM-DD and"),
        (b"[nse_holidays.txt]\nline 2026-11-08 18:00-19:15\n", "list line 2 is not YYYY-MM-DD and"),
        (b"[nse_extra_trading_days.txt]\nline 2026-11-08 19:15-18:00\n", "list line 2 has a window"),
        (b"[nse_extra_trading_days.txt]\nline 2026-11-08 18:00-24:00\n", "list line 2 has a window"),
        (b"[nse_extra_trading_days.txt]\nline 2026-11-08 6:00-7:00\n", "list line 2 is not YYYY-MM-DD,"),
        (b"[nse_extra_trading_days.txt]\nline 2026-11-08 # \xe0\xa4\xae\n",
         "list line 2 is not YYYY-MM-DD, an optional HH:MM-HH:MM window and an optional # comment, "
         "in plain ASCII"),
        (b"[nse_extra_trading_days.txt]\nline 2026-11-08\nline 2026-11-08 # x\n",
         "list line 3 is a second line for 2026-11-08"),
        (b"[nse_extra_trading_days.txt]\nwas 2026-11-08\n", "list line 2 is a was with no line above"),
        (b"[nse_extra_trading_days.txt]\nline 2026-11-09\nwas 2026-11-08\nwas 2026-11-08\n",
         "list line 4 repeats a was"),
        (b"[nse_extra_trading_days.txt]\nline 2026-11-09\nwas 2026-11-08\nline 2026-11-08\n",
         "list line 3 is also a line to keep"),
    ],
)  # fmt: skip
def test_a_bad_list_is_refused_and_changes_nothing(tmp_path, listing, says):
    server = Server(tmp_path, {EXTRA: b"# x\n", HOLIDAYS: b"2026-10-20\n"}, listing)
    before = _snapshot(server.etc)
    done = server.sync()
    assert done.returncode == 1
    assert f"calendar: {says}" in done.stdout
    assert _snapshot(server.etc) == before and list(server.backup.iterdir()) == []
    quiet = server.sync("quiet")  # a run that has already reported this list
    assert (quiet.returncode, quiet.stdout) == (1, "")


def test_a_symlinked_day_file_is_not_followed(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.write_bytes(b"# not a day file\n")
    server = Server(tmp_path / "server")
    server.day().symlink_to(elsewhere)
    for mode in ("check", "apply"):
        done = server.sync(mode)
        assert done.returncode == 0
        assert (
            f"WARNING: calendar: {server.day()} is not a plain file, so it is left alone"
            in done.stdout
        )
    assert server.day().is_symlink() and elsewhere.read_bytes() == b"# not a day file\n"
    assert list(server.backup.iterdir()) == []


def _shipped(listing: str) -> list[tuple[str | None, str]]:
    """(file, text) of every `line` and `was` in a list, read as the list's header describes."""
    found, name = [], None
    for raw in listing.splitlines():
        if section := re.fullmatch(r"\s*\[(.+)\]\s*", raw):
            name = section.group(1)
        elif entry := re.fullmatch(r"\s*(?:line|was)\s+(.*\S)\s*", raw):
            found.append((name, entry.group(1)))
    return found


def _deploy_gate_lists(folder: Path, day: str, text: str) -> bool:
    """The deploy tool's own listed(), cut out as it ships, on a file holding ``text``."""
    (folder / "day.txt").write_text(text + "\n")
    script = _function("listed") + f'listed "{day}" "{folder / "day.txt"}"\n'
    return subprocess.run(["bash", "-c", script]).returncode == 0


def test_every_shipped_line_is_read_by_every_reader(tmp_path, caplog):
    """Auto-login and the alerts' calendar, the alerts' own window, the feed and the market gate."""
    caplog.set_level(logging.WARNING)
    shipped = _shipped(SHIPPED_LIST.read_text())
    assert (EXTRA, MUHURAT.decode()) in shipped
    for name, text in shipped:
        assert name in (HOLIDAYS, EXTRA)
        day = date.fromisoformat(text.split()[0])
        assert autologin_days(text) == ({day}, 0)
        assert _deploy_gate_lists(tmp_path, day.isoformat(), text)
        if name == HOLIDAYS:
            assert feed_days(text) == ({day}, 0)  # the feed reads only the holiday file
        else:
            words = text.split("#", 1)[0].split()
            assert parse_day_windows(text) == ({day: parse_window(words[1])} if words[1:] else {})
    assert caplog.records == []  # no reader found a line it could not read


def _market_gate(etc: Path, now: str) -> int:
    """The deploy tool's listed() and market_open() on a fake clock: 0 = trading, no install."""
    text = DEPLOY_TOOL.read_text()
    start = text.index("listed() {")
    gate = text[start : text.index("\n}\n", text.index("market_open() {")) + 3]
    script = (
        f'date() {{ echo "{now}"; }}\n' + gate.replace("/etc/sudarshan", str(etc)) + "market_open\n"
    )
    return subprocess.run(["bash", "-c", script]).returncode


def test_the_shipped_list_on_the_server(tmp_path):
    """Over the holiday file the server has, the Muhurat Sunday becomes a trading day."""
    holidays = b"# NSE trading holidays 2026\n2026-10-20  # Dussehra\n2026-11-10\n"
    server, out = _applied(tmp_path, {HOLIDAYS: holidays}, SHIPPED_LIST.read_bytes())
    assert server.day(HOLIDAYS).read_bytes() == holidays
    assert server.day().read_bytes() == MUHURAT + b"\n"
    sunday = date(2026, 11, 8)
    calendar = TradingCalendar(server.day(HOLIDAYS), server.day())
    assert calendar.is_trading_day(sunday)
    # auto-login: its usual morning wake-ups that Sunday
    assert calendar.next_slot(datetime(2026, 11, 7, 20, 0, tzinfo=IST)) == datetime(
        2026, 11, 8, 8, 35, tzinfo=IST
    )
    # the order alerts: no window of its own yet, so the usual 08:45-16:00
    alerts = AlertWindow(calendar, parse_window("08:45-16:00"), False, server.day())
    assert alerts.status(datetime(2026, 11, 8, 10, 0, tzinfo=IST))[0] is True
    assert alerts.status(datetime(2026, 11, 8, 18, 30, tzinfo=IST)) == (
        False,
        "after the alert window",
    )
    # the deploy market gate: no install at any hour that Sunday
    for now in ("7 0300 2026-11-08", "7 1830 2026-11-08", "7 2330 2026-11-08"):
        assert _market_gate(server.etc, now) == 0
    assert _market_gate(server.etc, "7 1830 2026-11-15") == 1  # an ordinary Sunday stays open


def test_the_tool_runs_the_calendar_where_it_must():
    text = DEPLOY_TOOL.read_text()
    every_run = text.index('\nif [[ "${MODE}" != "check" ]]; then cal_run ')
    assert (
        every_run < text.index("\njson=") < text.index("waiting for market close")
    )  # before any exit
    fetch = text.index('cal_conf_fetch "${tag}" "${cal_conf}"')
    stop = text.index('\nsystemctl stop "${SERVICE}"\n')
    env_apply = text.index("if ! env_sync apply ")
    cal_apply = text.index('cal_sync apply "${cal_conf}" "${CAL_DIR}" "${tmp}/cal" "${bdir}"')
    migrations = text.index('migrate_all.py"; then')
    start = text.index('\nsystemctl start "${SERVICE}"\n')
    assert fetch < stop < env_apply < cal_apply < migrations < start
    begin = text.index("\nrollback() {")
    assert 'cal_restore "${tmp}/cal" "${CAL_DIR}"' in text[begin : text.index("\n}\n", begin)]


FAKE_CURL = r"""
# A fake curl: the answer to a URL is web/KEY.code and web/KEY.body (KEY from the URL's sha256), else 404.
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
code=404
if [[ -f "${FAKE_WEB}/${key}.code" ]]; then code="$(cat "${FAKE_WEB}/${key}.code")"; fi
if [[ "${code}" == "000" ]]; then [[ -z "${write}" ]] || printf '000'; exit 7; fi
if [[ ${fail} -eq 1 && "${code}" != 2* ]]; then exit 22; fi
body="${FAKE_WEB}/${key}.body"; [[ -f "${body}" ]] || body=/dev/null
if [[ -n "${out}" ]]; then cat "${body}" > "${out}"; else cat "${body}"; fi
[[ -z "${write}" ]] || printf '%s' "${code}"
exit 0
"""

FAKES = {
    "curl": FAKE_CURL,
    "sudo": 'while [[ $# -gt 0 && "$1" != "--" ]]; do shift; done\nshift\nexec "$@"',
    "git": 'if [[ " $* " == *" rev-parse "* ]]; then echo 0123456789abcdef0123456789abcdef01234567; fi',
    "systemctl": 'if [[ "$1" == "is-active" ]]; then echo active; fi',
    "uv": "exit 0",
    "sleep": "exit 0",
    # chown -R of the app and backup folders needs root; any other chown is the real one
    "chown": 'if [[ "$1" == "-R" ]]; then exit 0; fi\nexec CHOWN "$@"',
    "date": 'if [[ "$1" == "+%u %H%M %F" ]]; then echo "${FAKE_NOW}"; exit 0; fi\nexec DATE "$@"',
}


class Box:
    """A server for the whole tool: /etc/sudarshan, its state, backups and app, fakes on PATH."""

    OWNER_REPO = "owner/sudarshan"

    def __init__(self, root: Path, *, latest: str = "v1.0.0", now: str = "6 1200 2026-10-24"):
        self.root, self.now, self.migrations = root, now, "0"  # a Saturday noon: the market is shut
        self.etc, self.state, self.backups = root / "etc", root / "state", root / "backups"
        self.app, self.bin, self.web, self.tmp = (
            root / "app",
            root / "bin",
            root / "web",
            root / "tmp",
        )
        for folder in (self.etc, self.state, self.backups, self.bin, self.web, self.tmp):
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

    def serve(self, url: str, body: bytes, code: str = "200") -> None:
        key = hashlib.sha256(url.encode()).hexdigest()[:16]
        (self.web / f"{key}.code").write_text(code)
        (self.web / f"{key}.body").write_bytes(body)

    def calendar(self, tag: str, listing: bytes, code: str = "200") -> None:
        path = f"{tag}/sudarshan/ops/deploy/nse-calendar.conf"
        self.serve(f"https://raw.githubusercontent.com/{self.OWNER_REPO}/{path}", listing, code)

    def day(self, name: str = EXTRA) -> Path:
        return self.etc / name

    def deployed(self) -> str:
        return (self.state / "deployed_tag").read_text().strip()

    def requests(self) -> list[str]:
        log = self.web / "requests"
        return log.read_text().splitlines() if log.exists() else []

    def run(self, *args: str) -> subprocess.CompletedProcess:
        script = (
            DEPLOY_TOOL.read_text()
            .replace("/etc/sudarshan", str(self.etc))
            .replace('STATE="/var/lib/sudarshan"', f'STATE="{self.state}"')
            .replace("/opt/sudarshan/backups", str(self.backups))
            .replace("/usr/local/bin/uv", str(self.bin / "uv"))
        )
        return subprocess.run(
            ["bash", "-c", script, "sudarshan-deploy", *args],
            capture_output=True,
            text=True,
            errors="replace",
            env={
                **os.environ,
                "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
                "TMPDIR": str(self.tmp),
                "LC_ALL": "C.UTF-8",
                "FAKE_WEB": str(self.web),
                "FAKE_NOW": self.now,
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
    again = box.run()  # v1.0.1 installed and its list applied: the next run has nothing to say
    assert (again.returncode, again.stdout) == (0, "")


@whole_tool
@pytest.mark.parametrize(
    ("code", "listing", "says"),
    [
        ("200", b"[nse_extra_trading_days.txt]\nline 2026-11-08 19:15-18:00\n", "list line 2 has a window"),
        ("200", b"[/etc/passwd]\nline 2026-11-08\n", "list line 1 names a file other than"),
        ("000", b"", "could not fetch the list of v1.0.1; the day files stay as they are"),
    ],
)  # fmt: skip
def test_a_broken_list_never_blocks_an_install(tmp_path, code, listing, says):
    box = Box(tmp_path, latest="v1.0.1")
    box.calendar("v1.0.1", listing, code)
    before = _snapshot(box.etc)
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"calendar: {says}" in done.stdout
    assert "deployed v1.0.1 OK" in done.stdout and box.deployed() == "v1.0.1"
    assert _snapshot(box.etc) == before


@whole_tool
def test_a_rollback_puts_the_day_files_back(tmp_path):
    box = Box(tmp_path, latest="v1.0.1")
    box.migrations = "1"  # the install fails after the day files are written, and rolls back
    box.day().write_bytes(b"# special sessions\n" + MUHURAT + b"\n")
    box.calendar("v1.0.0", FIRST)
    box.calendar("v1.0.1", BOTH)
    before = _snapshot(box.etc)
    done = box.run()
    assert done.returncode == 1
    assert "migrations failed" in done.stdout and "rolling back" in done.stdout
    assert f'replaced "{MUHURAT.decode()}" with "{WINDOW.decode()}"' in done.stdout  # written...
    assert f"calendar: {box.day()} put back as it was before the deploy" in done.stdout  # ...undone
    assert f"calendar: {box.day(HOLIDAYS)} put back as it was before the deploy" in done.stdout
    assert {k: v[0] for k, v in _snapshot(box.etc).items()} == {k: v[0] for k, v in before.items()}
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
