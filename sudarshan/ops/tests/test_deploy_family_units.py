"""O1 (P6; cross-design Y13): the deploy tool's side of the family view's account, before it exists.

The whole tool runs on the fake server of ``test_deploy_calendar.py``; the fake stat names ``sudarshan``
as the owner of every ring folder unless a test says otherwise (``Box.owner``), and the fake sudo
records the account a command was run as. On this server ``/etc/sudarshan-family`` is
``<root>/etc-family``.

What these pin: the unit policy lets ``sudarshan-family.service``, and no other unit, run as
``sudarshan-family``, only with the condition that waits for the family check, and still runs alone
when the units' own tests cut it out; a unit its condition skips is not a failure (no warning, no
health wait, no rollback, no notice); the ring's databases are copied, put back and asked about as the
account that owns their folder, ``sudarshan`` or ``sudarshan-family``, never as root, and any other
owner is left out with a warning; the family store's copy for the nightly backup is not taken for a
store; the family view's settings file lives in its own folder.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from test_deploy_calendar import DEPLOY_TOOL, Box, _function, _shim, whole_tool
from test_deploy_notices import UNIT, install_box
from test_deploy_ring_databases import (
    make_db,
    migration_that,
    ring_box,
    rows_of,
    serve_units,
    the_backup,
)

pytestmark = [
    pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash"),
    pytest.mark.skipif(shutil.which("sqlite3") is None, reason="needs the sqlite3 command"),
]

FAMILY = "sudarshan-family"
FAMILY_UNIT = f"{FAMILY}.service"


def fam_dir(box: Box) -> Path:
    return box.root / "etc-family"


def lines(box: Box, name: str) -> list[str]:
    path = box.web / name
    return path.read_text().splitlines() if path.exists() else []


def notices(box: Box) -> list[dict]:
    """Every line of the deploy's spool, read as JSON (the family kind is worded by the alerts lane)."""
    path = box.state / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def family_unit(box: Box) -> str:
    return (
        "[Unit]\nDescription=the family view\n"
        f"ConditionPathExists={fam_dir(box)}/provisioned\n"
        f"[Service]\nUser={FAMILY}\nGroup={FAMILY}\nNoNewPrivileges=yes\nExecStart=/bin/true\n"
    )


# ---- O1: the unit policy ----------------------------------------------------------------------


def policy(tmp_path: Path, name: str, body: str) -> subprocess.CompletedProcess:
    """unit_ok, cut out of the tool as it ships and run alone, as the units' own tests run it."""
    unit = tmp_path / name
    unit.write_text(body)
    script = (
        'set -Eeuo pipefail\nlog() { echo "$*"; }\n'
        + _function("unit_ok")
        + 'if unit_ok "$1" "$2"; then echo ACCEPTED; else echo REFUSED; fi\n'
    )
    return subprocess.run(
        ["bash", "-c", script, "policy", str(unit), name], capture_output=True, text=True
    )


def test_the_policy_follows_the_tools_own_names(tmp_path):
    """Alone it uses the tool's values; inside the tool the tool's names, set once at the top."""
    text = DEPLOY_TOOL.read_text()
    assert 'RING_USER="sudarshan"' in text and 'FAM_USER="sudarshan-family"' in text
    assert 'FAM_DIR="/etc/sudarshan-family"' in text
    function = _function("unit_ok")
    for default in (
        "${RING_USER:-sudarshan}",
        "${FAM_USER:-sudarshan-family}",
        "${FAM_DIR:-/etc/sudarshan-family}",
    ):
        assert default in function, default


FAMILY_BODY = (
    "[Unit]\nConditionPathExists = /etc/sudarshan-family/provisioned\n"
    "[Service]\nUser=sudarshan-family\nGroup=sudarshan-family\nNoNewPrivileges=yes\n"
    "ExecStart=/opt/app/.venv/bin/python -m sudarshan.family\n"
)


def test_the_family_unit_may_run_as_its_own_account(tmp_path):
    done = policy(tmp_path, FAMILY_UNIT, FAMILY_BODY)
    assert done.stdout.strip() == "ACCEPTED", done.stdout + done.stderr


