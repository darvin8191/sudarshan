"""O2' (P6; cross-design Y1-Y3, Y25): the deploy tool's provision step and the feed's tokens.

The whole tool runs on the fake server of ``test_deploy_calendar.py`` (fake curl, sudo, git, systemctl,
uv, chown, stat, sleep and date first on PATH). The fake sudo runs a command as the caller and records
the account it was asked for; the fake stat names ``sudarshan`` as the owner of every ring folder unless
a test says otherwise (``Box.owner``). On this server ``/etc/sudarshan-family`` is ``<root>/etc-family``
and ``/etc/sudarshan/feed-clients`` is ``<root>/etc/feed-clients``. The app's Python is a shim that says
what a test tells it to (the family's derive and check) and records who ran it, and from where.

What these pin: before the owner's set-up nothing of the family happens at all, and the one new thing
on every server is the price feed's client tokens; after it, every run keeps the keys and copies in
place, makes the one-time secrets once, runs the check as the family account and writes provisioned
only after it passes; a failure removes it, stops the family unit and is told once; root runs none of
the app's Python (every such call is behind sudo -u), and no key or token is ever a command's
argument. The unit policy and the copies by the folder's account (O1) are in
``test_deploy_family_units.py``.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
import test_deploy_calendar as calendar_harness
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    load_pem_private_key,
)
from test_deploy_calendar import (
    DEPLOY_TOOL,
    EXTRA,
    HOLIDAYS,
    Box,
    _function,
    _shim,
    _snapshot,
    whole_tool,
)
from test_deploy_family_units import FAMILY, FAMILY_UNIT, fam_dir, family_unit, lines, notices
from test_deploy_notices import install_box
from test_deploy_ring_databases import ring_box, the_backup

pytestmark = [
    pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash"),
    pytest.mark.skipif(shutil.which("sqlite3") is None, reason="needs the sqlite3 command"),
    pytest.mark.skipif(shutil.which("openssl") is None, reason="needs openssl"),
]

KEY_NAMES = ("admin", "nudge", "internal", "portfolio-family", "journal-family")
CLIENTS = (
    "sudarshan-alerts",
    "sudarshan-screener",
    "sudarshan-portfolio",
    "sudarshan-journal",
    "sudarshan-family",
    "sudarshan-protect",
)
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\n")


def owned_as(box: Box, spec: str, folder: Path, name: str) -> bool:
    """The fake chown was asked to give the temporary file of ``name`` in ``folder`` to ``spec``."""
    pattern = (
        rf"-- {re.escape(spec)} {re.escape(str(folder))}/\.{re.escape(name)}\.[A-Za-z0-9]{{6}}"
    )
    return any(re.fullmatch(pattern, line) for line in lines(box, "chown"))


PASSING = (
    "PASS OpenAlgo's .env: closed to this account (/x/.env)\n"
    "PASS OpenAlgo's market-data bus (127.0.0.1:5555): refused by the firewall\n"
)
FAILING = (
    "PASS OpenAlgo's .env: closed to this account (/x/.env)\n"
    "FAIL OpenAlgo's db folder: this account can open /x/db/openalgo.db\n"
    "FAIL OpenAlgo's market-data bus (127.0.0.1:5555): the port itself refused it, so no firewall "
    "rule stops it\n"
)
# The app's Python: the family's derive and check say what the test wrote in web/, the migrate
# modules say nothing, migrate_all.py exits with the box's migrations. Every -m call is recorded with
# the account it ran as and the folder it ran in.
PYTHON = r"""
if [[ "$1" == -m ]]; then
    echo "${FAKE_SUDO_USER:-root} $2 $(pwd)" >> "${FAKE_WEB}/python_m"
    case "$2" in
        sudarshan.family.derive)
            cat "${FAKE_WEB}/derive_out" 2>/dev/null
            exit "$(cat "${FAKE_WEB}/derive_rc" 2>/dev/null || echo 0)" ;;
        sudarshan.family.check)
            cat "${FAKE_WEB}/check_out" 2>/dev/null
            exit "$(cat "${FAKE_WEB}/check_rc" 2>/dev/null || echo 0)" ;;
    esac
    exit 0
