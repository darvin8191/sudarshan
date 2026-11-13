"""The owner's one-time set-up of the offsite copy (release review v0.8.0: H2, M1, L2, L3).

H2: root never runs Python from the app folder. Root only makes the settings file, empty, with
coreutils (``install``); ``--configure`` then runs as ``sudarshan`` and writes into that file in
place, never replacing it and never changing its owner or mode.

M1: the certificate reaches ``--configure`` on standard input (the owner's own shell opens the file
in his home), never through a fixed name in /tmp; the questions are asked on the terminal, so the
hidden prompt for the upload link still works with standard input taken; the fingerprint of the
certificate received is shown and nothing is saved until the owner types yes.

L2: a settings file that is there but cannot be used is a notice, never "not set up"; an empty one,
as the set-up leaves it until it is filled in, is "not set up".

L3: a certificate the nightly encryption cannot use is refused at the set-up, not every night.

Real openssl, real files, and for the terminal a real pseudo-terminal in a child process.
"""

from __future__ import annotations

import json
import os
import pty
import re
import select
import shutil
import stat
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import pytest
from backup_fakes import NIGHT, Server, go

from sudarshan.ops import backup, offsite

pytestmark = pytest.mark.skipif(not shutil.which("openssl"), reason="needs openssl")

REPO = Path(__file__).resolve().parents[3]
TOKEN = "SECRETPARTOKEN0123456789abcdef"
LINK = f"https://objectstorage.example.com/p/{TOKEN}/n/ns/b/bk/o/"
TODAY = date(2026, 11, 13)
LATER = "2027-11-12"
#: The user the settings file belongs to in these tests: this process's own, or a made-up one
#: when the tests run as root (root may own nothing here: the command refuses to run as root).
USER = os.geteuid() or 4242


@pytest.fixture
def server(tmp_path):
    pretend = Server(tmp_path / "server")
    yield pretend
    pretend.close()


def make_pair(folder: Path, *, kind: str = "rsa:2048") -> tuple[Path, Path]:
    folder.mkdir(parents=True, exist_ok=True)
    key, cert = folder / "k.pem", folder / "c.pem"
    newkey = (
        ["-newkey", kind]
        if kind.startswith("rsa")
        else ["-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1"]
    )
    subprocess.run(
        ["openssl", "req", "-x509", *newkey, "-nodes", "-keyout", str(key), "-out", str(cert),
         "-days", "30", "-subj", "/CN=test"],
        check=True, capture_output=True,
    )  # fmt: skip
    return key, cert


def openssl_fingerprint(cert: Path) -> str:
    """What the .bat prints on the laptop: ``sha256 Fingerprint=AB:CD:...``, the pairs only."""
    out = subprocess.run(
        ["openssl", "x509", "-noout", "-fingerprint", "-sha256", "-in", str(cert)],
        check=True, capture_output=True, text=True,
    ).stdout  # fmt: skip
    return out.strip().split("=", 1)[1]


def made_by_install(path: Path) -> Path:
    """The file as ``sudo install -o sudarshan -g sudarshan -m 600 /dev/null <path>`` leaves it."""
    path.write_bytes(b"")
    path.chmod(0o600)
    if os.geteuid() == 0:
        os.chown(path, USER, USER)
    return path


class Terminal:
    """The owner at the terminal: answers in order, and what was shown."""

    def __init__(self, *answers: str):
        self.answers = list(answers)
        self.shown: list[str] = []
        self.asked: list[tuple[str, bool]] = []

    def say(self, text: str) -> None:
        self.shown.append(text)

    def ask(self, prompt: str) -> str:
        self.asked.append((prompt, False))
        return self.answers.pop(0)

    def ask_hidden(self, prompt: str) -> str:
        self.asked.append((prompt, True))
        return self.answers.pop(0)

    @property
    def text(self) -> str:
        return "\n".join(self.shown + [prompt for prompt, _ in self.asked])


def configure(server: Server, cert_text: str, terminal: Terminal, *, euid: int = USER) -> int:
    return offsite.configure(server.etc, cert_text, terminal, today=TODAY, euid=euid)


# ---- H2: written in place, as the job's own user ----------------------------------------------