@pytest.mark.parametrize(
    ("name", "body", "says"),
    [
        # the family unit as the ring's account, or in its group: it could read the owner's secrets
        (FAMILY_UNIT, FAMILY_BODY.replace("User=sudarshan-family", "User=sudarshan"),
         "User/Group other than sudarshan-family"),
        (FAMILY_UNIT, FAMILY_BODY.replace("Group=sudarshan-family", "Group=sudarshan"),
         "User/Group other than sudarshan-family"),
        (FAMILY_UNIT, FAMILY_BODY.replace("User=sudarshan-family\n", ""),
         "must say User=sudarshan-family"),
        (FAMILY_UNIT, FAMILY_BODY.replace("User=sudarshan-family", "User=root"),
         "User/Group other than sudarshan-family"),
        # without the condition it would start before its check has passed
        (FAMILY_UNIT, FAMILY_BODY.replace("ConditionPathExists = /etc/sudarshan-family/provisioned\n", ""),
         "must say ConditionPathExists=/etc/sudarshan-family/provisioned"),
        (FAMILY_UNIT, FAMILY_BODY.replace("/provisioned", "/elsewhere"),
         "must say ConditionPathExists=/etc/sudarshan-family/provisioned"),
        (FAMILY_UNIT, FAMILY_BODY.replace("= /etc/", "= !/etc/"),
         "must say ConditionPathExists=/etc/sudarshan-family/provisioned"),
        # the family account for any other unit
        ("sudarshan-alerts.service", FAMILY_BODY, "User/Group other than sudarshan"),
        ("sudarshan-family-two.service", FAMILY_BODY, "User/Group other than sudarshan"),
        ("sudarshan-alerts.service", UNIT.format(user="sudarshan") + "Group=sudarshan-family\n",
         "User/Group other than sudarshan"),
    ],
)  # fmt: skip
def test_the_family_account_is_refused_everywhere_else_and_the_family_unit_is_held_to_it(
    tmp_path, name, body, says
):
    done = policy(tmp_path, name, body)
    assert done.stdout.splitlines() == [f"unit {name}: {says}", "REFUSED"], (
        done.stdout + done.stderr
    )


def test_every_other_unit_still_runs_as_sudarshan(tmp_path):
    assert (
        policy(tmp_path, "sudarshan-alerts.service", UNIT.format(user="sudarshan")).stdout.strip()
        == "ACCEPTED"
    )
    done = policy(tmp_path, "sudarshan-alerts.service", UNIT.format(user="nobody"))
    assert done.stdout.splitlines()[-1] == "REFUSED"


def test_the_ring_user_comment_stays_true():
    """The comment beside RING_USER says every ring service runs as it: it names the one exception."""
    text = DEPLOY_TOOL.read_text()
    at = text.index('RING_USER="sudarshan"')
    assert "but one" in text[at : at + 200]
    assert text[at:].index('FAM_USER="sudarshan-family"') < 200


# ---- O1: a unit its condition skips -----------------------------------------------------------


def condition_skips(box: Box) -> None:
    """systemd as it answers for a unit whose ConditionPathExists does not hold: the start is done,
    the unit stays inactive and its last condition check says no."""
    _shim(
        box.bin,
        "systemctl",
        'echo "$*" >> "${FAKE_WEB}/systemctl"\n'
        'case "$*" in\n'
        '    "is-active sudarshan-family.service") echo inactive; exit 3 ;;\n'
        '    "show -p ConditionResult --value -- sudarshan-family.service") echo no; exit 0 ;;\n'
        "esac\n"
        'if [[ "$1" == "is-active" ]]; then echo active; fi',
    )