fi
exit "${FAKE_MIGRATIONS}"
"""


def feed_dir(box: Box) -> Path:
    return box.etc / "feed-clients"


def new_keys() -> dict[str, str]:
    return {name: base64.urlsafe_b64encode(os.urandom(32)).decode() for name in KEY_NAMES}


def keys_text(keys: dict[str, str]) -> str:
    return "".join(f"{name} {key}\n" for name, key in keys.items())


def derive_says(box: Box, text: str, rc: int = 0) -> None:
    (box.web / "derive_out").write_text(text)
    (box.web / "derive_rc").write_text(str(rc))


def check_says(box: Box, text: str, rc: int = 0) -> None:
    (box.web / "check_out").write_text(text)
    (box.web / "check_rc").write_text(str(rc))


def bootstrapped(box: Box, keys: dict[str, str] | None = None) -> dict[str, str]:
    """The owner's set-up has made /etc/sudarshan-family; derive answers keys and the check passes."""
    folder = fam_dir(box)
    folder.mkdir(mode=0o750)
    folder.chmod(0o750)
    family_python(box)
    keys = keys or new_keys()
    derive_says(box, keys_text(keys))
    check_says(box, PASSING)
    return keys


def quiet_box(tmp_path: Path) -> Box:
    """v1.0.0 installed long ago and the latest release: a run that installs nothing and has nothing
    to say."""
    box = Box(tmp_path)
    box.calendar("v1.0.0", b"")
    (box.state / "notice_announced").write_text("v1.0.0\n")
    return box


def family_notices(box: Box) -> list[dict]:
    return [n for n in notices(box) if n["kind"] == "family_check_failed"]


def family_python(box: Box, migration: str = "") -> None:
    """The app's Python as PYTHON says, with ``migration`` run by migrate_all.py before it exits."""
    body = PYTHON.replace(
        'exit "${FAKE_MIGRATIONS}"\n', f'{migration}\nexit "${{FAKE_MIGRATIONS}}"\n'
    )
    _shim(box.app / ".venv" / "bin", "python", body)


# ---- O2': nothing before the owner's set-up but the feed's client tokens -------------------


def neutral_tool(folder: Path) -> Path:
    """The tool with the three steps O2' added made into no-ops: what it did before them."""
    text = DEPLOY_TOOL.read_text()
    for name, code in (("feed_tokens", 0), ("fam_provision", 2), ("fam_backup", 0)):
        head = f"\n{name}() ("
        assert text.count(head) == 1, name
        text = text.replace(head, f"{head} exit {code};")
    path = folder / "sudarshan-deploy"
    path.write_text(text)
    return path


def normalized(box: Box, text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        line = re.sub(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [^]]*\] ", "", line)
        line = line.replace(str(box.root), "ROOT")
        line = re.sub(r"\d{8}-\d{6}", "TS", line)
        line = re.sub(
            r"checksum OK \([0-9a-f]{12}\.\.\.\)", "checksum OK (SHA)", line
        )  # each box's tarball
        out.append(re.sub(r"tmp\.[A-Za-z0-9]{10}", "TMP", line))
    return out


def server_view(box: Box) -> dict[str, object]:
    """What a run leaves, made comparable between two servers: every file and folder by its place
    (times in names made TS), with its bytes, except the files that hold the time they were made
    (the run's log, the notices, the previous frontend's tarball)."""
    view: dict[str, object] = {}
    timed = ("events.jsonl", "frontend-dist-prev.tar.gz")
    for top in (box.etc, box.state, box.backups, box.units, box.ringvar, box.app):
        for path in [top, *sorted(top.rglob("*"))]:
            rel = re.sub(r"\d{8}-\d{6}", "TS", str(path.relative_to(box.root)))
            if path.name.startswith("deploy-") and path.suffix == ".log" or path.name in timed:
                view[rel] = "(times)"
            elif path.is_file() and not path.is_symlink():
                content = path.read_bytes().replace(str(box.root).encode(), b"ROOT")
                view[rel] = (content, stat.S_IMODE(path.stat().st_mode))
            else:
                view[rel] = "folder"
    for name in ("sudo", "systemctl", "requests", "python_m"):
        view[f"web/{name}"] = normalized(box, "\n".join(lines(box, name)))
    view["web/chown"] = normalized(box, "\n".join(lines(box, "chown")))
    view["notices"] = [n["kind"] for n in notices(box)]
    return view


def scenario(kind: str, root: Path) -> Box:
    root.mkdir()
    if kind == "quiet":
        box = quiet_box(root)
    elif kind == "install":
        box = ring_box(root)
    else:
        box = ring_box(root)
        box.migrations = "1"
    _shim(box.app / ".venv" / "bin", "python", PYTHON)  # records any -m call: there must be none
    return box