def test_the_settings_go_into_the_file_root_made_which_keeps_its_owner_mode_and_inode(
    server, tmp_path
):
    _key, cert = make_pair(tmp_path / "pair")
    path = made_by_install(server.etc / "offsite.json")
    before = path.stat()
    terminal = Terminal("yes", LATER, LINK)

    assert configure(server, cert.read_text(), terminal) == 0

    after = path.stat()
    assert (after.st_ino, after.st_uid, after.st_gid) == (
        before.st_ino,
        before.st_uid,
        before.st_gid,
    )
    assert stat.S_IMODE(after.st_mode) == 0o600
    assert [p.name for p in server.etc.iterdir() if "offsite" in p.name] == ["offsite.json"]
    settings = offsite.load(server.etc)
    assert (settings.par_url, settings.expires) == (LINK, date(2027, 11, 12))
    assert TOKEN not in terminal.text  # the link is never shown back
    assert [hidden for _prompt, hidden in terminal.asked] == [False, False, True]


def test_root_is_refused_before_anything_is_asked_or_written(server, tmp_path):
    _key, cert = make_pair(tmp_path / "pair")
    path = made_by_install(server.etc / "offsite.json")
    terminal = Terminal()

    assert configure(server, cert.read_text(), terminal, euid=0) == 2

    assert terminal.asked == [] and path.read_bytes() == b""
    assert "sudo -u sudarshan" in terminal.text


@pytest.mark.parametrize("what", ["missing", "link", "folder", "other owner", "others read"])
def test_a_settings_file_that_is_not_the_one_root_made_is_refused_and_left_alone(
    server, tmp_path, what
):
    _key, cert = make_pair(tmp_path / "pair")
    path = server.etc / "offsite.json"
    elsewhere = made_by_install(tmp_path / "elsewhere.json")
    euid = USER
    if what == "link":
        path.symlink_to(elsewhere)
    elif what == "folder":
        path.mkdir()
    elif what == "other owner":
        made_by_install(path)
        euid = USER + 1  # the file is someone else's
    elif what == "others read":
        made_by_install(path).chmod(0o644)
    terminal = Terminal("yes", LATER, LINK)

    code = configure(server, cert.read_text(), terminal, euid=euid)

    assert code == 2
    assert terminal.asked == []  # told before anything is typed
    assert elsewhere.read_bytes() == b""  # a link is never followed
    if what in ("other owner", "others read"):
        assert path.read_bytes() == b""
    if what == "other owner":  # both causes named: not run as sudarshan, or someone else's file
        assert "not this user's to write" in terminal.text and "as sudarshan" in terminal.text
    if what == "missing":
        assert not path.exists()  # never made here: root makes it, with install
        assert "sudo install -o sudarshan -g sudarshan -m 600 /dev/null" in terminal.text


# ---- M1: the certificate shown before anything is saved ---------------------------------------


def test_the_fingerprint_shown_is_the_one_openssl_prints_on_the_laptop(tmp_path):
    _key, cert = make_pair(tmp_path / "pair")
    assert offsite.fingerprint(cert.read_text()) == openssl_fingerprint(cert)


@pytest.mark.parametrize("answer", ["no", "", "y", "YES please"])
def test_nothing_is_saved_unless_the_owner_types_yes(server, tmp_path, answer):
    _key, cert = make_pair(tmp_path / "pair")
    path = made_by_install(server.etc / "offsite.json")
    terminal = Terminal(answer, LATER, LINK)

    assert configure(server, cert.read_text(), terminal) == 1

    assert path.read_bytes() == b""
    assert openssl_fingerprint(cert) in terminal.text
    assert len(terminal.asked) == 1  # neither the date nor the link was asked for


def test_a_private_key_or_something_else_on_standard_input_is_refused(server, tmp_path):
    key, cert = make_pair(tmp_path / "pair")
    path = made_by_install(server.etc / "offsite.json")
    for text in (key.read_text(), cert.read_text() + key.read_text(), "hello\n", ""):
        terminal = Terminal("yes", LATER, LINK)
        assert configure(server, text, terminal) == 2
        assert terminal.asked == [] and path.read_bytes() == b""


def test_a_date_that_has_come_or_a_link_that_is_not_a_bucket_link_is_not_saved(server, tmp_path):
    _key, cert = make_pair(tmp_path / "pair")
    path = made_by_install(server.etc / "offsite.json")
    for date_typed, link in (
        (TODAY.isoformat(), LINK),
        ("2026-11-01", LINK),
        ("12/11/2027", LINK),
        (LATER, "http://objectstorage.example.com/p/x/n/a/b/c/o/"),
        (LATER, "https://objectstorage.example.com/p/x/n/a/b/c/"),
    ):
        assert configure(server, cert.read_text(), Terminal("yes", date_typed, link)) == 2
        assert path.read_bytes() == b""


# ---- L3: a certificate the nightly encryption cannot use ---------------------------------------