@whole_tool
def test_a_unit_its_condition_skips_is_not_a_failure(tmp_path):
    box = install_box(tmp_path)
    serve_units(
        box,
        "v1.0.1",
        {"sudarshan-alerts.service": UNIT.format(user="sudarshan"), FAMILY_UNIT: family_unit(box)},
    )
    box.serve("http://127.0.0.1:5582/health", b'{"state": "running", "release": "v1.0.1"}')
    condition_skips(box)
    done = box.run()
    out = done.stdout
    assert done.returncode == 0 and box.deployed() == "v1.0.1", out + done.stderr
    assert (box.units / FAMILY_UNIT).read_text() == family_unit(box)  # installed and enabled
    assert f"enable {FAMILY_UNIT}" in lines(box, "systemctl")
    assert f"ring: {FAMILY_UNIT} not started: its start condition is not met yet\n" in out
    assert "rolling back" not in out and "WARNING" not in out and "did not start" not in out
    assert not [url for url in box.requests() if ":5585/" in url]  # no health wait for it
    assert "ring: sudarshan-alerts.service active" in out  # the others as before
    assert [n["kind"] for n in notices(box)] == ["installed"]  # and nothing to tell


@whole_tool
def test_a_unit_that_failed_to_start_is_still_told_as_before(tmp_path):
    """The other side: inactive with its condition met is a failed start, and its health is asked."""
    box = install_box(tmp_path)
    serve_units(box, "v1.0.1", {FAMILY_UNIT: family_unit(box)})
    _shim(
        box.bin,
        "systemctl",
        'echo "$*" >> "${FAKE_WEB}/systemctl"\n'
        'case "$*" in\n'
        '    "is-active sudarshan-family.service") echo failed; exit 3 ;;\n'
        '    "show -p ConditionResult --value -- sudarshan-family.service") echo yes; exit 0 ;;\n'
        "esac\n"
        'if [[ "$1" == "is-active" ]]; then echo active; fi',
    )
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert f"ring: {FAMILY_UNIT} failed\n" in done.stdout
    assert f"WARNING: ring: {FAMILY_UNIT} did not answer on 127.0.0.1:5585/health" in done.stdout


# ---- O1: the ring's databases as the account that owns their folder ---------------------------


def family_store(box: Box) -> Path:
    """The family's own state folder, owned by its account, with its store; and the export folder
    with the store's copy for the nightly backup."""
    store = make_db(box.ringvar / FAMILY / "family.db", [61, 62])
    box.owner(box.ringvar / FAMILY, FAMILY)
    make_db(box.ringvar / f"{FAMILY}-export" / "family-copy.db", [61])
    box.owner(box.ringvar / f"{FAMILY}-export", FAMILY)
    return store


@whole_tool
def test_the_family_store_is_copied_and_put_back_by_its_own_account(tmp_path):
    box = ring_box(tmp_path)
    store = family_store(box)
    box.migrations = "1"
    migration_that(box, f'sqlite3 "{store}" "INSERT INTO t VALUES (999)"')
    done = box.run()
    assert done.returncode == 1 and "rolling back" in done.stdout, done.stdout + done.stderr
    copy = the_backup(box) / "ring" / FAMILY / "family.db"
    assert rows_of(copy) == [61, 62] and f"ring: backed up {FAMILY}/family.db\n" in done.stdout
    assert rows_of(store) == [61, 62] and f"ring: put back {FAMILY}/family.db\n" in done.stdout
    asked = lines(box, "sudo")
    mine = [line for line in asked if str(box.ringvar / FAMILY) + "/" in line]
    assert len(mine) == 2 and all(line.startswith(f"-u {FAMILY} -H -- sh -c ") for line in mine)
    # the others are still sudarshan's, and nothing ran as root
    assert any(
        line.startswith("-u sudarshan -H -- sh -c ") and "alerts.db" in line for line in asked
    )
    assert not [line for line in asked if line.startswith("-u root ") and str(box.ringvar) in line]


@whole_tool
def test_the_family_stores_copy_for_the_nightly_backup_is_not_a_store(tmp_path):
    box = ring_box(tmp_path)
    family_store(box)
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert not (the_backup(box) / "ring" / f"{FAMILY}-export").exists()
    assert "family-copy.db" not in done.stdout and "family-copy.db" not in "".join(
        lines(box, "sudo")
    )