@whole_tool
@pytest.mark.parametrize("kind", ["quiet", "install", "rollback"])
def test_before_the_set_up_a_run_is_what_it_was_but_for_the_feed_tokens(
    tmp_path, kind, monkeypatch
):
    """The owner has not run the family set-up: the run's words, its files, what it ran and what it
    told are those of the tool without the O2' steps, but for the feed's six client tokens."""
    box = scenario(kind, tmp_path / "now")
    done = box.run()
    old = scenario(kind, tmp_path / "before")
    monkeypatch.setattr(calendar_harness, "DEPLOY_TOOL", neutral_tool(tmp_path))
    was = old.run()
    assert done.returncode == was.returncode, done.stdout + done.stderr
    assert normalized(box, done.stdout) == normalized(old, was.stdout)
    assert normalized(box, done.stderr) == normalized(old, was.stderr)
    now_view, old_view = server_view(box), server_view(old)
    tokens = {k: v for k, v in now_view.items() if k.startswith("etc/feed-clients")}
    assert sorted(tokens) == ["etc/feed-clients", *sorted(f"etc/feed-clients/{c}" for c in CLIENTS)]
    for name in CLIENTS:
        content, mode = tokens[f"etc/feed-clients/{name}"]
        assert TOKEN.fullmatch(content.decode()) and mode == 0o640
    assert len({tokens[f"etc/feed-clients/{c}"][0] for c in CLIENTS}) == 6  # six different ones
    chowns = [line for line in now_view.pop("web/chown") if "feed-clients" not in line]
    assert chowns == old_view.pop("web/chown")
    assert {k: v for k, v in now_view.items() if k not in tokens} == old_view
    assert not (box.root / "etc-family").exists() and lines(box, "python_m") == []
    assert "family" not in done.stdout.lower()


@whole_tool
def test_the_tokens_are_made_once_and_never_written_again(tmp_path):
    box = quiet_box(tmp_path)
    assert box.run().returncode == 0
    folder, beside = feed_dir(box), box.etc / ".feed-clients.new"
    assert stat.S_IMODE(folder.stat().st_mode) == 0o750
    chowns = lines(box, "chown")
    assert f"-- root:sudarshan {beside}" in chowns  # made beside, then renamed into place
    for name in CLIENTS:
        assert f"-- root:sudarshan {beside}/{name}" in chowns, name
    assert not beside.exists()
    (folder / "sudarshan-alerts").write_text(
        "chosen by the owner\n"
    )  # whatever a token holds, it stays
    (folder / "sudarshan-protect").unlink()
    (tmp_path / "elsewhere").write_text("not a token\n")
    (folder / "sudarshan-protect").symlink_to(tmp_path / "elsewhere")  # a link: never followed
    before = _snapshot(folder)
    again = box.run()
    assert (again.returncode, again.stdout) == (0, "")
    assert _snapshot(folder) == before
    assert (tmp_path / "elsewhere").read_text() == "not a token\n"
    (folder / "sudarshan-journal").unlink()  # one taken away is made again, and only that one
    third = box.run()
    assert third.stdout == "" and TOKEN.fullmatch((folder / "sudarshan-journal").read_text())
    assert owned_as(box, "root:sudarshan", folder, "sudarshan-journal")  # through a file beside it
    assert (folder / "sudarshan-alerts").read_text() == "chosen by the owner\n"


# Records, for a move onto the tokens folder, whether the folder was already there and what the folder
# moved into place held, then moves it.
MV_WATCH = r"""
dest="${@: -1}"; src="${@: -2:1}"
if [[ "${dest}" == "FEEDDIR" ]]; then
    if [[ -e "${dest}" ]]; then state=there; else state=absent; fi
    echo "${state} $(cd -- "${src}" && ls -A | sort | tr '\n' ' ')" >> "${FAKE_WEB}/mv_feed"
fi
exec MV "$@"
"""


@whole_tool
def test_the_six_tokens_appear_at_once(tmp_path):
    """L1: the feed reads the folder again within a second and, once any token is there, refuses a
    client whose file is not yet: the first six are made in a folder beside it, renamed whole."""
    box = quiet_box(tmp_path)
    folder = feed_dir(box)
    watch = MV_WATCH.replace("FEEDDIR", str(folder)).replace("MV", shutil.which("mv") or "mv")
    _shim(box.bin, "mv", watch)
    done = box.run()
    assert (done.returncode, done.stdout) == (0, "")
    moves = [line.split() for line in (box.web / "mv_feed").read_text().splitlines()]
    assert moves == [["absent", *sorted(CLIENTS)]]  # one move: no folder before, all six in it
    assert sorted(p.name for p in folder.iterdir()) == sorted(CLIENTS)
    assert all(TOKEN.fullmatch((folder / name).read_text()) for name in CLIENTS)
    assert not (box.etc / ".feed-clients.new").exists()