def test_a_certificate_the_nightly_encryption_cannot_use_is_refused_at_the_set_up(server, tmp_path):
    _key, cert = make_pair(tmp_path / "ec", kind="ec")
    path = made_by_install(server.etc / "offsite.json")
    terminal = Terminal("yes", LATER, LINK)

    assert configure(server, cert.read_text(), terminal) == 2

    assert path.read_bytes() == b"" and terminal.asked == []
    assert "step 7" in terminal.text


# ---- L2 and H2: what the job and the check make of the file ------------------------------------


def test_an_empty_file_as_the_set_up_leaves_it_is_not_set_up_for_the_job_and_the_check(server):
    server.populate()
    made_by_install(server.etc / "offsite.json")

    result = go(server)

    assert (result.offsite, result.failures) == ("off", [])
    assert server.events() == []
    assert "offsite: not set" in backup.check(server.settings(), NIGHT)


@pytest.mark.parametrize("what", ["link", "folder"])
def test_a_file_that_is_there_but_cannot_be_read_is_a_notice_not_off(server, tmp_path, what):
    server.populate()
    _key, cert = make_pair(tmp_path / "pair")
    real = tmp_path / "real.json"  # good settings, but reached only through a link
    real.write_text(
        json.dumps({"par_url": LINK, "expires": LATER, "certificate": cert.read_text()})
    )
    path = server.etc / "offsite.json"
    path.symlink_to(real) if what == "link" else path.mkdir()

    result = go(server)

    assert result.offsite == "failed"
    (event,) = [e for e in server.events() if e.fact("why") == "offsite"]
    assert event.fact("reason") == "config"
    assert "offsite: the settings file cannot be used" in backup.check(server.settings(), NIGHT)


# ---- M1: the questions come from the terminal, the certificate from standard input ------------


class Pty:
    """A child process whose terminal is ours, the way ``ssh -t`` gives the server one."""

    def __init__(self, argv: list[str], stdin: Path, env: dict[str, str]):
        self.seen = b""
        self.pid, self.fd = pty.fork()
        if self.pid == 0:  # the child: standard input is the file, the terminal stays the pty
            try:
                os.dup2(os.open(stdin, os.O_RDONLY), 0)
                os.chdir(REPO)
                os.execve(argv[0], argv, env)
            finally:
                os._exit(127)

    def wait_for(self, text: str, timeout: float = 60.0) -> None:
        deadline = time.monotonic() + timeout
        while text.encode() not in self.seen:
            left = deadline - time.monotonic()
            assert left > 0, f"never saw {text!r}; saw {self.seen.decode(errors='replace')!r}"
            if select.select([self.fd], [], [], left)[0]:
                try:
                    chunk = os.read(self.fd, 4096)
                except OSError:  # the child has gone
                    chunk = b""
                assert chunk, f"ended before {text!r}; saw {self.seen.decode(errors='replace')!r}"
                self.seen += chunk

    def type(self, line: str) -> None:
        os.write(self.fd, line.encode() + b"\r")

    def finish(self, timeout: float = 60.0) -> int:
        deadline = time.monotonic() + timeout
        while True:
            done, status = os.waitpid(self.pid, os.WNOHANG)
            if done:
                break
            assert time.monotonic() < deadline, "the child did not end"
            if select.select([self.fd], [], [], 0.2)[0]:
                try:
                    self.seen += os.read(self.fd, 4096)
                except OSError:
                    pass
        os.close(self.fd)
        return os.waitstatus_to_exitcode(status)


def test_the_hidden_prompt_and_the_questions_read_the_terminal_while_the_certificate_is_stdin(
    server, tmp_path
):
    """The .bat runs ``... --configure < ~/sudarshan-backup.crt`` over ``ssh -t``: standard input
    is the certificate, so every answer must come from the terminal. getpass reads /dev/tty (it
    falls back to standard input only when there is no terminal), and so do the other questions;
    the link is not echoed as it is typed."""
    _key, cert = make_pair(tmp_path / "pair")
    path = made_by_install(server.etc / "offsite.json")
    # The command refuses root, and these tests may run as root: the child is told it runs as the
    # file's owner. Nothing else about it is changed.
    starter = (
        "import os, sys; from sudarshan.ops import offsite; "
        f"offsite.os.geteuid = lambda: {USER}; sys.exit(offsite.main(sys.argv[1:]))"
    )
    env = {**{k: v for k, v in os.environ.items() if k in ("PATH", "LANG", "HOME")},
           "PYTHONPATH": str(REPO), "TERM": "dumb"}  # fmt: skip
    child = Pty([sys.executable, "-c", starter, "--configure", "--etc-dir", str(server.etc)],
                cert, env)  # fmt: skip

    child.wait_for("Type yes")
    child.type("yes")
    child.wait_for("(YYYY-MM-DD)")
    child.type(LATER)
    child.wait_for("Upload link")
    child.type(LINK)
    child.wait_for("Saved.")
    code = child.finish()

    shown = child.seen.decode(errors="replace")
    assert code == 0, shown
    assert openssl_fingerprint(cert) in shown
    assert TOKEN not in shown  # not echoed as typed, never printed
    assert offsite.load(server.etc).par_url == LINK
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