@whole_tool
@pytest.mark.parametrize("account", ["root", "trinetra", "UNKNOWN"])
def test_a_folder_any_other_account_owns_is_left_alone_and_nothing_runs_as_root(tmp_path, account):
    box = ring_box(tmp_path)
    make_db(box.ringvar / "sudarshan-odd" / "odd.db", [1])
    box.owner(box.ringvar / "sudarshan-odd", account)
    box.migrations = "1"  # an install that rolls back: the copy and the restore are both asked
    done = box.run()
    assert done.returncode == 1, done.stdout + done.stderr
    says = (
        f"WARNING: ring: sudarshan-odd belongs to {account}, not to sudarshan or sudarshan-family "
        "- its databases are not backed up"
    )
    assert says in done.stdout
    assert not (the_backup(box) / "ring" / "sudarshan-odd").exists()
    odd = str(box.ringvar / "sudarshan-odd")
    assert not [
        line for line in lines(box, "sudo") if line.startswith(f"-u {account} ") and odd in line
    ]
    assert rows_of(box.ringvar / "sudarshan-odd" / "odd.db") == [1]  # untouched


@whole_tool
def test_a_folder_that_changed_hands_after_the_backup_is_not_put_back(tmp_path):
    box = ring_box(tmp_path)
    box.migrations = "1"
    # during the install the portfolio's folder comes to belong to root (by hand, say)
    key = box.key(str(box.ringvar / "sudarshan-portfolio"))
    migration_that(box, f'echo root > "{box.web}/owner-{key}"')
    done = box.run()
    assert done.returncode == 1, done.stdout + done.stderr
    assert (
        "WARNING: ring: sudarshan-portfolio belongs to root, not to sudarshan or sudarshan-family "
        "- portfolio.db not put back"
    ) in done.stdout
    assert "ring: put back sudarshan-alerts/alerts.db" in done.stdout


def status_shim(box: Box) -> None:
    _shim(
        box.app / ".venv" / "bin",
        "python",
        'if [[ "$1" == -m ]]; then echo "${FAKE_SUDO_USER:-root} $2" >> "${FAKE_WEB}/python_m"; '
        'echo "store: fine"; exit 0; fi\nexit "${FAKE_MIGRATIONS}"',
    )


@whole_tool
def test_the_migration_status_is_asked_as_the_folders_own_account(tmp_path):
    box = ring_box(tmp_path)
    family_store(box)
    for service in ("family", "alerts", "feed"):
        path = box.app / "sudarshan" / service / "migrate.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
    make_db(box.ringvar / "sudarshan-feed" / "feed.db", [1])
    box.owner(box.ringvar / "sudarshan-feed", "root")
    status_shim(box)
    serve_units(
        box,
        "v1.0.1",
        {
            "sudarshan-alerts.service": UNIT.format(user="sudarshan"),
            "sudarshan-feed.service": UNIT.format(user="sudarshan"),
            FAMILY_UNIT: family_unit(box),
        },
    )
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    ran = lines(box, "python_m")
    assert f"{FAMILY} sudarshan.family.migrate" in ran
    assert "sudarshan sudarshan.alerts.migrate" in ran
    assert not [
        line for line in ran if "sudarshan.feed.migrate" in line
    ]  # root's folder: not asked
    assert (
        "WARNING: ring: sudarshan-feed belongs to root, not to sudarshan or sudarshan-family "
        "- its migration status is not asked"
    ) in done.stdout
    assert "ring: sudarshan.family.migrate --status: store: fine" in done.stdout


@whole_tool
def test_a_service_with_no_state_folder_yet_has_no_status_line(tmp_path):
    box = ring_box(tmp_path)
    path = box.app / "sudarshan" / "screener" / "migrate.py"
    path.parent.mkdir(parents=True)
    path.write_text("")
    status_shim(box)
    serve_units(box, "v1.0.1", {"sudarshan-screener.service": UNIT.format(user="sudarshan")})
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert "sudarshan.screener.migrate" not in done.stdout and lines(box, "python_m") == []


