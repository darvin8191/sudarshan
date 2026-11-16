"""O12: the TRINETRA letterbox's set-up script, run whole on a folder of the test's own.

``BOOTSTRAP_ROOT`` puts a test folder before every path the script touches, and a fake ``getent``
answers from a fake account list, so the script runs here without root and without a real
account. Nothing in it may read or name a path of another system."""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "signals" / "bootstrap.sh"
ME = os.geteuid()


@pytest.fixture
def server(tmp_path):
    root = tmp_path / "root"
    (root / "etc" / "sudarshan").mkdir(parents=True)
    (root / "etc" / "sudarshan" / "deploy.env").write_text("APP_DIR=/opt/sudarshan/apps/openalgo\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    getent = bin_dir / "getent"
    getent.write_text(
        '#!/usr/bin/env bash\n[[ "$1" == passwd ]] || exit 2\ngrep "^$2:" "$FAKE_PASSWD" || exit 2\n'
    )
    getent.chmod(0o755)
    passwd = tmp_path / "passwd"
    passwd.write_text(
        "publisher:x:4321:4321::/nonexistent:/usr/sbin/nologin\n"
        "other:x:5555:5555::/nonexistent:/usr/sbin/nologin\n"
        "boss:x:0:0::/root:/bin/bash\n"
        "sudarshan:x:7777:7777::/nonexistent:/usr/sbin/nologin\n"
    )
    env = {**os.environ, "BOOTSTRAP_ROOT": str(root), "FAKE_PASSWD": str(passwd),
           "PATH": f"{bin_dir}:{os.environ['PATH']}"}  # fmt: skip
    return root, env


def run(env, *args):
    return subprocess.run(["bash", str(SCRIPT), *args], env=env, capture_output=True, text=True,
                          timeout=60)  # fmt: skip


def mode(path: Path) -> int:
    return stat.S_IMODE(path.lstat().st_mode)


def test_it_parses_and_explains_itself(server):
    _root, env = server
    assert subprocess.run(["bash", "-n", str(SCRIPT)], timeout=30).returncode == 0
    help_text = run(env, "--help").stdout
    assert "--publisher ACCOUNT" in help_text and "touches nothing of TRINETRA's" in help_text


@pytest.mark.parametrize(
    ("args", "words"),
    [((), "Name the account TRINETRA runs as"),
     (("--publisher", "nobody-here"), "There is no account named nobody-here"),
     (("--publisher", "Bad Name"), "is not an account name"),
     (("--publisher", "boss"), "is root"),
     (("--publisher", "sudarshan"), "SUDARSHAN's own account"),
     (("--wrong",), "usage")],
)  # fmt: skip
def test_what_it_refuses_changes_nothing(server, args, words):
    root, env = server
    done = run(env, *args)
    assert done.returncode == 2 and words in done.stdout + done.stderr
    assert not (root / "var" / "spool" / "sudarshan-signals").exists()
    assert not (root / "etc" / "sudarshan" / "signals.env").exists()


def test_no_settings_folder_means_no_set_up(server):
    root, env = server
    for f in (root / "etc" / "sudarshan").iterdir():
        f.unlink()
    (root / "etc" / "sudarshan").rmdir()
    done = run(env, "--publisher", "publisher")
    assert done.returncode == 2 and "the deploy tool is not set up" in done.stdout


def test_a_dry_run_says_what_it_would_do(server):
    root, env = server
    done = run(env, "--publisher", "publisher", "--dry-run")
    assert done.returncode == 0 and "would change: new folder" in done.stdout
    assert "Dry run: nothing was changed." in done.stdout
    assert not (root / "var" / "spool" / "sudarshan-signals" / "in").exists()


def test_the_first_run_makes_the_letterbox_and_the_second_changes_nothing(server):
    root, env = server
    done = run(env, "--publisher", "publisher")
    assert done.returncode == 0, done.stdout
    box = root / "var" / "spool" / "sudarshan-signals" / "in"
    assert mode(box.parent) == 0o755 and mode(box) == 0o1733 and box.stat().st_uid == ME
    env_file = root / "etc" / "sudarshan" / "signals.env"
    assert mode(env_file) == 0o640
    assert env_file.read_text().splitlines()[-1] == "SUDARSHAN_SIGNALS_PUBLISHER_UID=4321"
    assert "PASS. The letterbox is ready." in done.stdout
    assert "cd /opt/sudarshan/apps/openalgo && sudo -u sudarshan .venv/bin/python -m " \
           "sudarshan.signals.check" in done.stdout  # fmt: skip
    again = run(env, "--publisher", "publisher")
    assert again.returncode == 0 and "PASS. Nothing was changed" in again.stdout
    assert not [line for line in again.stdout.splitlines() if line.startswith("changed:")]


def test_a_wrong_mode_is_set_right_and_a_new_publisher_rewrites_the_number(server):
    root, env = server
    assert run(env, "--publisher", "publisher").returncode == 0
    box = root / "var" / "spool" / "sudarshan-signals" / "in"
    os.chmod(box, 0o777)
    done = run(env, "--publisher", "other")
    assert done.returncode == 0 and "set to" in done.stdout and mode(box) == 0o1733
    env_file = root / "etc" / "sudarshan" / "signals.env"
    assert "SUDARSHAN_SIGNALS_PUBLISHER_UID=5555" in env_file.read_text()
    assert not [p for p in env_file.parent.iterdir() if p.name.startswith(".signals.env")]


def test_a_link_in_place_of_the_letterbox_fails(server, tmp_path):
    root, env = server
    spool = root / "var" / "spool" / "sudarshan-signals"
    spool.mkdir(parents=True)
    os.chmod(spool, 0o755)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (spool / "in").symlink_to(elsewhere)
    done = run(env, "--publisher", "publisher")
    assert done.returncode == 1 and "is there but is not a folder" in done.stdout
    assert mode(elsewhere) != 0o1733  # never followed


def test_it_names_only_its_own_paths():
    text = SCRIPT.read_text()
    paths = set(re.findall(r"(?<![\w$])/(?:etc|var|opt|home|srv|run)/[\w./-]*", text))
    allowed = ("/var/spool/sudarshan-signals", "/etc/sudarshan", "/opt/sudarshan", "/var/spool")
    assert paths and all(p.startswith(allowed) for p in paths), paths
    body = text.split("set -uo pipefail", 1)[1]
    for word in ("systemctl", "useradd", "usermod", "gpasswd", "setfacl", "nft ", "kill"):
        assert word not in body, word
    # Python is named once, in the line it tells the owner to run as sudarshan; it never runs it
    assert [line.strip()[:4] for line in body.splitlines() if "python" in line] == ["say "]