# ---- the .bat the README gives the owner -------------------------------------------------------


README = (REPO / "sudarshan" / "README.md").read_text()


def the_bat() -> str:
    """The ``bat`` block of step 9, as the owner saves it (the README indents it by three)."""
    found = re.search(r"\n   ```bat\n(.*?)\n   ```\n", README, re.DOTALL)
    assert found, "step 9 has no bat block"
    return "\n".join(line.removeprefix("   ") for line in found.group(1).splitlines())


def test_the_bat_never_runs_the_app_s_python_as_root_and_uses_no_fixed_name_in_tmp():
    bat = the_bat()
    assert "/tmp" not in bat
    run_by_sudo = re.findall(r"\bsudo\s+([^;&|{}<]+)", bat)
    assert len(run_by_sudo) == 3, run_by_sudo
    for command in run_by_sudo:
        if "python" in command or ".venv" in command or "sudarshan." in command:
            assert command.startswith("-u sudarshan "), command  # never as root
    assert (
        "{ sudo test -e /etc/sudarshan/offsite.json || sudo install -o sudarshan -g sudarshan "
        "-m 600 /dev/null /etc/sudarshan/offsite.json; }"
    ) in bat  # root makes the file only, only when it is not there, with coreutils
    assert (
        "sudo -u sudarshan .venv/bin/python -m sudarshan.ops.offsite --configure "
        "< ~/sudarshan-backup.crt; code=$?; rm -f ~/sudarshan-backup.crt; exit $code"
    ) in bat  # as the job's user, the certificate on standard input, the copy removed whatever
    assert "scp sudarshan-backup.crt %SERVER%:sudarshan-backup.crt" in bat
    assert "ssh -t %SERVER%" in bat  # a terminal for the questions and the hidden prompt


def test_the_bat_shows_the_fingerprint_before_sending_and_stops_at_the_first_failed_step():
    lines = the_bat().splitlines()
    commands = [i for i, line in enumerate(lines) if re.match(r'("%OPENSSL%"|scp|ssh) ', line)]
    assert [lines[i].split()[0] for i in commands] == ['"%OPENSSL%"', "scp", "ssh"]
    assert "x509 -noout -fingerprint -sha256" in lines[commands[0]]
    for i in commands:
        assert lines[i + 1] == "if errorlevel 1 (", lines[i]
        assert "goto stop" in lines[i + 3], lines[i]
    assert lines[-3:] == [":stop", "pause", "exit /b 1"]


def test_the_server_part_of_the_bat_is_valid_shell():
    remote = re.search(r'ssh -t %SERVER% "(.*)"$', the_bat(), re.MULTILINE).group(1)
    assert "%" not in remote  # cmd would expand it before ssh sends the line
    done = subprocess.run(["bash", "-n", "-c", remote], capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stderr


def test_what_the_readme_says_the_owner_sees_is_what_the_command_prints():
    import inspect

    printed = inspect.getsource(offsite.configure)
    for line in (
        "The certificate the server received has this SHA-256 fingerprint:",
        "Type yes if the two are the same",
        "Expiry date of the upload link, as the console shows it (YYYY-MM-DD):",
        "Upload link (nothing shows as you paste or type it; press Enter after it):",
        "Saved.",
        "Not saved:",
    ):
        assert line in printed, line
        assert line in " ".join(README.split()), line


def test_without_a_terminal_it_is_refused_and_nothing_is_written(server, tmp_path):
    _key, cert = make_pair(tmp_path / "pair")
    made_by_install(server.etc / "offsite.json")
    done = subprocess.run(
        [sys.executable, "-m", "sudarshan.ops.offsite", "--configure", "--etc-dir", str(server.etc)],
        stdin=cert.open("rb"), capture_output=True, text=True, cwd=REPO, timeout=60,
        env={**os.environ, "PYTHONPATH": str(REPO)}, start_new_session=True,  # no terminal
    )  # fmt: skip
    assert done.returncode == 2
    assert re.search(r"terminal", done.stderr) and "ssh -t" in done.stderr
    assert (server.etc / "offsite.json").read_bytes() == b""