@whole_tool
def test_removing_the_family_unit_copies_its_store_as_its_account(tmp_path):
    box = install_box(tmp_path)
    family_store(box)
    (box.units / FAMILY_UNIT).write_text(family_unit(box))
    (box.state / "managed_units").write_text(f"{FAMILY_UNIT}\n")
    box.serve(f"http://127.0.0.1:5580/claims/{FAMILY}", b'{"dropped": 3}')
    done = box.run("--remove-ring", FAMILY_UNIT)
    assert done.returncode == 0, done.stdout + done.stderr
    assert rows_of(next(box.backups.iterdir()) / "ring" / FAMILY / "family.db") == [61, 62]
    copied = [line for line in lines(box, "sudo") if "family.db" in line]
    assert len(copied) == 1 and copied[0].startswith(f"-u {FAMILY} -H -- sh -c ")
    assert f"ring: backed up {FAMILY}/family.db before removal" in done.stdout
    assert f"ring: the price feed no longer keeps {FAMILY}'s instruments" in done.stdout


@whole_tool
def test_removing_a_service_whose_folder_another_account_owns_copies_nothing(tmp_path):
    box = install_box(tmp_path)
    make_db(box.ringvar / FAMILY / "family.db", [1])
    box.owner(box.ringvar / FAMILY, "root")
    (box.state / "managed_units").write_text(f"{FAMILY_UNIT}\n")
    done = box.run("--remove-ring", FAMILY_UNIT)
    assert done.returncode == 0, done.stdout + done.stderr
    assert (
        f"WARNING: ring: {FAMILY} belongs to root, not to sudarshan or sudarshan-family - its databases "
        "are not copied"
    ) in done.stdout
    assert not (next(box.backups.iterdir()) / "ring").exists()


FAMILY_ENV_LIST = Path(__file__).resolve().parents[3] / "sudarshan/ops/deploy/family-env.conf"


def test_l1_the_release_ships_the_family_settings_list_with_the_view_switched_off():
    """v0.12.0 review L1: no family-env.conf shipped, so no deploy ever made family.env and the
    switch-on steps had no file to name. The list holds the switch alone, off, with no old value
    (an owner who turned it on keeps it on), and every key in it is one the service reads.
    Fails on the old tree: the file was not there."""
    from sudarshan.family.config import Settings

    words = [line.split() for line in FAMILY_ENV_LIST.read_text(encoding="utf-8").splitlines()
             if line.strip() and not line.startswith("#")]  # fmt: skip
    assert words == [["SUDARSHAN_FAMILY", "off"]]
    assert FAMILY_ENV_LIST.read_bytes().endswith(b"\n")  # the tool refuses a list cut off
    assert Settings.from_env(dict(words)).enabled is False
    assert Settings.from_env({"SUDARSHAN_FAMILY": "on"}).enabled is True


@whole_tool
def test_l1_the_shipped_list_makes_family_env_off_and_keeps_the_owners_on(tmp_path):
    box = install_box(tmp_path)
    conf = f"https://raw.githubusercontent.com/{box.OWNER_REPO}/v1.0.1/sudarshan/ops/deploy/family-env.conf"
    fam_dir(box).mkdir(mode=0o750)  # the owner's set-up has made the family's folder
    serve_units(box, "v1.0.1", {FAMILY_UNIT: family_unit(box)})
    box.serve(conf, FAMILY_ENV_LIST.read_bytes())
    condition_skips(box)
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    env = fam_dir(box) / "family.env"
    assert env.read_text() == "SUDARSHAN_FAMILY='off'\n"
    (tmp_path / "again").mkdir()  # the owner has switched the view on: a later deploy keeps it
    again = install_box(tmp_path / "again")
    fam_dir(again).mkdir(mode=0o750)
    (fam_dir(again) / "family.env").write_text("SUDARSHAN_FAMILY=on\n")
    serve_units(again, "v1.0.1", {FAMILY_UNIT: family_unit(again)})
    again.serve(conf, FAMILY_ENV_LIST.read_bytes())
    condition_skips(again)
    done = again.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert (fam_dir(again) / "family.env").read_text() == "SUDARSHAN_FAMILY=on\n"