@whole_tool
def test_a_half_made_tokens_folder_a_killed_run_left_is_made_again_whole(tmp_path):
    box = quiet_box(tmp_path)
    beside = box.etc / ".feed-clients.new"
    beside.mkdir(mode=0o750)
    (beside / "sudarshan-alerts").write_text("cut off\n")
    assert box.run().returncode == 0
    folder = feed_dir(box)
    assert sorted(p.name for p in folder.iterdir()) == sorted(CLIENTS)
    assert TOKEN.fullmatch((folder / "sudarshan-alerts").read_text())
    assert not beside.exists()


@whole_tool
def test_a_tokens_folder_others_could_write_is_left_alone_and_said_once(tmp_path):
    box = quiet_box(tmp_path)
    folder = feed_dir(box)
    folder.mkdir()
    folder.chmod(0o777)
    done = box.run()
    says = f"WARNING: feed: {folder} is not a folder owned by root and closed to others, so no client token is made there"
    assert says in done.stdout and list(folder.iterdir()) == []
    assert box.run().stdout == ""  # said once while it holds
    folder.chmod(0o750)
    assert box.run().stdout == "" and len(list(folder.iterdir())) == 6


@whole_tool
def test_removing_a_ring_service_hands_the_feed_its_token_never_on_a_command_line(tmp_path):
    """The feed takes a change of a client's claims only with that client's token (lane FL)."""
    box = install_box(tmp_path)
    (box.units / FAMILY_UNIT).write_text(family_unit(box))
    (box.state / "managed_units").write_text(f"{FAMILY_UNIT}\n")
    feed_dir(box).mkdir()
    token = "T" * 43
    (feed_dir(box) / FAMILY).write_text(token + "\n")
    box.serve(f"http://127.0.0.1:5580/claims/{FAMILY}", b'{"dropped": 3}')
    (box.bin / "fakecurl").write_text("#!/usr/bin/env bash\n" + calendar_harness.FAKE_CURL)
    (box.bin / "fakecurl").chmod(0o755)
    _shim(  # what curl was given, and what the header file it was pointed at held at that moment
        box.bin,
        "curl",
        'printf "%s\\n" "$*" >> "${FAKE_WEB}/curl_args"\n'
        'for a in "$@"; do if [[ "$a" == @* ]]; then cat "${a#@}" >> "${FAKE_WEB}/curl_header"; fi; done\n'
        f'exec "{box.bin}/fakecurl" "$@"',
    )
    done = box.run("--remove-ring", FAMILY_UNIT)
    assert done.returncode == 0, done.stdout + done.stderr
    assert (box.web / "curl_header").read_text() == f"Authorization: Bearer {token}\n"
    assert token not in (box.web / "curl_args").read_text()  # never on the command line
    assert f"ring: the price feed no longer keeps {FAMILY}'s instruments" in done.stdout
    assert list(box.tmp.iterdir()) == []  # the header file went with its folder
    (feed_dir(box) / FAMILY).unlink()  # no token yet (a feed from before the tokens): asked without
    (box.state / "managed_units").write_text(f"{FAMILY_UNIT}\n")
    (box.web / "curl_args").unlink()
    assert box.run("--remove-ring", FAMILY_UNIT).returncode == 0
    assert "@" not in (box.web / "curl_args").read_text()


# ---- O2': after the set-up -------------------------------------------------------------------


def vapid_point(folder: Path) -> str:
    key = load_pem_private_key((folder / "vapid_private.pem").read_bytes(), password=None)
    assert isinstance(key, ec.EllipticCurvePrivateKey) and key.curve.name == "secp256r1"
    point = key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(point).decode().rstrip("=")