@whole_tool
def test_the_family_settings_file_lives_in_its_own_folder(tmp_path):
    box = install_box(tmp_path)
    serve_units(box, "v1.0.1", {FAMILY_UNIT: family_unit(box)})
    conf = f"https://raw.githubusercontent.com/{box.OWNER_REPO}/v1.0.1/sudarshan/ops/deploy/family-env.conf"
    box.serve(conf, b"SUDARSHAN_FAMILY  off\n")
    condition_skips(box)
    done = box.run()  # before the set-up: no file anywhere
    assert done.returncode == 0, done.stdout + done.stderr
    assert not (box.etc / "family.env").exists() and not fam_dir(box).exists()
    (tmp_path / "after").mkdir()
    box = install_box(tmp_path / "after")
    fam_dir(box).mkdir(mode=0o750)  # the owner's set-up has made the family's folder
    serve_units(box, "v1.0.1", {FAMILY_UNIT: family_unit(box)})
    box.serve(conf, b"SUDARSHAN_FAMILY  off\n")
    condition_skips(box)
    done = box.run()
    assert (fam_dir(box) / "family.env").read_text() == "SUDARSHAN_FAMILY='off'\n"
    assert not (box.etc / "family.env").exists()


# ---- review N1: the set-up after the install ----------------------------------------------------


def fetched(box: Box, url: str) -> int:
    """How many times the tool asked for ``url`` (the fake curl notes every URL)."""
    return lines(box, "requests").count(url)


@whole_tool
def test_n1_the_set_up_after_the_install_still_gets_family_env(tmp_path):
    """v0.12.0 verification N1: family.env was made only while a release was being installed, so
    an owner who installed first and ran the family set-up afterwards (the order the README gives)
    never got the file, and the switch line answered "wait ten minutes" for ever. Every run now
    makes it. Fails on the old tool: no file after the second run."""
    box = install_box(tmp_path)
    conf = f"https://raw.githubusercontent.com/{box.OWNER_REPO}/v1.0.1/sudarshan/ops/deploy/family-env.conf"
    serve_units(box, "v1.0.1", {FAMILY_UNIT: family_unit(box)})
    box.serve(conf, FAMILY_ENV_LIST.read_bytes())
    condition_skips(box)
    done = box.run()  # the install, before any family set-up: nothing of the family is made
    assert done.returncode == 0, done.stdout + done.stderr
    assert box.deployed() == "v1.0.1" and not fam_dir(box).exists()
    assert not (box.state / "family_env_tag").exists()
    fam_dir(box).mkdir(mode=0o750)  # the owner's set-up, after the install
    done = box.run()  # the next ten-minute run: the same release, nothing to install
    assert done.returncode == 0, done.stdout + done.stderr
    assert "No such file" not in done.stdout + done.stderr  # the first look for its note is quiet
    env = fam_dir(box) / "family.env"
    assert env.read_text() == "SUDARSHAN_FAMILY='off'\n"
    assert (env.stat().st_mode & 0o777) == 0o640
    assert (box.state / "family_env_tag").read_text() == "v1.0.1\n"
    asked = fetched(box, conf)
    systemctl = lines(box, "systemctl")
    again = (
        box.run()
    )  # and after that a run does nothing to the file: no fetch, no word, no restart
    assert again.returncode == 0, again.stdout + again.stderr
    assert "ring:" not in again.stdout and "settings:" not in again.stdout
    assert fetched(box, conf) == asked
    assert not [line for line in lines(box, "systemctl")[len(systemctl) :] if "restart" in line]


@whole_tool
def test_n1_a_run_never_overwrites_the_owners_switch(tmp_path):
    box = install_box(tmp_path)
    conf = f"https://raw.githubusercontent.com/{box.OWNER_REPO}/v1.0.1/sudarshan/ops/deploy/family-env.conf"
    serve_units(box, "v1.0.1", {FAMILY_UNIT: family_unit(box)})
    box.serve(conf, FAMILY_ENV_LIST.read_bytes())
    condition_skips(box)
    assert box.run().returncode == 0
    fam_dir(box).mkdir(mode=0o750)
    env = fam_dir(box) / "family.env"
    env.write_text("# mine\nSUDARSHAN_FAMILY=on")  # the owner's own, with no final newline
    for _ in range(2):
        done = box.run()
        assert done.returncode == 0, done.stdout + done.stderr
        assert "No such file" not in done.stderr  # a file with no note yet is looked at quietly
        assert env.read_text() == "# mine\nSUDARSHAN_FAMILY=on"
    (box.state / "family_env_tag").unlink()  # as a server that has never run this tool's step
    env.write_text(
        "OTHER=1"
    )  # a file of the owner's without the switch: the key is added, on a line of its own
    assert box.run().returncode == 0
    assert env.read_text() == "OTHER=1\nSUDARSHAN_FAMILY='off'\n"


@whole_tool
def test_n1_nothing_before_the_set_up_and_nothing_for_a_release_without_the_family(tmp_path):
    box = install_box(tmp_path)
    conf = f"https://raw.githubusercontent.com/{box.OWNER_REPO}/v1.0.1/sudarshan/ops/deploy/family-env.conf"
    serve_units(box, "v1.0.1", {"sudarshan-alerts.service": UNIT.format(user="sudarshan")})
    box.serve(conf, FAMILY_ENV_LIST.read_bytes())
    assert box.run().returncode == 0
    asked = fetched(box, conf)
    fam_dir(box).mkdir(mode=0o750)  # set up, but the installed release ships no family unit
    assert box.run().returncode == 0
    assert not (fam_dir(box) / "family.env").exists() and fetched(box, conf) == asked


@whole_tool
def test_n1_a_list_that_did_not_arrive_is_asked_for_again_and_a_release_without_one_once(tmp_path):
    box = install_box(tmp_path)
    conf = f"https://raw.githubusercontent.com/{box.OWNER_REPO}/v1.0.1/sudarshan/ops/deploy/family-env.conf"
    serve_units(box, "v1.0.1", {FAMILY_UNIT: family_unit(box)})
    condition_skips(box)
    assert box.run().returncode == 0
    fam_dir(box).mkdir(mode=0o750)
    box.serve(conf, FAMILY_ENV_LIST.read_bytes(), cut=True)  # cut off in transit
    assert box.run().returncode == 0
    assert not (fam_dir(box) / "family.env").exists()
    assert not (box.state / "family_env_tag").exists()
    box.serve(conf, FAMILY_ENV_LIST.read_bytes())
    assert box.run().returncode == 0
    assert (fam_dir(box) / "family.env").read_text() == "SUDARSHAN_FAMILY='off'\n"


@whole_tool
def test_n1_the_set_up_itself_makes_family_env_once_the_release_is_installed(tmp_path):
    """The owner is at the set-up, with the release installed: the file the switch line needs is
    there when the set-up ends, and the set-up says so once. Fails on the old tool: no file."""
    box = install_box(tmp_path)
    conf = f"https://raw.githubusercontent.com/{box.OWNER_REPO}/v1.0.1/sudarshan/ops/deploy/family-env.conf"
    serve_units(box, "v1.0.1", {FAMILY_UNIT: family_unit(box)})
    box.serve(conf, FAMILY_ENV_LIST.read_bytes())
    condition_skips(box)
    assert box.run().returncode == 0
    fam_dir(box).mkdir(mode=0o750)
    made = f"family: written in {fam_dir(box)}: family.env, with the family view switched off"
    done = box.run("--family-provision")
    assert made in done.stdout.splitlines(), done.stdout + done.stderr
    assert (fam_dir(box) / "family.env").read_text() == "SUDARSHAN_FAMILY='off'\n"
    (fam_dir(box) / "family.env").write_text("SUDARSHAN_FAMILY=on\n")  # the owner's switch
    done = box.run("--family-provision")  # the set-up again: nothing said, nothing changed
    assert made not in done.stdout
    assert (fam_dir(box) / "family.env").read_text() == "SUDARSHAN_FAMILY=on\n"