@whole_tool
def test_after_the_set_up_a_run_puts_everything_in_place_and_provisioned_after_the_check(tmp_path):
    box = quiet_box(tmp_path)
    keys = bootstrapped(box)
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    folder = fam_dir(box)
    for name, key in keys.items():
        assert (folder / f"seal-{name}.key").read_text() == key + "\n"
    store = (folder / "store.key").read_bytes()
    assert len(store) == 45 and len(base64.b64decode(store.strip(), validate=True)) == 32
    assert (folder / "vapid_public.txt").read_text() == vapid_point(folder) + "\n"
    assert len(vapid_point(folder)) == 87
    assert (folder / HOLIDAYS).read_bytes() == box.day(HOLIDAYS).read_bytes()
    assert (folder / EXTRA).read_bytes() == box.day().read_bytes()
    assert (folder / "feed-token").read_bytes() == (feed_dir(box) / FAMILY).read_bytes()
    assert (folder / "provisioned").read_text().startswith("the family check passed at ")
    names = sorted(p.name for p in folder.iterdir())
    assert names == sorted(
        [f"seal-{n}.key" for n in KEY_NAMES]
        + [
            "store.key",
            "vapid_private.pem",
            "vapid_public.txt",
            "feed-token",
            HOLIDAYS,
            EXTRA,
            "provisioned",
        ]
    )  # nothing temporary left
    for path in folder.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o640, path.name
        assert owned_as(box, f"root:{FAMILY}", folder, path.name), path.name
    # derive as sudarshan, the check as the family account, both from the app folder; nothing as root
    assert lines(box, "python_m") == [
        f"sudarshan sudarshan.family.derive {box.app}",
        f"{FAMILY} sudarshan.family.check {box.app}",
    ]
    out = done.stdout
    assert f"family: written in {folder}: " in out
    assert (
        f"family: the check passed: {folder}/provisioned written, so the family view may start"
        in out
    )
    assert family_notices(box) == []


@whole_tool
def test_a_second_run_touches_nothing_and_says_nothing(tmp_path):
    box = quiet_box(tmp_path)
    bootstrapped(box)
    assert box.run().returncode == 0
    before = _snapshot(fam_dir(box), feed_dir(box))
    again = box.run()
    assert (again.returncode, again.stdout) == (0, "")
    assert _snapshot(fam_dir(box), feed_dir(box)) == before  # not even the folder's time


@whole_tool
def test_new_keys_are_written_and_the_one_time_secrets_never_again(tmp_path):
    """The pepper was rotated: derive answers other keys. store.key and the VAPID pair stay."""
    box = quiet_box(tmp_path)
    bootstrapped(box)
    assert box.run().returncode == 0
    folder = fam_dir(box)
    once = {
        n: (folder / n).read_bytes() for n in ("store.key", "vapid_private.pem", "vapid_public.txt")
    }
    rotated = new_keys()
    derive_says(box, keys_text(rotated))
    done = box.run()
    assert done.returncode == 0
    for name, key in rotated.items():
        assert (folder / f"seal-{name}.key").read_text() == key + "\n"
    assert {n: (folder / n).read_bytes() for n in once} == once
    assert (
        f"family: written in {folder}: {' '.join(f'seal-{n}.key' for n in KEY_NAMES)}"
        in done.stdout
    )


def test_the_public_key_is_taken_whatever_its_point_holds(tmp_path):
    """The tool's own lines, on many keys: the point is 04 x y, so its text starts with B and the
    second character carries four bits of x (A to P). A check that took A to D refused three keys in
    four."""
    text = DEPLOY_TOOL.read_text()
    pipeline = re.search(r'^\s*(pub="\$\(tail -c 65 .*\)")$', text, re.M).group(1)
    check = re.search(r'\[\[ "\$\{pub\}" =~ (\S+) \]\]', text).group(1)
    script = f'set -euo pipefail\nwork="$1"\n{pipeline}\n[[ "${{pub}}" =~ {check} ]] && printf "%s" "${{pub}}"\n'
    seen = set()
    for n in range(48):
        key = ec.generate_private_key(ec.SECP256R1())
        der = key.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        assert len(der) == 91
        (tmp_path / "pub.der").write_bytes(der)
        done = subprocess.run(
            ["bash", "-c", script, "pub", str(tmp_path)], capture_output=True, text=True
        )
        point = key.public_key().public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        assert done.stdout == base64.urlsafe_b64encode(point).decode().rstrip("="), (n, done.stderr)
        seen.add(done.stdout[1])
    assert len(seen) > 4  # the second character took more than the four values the old check knew


@whole_tool
def test_a_missing_public_key_is_made_again_from_the_private_one(tmp_path):
    box = quiet_box(tmp_path)
    bootstrapped(box)
    assert box.run().returncode == 0
    folder = fam_dir(box)
    private = (folder / "vapid_private.pem").read_bytes()
    (folder / "vapid_public.txt").unlink()
    assert box.run().returncode == 0
    assert (folder / "vapid_private.pem").read_bytes() == private
    assert (folder / "vapid_public.txt").read_text() == vapid_point(folder) + "\n"


def bad_answer(case: str, keys: dict[str, str]) -> str:
    good = keys_text(new_keys())
    first, rest = good.split("\n", 1)
    return {
        "something else too": good + "extra line\n",
        "a name twice": good + f"admin {keys['admin']}\n",
        "a name it does not know": good + f"nudge2 {keys['admin']}\n",
        "a key cut short": f"{first[:-1]}\n{rest}",
        "a name changed": good.replace("internal ", "intern ", 1),
        "nothing at all": "",
    }[case]


@whole_tool
@pytest.mark.parametrize(
    "case",
    [
        "something else too",
        "a name twice",
        "a name it does not know",
        "a key cut short",
        "a name changed",
        "nothing at all",
    ],
)
def test_an_answer_that_is_not_five_keys_writes_nothing(tmp_path, case):
    box = quiet_box(tmp_path)
    keys = bootstrapped(box)
    assert box.run().returncode == 0
    folder = fam_dir(box)
    before = _snapshot(folder)
    derive_says(box, bad_answer(case, keys))
    done = box.run()
    assert _snapshot(folder) == before  # not a byte, not the folder's time
    says = (
        "family: the derived seal keys were not five known names with a key each; nothing was "
        "written, the keys in place stay"
    )
    assert says in done.stdout
    assert box.run().stdout == ""  # said once while it holds
    assert (
        folder / "provisioned"
    ).exists()  # the keys in place are whole: the view stays as it was


@whole_tool
def test_no_key_on_the_first_run_keeps_the_view_off_and_tells_the_owner(tmp_path):
    box = quiet_box(tmp_path)
    bootstrapped(box)
    derive_says(box, "", rc=2)
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr  # never a failed deploy run
    folder = fam_dir(box)
    assert not (folder / "provisioned").exists()
    assert (
        "family: the seal keys could not be derived (exit 2); the keys in place stay" in done.stdout
    )
    assert "family: FAIL: the family's keys are not all in" in done.stdout
    (told,) = family_notices(box)
    assert (told["src"], told["why"]) == ("deploy", "files") and "seal-admin.key" in told["what"]
    assert lines(box, "python_m") == [
        f"sudarshan sudarshan.family.derive {box.app}"
    ]  # no check run


@whole_tool
def test_a_failed_check_takes_provisioned_away_and_is_told_once_while_it_lasts(tmp_path):
    box = quiet_box(tmp_path)
    bootstrapped(box)
    assert box.run().returncode == 0
    folder = fam_dir(box)
    assert (folder / "provisioned").exists()
    check_says(box, FAILING, rc=1)
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert not (folder / "provisioned").exists()
    out = done.stdout
    assert (
        f"family: {folder}/provisioned removed: the family view cannot start until the check passes again"
        in out
    )
    assert "family: FAIL: OpenAlgo's db folder: this account can open /x/db/openalgo.db" in out
    assert "family: check: FAIL OpenAlgo's market-data bus (127.0.0.1:5555)" in out
    (told,) = family_notices(box)
    assert (
        told["why"] == "check"
        and told["what"] == "OpenAlgo's db folder: this account can open /x/db/openalgo.db"
    )
    assert told["src"] == "deploy" and re.fullmatch(
        r"deploy-family-check-\d{8}-\d{6}-\d+", told["id"]
    )
    # the next runs while it still fails: nothing new said, nothing new told
    again = box.run()
    assert again.stdout == "" and len(family_notices(box)) == 1
    # it passes again: provisioned is back, said once
    check_says(box, PASSING)
    back = box.run()
    assert (folder / "provisioned").exists()
    assert "family: the check passed:" in back.stdout and len(family_notices(box)) == 1
    # a later failure is a new one, told again
    check_says(box, FAILING, rc=1)
    later = box.run()
    assert "family: FAIL:" in later.stdout and len(family_notices(box)) == 2


@whole_tool
def test_a_check_that_cannot_run_says_what_the_account_needs(tmp_path):
    box = quiet_box(tmp_path)
    bootstrapped(box)
    check_says(box, "/x/.venv/bin/python: No module named sudarshan\n", rc=1)
    done = box.run()
    assert (
        f"family: FAIL: the family check could not run as {FAMILY} (exit 1): that account must be able "
        f"to enter every folder down to {box.app} and read .venv and sudarshan/ there"
    ) in done.stdout
    assert "family: check: /x/.venv/bin/python: No module named sudarshan" in done.stdout
    assert not (fam_dir(box) / "provisioned").exists()


@whole_tool
def test_a_failed_check_stops_a_running_family_unit_and_a_pass_starts_it_again(tmp_path):
    box = quiet_box(tmp_path)
    bootstrapped(box)
    (box.state / "managed_units").write_text(f"{FAMILY_UNIT}\n")
    assert box.run().returncode == 0
    check_says(box, FAILING, rc=1)
    done = box.run()  # the fake systemctl says every unit is active
    assert f"stop {FAMILY_UNIT}" in lines(box, "systemctl")
    assert f"family: {FAMILY_UNIT} stopped: its check failed" in done.stdout
    _shim(  # stopped now, enabled
        box.bin,
        "systemctl",
        'echo "$*" >> "${FAKE_WEB}/systemctl"\n'
        'if [[ "$1" == "is-active" ]]; then echo inactive; exit 3; fi\n'
        'if [[ "$1" == "is-enabled" ]]; then echo enabled; exit 0; fi',
    )
    check_says(box, PASSING)
    back = box.run()
    assert f"start {FAMILY_UNIT}" in lines(box, "systemctl")
    assert f"family: {FAMILY_UNIT} started" in back.stdout


@whole_tool
def test_a_family_unit_that_is_not_installed_is_never_started_or_stopped(tmp_path):
    box = quiet_box(tmp_path)
    bootstrapped(box)
    check_says(box, FAILING, rc=1)
    box.run()
    check_says(box, PASSING)
    box.run()
    assert not [line for line in lines(box, "systemctl") if FAMILY in line]


@whole_tool
def test_a_family_folder_others_could_write_is_not_written_in(tmp_path):
    box = quiet_box(tmp_path)
    bootstrapped(box)
    fam_dir(box).chmod(0o777)
    done = box.run()
    assert list(fam_dir(box).iterdir()) == [] and lines(box, "python_m") == []
    assert (
        f"family: FAIL: {fam_dir(box)} is not a folder owned by root and closed to others"
        in done.stdout
    )
    (told,) = family_notices(box)
    assert told["why"] == "folder"


@whole_tool
def test_the_copies_follow_the_day_files_and_the_feed_token(tmp_path):
    box = quiet_box(tmp_path)
    bootstrapped(box)
    assert box.run().returncode == 0
    folder = fam_dir(box)
    box.day(HOLIDAYS).write_bytes(b"2026-10-20  # Dussehra\n2026-11-24  # Guru Nanak Jayanti\n")
    box.day().unlink()
    done = box.run()
    assert (folder / HOLIDAYS).read_bytes() == box.day(HOLIDAYS).read_bytes()
    assert not (folder / EXTRA).exists()
    assert (
        f"family: written in {folder}: {HOLIDAYS} {EXTRA} (removed: {box.etc} has none)"
        in done.stdout
    )


@whole_tool
def test_an_install_runs_the_step_again_with_the_new_release_and_backs_the_folder_up(tmp_path):
    box = ring_box(tmp_path)
    bootstrapped(box)
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    ran = lines(box, "python_m")
    assert (
        ran.count(f"{FAMILY} sudarshan.family.check {box.app}") == 2
    )  # before, and after the install
    copy = the_backup(box) / "etc-family"
    assert sorted(p.name for p in copy.iterdir()) == sorted(p.name for p in fam_dir(box).iterdir())
    assert stat.S_IMODE(the_backup(box).stat().st_mode) & 0o077 == 0
    assert (
        f"family: {fam_dir(box)} copied into the backup (a rollback never puts it back)"
        in done.stdout
    )


@whole_tool
def test_a_rollback_never_puts_the_family_folder_back(tmp_path):
    """What the owner or the tool saved there after the backup (a bot token, new keys) survives."""
    box = ring_box(tmp_path)
    bootstrapped(box)
    box.migrations = "1"
    added = fam_dir(box) / "telegram_bot_token"
    family_python(box, f'echo "saved after the backup" > "{added}"; chmod 640 "{added}"')
    done = box.run()
    assert done.returncode == 1 and "rolling back" in done.stdout, done.stdout + done.stderr
    assert (the_backup(box) / "etc-family" / "seal-admin.key").exists()  # it was copied
    assert added.read_text() == "saved after the backup\n"  # and never put back over
    assert "etc-family" not in "".join(
        line for line in done.stdout.splitlines() if "put back" in line
    )


# ---- the provision step on its own, for the set-up ---------------------------------------------


@whole_tool
def test_the_set_up_step_says_everything_without_times(tmp_path):
    box = quiet_box(tmp_path)
    bootstrapped(box)
    done = box.run("--family-provision")
    assert done.returncode == 0, done.stdout + done.stderr
    out = done.stdout.splitlines()
    assert all(line.startswith("family: ") for line in out), out
    assert (
        out[-1]
        == "family: PASS: the family account is set up, and its check finds it closed off from the owner's secrets"
    )
    assert "family: check: PASS OpenAlgo's .env: closed to this account (/x/.env)" in out
    assert box.requests() == []  # no release is looked at
    again = box.run("--family-provision")
    assert "family: nothing to write: every key and copy in" in again.stdout
    assert f"family: the check passed; {fam_dir(box)}/provisioned was already there" in again.stdout


@whole_tool
def test_the_set_up_step_before_the_folder_exists_and_after_a_failure(tmp_path):
    box = quiet_box(tmp_path)
    done = box.run("--family-provision")
    assert done.returncode == 2
    assert "family: there is no " in done.stdout and "bootstrap.sh" in done.stdout
    bootstrapped(box)
    check_says(box, FAILING, rc=1)
    failed = box.run("--family-provision")
    assert failed.returncode == 1
    assert (
        "family: FAIL: OpenAlgo's db folder: this account can open /x/db/openalgo.db"
        in failed.stdout
    )
    # the set-up repeats the failure each time; the timer's runs stay quiet about it
    assert "family: FAIL:" in box.run("--family-provision").stdout
    assert box.run().stdout == ""


# ---- root runs none of the app's Python; no secret is ever an argument --------------------------


def test_every_python_call_of_the_app_is_behind_sudo_u():
    """Y2: the app folder and its venv are written by the service user; root runs nothing from them."""
    text = DEPLOY_TOOL.read_text()
    assert re.search(r'^as_svc\(\) \{ sudo -u "\$\{SVC_USER\}" -H -- "\$@"; \}$', text, re.M)
    arrays = set()
    for number, line in enumerate(text.splitlines(), 1):
        code = line.split("  #")[0] if not line.lstrip().startswith("#") else ""
        if ".venv/bin/python" not in code:
            continue
        at = code.index(".venv/bin/python")
        before = code[:at]
        if re.match(r"\s*([A-Z_]+)=\(", code):  # an array of words, run only through as_svc
            arrays.add(re.match(r"\s*([A-Z_]+)=\(", code).group(1))
            continue
        assert "sudo -u " in before or re.search(r"\bas_svc\b", before), f"line {number}: {line}"
    for name in arrays:
        uses = re.findall(rf'(\S+)\s+"\$\{{{name}\[@\]\}}"', text)
        assert uses and all(use == "as_svc" for use in uses), (name, uses)
    family = [
        line
        for line in text.splitlines()
        if "-m sudarshan.family." in line and not line.lstrip().startswith("#")
    ]
    assert len(family) == 2
    assert 'sudo -u "${RING_USER}" -H --' in family[0] and "sudarshan.family.derive" in family[0]
    assert 'sudo -u "${FAM_USER}" -H --' in family[1] and "sudarshan.family.check" in family[1]


@whole_tool
@pytest.mark.skipif(shutil.which("strace") is None, reason="needs strace")
def test_no_key_or_token_is_ever_a_commands_argument(tmp_path):
    box = quiet_box(tmp_path)
    keys = bootstrapped(box)
    trace = tmp_path / "trace"
    done = box.run(
        prefix=("strace", "-f", "-qq", "-s", "4096", "-e", "trace=execve", "-o", str(trace))
    )
    assert done.returncode == 0, done.stdout + done.stderr
    calls = trace.read_text()
    assert "sudarshan.family.derive" in calls  # the trace saw the run
    secrets = [*keys.values(), *(p.read_text().strip() for p in feed_dir(box).iterdir())]
    secrets.append((fam_dir(box) / "store.key").read_text().strip())
    for secret in secrets:
        assert secret not in calls
    assert "BEGIN EC PRIVATE KEY" not in calls


def test_the_names_derive_prints_are_the_names_the_tool_takes():
    from sudarshan.family import derive

    text = DEPLOY_TOOL.read_text()
    listed = re.search(r"^FAM_KEYS=\(([^)]*)\)", text, re.M).group(1).split()
    assert listed == [name for name, _info in derive.PURPOSES]
    clients = re.search(r"^FEED_CLIENTS=\(([^)]*)\)", text, re.M).group(1).split()
    assert clients == list(CLIENTS)
